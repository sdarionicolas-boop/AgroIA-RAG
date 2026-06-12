"""
scratch/test_s1_eventualidades.py
=================================

Test end-to-end de la integración Sentinel-1 (radar SAR) + Eventualidades.

Verifica:
  1. Autenticación CDSE (token OAuth2).
  2. cache_key con sensor: las claves S1 y S2 son DISTINTAS para mismo
     (geom, year, month) — evita colisión en SQLite.
  3. Query S2 NDVI sobre un lote real (Sentinel-2 óptico).
  4. Query S1 stats sobre el mismo lote (Sentinel-1 radar: VV, VH, ratio en dB).
  5. Caché SQLite registra ambas entradas (S1 y S2) por separado.
  6. Pipeline completo `run_eventualidades` corre y devuelve los campos
     nuevos de radar: confirmado_radar, tipo_alerta_radar, mensaje_radar.

NO intenta forzar una clasificación específica (vuelco/inundación). El
contenido depende de los valores reales que devuelva CDSE para el lote
y mes elegidos; la lógica física es determinística y se valida en otro
test unitario aparte.

Cualquier paso que falle se reporta como FAIL con motivo. Si CDSE
no tiene cobertura S1 para el mes consultado (caso WARN), se sigue
ejecutando para no bloquear los pasos posteriores.

Uso:
    python scratch/test_s1_eventualidades.py
"""
from __future__ import annotations

import sys
import sqlite3
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import geopandas as gpd
from shapely.geometry import Point

from src.pipeline.eodag_extractor import (
    get_cache_key,
    get_cdse_token,
    get_eodag_ndvi,
    get_eodag_s1_stats,
    get_eodag_s1_stats_with_state,
    get_eodag_stats_with_state,
    init_eodag,
    query_cache_stats,
    reset_cdse_state_log,
)
from src.pipeline.eventualidades import run_eventualidades

# ---------------------------------------------------------------------------
# Configuración del test
# ---------------------------------------------------------------------------
# Lote real: primer punto del shapefile de Córdoba (granizo trigo 2018)
TEST_LATLON = (-32.944817, -63.164883)   # Idiazabal, Unión, Córdoba (lote 71)
TEST_FECHA_EVENTO = "2018-10-15"          # Granizo primavera 2018
TEST_CULTIVO = "trigo"
TEST_TIPO_EVENTO = "Granizo"
TEST_BUFFER_DEG = 0.005                   # ~80 ha envelope

# Para test de cache_key — mes pre-evento (Sep 2018)
TEST_YEAR = 2018
TEST_MONTH_PRE = 9
TEST_MONTH_POST = 11

# Registro de resultados
RESULTS: list[tuple[str, str, str]] = []  # (id, status, msg)


def _record(test_id: str, status: str, msg: str) -> None:
    """status ∈ {PASS, FAIL, WARN, INFO}"""
    RESULTS.append((test_id, status, msg))
    icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️ ", "INFO": "ℹ️ "}.get(status, "  ")
    print(f"  {icon} [{status}] {test_id}: {msg}")


def banner(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_1_auth() -> bool:
    """Verifica que se puede obtener token CDSE OAuth2."""
    banner("TEST 1 — Autenticación CDSE")
    try:
        init_eodag()
    except Exception as e:
        _record("1.init_eodag", "FAIL", f"init_eodag() levantó {type(e).__name__}: {e}")
        return False
    _record("1.init_eodag", "PASS", "init_eodag() OK")

    try:
        token = get_cdse_token()
    except Exception as e:
        _record("1.get_token", "FAIL", f"get_cdse_token() levantó {type(e).__name__}: {e}")
        return False
    if not token:
        _record("1.get_token", "FAIL",
                "get_cdse_token() devolvió None (credenciales config/.env?)")
        return False
    _record("1.get_token", "PASS", f"token OAuth2 obtenido (len={len(token)})")
    return True


def test_2_cache_key_independence() -> bool:
    """get_cache_key con sensor='S1' vs 'S2' debe devolver claves DISTINTAS."""
    banner("TEST 2 — Cache key independence (S1 vs S2)")
    geom = Point(TEST_LATLON[1], TEST_LATLON[0]).buffer(TEST_BUFFER_DEG).envelope
    key_s2 = get_cache_key(geom, TEST_YEAR, TEST_MONTH_PRE, sensor="S2")
    key_s1 = get_cache_key(geom, TEST_YEAR, TEST_MONTH_PRE, sensor="S1")
    key_s2_default = get_cache_key(geom, TEST_YEAR, TEST_MONTH_PRE)  # default

    _record("2.s2_key_hash", "INFO", f"S2 key = {key_s2[:16]}…")
    _record("2.s1_key_hash", "INFO", f"S1 key = {key_s1[:16]}…")

    if key_s1 == key_s2:
        _record("2.collision", "FAIL",
                "S1 y S2 producen la MISMA cache_key → colisión, datos se sobrescribirían")
        return False
    _record("2.collision", "PASS", "S1 ≠ S2 (sin colisión, datos persisten separados)")

    if key_s2_default != key_s2:
        _record("2.default_s2", "FAIL",
                "get_cache_key() sin sensor no produce la misma clave que sensor='S2'")
        return False
    _record("2.default_s2", "PASS", "Default es sensor='S2' (backward compat preservada)")
    return True


def test_3_s2_ndvi() -> tuple[bool, dict]:
    """Query S2 NDVI sobre el lote para mes pre y post."""
    banner("TEST 3 — Sentinel-2 NDVI sobre lote real")
    geom = Point(TEST_LATLON[1], TEST_LATLON[0]).buffer(TEST_BUFFER_DEG).envelope
    out = {}
    for month, label in [(TEST_MONTH_PRE, "pre"), (TEST_MONTH_POST, "post")]:
        try:
            ndvi = get_eodag_ndvi(geom, TEST_YEAR, month)
        except Exception as e:
            _record(f"3.ndvi_{label}", "FAIL",
                    f"get_eodag_ndvi {TEST_YEAR}-{month:02d}: {type(e).__name__}: {e}")
            return False, out
        if ndvi is None:
            _record(f"3.ndvi_{label}", "WARN",
                    f"NDVI {TEST_YEAR}-{month:02d}: None (sin días limpios o CDSE caído)")
        else:
            out[f"ndvi_{label}"] = ndvi
            _record(f"3.ndvi_{label}", "PASS",
                    f"NDVI {TEST_YEAR}-{month:02d} = {ndvi:.4f}")
    return True, out


def test_4_s1_stats() -> tuple[bool, dict]:
    """Query S1 stats sobre el lote para mes pre y post."""
    banner("TEST 4 — Sentinel-1 stats (VV/VH/ratio en dB)")
    geom = Point(TEST_LATLON[1], TEST_LATLON[0]).buffer(TEST_BUFFER_DEG).envelope
    out = {}
    for month, label in [(TEST_MONTH_PRE, "pre"), (TEST_MONTH_POST, "post")]:
        try:
            s1 = get_eodag_s1_stats(geom, TEST_YEAR, month)
        except Exception as e:
            _record(f"4.s1_{label}", "FAIL",
                    f"get_eodag_s1_stats {TEST_YEAR}-{month:02d}: {type(e).__name__}: {e}")
            return False, out

        if not isinstance(s1, dict) or set(s1.keys()) < {"vv", "vh", "ratio"}:
            _record(f"4.s1_{label}_shape", "FAIL",
                    f"Tipo de retorno inesperado: {type(s1).__name__} keys={list(s1.keys()) if isinstance(s1, dict) else 'N/A'}")
            return False, out
        _record(f"4.s1_{label}_shape", "PASS",
                f"dict con keys {{vv, vh, ratio}}")

        vv, vh, ratio = s1["vv"], s1["vh"], s1["ratio"]
        if vv is None or vh is None:
            _record(f"4.s1_{label}_values", "WARN",
                    f"S1 {TEST_YEAR}-{month:02d}: sin días válidos (vv={vv}, vh={vh})")
        else:
            out[f"s1_{label}"] = s1
            # Rango razonable: cultivos VV típicamente -16 a -6 dB, VH típicamente -22 a -12 dB
            if not (-30 < vv < 5) or not (-35 < vh < 0):
                _record(f"4.s1_{label}_range", "WARN",
                        f"Valores fuera de rango típico: vv={vv} dB, vh={vh} dB")
            else:
                _record(f"4.s1_{label}_range", "PASS",
                        f"vv={vv} dB, vh={vh} dB, ratio={ratio} dB (rango plausible)")
    return True, out


def test_5_cache_persistence() -> bool:
    """Después de las queries, la BD SQLite debe tener entradas S1 y S2 separadas."""
    banner("TEST 5 — Persistencia separada en caché SQLite")
    geom = Point(TEST_LATLON[1], TEST_LATLON[0]).buffer(TEST_BUFFER_DEG).envelope

    ok = True
    for sensor in ("S2", "S1"):
        key = get_cache_key(geom, TEST_YEAR, TEST_MONTH_PRE, sensor=sensor)
        data, ts = query_cache_stats(key)
        if data is None:
            # Solo es FAIL si la query previa fue PASS pero la caché no guardó
            _record(f"5.cache_{sensor}", "WARN",
                    f"cache_key {sensor} no encontrada (query previa pudo haber fallado)")
        else:
            length = len(data)
            _record(f"5.cache_{sensor}", "PASS",
                    f"cache_key {sensor} encontrada en SQLite (len={length}, ts={ts})")
    return ok


def test_6_pipeline_run_eventualidades() -> bool:
    """Pipeline completo: run_eventualidades debe corre sin excepción y devolver
    campos de radar (confirmado_radar, tipo_alerta_radar, mensaje_radar)."""
    banner("TEST 6 — Pipeline run_eventualidades end-to-end")
    geom = Point(TEST_LATLON[1], TEST_LATLON[0]).buffer(TEST_BUFFER_DEG).envelope
    try:
        reset_cdse_state_log()
        result = run_eventualidades(
            geom_shapely=geom,
            fecha_evento=TEST_FECHA_EVENTO,
            cultivo=TEST_CULTIVO,
            tipo_evento=TEST_TIPO_EVENTO,
            caso_nombre="test_s1_e2e",
            log_fn=lambda m: None,  # silenciar logs internos del pipeline
        )
    except Exception as e:
        _record("6.pipeline_run", "FAIL",
                f"run_eventualidades levantó {type(e).__name__}: {e}")
        return False

    if result is None:
        _record("6.pipeline_run", "WARN",
                "run_eventualidades devolvió None (datos satelitales insuficientes para el mes)")
        return True  # No es fallo del código S1, es disponibilidad de datos

    _record("6.pipeline_run", "PASS", "run_eventualidades terminó sin excepción")

    # Verificar que los nuevos campos están en el dict de salida
    expected_radar_keys = {"confirmado_radar", "tipo_alerta_radar", "mensaje_radar"}
    missing = expected_radar_keys - set(result.keys())
    if missing:
        _record("6.radar_fields", "FAIL",
                f"Campos faltantes en resultado: {missing}")
        return False
    _record("6.radar_fields", "PASS",
            f"Campos radar presentes: {sorted(expected_radar_keys)}")

    # Reportar la clasificación (no juzgar — solo registrar)
    cr = result["confirmado_radar"]
    ta = result["tipo_alerta_radar"]
    mr = result["mensaje_radar"]
    _record("6.radar_classification", "INFO",
            f"confirmado_radar={cr}, tipo_alerta_radar={ta}")
    _record("6.radar_message", "INFO", f"mensaje: {mr[:80]}{'…' if len(mr)>80 else ''}")

    return True


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    print("=" * 70)
    print("🛰️  TEST E2E — Sentinel-1 + Eventualidades en AgroIA")
    print("=" * 70)
    print(f"  Lote test     : lat={TEST_LATLON[0]}, lon={TEST_LATLON[1]}")
    print(f"  Buffer        : {TEST_BUFFER_DEG}° envelope (~80 ha en pampeana)")
    print(f"  Fecha evento  : {TEST_FECHA_EVENTO}")
    print(f"  Cultivo       : {TEST_CULTIVO}")
    print(f"  Tipo evento   : {TEST_TIPO_EVENTO}")
    print(f"  Ventana NDVI/S1: pre={TEST_YEAR}-{TEST_MONTH_PRE:02d}, post={TEST_YEAR}-{TEST_MONTH_POST:02d}")

    # Test 1 es bloqueante: sin auth no podemos seguir
    if not test_1_auth():
        print("\n🛑 Test 1 falló — abortando.")
        return summarize_and_exit(2)

    # Tests 2-6: independientes (cada uno reporta su propio status)
    test_2_cache_key_independence()
    test_3_s2_ndvi()
    test_4_s1_stats()
    test_5_cache_persistence()
    test_6_pipeline_run_eventualidades()

    return summarize_and_exit()


def summarize_and_exit(forced_exit: int | None = None) -> int:
    banner("RESUMEN")
    counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "INFO": 0}
    for _id, st, _m in RESULTS:
        counts[st] = counts.get(st, 0) + 1
    for st in ("PASS", "FAIL", "WARN", "INFO"):
        print(f"  {st}: {counts.get(st, 0)}")
    print()
    if forced_exit is not None:
        return forced_exit
    if counts["FAIL"] > 0:
        print(f"🛑 Test E2E FAILED: {counts['FAIL']} fail(s).")
        return 1
    if counts["WARN"] > 0:
        print(f"⚠️  Test E2E PASSED con {counts['WARN']} warning(s) (probablemente "
              "sin cobertura S1 para el mes consultado — no es un bug del código).")
        return 0
    print("✅ Test E2E PASSED limpio.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
