"""
=============================================================================
AGROIA - POLIGONIZACIÓN AUTOMÁTICA DE LOTES
Pipeline Unificado v2.5 (Soberano - Copernicus Native)

MIGRADO: Se eliminó GEE. Usa Processing API de Copernicus CDSE.
=============================================================================
"""

import os
import sys
import json
import time
import math
import calendar
import warnings
import requests
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib
import matplotlib.cm as cm
import cv2
import rasterio
from shapely.geometry import Polygon, mapping, shape, Point

# Detección de Hardware
try:
    import torch
    DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'
except ImportError:
    DEVICE = 'cpu'

try:
    import geopandas as gpd
    from segment_anything import sam_model_registry, SamPredictor
except ImportError as e:
    print(f"⚠ Alerta de libreria: {e}")

warnings.filterwarnings('ignore')
matplotlib.use('Agg')
cmap = matplotlib.colormaps['RdYlGn']

# Importar configuración y tokens
try:
    from src.utils.config import settings
    from src.pipeline.eodag_extractor import get_cdse_token
except ImportError:
    # Fallback si se corre fuera del entorno src
    settings = None

# ============================================================================
# CONFIGURACIÓN GLOBAL
# ============================================================================

class Config:
    """Configuración centralizada del pipeline."""

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
        'buffer_m': 1500, # Radio de búsqueda alrededor del punto
    }

    COLUMNS = {
        'id': ['id', 'taype', 'numero', 'nro', 'lote_id', 'nombre'],
        'lat': ['lat_dec', 'latitude', 'lat', 'cg_latitud', 'y', 'latitud'],
        'lon': ['lon_dec', 'longitude', 'lon', 'cg_longitud', 'x', 'longitud', 'lng'],
        'fecha': ['fecha_de_s', 'fecha', 'date', 'fecha_siniestro', 'fecha_evento'],
        'dano_ha': ['has__daña', 'dano_ha', 'dano_estimado', 'area_ha', 'sup_afectada_ha', 'superficie', 'area', 'ha'],
        'cultivo': ['cultivo', 'crop', 'cultivoafectado', 'especie'],
        'localidad': ['localidad', 'location', 'loc', 'ciudad', 'departamento'],
    }


def ndvi_a_rgb(ndvi):
    """Normaliza NDVI y aplica colormap para visualización de SAM."""
    ndvi_clip = np.clip(ndvi, -0.2, 0.8)
    ndvi_norm = ((ndvi_clip + 0.2) * 255).astype(np.uint8)
    return (cmap(ndvi_norm / 255.0)[:, :, :3] * 255).astype(np.uint8)

def pixel_a_geo(col, row, transform):
    """Convierte píxel a coordenadas geográficas usando la matriz de transformación."""
    lon, lat = transform * (col, row)
    return (lon, lat)


class EngineCopernicus:
    """Maneja la descarga de imágenes minúsculas vía Processing API."""
    
    def __init__(self):
        self.token = None

    def conectar(self):
        self.token = get_cdse_token()
        return self.token is not None

    def descargar_ndvi_point(self, lat, lon, year=2024, month=1):
        """Descarga un recorte de 2km x 2km alrededor del punto."""
        if not self.token and not self.conectar():
            return None, None, None

        # Definir bbox de 2km (~0.02 grados)
        delta = 0.012 
        bbox = [lon - delta, lat - delta, lon + delta, lat + delta]
        last_day = calendar.monthrange(year, month)[1]

        evalscript = """
        //VERSION=3
        function setup() {
          return {
            input: ["B04", "B08", "SCL"],
            output: { id: "default", bands: 1, sampleType: "FLOAT32" }
          };
        }
        function evaluatePixel(samples) {
          let ndvi = (samples.B08 - samples.B04) / (samples.B08 + samples.B04);
          let isCloud = samples.SCL === 3 || samples.SCL === 8 || samples.SCL === 9 || samples.SCL === 10;
          return isCloud ? [-1] : [ndvi];
        }
        """

        payload = {
            "input": {
                "bounds": {
                    "bbox": bbox,
                    "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}
                },
                "data": [{
                    "type": "S2L2A",
                    "dataFilter": {"timeRange": {"from": f"{year}-{month:02d}-01T00:00:00Z", "to": f"{year}-{month:02d}-{last_day:02d}T23:59:59Z"}}
                }]
            },
            "output": {"resx": 0.0001, "resy": 0.0001, "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]},
            "evalscript": evalscript
        }

        try:
            resp = requests.post('https://sh.dataspace.copernicus.eu/api/v1/process', 
                                 headers={'Authorization': f'Bearer {self.token}'}, json=payload, timeout=60)
            
            if resp.status_code != 200:
                print(f"    ❌ Error Processing API: {resp.status_code} - {resp.text}")
                return None, None, None
            
            with rasterio.MemoryFile(resp.content) as memfile:
                with memfile.open() as dataset:
                    data = dataset.read(1)
                    transform = dataset.transform
            return data, transform, bbox
        except Exception as e:
            print(f"    ❌ Excepción en descarga: {e}")
            return None, None, None


class SegmentadorSAM:
    """Maneja la inferencia de Segment Anything Model."""
    def __init__(self, config=None):
        self.config = config or Config.SAM
        self.predictor = None

    def cargar(self):
        if self.predictor: return True
        # Buscar el checkpoint en la raíz (donde lo montamos en Docker)
        cp = self.config['checkpoint']
        if not os.path.exists(cp):
            # Probar en el padre si estamos en Poligonizacion/
            cp = os.path.join("..", self.config['checkpoint'])
        
        if not os.path.exists(cp):
            print(f"📥 Checkpoint {cp} no encontrado.")
            return False
        
        try:
            sam = sam_model_registry[self.config['model_type']](checkpoint=cp)
            sam.to(self.config['device'])
            self.predictor = SamPredictor(sam)
            return True
        except Exception as e:
            print(f"❌ Error SAM: {e}")
            return False

    def segmentar(self, ndvi, transform):
        if not self.predictor: return None
        
        h, w = ndvi.shape
        self.predictor.set_image(ndvi_a_rgb(ndvi))
        
        # Punto central (donde está el usuario)
        cx, cy = w // 2, h // 2
        
        masks, scores, _ = self.predictor.predict(
            point_coords=np.array([[cx, cy]]),
            point_labels=np.array([1]),
            multimask_output=True
        )
        
        idx = np.argmax(scores)
        mask = masks[idx]
        
        # Vectorizar
        contornos, _ = cv2.findContours((mask * 255).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contornos: return None
        
        cnt = max(contornos, key=cv2.contourArea)
        eps = 0.005 * cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, eps, True).squeeze()
        
        if approx.ndim == 1 or len(approx) < 3: return None
        
        # Georreferenciar
        v_geo = [pixel_a_geo(p[0], p[1], transform) for p in approx]
        return Polygon(v_geo)


class AgroIAPipeline:
    def __init__(self):
        self.copernicus = EngineCopernicus()
        self.sam = SegmentadorSAM()
        self.df = None
        self.features = []

    def cargar_datos(self, path):
        print(f"📂 Cargando: {path}")
        if str(path).endswith('.xlsx'):
            self.df = pd.read_excel(path)
        else:
            self.df = pd.read_csv(path)
            
        # Normalizar columnas
        for key, aliases in Config.COLUMNS.items():
            col = next((c for c in self.df.columns if c.lower() in aliases), None)
            if col: self.df.rename(columns={col: key}, inplace=True)

    def ejecutar(self, output_prefix='agroia_poligonos'):
        if self.df is None: return
        if not self.sam.cargar(): return
        if not self.copernicus.conectar(): return
        
        print(f"🚀 Iniciando delineación inteligente de {len(self.df)} puntos...")
        
        for idx, row in self.df.iterrows():
            lat = row.get('lat')
            lon = row.get('lon')
            lote_id = str(row.get('id', f"LOTE_{idx+1:03d}"))
            cultivo = str(row.get('cultivo', 'maiz')).lower()
            
            print(f"  [{idx+1}/{len(self.df)}] Delineando: {lote_id} ({lat}, {lon})...")
            sys.stdout.flush()
            
            if pd.isna(lat) or pd.isna(lon):
                print(f"    ⚠️ Coordenadas inválidas para {lote_id}")
                sys.stdout.flush()
                continue

            img, trans, bbox = self.copernicus.descargar_ndvi_point(float(lat), float(lon))
            if img is None:
                print(f"    ⚠️ No se pudo descargar imagen de Copernicus para {lote_id}")
                sys.stdout.flush()
                continue
                
            print(f"    🛰️ Imagen descargada. Iniciando segmentación SAM...")
            sys.stdout.flush()
            poly = self.sam.segmentar(img, trans)
            if poly:
                print(f"    ✅ Polígono generado para {lote_id}")
                sys.stdout.flush()
                self.features.append({
                    "type": "Feature",
                    "properties": {"id": lote_id, "cultivo": cultivo, "area_ha": row.get('dano_ha', 0)},
                    "geometry": mapping(poly)
                })
            else:
                print(f"    ⚠️ SAM no pudo identificar un lote en estas coordenadas para {lote_id}")
                sys.stdout.flush()
        
        if self.features:
            geojson = {"type": "FeatureCollection", "features": self.features}
            out_file = f"{output_prefix}.geojson"
            with open(out_file, 'w') as f:
                json.dump(geojson, f)
            print(f"✅ Proceso completado. Archivo generado: {out_file}")
            return out_file
        return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="CSV o XLSX con puntos GPS")
    args = parser.parse_args()
    
    pipe = AgroIAPipeline()
    pipe.cargar_datos(args.input)
    pipe.ejecutar()
