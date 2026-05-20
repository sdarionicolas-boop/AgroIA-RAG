# src/pipeline/eventualidades.py
"""
AgroIA Eventualidades — Evaluacion satelital de dano agricola
Portado de Colab v2.3 + visualizaciones de v2.0

Metodo:  delta_rel = (NDVI_pre - NDVI_post) / NDVI_pre x 100
         dano_ponderado = delta_rel x peso_fenologico x factor_conservador(0.92)
Fuente:  Sentinel-2 L2A (Copernicus) vía GEE + baseline 3 anios
"""

import sys
import os
import zipfile
import math
import numpy as np
import pandas as pd
import geopandas as gpd
import ee
import folium
from folium import plugins
from datetime import datetime, timedelta

# Fix Windows cp1252 encoding
try:
    if sys.stdout and hasattr(sys.stdout, 'encoding') and sys.stdout.encoding and sys.stdout.encoding.lower().startswith('cp'):
        sys.stdout.reconfigure(errors='replace')
        sys.stderr.reconfigure(errors='replace')
except Exception:
    pass

try:
    import simplekml
    HAS_SIMPLEKML = True
except ImportError:
    HAS_SIMPLEKML = False

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from io import BytesIO
    import base64
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

from .agro_math import TABLA_FENOLOGICA, FACTOR_CONSERVADOR, get_peso_fenologico
from .gee_extractor import init_gee


# ─────────────────────────────────────────────────────────────
# Sentinel-2 helpers
# ─────────────────────────────────────────────────────────────

def mask_s2_clouds(image):
    """Enmascara nubes y cirrus usando banda QA60."""
    qa = image.select('QA60')
    mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
    return image.updateMask(mask).divide(10000)


def add_ndvi(image):
    """Agrega banda NDVI a una imagen Sentinel-2."""
    return image.addBands(image.normalizedDifference(['B8', 'B4']).rename('NDVI'))


def get_median_ndvi(start_date, end_date, geometry, log_fn=None):
    """
    NDVI mediano con fallback SR -> HARMONIZED para fechas historicas (<2019).
    Retorna (ee.Image, count).  Si count==0, retorna imagen dummy con NDVI=0.
    """
    if log_fn is None:
        log_fn = print

    for col_name in ['COPERNICUS/S2_SR_HARMONIZED', 'COPERNICUS/S2_HARMONIZED']:
        col = (ee.ImageCollection(col_name)
               .filterBounds(geometry).filterDate(start_date, end_date)
               .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 35))
               .map(mask_s2_clouds).map(add_ndvi).select('NDVI'))
        count = col.size().getInfo()
        if count > 0:
            log_fn(f"  {start_date} -> {end_date}: {count} img [{col_name.split('/')[1]}]")
            return col.median().clip(geometry), count

    # Relajar umbral de nubes
    log_fn("  Sin imagenes limpias. Relajando umbral de nubes...")
    col = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
           .filterBounds(geometry).filterDate(start_date, end_date)
           .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 80))
           .map(mask_s2_clouds).map(add_ndvi).select('NDVI'))
    count = col.size().getInfo()
    log_fn(f"  {count} img (filtro relajado)")
    if count == 0:
        return ee.Image(0).rename('NDVI'), 0
    return col.median().clip(geometry), count


# ─────────────────────────────────────────────────────────────
# Baseline historico
# ─────────────────────────────────────────────────────────────

def get_baseline_year(fecha_obj, year, geometry, log_fn=None):
    """Calcula delta NDVI para un anio historico en la misma ventana fenologica."""
    try:
        pre_ini = (fecha_obj.replace(year=year) - timedelta(days=14)).strftime('%Y-%m-%d')
        pre_fin = (fecha_obj.replace(year=year) - timedelta(days=1)).strftime('%Y-%m-%d')
        post_ini = (fecha_obj.replace(year=year) + timedelta(days=1)).strftime('%Y-%m-%d')
        post_fin = (fecha_obj.replace(year=year) + timedelta(days=20)).strftime('%Y-%m-%d')

        pre, _ = get_median_ndvi(pre_ini, pre_fin, geometry, log_fn)
        post, _ = get_median_ndvi(post_ini, post_fin, geometry, log_fn)
        return post.subtract(pre).rename('delta_year')
    except Exception as e:
        if log_fn:
            log_fn(f"  Error anio {year}: {e}. Usando imagen neutra.")
        return ee.Image(0).rename('delta_year')


# ─────────────────────────────────────────────────────────────
# GEE reduce helpers
# ─────────────────────────────────────────────────────────────

def _calculate_area(mask, geometry, scale=10):
    """Calcula area en hectareas de una mascara binaria."""
    result = (mask.rename('m').multiply(ee.Image.pixelArea())
              .reduceRegion(ee.Reducer.sum(), geometry, scale,
                            maxPixels=1e9, bestEffort=True))
    return result.getNumber('m').getInfo() / 10_000


def _get_mean(img, band, geometry, scale=10):
    """Obtiene el valor medio de una banda sobre una geometria."""
    val = img.reduceRegion(
        ee.Reducer.mean(), geometry, scale, bestEffort=True
    ).getInfo().get(band, 0)
    return round(val if val is not None else 0, 3)


# ─────────────────────────────────────────────────────────────
# Ingesta de poligono
# ─────────────────────────────────────────────────────────────

def cargar_poligono(modo, coords=None, file_path=None):
    """
    Carga un poligono y lo retorna como ee.Geometry.Polygon.

    Parametros
    ----------
    modo      : 'coordenadas' | 'kmz' | 'shp' | 'geojson'
    coords    : lista de [lon, lat] si modo=='coordenadas'
    file_path : ruta al archivo si modo in ('kmz','shp','geojson')

    Retorna
    -------
    (ee.Geometry.Polygon, list_coords_lonlat)
    """
    if modo == 'coordenadas':
        if not coords:
            raise ValueError("Se requiere lista de coordenadas [lon, lat].")
        geom_ee = ee.Geometry.Polygon([coords])
        return geom_ee, coords

    if file_path is None:
        raise ValueError("Se requiere file_path para modo kmz/shp/geojson.")

    if modo == 'kmz':
        tmpdir = file_path + '_tmp'
        with zipfile.ZipFile(file_path, 'r') as z:
            z.extractall(tmpdir)
        kml_file = [f for f in os.listdir(tmpdir) if f.endswith('.kml')][0]
        gdf = gpd.read_file(os.path.join(tmpdir, kml_file)).to_crs('EPSG:4326')
    elif modo in ('shp', 'geojson'):
        gdf = gpd.read_file(file_path).to_crs('EPSG:4326')
    else:
        raise ValueError(f"Modo no soportado: {modo}")

    geom = gdf.geometry.iloc[0]
    if geom.geom_type == 'MultiPolygon':
        geom = max(geom.geoms, key=lambda g: g.area)

    coords_out = [[lon, lat] for lon, lat in geom.exterior.coords]
    geom_ee = ee.Geometry.Polygon([coords_out])
    return geom_ee, coords_out


def cargar_poligono_desde_gdf(gdf):
    """
    Carga poligono desde un GeoDataFrame (para uso con Streamlit file upload).
    Retorna (ee.Geometry.Polygon, list_coords_lonlat).
    """
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs('EPSG:4326')

    geom = gdf.geometry.iloc[0]
    if geom.geom_type == 'MultiPolygon':
        geom = max(geom.geoms, key=lambda g: g.area)

    coords = [[lon, lat] for lon, lat in geom.exterior.coords]
    geom_ee = ee.Geometry.Polygon([coords])
    return geom_ee, coords


# ─────────────────────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ─────────────────────────────────────────────────────────────

def run_eventualidades(geom_ee, fecha_evento, cultivo, tipo_evento,
                       caso_nombre="Analisis AgroIA", log_fn=None):
    """
    Ejecuta el pipeline completo de Eventualidades.

    Parametros
    ----------
    geom_ee      : ee.Geometry.Polygon
    fecha_evento : str 'YYYY-MM-DD'
    cultivo      : str (maiz|soja|trigo|cebada|girasol)
    tipo_evento  : str (granizo|viento|inundacion|sequia)
    caso_nombre  : str
    log_fn       : callable para mensajes de progreso

    Retorna
    -------
    dict con todas las metricas y objetos GEE para visualizacion
    """
    if log_fn is None:
        log_fn = print

    fecha_obj = datetime.strptime(fecha_evento, '%Y-%m-%d')
    mes_evento = fecha_obj.month

    # Ventanas temporales
    pre_ini = (fecha_obj - timedelta(days=14)).strftime('%Y-%m-%d')
    pre_fin = (fecha_obj - timedelta(days=1)).strftime('%Y-%m-%d')
    post_ini = (fecha_obj + timedelta(days=1)).strftime('%Y-%m-%d')
    post_fin = (fecha_obj + timedelta(days=20)).strftime('%Y-%m-%d')

    # Fenologia
    etapa_desc, peso_fenologico = get_peso_fenologico(cultivo, mes_evento)
    log_fn(f"Cultivo: {cultivo.upper()} - {etapa_desc}")
    log_fn(f"Peso fenologico: {peso_fenologico} | Factor conservador: {FACTOR_CONSERVADOR}")
    log_fn(f"Ventana PRE:  {pre_ini} -> {pre_fin}")
    log_fn(f"Ventana POST: {post_ini} -> {post_fin}")

    # ── NDVI pre y post ──────────────────────────────────────
    log_fn("Descargando Sentinel-2...")
    log_fn("PRE-evento:")
    ndvi_pre, n_pre = get_median_ndvi(pre_ini, pre_fin, geom_ee, log_fn)
    log_fn("POST-evento:")
    ndvi_post, n_post = get_median_ndvi(post_ini, post_fin, geom_ee, log_fn)

    confianza = ('ALTA' if n_pre >= 3 and n_post >= 3
                 else 'MEDIA' if n_pre >= 1 and n_post >= 1
                 else 'BAJA')
    log_fn(f"Confianza: {confianza} ({n_pre} img PRE / {n_post} img POST)")

    # ── Baseline 3 anios ─────────────────────────────────────
    anios_baseline = [fecha_obj.year - i for i in range(1, 4)]
    log_fn(f"Calculando baseline: {anios_baseline}...")
    deltas = [get_baseline_year(fecha_obj, y, geom_ee, log_fn) for y in anios_baseline]
    baseline_3y = ee.ImageCollection(deltas).mean().rename('baseline_3y')
    log_fn("Baseline calculado.")

    # ── Calculo de dano ──────────────────────────────────────
    delta_obs = ndvi_post.subtract(ndvi_pre).rename('delta_observado')
    delta_adj = delta_obs.subtract(baseline_3y).rename('delta_ajustado')

    epsilon = ee.Image(0.01)
    delta_rel = (ndvi_pre.subtract(ndvi_post)
                 .divide(ndvi_pre.max(epsilon))
                 .multiply(100).max(ee.Image(0))
                 .rename('delta_relativo'))

    dano_pond_img = (delta_rel
                     .multiply(peso_fenologico)
                     .multiply(FACTOR_CONSERVADOR)
                     .rename('dano_ponderado'))

    # Clasificacion espacial por pixel
    mask_leve = dano_pond_img.gte(20).And(dano_pond_img.lt(40))
    mask_moderado = dano_pond_img.gte(40).And(dano_pond_img.lt(70))
    mask_severo = dano_pond_img.gte(70)

    severidad = (ee.Image.cat([
        mask_leve.multiply(1),
        mask_moderado.multiply(2),
        mask_severo.multiply(3)
    ]).reduce(ee.Reducer.max()).rename('severidad'))

    # ── Estadisticas ─────────────────────────────────────────
    log_fn("Calculando estadisticas...")
    area_total = round(geom_ee.area().divide(10_000).getInfo(), 1)
    ndvi_pre_val = _get_mean(ndvi_pre, 'NDVI', geom_ee)
    ndvi_post_val = _get_mean(ndvi_post, 'NDVI', geom_ee)
    baseline_val = _get_mean(baseline_3y, 'baseline_3y', geom_ee)
    delta_adj_val = _get_mean(delta_adj, 'delta_ajustado', geom_ee)

    delta_rel_val = round(max((ndvi_pre_val - ndvi_post_val) / max(ndvi_pre_val, 0.01) * 100, 0), 1)
    dano_pond_val = round(delta_rel_val * peso_fenologico * FACTOR_CONSERVADOR, 1)

    area_leve = _calculate_area(severidad.eq(1), geom_ee)
    area_moderada = _calculate_area(severidad.eq(2), geom_ee)
    area_severa = _calculate_area(severidad.eq(3), geom_ee)
    area_afectada = area_leve + area_moderada + area_severa

    if dano_pond_val < 20:
        clasificacion = 'LEVE'
    elif dano_pond_val < 40:
        clasificacion = 'MODERADO'
    else:
        clasificacion = 'SEVERO'

    log_fn(f"Dano ponderado: {dano_pond_val}% ({clasificacion})")
    log_fn(f"Area afectada: {area_afectada:.1f} ha de {area_total} ha")

    result = {
        # Config
        'caso_nombre': caso_nombre,
        'fecha_evento': fecha_evento,
        'cultivo': cultivo,
        'tipo_evento': tipo_evento,
        'etapa_desc': etapa_desc,
        'peso_fenologico': peso_fenologico,
        'confianza': confianza,
        'n_pre': n_pre,
        'n_post': n_post,
        'pre_ini': pre_ini,
        'pre_fin': pre_fin,
        'post_ini': post_ini,
        'post_fin': post_fin,
        'anios_baseline': anios_baseline,
        # Metricas
        'area_total': area_total,
        'ndvi_pre_val': ndvi_pre_val,
        'ndvi_post_val': ndvi_post_val,
        'baseline_val': baseline_val,
        'delta_adj_val': delta_adj_val,
        'delta_rel_val': delta_rel_val,
        'dano_pond_val': dano_pond_val,
        'clasificacion': clasificacion,
        'area_leve': area_leve,
        'area_moderada': area_moderada,
        'area_severa': area_severa,
        'area_afectada': area_afectada,
        # GEE images (para mapas)
        'ndvi_pre': ndvi_pre,
        'ndvi_post': ndvi_post,
        'delta_adj': delta_adj,
        'dano_pond_img': dano_pond_img,
        'severidad': severidad,
        'baseline_3y': baseline_3y,
        # Geometria
        'geom_ee': geom_ee,
    }

    return result


# ─────────────────────────────────────────────────────────────
# MAPA FOLIUM
# ─────────────────────────────────────────────────────────────

def generar_mapa_folium(result, df_muestreo=None):
    """
    Genera un mapa Folium con capas GEE via tile URLs.

    Parametros
    ----------
    result      : dict retornado por run_eventualidades
    df_muestreo : DataFrame de puntos de muestreo (opcional)

    Retorna
    -------
    folium.Map
    """
    geom_ee = result['geom_ee']
    ndvi_pre = result['ndvi_pre']
    ndvi_post = result['ndvi_post']
    dano_pond_img = result['dano_pond_img']
    severidad = result['severidad']
    peso = result['peso_fenologico']

    # Tile URLs via ee.Image.getMapId()
    def get_tile_url(img, vis):
        return ee.Image(img).getMapId(vis)['tile_fetcher'].url_format

    url_pre = get_tile_url(ndvi_pre, {
        'min': 0, 'max': 0.8,
        'palette': ['#8B4513', '#DAA520', '#228B22', '#006400']
    })
    url_post = get_tile_url(ndvi_post, {
        'min': 0, 'max': 0.8,
        'palette': ['#8B4513', '#DAA520', '#228B22', '#006400']
    })
    url_dano = get_tile_url(dano_pond_img, {
        'min': 0, 'max': 100,
        'palette': ['#F0F0F0', '#FFD700', '#FF8C00', '#8B0000']
    })
    url_sev = get_tile_url(severidad.selfMask(), {
        'min': 1, 'max': 3,
        'palette': ['#FFD700', '#FF8C00', '#8B0000']
    })

    # Centro del mapa
    centro = geom_ee.centroid().coordinates().getInfo()[::-1]

    m = folium.Map(
        location=centro, zoom_start=14,
        control_scale=True,
        tiles='https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}',
        attr='Google Satellite Hybrid'
    )

    # Capas GEE
    layers = [
        (url_pre,  'NDVI Pre-evento',                         0.75, False),
        (url_post, 'NDVI Post-evento',                        0.75, False),
        (url_dano, f'Dano ponderado % (peso={peso})',         0.80, False),
        (url_sev,  'Severidad del dano',                      0.85, True),
    ]
    for url, name, opacity, show in layers:
        folium.TileLayer(
            tiles=url, attr='GEE - AgroIA',
            name=name, overlay=True,
            control=True, opacity=opacity, show=show
        ).add_to(m)

    # Borde del lote
    lote_coords = geom_ee.coordinates().getInfo()[0]
    folium.Polygon(
        locations=[[lat, lon] for lon, lat in lote_coords],
        color='black', weight=3, fill=False,
        tooltip='Limite del lote'
    ).add_to(m)

    # Puntos de muestreo
    if df_muestreo is not None and len(df_muestreo) > 0:
        colores = {'Leve': 'cadetblue', 'Moderado': 'orange', 'Severo': 'red'}
        iconos = {'Leve': 'info-sign', 'Moderado': 'warning-sign', 'Severo': 'exclamation-sign'}

        for _, row in df_muestreo.iterrows():
            cat = row['Categoria']
            folium.Marker(
                location=[row['Latitud'], row['Longitud']],
                popup=folium.Popup(
                    f"<b>Categoria: {cat}</b><br>"
                    f"Lat: {row['Latitud']}<br>Lon: {row['Longitud']}<br>"
                    f"<a href='{row['Google_Maps']}' target='_blank'>Ver en Google Maps</a>",
                    max_width=220),
                tooltip=f'Punto {cat}',
                icon=folium.Icon(color=colores.get(cat, 'gray'),
                                 icon=iconos.get(cat, 'info-sign'))
            ).add_to(m)

    # Herramientas de campo
    plugins.LocateControl(auto_start=False).add_to(m)
    plugins.MeasureControl(primary_length_unit='meters').add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    return m


# ─────────────────────────────────────────────────────────────
# PUNTOS DE MUESTREO
# ─────────────────────────────────────────────────────────────

def generar_puntos_muestreo(result, n_puntos=5):
    """
    Genera puntos de muestreo estratificados por severidad.

    Retorna
    -------
    pd.DataFrame con columnas: Categoria, Latitud, Longitud, Google_Maps
    """
    severidad = result['severidad']
    geom_ee = result['geom_ee']
    puntos = []

    for cat_val, nombre in [(1, 'Leve'), (2, 'Moderado'), (3, 'Severo')]:
        mask = severidad.eq(cat_val)
        try:
            muestras = mask.stratifiedSample(
                numPoints=n_puntos, region=geom_ee,
                scale=10, geometries=True
            ).getInfo()
            for feat in muestras.get('features', []):
                lon, lat = feat['geometry']['coordinates']
                puntos.append({
                    'Categoria': nombre,
                    'Latitud': round(lat, 6),
                    'Longitud': round(lon, 6),
                    'Google_Maps': f'https://www.google.com/maps?q={lat:.6f},{lon:.6f}'
                })
        except Exception:
            # Puede fallar si no hay pixeles en esa categoria
            pass

    return pd.DataFrame(puntos)


# ─────────────────────────────────────────────────────────────
# EXPORTACIONES: KML, CSV
# ─────────────────────────────────────────────────────────────

def exportar_kml(df_muestreo, result, output_path):
    """Exporta puntos de muestreo a KML para GPS / Google Earth."""
    if not HAS_SIMPLEKML:
        raise ImportError("simplekml no instalado. pip install simplekml")

    kml = simplekml.Kml(name=f"AgroIA - {result['caso_nombre']}")
    estilos_kml = {'Severo': 'ff0000ff', 'Moderado': 'ff0064ff', 'Leve': 'ff00ffff'}

    for _, row in df_muestreo.iterrows():
        cat = row['Categoria']
        pnt = kml.newpoint(name=cat, coords=[(row['Longitud'], row['Latitud'])])
        pnt.style.iconstyle.color = estilos_kml.get(cat, 'ffffffff')
        pnt.style.iconstyle.scale = 1.2
        pnt.description = (
            f"Punto de validacion AgroIA\n"
            f"Categoria: {cat}\n"
            f"Caso: {result['caso_nombre']}\n"
            f"Fecha: {result['fecha_evento']}"
        )

    kml.save(output_path)
    return output_path


def exportar_csv_muestreo(df_muestreo, output_path):
    """Exporta puntos de muestreo a CSV."""
    df_muestreo.to_csv(output_path, index=False)
    return output_path


# ─────────────────────────────────────────────────────────────
# VISUALIZACIONES MATPLOTLIB (portado de v2.0)
# ─────────────────────────────────────────────────────────────

def plot_comparativa_ndvi(result, save_path=None):
    """
    Genera panel de 3 imagenes: NDVI PRE, NDVI POST, SEVERIDAD.
    Usa ee.Image.getThumbURL() en vez de geemap para evitar dependencias.
    Retorna bytes PNG si save_path is None, sino guarda y retorna path.
    """
    if not HAS_MATPLOTLIB:
        return None

    import urllib.request
    from PIL import Image
    from io import BytesIO

    geom_ee = result['geom_ee']
    ndvi_pre = result['ndvi_pre']
    ndvi_post = result['ndvi_post']
    severidad = result['severidad']

    pal_ndvi = {'min': 0, 'max': 0.8,
                'palette': ['#8B4513', '#DAA520', '#228B22', '#006400']}
    pal_sev = {'min': 0, 'max': 3,
               'palette': ['#FFFFFF', '#FFD700', '#FF8C00', '#8B0000']}

    region = geom_ee.bounds().getInfo()['coordinates']
    dims = '512x512'

    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    fig.suptitle(
        f"AgroIA Eventualidades - {result['caso_nombre']} ({result['fecha_evento']})",
        fontsize=14, fontweight='bold'
    )

    for ax, img, pal, title in zip(
        axes,
        [ndvi_pre, ndvi_post, severidad],
        [pal_ndvi, pal_ndvi, pal_sev],
        ['NDVI PRE-EVENTO', 'NDVI POST-EVENTO', 'SEVERIDAD DEL DANO']
    ):
        try:
            url = img.visualize(**pal).getThumbURL({
                'region': geom_ee.getInfo(),
                'dimensions': dims,
                'format': 'png'
            })
            response = urllib.request.urlopen(url, timeout=30)
            img_data = Image.open(BytesIO(response.read()))
            ax.imshow(np.array(img_data))
        except Exception:
            ax.text(0.5, 0.5, 'No disponible', ha='center', va='center',
                    transform=ax.transAxes, fontsize=14, color='gray')
        ax.set_title(title, fontweight='bold')
        ax.axis('off')

    # Leyenda en panel de severidad
    axes[2].legend(handles=[
        Patch(facecolor='#FFD700', label='Leve (20-40%)'),
        Patch(facecolor='#FF8C00', label='Moderado (40-70%)'),
        Patch(facecolor='#8B0000', label='Severo (>70%)'),
    ], loc='lower left', fontsize=9)

    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=200, bbox_inches='tight')
        plt.close(fig)
        return save_path
    else:
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=200, bbox_inches='tight')
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()


def plot_donut_dano(result, save_path=None):
    """
    Genera grafico donut con distribucion de dano.
    Retorna bytes PNG si save_path is None, sino guarda y retorna path.
    """
    if not HAS_MATPLOTLIB:
        return None

    area_total = result['area_total']
    area_afectada = result['area_afectada']
    area_leve = result['area_leve']
    area_moderada = result['area_moderada']
    area_severa = result['area_severa']
    dano_pond_val = result['dano_pond_val']
    clasificacion = result['clasificacion']
    caso_nombre = result['caso_nombre']

    fig, ax = plt.subplots(figsize=(8, 7))
    sizes = [area_total - area_afectada, area_leve, area_moderada, area_severa]
    labels = ['Sin dano', 'Leve', 'Moderado', 'Severo']
    colors = ['#4CAF50', '#FFD700', '#FF8C00', '#8B0000']

    # Filtrar categorias con area 0 para evitar problemas de visualizacion
    filtered = [(s, l, c) for s, l, c in zip(sizes, labels, colors) if s > 0]
    if not filtered:
        plt.close(fig)
        return None

    f_sizes, f_labels, f_colors = zip(*filtered)

    ax.pie(f_sizes, labels=f_labels, colors=f_colors,
           autopct=lambda p: f'{p:.1f}%\n({p * area_total / 100:.0f} ha)',
           startangle=90,
           explode=[0.05] * len(f_sizes),
           textprops={'fontsize': 10, 'fontweight': 'bold'},
           wedgeprops={'edgecolor': 'white', 'linewidth': 2})

    circle = plt.Circle((0, 0), 0.65, fc='white')
    ax.add_artist(circle)
    ax.text(0, 0, f'{area_afectada / max(area_total, 0.01) * 100:.1f}%\nafectado',
            ha='center', va='center', fontsize=14, fontweight='bold')
    ax.set_title(f'{caso_nombre}\nDano ponderado: {dano_pond_val}% ({clasificacion})',
                 fontsize=12, fontweight='bold')

    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=200, bbox_inches='tight')
        plt.close(fig)
        return save_path
    else:
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=200, bbox_inches='tight')
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()


# ─────────────────────────────────────────────────────────────
# REPORTE HTML
# ─────────────────────────────────────────────────────────────

def generar_reporte_html(result, output_path=None):
    """
    Genera reporte HTML ejecutivo con trazabilidad completa.

    Retorna
    -------
    str HTML si output_path is None, sino guarda y retorna path.
    """
    r = result
    time_str = datetime.now().strftime('%d/%m/%Y %H:%M')
    area_total = r['area_total']

    html = f"""<!DOCTYPE html><html lang='es'><head><meta charset='UTF-8'>
<title>AgroIA Eventualidades - {r['caso_nombre']}</title>
<style>
body{{font-family:'Segoe UI',Arial,sans-serif;margin:0;padding:20px;background:#f0f4f8;}}
.wrap{{max-width:960px;margin:auto;background:#fff;border-radius:16px;padding:32px;box-shadow:0 8px 30px rgba(0,0,0,.1);}}
h1{{color:#1a1a2e;border-left:6px solid #8B0000;padding-left:16px;font-size:1.4em;}}
h2{{color:#333;margin-top:28px;}}
.badge{{display:inline-block;padding:6px 18px;border-radius:20px;font-weight:bold;font-size:1.1em;}}
.leve{{background:#FFD700;color:#333;}}.moderado{{background:#FF8C00;color:#fff;}}.severo{{background:#8B0000;color:#fff;}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin:20px 0;}}
.card{{background:#f7f9fc;border-radius:12px;padding:16px;text-align:center;}}
.num{{font-size:1.9em;font-weight:bold;color:#8B0000;}}
.formula{{background:#f0f4f8;border-left:4px solid #8B0000;padding:14px;border-radius:8px;font-family:monospace;margin:16px 0;}}
table{{width:100%;border-collapse:collapse;margin:12px 0;}}
th{{background:#8B0000;color:#fff;padding:10px;text-align:left;}}
td{{padding:9px;border-bottom:1px solid #eee;}}
tr:nth-child(even){{background:#fafafa;}}
.conf-alta{{color:#2e7d32;font-weight:bold;}}.conf-media{{color:#f57c00;font-weight:bold;}}.conf-baja{{color:#c62828;font-weight:bold;}}
.footer{{text-align:center;margin-top:28px;padding-top:18px;border-top:1px solid #ddd;color:#888;font-size:.85em;}}
</style></head><body><div class='wrap'>
<h1>Informe de Eventualidad Agricola - AgroIA</h1>
<p><b>Caso:</b> {r['caso_nombre']} &nbsp;|&nbsp; <b>Evento:</b> {r['fecha_evento']} ({r['tipo_evento']}) &nbsp;|&nbsp; <b>Cultivo:</b> {r['cultivo'].upper()}</p>
<p><b>Etapa fenologica:</b> {r['etapa_desc']}</p>
<p><b>Confianza del analisis:</b> <span class='conf-{r["confianza"].lower()}'>{r['confianza']}</span> - {r['n_pre']} imagenes PRE / {r['n_post']} imagenes POST</p>
<h2>Resultado</h2>
<p>Dano ponderado estimado: <span class='badge {r["clasificacion"].lower()}'>{r['dano_pond_val']}% - {r['clasificacion']}</span></p>
<div class='grid'>
  <div class='card'><div>Area total</div><div class='num'>{area_total} ha</div></div>
  <div class='card'><div>Area afectada</div><div class='num'>{r['area_afectada']:.0f} ha</div><div>({r['area_afectada']/max(area_total,0.01)*100:.1f}%)</div></div>
  <div class='card'><div>NDVI pre</div><div class='num'>{r['ndvi_pre_val']}</div></div>
  <div class='card'><div>NDVI post</div><div class='num'>{r['ndvi_post_val']}</div></div>
  <div class='card'><div>Delta relativo</div><div class='num'>{r['delta_rel_val']}%</div></div>
  <div class='card'><div>Dano ponderado</div><div class='num'>{r['dano_pond_val']}%</div></div>
</div>
<h2>Metodologia</h2>
<div class='formula'>
delta_rel      = (NDVI_pre - NDVI_post) / NDVI_pre x 100 = {r['delta_rel_val']}%<br>
dano_ponderado = {r['delta_rel_val']}% x {r['peso_fenologico']} (peso fenologico: {r['etapa_desc']}) x {FACTOR_CONSERVADOR} (factor conservador)<br>
               = <b>{r['dano_pond_val']}%</b>
</div>
<h2>Distribucion espacial del dano</h2>
<table>
<tr><th>Categoria</th><th>Area (ha)</th><th>% del lote</th></tr>
<tr><td>Leve (20-40%)</td><td>{r['area_leve']:.1f}</td><td>{r['area_leve']/max(area_total,0.01)*100:.1f}%</td></tr>
<tr><td>Moderado (40-70%)</td><td>{r['area_moderada']:.1f}</td><td>{r['area_moderada']/max(area_total,0.01)*100:.1f}%</td></tr>
<tr><td>Severo (&gt;70%)</td><td>{r['area_severa']:.1f}</td><td>{r['area_severa']/max(area_total,0.01)*100:.1f}%</td></tr>
<tr><td><b>Total afectado</b></td><td><b>{r['area_afectada']:.1f}</b></td><td><b>{r['area_afectada']/max(area_total,0.01)*100:.1f}%</b></td></tr>
</table>
<h2>Trazabilidad</h2>
<table>
<tr><th>Parametro</th><th>Detalle</th></tr>
<tr><td>Sensor</td><td>Sentinel-2 MSI - Copernicus / ESA</td></tr>
<tr><td>Resolucion</td><td>10 m/pixel</td></tr>
<tr><td>Indice</td><td>NDVI = (B8 - B4) / (B8 + B4)</td></tr>
<tr><td>Baseline</td><td>{r['anios_baseline'][-1]}-{r['anios_baseline'][0]} (misma ventana fenologica)</td></tr>
<tr><td>Confianza</td><td>{r['confianza']} - {r['n_pre']} img PRE / {r['n_post']} img POST</td></tr>
<tr><td>Procesamiento</td><td>Google Earth Engine - AgroIA Eventualidades v2.3</td></tr>
<tr><td>Generado</td><td>{time_str}</td></tr>
</table>
<div class='footer'>
<p>AgroIA Eventualidades v2.3 | Sentinel-2 L2A (Copernicus) | Baseline 3 anios</p>
<p>Los valores son estimaciones satelitales. Se recomienda validacion en campo para siniestros formales.</p>
</div></div></body></html>"""

    if output_path:
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
        return output_path

    return html
