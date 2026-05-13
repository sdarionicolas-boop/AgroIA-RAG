"""
=============================================================================
AGROIA - POLIGONIZACIÓN AUTOMÁTICA DE LOTES
Pipeline Unificado v2.0 (Pro)

Este script unifica las versiones Local (CPU/GPU) y Colab, optimizando:
1. Detección automática de hardware (CUDA/CPU).
2. Control de fugas avanzado en SAM (puntos negativos dinámicos).
3. Integración robusta con GEE (Sentinel-2 Harmonized).
4. Generación de reportes: GeoJSON, CSV, PDF y Mapa HTML interactivo.
5. Soporte para entrada CSV y Excel (.xlsx).

Autor: AgroIA Team
Tecnología: Google Earth Engine + Segment Anything Model (SAM)
=============================================================================
"""

import os
import sys
import json
import time
import math
import argparse
import warnings
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
import matplotlib.cm as cm
import cv2

# Detección de Colab para progreso visual
try:
    from google.colab import drive
    IN_COLAB = True
    from tqdm.notebook import tqdm
except ImportError:
    IN_COLAB = False
    from tqdm import tqdm

# Detección de Hardware
try:
    import torch
    DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'
except ImportError:
    DEVICE = 'cpu'

try:
    import ee
    import geopandas as gpd
    from shapely.geometry import Polygon, mapping, shape
    from segment_anything import sam_model_registry, SamPredictor
except ImportError as e:
    print(f"❌ Falta libreria: {e}")
    print("   Ejecutar: pip install segment-anything earthengine-api geopandas shapely opencv-python-headless pandas numpy matplotlib tqdm scipy fpdf2 folium")
    sys.exit(1)

warnings.filterwarnings('ignore')
matplotlib.use('Agg')
cmap = matplotlib.colormaps['RdYlGn']


# ============================================================================
# CONFIGURACIÓN GLOBAL
# ============================================================================

class Config:
    """Configuración centralizada del pipeline."""

    GEE = {
        'project': 'applied-oxygen-459415-e2',
        'buffer_m': 2500,
        'max_nubes_pct': 20,
        'dias_ventana': 60,
    }

    SAM = {
        'checkpoint': 'sam_vit_b_01ec64.pth',
        'model_type': 'vit_b',
        'device': DEVICE,
    }

    POLYGON = {
        'min_area_ha': 5,
        'max_area_ha': 800,
        'tolerancia_fuga': 1.5,
        'margen_px_min': 20,
        'margen_px_max': 80,
        'factor_suavizado': 0.005,
    }

    OUTPUT = {
        'guardar_cada': 10,
        'fecha_fallback_inicio': '2025-12-01',
        'fecha_fallback_fin': '2026-03-31',
    }

    COLUMNS = {
        'id': ['id', 'taype', 'numero', 'nro', 'lote_id'],
        'lat': ['lat_dec', 'latitude', 'lat', 'cg_latitud', 'y', 'latitud'],
        'lon': ['lon_dec', 'longitude', 'lon', 'cg_longitud', 'x', 'longitud'],
        'fecha': ['fecha_de_s', 'fecha', 'date', 'fecha_siniestro', 'fecha_evento'],
        'dano_ha': ['has__daña', 'dano_ha', 'dano_estimado', 'area_ha', 'sup_afectada_ha', 'superficie', 'area'],
        'cultivo': ['cultivo', 'crop', 'cultivoafectado', 'especie'],
        'localidad': ['localidad', 'location', 'loc', 'ciudad', 'departamento'],
        'provincia': ['provincia', 'prov', 'estado'],
    }


# ============================================================================
# UTILIDADES DE DATOS
# ============================================================================

def parse_decimal(val):
    """Convierte valores numéricos manejando formatos europeos y errores."""
    if pd.isna(val):
        return np.nan
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(',', '.')
    if s.count('.') > 1:
        parts = s.split('.')
        s = ''.join(parts[:-1]) + '.' + parts[-1]
    try:
        return float(s)
    except:
        return np.nan


def encontrar_columna(patrones, columnas):
    """Mapea columnas del archivo a los nombres internos esperados."""
    for p in patrones:
        for col in columnas:
            if col is not None and str(col).strip():
                if p in str(col).strip().lower():
                    return col
    return None


def bounding_box_dinamico(area_ref, shape_hw):
    """Calcula el zoom óptimo para SAM basado en el tamaño esperado del lote."""
    lado_m = math.sqrt(max(area_ref, 1) * 10000)
    margen = int((lado_m / 2) / 10) # 10m/px aprox en S2
    margen = max(Config.POLYGON['margen_px_min'],
                min(margen, Config.POLYGON['margen_px_max']))
    h, w = shape_hw
    cx, cy = w // 2, h // 2
    return np.array([max(0, cx-margen), max(0, cy-margen), 
                     min(w-1, cx+margen), min(h-1, cy+margen)])


def pixel_a_geo(col, row, lon_min, lon_max, lat_min, lat_max, w, h):
    """Interpolación lineal para convertir píxel a coordenadas geográficas."""
    lon = lon_min + (col / w) * (lon_max - lon_min)
    lat = lat_max - (row / h) * (lat_max - lat_min)
    return (lon, lat)


def calcular_area_ha(poligono, lat_centro):
    """Cálculo de área geodésica simplificada en hectáreas."""
    factor = math.cos(math.radians(lat_centro))
    return poligono.area * (111320 ** 2) * factor / 10000


def ndvi_a_rgb(ndvi):
    """Normaliza NDVI y aplica colormap para visualización de SAM."""
    ndvi_clip = np.clip(ndvi, -0.2, 0.8)
    ndvi_norm = ((ndvi_clip + 0.2) * 255).astype(np.uint8)
    return (cmap(ndvi_norm / 255.0)[:, :, :3] * 255).astype(np.uint8)


# ============================================================================
# CLASES MOTOR (GEE + SAM)
# ============================================================================

class EngineSatelital:
    """Maneja la conexión y descarga de Google Earth Engine."""
    def __init__(self, config=None):
        self.config = config or Config.GEE
        self.conectado = False

    def conectar(self):
        if self.conectado: return True
        try:
            ee.Initialize(project=self.config['project'])
            self.conectado = True
            return True
        except:
            try:
                ee.Authenticate()
                ee.Initialize(project=self.config['project'])
                self.conectado = True
                return True
            except Exception as e:
                print(f"❌ Error GEE: {e}")
                return False

    def descargar_ndvi(self, lat, lon, f_ini, f_fin):
        """Descarga la mejor escena S2 disponible en el rango."""
        try:
            centro = ee.Geometry.Point([lon, lat])
            bbox = centro.buffer(self.config['buffer_m']).bounds()
            
            # S2_HARMONIZED es más estable para series temporales
            coleccion = (ee.ImageCollection('COPERNICUS/S2_HARMONIZED')
                        .filterBounds(bbox)
                        .filterDate(f_ini, f_fin)
                        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', self.config['max_nubes_pct']))
                        .sort('CLOUDY_PIXEL_PERCENTAGE'))

            if coleccion.size().getInfo() == 0: return None, None
            
            img = coleccion.first()
            datos = img.select(['B4', 'B8']).sampleRectangle(region=bbox, defaultValue=0)
            
            rojo = np.array(datos.get('B4').getInfo(), dtype=np.float32)
            nir = np.array(datos.get('B8').getInfo(), dtype=np.float32)
            ndvi = (nir - rojo) / (nir + rojo + 1e-6)
            
            return ndvi, bbox
        except Exception as e:
            print(f"  ⚠ Error descarga ({lat}, {lon}): {e}")
            return None, None


class SegmentadorSAM:
    """Maneja la inferencia de Segment Anything Model."""
    def __init__(self, config=None):
        self.config = config or Config.SAM
        self.predictor = None

    def cargar(self):
        if self.predictor: return True
        cp = self.config['checkpoint']
        if not os.path.exists(cp):
            print(f"📥 Descargando SAM ({self.config['model_type']})...")
            os.system(f'wget -q https://dl.fbaipublicfiles.com/segment_anything/{cp}')
        
        try:
            sam = sam_model_registry[self.config['model_type']](checkpoint=cp)
            sam.to(self.config['device'])
            self.predictor = SamPredictor(sam)
            print(f"✅ SAM listo en {self.config['device'].upper()}")
            return True
        except Exception as e:
            print(f"❌ Error SAM: {e}")
            return False

    def segmentar(self, ndvi, area_ref, bbox_ee=None):
        """Segmenta con control de fugas automático."""
        if not self.predictor: return {'status': 'SAM_ERROR'}
        
        h, w = ndvi.shape
        self.predictor.set_image(ndvi_a_rgb(ndvi))
        
        box = bounding_box_dinamico(area_ref, (h, w))
        cx, cy = w // 2, h // 2
        
        # Intento 1: Detección estándar
        masks, scores, _ = self.predictor.predict(
            point_coords=np.array([[cx, cy]]),
            point_labels=np.array([1]),
            box=box[None, :],
            multimask_output=True
        )
        
        idx = np.argmax(scores)
        mask = masks[idx].copy()
        score = float(scores[idx])
        area_px = mask.sum()
        area_ha_aprox = (area_px * 100) / 10000 # 10m res
        
        # Refinamiento si hay fuga (área mucho mayor a la esperada)
        if area_ha_aprox > area_ref * Config.POLYGON['tolerancia_fuga']:
            x0, y0, x1, y1 = box
            # Puntos negativos en las esquinas del BBox sugerido
            pts_neg = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
            coords = np.vstack([[cx, cy], pts_neg])
            labels = np.array([1, 0, 0, 0, 0])
            
            m2, s2, _ = self.predictor.predict(
                point_coords=coords, point_labels=labels,
                box=box[None, :], multimask_output=False
            )
            mask, score = m2[0].copy(), float(s2[0])
            area_ha_aprox = (mask.sum() * 100) / 10000

        # Vectorización
        contornos, _ = cv2.findContours((mask * 255).astype(np.uint8), 
                                         cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contornos: return {'status': 'SIN_CONTORNO', 'score': score}
        
        cnt = max(contornos, key=cv2.contourArea)
        eps = Config.POLYGON['factor_suavizado'] * cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, eps, True).squeeze()
        
        if approx.ndim == 1 or len(approx) < 3: return {'status': 'CONTORNO_INVALIDO'}
        
        # Georreferenciación
        poligono = None
        if bbox_ee:
            coords_bb = bbox_ee.bounds().getInfo()['coordinates'][0]
            lons = [c[0] for c in coords_bb]; lats = [c[1] for c in coords_bb]
            v_geo = [pixel_a_geo(p[0], p[1], min(lons), max(lons), min(lats), max(lats), w, h) for p in approx]
            poligono = Polygon(v_geo).buffer(0)

        return {
            'status': 'OK',
            'poligono': poligono,
            'area_ha': round(area_ha_aprox, 1),
            'score': round(score, 3),
            'vertices': len(approx)
        }


# ============================================================================
# PIPELINE COORDINADOR
# ============================================================================

class AgroIAPipeline:
    def __init__(self):
        self.gee = EngineSatelital()
        self.sam = SegmentadorSAM()
        self.df = None
        self.results = []
        self.features = []

    def cargar_datos(self, path):
        print(f"📂 Cargando: {path}")
        df = pd.read_excel(path) if path.endswith('.xlsx') else pd.read_csv(path)
        
        renames = {}
        for target, patterns in Config.COLUMNS.items():
            col = encontrar_columna(patterns, df.columns)
            if col: renames[col] = target
        
        df = df.rename(columns=renames)
        
        for c in ['lat', 'lon', 'dano_ha']:
            if c in df.columns: df[c] = df[c].apply(parse_decimal)
        
        if 'fecha' in df.columns:
            df['fecha'] = pd.to_datetime(df['fecha'], errors='coerce', dayfirst=True)
        
        # Limpieza básica
        df = df.dropna(subset=['lat', 'lon'])
        if 'id' not in df.columns: df['id'] = range(1, len(df)+1)
        df['id'] = df['id'].astype(str)
        
        # Filtro Argentina
        df = df[df['lat'].between(-42, -22) & df['lon'].between(-70, -53)]
        
        # Lotes únicos para procesar (reducción por proximidad)
        df['lat_r'] = df['lat'].round(4)
        df['lon_r'] = df['lon'].round(4)
        self.df = df.drop_duplicates(subset=['lat_r', 'lon_r']).reset_index(drop=True)
        
        print(f"✅ {len(self.df)} lotes preparados.")

    def ejecutar(self, output_prefix='agroia_results'):
        if self.df is None: return
        
        self.gee.conectar()
        self.sam.cargar()
        
        out_geojson = f"{output_prefix}.geojson"
        out_csv = f"{output_prefix}_log.csv"
        
        print(f"🚀 Iniciando poligonización en {DEVICE.upper()}...")
        
        pbar = tqdm(self.df.iterrows(), total=len(self.df), desc="Procesando")
        
        for idx, row in pbar:
            t0 = time.time()
            area_ref = row.get('dano_ha', 50)
            if pd.isna(area_ref) or area_ref <= 0: area_ref = 50
            
            f_ini = (row['fecha'] - pd.Timedelta(days=Config.GEE['dias_ventana'])).strftime('%Y-%m-%d') if pd.notna(row['fecha']) else Config.OUTPUT['fecha_fallback_inicio']
            f_fin = row['fecha'].strftime('%Y-%m-%d') if pd.notna(row['fecha']) else Config.OUTPUT['fecha_fallback_fin']
            
            ndvi, bbox = self.gee.descargar_ndvi(row['lat'], row['lon'], f_ini, f_fin)
            
            res = {'id': row['id'], 'estado': 'SIN_IMAGEN', 'area_ha': 0, 'score': 0, 'segundos': round(time.time()-t0, 1)}
            
            if ndvi is not None:
                sam_res = self.sam.segmentar(ndvi, area_ref, bbox)
                res.update(sam_res)
                
                if sam_res['status'] == 'OK' and sam_res['poligono']:
                    real_ha = calcular_area_ha(sam_res['poligono'], row['lat'])
                    res['area_ha'] = round(real_ha, 1)
                    
                    if real_ha < Config.POLYGON['min_area_ha']: res['estado'] = 'AREA_MIN_FAIL'
                    elif real_ha > Config.POLYGON['max_area_ha']: res['estado'] = 'SOBRE_SEGMENTADO'
                    else:
                        feat = {
                            "type": "Feature",
                            "properties": {k: str(row.get(k, '')) for k in ['id', 'localidad', 'cultivo']},
                            "geometry": mapping(sam_res['poligono'])
                        }
                        feat['properties'].update({
                            "area_ha": res['area_ha'], "error_pct": round(abs(res['area_ha']-area_ref)/area_ref*100, 1),
                            "sam_score": res['score'], "fecha_sat": f_fin
                        })
                        self.features.append(feat)
            
            self.results.append(res)
            pbar.set_postfix({'status': res['estado'], 'ha': res['area_ha']})
            
            if len(self.results) % Config.OUTPUT['guardar_cada'] == 0:
                self._guardar(out_geojson, out_csv)
        
        self._guardar(out_geojson, out_csv)
        self._finalizar(output_prefix)

    def _guardar(self, g, c):
        with open(g, 'w') as f: json.dump({"type": "FeatureCollection", "features": self.features}, f)
        pd.DataFrame(self.results).drop(columns=['poligono'], errors='ignore').to_csv(c, index=False)

    def _finalizar(self, prefix):
        print(f"\n📊 Resultados: {len(self.features)} polígonos válidos.")
        self.generar_mapa(f"{prefix}_mapa.html")
        self.generar_pdf(f"{prefix}_reporte.pdf")

    def generar_mapa(self, path):
        try:
            import folium
            if not self.features: return
            gdf = gpd.GeoDataFrame.from_features(self.features)
            centro = [gdf.geometry.centroid.y.mean(), gdf.geometry.centroid.x.mean()]
            m = folium.Map(location=centro, zoom_start=8, tiles='CartoDB positron')
            for _, r in gdf.iterrows():
                color = 'green' if r.get('error_pct', 100) < 25 else 'orange'
                folium.GeoJson(r.geometry.__geo_interface__, style_function=lambda x, c=color: {'fillColor': c, 'color': c}).add_to(m)
            m.save(path)
            print(f"🗺️ Mapa: {path}")
        except: pass

    def generar_pdf(self, path):
        try:
            from fpdf import FPDF
            df = pd.DataFrame(self.results)
            pdf = FPDF(); pdf.add_page(); pdf.set_font("Arial", 'B', 16)
            pdf.cell(0, 10, "AgroIA - Reporte de Poligonización", 1, 1, 'C')
            pdf.ln(10); pdf.set_font("Arial", size=12)
            pdf.cell(0, 10, f"Total procesado: {len(df)}", 0, 1)
            pdf.cell(0, 10, f"Exitosos (OK): {len(self.features)}", 0, 1)
            pdf.output(path)
            print(f"📄 Reporte: {path}")
        except: pass


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='AgroIA Unificada - SAM + GEE')
    parser.add_argument('--csv', default='siniestros.csv', help='Entrada CSV o XLSX')
    parser.add_argument('--output', default='agroia_poligonos', help='Prefijo de salida')
    args = parser.parse_args()

    pipe = AgroIAPipeline()
    try:
        pipe.cargar_datos(args.csv)
        pipe.ejecutar(args.output)
        print("\n✅ Proceso completado con éxito.")
    except Exception as e:
        print(f"❌ Error fatal: {e}")
        traceback.print_exc()
