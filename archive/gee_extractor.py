import sys
import ee
import numpy as np
import pandas as pd
import geopandas as gpd
from sklearn.cluster import KMeans

# Fix Unicode output on Windows cp1252 terminals
try:
    if sys.stdout and hasattr(sys.stdout, 'encoding') and sys.stdout.encoding and sys.stdout.encoding.lower().startswith('cp'):
        sys.stdout.reconfigure(errors='replace')
        sys.stderr.reconfigure(errors='replace')
except Exception:
    pass

def _safe_print(msg):
    """Print that never raises, even on broken stdout."""
    try:
        print(msg)
    except Exception:
        pass

def init_gee(project_id=None):
    """Inicializa Google Earth Engine."""
    try:
        if project_id:
            ee.Initialize(project=project_id)
        else:
            ee.Initialize()
        _safe_print("[OK] GEE inicializado correctamente.")
    except ee.EEException:
        _safe_print("[...] Autenticando GEE por primera vez...")
        ee.Authenticate()
        if project_id:
            ee.Initialize(project=project_id)
        else:
            ee.Initialize()
        _safe_print("[OK] GEE autenticado e inicializado.")

def get_gee_ndvi(geom_ee, year, month):
    """Obtiene el NDVI mediano de un mes para una geometría dada."""
    start = ee.Date.fromYMD(year, month, 1)
    end   = start.advance(1, 'month')
    collection = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                  .filterBounds(geom_ee).filterDate(start, end)
                  .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
                  .map(lambda img: img.normalizedDifference(['B8', 'B4']).rename('ndvi')))
    if collection.size().getInfo() == 0: return None
    return collection.median().reduceRegion(reducer=ee.Reducer.mean(), geometry=geom_ee, scale=10, maxPixels=1e9).get('ndvi').getInfo()

def get_gee_ndvi_ventana(geom_ee, year, month, max_delta=2):
    """Busca NDVI en una ventana de meses alrededor del mes crítico."""
    for delta in range(0, max_delta + 1):
        for signo in ([0] if delta == 0 else [1, -1]):
            mes_intento = month + (delta * signo)
            if mes_intento < 1 or mes_intento > 12:
                continue
            val = get_gee_ndvi(geom_ee, year, mes_intento)
            if val is not None:
                aviso = None if delta == 0 else (
                    f"⚠ {year}: mes critico ({month:02d}) sin imagenes limpias. "
                    f"NDVI tomado de mes {mes_intento:02d} (delta {delta} mes). "
                    f"Aplicar con criterio agronomico."
                )
                return val, mes_intento, aviso
    return None, None, (
        f"Sin imagenes validas en ventana +/-{max_delta} meses "
        f"alrededor del mes {month:02d} para {year}. Ano excluido del Score."
    )

def calcular_cv_gee(geom_ee, year, month, scale=20):
    """Calcula el Coeficiente de Variación (std/mean) del NDVI."""
    start = ee.Date.fromYMD(year, month, 1)
    end   = start.advance(1, 'month')
    img = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED').filterBounds(geom_ee).filterDate(start, end)
           .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
           .map(lambda i: i.normalizedDifference(['B8', 'B4']).rename('ndvi')).median())
    stats = img.reduceRegion(reducer=ee.Reducer.mean().combine(ee.Reducer.stdDev(), sharedInputs=True),
                             geometry=geom_ee, scale=scale, maxPixels=1e9).getInfo()
    mean, std = stats.get('ndvi_mean'), stats.get('ndvi_stdDev')
    if mean and mean > 0 and std is not None: return round(std / mean, 4), round(mean, 4), round(std, 4)
    return None, None, None

def zonificar_lote_gee(geom_ee, year, month, n_clusters=3):
    """Realiza clustering K-Means para zonificación A/B/C."""
    start = ee.Date.fromYMD(year, month, 1)
    img = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')\
            .filterBounds(geom_ee).filterDate(start, start.advance(1, 'month'))\
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))\
            .median().normalizedDifference(['B8', 'B4']).rename('ndvi')
    sample = img.sample(region=geom_ee, scale=10, numPixels=2000, geometries=True).getInfo()
    if not sample['features']: return None
    coords = [f['geometry']['coordinates'] for f in sample['features']]
    values = [[f['properties']['ndvi']] for f in sample['features']]
    kmeans = KMeans(n_clusters=n_clusters, random_state=42).fit(values)
    centers = kmeans.cluster_centers_.flatten()
    order = np.argsort(centers)
    label_map = {order[0]: 'C', order[1]: 'B', order[2]: 'A'}
    zonas_puntos = gpd.GeoDataFrame({
        'zona': [label_map[l] for l in kmeans.labels_],
        'ndvi': [v[0] for v in values]
    }, geometry=gpd.points_from_xy([c[0] for c in coords], [c[1] for c in coords]), crs="EPSG:4326")
    return zonas_puntos
