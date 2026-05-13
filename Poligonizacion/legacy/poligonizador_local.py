"""
=============================================================================
POLIGONIZACIÓN AUTOMÁTICA DE LOTES AGRÍCOLAS
Pipeline Productivo v1.0 - LOCAL GPU

⚠️  REQUISITO: NVIDIA GPU con CUDA ( mínimo RTX 2070 / T4 / A100 )
❌  NO funciona en CPU. El script fallará si no hay GPU disponible.

Autor: Proyecto Poligonización Argentina
Tecnología: Google Earth Engine + Segment Anything Model (SAM)
Uso: Aseguradoras agrícolas, agrotech, consultoras

MODO DE USO:
    python poligonizador_local.py                    # Default CSV
    python poligonizador_local.py --csv datos.csv   # CSV específico
    python poligonizador_local.py --resume          # Reanudar
    python poligonizador_local.py --test            # Validar entorno

INSTALACIÓN:
    1. NVIDIA Driver + CUDA 11.8+
    2. pip install -r requisitos.txt
    3. Descargar sam_vit_b_01ec64.pth

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

try:
    import torch
    if not torch.cuda.is_available():
        print("\n" + "=" * 60)
        print("❌ ERROR CRÍTICO: No se detectó GPU NVIDIA con CUDA")
        print("=" * 60)
        print("Este script requiere una GPU NVIDIA con soporte CUDA.")
        print()
        print("Opciones:")
        print("  1. Ejecutar en Google Colab (GPU T4 gratuita)")
        print("     → python poligonizador_colab.py")
        print()
        print("  2. Usar GPU rental (Vast.ai, RunPod)")
        print()
        print("  3. Instalar CUDA:")
        print("     → https://developer.nvidia.com/cuda-downloads")
        print()
        print("  4. Verificar instalación:")
        print("     → nvidia-smi")
        print("     → python -c 'import torch; print(torch.cuda.is_available())'")
        print("=" * 60)
        sys.exit(1)
    
    CUDA_DEVICE = torch.cuda.get_device_name(0)
    print(f"\n🟢 GPU detectada: {CUDA_DEVICE}")
    print(f"   Memoria: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")
except ImportError:
    print("❌ PyTorch no instalado. Ejecutar: pip install torch")
    sys.exit(1)

import pandas as pd
import matplotlib
import matplotlib.cm as cm

import ee
from shapely.geometry import Polygon, mapping, shape
from segment_anything import sam_model_registry, SamPredictor

warnings.filterwarnings('ignore')
matplotlib.use('Agg')
cmap = matplotlib.colormaps['RdYlGn']


# ============================================================================
# CONFIGURACIÓN
# ============================================================================

class Config:
    PROJECT = 'applied-oxygen-459415-e2'
    
    GEE = {
        'project': 'applied-oxygen-459415-e2',
        'buffer_m': 2500,
        'max_nubes_pct': 20,
        'dias_ventana': 60,
    }
    
    SAM = {
        'checkpoint': 'sam_vit_b_01ec64.pth',
        'model_type': 'vit_b',
        'device': 'cuda',
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
    
    CSV_COLUMNS = {
        'id': ['id', 'taype', 'numero', 'nro'],
        'lat': ['lat_dec', 'latitude', 'lat', 'cg_latitud', 'y'],
        'lon': ['lon_dec', 'longitude', 'lon', 'cg_longitud', 'x'],
        'fecha': ['fecha_de_s', 'fecha', 'date', 'fecha_siniestro'],
        'dano_ha': ['has__daña', 'dano_ha', 'dano_estimado', 'area_ha', 
                    'sup_afectada_ha', 'superficie'],
        'cultivo': ['cultivo', 'crop', 'cultivoafectado'],
        'localidad': ['localidad', 'location', 'loc', 'ciudad'],
        'provincia': ['provincia', 'prov', 'estado'],
    }


# ============================================================================
# UTILIDADES
# ============================================================================

def parse_decimal(val):
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
    for p in patrones:
        for col in columnas:
            if col is not None and str(col).strip():
                if p in str(col).strip().lower():
                    return col
    return None


def pixel_a_geo(col, row, lon_min, lon_max, lat_min, lat_max, w, h):
    lon = lon_min + (col / w) * (lon_max - lon_min)
    lat = lat_max - (row / h) * (lat_max - lat_min)
    return (lon, lat)


def calcular_area_ha(poligono, lat_centro):
    factor = math.cos(math.radians(lat_centro))
    return poligono.area * (111320 ** 2) * factor / 10000


def ndvi_a_rgb(ndvi):
    ndvi_clip = np.clip(ndvi, -0.2, 0.8)
    ndvi_norm = ((ndvi_clip + 0.2) * 255).astype(np.uint8)
    return (cmap(ndvi_norm / 255.0)[:, :, :3] * 255).astype(np.uint8)


def guardar_checkpoint(features, log_rows, output_file, log_file):
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, indent=2)
    pd.DataFrame(log_rows).to_csv(log_file, index=False, encoding='utf-8')


def cargar_checkpoint(output_file, log_file):
    features = []
    ids_procesados = set()
    log_rows = []
    
    if os.path.exists(output_file):
        with open(output_file, 'r', encoding='utf-8') as f:
            gj = json.load(f)
        features = gj.get('features', [])
        ids_procesados = {ft['properties'].get('id') for ft in features 
                          if ft['properties'].get('id') is not None}
        print(f"  → GeoJSON previo: {len(features)} polígonos")
    
    if os.path.exists(log_file):
        df_log = pd.read_csv(log_file)
        log_rows = df_log.to_dict('records')
        ids_log = {r['id'] for r in log_rows if r.get('id') is not None}
        ids_procesados |= ids_log
        print(f"  → Log previo: {len(log_rows)} entradas")
    
    return features, ids_procesados, log_rows


def bounding_box_dinamico(area_ref, shape_hw):
    lado_m = math.sqrt(max(area_ref, 1) * 10000)
    margen = int((lado_m / 2) / 10)
    margen = max(Config.POLYGON['margen_px_min'],
                min(margen, Config.POLYGON['margen_px_max']))
    h, w = shape_hw
    cx, cy = w // 2, h // 2
    return np.array([max(0, cx-margen), max(0, cy-margen), 
                     min(w-1, cx+margen), min(h-1, cy+margen)])


# ============================================================================
# MÓDULO GEE
# ============================================================================

class DescargadorSatelital:
    def __init__(self, config=None):
        self.config = config or Config.GEE
        self.conectado = False
    
    def conectar(self):
        if self.conectado:
            return True
        try:
            ee.Initialize(project=self.config['project'])
            self.conectado = True
            print("  ✅ GEE conectado")
            return True
        except Exception:
            try:
                ee.Authenticate()
                ee.Initialize(project=self.config['project'])
                self.conectado = True
                print("  ✅ GEE conectado (autenticado)")
                return True
            except Exception as e:
                print(f"  ❌ Error conectando a GEE: {e}")
                return False
    
    def descargar_ndvi(self, lat, lon, fecha_inicio, fecha_fin):
        try:
            centro = ee.Geometry.Point([lon, lat])
            bbox = centro.buffer(self.config['buffer_m']).bounds()
            
            coleccion = (
                ee.ImageCollection('COPERNICUS/S2_HARMONIZED')
                .filterBounds(bbox)
                .filterDate(fecha_inicio, fecha_fin)
                .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 
                                     self.config['max_nubes_pct']))
                .sort('CLOUDY_PIXEL_PERCENTAGE')
            )
            
            if coleccion.size().getInfo() == 0:
                return None, None
            
            img = coleccion.first()
            datos = img.select(['B4', 'B8']).sampleRectangle(region=bbox, 
                                                            defaultValue=0)
            
            rojo = np.array(datos.get('B4').getInfo(), dtype=np.float32)
            nir = np.array(datos.get('B8').getInfo(), dtype=np.float32)
            ndvi = (nir - rojo) / (nir + rojo + 1e-6)
            
            return ndvi, bbox
        
        except Exception as e:
            print(f"    ⚠ Error descargando: {e}")
            return None, None


# ============================================================================
# MÓDULO SAM
# ============================================================================

class SegmentadorSAM:
    def __init__(self, config=None):
        self.config = config or Config.SAM
        self.predictor = None
        self.modelo = None
    
    def cargar(self):
        if self.predictor is not None:
            return True
        
        checkpoint = self.config['checkpoint']
        
        if not os.path.exists(checkpoint):
            print(f"  📥 Descargando modelo SAM...")
            os.system(f'wget -q https://dl.fbaipublicfiles.com/segment_anything/{checkpoint}')
        
        if not os.path.exists(checkpoint):
            print(f"  ❌ No se encontró {checkpoint}")
            print("  → Descargar: https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth")
            return False
        
        try:
            sam = sam_model_registry[self.config['model_type']](checkpoint=checkpoint)
            sam.to(self.config['device'])
            self.predictor = SamPredictor(sam)
            self.modelo = sam
            print(f"  ✅ SAM cargado en {self.config['device'].upper()}")
            return True
        except Exception as e:
            print(f"  ❌ Error cargando SAM: {e}")
            return False
    
    def segmentar(self, ndvi, area_ref, coordenadas=None):
        if self.predictor is None:
            return {'status': 'SAM_NO_CARGADO', 'mask': None, 'poligono': None,
                    'area_ha': 0, 'score': 0, 'vertices': 0}
        
        h, w = ndvi.shape
        ndvi_rgb = ndvi_a_rgb(ndvi)
        self.predictor.set_image(ndvi_rgb)
        
        box = bounding_box_dinamico(area_ref, (h, w))
        cx, cy = w // 2, h // 2
        
        masks, scores, _ = self.predictor.predict(
            point_coords=np.array([[cx, cy]]),
            point_labels=np.array([1]),
            box=box[None, :],
            multimask_output=True
        )
        
        mejor_idx = np.argmax(scores)
        mask = masks[mejor_idx].copy()
        score = float(scores[mejor_idx])
        area_sam = (mask.sum() * 100) / 10000
        
        if area_sam > area_ref * Config.POLYGON['tolerancia_fuga']:
            x_min, y_min, x_max, y_max = box
            pts_neg = np.array([
                [x_min, y_min], [x_max, y_min],
                [x_max, y_max], [x_min, y_max]
            ])
            todas_coords = np.vstack([[cx, cy], pts_neg])
            todas_labels = np.array([1, 0, 0, 0, 0])
            
            masks2, scores2, _ = self.predictor.predict(
                point_coords=todas_coords,
                point_labels=todas_labels,
                box=box[None, :],
                multimask_output=False
            )
            mask = masks2[0].copy()
            score = float(scores2[0])
            area_sam = (mask.sum() * 100) / 10000
            
            if area_sam > area_ref * Config.POLYGON['tolerancia_fuga']:
                return {
                    'status': 'FUGA_NO_RESUELTA',
                    'mask': mask, 'poligono': None,
                    'area_ha': round(area_sam, 1), 
                    'score': round(score, 3),
                    'vertices': 0
                }
        
        contornos, _ = cv2.findContours(
            (mask * 255).astype(np.uint8),
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )
        
        if not contornos:
            return {'status': 'SIN_CONTORNO', 'mask': mask, 'poligono': None,
                    'area_ha': round(area_sam, 1), 
                    'score': round(score, 3), 
                    'vertices': 0}
        
        contorno = max(contornos, key=cv2.contourArea)
        epsilon = Config.POLYGON['factor_suavizado'] * cv2.arcLength(contorno, True)
        contorno_simple = cv2.approxPolyDP(contorno, epsilon, True)
        puntos = contorno_simple.squeeze()
        
        if puntos.ndim == 1:
            puntos = puntos.reshape(1, -1)
        if len(puntos) < 3:
            return {'status': 'CONTORNO_INVALIDO', 'mask': mask, 'poligono': None,
                    'area_ha': round(area_sam, 1), 
                    'score': round(score, 3), 
                    'vertices': 0}
        
        if coordenadas and 'bbox' in coordenadas:
            coords_bbox = coordenadas['bbox'].bounds().getInfo()['coordinates'][0]
            lons = [c[0] for c in coords_bbox]
            lats = [c[1] for c in coords_bbox]
            lon_min, lon_max = min(lons), max(lons)
            lat_min, lat_max = min(lats), max(lats)
            
            vertices_geo = [
                pixel_a_geo(p[0], p[1], lon_min, lon_max, lat_min, lat_max, w, h)
                for p in puntos
            ]
            poligono = Polygon(vertices_geo).buffer(0)
        else:
            poligono = None
        
        return {
            'status': 'OK',
            'mask': mask,
            'poligono': poligono,
            'area_ha': round(area_sam, 1),
            'score': round(score, 3),
            'vertices': len(puntos)
        }


# ============================================================================
# PIPELINE PRINCIPAL
# ============================================================================

class PipelinePoligonizacion:
    def __init__(self, config=None, csv_path=None):
        self.config = config or Config
        self.csv_path = csv_path
        self.descargador = DescargadorSatelital()
        self.segmentador = SegmentadorSAM()
        self.df = None
        self.df_geo = None
        self.features = []
        self.log_rows = []
        self.stats = {
            'total': 0, 'ok': 0, 'fugas': 0, 'sin_imagen': 0,
            'otros_errores': 0, 'tiempo_total_s': 0
        }
    
    def cargar_datos(self, csv_path=None):
        path = csv_path or self.csv_path
        if not path or not os.path.exists(path):
            raise FileNotFoundError(f"No se encontró: {path}")
        
        print(f"\n📂 Cargando: {path}")
        df_raw = pd.read_csv(path)
        print(f"  → {len(df_raw)} registros")
        
        rename_dict = {}
        for target, patrones in Config.CSV_COLUMNS.items():
            col = encontrar_columna(patrones, df_raw.columns)
            if col:
                rename_dict[col] = target
        
        self.df = df_raw.rename(columns=rename_dict)
        
        for col in ['lat', 'lon', 'dano_ha']:
            if col in self.df.columns:
                self.df[col] = self.df[col].apply(parse_decimal)
        
        if 'fecha' in self.df.columns:
            self.df['fecha'] = pd.to_datetime(self.df['fecha'], errors='coerce', dayfirst=True)
        else:
            self.df['fecha'] = pd.NaT
        
        self.df = self.df.dropna(subset=['lat', 'lon'])
        self.df = self.df[self.df['lat'].between(-42, -22)]
        self.df = self.df[self.df['lon'].between(-70, -53)]
        
        if 'id' not in self.df.columns or self.df['id'].isna().all():
            self.df['id'] = range(1, len(self.df) + 1)
        self.df['id'] = self.df['id'].astype(str)
        
        self.df['lat_r'] = self.df['lat'].round(4)
        self.df['lon_r'] = self.df['lon'].round(4)
        
        self.df_geo = (
            self.df.sort_values('id')
            .drop_duplicates(subset=['lat_r', 'lon_r'])
            .reset_index(drop=True)
        )
        
        dias = self.config.GEE['dias_ventana']
        
        def set_fechas(row, is_inicio=True):
            if pd.isna(row.get('fecha')):
                fb_ini = self.config.OUTPUT['fecha_fallback_inicio']
                fb_fin = self.config.OUTPUT['fecha_fallback_fin']
                return pd.to_datetime(fb_ini if is_inicio else fb_fin)
            return row['fecha'] - pd.Timedelta(days=dias if is_inicio else 1)
        
        self.df_geo['fecha_inicio'] = self.df_geo.apply(lambda r: set_fechas(r, True), axis=1)
        self.df_geo['fecha_fin'] = self.df_geo.apply(lambda r: set_fechas(r, False), axis=1)
        
        print(f"  → {len(self.df_geo)} lotes únicos")
        return self.df_geo
    
    def inicializar_servicios(self):
        print("\n🚀 Inicializando servicios...")
        gee_ok = self.descargador.conectar()
        sam_ok = self.segmentador.cargar()
        
        if not gee_ok or not sam_ok:
            raise RuntimeError("No se pudo inicializar GEE o SAM")
        
        return gee_ok and sam_ok
    
    def procesar_lote(self, row):
        t0 = time.time()
        area_ref = row.get('dano_ha', row.get('superficie', 50))
        if pd.isna(area_ref) or area_ref <= 0:
            area_ref = 50
        
        lat, lon = row['lat'], row['lon']
        fecha_ini = row['fecha_inicio'].strftime('%Y-%m-%d')
        fecha_fin = row['fecha_fin'].strftime('%Y-%m-%d')
        
        ndvi, bbox = self.descargador.descargar_ndvi(lat, lon, fecha_ini, fecha_fin)
        if ndvi is None:
            return self._crear_resultado(row, 'SIN_IMAGEN', 0, 0, time.time() - t0)
        
        resultado = self.segmentador.segmentar(ndvi, area_ref, {'bbox': bbox, 
                                                                   'lat': lat, 
                                                                   'lon': lon})
        
        area_ha = resultado['area_ha']
        score = resultado['score']
        poligono = resultado['poligono']
        status = resultado['status']
        
        if status == 'OK' and poligono is not None:
            area_ha = calcular_area_ha(poligono, lat)
            if area_ha < self.config.POLYGON['min_area_ha']:
                status = 'AREA_MUY_CHICA'
                poligono = None
            elif area_ha > self.config.POLYGON['max_area_ha']:
                status = 'SOBRE_SEGMENTACION'
                poligono = None
        
        error_pct = abs(area_ha - area_ref) / area_ref * 100 if area_ref > 0 else 0
        return self._crear_resultado(row, status, area_ha, score, time.time() - t0,
                                      dano_ha=area_ref, error_pct=error_pct,
                                      vertices=resultado['vertices'],
                                      poligono=poligono)
    
    def _crear_resultado(self, row, status, area_ha, score, segundos, 
                         dano_ha=None, error_pct=0, vertices=0, poligono=None):
        return {
            'id': row['id'],
            'localidad': row.get('localidad', ''),
            'provincia': row.get('provincia', ''),
            'cultivo': row.get('cultivo', ''),
            'fecha': str(row['fecha'].date()) if pd.notna(row.get('fecha')) else '',
            'estado': status,
            'area_ha': round(area_ha, 1),
            'dano_ha': dano_ha or area_ha,
            'error_pct': round(error_pct, 1),
            'sam_score': round(score, 3),
            'vertices': vertices,
            'segundos': round(segundos, 1),
            '_poligono': poligono
        }
    
    def ejecutar(self, output_file='poligonos_produccion.geojson',
                 log_file='log_produccion.csv', resume=True, verbose=True):
        print("\n" + "=" * 60)
        print("   POLIGONIZACIÓN AUTOMÁTICA - LOCAL GPU")
        print(f"   GPU: {torch.cuda.get_device_name(0)}")
        print("=" * 60)
        
        if resume:
            self.features, ids_procesados, self.log_rows = cargar_checkpoint(output_file, log_file)
        else:
            ids_procesados = set()
            self.features = []
            self.log_rows = []
        
        pendientes = self.df_geo[~self.df_geo['id'].isin(ids_procesados)].reset_index(drop=True)
        
        print(f"\n📊 Resumen:")
        print(f"   Procesados: {len(ids_procesados)}")
        print(f"   Pendientes:  {len(pendientes)}")
        
        if len(pendientes) == 0:
            print("\n✅ No hay lotes pendientes.")
            return self.features, self.log_rows
        
        self.stats['total'] = len(pendientes)
        t_inicio = time.time()
        
        print(f"\n🔄 Procesando {len(pendientes)} lotes...")
        barra = tqdm(pendientes.iterrows(), total=len(pendientes),
                     desc="Poligonizando", ncols=80, disable=not verbose)
        
        for idx, row in barra:
            resultado = self.procesar_lote(row)
            self.log_rows.append(resultado)
            
            if resultado['estado'] == 'OK':
                self.stats['ok'] += 1
                poligono = resultado.get('_poligono')
                
                if poligono is not None:
                    feature = {
                        "type": "Feature",
                        "properties": {
                            "id": str(resultado['id']),
                            "localidad": resultado['localidad'],
                            "provincia": resultado['provincia'],
                            "cultivo": resultado['cultivo'],
                            "fecha": resultado['fecha'],
                            "area_ha": resultado['area_ha'],
                            "dano_ha": resultado['dano_ha'],
                            "error_pct": resultado['error_pct'],
                            "sam_score": resultado['sam_score'],
                            "vertices": resultado['vertices'],
                            "timestamp": datetime.now().isoformat()
                        },
                        "geometry": mapping(poligono)
                    }
                    self.features.append(feature)
                
                barra.set_postfix({
                    'ok': self.stats['ok'],
                    'area': f"{resultado['area_ha']}ha",
                    'status': 'OK'
                })
            elif resultado['estado'] == 'FUGA_NO_RESUELTA':
                self.stats['fugas'] += 1
                barra.set_postfix({'ok': self.stats['ok'], 'fugas': self.stats['fugas'], 
                                   'status': 'FUGA'})
            elif resultado['estado'] == 'SIN_IMAGEN':
                self.stats['sin_imagen'] += 1
                barra.set_postfix({'ok': self.stats['ok'], 'sin_img': self.stats['sin_imagen'],
                                   'status': 'SIN_IMG'})
            else:
                self.stats['otros_errores'] += 1
            
            if len(self.features) % self.config.OUTPUT['guardar_cada'] == 0:
                guardar_checkpoint(self.features, self.log_rows, output_file, log_file)
        
        guardar_checkpoint(self.features, self.log_rows, output_file, log_file)
        self.stats['tiempo_total_s'] = round(time.time() - t_inicio, 1)
        
        self._imprimir_resumen()
        
        return self.features, self.log_rows
    
    def _imprimir_resumen(self):
        s = self.stats
        total = s['total']
        ok = s['ok']
        t = s['tiempo_total_s']
        
        print("\n" + "=" * 60)
        print("   📋 RESUMEN")
        print("=" * 60)
        print(f"   Total:          {total}")
        print(f"   Exitosos:      {ok} ({ok/total*100:.1f}%)")
        print(f"   Fugas:         {s['fugas']} ({s['fugas']/total*100:.1f}%)")
        print(f"   Sin imagen:    {s['sin_imagen']} ({s['sin_imagen']/total*100:.1f}%)")
        print(f"   Otros:         {s['otros_errores']} ({s['otros_errores']/total*100:.1f}%)")
        print(f"   Tiempo:        {t:.0f}s ({t/60:.1f} min)")
        if ok > 0:
            print(f"   Promedio:      {t/ok:.1f}s/lote")
        
        df_log = pd.DataFrame(self.log_rows)
        df_ok = df_log[df_log['estado'] == 'OK']
        
        if not df_ok.empty:
            print(f"\n   Calidad (lotes OK):")
            print(f"     SAM score promedio:   {df_ok['sam_score'].mean():.3f}")
            print(f"     Error área promedio:  {df_ok['error_pct'].mean():.1f}%")
            print(f"     Error área mediana:   {df_ok['error_pct'].median():.1f}%")
            print(f"     Error <20%:           {(df_ok['error_pct'] < 20).sum()/len(df_ok)*100:.0f}%")
            print(f"     Error <40%:           {(df_ok['error_pct'] < 40).sum()/len(df_ok)*100:.0f}%")
        
        print("=" * 60)
    
    def generar_reporte(self, output_pdf='reporte_produccion.pdf'):
        try:
            from fpdf import FPDF
            from fpdf.enums import XPos, YPos
            
            df_log = pd.DataFrame(self.log_rows)
            df_ok = df_log[df_log['estado'] == 'OK']
            
            pdf = FPDF()
            pdf.add_page()
            pdf.set_auto_page_break(auto=True, margin=15)
            
            pdf.set_font('helvetica', 'B', 16)
            pdf.set_text_color(41, 128, 185)
            pdf.cell(0, 10, 'Reporte de Poligonizacion Automatica', 
                    new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            pdf.ln(5)
            
            pdf.set_font('helvetica', '', 11)
            pdf.set_text_color(0, 0, 0)
            
            resumen = (
                f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                f"Total: {self.stats['total']}\n"
                f"Exitosos: {self.stats['ok']}\n"
            )
            pdf.multi_cell(0, 6, resumen)
            
            if not df_ok.empty:
                pdf.ln(5)
                pdf.set_font('helvetica', 'B', 12)
                pdf.cell(0, 8, 'Metricas de Calidad:', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_font('helvetica', '', 11)
                metricas = (
                    f"SAM Score promedio: {df_ok['sam_score'].mean():.3f}\n"
                    f"Error area promedio: {df_ok['error_pct'].mean():.1f}%\n"
                    f"Error < 20%: {(df_ok['error_pct'] < 20).sum()/len(df_ok)*100:.0f}%\n"
                )
                pdf.multi_cell(0, 6, metricas)
            
            pdf.output(output_pdf)
            print(f"\n📄 PDF: {output_pdf}")
        
        except ImportError:
            print("Instalar fpdf2: pip install fpdf2")
    
    def exportar_csv(self, output_csv='resultados_lotes.csv'):
        df_log = pd.DataFrame(self.log_rows)
        df_log.to_csv(output_csv, index=False, encoding='utf-8')
        print(f"📊 CSV: {output_csv}")
    
    def exportar_mapa_html(self, output_html='mapa_resultados.html'):
        try:
            import folium
            import geopandas as gpd
            
            if not self.features:
                print("⚠ No hay polígonos")
                return
            
            gdf = gpd.GeoDataFrame.from_features(self.features)
            gdf.set_crs("EPSG:4326", inplace=True)
            
            centro = [gdf.geometry.centroid.y.mean(), gdf.geometry.centroid.x.mean()]
            mapa = folium.Map(location=centro, zoom_start=7, tiles='CartoDB positron')
            
            for _, row in gdf.iterrows():
                color = 'green' if row.get('error_pct', 0) < 20 else 'orange' if row.get('error_pct', 0) < 50 else 'red'
                folium.GeoJson(
                    row.geometry.__geo_interface__,
                    style_function=lambda f, c=color: {
                        'fillColor': c, 'color': c, 'fillOpacity': 0.3, 'weight': 2
                    },
                    popup=folium.Popup(
                        f"<b>ID:</b> {row.get('id','')}<br>"
                        f"<b>Localidad:</b> {row.get('localidad','')}<br>"
                        f"<b>Area:</b> {row.get('area_ha','')} ha<br>"
                        f"<b>Score:</b> {row.get('sam_score','')}<br>"
                        f"<b>Error:</b> {row.get('error_pct','')}%",
                        max_width=250
                    )
                ).add_to(mapa)
            
            mapa.save(output_html)
            print(f"🗺️ Mapa: {output_html}")
        
        except ImportError:
            print("Instalar: pip install folium geopandas")


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Poligonizacion Automatica - LOCAL GPU',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python poligonizador_local.py --csv datos.csv
  python poligonizador_local.py --csv datos.csv --resume
  python poligonizador_local.py --csv datos.csv --reporte --mapa
  python poligonizador_local.py --test
        """
    )
    
    parser.add_argument('--csv', default='siniestros.csv',
                        help='CSV de entrada')
    parser.add_argument('--output', default='poligonos_produccion.geojson',
                        help='GeoJSON de salida')
    parser.add_argument('--log', default='log_produccion.csv',
                        help='Archivo de log')
    parser.add_argument('--resume', action='store_true',
                        help='Reanudar procesamiento')
    parser.add_argument('--no-resume', action='store_true',
                        help='Forzar desde cero')
    parser.add_argument('--reporte', action='store_true',
                        help='Generar PDF')
    parser.add_argument('--mapa', action='store_true',
                        help='Generar mapa HTML')
    parser.add_argument('--min-area', type=float,
                        help='Area minima (ha)')
    parser.add_argument('--max-area', type=float,
                        help='Area maxima (ha)')
    parser.add_argument('--verbose', action='store_true', default=True)
    parser.add_argument('--quiet', action='store_true')
    parser.add_argument('--test', action='store_true',
                        help='Testear entorno')
    
    args = parser.parse_args()
    
    if args.quiet:
        args.verbose = False
    
    if args.test:
        print("\n🧪 Test de entorno...")
        print(f"   PyTorch: {torch.__version__}")
        print(f"   CUDA available: {torch.cuda.is_available()}")
        print(f"   GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A'}")
        
        d = DescargadorSatelital()
        print(f"   GEE: {'OK' if d.conectar() else 'FALLO'}")
        
        s = SegmentadorSAM()
        print(f"   SAM checkpoint: {'OK' if os.path.exists(Config.SAM['checkpoint']) else 'FALTA'}")
        
        print("\n✅ Test completo.")
        return
    
    if args.min_area:
        Config.POLYGON['min_area_ha'] = args.min_area
    if args.max_area:
        Config.POLYGON['max_area_ha'] = args.max_area
    
    resume = args.resume and not args.no_resume
    
    print("\n⚙️  Config:")
    print(f"   CSV:   {args.csv}")
    print(f"   Min/Max: {Config.POLYGON['min_area_ha']}-{Config.POLYGON['max_area_ha']} ha")
    
    try:
        pipeline = PipelinePoligonizacion()
        pipeline.cargar_datos(args.csv)
        pipeline.inicializar_servicios()
        
        features, log_rows = pipeline.ejecutar(
            output_file=args.output,
            log_file=args.log,
            resume=resume,
            verbose=args.verbose
        )
        
        if args.reporte:
            pipeline.generar_reporte()
        if args.mapa:
            pipeline.exportar_mapa_html()
        
        pipeline.exportar_csv(args.log.replace('.csv', '_resultados.csv'))
        
        print(f"\n✅ Listo. Poligonos: {len(features)}")
    
    except FileNotFoundError as e:
        print(f"\n❌ {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n⚠️  Interrumpido. Progreso guardado.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()