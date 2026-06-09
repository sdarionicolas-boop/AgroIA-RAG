"""
scratch/validar_correlacion.py
==============================

Valida la correlación predictiva entre el Score AgroIA pre-evento y el
rinde real post-cosecha (kg/ha) por cultivo, usando NDVI medido sobre
Sentinel-2 vía CDSE Statistical API.

Reemplaza la versión anterior que sintetizaba NDVI a partir del ground
truth (ver `outputs/reporte_correlacion_score_DEPRECATED_synthetic_ndvi.md`).

Reglas duras:
  * SIN FALLBACK SINTÉTICO. Si CDSE no responde para un lote, ese lote se
    excluye con motivo registrado. Si fallan todos, el script aborta sin
    generar reporte (NO se inventan números).
  * Requiere CSV con `rinde_real_kg_ha` (kg/ha de monitor o tasación
    post-cosecha). El % de daño del peritaje NO sustituye al rinde.
  * Correlación calculada por cultivo separadamente (no se agrega
    trigo+maíz+soja en un único Pearson).
  * Reporta LOO-CV y bootstrap CI 95% (1000 iter, semilla fija).

Uso:
    python scratch/validar_correlacion.py \
        --input-csv path/to/lotes_validacion.csv \
        --ndvi-window pre_event \
        --output-md outputs/reporte_correlacion_predictiva.md

CSV de entrada — columnas obligatorias:
    lote_id           : identificador único
    cultivo           : trigo | maiz | soja | girasol
    fecha_siniestro   : YYYY-MM-DD
    fecha_cosecha     : YYYY-MM-DD
    geometria_wkt     : polígono del lote en WKT (EPSG:4326)
    rinde_real_kg_ha  : rinde real medido post-cosecha (kg/ha)

Columnas opcionales:
    horas_calor       : horas de calor en el período crítico (default 15.0)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import pearsonr
from shapely import wkt

# Inyectar src/ al path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from src.pipeline.eodag_extractor import get_eodag_ndvi, init_eodag  # noqa: E402
from src.pipeline.agro_math import CONFIG, calcular_score  # noqa: E402

REQUIRED_COLUMNS: dict[str, str] = {
    "lote_id": "Identificador único del lote",
    "cultivo": "Tipo de cultivo (trigo|maiz|soja|girasol)",
    "fecha_siniestro": "Fecha del evento (YYYY-MM-DD)",
    "fecha_cosecha": "Fecha de cosecha (YYYY-MM-DD)",
    "geometria_wkt": "Polígono del lote en WKT (EPSG:4326)",
    "rinde_real_kg_ha": "Rinde real medido post-cosecha (kg/ha)",
}
VALID_CROPS: set[str] = {"trigo", "maiz", "soja", "girasol"}
DEFAULT_HORAS_CALOR: float = 15.0
MIN_N_PER_CROP: int = 4  # mínimo para estadísticos
MIN_N_HIST_YEARS: int = 3  # mínimo de años históricos para Score


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Validación predictiva del Score AgroIA vs rinde real (kg/ha) "
            "con NDVI medido sobre Sentinel-2 (CDSE)."
        )
    )
    p.add_argument(
        "--input-csv", required=True,
        help="CSV con columnas: " + ", ".join(REQUIRED_COLUMNS),
    )
    p.add_argument(
        "--ndvi-window", default="pre_event",
        choices=["pre_event", "post_event", "both"],
        help="Ventana de NDVI relativa a la fecha del siniestro (default: pre_event).",
    )
    p.add_argument(
        "--pre-event-months", type=int, default=3,
        help="Meses ANTES del siniestro para ventana pre_event (default: 3).",
    )
    p.add_argument(
        "--post-event-months", type=int, default=1,
        help="Meses DESPUÉS del siniestro para ventana post_event (default: 1).",
    )
    p.add_argument(
        "--bootstrap-iter", type=int, default=1000,
        help="Iteraciones bootstrap para CI95%% (default: 1000).",
    )
    p.add_argument(
        "--seed", type=int, default=42,
        help="Semilla para bootstrap (default: 42).",
    )
    p.add_argument(
        "--output-md",
        default="outputs/reporte_correlacion_predictiva.md",
        help="Path del reporte markdown.",
    )
    p.add_argument(
        "--output-json",
        default="outputs/reporte_correlacion_predictiva.json",
        help="Path del JSON con métricas crudas.",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Validación de input
# ---------------------------------------------------------------------------
def validate_input(csv_path: Path) -> pd.DataFrame:
    if not csv_path.exists():
        sys.exit(f"❌ ABORT: el CSV de entrada no existe: {csv_path}")

    try:
        df = pd.read_csv(csv_path)
    except Exception as e:
        sys.exit(f"❌ ABORT: no se pudo leer el CSV ({e}).")

    missing = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        msg = ["❌ ABORT: faltan columnas obligatorias en el CSV:"]
        for col in sorted(missing):
            msg.append(f"   - {col}: {REQUIRED_COLUMNS[col]}")
        msg.append("")
        msg.append(
            "Si solo se dispone del % de daño del peritaje (no rinde real "
            "en kg/ha), la validación predictiva NO puede ejecutarse. "
            "Ver el protocolo de adquisición en "
            "`docs/validation/predictive_validation_plan.md` (Track B)."
        )
        sys.exit("\n".join(msg))

    df["cultivo"] = df["cultivo"].astype(str).str.lower().str.strip()
    df = df[df["cultivo"].isin(VALID_CROPS)].copy()
    df = df.dropna(
        subset=["rinde_real_kg_ha", "geometria_wkt", "fecha_siniestro"]
    )
    if df.empty:
        sys.exit(
            "❌ ABORT: no quedan filas válidas tras filtrar cultivos "
            "soportados y NaNs en columnas críticas."
        )
    return df


# ---------------------------------------------------------------------------
# Ventana NDVI y consulta CDSE
# ---------------------------------------------------------------------------
def build_window(
    fecha_evento: datetime,
    window: str,
    pre_months: int,
    post_months: int,
) -> list[tuple[int, int]]:
    months: list[tuple[int, int]] = []
    if window in ("pre_event", "both"):
        d = fecha_evento.replace(day=1)
        for _ in range(pre_months):
            d = d - timedelta(days=1)
            d = d.replace(day=1)
            months.append((d.year, d.month))
    if window in ("post_event", "both"):
        d = fecha_evento.replace(day=1)
        for _ in range(post_months):
            if d.month == 12:
                d = d.replace(year=d.year + 1, month=1)
            else:
                d = d.replace(month=d.month + 1)
            months.append((d.year, d.month))
    return months


def fetch_ndvi_series(
    geom, months: list[tuple[int, int]]
) -> tuple[list[float], list[tuple[int, int]]]:
    """Llama CDSE para cada (year, month). Retorna (válidos, faltantes). SIN síntesis."""
    valid: list[float] = []
    missing: list[tuple[int, int]] = []
    for year, month in months:
        val = get_eodag_ndvi(geom, year, month)
        if val is None:
            missing.append((year, month))
        else:
            valid.append(float(val))
    return valid, missing


# ---------------------------------------------------------------------------
# Estadísticos
# ---------------------------------------------------------------------------
def leave_one_out_correlation(
    scores: np.ndarray, rindes: np.ndarray
) -> dict[str, Any]:
    n = len(scores)
    if n < MIN_N_PER_CROP:
        return {"loo_r_mean": None, "loo_r_std": None, "n_folds": 0,
                "note": f"n={n} < {MIN_N_PER_CROP}: LOO-CV no aplicable."}
    r_values: list[float] = []
    for i in range(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        s_i, r_i = scores[mask], rindes[mask]
        if s_i.std() == 0 or r_i.std() == 0:
            continue
        r_val, _ = pearsonr(s_i, r_i)
        r_values.append(float(r_val))
    if not r_values:
        return {"loo_r_mean": None, "loo_r_std": None, "n_folds": 0,
                "note": "todas las particiones tuvieron desviación nula."}
    return {
        "loo_r_mean": float(np.mean(r_values)),
        "loo_r_std": float(np.std(r_values)),
        "n_folds": len(r_values),
    }


def bootstrap_ci(
    scores: np.ndarray,
    rindes: np.ndarray,
    n_iter: int,
    seed: int,
) -> dict[str, Any]:
    n = len(scores)
    if n < MIN_N_PER_CROP:
        return {"r_mean": None, "ci95_low": None, "ci95_high": None,
                "n_iter": 0,
                "note": f"n={n} < {MIN_N_PER_CROP}: bootstrap no aplicable."}
    rng = np.random.default_rng(seed)
    r_values: list[float] = []
    for _ in range(n_iter):
        idx = rng.integers(0, n, size=n)
        s_b, r_b = scores[idx], rindes[idx]
        if s_b.std() == 0 or r_b.std() == 0:
            continue
        r_val, _ = pearsonr(s_b, r_b)
        r_values.append(float(r_val))
    if not r_values:
        return {"r_mean": None, "ci95_low": None, "ci95_high": None,
                "n_iter": 0,
                "note": "todas las réplicas tuvieron desviación nula."}
    arr = np.array(r_values)
    return {
        "r_mean": float(arr.mean()),
        "ci95_low": float(np.percentile(arr, 2.5)),
        "ci95_high": float(np.percentile(arr, 97.5)),
        "n_iter": len(r_values),
    }


# ---------------------------------------------------------------------------
# Score por lote
# ---------------------------------------------------------------------------
def compute_score_for_row(
    row: pd.Series, ndvi_window_mean: float, ndvi_historico: list[float]
) -> int:
    conf = CONFIG[row["cultivo"]]
    horas_calor = float(row.get("horas_calor", DEFAULT_HORAS_CALOR))
    score_res = calcular_score(
        ndvi_critico=ndvi_window_mean,
        horas_calor=horas_calor,
        ndvi_historico=ndvi_historico,
        umbral_clima=conf["umbral_clima"],
    )
    return int(score_res["total"])


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------
def process_lotes(df: pd.DataFrame, args: argparse.Namespace):
    rows_out: list[dict[str, Any]] = []
    exclusions: dict[str, str] = {}
    for _, row in df.iterrows():
        lote_id = str(row["lote_id"])
        cultivo = row["cultivo"]

        # Geometría
        try:
            geom = wkt.loads(str(row["geometria_wkt"]))
        except Exception as e:
            exclusions[lote_id] = f"wkt_invalid: {e}"
            print(f"   ⚠️  {lote_id}: WKT inválido. Excluido.")
            continue
        if not geom.is_valid:
            exclusions[lote_id] = "geometry_not_valid"
            print(f"   ⚠️  {lote_id}: geometría no válida (shapely). Excluido.")
            continue

        # Fecha
        try:
            fecha_evento = datetime.strptime(
                str(row["fecha_siniestro"])[:10], "%Y-%m-%d"
            )
        except Exception:
            exclusions[lote_id] = "fecha_siniestro_invalid"
            print(f"   ⚠️  {lote_id}: fecha_siniestro inválida. Excluido.")
            continue

        # Ventana NDVI
        months = build_window(
            fecha_evento, args.ndvi_window,
            args.pre_event_months, args.post_event_months,
        )
        print(f"   🛰️  {lote_id} ({cultivo}): consultando CDSE en {len(months)} mes(es)...")
        ndvi_vals, missing = fetch_ndvi_series(geom, months)
        if missing:
            exclusions[lote_id] = f"cdse_missing_window_{len(missing)}_of_{len(months)}"
            print(f"      ❌ CDSE sin datos en {missing}. Excluido (sin fallback).")
            continue

        # Histórico para Score
        mes_critico = CONFIG[cultivo]["mes_critico"]
        hist_months = [(fecha_evento.year - i, mes_critico) for i in range(1, 6)]
        ndvi_hist, hist_missing = fetch_ndvi_series(geom, hist_months)
        if len(ndvi_hist) < MIN_N_HIST_YEARS:
            exclusions[lote_id] = (
                f"hist_insufficient_{len(ndvi_hist)}_of_{len(hist_months)}"
            )
            print(f"      ❌ Histórico insuficiente ({len(ndvi_hist)}/5). Excluido.")
            continue

        ndvi_window_mean = float(np.mean(ndvi_vals))
        ndvi_hist_full = ndvi_hist + [ndvi_window_mean]
        score = compute_score_for_row(row, ndvi_window_mean, ndvi_hist_full)

        rows_out.append({
            "lote_id": lote_id,
            "cultivo": cultivo,
            "fecha_siniestro": fecha_evento.strftime("%Y-%m-%d"),
            "n_meses_ndvi_window": len(ndvi_vals),
            "n_anios_hist": len(ndvi_hist),
            "ndvi_window_mean": round(ndvi_window_mean, 4),
            "score_agroia": score,
            "rinde_real_kg_ha": float(row["rinde_real_kg_ha"]),
        })

    return rows_out, exclusions


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------
def compute_per_crop_stats(
    df_res: pd.DataFrame, args: argparse.Namespace
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for cultivo, sub in df_res.groupby("cultivo"):
        n = len(sub)
        scores = sub["score_agroia"].to_numpy(dtype=float)
        rindes = sub["rinde_real_kg_ha"].to_numpy(dtype=float)
        block: dict[str, Any] = {"n": n}
        if n < MIN_N_PER_CROP:
            block["note"] = f"n={n} < {MIN_N_PER_CROP}. No se calculan estadísticos."
        elif scores.std() == 0 or rindes.std() == 0:
            block["note"] = "Desviación estándar nula en Score o rinde."
        else:
            r, p = pearsonr(scores, rindes)
            block["pearson_r"] = float(r)
            block["p_value"] = float(p)
            block["bootstrap"] = bootstrap_ci(
                scores, rindes, args.bootstrap_iter, args.seed
            )
            block["loo_cv"] = leave_one_out_correlation(scores, rindes)
        out[cultivo] = block
    return out


def write_markdown_report(
    out_path: Path, payload: dict[str, Any], df_res: pd.DataFrame
) -> None:
    lines: list[str] = []
    lines.append("# Reporte de Validación Predictiva — Score AgroIA vs Rinde Real")
    lines.append("")
    lines.append(
        f"_Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')} "
        f"(UTC local del runner)_"
    )
    lines.append("")
    lines.append("## Parámetros")
    lines.append("")
    lines.append(f"- **Input CSV:** `{payload['input_csv']}`")
    lines.append(f"- **Ventana NDVI:** `{payload['ndvi_window']}`")
    lines.append(f"- **Pre-event months:** {payload['pre_event_months']}")
    lines.append(f"- **Post-event months:** {payload['post_event_months']}")
    lines.append(f"- **Bootstrap iter:** {payload['bootstrap_iter']}  (semilla {payload['seed']})")
    lines.append(f"- **Lotes procesados:** {payload['n_total']}")
    lines.append(f"- **Lotes excluidos por falta de datos CDSE:** {payload['n_excluded']}")
    lines.append("- **Política de datos:** sin fallback sintético; lotes sin Sentinel-2 se excluyen.")
    lines.append("")
    lines.append("## Resultados por cultivo")
    lines.append("")
    lines.append("| Cultivo | N | Pearson R | p-value | Bootstrap R̄ | CI95% | LOO R̄ |")
    lines.append("|---|---|---|---|---|---|---|")
    for cultivo, block in payload["by_crop"].items():
        n = block["n"]
        if "pearson_r" not in block:
            note = block.get("note", "")
            lines.append(f"| {cultivo} | {n} | — | — | — | — | _{note}_ |")
            continue
        r = block["pearson_r"]
        p = block["p_value"]
        b = block["bootstrap"]
        lo = block["loo_cv"]
        ci = (
            f"[{b['ci95_low']:+.3f}, {b['ci95_high']:+.3f}]"
            if b.get("ci95_low") is not None else "—"
        )
        bmean = f"{b['r_mean']:+.3f}" if b.get("r_mean") is not None else "—"
        loomean = (
            f"{lo['loo_r_mean']:+.3f}"
            if lo.get("loo_r_mean") is not None else "—"
        )
        lines.append(
            f"| {cultivo} | {n} | {r:+.3f} | {p:.4f} | {bmean} | {ci} | {loomean} |"
        )
    lines.append("")
    lines.append("## Interpretación")
    lines.append("")
    lines.append("- p-value > 0.05 → no se rechaza H0 (no hay evidencia de correlación).")
    lines.append("- CI95% bootstrap que cruza 0 → coeficiente no distinguible de cero.")
    lines.append("- LOO R̄ alejado del R muestral → presencia de puntos influyentes / sobreajuste a la muestra.")
    lines.append("- Lotes excluidos por falta de Sentinel-2 NO se reemplazan por simulación.")
    lines.append("")
    lines.append("## Exclusiones")
    lines.append("")
    if payload["exclusions"]:
        for lid, motivo in payload["exclusions"].items():
            lines.append(f"- `{lid}`: {motivo}")
    else:
        lines.append("- (ninguna)")
    lines.append("")
    lines.append("## Matriz de datos procesados (NDVI real CDSE)")
    lines.append("")
    if df_res.empty:
        lines.append("(ninguna fila — no se pudo procesar ningún lote con datos CDSE reales)")
    else:
        lines.append(df_res.to_markdown(index=False))
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main() -> None:
    args = parse_args()
    csv_path = Path(args.input_csv).resolve()
    print("=" * 70)
    print("🌾 AgroIA — Validación predictiva (Score vs rinde real, kg/ha)")
    print("=" * 70)
    print(f"📄 Input CSV     : {csv_path}")
    print(f"🪟 Ventana NDVI  : {args.ndvi_window}")
    print(f"   Pre-event     : {args.pre_event_months} mes(es) antes del siniestro")
    print(f"   Post-event    : {args.post_event_months} mes(es) después del siniestro")
    print(f"🎲 Bootstrap     : {args.bootstrap_iter} iter (semilla {args.seed})")
    print()

    df = validate_input(csv_path)
    print(f"✅ {len(df)} filas válidas tras filtrado por cultivo y NaNs.")
    init_eodag()
    print()

    rows_out, exclusions = process_lotes(df, args)

    if exclusions:
        print("\n⚠️  Lotes excluidos por falta de datos CDSE (sin síntesis):")
        for lid, motivo in exclusions.items():
            print(f"     - {lid}: {motivo}")

    if not rows_out:
        sys.exit(
            "\n❌ ABORT: ningún lote pudo procesarse con NDVI real CDSE. "
            "No se genera reporte. No se usa fallback sintético."
        )

    df_res = pd.DataFrame(rows_out)
    print(f"\n📊 {len(df_res)} lotes con métricas completas. Distribución por cultivo:")
    print(df_res.groupby("cultivo")["lote_id"].count().to_string())

    payload: dict[str, Any] = {
        "input_csv": str(csv_path),
        "ndvi_window": args.ndvi_window,
        "pre_event_months": args.pre_event_months,
        "post_event_months": args.post_event_months,
        "bootstrap_iter": args.bootstrap_iter,
        "seed": args.seed,
        "n_total": len(df_res),
        "n_excluded": len(exclusions),
        "exclusions": exclusions,
        "by_crop": compute_per_crop_stats(df_res, args),
    }

    out_md = Path(args.output_md)
    out_json = Path(args.output_json)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    write_markdown_report(out_md, payload, df_res)
    print(f"\n✅ Reporte JSON     : {out_json}")
    print(f"✅ Reporte Markdown : {out_md}")


if __name__ == "__main__":
    main()
