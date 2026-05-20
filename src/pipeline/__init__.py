# src/pipeline/__init__.py
import os
import sys
import json
import geopandas as gpd
from datetime import datetime
import ee

# Fix Unicode output on Windows cp1252 terminals
try:
    if sys.stdout and hasattr(sys.stdout, 'encoding') and sys.stdout.encoding and sys.stdout.encoding.lower().startswith('cp'):
        sys.stdout.reconfigure(errors='replace')
        sys.stderr.reconfigure(errors='replace')
except Exception:
    pass

import builtins
_original_print = builtins.print
def _safe_print(*args, **kwargs):
    """Print wrapper that never crashes on encoding errors."""
    try:
        _original_print(*args, **kwargs)
    except (UnicodeEncodeError, OSError):
        # Fallback: strip non-ASCII and retry
        try:
            safe_args = [str(a).encode('ascii', 'replace').decode('ascii') for a in args]
            _original_print(*safe_args, **kwargs)
        except Exception:
            pass
print = _safe_print

from src.utils.config import settings
from .gee_extractor import (
    init_gee, get_gee_ndvi, get_gee_ndvi_ventana,
    calcular_cv_gee, zonificar_lote_gee
)
from .nasa_power import get_nasa_climate_safe
from .agro_math import CONFIG, calcular_score, get_gee_ndvi_validado, validar_ndvi
from .reporter import build_report, generar_mapa_offline
from .utils import validar_shapefile
from .ingesta import construir_payload_v2, enviar_al_rag


# ─────────────────────────────────────────────────────────────────────────────
# NÚCLEO — análisis de un solo polígono (ya validado, sin re-init GEE)
# ─────────────────────────────────────────────────────────────────────────────

def _analyze_one_polygon(lote_gdf, lote_id, cultivo, years, epsg_utm, push_to_rag):
    """
    Ejecuta los pasos 2-6 del pipeline sobre un GeoDataFrame de un solo polígono.
    GEE ya debe estar inicializado antes de llamar a esta función.

    Parámetros
    ----------
    lote_gdf    : GeoDataFrame con una sola geometría en EPSG:4326
    lote_id     : str  — identificador único del lote
    cultivo     : str  — clave en CONFIG (maiz, soja, trigo, girasol)
    years       : list[int] — años a analizar
    epsg_utm    : int  — EPSG UTM dinámico del centroide
    push_to_rag : bool — si True, envía el payload a la API local
    """
    centroide = lote_gdf.geometry.iloc[0].centroid
    coords = [list(c) for c in lote_gdf.geometry.iloc[0].exterior.coords]
    geom_ee = ee.Geometry.Polygon([coords])
    hectareas = round(lote_gdf.to_crs(lote_gdf.estimate_utm_crs()).geometry.area.sum() / 10_000, 2)
    conf = CONFIG[cultivo]
    log_processing = [f"Análisis iniciado para {lote_id} ({cultivo})"]

    # 2. Clima NASA POWER
    print(f"  🌡️  NASA POWER...")
    horas_calor_hist = {}
    for yr in years:
        h, status_clima = get_nasa_climate_safe(centroide.y, centroide.x, yr, conf)
        horas_calor_hist[yr] = h
        log_processing.append(f"NASA POWER {yr}: {h:.1f}h ({status_clima})")

    # 3. Satelital GEE Sentinel-2
    print(f"  🛰️  GEE Sentinel-2...")
    ndvi_hist = {}
    ndvi_hist_bruto = {}
    anos_excluidos = []

    for yr in years:
        val, status, msg = get_gee_ndvi_validado(geom_ee, yr, conf['mes_critico'], cultivo)
        if val is not None and status == 'ok':
            ndvi_hist[yr] = val
            ndvi_hist_bruto[yr] = val
            log_processing.append(f"GEE {yr}: NDVI {val:.3f} ({status})")

        if yr not in ndvi_hist:
            val_fb, mes_fb, aviso_fb = get_gee_ndvi_ventana(geom_ee, yr, conf['mes_critico'])
            if val_fb is not None:
                ndvi_hist[yr] = val_fb
                ndvi_hist_bruto[yr] = val_fb
                log_processing.append(f"GEE {yr}: NDVI {val_fb:.3f} (Ventana mes {mes_fb})")
            else:
                anos_excluidos.append(yr)
                val_raw = get_gee_ndvi(geom_ee, yr, conf['mes_critico'])
                if val_raw:
                    ndvi_hist_bruto[yr] = val_raw
                log_processing.append(f"GEE {yr}: EXCLUIDO - {aviso_fb}")

    # 4. Score y Zonificación
    años_procesados = sorted(ndvi_hist.keys())
    if not años_procesados:
        print(f"  ❌ {lote_id}: sin datos satelitales válidos. Saltando.")
        return None

    año_fin = años_procesados[-1]
    cv, _, _ = calcular_cv_gee(geom_ee, año_fin, conf['mes_critico'])
    zonas_gdf = zonificar_lote_gee(geom_ee, año_fin, conf['mes_critico']) if cv and cv > 0.05 else None

    historial_full = {}
    for yr in años_procesados:
        previos = [ndvi_hist[y] for y in años_procesados if y < yr]
        s = calcular_score(ndvi_hist[yr], horas_calor_hist.get(yr, 0), previos + [ndvi_hist[yr]], conf['umbral_clima'])
        historial_full[yr] = {
            "total": s["total"], "vigor": s["vigor"], "estabilidad": s["estabilidad"],
            "limpieza": s["limpieza"], "clima": s["clima"],
            "ndvi": ndvi_hist[yr], "horas_calor": horas_calor_hist.get(yr, 0), "valido": True
        }

    score_act = historial_full[año_fin]

    # 5. Reportes
    print(f"  📄 Reportes...")
    res = {
        "cultivo": cultivo, "hectareas": hectareas, "centroide": (centroide.y, centroide.x),
        "score": score_act, "ndvi_historico": ndvi_hist, "ndvi_critico_actual": ndvi_hist[año_fin],
        "ndvi_hist_bruto": ndvi_hist_bruto, "anos_excluidos": anos_excluidos,
        "horas_calor_hist": horas_calor_hist, "horas_calor_actual": horas_calor_hist[año_fin],
        "cv": cv, "es_variable": (zonas_gdf is not None), "lote_gdf": lote_gdf,
        "zonas_gdf": zonas_gdf, "conf": conf, "epsg_utm": epsg_utm, "log": log_processing
    }
    os.makedirs("outputs", exist_ok=True)
    pdf = build_report(res, f"outputs/AgroIA_{lote_id}.pdf", lote_id)
    html = generar_mapa_offline(lote_gdf, cultivo, score_act['total'], conf, zonas_gdf, f"outputs/Mapa_{lote_id}.html")
    print(f"  ✓ PDF: {pdf}  |  Mapa: {html}")

    # 6. Ingesta RAG
    if push_to_rag:
        print(f"  📡 RAG Ingesta...")
        payload = construir_payload_v2(
            lote_id=lote_id, cultivo=cultivo, superficie_ha=hectareas, fecha_analisis=datetime.now().date(),
            ndvi_critico=ndvi_hist[año_fin], horas_calor=horas_calor_hist[año_fin],
            score_total=score_act['total'], score_vigor=score_act['vigor'],
            score_estabilidad=score_act['estabilidad'], score_limpieza=score_act['limpieza'],
            score_clima=score_act['clima'], cv_espacial=cv if cv else 0.0,
            zona_activa=(zonas_gdf is not None),
            puntos_zona_c=len(zonas_gdf[zonas_gdf['zona'] == 'C']) if zonas_gdf is not None else 0,
            historial_full=historial_full,
            centroide=[centroide.y, centroide.x]
        )
        enviar_al_rag("http://localhost:8000", settings.ingesta_secret_key, payload)

    print(f"  ✅ {lote_id} completado (Score: {score_act['total']}/100)\n")
    return res


# ─────────────────────────────────────────────────────────────────────────────
# ENTRADA ÚNICA — un shapefile / GeoJSON
# ─────────────────────────────────────────────────────────────────────────────

def run_full_analysis(shp_path, cultivo="maiz", years=None, lote_id=None, push_to_rag=True):
    """
    Ejecuta el pipeline completo de AgroIA sobre un único shapefile o GeoJSON.
    Si el archivo contiene múltiples polígonos, se analiza solo el de mayor área.

    Uso:  python start.py --pipeline <ruta.shp|ruta.geojson> [cultivo]
    """
    if years is None:
        año_actual = datetime.now().year
        years = list(range(año_actual - 5, año_actual + 1))

    shp_path = os.path.normpath(shp_path.lstrip('@'))

    if lote_id is None:
        lote_id = os.path.splitext(os.path.basename(shp_path))[0]

    print(f"\n🚀 [AgroIA] Iniciando análisis completo: {lote_id}")

    init_gee(project_id=settings.gee_project_id)
    lote_gdf, _, epsg_utm = validar_shapefile(shp_path)

    return _analyze_one_polygon(lote_gdf, lote_id, cultivo, years, epsg_utm, push_to_rag)


# ─────────────────────────────────────────────────────────────────────────────
# BATCH — GeoJSON del poligonizador → analiza cada polígono individualmente
# ─────────────────────────────────────────────────────────────────────────────

def run_batch_from_geojson(geojson_path, cultivo_default="maiz", years=None,
                           push_to_rag=True, limit=None, id_prefix="POLIGONO"):
    """
    Procesa cada polígono de un GeoJSON (salida del poligonizador SAM) de forma
    individual, inicializando GEE una sola vez para toda la tanda.

    Parámetros
    ----------
    geojson_path    : str  — ruta al GeoJSON del poligonizador
    cultivo_default : str  — cultivo si el polígono no tiene propiedad 'cultivo'
    years           : list[int] | None — años a analizar (default: últimos 6)
    push_to_rag     : bool — si True, ingesta cada lote al RAG al terminar
    limit           : int | None — si se indica, procesa solo los primeros N polígonos
    id_prefix       : str  — prefijo para lote_id si el polígono no tiene propiedad 'id'

    Retorna
    -------
    list[dict] — resumen por polígono: lote_id, status, score
    """
    if years is None:
        año_actual = datetime.now().year
        years = list(range(año_actual - 5, año_actual + 1))

    geojson_path = os.path.normpath(geojson_path)
    if not os.path.exists(geojson_path):
        raise FileNotFoundError(f"Archivo no encontrado: {geojson_path}")

    # Soporte para archivos comprimidos (SHP en ZIP o KMZ)
    read_path = geojson_path
    if geojson_path.lower().endswith('.zip') or geojson_path.lower().endswith('.kmz'):
        read_path = f"zip://{geojson_path}"

    try:
        gdf = gpd.read_file(read_path)
    except Exception as e:
        if geojson_path.lower().endswith(('.kml', '.kmz')):
            print(f"  ⚠ Driver de KML no encontrado. Usando parser manual de AgroIA...")
            from src.utils.kml_fallback import parse_kml_manual
            manual_data = parse_kml_manual(geojson_path)
            if manual_data:
                # Escribir a un temporal como GeoJSON para que GeoPandas lo lea fácil
                tmp_json = f"{geojson_path}_tmp.json"
                with open(tmp_json, 'w') as f:
                    json.dump(manual_data, f)
                gdf = gpd.read_file(tmp_json)
                os.remove(tmp_json)
            else:
                raise ValueError(f"No se pudo parsear el archivo KML/KMZ de forma manual.")
        else:
            raise e
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")

    total = len(gdf)
    if limit:
        gdf = gdf.head(limit)
        total_proc = limit
    else:
        total_proc = total

    print(f"\n🚀 [AgroIA BATCH] {geojson_path}")
    print(f"   Polígonos en archivo: {total} | A procesar: {total_proc}")
    print(f"   Cultivo default: {cultivo_default} | Push RAG: {push_to_rag}\n")

    # GEE se inicializa UNA SOLA VEZ para toda la tanda
    init_gee(project_id=settings.gee_project_id)

    resultados = []
    errores = []

    for idx, row in gdf.iterrows():
        # Derivar lote_id desde propiedades del polígono
        raw_id = row.get('id', None)
        lote_id = str(raw_id).strip() if raw_id else f"{id_prefix}_{idx + 1:03d}"

        # Cultivo: preferir propiedad del polígono
        cultivo_raw = row.get('cultivo', None)
        cultivo = str(cultivo_raw).strip().lower() if cultivo_raw else cultivo_default
        if cultivo not in CONFIG:
            print(f"  ⚠ Cultivo '{cultivo}' no reconocido en lote {lote_id}. Usando '{cultivo_default}'.")
            cultivo = cultivo_default

        print(f"[{idx + 1}/{total_proc}] {lote_id} ({cultivo})")

        # Extraer geometría como GeoDataFrame de un solo polígono
        single_gdf = gpd.GeoDataFrame([row], crs="EPSG:4326").reset_index(drop=True)
        geom = single_gdf.geometry.iloc[0]

        # Convertir MultiPolygon al mayor componente
        if geom.geom_type == "MultiPolygon":
            geom = max(geom.geoms, key=lambda p: p.area)
            single_gdf.at[0, "geometry"] = geom

        if geom.geom_type != "Polygon" or not geom.is_valid:
            print(f"  ⚠ Geometría inválida — saltando.")
            resultados.append({"lote_id": lote_id, "status": "GEOMETRIA_INVALIDA", "score": None})
            errores.append(lote_id)
            continue

        # EPSG UTM dinámico
        zona_utm = int((geom.centroid.x + 180) / 6) + 1
        hemisferio = 32700 if geom.centroid.y < 0 else 32600
        epsg_utm = hemisferio + zona_utm

        try:
            res = _analyze_one_polygon(single_gdf, lote_id, cultivo, years, epsg_utm, push_to_rag)
            if res is not None:
                resultados.append({
                    "lote_id": lote_id, "status": "OK",
                    "score": res["score"]["total"], "hectareas": res["hectareas"]
                })
            else:
                resultados.append({"lote_id": lote_id, "status": "SIN_DATOS_SATELITALES", "score": None})
                errores.append(lote_id)
        except Exception as e:
            print(f"  ❌ Error en {lote_id}: {e}")
            resultados.append({"lote_id": lote_id, "status": f"ERROR: {e}", "score": None})
            errores.append(lote_id)

    # Resumen final
    ok = sum(1 for r in resultados if r["status"] == "OK")
    print(f"\n{'='*55}")
    print(f"  BATCH COMPLETADO")
    print(f"  Procesados : {total_proc}")
    print(f"  Exitosos   : {ok}")
    print(f"  Con error  : {len(errores)}")
    if errores:
        print(f"  Fallidos   : {', '.join(errores[:10])}{'...' if len(errores) > 10 else ''}")
    print(f"{'='*55}\n")

    return resultados
