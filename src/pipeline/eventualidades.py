# src/pipeline/eventualidades.py
"""
AgroIA Eventualidades — Evaluacion satelital de dano agricola
MIGRADO A COPERNICUS CDSE (Soberano)

Metodo:  delta_rel = (NDVI_pre - NDVI_post) / NDVI_pre x 100
         dano_ponderado = delta_rel x peso_fenologico x factor_conservador(0.92)
Fuente:  Copernicus Sentinel-2 L2A (Soberano via CDSE)
"""

import sys
import os
import zipfile
import math
import numpy as np
import pandas as pd
import geopandas as gpd
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
from .eodag_extractor import init_eodag, get_eodag_ndvi


# ─────────────────────────────────────────────────────────────
# Ingesta de poligono
# ─────────────────────────────────────────────────────────────

def cargar_poligono_desde_gdf(gdf):
    """
    Carga poligono desde un GeoDataFrame.
    Retorna (shapely.geometry.Polygon, list_coords_lonlat).
    """
    if gdf.crs is None:
        gdf = gdf.set_crs('EPSG:4326')
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs('EPSG:4326')

    geom = gdf.geometry.iloc[0]
    if geom.geom_type == 'MultiPolygon':
        geom = max(geom.geoms, key=lambda g: g.area)

    coords = [[lon, lat] for lon, lat in geom.exterior.coords]
    return geom, coords


# ─────────────────────────────────────────────────────────────
# PIPELINE PRINCIPAL (VERSION CDSE LIGERA)
# ─────────────────────────────────────────────────────────────

def run_eventualidades(geom_shapely, fecha_evento, cultivo, tipo_evento,
                       caso_nombre="Analisis AgroIA", log_fn=None):
    """
    Ejecuta el pipeline de Eventualidades usando el motor de Copernicus CDSE.
    """
    if log_fn is None:
        log_fn = print

    fecha_obj = datetime.strptime(fecha_evento, '%Y-%m-%d')
    mes_evento = fecha_obj.month

    # Fenologia
    etapa_desc, peso_fenologico = get_peso_fenologico(cultivo, mes_evento)
    log_fn(f"Cultivo: {cultivo.upper()} - {etapa_desc}")
    log_fn(f"Peso fenologico: {peso_fenologico}")

    # ── NDVI pre y post via CDSE ─────────────────────────────
    log_fn("Consultando Copernicus CDSE (Análisis Soberano)...")
    
    # En esta versión simplificada usamos el extractor por mes
    # (Para máxima precisión se usaría la Statistical API con fechas exactas)
    # Cruza el año correctamente para eventos en enero / diciembre.
    if fecha_obj.month > 1:
        pre_year, pre_month = fecha_obj.year, fecha_obj.month - 1
    else:
        pre_year, pre_month = fecha_obj.year - 1, 12
    if fecha_obj.month < 12:
        post_year, post_month = fecha_obj.year, fecha_obj.month + 1
    else:
        post_year, post_month = fecha_obj.year + 1, 1

    ndvi_pre = get_eodag_ndvi(geom_shapely, pre_year, pre_month)
    ndvi_post = get_eodag_ndvi(geom_shapely, post_year, post_month)

    if ndvi_pre is None or ndvi_post is None:
        log_fn("⚠ No se pudieron obtener datos satelitales suficientes para el análisis.")
        return None

    # ── Calculo de daño ──────────────────────────────────────
    delta_rel_val = round(max((ndvi_pre - ndvi_post) / max(ndvi_pre, 0.01) * 100, 0), 1)
    dano_pond_val = round(delta_rel_val * peso_fenologico * FACTOR_CONSERVADOR, 1)

    if dano_pond_val < 20:
        clasificacion = 'LEVE'
    elif dano_pond_val < 40:
        clasificacion = 'MODERADO'
    else:
        clasificacion = 'SEVERO'

    log_fn(f"Daño ponderado: {dano_pond_val}% ({clasificacion})")

    # Área total real del polígono (proyectando al UTM correspondiente)
    try:
        gdf_tmp = gpd.GeoDataFrame(geometry=[geom_shapely], crs="EPSG:4326")
        area_total_ha = round(gdf_tmp.to_crs(gdf_tmp.estimate_utm_crs()).geometry.area.iloc[0] / 10_000, 1)
    except Exception:
        area_total_ha = 0.0

    # Distribución de daño derivada del % ponderado (proxy hasta tener pixel-wise SCL)
    frac_afectada = min(dano_pond_val / 100.0, 1.0)
    area_afectada_ha = round(area_total_ha * frac_afectada, 1)

    if clasificacion == 'SEVERO':
        area_leve = round(area_total_ha * 0.10, 1)
        area_moderada = round(area_total_ha * 0.25, 1)
        area_severa = round(area_afectada_ha - area_leve - area_moderada, 1)
    elif clasificacion == 'MODERADO':
        area_leve = round(area_total_ha * 0.15, 1)
        area_severa = round(area_total_ha * 0.05, 1)
        area_moderada = round(area_afectada_ha - area_leve - area_severa, 1)
    else:
        area_leve = round(area_afectada_ha * 0.80, 1)
        area_moderada = round(area_afectada_ha * 0.15, 1)
        area_severa = round(area_afectada_ha * 0.05, 1)

    # Confianza basada en disponibilidad de ambos NDVI (extensible a métricas de nubes)
    confianza = 'Alta' if (ndvi_pre and ndvi_post and ndvi_pre > 0.05 and ndvi_post > 0.05) else 'Media'

    result = {
        'caso_nombre': caso_nombre,
        'fecha_evento': fecha_evento,
        'cultivo': cultivo,
        'tipo_evento': tipo_evento,
        'etapa_desc': etapa_desc,
        'peso_fenologico': peso_fenologico,
        'ndvi_pre_val': round(ndvi_pre, 3),
        'ndvi_post_val': round(ndvi_post, 3),
        'baseline_val': round(ndvi_pre, 3),
        'delta_rel_val': delta_rel_val,
        'delta_adj_val': round(delta_rel_val * peso_fenologico, 1),
        'dano_pond_val': dano_pond_val,
        'clasificacion': clasificacion,
        'confianza': confianza,
        'area_total': area_total_ha,
        'area_afectada': area_afectada_ha,
        'area_leve': max(0.0, area_leve),
        'area_moderada': max(0.0, area_moderada),
        'area_severa': max(0.0, area_severa),
        'geom_shapely': geom_shapely,
    }

    return result


# ─────────────────────────────────────────────────────────────
# MAPA FOLIUM (MIGRADO)
# ─────────────────────────────────────────────────────────────

def generar_mapa_folium(result, df_muestreo=None):
    """Genera mapa con la ubicación del evento."""
    geom = result['geom_shapely']
    centro = [geom.centroid.y, geom.centroid.x]

    m = folium.Map(
        location=centro, zoom_start=14,
        tiles='https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}',
        attr='Google Satellite Hybrid'
    )

    # Borde del lote
    coords = [[lat, lon] for lon, lat in geom.exterior.coords]
    folium.Polygon(
        locations=coords,
        color='red', weight=3, fill=True, fill_opacity=0.2,
        tooltip=f"Lote Afectado: {result['dano_pond_val']}%"
    ).add_to(m)

    return m

# ─────────────────────────────────────────────────────────────
# VISUALIZACIONES DE PERITAJE
# ─────────────────────────────────────────────────────────────

def plot_donut_dano(result):
    """
    Gráfico donut con la distribución de severidad del daño.
    Retorna un BytesIO con PNG o None si matplotlib no está disponible.
    """
    if not HAS_MATPLOTLIB:
        return None

    try:
        area_leve = float(result.get('area_leve', 0))
        area_moderada = float(result.get('area_moderada', 0))
        area_severa = float(result.get('area_severa', 0))
        area_total = float(result.get('area_total', 0))
        area_sana = max(0.0, area_total - (area_leve + area_moderada + area_severa))

        valores = [area_sana, area_leve, area_moderada, area_severa]
        labels = ['Sana', 'Leve', 'Moderada', 'Severa']
        colores = ['#40916C', '#F4D35E', '#F4A261', '#D62828']

        # Filtrar segmentos vacíos para que el donut no se vea raro
        pairs = [(v, l, c) for v, l, c in zip(valores, labels, colores) if v > 0]
        if not pairs:
            return None
        valores, labels, colores = zip(*pairs)

        fig, ax = plt.subplots(figsize=(5, 5))
        fig.patch.set_facecolor('white')
        wedges, _ = ax.pie(
            valores,
            colors=colores,
            startangle=90,
            wedgeprops=dict(width=0.35, edgecolor='white', linewidth=2)
        )

        # Texto central
        clasificacion = result.get('clasificacion', '—')
        dano_pct = result.get('dano_pond_val', 0)
        ax.text(0, 0.1, f"{dano_pct}%", ha='center', va='center',
                fontsize=22, fontweight='bold', color='#1B4332')
        ax.text(0, -0.15, clasificacion, ha='center', va='center',
                fontsize=11, color='#495057')

        ax.legend(wedges, [f"{l}: {v:.1f} ha" for l, v in zip(labels, valores)],
                  loc='upper center', bbox_to_anchor=(0.5, 0.02),
                  ncol=2, fontsize=8, frameon=False)
        ax.set_aspect('equal')

        buf = BytesIO()
        fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception:
        return None


def plot_comparativa_ndvi(result):
    """
    Panel comparativo NDVI pre-evento vs post-evento vs baseline histórico.
    Retorna un BytesIO con PNG o None si matplotlib no está disponible.
    """
    if not HAS_MATPLOTLIB:
        return None

    try:
        ndvi_pre = float(result.get('ndvi_pre_val', 0))
        ndvi_post = float(result.get('ndvi_post_val', 0))
        baseline = float(result.get('baseline_val', ndvi_pre))
        delta_rel = float(result.get('delta_rel_val', 0))
        peso = float(result.get('peso_fenologico', 1.0))
        clasificacion = result.get('clasificacion', '—')

        fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
        fig.patch.set_facecolor('white')

        # Panel 1: Comparativa NDVI
        ax1 = axes[0]
        etiquetas = ['Baseline', 'Pre-evento', 'Post-evento']
        valores = [baseline, ndvi_pre, ndvi_post]
        colores_barras = ['#40916C', '#74C69D', '#D62828' if ndvi_post < ndvi_pre else '#F4A261']
        bars = ax1.bar(etiquetas, valores, color=colores_barras, edgecolor='white', linewidth=1.2)
        for bar, v in zip(bars, valores):
            ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.015,
                     f"{v:.3f}", ha='center', fontsize=10, fontweight='bold', color='#212529')
        ax1.set_ylim(0, max(1.0, max(valores) * 1.2))
        ax1.set_ylabel('NDVI medio', fontsize=9)
        ax1.set_title('NDVI Pre vs Post Evento', fontsize=11, loc='left')
        ax1.grid(axis='y', linestyle=':', alpha=0.4)
        ax1.spines['top'].set_visible(False)
        ax1.spines['right'].set_visible(False)

        # Panel 2: Severidad calculada
        ax2 = axes[1]
        color_sev = '#D62828' if clasificacion == 'SEVERO' else '#F4A261' if clasificacion == 'MODERADO' else '#F4D35E'
        ax2.barh(['ΔNDVI relativo', 'Peso fenológico', 'Daño ponderado'],
                 [delta_rel, peso * 100, result.get('dano_pond_val', 0)],
                 color=['#74C69D', '#40916C', color_sev], edgecolor='white', linewidth=1.2)
        ax2.set_xlim(0, max(100, delta_rel * 1.2))
        ax2.set_xlabel('Magnitud (%)', fontsize=9)
        ax2.set_title(f'Severidad: {clasificacion}', fontsize=11, loc='left', color=color_sev, fontweight='bold')
        ax2.grid(axis='x', linestyle=':', alpha=0.4)
        ax2.spines['top'].set_visible(False)
        ax2.spines['right'].set_visible(False)

        plt.tight_layout()
        buf = BytesIO()
        fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception:
        return None


# Otros métodos stubbed para evitar errores de importación
def generar_puntos_muestreo(result, n_puntos=5):
    return pd.DataFrame()

def generar_reporte_html(result, output_path=None):
    return "<html><body>Reporte Soberano Copernicus CDSE</body></html>"
