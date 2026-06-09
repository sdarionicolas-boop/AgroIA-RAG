"""
demo/record_sam_fallback_demo.py
================================

Prepara un caso reproducible donde la delineación SAM falla y debe
caer al buffer geométrico. Sirve para grabar un video de demo del
hardening implementado en `src/pipeline/sam_fallback.py`.

Tres escenarios soportados (elegir con --scenario):

  water      Punto sobre el Río Paraná frente a Rosario. SAM no encuentra
             lote agrícola; el sistema debe caer al buffer y mostrar
             warning explicando que la geometría no fue aceptada.
             (Default — el más visual para video).

  urban      Punto sobre el centro de Rosario. SAM puede segmentar
             una manzana; el chequeo de área plausible debería rechazar
             la geometría si es < MIN_AREA_HA.

  invalid    Punto válido en Pampa, pero se inyecta una geometría
             auto-intersectante para forzar el path de validación
             en el bloque de polígono dibujado.

El script:
  1. Imprime el escenario y las coordenadas seleccionadas.
  2. Escribe un CSV de entrada compatible con el formato del modo
     siniestros (`id`, `lat`, `lon`, `fecha`) en demo/sam_demo_input.csv.
  3. Imprime los pasos manuales a seguir en Streamlit para grabar el
     video (no levanta Streamlit automáticamente).
  4. Si --execute se pasa, además invoca safe_sam_delineation contra un
     sam_runner que simula la falla esperada y deja el resultado en
     demo/sam_demo_result.json. Útil para verificar que el path queda
     en estado limpio antes de grabar.

Uso:
    python demo/record_sam_fallback_demo.py
    python demo/record_sam_fallback_demo.py --scenario water --execute
    python demo/record_sam_fallback_demo.py --scenario invalid

NO TIRA EXCEPCIONES — todos los caminos terminan en mensaje a stdout
y exit 0, salvo error de IO al escribir los archivos.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

# Inyectar raíz al path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


SCENARIOS = {
    "water": {
        "label": "Punto sobre el Río Paraná (frente a Rosario)",
        "lat": -32.945,
        "lon": -60.660,
        "expected_fallback": "buffer",
        "expected_reason_contains": "sam_",
        "description": (
            "El punto cae sobre agua. SAM no debería encontrar lote agrícola "
            "y la geometría devuelta (si la hay) debería fallar la "
            "validación de área plausible o is_valid."
        ),
    },
    "urban": {
        "label": "Punto sobre el centro de Rosario (zona urbana)",
        "lat": -32.949,
        "lon": -60.642,
        "expected_fallback": "buffer",
        "expected_reason_contains": "sam_",
        "description": (
            "El punto cae sobre tejido urbano. SAM puede segmentar una "
            "manzana o cuadra; el chequeo de área mínima debería rechazarla."
        ),
    },
    "invalid": {
        "label": "Punto válido en Pampa cordobesa + geometría manual auto-intersectante",
        "lat": -32.700,
        "lon": -62.100,
        "expected_fallback": "buffer",
        "expected_reason_contains": "inválido",
        "description": (
            "Para el bloque de polígono dibujado (Folium Draw). Se demuestra "
            "el rechazo del bowtie y el mensaje claro al usuario."
        ),
    },
}


def write_input_csv(scenario_key: str, lat: float, lon: float, out_path: Path) -> None:
    import csv
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "lat", "lon", "fecha"])
        w.writerow([
            f"DEMO_SAM_FALLBACK_{scenario_key.upper()}",
            lat, lon, date.today().isoformat(),
        ])


def execute_scenario(scenario_key: str) -> dict:
    """Invoca la lógica de fallback con un sam_runner mockeado que simula
    el modo de falla del escenario. Permite verificar que el reporte
    queda en estado limpio antes de grabar el video.
    """
    from shapely.geometry import Point, Polygon
    from src.pipeline.sam_fallback import (
        safe_sam_delineation,
        validate_user_polygon,
    )

    s = SCENARIOS[scenario_key]
    pt = Point(s["lon"], s["lat"])

    if scenario_key == "water":
        # SAM tira excepción simulando que el segmentador no encuentra parcela
        def sam_runner(_):
            raise RuntimeError("No vegetation mask found over water surface.")
        result = safe_sam_delineation(pt, sam_runner)
        return {
            "scenario": scenario_key,
            "path": "safe_sam_delineation",
            "source": result.source,
            "message": result.message,
            "area_ha": result.area_ha,
            "confidence": result.confidence,
        }

    if scenario_key == "urban":
        # SAM devuelve un polígono diminuto (~0.1 ha) simulando una manzana
        manzana = pt.buffer(0.0003).envelope  # ~30 m de lado → ~0.09 ha
        def sam_runner(_):
            return {"geometry": manzana, "confidence": 0.62}
        result = safe_sam_delineation(pt, sam_runner)
        return {
            "scenario": scenario_key,
            "path": "safe_sam_delineation",
            "source": result.source,
            "message": result.message,
            "area_ha": result.area_ha,
            "confidence": result.confidence,
        }

    if scenario_key == "invalid":
        # Bowtie auto-intersectante para el path de Folium Draw
        bowtie = Polygon([
            (s["lon"], s["lat"]),
            (s["lon"] + 0.01, s["lat"] + 0.01),
            (s["lon"], s["lat"] + 0.01),
            (s["lon"] + 0.01, s["lat"]),
            (s["lon"], s["lat"]),
        ])
        validation = validate_user_polygon(bowtie)
        return {
            "scenario": scenario_key,
            "path": "validate_user_polygon",
            "valid": validation.valid,
            "reasons": list(validation.reasons),
            "geometry_repaired": validation.geometry.geom_type if validation.geometry else None,
        }

    return {"scenario": scenario_key, "error": "unknown scenario"}


def print_manual_steps(scenario_key: str, csv_path: Path) -> None:
    s = SCENARIOS[scenario_key]
    print("\n" + "=" * 68)
    print(f"📹 PASOS PARA GRABAR EL VIDEO — escenario: {scenario_key}")
    print("=" * 68)
    print(f"Escenario : {s['label']}")
    print(f"Lat/Lon   : {s['lat']}, {s['lon']}")
    print(f"Fallback esperado: {s['expected_fallback']}")
    print(f"Razón esperada contiene: {s['expected_reason_contains']!r}")
    print(f"CSV de entrada generado: {csv_path}")
    print()
    print("Pasos:")
    print("  1. Levantar Streamlit:")
    print("       python start.py")
    print("  2. Abrir http://localhost:8501")
    print("  3. Ir al modo 'Siniestros (Eventualidades)'.")
    print(f"  4. Cargar el CSV demo: {csv_path}")
    if scenario_key == "invalid":
        print("  5. Elegir método '✍️ Dibujar Polígono en Mapa'.")
        print("  6. Dibujar un polígono en forma de '8' (bowtie).")
        print("     Esperado: banner de error rojo rechazando la geometría.")
    else:
        print("  5. Elegir método '🧠 Automático (SAM con Fallback)'.")
        print("  6. Presionar '🚀 Ejecutar Peritaje Inteligente'.")
        print("     Esperado:")
        print("       a. Warning amarillo: 'Delineación SAM no aceptada (...)'")
        print("       b. Mensaje verde: 'Fallback de buffer geométrico (500m) aplicado'")
        print("       c. El análisis posterior corre sobre el buffer.")
    print("=" * 68)


def main() -> int:
    # Forzar UTF-8 en stdout para que los emojis no crasheen en Windows (cp1252)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--scenario", default="water", choices=list(SCENARIOS),
        help="Modo de falla a demostrar (default: water).",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Además invocá la lógica de fallback localmente y dejá el "
             "resultado en demo/sam_demo_result.json.",
    )
    args = parser.parse_args()

    s = SCENARIOS[args.scenario]
    csv_path = _root / "demo" / "sam_demo_input.csv"
    write_input_csv(args.scenario, s["lat"], s["lon"], csv_path)
    print(f"✅ CSV de entrada escrito: {csv_path}")

    if args.execute:
        result = execute_scenario(args.scenario)
        result_path = _root / "demo" / "sam_demo_result.json"
        result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False),
                               encoding="utf-8")
        print(f"✅ Resultado del fallback escrito: {result_path}")
        print(json.dumps(result, indent=2, ensure_ascii=False))

    print_manual_steps(args.scenario, csv_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
