"""
=============================================================================
POLIGONIZACIÓN AUTOMÁTICA - GOOGLE COLAB
Pipeline Productivo v1.0 - COLAB T4

Este script genera el notebook listo para subir a Google Colab.

MODO DE USO:
    python poligonizador_colab.py       # Genera notebook
    Subir notebook a Google Colab
    Ejecutar celdas secuencialmente
    Descargar resultados (GeoJSON + CSV)

=============================================================================
"""

import nbformat as nbf


def crear_celda_codigo(codigo):
    """Crea una celda de código evitando problemas de sintaxis."""
    return nbf.v4.new_code_cell(codigo)


def crear_celda_markdown(texto):
    """Crea una celda markdown."""
    return nbf.v4.new_markdown_cell(texto)


def generar_notebook():
    """Genera el notebook de Colab completo."""
    
    nb = nbf.v4.new_notebook()
    nb.metadata = {
        "kernelspec": {
            "display_name": "Python 3",
            "language": "python",
            "name": "python3"
        }
    }
    
    cells = []
    
    # ============================================================
    # CELDA 1: TÍTULO
    # ============================================================
    cells.append(crear_celda_markdown(
        '# Poligonizacion Automatica - GOOGLE COLAB (T4 GPU)\n'
        '## Pipeline Productivo v1.0\n'
        '\n'
        'Este notebook ejecuta la poligonizacion automatica de lotes agricolas usando:\n'
        '- **Google Earth Engine** -> Descarga de imagenes Sentinel-2\n'
        '- **SAM (Segment Anything Model)** -> Delineacion automatica de parcelas\n'
        '- **GPU T4 gratuita** -> Procesamiento rapido (~9 seg/lote)\n'
        '\n'
        '---\n'
        '\n'
        '## Instrucciones\n'
        '1. **Subir archivo CSV** -> Ejecutar celda de carga\n'
        '2. **Ejecutar todas las celdas** -> Runtime > Run all\n'
        '3. **Descargar resultados** -> GeoJSON + CSV + Mapa HTML\n'
    ))
    
    # ============================================================
    # CELDA 2: INSTALACIÓN
    # ============================================================
    cells.append(crear_celda_codigo(
        '# INSTALAR DEPENDENCIAS\n'
        '# Ejecutar una sola vez al inicio de la sesion\n'
        '\n'
        'print("Instalando dependencias...")\n'
        '\n'
        '!pip install -q segment-anything\n'
        '!pip install -q earthengine-api\n'
        '!pip install -q geemap\n'
        '!pip install -q geopandas\n'
        '!pip install -q shapely\n'
        '!pip install -q rasterio\n'
        '!pip install -q folium\n'
        '!pip install -q tqdm\n'
        '!pip install -q scipy\n'
        '!pip install -q opencv-python-headless\n'
        '!pip install -q fpdf2\n'
        '\n'
        'print("Si aparece Restart runtime, hacer: Runtime > Restart runtime")\n'
        'print("Dependencias instaladas")'
    ))
    
    # ============================================================
    # CELDA 3: CONFIGURACIÓN
    # ============================================================
    cells.append(crear_celda_codigo(
        '# CONFIGURACION\n'
        '\n'
        'CONFIG = {\n'
        "    'GEE_PROJECT': 'applied-oxygen-459415-e2',\n"
        "    'BUFFER_M': 2500,\n"
        "    'MAX_NUBES': 20,\n"
        "    'DIAS_VENTANA': 60,\n"
        "    'SAM_CHECKPOINT': 'sam_vit_b_01ec64.pth',\n"
        "    'SAM_MODEL': 'vit_b',\n"
        "    'MIN_AREA_HA': 5,\n"
        "    'MAX_AREA_HA': 800,\n"
        "    'TOLERANCIA_FUGA': 1.5,\n"
        "    'MARGEN_PX_MIN': 20,\n"
        "    'MARGEN_PX_MAX': 80,\n"
        "    'FACTOR_SUAVIZADO': 0.005,\n"
        "    'GUARDAR_CADA': 10,\n"
        "    'FECHA_FALLBACK_INICIO': '2025-12-01',\n"
        "    'FECHA_FALLBACK_FIN': '2026-03-31',\n"
        '}\n'
        '\n'
        "OUTPUT_FILE = 'poligonos_produccion.geojson'\n"
        "LOG_FILE = 'log_produccion.csv'\n"
        '\n'
        "print('Configuracion lista')"
    ))
    
    # ============================================================
    # CELDA 4: IMPORTAR
    # ============================================================
    cells.append(crear_celda_codigo(
        '# IMPORTAR LIBRERIAS\n'
        '\n'
        'import os, sys, json, time, math, warnings, traceback\n'
        'from datetime import datetime\n'
        'from pathlib import Path\n'
        '\n'
        'import numpy as np\n'
        'import pandas as pd\n'
        'import matplotlib\n'
        'import matplotlib.cm as cm\n'
        'import cv2\n'
        '\n'
        'from tqdm.notebook import tqdm\n'
        '\n'
        'warnings.filterwarnings("ignore")\n'
        'matplotlib.use("Agg")\n'
        'cmap = matplotlib.colormaps["RdYlGn"]\n'
        '\n'
        'from shapely.geometry import Polygon, mapping'
        '\n'
        "print('Librerias importadas')"
    ))
    
    # ============================================================
    # CELDA 5: CARGA DE DATOS
    # ============================================================
    cells.append(crear_celda_codigo(
        '# CARGA DE DATOS\n'
        '# Subir archivo CSV\n'
        '\n'
        'from google.colab import files\n'
        '\n'
        "print('Subir archivo CSV con columnas: lat, lon, fecha, dano_ha, localidad, cultivo')\n"
        '\n'
        'uploaded = files.upload()\n'
        "filename = list(uploaded.keys())[0]\n"
        "print(f'Archivo: {filename}')\n"
        '\n'
        'df_raw = pd.read_csv(filename)\n'
        "print(f'{len(df_raw)} registros')\n"
        '\n'
        '# Detectar columnas\n'
        'def encontrar_columna(patrones, df):\n'
        '    for p in patrones:\n'
        '        for col in df.columns:\n'
        "            if col is not None and str(col).strip():\n"
        "                if p in str(col).strip().lower():\n"
        '                    return col\n'
        '    return None\n'
        '\n'
        "columnas_csv = {\n"
        "    'id': ['id', 'taype', 'numero', 'nro'],\n"
        "    'lat': ['lat_dec', 'latitude', 'lat', 'cg_latitud', 'y'],\n"
        "    'lon': ['lon_dec', 'longitude', 'lon', 'cg_longitud', 'x'],\n"
        "    'fecha': ['fecha_de_s', 'fecha', 'date'],\n"
        "    'dano_ha': ['has__daña', 'dano_ha', 'dano_estimado', 'area_ha', 'sup_afectada_ha'],\n"
        "    'cultivo': ['cultivo', 'crop'],\n"
        "    'localidad': ['localidad', 'location', 'loc'],\n"
        "    'provincia': ['provincia', 'prov'],\n"
        '}\n'
        '\n'
        'rename_dict = {}\n'
        'for target, patrones in columnas_csv.items():\n'
        '    col = encontrar_columna(patrones, df_raw)\n'
        '    if col:\n'
        "        rename_dict[col] = target\n"
        "        print(f'  Detectado: {col} -> {target}')\n"
        '\n'
        'df = df_raw.rename(columns=rename_dict)\n'
        '\n'
        '# Limpiar decimales (coma vs punto)\n'
        'def parse_decimal(val):\n'
        '    if pd.isna(val): return np.nan\n'
        '    if isinstance(val, (int, float)): return float(val)\n'
        "    s = str(val).strip().replace(',', '.')\n"
        '    if s.count(".") > 1:\n'
        '        parts = s.split(".")\n'
        "        s = ''.join(parts[:-1]) + '.' + parts[-1]\n"
        '    try: return float(s)\n'
        '    except: return np.nan\n'
        '\n'
        'for col in ["lat", "lon", "dano_ha"]:\n'
        '    if col in df.columns:\n'
        '        df[col] = df[col].apply(parse_decimal)\n'
        '\n'
        '# Fechas\n'
        "if 'fecha' in df.columns:\n"
        '    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce", dayfirst=True)\n'
        'else:\n'
        '    df["fecha"] = pd.NaT\n'
        '\n'
        '# Filtrar coords validas (Argentina)\n'
        'df = df.dropna(subset=["lat", "lon"])\n'
        'df = df[df["lat"].between(-42, -22)]\n'
        'df = df[df["lon"].between(-70, -53)]\n'
        '\n'
        "if 'id' not in df.columns or df['id'].isna().all():\n"
        '    df["id"] = range(1, len(df) + 1)\n'
        'df["id"] = df["id"].astype(str)\n'
        '\n'
        '# Reduccion espacial\n'
        'df["lat_r"] = df["lat"].round(4)\n'
        'df["lon_r"] = df["lon"].round(4)\n'
        "df_geo = df.sort_values('id').drop_duplicates(subset=['lat_r', 'lon_r']).reset_index(drop=True)\n"
        '\n'
        '# Generar fechas\n'
        "dias = CONFIG['DIAS_VENTANA']\n"
        "fb_ini = CONFIG['FECHA_FALLBACK_INICIO']\n"
        "fb_fin = CONFIG['FECHA_FALLBACK_FIN']\n"
        '\n'
        'def set_fechas(row, is_inicio=True):\n'
        '    if pd.isna(row.get("fecha")):\n'
        "        return pd.to_datetime(fb_ini if is_inicio else fb_fin)\n"
        '    return row["fecha"] - pd.Timedelta(days=dias if is_inicio else 1)\n'
        '\n'
        "df_geo['fecha_inicio'] = df_geo.apply(lambda r: set_fechas(r, True), axis=1)\n"
        "df_geo['fecha_fin'] = df_geo.apply(lambda r: set_fechas(r, False), axis=1)\n"
        '\n'
        "print(f'{len(df_geo)} lotes unicos listos')"
    ))
    
    # ============================================================
    # CELDA 6: GEE + SAM
    # ============================================================
    cells.append(crear_celda_codigo(
        '# INICIALIZAR GEE + SAM\n'
        '\n'
        'import ee\n'
        '\n'
        "print('Conectando GEE...')\n"
        'try:\n'
        "    ee.Initialize(project=CONFIG['GEE_PROJECT'])\n"
        "    print('  GEE conectado')\n"
        'except:\n'
        '    ee.Authenticate()\n'
        "    ee.Initialize(project=CONFIG['GEE_PROJECT'])\n"
        "    print('  GEE conectado')\n"
        '\n'
        '# Descargar SAM\n'
        "print('Cargando SAM...')\n"
        "if not os.path.exists(CONFIG['SAM_CHECKPOINT']):\n"
        "    !wget -q -nc https://dl.fbaipublicfiles.com/segment_anything/{CONFIG['SAM_CHECKPOINT']}\n"
        '\n'
        'from segment_anything import sam_model_registry, SamPredictor\n'
        '\n'
        "sam = sam_model_registry[CONFIG['SAM_MODEL']](checkpoint=CONFIG['SAM_CHECKPOINT'])\n"
        'sam.to("cuda")\n'
        'predictor = SamPredictor(sam)\n'
        '\n'
        "print('SAM listo en GPU T4')"
    ))
    
    # ============================================================
    # CELDA 7: FUNCIONES
    # ============================================================
    cells.append(crear_celda_codigo(
        '# FUNCIONES DEL PIPELINE\n'
        '\n'
        'def ndvi_a_rgb(ndvi):\n'
        '    ndvi_clip = np.clip(ndvi, -0.2, 0.8)\n'
        '    ndvi_norm = ((ndvi_clip + 0.2) * 255).astype(np.uint8)\n'
        '    return (cmap(ndvi_norm / 255.0)[:, :, :3] * 255).astype(np.uint8)\n'
        '\n'
        'def pixel_a_geo(col, row, lon_min, lon_max, lat_min, lat_max, w, h):\n'
        '    lon = lon_min + (col / w) * (lon_max - lon_min)\n'
        '    lat = lat_max - (row / h) * (lat_max - lat_min)\n'
        '    return (lon, lat)\n'
        '\n'
        'def calcular_area_ha(poligono, lat_centro):\n'
        '    factor = math.cos(math.radians(lat_centro))\n'
        '    return poligono.area * (111320 ** 2) * factor / 10000\n'
        '\n'
        'def bounding_box_dinamico(area_ref, shape_hw):\n'
        '    lado_m = math.sqrt(max(area_ref, 1) * 10000)\n'
        '    margen = int((lado_m / 2) / 10)\n'
        '    margen = max(CONFIG["MARGEN_PX_MIN"], min(margen, CONFIG["MARGEN_PX_MAX"]))\n'
        '    h, w = shape_hw\n'
        '    cx, cy = w // 2, h // 2\n'
        '    return np.array([max(0, cx-margen), max(0, cy-margen), min(w-1, cx+margen), min(h-1, cy+margen)])\n'
        '\n'
        'def descargar_ndvi(lat, lon, fecha_inicio, fecha_fin):\n'
        '    centro = ee.Geometry.Point([lon, lat])\n'
        '    bbox = centro.buffer(CONFIG["BUFFER_M"]).bounds()\n'
        '    coleccion = (ee.ImageCollection("COPERNICUS/S2_HARMONIZED")\n'
        '                 .filterBounds(bbox)\n'
        '                 .filterDate(fecha_inicio.strftime("%Y-%m-%d"), fecha_fin.strftime("%Y-%m-%d"))\n'
        '                 .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", CONFIG["MAX_NUBES"]))\n'
        '                 .sort("CLOUDY_PIXEL_PERCENTAGE"))\n'
        '    if coleccion.size().getInfo() == 0:\n'
        '        return None, None\n'
        '    img = coleccion.first()\n'
        '    datos = img.select(["B4", "B8"]).sampleRectangle(region=bbox, defaultValue=0)\n'
        '    rojo = np.array(datos.get("B4").getInfo(), dtype=np.float32)\n'
        '    nir = np.array(datos.get("B8").getInfo(), dtype=np.float32)\n'
        '    ndvi = (nir - rojo) / (nir + rojo + 1e-6)\n'
        '    return ndvi, bbox\n'
        '\n'
        'def segmentar_lote(ndvi, bbox, lat, lon, area_ref):\n'
        '    h, w = ndvi.shape\n'
        '    predictor.set_image(ndvi_a_rgb(ndvi))\n'
        '    box = bounding_box_dinamico(area_ref, (h, w))\n'
        '    cx, cy = w // 2, h // 2\n'
        '    masks, scores, _ = predictor.predict(\n'
        '        point_coords=np.array([[cx, cy]]),\n'
        '        point_labels=np.array([1]),\n'
        '        box=box[None, :],\n'
        '        multimask_output=True\n'
        '    )\n'
        '    mejor_idx = np.argmax(scores)\n'
        '    mask = masks[mejor_idx].copy()\n'
        '    score = float(scores[mejor_idx])\n'
        '    area_sam = (mask.sum() * 100) / 10000\n'
        '\n'
        '    # Control de fuga\n'
        '    if area_sam > area_ref * CONFIG["TOLERANCIA_FUGA"]:\n'
        '        x_min, y_min, x_max, y_max = box\n'
        '        pts_neg = np.array([[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]])\n'
        '        todas_coords = np.vstack([[cx, cy], pts_neg])\n'
        '        todas_labels = np.array([1, 0, 0, 0, 0])\n'
        '        masks2, scores2, _ = predictor.predict(\n'
        '            point_coords=todas_coords, point_labels=todas_labels,\n'
        '            box=box[None, :], multimask_output=False\n'
        '        )\n'
        '        mask = masks2[0].copy()\n'
        '        score = float(scores2[0])\n'
        '        area_sam = (mask.sum() * 100) / 10000\n'
        '        if area_sam > area_ref * CONFIG["TOLERANCIA_FUGA"]:\n'
        '            return {"status": "FUGA_NO_RESUELTA", "area_ha": round(area_sam, 1),\n'
        '                    "score": round(score, 3), "mask": mask, "poligono": None, "vertices": 0}\n'
        '\n'
        '    # Vectorizacion\n'
        '    contornos, _ = cv2.findContours((mask * 255).astype(np.uint8),\n'
        '                                     cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)\n'
        '    if not contornos:\n'
        '        return {"status": "SIN_CONTORNO", "area_ha": round(area_sam, 1),\n'
        '                "score": round(score, 3), "mask": mask, "poligono": None, "vertices": 0}\n'
        '\n'
        '    contorno = max(contornos, key=cv2.contourArea)\n'
        '    epsilon = CONFIG["FACTOR_SUAVIZADO"] * cv2.arcLength(contorno, True)\n'
        '    contorno_simple = cv2.approxPolyDP(contorno, epsilon, True)\n'
        '    puntos = contorno_simple.squeeze()\n'
        '    if puntos.ndim == 1: puntos = puntos.reshape(1, -1)\n'
        '    if len(puntos) < 3:\n'
        '        return {"status": "CONTORNO_INVALIDO", "area_ha": round(area_sam, 1),\n'
        '                "score": round(score, 3), "mask": mask, "poligono": None, "vertices": 0}\n'
        '\n'
        '    coords_bbox = bbox.bounds().getInfo()["coordinates"][0]\n'
        '    lons = [c[0] for c in coords_bbox]\n'
        '    lats = [c[1] for c in coords_bbox]\n'
        '    lon_min, lon_max = min(lons), max(lons)\n'
        '    lat_min, lat_max = min(lats), max(lats)\n'
        '\n'
        '    vertices_geo = [pixel_a_geo(p[0], p[1], lon_min, lon_max, lat_min, lat_max, w, h) for p in puntos]\n'
        '    poligono = Polygon(vertices_geo).buffer(0)\n'
        '\n'
        '    return {"status": "OK", "area_ha": round(area_sam, 1), "score": round(score, 3),\n'
        '            "mask": mask, "poligono": poligono, "vertices": len(puntos)}\n'
        '\n'
        "print('Funciones listas')"
    ))
    
    # ============================================================
    # CELDA 8: PIPELINE
    # ============================================================
    cells.append(crear_celda_codigo(
        '# EJECUTAR PIPELINE\n'
        '\n'
        '# Cargar checkpoint\n'
        'features = []\n'
        'log_rows = []\n'
        'ids_procesados = set()\n'
        '\n'
        'if os.path.exists(OUTPUT_FILE):\n'
        '    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:\n'
        '        gj = json.load(f)\n'
        '    features = gj.get("features", [])\n'
        '    ids_procesados = {ft["properties"].get("id") for ft in features if ft["properties"].get("id")}\n'
        "    print(f'GeoJSON previo: {len(features)} poligonos')\n"
        '\n'
        'if os.path.exists(LOG_FILE):\n'
        '    df_log = pd.read_csv(LOG_FILE)\n'
        '    log_rows = df_log.to_dict("records")\n'
        '    ids_procesados |= {r["id"] for r in log_rows if r.get("id")}\n'
        "    print(f'Log previo: {len(log_rows)} entradas')\n"
        '\n'
        "pendientes = df_geo[~df_geo['id'].isin(ids_procesados)].reset_index(drop=True)\n"
        "print(f'Procesados: {len(ids_procesados)} | Pendientes: {len(pendientes)}')\n"
        '\n'
        'if len(pendientes) == 0:\n'
        '    print("No hay lotes pendientes")\n'
        'else:\n'
        "    print(f'Procesando {len(pendientes)} lotes...')\n"
        '    \n'
        '    stats = {"total": len(pendientes), "ok": 0, "fugas": 0, "sin_imagen": 0, "otros": 0}\n'
        '    t_inicio = time.time()\n'
        '    \n'
        '    for idx, row in tqdm(pendientes.iterrows(), total=len(pendientes), desc="Poligonizando", ncols=80):\n'
        '        t0 = time.time()\n'
        '        area_ref = row.get("dano_ha", row.get("superficie", 50))\n'
        '        if pd.isna(area_ref) or area_ref <= 0: area_ref = 50\n'
        '        lat, lon = row["lat"], row["lon"]\n'
        '        fecha_ini = row["fecha_inicio"].strftime("%Y-%m-%d")\n'
        '        fecha_fin = row["fecha_fin"].strftime("%Y-%m-%d")\n'
        '        \n'
        '        ndvi, bbox = descargar_ndvi(lat, lon, fecha_ini, fecha_fin)\n'
        '        \n'
        '        if ndvi is None:\n'
        '            estado, area_ha, score, poligono, vertices = "SIN_IMAGEN", 0, 0, None, 0\n'
        '        else:\n'
        '            resultado = segmentar_lote(ndvi, bbox, lat, lon, area_ref)\n'
        '            estado, area_ha, score, poligono, vertices = (\n'
        '                resultado["status"], resultado["area_ha"], resultado["score"],\n'
        '                resultado["poligono"], resultado["vertices"]\n'
        '            )\n'
        '            if estado == "OK":\n'
        '                area_ha = calcular_area_ha(poligono, lat)\n'
        '                if area_ha < CONFIG["MIN_AREA_HA"]: estado = "AREA_MUY_CHICA"; poligono = None\n'
        '                elif area_ha > CONFIG["MAX_AREA_HA"]: estado = "SOBRE_SEGMENTACION"; poligono = None\n'
        '        \n'
        '        error_pct = abs(area_ha - area_ref) / area_ref * 100 if area_ref > 0 else 0\n'
        '        segundos = round(time.time() - t0, 1)\n'
        '        \n'
        '        log_rows.append({\n'
        '            "id": row["id"], "localidad": row.get("localidad", ""),\n'
        '            "provincia": row.get("provincia", ""), "cultivo": row.get("cultivo", ""),\n'
        '            "fecha": str(row["fecha"].date()) if pd.notna(row.get("fecha")) else "",\n'
        '            "estado": estado, "area_ha": round(area_ha, 1),\n'
        '            "dano_ha": area_ref, "error_pct": round(error_pct, 1),\n'
        '            "sam_score": score, "vertices": vertices, "segundos": segundos\n'
        '        })\n'
        '        \n'
        '        if estado == "OK" and poligono is not None:\n'
        '            stats["ok"] += 1\n'
        '            features.append({\n'
        '                "type": "Feature",\n'
        '                "properties": {\n'
        '                    "id": str(row["id"]), "localidad": row.get("localidad", ""),\n'
        '                    "provincia": row.get("provincia", ""), "cultivo": row.get("cultivo", ""),\n'
        '                    "fecha": str(row["fecha"].date()) if pd.notna(row.get("fecha")) else "",\n'
        '                    "area_ha": round(area_ha, 1), "dano_ha": area_ref,\n'
        '                    "error_pct": round(error_pct, 1), "sam_score": score,\n'
        '                    "vertices": vertices, "timestamp": datetime.now().isoformat()\n'
        '                },\n'
        '                "geometry": mapping(poligono)\n'
        '            })\n'
        '        elif estado == "FUGA_NO_RESUELTA": stats["fugas"] += 1\n'
        '        elif estado == "SIN_IMAGEN": stats["sin_imagen"] += 1\n'
        '        else: stats["otros"] += 1\n'
        '        \n'
        '        if len(features) % CONFIG["GUARDAR_CADA"] == 0:\n'
        '            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:\n'
        '                json.dump({"type": "FeatureCollection", "features": features}, f, indent=2)\n'
        '            pd.DataFrame(log_rows).to_csv(LOG_FILE, index=False)\n'
        '    \n'
        '    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:\n'
        '        json.dump({"type": "FeatureCollection", "features": features}, f, indent=2)\n'
        '    pd.DataFrame(log_rows).to_csv(LOG_FILE, index=False)\n'
        '    \n'
        '    t_total = round(time.time() - t_inicio, 1)\n'
        '    \n'
        '    print("=" * 60)\n'
        '    print("RESUMEN")\n'
        '    print("=" * 60)\n'
        '    print(f"Total: {stats[\'total\']} | OK: {stats[\'ok\']} | Fugas: {stats[\'fugas\']} | Sin img: {stats[\'sin_imagen\']}")\n'
        '    print(f"Tiempo: {t_total:.0f}s ({t_total/60:.1f} min)")\n'
        '    print("=" * 60)'
    ))
    
    # ============================================================
    # CELDA 9: DESCARGAR
    # ============================================================
    cells.append(crear_celda_codigo(
        '# DESCARGAR RESULTADOS\n'
        '\n'
        'from google.colab import files\n'
        '\n'
        "print('Descargando archivos...')\n"
        'files.download(OUTPUT_FILE)\n'
        "print(f'{OUTPUT_FILE}')\n"
        '\n'
        'files.download(LOG_FILE)\n'
        "print(f'{LOG_FILE}')\n"
        '\n'
        "resultados_csv = LOG_FILE.replace('.csv', '_resultados.csv')\n"
        'df_log = pd.DataFrame(log_rows)\n'
        'df_log.to_csv(resultados_csv, index=False, encoding="utf-8")\n'
        'files.download(resultados_csv)\n'
        "print(f'{resultados_csv}')\n"
        '\n'
        "print('Descarga iniciada')"
    ))
    
    # ============================================================
    # CELDA 10: MAPA
    # ============================================================
    cells.append(crear_celda_codigo(
        '# GENERAR MAPA HTML\n'
        '\n'
        'import folium\n'
        '\n'
        "print('Generando mapa...')\n"
        '\n'
        'with open(OUTPUT_FILE, "r", encoding="utf-8") as f:\n'
        '    gj = json.load(f)\n'
        '\n'
        'features = gj.get("features", [])\n'
        '\n'
        'if features:\n'
        '    lats = [f["geometry"]["coordinates"][0][0][1] for f in features]\n'
        '    lons = [f["geometry"]["coordinates"][0][0][0] for f in features]\n'
        '    centro = [np.mean(lats), np.mean(lons)]\n'
        '    \n'
        '    mapa = folium.Map(location=centro, zoom_start=8, tiles="CartoDB positron")\n'
        '    \n'
        '    for feature in features:\n'
        '        props = feature["properties"]\n'
        '        geom = feature["geometry"]\n'
        '        error = props.get("error_pct", 0)\n'
        '        color = "green" if error < 20 else "orange" if error < 50 else "red"\n'
        '        \n'
        '        folium.GeoJson(\n'
        '            geom,\n'
        '            style_function=lambda f, c=color: {"fillColor": c, "color": c, "fillOpacity": 0.3, "weight": 2},\n'
        '            popup=f"<b>ID:</b> {props.get(\'id\',\'\')}<br><b>Area:</b> {props.get(\'area_ha\',\'\')} ha<br><b>Error:</b> {props.get(\'error_pct\',\'\')}%"\n'
        '        ).add_to(mapa)\n'
        '    \n'
        '    mapa.save("mapa_resultados.html")\n'
        '    files.download("mapa_resultados.html")\n'
        '    print(f"Mapa generado: {len(features)} poligonos")\n'
        'else:\n'
        '    print("No hay poligonos")'
    ))
    
    # ============================================================
    # CELDA 11: PDF
    # ============================================================
    cells.append(crear_celda_codigo(
        '# GENERAR REPORTE PDF\n'
        '\n'
        'from fpdf import FPDF\n'
        'from fpdf.enums import XPos, YPos\n'
        '\n'
        "print('Generando PDF...')\n"
        '\n'
        'df_log = pd.DataFrame(log_rows)\n'
        'df_ok = df_log[df_log["estado"] == "OK"]\n'
        '\n'
        'pdf = FPDF()\n'
        'pdf.add_page()\n'
        'pdf.set_auto_page_break(auto=True, margin=15)\n'
        '\n'
        'pdf.set_font("helvetica", "B", 16)\n'
        'pdf.set_text_color(41, 128, 185)\n'
        'pdf.cell(0, 10, "Reporte de Poligonizacion", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")\n'
        'pdf.ln(5)\n'
        '\n'
        'pdf.set_font("helvetica", "", 11)\n'
        'pdf.set_text_color(0, 0, 0)\n'
        '\n'
        "resumen = f'''Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        "Archivo: {filename}\n\n"
        "Total: {stats['total']}\n"
        "OK: {stats['ok']} ({stats['ok']/stats['total']*100:.1f}%)\n"
        "Sin imagen: {stats['sin_imagen']}\n"
        "Fugas: {stats['fugas']}\n'''\n"
        'pdf.multi_cell(0, 6, resumen)\n'
        '\n'
        'if not df_ok.empty:\n'
        '    pdf.ln(5)\n'
        '    pdf.set_font("helvetica", "B", 12)\n'
        '    pdf.cell(0, 8, "Calidad:", new_x=XPos.LMARGIN, new_y=YPos.NEXT)\n'
        '    pdf.set_font("helvetica", "", 11)\n'
        '    pdf.multi_cell(0, 6, f"SAM Score promedio: {df_ok[\'sam_score\'].mean():.3f}\\nError promedio: {df_ok[\'error_pct\'].mean():.1f}%")\n'
        '\n'
        "pdf.output('reporte_produccion.pdf')\n"
        'files.download("reporte_produccion.pdf")\n'
        'print("PDF generado")'
    ))
    
    nb['cells'] = cells
    return nb


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    print('Generando notebook de Google Colab...')
    
    nb = generar_notebook()
    
    output_path = 'Poligonizador_Colab.ipynb'
    
    with open(output_path, 'w', encoding='utf-8') as f:
        nbf.write(nb, f)
    
    print(f'Notebook generado: {output_path}')
    print()
    print('Instrucciones:')
    print('  1. Abrir Google Colab (https://colab.research.google.com)')
    print('  2. File > Upload notebook > seleccionar Poligonizador_Colab.ipynb')
    print('  3. Ejecutar todas las celdas (Runtime > Run all)')
    print('  4. Subir CSV cuando lo pida')
    print('  5. Descargar resultados')