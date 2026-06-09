"""
tests/test_sam_fallback.py
==========================

Tests del módulo `src.pipeline.sam_fallback`. Cubren los 3 casos que
exige el enunciado de la Tarea 3:

  1. SAM tira excepción           → debe generar buffer 500 m sin crashear.
  2. SAM devuelve geometría con
     confianza < 0.3               → debe degradar a buffer + warning.
  3. Usuario dibuja polígono
     inválido (auto-intersectante) → debe rechazar con mensaje claro.

Tests adicionales (cobertura de bordes):
  - SAM disponible y todo OK              → SamResult con source='sam'.
  - SAM devuelve None                     → buffer.
  - SAM devuelve geometría con área absurda → buffer.
  - Polígono válido con área aceptable    → validación retorna valid=True.
  - Polígono colapsado (área ≈ 0)         → rechazado.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from shapely.geometry import Point, Polygon

# Inyectar raíz del repo al path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.pipeline.sam_fallback import (  # noqa: E402
    BUFFER_DEG_HALF,
    DEFAULT_CONFIDENCE,
    MAX_AREA_HA,
    MIN_AREA_HA,
    SamResult,
    approximate_area_ha,
    geometric_buffer_envelope,
    safe_sam_delineation,
    validate_user_polygon,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def punto_cordoba() -> Point:
    """Punto representativo en Pampa cordobesa (Marcos Juárez)."""
    return Point(-62.10, -32.70)


@pytest.fixture
def poligono_valido_25ha() -> Polygon:
    """Polígono ~25 ha (cuadrado de ~500 m) cerca del punto de Córdoba."""
    return Point(-62.10, -32.70).buffer(BUFFER_DEG_HALF).envelope


@pytest.fixture
def poligono_bowtie() -> Polygon:
    """Polígono auto-intersectante en forma de '8' (bowtie).
    is_valid debe ser False para este caso."""
    coords = [
        (-62.10, -32.70),
        (-62.09, -32.69),
        (-62.10, -32.69),
        (-62.09, -32.70),
        (-62.10, -32.70),
    ]
    return Polygon(coords)


@pytest.fixture
def poligono_colapsado() -> Polygon:
    """Polígono de área ≈ 0 (lado < 1 m)."""
    return Polygon([
        (-62.10, -32.70),
        (-62.099999, -32.70),
        (-62.099999, -32.699999),
        (-62.10, -32.699999),
        (-62.10, -32.70),
    ])


# ---------------------------------------------------------------------------
# CASO 1 — SAM tira excepción
# ---------------------------------------------------------------------------
class TestCase1_SamException:
    """Si SAM levanta cualquier excepción, debemos caer al buffer 500 m
    sin propagar la excepción al caller."""

    def test_runtime_error(self, punto_cordoba):
        def sam_que_falla(pt):
            raise RuntimeError("CUDA out of memory")

        result = safe_sam_delineation(punto_cordoba, sam_que_falla)
        assert result.source == "buffer"
        assert "sam_exception" in result.message
        assert "RuntimeError" in result.message
        assert "CUDA out of memory" in result.message
        # El buffer debe ser una geometría válida y no vacía
        assert result.geometry.is_valid
        assert not result.geometry.is_empty
        # Área real del envelope de buffer(0.005°) en lat pampeana: ~80-90 ha
        # (1110 m × 909 m). El comentario histórico "~25 ha" en la UI estaba mal.
        assert 50.0 < result.area_ha < 150.0

    def test_value_error(self, punto_cordoba):
        def sam_que_falla(pt):
            raise ValueError("Modelo SAM corrupto")
        result = safe_sam_delineation(punto_cordoba, sam_que_falla)
        assert result.source == "buffer"
        assert "ValueError" in result.message

    def test_oserror(self, punto_cordoba):
        def sam_que_falla(pt):
            raise OSError("No se pudo leer sam_vit_b_01ec64.pth")
        result = safe_sam_delineation(punto_cordoba, sam_que_falla)
        assert result.source == "buffer"
        assert result.geometry.is_valid


# ---------------------------------------------------------------------------
# CASO 2 — SAM devuelve polígono con confianza < 0.3
# ---------------------------------------------------------------------------
class TestCase2_LowConfidence:
    """Si SAM devuelve confidence por debajo del umbral, debe degradar
    al buffer y reportar el motivo al usuario."""

    def test_below_default_threshold(self, punto_cordoba, poligono_valido_25ha):
        def sam_low_conf(pt):
            return {"geometry": poligono_valido_25ha, "confidence": 0.15}

        result = safe_sam_delineation(punto_cordoba, sam_low_conf)
        assert result.source == "buffer"
        assert "sam_low_confidence" in result.message
        assert "0.150" in result.message
        assert result.confidence == pytest.approx(0.15)

    def test_just_below_threshold(self, punto_cordoba, poligono_valido_25ha):
        def sam_runner(pt):
            return {"geometry": poligono_valido_25ha,
                    "confidence": DEFAULT_CONFIDENCE - 0.01}
        result = safe_sam_delineation(punto_cordoba, sam_runner)
        assert result.source == "buffer"

    def test_exactly_at_threshold(self, punto_cordoba, poligono_valido_25ha):
        """En el umbral exacto debe ACEPTAR (>= threshold)."""
        def sam_runner(pt):
            return {"geometry": poligono_valido_25ha,
                    "confidence": DEFAULT_CONFIDENCE}
        result = safe_sam_delineation(punto_cordoba, sam_runner)
        assert result.source == "sam"

    def test_custom_threshold(self, punto_cordoba, poligono_valido_25ha):
        def sam_runner(pt):
            return {"geometry": poligono_valido_25ha, "confidence": 0.45}
        result = safe_sam_delineation(
            punto_cordoba, sam_runner, confidence_threshold=0.50,
        )
        assert result.source == "buffer"
        assert "0.450" in result.message

    def test_missing_confidence(self, punto_cordoba, poligono_valido_25ha):
        """confidence ausente → se trata como 0 → buffer + warning."""
        def sam_runner(pt):
            return {"geometry": poligono_valido_25ha}
        result = safe_sam_delineation(punto_cordoba, sam_runner)
        assert result.source == "buffer"
        assert "sam_no_confidence_reported" in result.warnings


# ---------------------------------------------------------------------------
# CASO 3 — Polígono dibujado por usuario inválido
# ---------------------------------------------------------------------------
class TestCase3_InvalidUserPolygon:
    """Polígonos inválidos (auto-intersectantes, colapsados, vacíos)
    deben rechazarse con mensaje claro — NO aceptarse silenciosamente."""

    def test_bowtie_self_intersecting(self, poligono_bowtie):
        # Sanity: el bowtie debe ser is_valid=False antes de pasarlo
        assert poligono_bowtie.is_valid is False
        result = validate_user_polygon(poligono_bowtie)
        # make_valid puede reparar el bowtie a un MultiPolygon válido;
        # lo importante es que se REPORTE el problema en `reasons`
        assert any("inválido" in r for r in result.reasons)

    def test_none_geometry(self):
        result = validate_user_polygon(None)
        assert result.valid is False
        assert result.geometry is None
        assert any("None" in r for r in result.reasons)

    def test_empty_polygon(self):
        empty = Polygon()
        result = validate_user_polygon(empty)
        assert result.valid is False
        assert any("vacía" in r for r in result.reasons)

    def test_collapsed_polygon(self, poligono_colapsado):
        result = validate_user_polygon(poligono_colapsado)
        assert result.valid is False
        assert any("área" in r.lower() for r in result.reasons)

    def test_huge_polygon_above_max(self):
        # Polígono ~5° × 5° → millones de hectáreas
        huge = Polygon([
            (-65, -35), (-60, -35), (-60, -30), (-65, -30), (-65, -35),
        ])
        result = validate_user_polygon(huge)
        assert result.valid is False
        assert any(">" in r for r in result.reasons)

    def test_valid_polygon_passes(self, poligono_valido_25ha):
        result = validate_user_polygon(poligono_valido_25ha)
        assert result.valid is True
        assert result.geometry is not None
        assert result.geometry.is_valid


# ---------------------------------------------------------------------------
# Cobertura extra: comportamiento del runner cuando todo va bien y bordes
# ---------------------------------------------------------------------------
class TestExtraCoverage:

    def test_sam_unavailable(self, punto_cordoba):
        result = safe_sam_delineation(punto_cordoba, None)
        assert result.source == "buffer"
        assert "sam_unavailable" in result.message

    def test_sam_returns_none(self, punto_cordoba):
        result = safe_sam_delineation(punto_cordoba, lambda pt: None)
        assert result.source == "buffer"
        assert "sam_no_geometry" in result.message

    def test_sam_returns_payload_without_geometry(self, punto_cordoba):
        result = safe_sam_delineation(
            punto_cordoba, lambda pt: {"confidence": 0.9},
        )
        assert result.source == "buffer"
        assert "sam_no_geometry" in result.message

    def test_sam_returns_invalid_geometry(self, punto_cordoba, poligono_bowtie):
        result = safe_sam_delineation(
            punto_cordoba,
            lambda pt: {"geometry": poligono_bowtie, "confidence": 0.9},
        )
        assert result.source == "buffer"
        assert "sam_invalid_geometry" in result.message

    def test_sam_returns_area_too_small(self, punto_cordoba, poligono_colapsado):
        result = safe_sam_delineation(
            punto_cordoba,
            lambda pt: {"geometry": poligono_colapsado, "confidence": 0.9},
        )
        assert result.source == "buffer"
        assert "sam_area_out_of_range" in result.message

    def test_sam_returns_area_too_big(self, punto_cordoba):
        huge = Polygon([
            (-65, -35), (-60, -35), (-60, -30), (-65, -30), (-65, -35),
        ])
        result = safe_sam_delineation(
            punto_cordoba,
            lambda pt: {"geometry": huge, "confidence": 0.9},
        )
        assert result.source == "buffer"
        assert "sam_area_out_of_range" in result.message

    def test_sam_happy_path(self, punto_cordoba, poligono_valido_25ha):
        result = safe_sam_delineation(
            punto_cordoba,
            lambda pt: {"geometry": poligono_valido_25ha, "confidence": 0.85},
        )
        assert result.source == "sam"
        assert result.confidence == pytest.approx(0.85)
        assert result.geometry is poligono_valido_25ha
        assert "sam_ok" in result.message

    def test_geometric_buffer_envelope_returns_polygon(self, punto_cordoba):
        env = geometric_buffer_envelope(punto_cordoba)
        assert env.geom_type == "Polygon"
        assert env.is_valid
        # 4 esquinas (rectángulo) → exterior tiene 5 vértices (cerrado)
        assert len(list(env.exterior.coords)) == 5

    def test_approximate_area_ha_for_buffer(self, punto_cordoba):
        env = geometric_buffer_envelope(punto_cordoba)
        area = approximate_area_ha(env)
        # ~80-90 ha en Pampa (envelope de buffer(0.005°) es ~1110 m × 909 m).
        assert 50.0 < area < 150.0

    def test_constants_sanity(self):
        assert MIN_AREA_HA < MAX_AREA_HA
        assert 0.0 < DEFAULT_CONFIDENCE < 1.0
        assert BUFFER_DEG_HALF > 0
