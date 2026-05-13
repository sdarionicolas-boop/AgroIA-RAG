"""
=============================================================================
AgroIA - MÓDULO DE POLIGONIZACIÓN AUTOMÁTICA
Pipeline Satelital: Google Earth Engine + Segment Anything Model (SAM)

Versión: 2.1 (Integración src/)
Autor: AgroIA Team
Estado: Validado en TAYPE (85.6% hit rate), INTA (74.9% hit rate)
=============================================================================
"""

import os
import sys
import json
import time
import math
import warnings
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import cv2

try:
    import ee
    import geopandas as gpd
    from shapely.geometry import Polygon, mapping
    from segment_anything import sam_model_registry, SamPredictor
    import torch
except ImportError as e:
    raise ImportError(
        f"Falta librería: {e}\n"
        "Instala: pip install segment-anything earthengine-api geopandas shapely opencv-python-headless torch pandas numpy"
    )

warnings.filterwarnings('ignore')
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

class PoligonizadorConfig:
    """Configuración centralizada del pipeline de poligonización."""

    GEE = {
        'project': 'applied-oxygen-459415-e2',
        'buffer_m': 2500,
        'max_nubes_pct': 20,
        'dias_ventana': 60,
    }

    SAM = {
        'checkpoint': 'sam_vit_b_01ec64.pth',
        'model_type': 'vit_b',
        'device': 'cuda' if torch.cuda.is_available() else 'cpu',
    }

    POLYGON = {
        'min_area_ha': 5,
        'max_area_ha': 800,
        'tolerancia_fuga': 1.5,
        'margen_px_min': 20,
        'margen_px_max': 80,
        'factor_suavizado': 0.005,
    }

    COLUMN_MAPPING = {
        'id': ['id', 'taype', 'numero', 'nro', 'lote_id'],
        'lat': ['lat_dec', 'latitude', 'lat', 'cg_latitud', 'y', 'latitud'],
        'lon': ['lon_dec', 'longitude', 'lon', 'cg_longitud', 'x', 'longitud'],
        'fecha': ['fecha_de_s', 'fecha', 'date', 'fecha_siniestro', 'fecha_evento'],
        'cultivo': ['cultivo', 'crop', 'cultivoafectado', 'especie'],
        'localidad': ['localidad', 'location', 'loc', 'ciudad', 'departamento'],
    }


# =============================================================================
# UTILIDADES
# =============================================================================

def encontrar_columna(patrones: List[str], columnas: List[str]) -> Optional[str]:
    """Mapea columna del CSV a nombre esperado."""
    for p in patrones:
        for col in columnas:
            if col and str(col).strip().lower().find(p) != -1:
                return col
    return None


def bounding_box_dinamico(area_ref: float, shape_hw: Tuple[int, int]) -> np.ndarray:
    """Calcula zoom óptimo para SAM basado en tamaño esperado del lote."""
    lado_m = math.sqrt(max(area_ref, 1) * 10000)
    margen = int((lado_m / 2) / 10)  # ~10m/px en Sentinel-2
    margen = max(
        PoligonizadorConfig.POLYGON['margen_px_min'],
        min(margen, PoligonizadorConfig.POLYGON['margen_px_max'])
    )
    h, w = shape_hw
    cx, cy = w // 2, h // 2
    return np.array([
        max(0, cx - margen),
        max(0, cy - margen),
        min(w - 1, cx + margen),
        min(h - 1, cy + margen)
    ])


def pixel_a_geo(col: int, row: int, lon_min: float, lon_max: float,
                lat_min: float, lat_max: float, w: int, h: int) -> Tuple[float, float]:
    """Convierte píxel a coordenadas geográficas."""
    lon = lon_min + (col / w) * (lon_max - lon_min)
    lat = lat_max - (row / h) * (lat_max - lat_min)
    return (lon, lat)


def calcular_area_ha(poligono: Polygon, lat_centro: float) -> float:
    """Calcula área geodésica en hectáreas."""
    factor = math.cos(math.radians(lat_centro))
    return poligono.area * (111320 ** 2) * factor / 10000


def ndvi_a_rgb(ndvi: np.ndarray) -> np.ndarray:
    """Normaliza NDVI para entrada a SAM."""
    import matplotlib.pyplot as plt
    import matplotlib.cm as cm
    cmap = cm.get_cmap('RdYlGn')

    ndvi_clip = np.clip(ndvi, -0.2, 0.8)
    ndvi_norm = ((ndvi_clip + 0.2) * 255).astype(np.uint8)
    return (cmap(ndvi_norm / 255.0)[:, :, :3] * 255).astype(np.uint8)


# =============================================================================
# MOTORES: GEE + SAM
# =============================================================================

class EngineSatelital:
    """Maneja conexión y descarga de Google Earth Engine."""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or PoligonizadorConfig.GEE
        self.conectado = False

    def conectar(self) -> bool:
        """Conecta con GEE (requiere autenticación previa)."""
        if self.conectado:
            return True
        try:
            ee.Initialize(project=self.config['project'])
            self.conectado = True
            logger.info(f"✅ GEE conectado a proyecto: {self.config['project']}")
            return True
        except Exception as e:
            try:
                logger.info("Intentando autenticación GEE...")
                ee.Authenticate()
                ee.Initialize(project=self.config['project'])
                self.conectado = True
                logger.info("✅ GEE autenticado e inicializado")
                return True
            except Exception as auth_err:
                logger.error(f"❌ Error GEE: {auth_err}")
                return False

    def descargar_ndvi(self, lat: float, lon: float, f_ini: str, f_fin: str) -> Tuple[Optional[np.ndarray], Optional[object]]:
        """Descarga la mejor escena Sentinel-2 disponible."""
        if not self.conectado:
            logger.warning(f"GEE no conectado para ({lat}, {lon})")
            return None, None

        try:
            centro = ee.Geometry.Point([lon, lat])
            bbox = centro.buffer(self.config['buffer_m']).bounds()

            coleccion = (ee.ImageCollection('COPERNICUS/S2_HARMONIZED')
                        .filterBounds(bbox)
                        .filterDate(f_ini, f_fin)
                        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', self.config['max_nubes_pct']))
                        .sort('CLOUDY_PIXEL_PERCENTAGE'))

            if coleccion.size().getInfo() == 0:
                return None, None

            img = coleccion.first()
            datos = img.select(['B4', 'B8']).sampleRectangle(region=bbox, defaultValue=0)

            rojo = np.array(datos.get('B4').getInfo(), dtype=np.float32)
            nir = np.array(datos.get('B8').getInfo(), dtype=np.float32)
            ndvi = (nir - rojo) / (nir + rojo + 1e-6)

            return ndvi, bbox
        except Exception as e:
            logger.warning(f"Error descarga NDVI ({lat:.2f}, {lon:.2f}): {str(e)[:50]}")
            return None, None


class SegmentadorSAM:
    """Maneja inferencia de Segment Anything Model."""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or PoligonizadorConfig.SAM
        self.predictor = None

    def cargar(self) -> bool:
        """Carga el modelo SAM (descarga si no existe)."""
        if self.predictor:
            return True

        cp = self.config['checkpoint']
        if not os.path.exists(cp):
            logger.info(f"Descargando SAM ({self.config['model_type']})...")
            os.system(f'wget -q https://dl.fbaipublicfiles.com/segment_anything/{cp}')

        try:
            sam = sam_model_registry[self.config['model_type']](checkpoint=cp)
            sam.to(self.config['device'])
            self.predictor = SamPredictor(sam)
            logger.info(f"✅ SAM listo en {self.config['device'].upper()}")
            return True
        except Exception as e:
            logger.error(f"❌ Error SAM: {e}")
            return False

    def segmentar(self, ndvi: np.ndarray, area_ref: float, bbox_ee: object) -> Dict:
        """Segmenta con control automático de fugas."""
        if not self.predictor:
            return {'status': 'SAM_ERROR'}

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
        area_ha_aprox = (area_px * 100) / 10000

        # Refinamiento si hay fuga (área mucho mayor a la esperada)
        if area_ha_aprox > area_ref * PoligonizadorConfig.POLYGON['tolerancia_fuga']:
            x0, y0, x1, y1 = box
            pts_neg = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
            coords = np.vstack([[cx, cy], pts_neg])
            labels = np.array([1, 0, 0, 0, 0])

            m2, s2, _ = self.predictor.predict(
                point_coords=coords,
                point_labels=labels,
                box=box[None, :],
                multimask_output=False
            )
            mask, score = m2[0].copy(), float(s2[0])
            area_ha_aprox = (mask.sum() * 100) / 10000

        # Vectorización
        contornos, _ = cv2.findContours((mask * 255).astype(np.uint8),
                                        cv2.RETR_EXTERNAL,
                                        cv2.CHAIN_APPROX_SIMPLE)
        if not contornos:
            return {'status': 'SIN_CONTORNO', 'score': score}

        cnt = max(contornos, key=cv2.contourArea)
        eps = PoligonizadorConfig.POLYGON['factor_suavizado'] * cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, eps, True).squeeze()

        if approx.ndim == 1 or len(approx) < 3:
            return {'status': 'CONTORNO_INVALIDO'}

        # Georreferenciación
        poligono = None
        if bbox_ee:
            coords_bb = bbox_ee.bounds().getInfo()['coordinates'][0]
            lons = [c[0] for c in coords_bb]
            lats = [c[1] for c in coords_bb]
            v_geo = [pixel_a_geo(p[0], p[1], min(lons), max(lons), min(lats), max(lats), w, h)
                     for p in approx]
            poligono = Polygon(v_geo).buffer(0)

        return {
            'status': 'OK',
            'poligono': poligono,
            'area_ha': round(area_ha_aprox, 1),
            'score': round(score, 3),
            'vertices': len(approx)
        }


# =============================================================================
# PIPELINE COORDINADOR
# =============================================================================

class Poligonizador:
    """Pipeline unificado de poligonización."""

    def __init__(self):
        self.gee = EngineSatelital()
        self.sam = SegmentadorSAM()
        self.df = None
        self.results = []
        self.features = []

    def cargar_datos(self, path: str) -> bool:
        """Carga y normaliza datos de entrada (CSV/XLSX)."""
        try:
            logger.info(f"Cargando: {path}")
            df = pd.read_excel(path) if path.endswith('.xlsx') else pd.read_csv(path)

            # Mapeo de columnas
            renames = {}
            for target, patterns in PoligonizadorConfig.COLUMN_MAPPING.items():
                col = encontrar_columna(patterns, df.columns.tolist())
                if col:
                    renames[col] = target

            df = df.rename(columns=renames)

            # Parsing
            for c in ['lat', 'lon']:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors='coerce')

            if 'fecha' in df.columns:
                df['fecha'] = pd.to_datetime(df['fecha'], errors='coerce', dayfirst=True)

            # Limpieza
            df = df.dropna(subset=['lat', 'lon'])
            if 'id' not in df.columns:
                df['id'] = range(1, len(df) + 1)
            df['id'] = df['id'].astype(str)

            # Filtro Argentina
            df = df[(df['lat'] >= -42) & (df['lat'] <= -22) & (df['lon'] >= -70) & (df['lon'] <= -53)]

            # Reducción por proximidad (4 decimales = ~1.1 km)
            df['lat_r'] = df['lat'].round(4)
            df['lon_r'] = df['lon'].round(4)
            self.df = df.drop_duplicates(subset=['lat_r', 'lon_r']).reset_index(drop=True)

            logger.info(f"✅ {len(self.df)} lotes preparados")
            return True
        except Exception as e:
            logger.error(f"Error cargando datos: {e}")
            return False

    def ejecutar(self, output_prefix: str = 'agroia_poligonos') -> bool:
        """Ejecuta pipeline completo."""
        if self.df is None:
            logger.error("No hay datos cargados")
            return False

        if not self.gee.conectar():
            logger.error("No se pudo conectar a GEE")
            return False

        if not self.sam.cargar():
            logger.error("No se pudo cargar SAM")
            return False

        out_geojson = f"{output_prefix}.geojson"
        out_csv = f"{output_prefix}_log.csv"

        logger.info(f"Iniciando poligonización en {PoligonizadorConfig.SAM['device'].upper()}...")

        from tqdm import tqdm
        pbar = tqdm(self.df.iterrows(), total=len(self.df), desc="Procesando")

        for idx, row in pbar:
            t0 = time.time()
            area_ref = row.get('dano_ha', row.get('area', 50))
            if pd.isna(area_ref) or area_ref <= 0:
                area_ref = 50

            # Ventana de búsqueda
            if pd.notna(row.get('fecha')):
                f_ini = (row['fecha'] - pd.Timedelta(days=PoligonizadorConfig.GEE['dias_ventana'])).strftime('%Y-%m-%d')
                f_fin = row['fecha'].strftime('%Y-%m-%d')
            else:
                f_ini = '2025-12-01'
                f_fin = '2026-03-31'

            # Descargar NDVI
            ndvi, bbox = self.gee.descargar_ndvi(row['lat'], row['lon'], f_ini, f_fin)

            res = {
                'id': row['id'],
                'estado': 'SIN_IMAGEN',
                'area_ha': 0,
                'score': 0,
                'segundos': round(time.time() - t0, 1)
            }

            if ndvi is not None:
                sam_res = self.sam.segmentar(ndvi, area_ref, bbox)
                res.update(sam_res)

                if sam_res.get('status') == 'OK' and sam_res.get('poligono'):
                    real_ha = calcular_area_ha(sam_res['poligono'], row['lat'])
                    res['area_ha'] = round(real_ha, 1)

                    # VALIDACIÓN CRÍTICA
                    if real_ha < PoligonizadorConfig.POLYGON['min_area_ha']:
                        res['estado'] = 'AREA_MINIMA_FAIL'
                    elif real_ha > PoligonizadorConfig.POLYGON['max_area_ha']:
                        res['estado'] = 'SOBRE_SEGMENTADO'
                    else:
                        res['estado'] = 'OK'

                        # Calcular error
                        error_pct = 0
                        if area_ref > 0:
                            error_pct = round(abs(real_ha - area_ref) / area_ref * 100, 1)

                        # AGREGAR TODAS LAS PROPIEDADES
                        feat = {
                            "type": "Feature",
                            "properties": {
                                "id": row['id'],
                                "area_ha": res['area_ha'],
                                "sam_score": res.get('score', 0),
                                "error_pct": error_pct,
                                "cultivo": row.get('cultivo', ''),
                                "localidad": row.get('localidad', ''),
                            },
                            "geometry": mapping(sam_res['poligono'])
                        }
                        self.features.append(feat)

            self.results.append(res)
            pbar.set_postfix({'status': res['estado'], 'ha': res['area_ha']})

        # Guardar resultados
        self._guardar(out_geojson, out_csv)
        self._finalizar(output_prefix)
        return True

    def _guardar(self, g: str, c: str):
        """Guarda GeoJSON y CSV."""
        with open(g, 'w') as f:
            json.dump({"type": "FeatureCollection", "features": self.features}, f)

        pd.DataFrame(self.results).drop(columns=['poligono'], errors='ignore').to_csv(c, index=False)
        logger.info(f"✅ Guardado: {g}, {c}")

    def _finalizar(self, prefix: str):
        """Genera resumen y reportes."""
        logger.info(f"\n📊 Resultados: {len(self.features)} polígonos válidos")

        # Estadísticas
        df_res = pd.DataFrame(self.results)
        ok_count = len(df_res[df_res['estado'] == 'OK'])
        logger.info(f"   OK: {ok_count}/{len(df_res)} ({ok_count*100//len(df_res)}%)")
        logger.info(f"   Sam score promedio: {df_res[df_res['estado']=='OK']['score'].mean():.3f}")


# =============================================================================
# CLI
# =============================================================================

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='AgroIA Poligonizador v2.1')
    parser.add_argument('--csv', default='siniestros.csv', help='Archivo entrada CSV/XLSX')
    parser.add_argument('--output', default='agroia_poligonos', help='Prefijo salida')
    args = parser.parse_args()

    pipe = Poligonizador()
    try:
        if pipe.cargar_datos(args.csv):
            if pipe.ejecutar(args.output):
                logger.info("\n✅ Proceso completado con éxito.")
            else:
                logger.error("Error en ejecución")
        else:
            logger.error("Error cargando datos")
    except Exception as e:
        logger.error(f"Error fatal: {e}", exc_info=True)
