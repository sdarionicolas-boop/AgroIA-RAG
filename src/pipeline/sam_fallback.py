"""
src/pipeline/sam_fallback.py
============================

Lógica pura (sin Streamlit) para el fallback de delineación de lotes
cuando SAM falla, devuelve geometría con baja confianza o área absurda,
o cuando el usuario dibuja un polígono inválido.

Diseño:
  * Funciones puras y testeables sin levantar la UI.
  * Nunca tira excepciones crudas hacia el caller — encapsula errores
    de SAM y devuelve un `SamResult` con `source='buffer'` y mensaje.
  * Valida geometrías de usuario con `shapely.is_valid` + chequeo de
    área y rangos típicos de lotes agrícolas pampeanos.

Constantes operativas (revisar antes de cambiar):
  - BUFFER_DEG_HALF        : medio-lado del buffer envolvente (~500 m en Pampa).
  - MIN_AREA_HA            : área mínima razonable para considerar "lote" (0.5 ha).
  - MAX_AREA_HA            : área máxima razonable (50 000 ha; > eso = error).
  - DEFAULT_CONFIDENCE     : umbral mínimo de confianza para aceptar SAM (0.30).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Any

from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity, make_valid

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
BUFFER_DEG_HALF: float = 0.005
"""Medio-lado del buffer envolvente, en grados. ~500 m a lat ≈ -35°."""

MIN_AREA_HA: float = 0.5
"""Área mínima plausible para un lote agrícola (ha)."""

MAX_AREA_HA: float = 50_000.0
"""Área máxima plausible para un lote agrícola (ha)."""

DEFAULT_CONFIDENCE: float = 0.30
"""Umbral mínimo de confianza para aceptar una geometría SAM."""

DEG_TO_M_AT_PAMPA: float = 100_000.0
"""1° latitud ≈ 111 km; usamos 100 km como factor conservador para área aproximada."""


# ---------------------------------------------------------------------------
# Tipos de retorno
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SamResult:
    """Resultado de una delineación (SAM o fallback)."""
    geometry: BaseGeometry
    source: str  # 'sam' | 'buffer'
    confidence: Optional[float]
    area_ha: float
    message: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PolygonValidationResult:
    """Resultado de validar un polígono dibujado por el usuario."""
    valid: bool
    geometry: Optional[BaseGeometry]  # geometría reparada si se pudo, sino None
    reasons: tuple[str, ...]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def approximate_area_ha(geom: BaseGeometry) -> float:
    """Área aproximada en hectáreas para una geometría en EPSG:4326.

    Usa una proyección equirectangular degenerada (lat constante = lat centroide)
    suficiente para sanity-checking; NO sustituye reproyección a UTM para
    cálculos finales.
    """
    if geom is None or geom.is_empty:
        return 0.0
    try:
        c = geom.centroid
        import math
        deg_lon_m = DEG_TO_M_AT_PAMPA * math.cos(math.radians(c.y))
        deg_lat_m = DEG_TO_M_AT_PAMPA
        # area en grados² × (m/grado)² → m²; ÷ 10000 → ha
        return float(geom.area * deg_lon_m * deg_lat_m / 10_000.0)
    except Exception:
        return 0.0


def geometric_buffer_envelope(
    point: Point, buffer_deg: float = BUFFER_DEG_HALF
) -> Polygon:
    """Buffer envolvente cuadrado alrededor de un punto.

    Mantiene la convención histórica del repo: `Point.buffer(b).envelope`.
    Devuelve un rectángulo en EPSG:4326.
    """
    if not isinstance(point, Point):
        raise TypeError(f"se esperaba shapely Point, llegó {type(point).__name__}")
    return point.buffer(buffer_deg).envelope


# ---------------------------------------------------------------------------
# Validación de polígonos dibujados por usuario
# ---------------------------------------------------------------------------
def validate_user_polygon(
    geom: BaseGeometry,
    min_area_ha: float = MIN_AREA_HA,
    max_area_ha: float = MAX_AREA_HA,
) -> PolygonValidationResult:
    """Valida un polígono dibujado por el usuario.

    Reglas:
      1. Debe ser una geometría shapely no None y no vacía.
      2. `is_valid` debe ser True; si no, se intenta `make_valid()` y se
         retorna la geometría reparada si quedó válida; sino se rechaza.
      3. Área aproximada debe estar entre min/max.

    Nunca tira excepción: si algo falla, retorna `valid=False` con razón.
    """
    reasons: list[str] = []

    if geom is None:
        return PolygonValidationResult(False, None,
                                       ("geometría None — no se dibujó nada.",))
    if not isinstance(geom, BaseGeometry):
        return PolygonValidationResult(False, None,
                                       (f"tipo no soportado: {type(geom).__name__}",))
    if geom.is_empty:
        return PolygonValidationResult(False, None,
                                       ("geometría vacía.",))

    repaired: Optional[BaseGeometry] = None
    if not geom.is_valid:
        reason = explain_validity(geom)
        reasons.append(f"polígono inválido: {reason}")
        try:
            repaired = make_valid(geom)
        except Exception as e:
            reasons.append(f"intento de reparación falló: {e}")
            return PolygonValidationResult(False, None, tuple(reasons))
        if repaired is None or repaired.is_empty or not repaired.is_valid:
            reasons.append("reparación produjo geometría no utilizable.")
            return PolygonValidationResult(False, None, tuple(reasons))
        # OJO: make_valid puede devolver MultiPolygon / GeometryCollection.
        # Para downstream lo aceptamos solo si es Polygon o MultiPolygon.
        if repaired.geom_type not in ("Polygon", "MultiPolygon"):
            reasons.append(
                f"reparación produjo {repaired.geom_type}, no Polygon/MultiPolygon."
            )
            return PolygonValidationResult(False, None, tuple(reasons))
        reasons.append("polígono reparado con make_valid().")
        geom = repaired

    area_ha = approximate_area_ha(geom)
    if area_ha < min_area_ha:
        reasons.append(
            f"área {area_ha:.2f} ha < mínimo {min_area_ha} ha "
            "(¿punto único o polígono colapsado?)."
        )
        return PolygonValidationResult(False, None, tuple(reasons))
    if area_ha > max_area_ha:
        reasons.append(
            f"área {area_ha:.0f} ha > máximo {max_area_ha:.0f} ha "
            "(¿polígono cubre varios partidos?)."
        )
        return PolygonValidationResult(False, None, tuple(reasons))

    return PolygonValidationResult(True, geom, tuple(reasons))


# ---------------------------------------------------------------------------
# Delineación segura: SAM con fallback determinístico
# ---------------------------------------------------------------------------
SamRunner = Callable[[Point], dict[str, Any]]
"""Firma esperada del runner SAM. Debe retornar dict con keys:
   - 'geometry': shapely Polygon (o None si SAM no encontró nada)
   - 'confidence': float en [0, 1]
   - (opcional) 'message': str descriptiva.
   Puede tirar cualquier excepción; safe_sam_delineation la captura.
"""


def safe_sam_delineation(
    point: Point,
    sam_runner: Optional[SamRunner],
    confidence_threshold: float = DEFAULT_CONFIDENCE,
    min_area_ha: float = MIN_AREA_HA,
    max_area_ha: float = MAX_AREA_HA,
    buffer_deg: float = BUFFER_DEG_HALF,
) -> SamResult:
    """Intenta delinear con SAM; si falla por cualquier motivo, devuelve buffer.

    Casos cubiertos (todos devuelven SamResult, ninguno tira excepción):
      A. sam_runner es None              → buffer, message='sam_unavailable'
      B. sam_runner lanza excepción      → buffer, message=f'sam_exception: {e}'
      C. sam_runner devuelve None        → buffer, message='sam_no_geometry'
      D. confidence < threshold          → buffer, warning incluida
      E. geometría inválida (is_valid=F) → buffer, warning incluida
      F. área fuera de rango             → buffer, warning incluida
      G. todo OK                         → SamResult(source='sam')
    """
    warnings: list[str] = []
    buffer_geom = geometric_buffer_envelope(point, buffer_deg)
    buffer_area = approximate_area_ha(buffer_geom)

    # Caso A: runner no disponible
    if sam_runner is None:
        return SamResult(
            geometry=buffer_geom, source="buffer", confidence=None,
            area_ha=buffer_area,
            message="sam_unavailable: modelo SAM no instalado o no provisto.",
            warnings=tuple(warnings),
        )

    # Caso B: excepción del runner
    try:
        sam_out = sam_runner(point)
    except Exception as e:
        return SamResult(
            geometry=buffer_geom, source="buffer", confidence=None,
            area_ha=buffer_area,
            message=f"sam_exception: {type(e).__name__}: {e}",
            warnings=tuple(warnings),
        )

    # Caso C: runner devolvió None
    if sam_out is None:
        return SamResult(
            geometry=buffer_geom, source="buffer", confidence=None,
            area_ha=buffer_area,
            message="sam_no_geometry: SAM no devolvió delineación.",
            warnings=tuple(warnings),
        )

    geom = sam_out.get("geometry")
    confidence = sam_out.get("confidence")

    if geom is None:
        return SamResult(
            geometry=buffer_geom, source="buffer", confidence=confidence,
            area_ha=buffer_area,
            message="sam_no_geometry: payload sin clave 'geometry'.",
            warnings=tuple(warnings),
        )

    # Caso D: baja confianza
    if confidence is None:
        warnings.append("sam_no_confidence_reported")
        confidence_for_check = 0.0
    else:
        confidence_for_check = float(confidence)
    if confidence_for_check < confidence_threshold:
        return SamResult(
            geometry=buffer_geom, source="buffer", confidence=confidence,
            area_ha=buffer_area,
            message=(
                f"sam_low_confidence: {confidence_for_check:.3f} "
                f"< umbral {confidence_threshold:.3f}; usando buffer."
            ),
            warnings=tuple(warnings),
        )

    # Caso E: geometría inválida
    if not getattr(geom, "is_valid", False):
        return SamResult(
            geometry=buffer_geom, source="buffer", confidence=confidence,
            area_ha=buffer_area,
            message="sam_invalid_geometry: shapely.is_valid=False; usando buffer.",
            warnings=tuple(warnings),
        )

    # Caso F: área fuera de rango
    sam_area = approximate_area_ha(geom)
    if sam_area < min_area_ha or sam_area > max_area_ha:
        return SamResult(
            geometry=buffer_geom, source="buffer", confidence=confidence,
            area_ha=buffer_area,
            message=(
                f"sam_area_out_of_range: {sam_area:.2f} ha fuera de "
                f"[{min_area_ha}, {max_area_ha}]; usando buffer."
            ),
            warnings=tuple(warnings),
        )

    # Caso G: todo OK
    return SamResult(
        geometry=geom, source="sam", confidence=confidence,
        area_ha=sam_area,
        message=f"sam_ok: confianza {confidence_for_check:.3f}, área {sam_area:.1f} ha.",
        warnings=tuple(warnings),
    )
