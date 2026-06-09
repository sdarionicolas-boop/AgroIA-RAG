"""
scratch/validar_senal_satelital.py
==================================

Track B-minus: validación de la **señal satelital de daño** (delta NDVI
pre/post evento) contra el **Daño_Pond del peritaje** sobre el dataset
real `Cordoba/` (254 peritajes verificados, granizo trigo 2018).

Este script NO valida predicción de rinde (kg/ha) — eso queda para Track B
completo cuando exista dataset con `rinde_real_kg_ha` (ver
`docs/validation/predictive_validation_plan.md`).

Qué hace:
  1. Carga los 7 shapefiles de `Cordoba/`, deduplica el solape 45-60%,
     normaliza columnas (mojibake CP1252 → ASCII) y parsea lat/lon en
     formato DMS ('-32 43.453' → -32.7242).
  2. Para cada lote, calcula:
       * NDVI_pre  = media mensual de NDVI en el mes ANTERIOR al siniestro
       * NDVI_post = media mensual de NDVI en el mes POSTERIOR al siniestro
       * delta_rel = (NDVI_pre - NDVI_post) / max(NDVI_pre, 0.01)
     usando CDSE real (sin sintetizar). Si CDSE falla, el lote se excluye.
  3. Correlaciona delta_rel vs Daño_Pond por cohorte
     (cultivo × estadío fenológico) y global.
  4. Reporta Pearson R, Spearman ρ, bootstrap CI 95%, LOO-CV, N por
     cohorte, exclusiones, y telemetría de estados CDSE.

Limitaciones documentadas en el reporte:
  * Geometría = punto GPS + buffer envolvente ~80 ha en pampeana.
    La señal se diluye con caminos y vecindades; un R bajo en algunos
    cultivos no significa que la metodología falle, significa que el
    buffer no captura el lote real.
  * Cohorte dominante: trigo en estadíos avanzados de Sept-Oct 2018.
    Resultados sobre maíz/soja son referenciales por N bajo.
  * Daño_Pond es el ponderado del peritaje (ya integra "% del lote
    afectado" × "% de daño dentro de la franja"). No es un rinde
    medido post-cosecha.

Uso:
    # Dry-run: solo carga + parseo, sin tocar CDSE
    python scratch/validar_senal_satelital.py --dry-run

    # Sample de N lotes random (para verificar pipeline)
    python scratch/validar_senal_satelital.py --sample 5

    # Corrida completa
    python scratch/validar_senal_satelital.py --full \\
        --output-md outputs/validacion_senal_satelital_cordoba.md \\
        --output-json outputs/validacion_senal_satelital_cordoba.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Inyectar root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import geopandas as gpd  # noqa: E402
from scipy.stats import pearsonr, spearmanr  # noqa: E402
from shapely.geometry import Point  # noqa: E402

from src.pipeline.eodag_extractor import (  # noqa: E402
    CDSEDataState,
    get_eodag_ndvi,
    get_cdse_state_log,
    get_worst_cdse_state,
    init_eodag,
    reset_cdse_state_log,
)
from src.pipeline.sam_fallback import (  # noqa: E402
    approximate_area_ha,
    geometric_buffer_envelope,
)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
# Patrones de archivos a EXCLUIR del glob (shapefiles "de fondo" que no son peritajes,
# como `Cordoba.shp` o `Villegas.shp` con un solo polígono de contorno).
EXCLUDE_FILE_PATTERNS = (
    "Cordoba.shp",
    "Villegas.shp",
)

# Mapeo de columnas mojibake → ASCII normalizado.
# Los archivos .shp se grabaron con encoding que no respeta CP1252; al cargar
# con geopandas las ñ aparecen como U+FFFD (carácter de reemplazo).
COLUMN_MAP = {
    "Name": "lote_id",
    "Fecha_De_S": "fecha_siniestro_str",
    "Tipo_De_Si": "tipo_siniestro",
    "Cultivo": "cultivo_raw",
    "Has__Asegu": "has_aseguradas",
    "Estado_Fen": "estadio_fenologico_raw",
    "CG_Latitud": "lat_dms",
    "CG_Longitu": "lon_dms",
    "Localidad": "localidad",
    "Partido": "partido",
    "Provincia": "provincia",
}
# La columna Daño_Pond y Has__Dañada se detectan por contains('Pond') / contains('Da')
# porque los nombres reales tienen mojibake.

CULTIVO_MAP = {
    "trigo": "trigo",
    "maiz": "maiz",
    "maíz": "maiz",
    "ma�z": "maiz",  # mojibake
    "maiz 2da": "maiz",
    "maíz 2da": "maiz",
    "soja": "soja",
    "soja 1ra": "soja",
    "soja 2da": "soja",
    "girasol": "girasol",
    "sorgo": "sorgo",
    "cebada": "cebada",
}

# Cohortes mínimas para reportar estadísticos
MIN_N_COHORT = 5
DEFAULT_BOOTSTRAP_ITER = 1000
DEFAULT_PRE_DAYS_OFFSET = 30   # mes anterior al siniestro
DEFAULT_POST_DAYS_OFFSET = 30  # mes posterior


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def parse_dms(s: Any) -> float | None:
    """Parsea '-32 43.453' (grados-decimales-minutos) → grados decimales."""
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return None
    s = str(s).strip()
    if not s:
        return None
    parts = s.replace(",", ".").split()
    try:
        if len(parts) == 2:
            deg = float(parts[0])
            mins = float(parts[1])
            sign = -1 if str(parts[0]).startswith("-") else 1
            return sign * (abs(deg) + mins / 60.0)
        return float(s)
    except ValueError:
        return None


def find_dano_column(columns: list[str]) -> str | None:
    """Detecta la columna 'Daño_Pond' por sufijo aunque tenga mojibake."""
    for c in columns:
        cl = c.lower()
        if "pond" in cl and ("da" in cl or "dañ" in cl or "da�" in cl):
            return c
    return None


def find_has_danadas_column(columns: list[str]) -> str | None:
    for c in columns:
        cl = c.lower()
        if cl.startswith("has__") and "da" in cl and "asegu" not in cl:
            return c
    return None


def normalize_estadio(s: Any) -> str:
    """Mapea estadío fenológico (mojibake o normal) a 6 grupos consolidados.

    Reglas de agrupación (para que las cohortes tengan N ≥ 5):
      - vegetativo_temprano : V1-V5 (maíz/soja iniciales)
      - vegetativo_avanzado : V6+ (maíz/soja en crecimiento activo)
      - vegetativo_trigo    : 'encañazón' / 'macollaje' (trigo vegetativo)
      - reproductivo_inicio : espigazón / floración / R1-R3 / 'inicio flor'
      - llenado_grano       : grano lechoso / pastoso / formación de grano / R4-R5
      - madurez             : madurez fisiológica / R6
      - desconocido         : NaN
    """
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return "desconocido"
    s = str(s).lower().strip()
    # Trigo: encañazón / macollaje
    if "enca" in s or "enca�" in s or "macollaje" in s:
        return "vegetativo_trigo"
    # Reproductivo inicio: espigazón, floración, R1-R3, "inicio flor"
    if ("espig" in s or "floraci" in s or "inicio flor" in s
            or s.startswith("r1") or s.startswith("r2") or s.startswith("r3")):
        return "reproductivo_inicio"
    # Llenado de grano (lechoso / pastoso / R4-R5 / "formación de grano")
    if ("grano lech" in s or "grano past" in s or "form" in s
            or s.startswith("r4") or s.startswith("r5")):
        return "llenado_grano"
    # Madurez fisiológica / R6
    if "madur" in s or s.startswith("r6"):
        return "madurez"
    # Vegetativo V1-V15 (maíz/soja)
    if s.startswith("v") or s.startswith("vc") or s.startswith("g1"):
        # extraer número si lo hay
        digits = "".join(ch for ch in s.split("-")[0] if ch.isdigit())
        if digits:
            try:
                n_stage = int(digits)
                if n_stage <= 5:
                    return "vegetativo_temprano"
                return "vegetativo_avanzado"
            except ValueError:
                pass
        return "vegetativo_temprano"
    return "desconocido"


def normalize_cultivo(s: Any) -> str | None:
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return None
    key = str(s).lower().strip()
    return CULTIVO_MAP.get(key)


def parse_fecha_siniestro(s: Any) -> datetime | None:
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return None
    s = str(s).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def offset_month(d: datetime, offset_days: int) -> tuple[int, int]:
    """Retorna (year, month) del mes resultante de sumar offset_days a d."""
    target = d + timedelta(days=offset_days)
    return target.year, target.month


# ---------------------------------------------------------------------------
# Carga y normalización del dataset
# ---------------------------------------------------------------------------
def load_dataset(base_dir: Path,
                 provincia_filter: str | None = None) -> pd.DataFrame:
    """Carga todos los shapefiles del directorio + dedup + normaliza columnas.

    Args:
      base_dir         : directorio con los .shp de peritajes (Cordoba/, Villegas/, etc.)
      provincia_filter : si se pasa (e.g. "BUENOS AIRES"), subsetea por Provincia.
                         Match case-insensitive sobre el campo `Provincia` del shapefile.

    Filtra automáticamente los shapefiles de "fondo" (polígono único de contorno)
    listados en EXCLUDE_FILE_PATTERNS.
    """
    parts = []
    found_files = []
    for p in sorted(base_dir.glob("*.shp")):
        if p.name in EXCLUDE_FILE_PATTERNS:
            continue
        gdf = gpd.read_file(p)
        if len(gdf) == 0:
            continue
        # Filtrar shapefiles que son polígono único (fondo, no peritaje)
        if gdf.geometry.iloc[0].geom_type != "Point":
            continue
        gdf["_source_shp"] = p.name
        parts.append(gdf)
        found_files.append(p.name)
    if not parts:
        raise FileNotFoundError(f"No peritaje shapefiles found in {base_dir}")
    print(f"   Shapefiles cargados: {found_files}")
    raw = pd.concat(parts, ignore_index=True)

    dano_col = find_dano_column(list(raw.columns))
    has_dan_col = find_has_danadas_column(list(raw.columns))
    if dano_col is None:
        raise ValueError("No se encontró columna Daño_Pond en el dataset.")

    # Renombre defensivo
    rename = {k: v for k, v in COLUMN_MAP.items() if k in raw.columns}
    rename[dano_col] = "dano_pond"
    if has_dan_col:
        rename[has_dan_col] = "has_danadas"
    df = raw.rename(columns=rename).copy()

    # Parsear lat/lon DMS
    df["lat"] = df.get("lat_dms").apply(parse_dms) if "lat_dms" in df.columns else np.nan
    df["lon"] = df.get("lon_dms").apply(parse_dms) if "lon_dms" in df.columns else np.nan

    # Normalizar cultivo y estadío
    df["cultivo"] = df["cultivo_raw"].apply(normalize_cultivo)
    df["estadio"] = df["estadio_fenologico_raw"].apply(normalize_estadio)

    # Parsear fecha
    df["fecha_siniestro"] = df["fecha_siniestro_str"].apply(parse_fecha_siniestro)

    # Dedup: el archivo "Mayor a 45 o igual a 60" duplica al "Mayor a 45 y
    # menor o igual a 60". Dedup por (lote_id, fecha, cultivo).
    df = df.drop_duplicates(subset=["lote_id", "fecha_siniestro_str", "cultivo"])

    # Filtrar filas sin datos críticos
    df = df.dropna(subset=["lat", "lon", "fecha_siniestro", "cultivo", "dano_pond"])
    df = df[df["cultivo"].isin({"trigo", "maiz", "soja", "girasol",
                                "sorgo", "cebada"})]

    # Filtro opcional por provincia
    if provincia_filter and "provincia" in df.columns:
        before = len(df)
        df = df[df["provincia"].astype(str).str.upper().str.strip()
                == provincia_filter.upper().strip()]
        print(f"   Filtro provincia='{provincia_filter}': {before} → {len(df)} filas.")

    df = df.reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Cómputo del delta NDVI por lote
# ---------------------------------------------------------------------------
def compute_delta_for_row(
    row: pd.Series,
    pre_offset_days: int,
    post_offset_days: int,
    buffer_deg: float = 0.005,
) -> dict[str, Any]:
    """Calcula NDVI pre/post para un lote. Retorna dict con resultado o motivo."""
    fecha = row["fecha_siniestro"]
    geom = geometric_buffer_envelope(Point(row["lon"], row["lat"]),
                                     buffer_deg=buffer_deg)
    pre_year, pre_month = offset_month(fecha, -pre_offset_days)
    post_year, post_month = offset_month(fecha, post_offset_days)

    ndvi_pre = get_eodag_ndvi(geom, pre_year, pre_month)
    ndvi_post = get_eodag_ndvi(geom, post_year, post_month)

    out = {
        "lote_id": str(row["lote_id"]),
        "cultivo": row["cultivo"],
        "estadio": row["estadio"],
        "tipo_siniestro": row.get("tipo_siniestro"),
        "fecha_siniestro": fecha.strftime("%Y-%m-%d"),
        "dano_pond": float(row["dano_pond"]),
        "lat": float(row["lat"]),
        "lon": float(row["lon"]),
        "buffer_area_ha_approx": round(approximate_area_ha(geom), 1),
        "pre_year_month": f"{pre_year}-{pre_month:02d}",
        "post_year_month": f"{post_year}-{post_month:02d}",
        "ndvi_pre": float(ndvi_pre) if ndvi_pre is not None else None,
        "ndvi_post": float(ndvi_post) if ndvi_post is not None else None,
    }
    if ndvi_pre is None or ndvi_post is None:
        out["excluded"] = True
        missing = []
        if ndvi_pre is None:
            missing.append("ndvi_pre")
        if ndvi_post is None:
            missing.append("ndvi_post")
        out["exclusion_reason"] = f"cdse_missing: {','.join(missing)}"
        out["delta_rel"] = None
    else:
        out["excluded"] = False
        out["exclusion_reason"] = None
        ref = max(ndvi_pre, 0.01)
        out["delta_rel"] = float((ndvi_pre - ndvi_post) / ref * 100.0)
    return out


# ---------------------------------------------------------------------------
# Estadísticos
# ---------------------------------------------------------------------------
def bootstrap_ci(x: np.ndarray, y: np.ndarray, n_iter: int,
                 seed: int = 42) -> dict[str, Any]:
    n = len(x)
    if n < MIN_N_COHORT:
        return {"r_mean": None, "ci95_low": None, "ci95_high": None,
                "n_iter": 0, "note": f"n={n} < {MIN_N_COHORT}"}
    rng = np.random.default_rng(seed)
    rs = []
    for _ in range(n_iter):
        idx = rng.integers(0, n, size=n)
        xb, yb = x[idx], y[idx]
        if xb.std() == 0 or yb.std() == 0:
            continue
        r, _ = pearsonr(xb, yb)
        rs.append(float(r))
    if not rs:
        return {"r_mean": None, "ci95_low": None, "ci95_high": None,
                "n_iter": 0, "note": "todas las réplicas con varianza nula"}
    arr = np.array(rs)
    return {
        "r_mean": float(arr.mean()),
        "ci95_low": float(np.percentile(arr, 2.5)),
        "ci95_high": float(np.percentile(arr, 97.5)),
        "n_iter": len(rs),
    }


def loo_cv(x: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    n = len(x)
    if n < MIN_N_COHORT:
        return {"loo_r_mean": None, "n_folds": 0,
                "note": f"n={n} < {MIN_N_COHORT}"}
    rs = []
    for i in range(n):
        m = np.ones(n, dtype=bool)
        m[i] = False
        xi, yi = x[m], y[m]
        if xi.std() == 0 or yi.std() == 0:
            continue
        r, _ = pearsonr(xi, yi)
        rs.append(float(r))
    if not rs:
        return {"loo_r_mean": None, "n_folds": 0,
                "note": "todos los folds varianza nula"}
    return {
        "loo_r_mean": float(np.mean(rs)),
        "loo_r_std": float(np.std(rs)),
        "n_folds": len(rs),
    }


def cohort_stats(df: pd.DataFrame, label: str, n_iter: int) -> dict[str, Any]:
    n = len(df)
    block: dict[str, Any] = {"cohort": label, "n": n}
    if n < MIN_N_COHORT:
        block["note"] = f"n={n} < {MIN_N_COHORT}, no se calculan estadísticos"
        return block
    x = df["delta_rel"].to_numpy(dtype=float)
    y = df["dano_pond"].to_numpy(dtype=float)
    if x.std() == 0 or y.std() == 0:
        block["note"] = "desviación nula"
        return block
    r, p = pearsonr(x, y)
    rho, p_rho = spearmanr(x, y)
    block.update({
        "pearson_r": float(r), "pearson_p": float(p),
        "spearman_rho": float(rho), "spearman_p": float(p_rho),
        "delta_rel_mean": float(x.mean()), "delta_rel_std": float(x.std()),
        "dano_pond_mean": float(y.mean()), "dano_pond_std": float(y.std()),
        "bootstrap": bootstrap_ci(x, y, n_iter),
        "loo_cv": loo_cv(x, y),
    })
    return block


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------
def write_report(out_md: Path, payload: dict[str, Any],
                 df_res: pd.DataFrame) -> None:
    lines: list[str] = []
    lines.append("# Validación de Señal Satelital — Córdoba 2018 (Track B-minus)")
    lines.append("")
    lines.append(
        f"_Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')} "
        f"(hora local del runner)_"
    )
    lines.append("")
    lines.append("## Pregunta")
    lines.append("")
    lines.append(
        "¿La caída relativa del NDVI medida en Sentinel-2 (CDSE) entre el mes "
        "anterior y el mes posterior al siniestro correlaciona con el "
        "**Daño_Pond del peritaje en campo**, dentro de cada cohorte "
        "(cultivo × estadío fenológico)?"
    )
    lines.append("")
    lines.append("**Esto NO es Track B.** Track B exige rinde post-cosecha "
                 "(`rinde_real_kg_ha`) y este dataset no lo tiene. Acá se "
                 "valida la **señal satelital de daño**, no la predicción "
                 "de rinde.")
    lines.append("")
    lines.append("## Parámetros")
    lines.append("")
    lines.append(f"- Dataset: `Cordoba/` ({payload['n_dataset']} peritajes reales 2018)")
    lines.append(f"- Procesados: **{payload['n_processed']}** lotes con NDVI CDSE real")
    lines.append(f"- Excluidos por falta de Sentinel-2: **{payload['n_excluded']}**")
    lines.append(f"- Buffer envolvente alrededor del punto GPS: ~80 ha "
                 "(rectángulo ~1110 m × ~909 m)")
    lines.append(f"- Ventana NDVI: pre = mes anterior al siniestro, "
                 "post = mes posterior")
    lines.append(f"- Bootstrap: {payload['bootstrap_iter']} iter (semilla 42)")
    lines.append("")
    worst = payload.get("worst_cdse_state", "n/a")
    lines.append(f"- Estado CDSE peor de la corrida: **`{worst}`**")
    lines.append("")
    lines.append("## Limitaciones declaradas")
    lines.append("")
    lines.append("1. **Geometría = punto GPS + buffer envolvente ~80 ha.** "
                 "La señal NDVI se diluye con caminos, vecindades y mezcla "
                 "de cultivos. Un R bajo en una cohorte no significa que la "
                 "metodología falle: significa que el buffer no captura el "
                 "lote real. Para análisis actuarial se requiere polígono "
                 "del catastro (ver decisión SAM-only-en-B2C en AGENTS.md §13.1).")
    lines.append("2. **Cohorte dominante:** trigo en Sept-Oct 2018, "
                 "fenologías Encañazón / Espigazón / Grano Lechoso. "
                 "Resultados sobre maíz/soja son referenciales por N bajo.")
    lines.append("3. **Daño_Pond** ya integra '% del lote afectado × % de "
                 "daño dentro de la franja'. No es un rinde medido, es "
                 "un ponderado pericial subjetivo (pero independiente del NDVI).")
    lines.append("4. **Sin holdout temporal.** Todos los registros son de "
                 "la misma campaña; este reporte no estima generalización "
                 "a campañas futuras.")
    lines.append("")
    lines.append("## Resultados por cohorte (cultivo × estadío)")
    lines.append("")
    lines.append("| Cohorte | N | Pearson R | p | Spearman ρ | Bootstrap R̄ | CI95% | LOO R̄ |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for block in payload["per_cohort"]:
        cohort = block["cohort"]
        n = block["n"]
        if "pearson_r" not in block:
            lines.append(f"| {cohort} | {n} | — | — | — | — | — | _{block.get('note','')}_ |")
            continue
        r = block["pearson_r"]
        p = block["pearson_p"]
        rho = block["spearman_rho"]
        b = block["bootstrap"]
        lo = block["loo_cv"]
        ci = (f"[{b['ci95_low']:+.3f}, {b['ci95_high']:+.3f}]"
              if b.get("ci95_low") is not None else "—")
        bm = f"{b['r_mean']:+.3f}" if b.get("r_mean") is not None else "—"
        lm = f"{lo['loo_r_mean']:+.3f}" if lo.get("loo_r_mean") is not None else "—"
        lines.append(
            f"| {cohort} | {n} | {r:+.3f} | {p:.4f} | {rho:+.3f} | {bm} | {ci} | {lm} |"
        )
    lines.append("")
    lines.append("## Resultado global (todos los cultivos × estadíos)")
    lines.append("")
    g = payload.get("global", {})
    if "pearson_r" in g:
        b = g["bootstrap"]
        lines.append(f"- N = **{g['n']}**")
        lines.append(f"- Pearson R = **{g['pearson_r']:+.4f}** (p = {g['pearson_p']:.4e})")
        lines.append(f"- Spearman ρ = **{g['spearman_rho']:+.4f}** (p = {g['spearman_p']:.4e})")
        lines.append(f"- Bootstrap CI95% = [{b['ci95_low']:+.3f}, {b['ci95_high']:+.3f}]")
    else:
        lines.append(f"_{g.get('note','sin datos')}_ ")
    lines.append("")
    lines.append("## Interpretación")
    lines.append("")
    lines.append("- **p > 0.05 → no se rechaza H0** dentro de esa cohorte.")
    lines.append("- **CI95% que cruza 0** → coeficiente no distinguible de cero.")
    lines.append("- **LOO R̄ alejado del R muestral** → presencia de puntos "
                 "influyentes / inestabilidad.")
    lines.append("- Resultados estables (R > 0.4, CI95% no cruza 0, LOO ≈ R) "
                 "para una cohorte sugieren que la señal NDVI delta pre/post "
                 "es útil para esa fenología; resultados débiles sugieren que "
                 "el buffer 80 ha está diluyendo la señal en esos casos.")
    lines.append("")
    lines.append("## Lotes excluidos")
    lines.append("")
    if not df_res.empty:
        ex = df_res[df_res["excluded"]]
        if len(ex) == 0:
            lines.append("(ninguno)")
        else:
            lines.append(f"{len(ex)} lotes excluidos (CDSE sin datos). "
                         "Distribución por motivo:")
            for reason, count in Counter(ex["exclusion_reason"]).items():
                lines.append(f"- `{reason}`: {count}")
    lines.append("")
    lines.append("## Matriz de datos procesados (NDVI real CDSE)")
    lines.append("")
    df_ok = df_res[~df_res["excluded"]] if not df_res.empty else df_res
    if df_ok.empty:
        lines.append("(ninguna fila con CDSE válido)")
    else:
        cols = ["lote_id", "cultivo", "estadio", "fecha_siniestro",
                "dano_pond", "ndvi_pre", "ndvi_post", "delta_rel",
                "pre_year_month", "post_year_month"]
        lines.append(df_ok[cols].head(30).to_markdown(index=False))
        if len(df_ok) > 30:
            lines.append(f"\n_(mostrando primeras 30 de {len(df_ok)} filas; "
                         "ver JSON para set completo)_")
    lines.append("")
    out_md.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Validación de señal satelital sobre dataset real Córdoba 2018.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--input-dir", default="Cordoba",
                   help="Directorio con los shapefiles de peritajes (Cordoba, Villegas, etc.).")
    p.add_argument("--provincia", default=None,
                   help="Filtro opcional por provincia (e.g. 'BUENOS AIRES'). "
                        "Sin valor → no filtra.")
    p.add_argument("--dry-run", action="store_true",
                   help="Solo carga + parseo, sin tocar CDSE.")
    p.add_argument("--sample", type=int, default=0,
                   help="Procesar solo N lotes random (verificación). 0 = todos.")
    p.add_argument("--full", action="store_true",
                   help="Procesar TODOS los lotes (default si no se pasa --sample).")
    p.add_argument("--bootstrap-iter", type=int, default=DEFAULT_BOOTSTRAP_ITER)
    p.add_argument("--pre-days", type=int, default=DEFAULT_PRE_DAYS_OFFSET,
                   help="Días ANTES del siniestro para ventana pre (default 30).")
    p.add_argument("--post-days", type=int, default=DEFAULT_POST_DAYS_OFFSET,
                   help="Días DESPUÉS del siniestro para ventana post (default 30).")
    p.add_argument("--output-md",
                   default="outputs/validacion_senal_satelital_cordoba.md")
    p.add_argument("--output-json",
                   default="outputs/validacion_senal_satelital_cordoba.json")
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = parse_args()
    print("=" * 72)
    print("🌾 Validación de Señal Satelital — Córdoba 2018 (Track B-minus)")
    print("=" * 72)

    base = (PROJECT_ROOT / args.input_dir).resolve()
    print(f"📂 Dataset : {base}")
    if args.provincia:
        print(f"🗺️  Filtro provincia: {args.provincia}")
    df = load_dataset(base, provincia_filter=args.provincia)
    print(f"✅ Dataset cargado: {len(df)} peritajes válidos tras normalización.")
    print(f"   Cultivos: {df['cultivo'].value_counts().to_dict()}")
    print(f"   Estadíos: {df['estadio'].value_counts().to_dict()}")
    print(f"   Rango fechas: {df['fecha_siniestro'].min()} → "
          f"{df['fecha_siniestro'].max()}")

    if args.sample and args.sample > 0:
        df = df.sample(n=min(args.sample, len(df)),
                       random_state=args.seed).reset_index(drop=True)
        print(f"🎲 Sampling: {len(df)} lotes (semilla {args.seed})")

    if args.dry_run:
        print("\n[DRY-RUN] No se consulta CDSE. Mostrando primeras 10 filas:")
        cols = ["lote_id", "cultivo", "estadio", "fecha_siniestro", "lat", "lon",
                "dano_pond"]
        print(df[cols].head(10).to_string(index=False))
        return 0

    init_eodag()
    reset_cdse_state_log()

    rows_out: list[dict[str, Any]] = []
    for i, row in df.iterrows():
        if (i + 1) % 10 == 0 or i == 0:
            print(f"   [{i+1}/{len(df)}] {row['lote_id']} "
                  f"({row['cultivo']}, {row['fecha_siniestro'].strftime('%Y-%m')})")
        rows_out.append(
            compute_delta_for_row(row, args.pre_days, args.post_days)
        )

    df_res = pd.DataFrame(rows_out)
    df_ok = df_res[~df_res["excluded"]].copy()
    n_processed = len(df_ok)
    n_excluded = len(df_res) - n_processed
    print(f"\n📊 NDVI real obtenido para {n_processed}/{len(df_res)} lotes "
          f"({n_excluded} excluidos por CDSE).")

    # Estadísticos por cohorte
    cohort_blocks: list[dict[str, Any]] = []
    for (cultivo, estadio), sub in df_ok.groupby(["cultivo", "estadio"]):
        cohort_blocks.append(cohort_stats(sub, f"{cultivo} × {estadio}",
                                          args.bootstrap_iter))
    # Estadístico global
    global_block = cohort_stats(df_ok, "global", args.bootstrap_iter)

    payload = {
        "dataset_dir": str(base),
        "n_dataset": int(len(df_res)),
        "n_processed": n_processed,
        "n_excluded": n_excluded,
        "bootstrap_iter": args.bootstrap_iter,
        "pre_days": args.pre_days,
        "post_days": args.post_days,
        "per_cohort": cohort_blocks,
        "global": global_block,
        "worst_cdse_state": get_worst_cdse_state().value,
        "cdse_state_log_count": len(get_cdse_state_log()),
    }

    out_md = Path(args.output_md)
    out_json = Path(args.output_json)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    write_report(out_md, payload, df_res)
    print(f"\n✅ Reporte JSON     : {out_json}")
    print(f"✅ Reporte Markdown : {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
