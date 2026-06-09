"""
tests/test_cdse_degraded_states.py
==================================

Tests de integración del extractor CDSE con todos los modos degradados.

Cubre el requisito Tarea 4.4:
  "Test de integración con mock que simule CDSE down: el sistema no
   debe tirar excepción en ningún caso, siempre debe responder con uno
   de los 5 estados."

Mockeos:
  * `requests.post` para Statistical API + endpoint de token.
  * `get_cdse_token` (devuelve token fake para no requerir credenciales).
  * `time.sleep` (anula los delays exponenciales para que el test corra rápido).
  * `get_cache_db` para usar SQLite en tmp_path en lugar de data/.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from shapely.geometry import Point

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.pipeline import eodag_extractor as ee  # noqa: E402
from src.pipeline.eodag_extractor import (  # noqa: E402
    CACHE_TTL_SECONDS_CURRENT_YEAR,
    CDSEDataState,
    CDSEResult,
    STALE_FRACTION,
    get_cdse_state_log,
    get_eodag_stats,
    get_eodag_stats_with_state,
    get_worst_cdse_state,
    reset_cdse_state_log,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_cache_db(tmp_path, monkeypatch):
    """Reemplaza get_cache_db por una conexión a un SQLite temporal."""
    db_path = tmp_path / "cache_test.db"

    def _get_cache_db():
        conn = sqlite3.connect(str(db_path), timeout=10)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS statistics_cache (
                cache_key TEXT PRIMARY KEY,
                response_json TEXT,
                timestamp INTEGER
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS process_cache (
                cache_key TEXT PRIMARY KEY,
                zones_geojson TEXT,
                points_geojson TEXT,
                timestamp INTEGER
            )
        """)
        conn.commit()
        return conn

    monkeypatch.setattr(ee, "get_cache_db", _get_cache_db)
    yield db_path


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Anula time.sleep en el módulo del extractor para no demorar tests."""
    monkeypatch.setattr(ee.time, "sleep", lambda *_: None)


@pytest.fixture(autouse=True)
def fake_token(monkeypatch):
    """get_cdse_token siempre devuelve un token mock."""
    monkeypatch.setattr(ee, "get_cdse_token", lambda: "TEST_TOKEN_XYZ")


@pytest.fixture(autouse=True)
def fresh_state_log():
    """Reset del log antes y después de cada test."""
    reset_cdse_state_log()
    yield
    reset_cdse_state_log()


@pytest.fixture
def geom_demo() -> Point:
    return Point(-62.10, -32.70).buffer(0.005).envelope


def _make_response(status_code: int, payload=None):
    """Construye un mock minimalista de requests.Response."""
    class _R:
        def __init__(self):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload) if payload is not None else ""

        def json(self):
            return self._payload or {}
    return _R()


def _ok_payload():
    return {"data": [{"outputs": {"default": {"bands": {"B0": {"stats": {
        "mean": 0.65, "stDev": 0.05, "min": 0.5, "max": 0.8,
    }}}}}}]}


def _preseed_cache(db_path: Path, cache_key: str, payload_json: str, age_seconds: float):
    conn = sqlite3.connect(str(db_path), timeout=10)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS statistics_cache (
            cache_key TEXT PRIMARY KEY,
            response_json TEXT,
            timestamp INTEGER
        )
    """)
    cur.execute("""
        INSERT OR REPLACE INTO statistics_cache (cache_key, response_json, timestamp)
        VALUES (?, ?, ?)
    """, (cache_key, payload_json, int(time.time() - age_seconds)))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 5 estados — uno por test
# ---------------------------------------------------------------------------
class TestState_FRESH:
    """API responde al primer intento, sin caché previa → FRESH."""

    def test_fresh_first_attempt(self, tmp_cache_db, geom_demo):
        with patch.object(ee.requests, "post",
                          return_value=_make_response(200, _ok_payload())):
            result = get_eodag_stats_with_state(geom_demo, 2026, 5)
        assert isinstance(result, CDSEResult)
        assert result.state == CDSEDataState.FRESH
        assert result.attempts == 1
        assert result.data is not None
        assert "intento 1/3" in result.message

    def test_fresh_after_retries(self, tmp_cache_db, geom_demo):
        """500 → 503 → 200: debe acabar en FRESH con attempts=3."""
        responses = [_make_response(500), _make_response(503),
                     _make_response(200, _ok_payload())]
        with patch.object(ee.requests, "post", side_effect=responses):
            result = get_eodag_stats_with_state(geom_demo, 2026, 5)
        assert result.state == CDSEDataState.FRESH
        assert result.attempts == 3


class TestState_CACHED_VALID:
    """Caché vigente (cache_age < 90% del TTL) → CACHED_VALID."""

    def test_recent_cache_used(self, tmp_cache_db, geom_demo):
        ck = ee.get_cache_key(geom_demo, 2026, 5)
        _preseed_cache(tmp_cache_db, ck, json.dumps([{"hello": "world"}]),
                       age_seconds=3600)  # 1 hora de edad
        with patch.object(ee.requests, "post") as p:
            result = get_eodag_stats_with_state(geom_demo, 2026, 5)
        assert result.state == CDSEDataState.CACHED_VALID
        assert result.attempts == 0
        assert result.data == [{"hello": "world"}]
        p.assert_not_called()  # caché vigente → no se llama a la API

    def test_past_year_cache_permanent(self, tmp_cache_db, geom_demo):
        """Para años pasados, cualquier caché es CACHED_VALID."""
        ck = ee.get_cache_key(geom_demo, 2020, 5)
        _preseed_cache(tmp_cache_db, ck, json.dumps([{"hist": True}]),
                       age_seconds=999_999)  # muy vieja, pero año pasado
        with patch.object(ee.requests, "post") as p:
            result = get_eodag_stats_with_state(geom_demo, 2020, 5)
        assert result.state == CDSEDataState.CACHED_VALID
        p.assert_not_called()


class TestState_CACHED_STALE:
    """Caché vigente pero por encima del 90% del TTL → CACHED_STALE."""

    def test_stale_threshold(self, tmp_cache_db, geom_demo):
        ck = ee.get_cache_key(geom_demo, 2026, 5)
        stale_age = int(CACHE_TTL_SECONDS_CURRENT_YEAR * (STALE_FRACTION + 0.02))
        _preseed_cache(tmp_cache_db, ck, json.dumps([{"stale": True}]),
                       age_seconds=stale_age)
        with patch.object(ee.requests, "post") as p:
            result = get_eodag_stats_with_state(geom_demo, 2026, 5)
        assert result.state == CDSEDataState.CACHED_STALE
        assert result.attempts == 0
        assert result.data == [{"stale": True}]
        # En STALE no intentamos refresh bloqueante (el caller decide)
        p.assert_not_called()


class TestState_CACHED_EXPIRED_FALLBACK:
    """Caché expirada + API down (3 intentos fallan) → CACHED_EXPIRED_FALLBACK."""

    def test_expired_cache_with_api_down(self, tmp_cache_db, geom_demo):
        ck = ee.get_cache_key(geom_demo, 2026, 5)
        _preseed_cache(tmp_cache_db, ck, json.dumps([{"expired": True}]),
                       age_seconds=CACHE_TTL_SECONDS_CURRENT_YEAR + 86400)
        with patch.object(ee.requests, "post",
                          return_value=_make_response(503)):
            result = get_eodag_stats_with_state(geom_demo, 2026, 5)
        assert result.state == CDSEDataState.CACHED_EXPIRED_FALLBACK
        assert result.attempts == 3  # se agotaron los 3 reintentos
        assert result.data == [{"expired": True}]
        assert "HTTP 503" in result.message

    def test_expired_cache_with_api_exception(self, tmp_cache_db, geom_demo):
        ck = ee.get_cache_key(geom_demo, 2026, 5)
        _preseed_cache(tmp_cache_db, ck, json.dumps([{"expired": True}]),
                       age_seconds=CACHE_TTL_SECONDS_CURRENT_YEAR + 100)
        with patch.object(ee.requests, "post",
                          side_effect=ConnectionError("network unreachable")):
            result = get_eodag_stats_with_state(geom_demo, 2026, 5)
        assert result.state == CDSEDataState.CACHED_EXPIRED_FALLBACK
        assert result.attempts == 3
        assert "ConnectionError" in result.message


class TestState_NO_DATA_AVAILABLE:
    """Primera consulta + API down → NO_DATA_AVAILABLE (caso crítico Tarea 4)."""

    def test_first_query_api_down(self, tmp_cache_db, geom_demo):
        with patch.object(ee.requests, "post",
                          return_value=_make_response(503)):
            result = get_eodag_stats_with_state(geom_demo, 2026, 5)
        assert result.state == CDSEDataState.NO_DATA_AVAILABLE
        assert result.attempts == 3
        assert result.data is None
        assert "Reintenta" in result.message

    def test_first_query_api_exception(self, tmp_cache_db, geom_demo):
        with patch.object(ee.requests, "post",
                          side_effect=TimeoutError("CDSE timed out")):
            result = get_eodag_stats_with_state(geom_demo, 2026, 5)
        assert result.state == CDSEDataState.NO_DATA_AVAILABLE
        assert result.attempts == 3
        assert result.data is None

    def test_first_query_no_token(self, tmp_cache_db, geom_demo, monkeypatch):
        monkeypatch.setattr(ee, "get_cdse_token", lambda: None)
        with patch.object(ee.requests, "post") as p:
            result = get_eodag_stats_with_state(geom_demo, 2026, 5)
        assert result.state == CDSEDataState.NO_DATA_AVAILABLE
        assert result.data is None
        p.assert_not_called()  # sin token, ni siquiera llegamos al POST


# ---------------------------------------------------------------------------
# Garantías generales
# ---------------------------------------------------------------------------
class TestNoExceptionsEverPropagate:
    """El sistema NUNCA debe tirar una excepción al caller, incluso
    bajo combinaciones adversas. Siempre debe haber CDSEResult."""

    @pytest.mark.parametrize("failure", [
        ConnectionError("DNS"),
        TimeoutError("timeout"),
        RuntimeError("anything"),
        OSError("disk full?"),
    ])
    def test_any_exception_is_caught(self, tmp_cache_db, geom_demo, failure):
        with patch.object(ee.requests, "post", side_effect=failure):
            result = get_eodag_stats_with_state(geom_demo, 2026, 5)
        assert isinstance(result, CDSEResult)
        assert result.state in {
            CDSEDataState.NO_DATA_AVAILABLE,
            CDSEDataState.CACHED_EXPIRED_FALLBACK,
        }

    def test_compat_api_returns_data_or_none(self, tmp_cache_db, geom_demo):
        """get_eodag_stats (API legacy) sigue devolviendo data o None.

        Usa coordenadas/meses distintos para evitar que la respuesta
        cacheada de la primera llamada contamine la segunda.
        """
        with patch.object(ee.requests, "post",
                          return_value=_make_response(200, _ok_payload())):
            data = get_eodag_stats(geom_demo, 2026, 5)
        assert data is not None
        # Segundo bloque: geometría y mes distintos → cache miss garantizado
        other_geom = Point(-63.50, -33.10).buffer(0.005).envelope
        with patch.object(ee.requests, "post",
                          return_value=_make_response(503)):
            data = get_eodag_stats(other_geom, 2026, 8)
        assert data is None


# ---------------------------------------------------------------------------
# Telemetría
# ---------------------------------------------------------------------------
class TestTelemetry:
    def test_worst_state_picks_most_severe(self, tmp_cache_db, geom_demo):
        # Primera consulta: FRESH
        with patch.object(ee.requests, "post",
                          return_value=_make_response(200, _ok_payload())):
            get_eodag_stats_with_state(geom_demo, 2026, 5)
        # Segunda consulta: NO_DATA_AVAILABLE
        with patch.object(ee.requests, "post",
                          return_value=_make_response(500)):
            get_eodag_stats_with_state(Point(-62.20, -32.80).buffer(0.005).envelope,
                                       2026, 6)
        assert get_worst_cdse_state() == CDSEDataState.NO_DATA_AVAILABLE

    def test_reset_clears_log(self, tmp_cache_db, geom_demo):
        with patch.object(ee.requests, "post",
                          return_value=_make_response(200, _ok_payload())):
            get_eodag_stats_with_state(geom_demo, 2026, 5)
        assert len(get_cdse_state_log()) == 1
        reset_cdse_state_log()
        assert len(get_cdse_state_log()) == 0
        assert get_worst_cdse_state() == CDSEDataState.FRESH  # neutral

    def test_log_records_year_month(self, tmp_cache_db, geom_demo):
        with patch.object(ee.requests, "post",
                          return_value=_make_response(200, _ok_payload())):
            get_eodag_stats_with_state(geom_demo, 2024, 3)
        log = get_cdse_state_log()
        assert log[0].year == 2024
        assert log[0].month == 3
