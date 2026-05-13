"""
=============================================================================
POLIGONIZACIÓN AUTOMÁTICA DE LOTES AGRÍCOLAS
Pipeline Productivo v1.0

Autor: Proyecto Poligonización Argentina
Tecnología: Google Earth Engine + Segment Anything Model (SAM)
Uso: Aseguradoras agrícolas, agrotech, consultoras

MODO DE USO:
    python poligonizador.py                    # Ejecuta con config por defecto
    python poligonizador.py --help            # Muestra ayuda completa
    python poligonizador.py --csv datos.csv    # Especifica archivo de entrada
    python poligonizador.py --resume           # Retoma procesamiento anterior

REQUISITOS:
    pip install segment-anything earthengine-api geopandas shapely opencv-python-headless pandas numpy matplotlib tqdm scipy

MODELO SAM REQUERIDO:
    Descargar sam_vit_b_01ec64.pth desde:
    https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth

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

import numpy as np
import pandas as pd
import matplotlib
import matplotlib.cm as cm
import cv2

from pathlib import Path
from tqdm import tqdm

try:
    import ee
    from shapely.geometry import Polygon, mapping, shape
    from segment_anything import sam_model_registry, SamPredictor
except ImportError as e:
    print(f"❌ Falta libreria: {e}")
    print("   Ejecutar: pip install segment-anything earthengine-api geopandas shapely opencv-python-headless pandas numpy matplotlib tqdm scipy")
    sys.exit(1)

warnings.filterwarnings('ignore')
matplotlib.use('Agg')
cmap = matplotlib.colormaps['RdYlGn']


# ============================================================================
# CONFIGURACIÓN GLOBAL
# ============================================================================

class Config:
    """Configuración centralizada del pipeline."""

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
        'dano_ha': ['has__daña', 'dano_ha', 'dano_estimado', 'area_ha', 'sup_afectada_ha', 'superficie'],
        'cultivo': ['cultivo', 'crop', 'cultivoafectado'],
        'localidad': ['localidad', 'location', 'loc', 'ciudad'],
        'provincia': ['provincia', 'prov', 'estado'],
    }

    @classmethod
    def update(cls, **kwargs):
        for key, value in kwargs.items():
            if hasattr(cls, key.upper()):
                section = getattr(cls, key.upper())
                if isinstance(section, dict):
                    section.update(value)
                else:
                    setattr(cls, key.upper(), value)
            elif hasattr(cls, key):
                setattr(cls, key, value)


# ============================================================================
# UTILIDADES
# ============================================================================

def parse_decimal(val):
    """Convierte valores numéricos con decimales europeos (coma) o irregulares."""
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
    """Busca una columna que coincida con alguno de los patrones dados."""
    for p in patrones:
        for col in columnas:
            if col is not None and str(col).strip() and col != '':
                if p in str(col).strip().lower():
                    return col
    return None


def pixel_a_geo(col, row, lon_min, lon_max, lat_min, lat_max, w, h):
    """Convierte coordenadas de píxel a geo (lon, lat)."""
    lon = lon_min + (col / w) * (lon_max - lon_min)
    lat = lat_max - (row / h) * (lat_max - lat_min)
    return (lon, lat)


def calcular_area_ha(poligono, lat_centro):
    """Calcula área en hectáreas desde un polígono Shapely."""
    factor = math.cos(math.radians(lat_centro))
    return poligono.area * (111320 ** 2) * factor / 10000


def ndvi_a_rgb(ndvi):
    """Convierte array NDVI a imagen RGB usando colormap RdYlGn."""
    ndvi_clip = np.clip(ndvi, -0.2, 0.8)
    ndvi_norm = ((ndvi_clip + 0.2) * 255).astype(np.uint8)
    return (cmap(ndvi_norm / 255.0)[:, :, :3] * 255).astype(np.uint8)


def guardar_checkpoint(features, log_rows, output_file, log_file):
    """Guarda estado del procesamiento de forma incremental."""
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, indent=2)
    pd.DataFrame(log_rows).to_csv(log_file, index=False, encoding='utf-8')


def cargar_checkpoint(output_file, log_file):
    """Carga estado previo si existe. Devuelve (features, ids_procesados, log_rows)."""
    features = []
    ids_procesados = set()
    log_rows = []

    if os.path.exists(output_file):
        with open(output_file, 'r', encoding='utf-8') as f:
            gj = json.load(f)
        features = gj.get('features', [])
        ids_procesados = {ft['properties'].get('id') for ft in features if ft['properties'].get('id') is not None}
        print(f"  → GeoJSON previo: {len(features)} polígonos cargados")

    if os.path.exists(log_file):
        df_log = pd.read_csv(log_file)
        log_rows = df_log.to_dict('records')
        ids_log = {r['id'] for r in log_rows if r.get('id') is not None}
        ids_procesados |= ids_log
        print(f"  → Log previo: {len(log_rows)} entradas cargadas")

    return features, ids_procesados, log_rows


def bounding_box_dinamico(area_ref, shape_hw):
    """Calcula margen del bounding box según área esperada del lote."""
    lado_m = math.sqrt(max(area_ref, 1) * 10000)
    margen = int((lado_m / 2) / 10)
    margen = max(Config.POLYGON['margen_px_min'],
                min(margen, Config.POLYGON['margen_px_max']))
    h, w = shape_hw
    cx, cy = w // 2, h // 2
    x_min = max(0, cx - margen)
    y_min = max(0, cy - margen)
    x_max = min(w - 1, cx + margen)
    y_max = min(h - 1, cy + margen)
    return np.array([x_min, y_min, x_max, y_max])


# ============================================================================
# MÓDULO DE DESCARGA SATELITAL (GEE)
# ============================================================================

class DescargadorSatelital:
    """Maneja la conexión y descarga de imágenes de Google Earth Engine."""

    def __init__(self, config=None):
        self.config = config or Config.GEE
        self.conectado = False

    def conectar(self):
        """Inicializa conexión con GEE. Autentica si es necesario."""
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
        """
        Descarga imagen Sentinel-2 y calcula NDVI.

        Args:
            lat: Latitud del punto
            lon: Longitud del punto
            fecha_inicio: Fecha inicio de búsqueda (str 'YYYY-MM-DD')
            fecha_fin: Fecha fin de búsqueda (str 'YYYY-MM-DD')

        Returns:
            (ndvi_array, bbox_ee) o (None, None) si falla
        """
        try:
            centro = ee.Geometry.Point([lon, lat])
            bbox = centro.buffer(self.config['buffer_m']).bounds()

            coleccion = (
                ee.ImageCollection('COPERNICUS/S2_HARMONIZED')
                .filterBounds(bbox)
                .filterDate(fecha_inicio, fecha_fin)
                .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', self.config['max_nubes_pct']))
                .sort('CLOUDY_PIXEL_PERCENTAGE')
            )

            if coleccion.size().getInfo() == 0:
                return None, None

            img = coleccion.first()
            datos = img.select(['B4', 'B8']).sampleRectangle(region=bbox, defaultValue=0)

            rojo = np.array(datos.get('B4').getInfo(), dtype=np.float32)
            nir = np.array(datos.get('B8').getInfo(), dtype=np.float32)
            ndvi = (nir - rojo) / (nir + rojo + 1e-6)

            return ndvi, bbox

        except Exception as e:
            print(f"    ⚠ Error descargando imagen: {e}")
            return None, None

    def obtener_info_imagen(self, lat, lon, fecha_inicio, fecha_fin):
        """Obtiene metadata de la mejor imagen disponible."""
        try:
            centro = ee.Geometry.Point([lon, lat])
            bbox = centro.buffer(self.config['buffer_m']).bounds()

            coleccion = (
                ee.ImageCollection('COPERNICUS/S2_HARMONIZED')
                .filterBounds(bbox)
                .filterDate(fecha_inicio, fecha_fin)
                .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', self.config['max_nubes_pct']))
                .sort('CLOUDY_PIXEL_PERCENTAGE')
            )

            if coleccion.size().getInfo() == 0:
                return None

            img = coleccion.first()
            fecha = img.date().format('YYYY-MM-dd').getInfo()
            nubes = img.get('CLOUDY_PIXEL_PERCENTAGE').getInfo()
            return {'fecha': fecha, 'nubes': nubes}

        except:
            return None


# ============================================================================
# MÓDULO SAM (Segment Anything Model)
# ============================================================================

class SegmentadorSAM:
    """Maneja la carga e inferencia del modelo SAM."""

    def __init__(self, config=None):
        self.config = config or Config.SAM
        self.predictor = None
        self.modelo = None

    def cargar(self):
        """Carga el modelo SAM y lo mueve a GPU si está disponible."""
        if self.predictor is not None:
            return True

        checkpoint = self.config['checkpoint']

        if not os.path.exists(checkpoint):
            print(f"  📥 Descargando modelo SAM...")
            os.system(f'wget -q https://dl.fbaipublicfiles.com/segment_anything/{checkpoint}')

        if not os.path.exists(checkpoint):
            print(f"  ❌ No se encontró {checkpoint}")
            print("  Descargar manualmente desde: https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth")
            return False

        try:
            import torch
            device = self.config['device']
            if device == 'cuda' and not torch.cuda.is_available():
                device = 'cpu'

            sam = sam_model_registry[self.config['model_type']](checkpoint=checkpoint)
            sam.to(device)
            self.predictor = SamPredictor(sam)
            self.modelo = sam
            print(f"  ✅ SAM cargado en {device.upper()}")
            return True

        except Exception as e:
            print(f"  ❌ Error cargando SAM: {e}")
            return False

    def segmentar(self, ndvi, area_ref, coordenadas=None):
        """
        Segmenta un lote agrícola usando SAM.

        Args:
            ndvi: Array numpy con imagen NDVI
            area_ref: Área de referencia en hectáreas (para bbox dinámico)
            coordenadas: Dict opcional con lat, lon para georreferenciación

        Returns:
            Dict con {'mask', 'poligono', 'area_ha', 'score', 'status'}
        """
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
                    'area_ha': round(area_sam, 1), 'score': round(score, 3),
                    'vertices': 0
                }

        contornos, _ = cv2.findContours(
            (mask * 255).astype(np.uint8),
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )

        if not contornos:
            return {'status': 'SIN_CONTORNO', 'mask': mask, 'poligono': None,
                    'area_ha': round(area_sam, 1), 'score': round(score, 3), 'vertices': 0}

        contorno = max(contornos, key=cv2.contourArea)
        epsilon = Config.POLYGON['factor_suavizado'] * cv2.arcLength(contorno, True)
        contorno_simple = cv2.approxPolyDP(contorno, epsilon, True)
        puntos = contorno_simple.squeeze()

        if puntos.ndim == 1:
            puntos = puntos.reshape(1, -1)
        if len(puntos) < 3:
            return {'status': 'CONTORNO_INVALIDO', 'mask': mask, 'poligono': None,
                    'area_ha': round(area_sam, 1), 'score': round(score, 3), 'vertices': 0}

        if coordenadas:
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
    """Pipeline completo de poligonización automática."""

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
        """Carga y limpia datos del CSV de entrada."""
        path = csv_path or self.csv_path
        if not path or not os.path.exists(path):
            raise FileNotFoundError(f"No se encontró el archivo: {path}")

        print(f"\n📂 Cargando datos: {path}")

        df_raw = pd.read_csv(path)
        print(f"  → {len(df_raw)} registros leídos")

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
        fecha_fallback_ini = self.config.OUTPUT['fecha_fallback_inicio']
        fecha_fallback_fin = self.config.OUTPUT['fecha_fallback_fin']

        def set_fechas(row, is_inicio=True):
            if pd.isna(row.get('fecha')):
                return pd.to_datetime(fecha_fallback_ini if is_inicio else fecha_fallback_fin)
            return row['fecha'] - pd.Timedelta(days=dias if is_inicio else 1)

        self.df_geo['fecha_inicio'] = self.df_geo.apply(lambda r: set_fechas(r, True), axis=1)
        self.df_geo['fecha_fin'] = self.df_geo.apply(lambda r: set_fechas(r, False), axis=1)

        print(f"  → {len(self.df_geo)} lotes únicos a procesar")

        return self.df_geo

    def inicializar_servicios(self):
        """Inicializa GEE y SAM."""
        print("\n🚀 Inicializando servicios...")
        gee_ok = self.descargador.conectar()
        sam_ok = self.segmentador.cargar()

        if not gee_ok or not sam_ok:
            raise RuntimeError("No se pudo inicializar GEE o SAM")

        return gee_ok and sam_ok

    def procesar_lote(self, row):
        """Procesa un solo lote y devuelve resultado."""
        t0 = time.time()
        area_ref = row.get('dano_ha', row.get('superficie', 50))
        if pd.isna(area_ref) or area_ref <= 0:
            area_ref = 50

        lat, lon = row['lat'], row['lon']
        fecha_ini = row['fecha_inicio'].strftime('%Y-%m-%d')
        fecha_fin = row['fecha_fin'].strftime('%Y-%m-%d')

        ndvi, bbox = self.descargador.descargar_ndvi(lat, lon, fecha_ini, fecha_fin)
        if ndvi is None:
            return {
                'id': row['id'],
                'localidad': row.get('localidad', ''),
                'provincia': row.get('provincia', ''),
                'cultivo': row.get('cultivo', ''),
                'fecha': str(row['fecha'].date()) if pd.notna(row.get('fecha')) else '',
                'estado': 'SIN_IMAGEN',
                'area_ha': 0,
                'error_pct': 0,
                'sam_score': 0,
                'segundos': round(time.time() - t0, 1)
            }

        resultado = self.segmentador.segmentar(ndvi, area_ref, {'bbox': bbox, 'lat': lat, 'lon': lon})

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

        return {
            'id': row['id'],
            'localidad': row.get('localidad', ''),
            'provincia': row.get('provincia', ''),
            'cultivo': row.get('cultivo', ''),
            'fecha': str(row['fecha'].date()) if pd.notna(row.get('fecha')) else '',
            'estado': status,
            'area_ha': round(area_ha, 1),
            'dano_ha': area_ref,
            'error_pct': round(error_pct, 1),
            'sam_score': score,
            'vertices': resultado['vertices'],
            'segundos': round(time.time() - t0, 1)
        }

    def ejecutar(self, output_file='poligonos_produccion.geojson',
                 log_file='log_produccion.csv', resume=True, verbose=True):
        """Ejecuta el pipeline completo de producción."""

        print("\n" + "=" * 60)
        print("   POLIGONIZACIÓN AUTOMÁTICA - MODO PRODUCCIÓN")
        print("=" * 60)

        if resume:
            self.features, ids_procesados, self.log_rows = cargar_checkpoint(output_file, log_file)
        else:
            ids_procesados = set()
            self.features = []
            self.log_rows = []

        pendientes = self.df_geo[~self.df_geo['id'].isin(ids_procesados)].reset_index(drop=True)

        print(f"\n📊 Resumen:")
        print(f"   Ya procesados: {len(ids_procesados)}")
        print(f"   Pendientes:   {len(pendientes)}")
        print(f"   Guardado en:  {output_file}, {log_file}")

        if len(pendientes) == 0:
            print("\n✅ No hay lotes pendientes. Nada que hacer.")
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

                coords = row.get('coords')
                if coords is None:
                    coords_bbox = None
                else:
                    coords_bbox = coords

                poligono = None
                if 'poligono' in resultado and resultado['poligono'] is not None:
                    poligono = resultado['poligono']
                elif resultado.get('_poligono'):
                    poligono = resultado['_poligono']

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

                desc = f"{resultado['localidad'] or resultado['id']}"
                barra.set_postfix({
                    'ok': self.stats['ok'],
                    'area': f"{resultado['area_ha']}ha",
                    'status': resultado['estado']
                })
            elif resultado['estado'] == 'FUGA_NO_RESUELTA':
                self.stats['fugas'] += 1
                barra.set_postfix({
                    'ok': self.stats['ok'],
                    'fugas': self.stats['fugas'],
                    'status': 'FUGA'
                })
            elif resultado['estado'] == 'SIN_IMAGEN':
                self.stats['sin_imagen'] += 1
                barra.set_postfix({
                    'ok': self.stats['ok'],
                    'sin_img': self.stats['sin_imagen'],
                    'status': 'SIN_IMG'
                })
            else:
                self.stats['otros_errores'] += 1

            if len(self.features) % self.config.OUTPUT['guardar_cada'] == 0 or idx == len(pendientes) - 1:
                guardar_checkpoint(self.features, self.log_rows, output_file, log_file)
                barra.set_description(f"Guardado cada {self.config.OUTPUT['guardar_cada']}")

        guardar_checkpoint(self.features, self.log_rows, output_file, log_file)
        self.stats['tiempo_total_s'] = round(time.time() - t_inicio, 1)

        self._imprimir_resumen()

        return self.features, self.log_rows

    def _imprimir_resumen(self):
        """Imprime resumen final del procesamiento."""
        s = self.stats
        total = s['total']
        ok = s['ok']
        t = s['tiempo_total_s']

        print("\n" + "=" * 60)
        print("   📋 RESUMEN DE PROCESAMIENTO")
        print("=" * 60)
        print(f"   Total procesados:      {total}")
        print(f"   Exitosos (OK):        {ok} ({ok/total*100:.1f}%)")
        print(f"   Fugas no resueltas:   {s['fugas']} ({s['fugas']/total*100:.1f}%)")
        print(f"   Sin imagen limpia:    {s['sin_imagen']} ({s['sin_imagen']/total*100:.1f}%)")
        print(f"   Otros errores:        {s['otros_errores']} ({s['otros_errores']/total*100:.1f}%)")
        print(f"   Tiempo total:         {t:.0f}s ({t/60:.1f} min)")
        if ok > 0:
            print(f"   Tiempo promedio:      {t/ok:.1f}s/lote")

        df_log = pd.DataFrame(self.log_rows)
        df_ok = df_log[df_log['estado'] == 'OK']

        if not df_ok.empty:
            print(f"\n   Métricas de calidad (lotes OK):")
            print(f"     SAM score promedio:  {df_ok['sam_score'].mean():.3f}")
            print(f"     Error area promedio: {df_ok['error_pct'].mean():.1f}%")
            print(f"     Error area mediana:   {df_ok['error_pct'].median():.1f}%")

            pct_ok_20 = (df_ok['error_pct'] < 20).sum() / len(df_ok) * 100
            print(f"     Lotes con error <20%: {pct_ok_20:.0f}%")
            print(f"     Lotes con error <40%: {(df_ok['error_pct'] < 40).sum() / len(df_ok) * 100:.0f}%")

        print("=" * 60)

    def generar_reporte(self, output_pdf='reporte_produccion.pdf'):
        """Genera un PDF con el resumen del procesamiento."""
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
            pdf.cell(0, 10, 'Reporte de Poligonizacion Automatica', new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            pdf.ln(5)

            pdf.set_font('helvetica', '', 11)
            pdf.set_text_color(0, 0, 0)

            resumen = (
                f"Fecha de ejecucion: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                f"Total de lotes procesados: {self.stats['total']}\n"
                f"Exitosos: {self.stats['ok']} ({self.stats['ok']/max(self.stats['total'],1)*100:.1f}%)\n"
                f"Sin imagen: {self.stats['sin_imagen']}\n"
                f"Fugas: {self.stats['fugas']}\n"
            )
            pdf.multi_cell(0, 6, resumen, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

            if not df_ok.empty:
                pdf.ln(5)
                pdf.set_font('helvetica', 'B', 12)
                pdf.cell(0, 8, 'Metricas de Calidad (lotes OK):', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_font('helvetica', '', 11)
                metricas = (
                    f"SAM Score promedio: {df_ok['sam_score'].mean():.3f}\n"
                    f"Error de area promedio: {df_ok['error_pct'].mean():.1f}%\n"
                    f"Lotes con error < 20%: {(df_ok['error_pct'] < 20).sum() / len(df_ok) * 100:.0f}%\n"
                    f"Tiempo promedio: {self.stats['tiempo_total_s']/self.stats['ok']:.1f}s/lote"
                )
                pdf.multi_cell(0, 6, metricas, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

            pdf.output(output_pdf)
            print(f"\n📄 Reporte PDF generado: {output_pdf}")

        except ImportError:
            print("Para generar PDF instalar: pip install fpdf2")

    def exportar_csv(self, output_csv='resultados_lotes.csv'):
        """Exporta el log a CSV para análisis en Excel."""
        df_log = pd.DataFrame(self.log_rows)
        df_log.to_csv(output_csv, index=False, encoding='utf-8')
        print(f"📊 CSV exportado: {output_csv}")

    def exportar_mapa_html(self, output_html='mapa_resultados.html'):
        """Genera mapa interactivo Folium con los polígonos."""
        try:
            import folium

            if not self.features:
                print("⚠ No hay polígonos para mostrar en el mapa")
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
                        f"<b>Área:</b> {row.get('area_ha','')} ha<br>"
                        f"<b>Score:</b> {row.get('sam_score','')}<br>"
                        f"<b>Error:</b> {row.get('error_pct','')}%",
                        max_width=250
                    )
                ).add_to(mapa)

            mapa.save(output_html)
            print(f"🗺️ Mapa HTML generado: {output_html}")

        except ImportError:
            print("Para generar mapa instalar: pip install folium geopandas")


# ============================================================================
# INTERFAZ DE LÍNEA DE COMANDO
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Poligonización Automática de Lotes Agrícolas con GEE + SAM',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python poligonizador.py                                    # CSV por defecto
  python poligonizador.py --csv mis_datos.csv               # CSV específico
  python poligonizador.py --csv datos.csv --resume          # Resume procesamiento
  python poligonizador.py --csv datos.csv --output mis_poligonos.geojson
  python poligonizador.py --csv datos.csv --reporte         # Genera PDF
  python poligonizador.py --csv datos.csv --min-area 10 --max-area 500

Para verificar conexión GEE:
  python poligonizador.py --test-gee
        """
    )

    parser.add_argument('--csv', default='siniestros.csv',
                        help='Ruta al archivo CSV de entrada')
    parser.add_argument('--output', default='poligonos_produccion.geojson',
                        help='Archivo GeoJSON de salida')
    parser.add_argument('--log', default='log_produccion.csv',
                        help='Archivo de log CSV')
    parser.add_argument('--resume', action='store_true',
                        help='Reanudar procesamiento anterior')
    parser.add_argument('--no-resume', action='store_true',
                        help='Forzar procesamiento desde cero')
    parser.add_argument('--reporte', action='store_true',
                        help='Generar reporte PDF al finalizar')
    parser.add_argument('--mapa', action='store_true',
                        help='Generar mapa HTML interactivo')
    parser.add_argument('--min-area', type=float,
                        help='Área mínima en hectáreas')
    parser.add_argument('--max-area', type=float,
                        help='Área máxima en hectáreas')
    parser.add_argument('--buffer', type=int,
                        help='Buffer en metros para descarga de imagen')
    parser.add_argument('--max-nubes', type=int,
                        help='Porcentaje máximo de nubes')
    parser.add_argument('--verbose', action='store_true', default=True,
                        help='Mostrar progreso detallado')
    parser.add_argument('--quiet', action='store_true',
                        help='Minimizar salida')
    parser.add_argument('--test-gee', action='store_true',
                        help='Probar conexión a GEE y salir')
    parser.add_argument('--version', action='store_true',
                        help='Mostrar versión')

    args = parser.parse_args()

    if args.version:
        print("Poligonizador v1.0 - GEE + SAM Pipeline")
        return

    if args.quiet:
        args.verbose = False

    if args.test_gee:
        print("Probando conexión a GEE...")
        d = DescargadorSatelital()
        if d.conectar():
            print("✅ GEE OK")
            try:
                info = d.obtener_info_imagen(-34.5, -58.5, '2026-01-01', '2026-03-31')
                if info:
                    print(f"   Imagen disponible: {info['fecha']}, {info['nubes']:.1f}% nubes")
            except:
                pass
        else:
            print("❌ GEE no disponible")
        return

    if args.min_area:
        Config.POLYGON['min_area_ha'] = args.min_area
    if args.max_area:
        Config.POLYGON['max_area_ha'] = args.max_area
    if args.buffer:
        Config.GEE['buffer_m'] = args.buffer
    if args.max_nubes:
        Config.GEE['max_nubes_pct'] = args.max_nubes

    resume = args.resume and not args.no_resume

    print("\n⚙️  Configuración:")
    print(f"   CSV entrada:   {args.csv}")
    print(f"   GeoJSON salida: {args.output}")
    print(f"   Min/Max área:  {Config.POLYGON['min_area_ha']} - {Config.POLYGON['max_area_ha']} ha")
    print(f"   Buffer GEE:   {Config.GEE['buffer_m']}m")
    print(f"   Nubes máx:    {Config.GEE['max_nubes_pct']}%")

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

        print(f"\n✅ Pipeline finalizado.")
        print(f"   Polígonos válidos: {len(features)}")
        print(f"   Archivos: {args.output}, {args.log}")

    except FileNotFoundError as e:
        print(f"\n❌ {e}")
        print("   Asegúrate de que el archivo CSV exista o especifica la ruta con --csv")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n⚠️  Ejecución interrumpida por usuario.")
        print(f"   Progreso guardado en {args.output}")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error inesperado: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()