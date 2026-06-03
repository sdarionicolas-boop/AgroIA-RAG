# src/pipeline/eodag_extractor.py
import os
import sys
import requests
import json
import calendar
import math
from datetime import datetime
from shapely.geometry import mapping, Point

def _safe_print(msg):
    try:
        print(msg)
    except Exception:
        pass

def init_eodag():
    """
    Inicializa el entorno y verifica credenciales para la Statistical API de CDSE.
    Mantiene compatibilidad de importación con el validador y otros módulos del pipeline.
    """
    try:
        from dotenv import load_dotenv
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env_path = os.path.join(base_dir, "config", ".env")
        if os.path.exists(env_path):
            load_dotenv(env_path)
        
        username = os.environ.get("EODAG__COP_DATASPACE__AUTH__CREDENTIALS__USERNAME")
        password = os.environ.get("EODAG__COP_DATASPACE__AUTH__CREDENTIALS__PASSWORD")
        if not username or not password:
            _safe_print("⚠️ [ADVERTENCIA] Credenciales CDSE no encontradas en config/.env")
        return None
    except Exception as e:
        _safe_print(f"[ERROR] init_eodag: {e}")
        return None

_TOKEN_CACHE = {
    'token': None,
    'expiry': 0,
    'username': None
}
_ENV_LOADED = False

def get_cdse_token():
    """Obtiene el token OAuth2 para la Statistical API de CDSE (con caché)."""
    import time
    global _TOKEN_CACHE, _ENV_LOADED
    
    if not _ENV_LOADED:
        from dotenv import load_dotenv
        current_dir = os.path.dirname(os.path.abspath(__file__))
        env_loaded = False
        while current_dir and not env_loaded:
            env_path = os.path.join(current_dir, "config", ".env")
            if os.path.exists(env_path):
                load_dotenv(env_path, override=True)
                env_loaded = True
                break
            parent = os.path.dirname(current_dir)
            if parent == current_dir: break
            current_dir = parent
        _ENV_LOADED = True

    username = os.environ.get("EODAG__COP_DATASPACE__AUTH__CREDENTIALS__USERNAME")
    password = os.environ.get("EODAG__COP_DATASPACE__AUTH__CREDENTIALS__PASSWORD")
    
    if username: username = str(username).strip().strip('"').strip("'")
    if password: password = str(password).strip().strip('"').strip("'")
    
    if not username or not password:
        _safe_print("  ❌ ERROR: Credenciales de Copernicus no encontradas en el entorno ni en .env")
        return None

    # Verificar caché (válido por 50 minutos, el token suele durar 1h)
    now = time.time()
    if _TOKEN_CACHE['token'] and _TOKEN_CACHE['username'] == username and now < _TOKEN_CACHE['expiry']:
        return _TOKEN_CACHE['token']

    _safe_print(f"  🔑 Autenticando en CDSE como: {username[:4]}***@{username.split('@')[-1] if '@' in username else '???'}")
    
    auth_data = {
        'client_id': 'cdse-public',
        'grant_type': 'password',
        'username': username,
        'password': password
    }
    
    headers = {
        'User-Agent': 'AgroIA-RAG-Bot/2.5 (Hackathon CopernicusLAC)'
    }
    
    for attempt in range(1, 4):
        try:
            response = requests.post(
                'https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token', 
                data=auth_data, 
                headers=headers,
                timeout=30
            )
            if response.status_code == 200:
                data = response.json()
                token = data.get('access_token')
                expires_in = data.get('expires_in', 3600)
                
                _TOKEN_CACHE = {
                    'token': token,
                    'expiry': now + expires_in - 300, # 5 min de margen
                    'username': username
                }
                return token
            else:
                _safe_print(f"  ⚠️ Intento {attempt}/3 falló (Status {response.status_code}: {response.text[:100]}...)")
        except Exception as e:
            _safe_print(f"  ⚠️ Error en intento {attempt}/3: {e}")
        time.sleep(2)
    return None

def get_eodag_stats(geom_shapely, year, month):
    """
    Consulta la Statistical API de CDSE (Sentinel Hub) para obtener
    las estadísticas (mean, stdDev) del NDVI dentro del polígono.
    """
    token = get_cdse_token()
    if not token:
        return None

    last_day = calendar.monthrange(year, month)[1]
    start_date = f"{year}-{month:02d}-01T00:00:00Z"
    end_date = f"{year}-{month:02d}-{last_day}T23:59:59Z"

    evalscript = """
    //VERSION=3
    function setup() {
      return {
        input: ["B04", "B08", "SCL", "dataMask"],
        output: [
          {id: "default", bands: 1, sampleType: "FLOAT32"},
          {id: "dataMask", bands: 1}
        ]
      };
    }
    function evaluatePixel(samples) {
      let ndvi = (samples.B08 - samples.B04) / (samples.B08 + samples.B04);
      let isCloud = samples.SCL === 3 || samples.SCL === 8 || samples.SCL === 9 || samples.SCL === 10;
      let mask = samples.dataMask && !isCloud ? 1 : 0;
      return {
        default: [ndvi],
        dataMask: [mask]
      };
    }
    """

    payload = {
        "input": {
            "bounds": {
                "geometry": mapping(geom_shapely),
                "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}
            },
            "data": [{
                "type": "S2L2A",
                "dataFilter": {"timeRange": {"from": start_date, "to": end_date}}
            }]
        },
        "aggregation": {
            "timeRange": {"from": start_date, "to": end_date},
            "aggregationInterval": {"of": "P1D"},
            "evalscript": evalscript,
            "resx": 0.00005, "resy": 0.00005
        },

        "calculations": {
            "default": {
                "histograms": {
                    "default": {
                        "binBy": "VALUE",
                        "low": -1,
                        "high": 1,
                        "nBins": 10
                    }
                },
                "statistics": {
                    "default": {
                        "stDev": True,
                        "mean": True,
                        "min": True,
                        "max": True
                    }
                }
            }
        }
    }

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

    try:
        resp = requests.post(
            'https://sh.dataspace.copernicus.eu/api/v1/statistics', 
            headers=headers, 
            json=payload, 
            timeout=45
        )
        if resp.status_code == 200:
            return resp.json().get('data', [])
    except Exception as e:
        _safe_print(f"  ❌ Error consultando estadísticas CDSE: {e}")
    return None

def get_eodag_ndvi(geom_shapely, year, month):
    """Obtiene el NDVI promedio real usando la Statistical API."""
    _safe_print(f"🛰️ CDSE Cloud {year}-{month:02d} (Analítica en la Nube Copernicus)...")
    data = get_eodag_stats(geom_shapely, year, month)
    if not data:
        return None

    valid_means = []
    for d in data:
        # Exploración recursiva ligera para encontrar 'mean'
        outputs = d.get('outputs', {})
        default_out = outputs.get('default', {})
        bands = default_out.get('bands', {})
        b0 = bands.get('B0', {})
        stats = b0.get('stats', {})
        
        mean = stats.get('mean')
        if mean is not None and mean != 'NaN':
            try:
                val = float(mean)
                if not math.isnan(val):
                    valid_means.append(val)
            except: pass
    
    if valid_means:
        mean_val = sum(valid_means) / len(valid_means)
        _safe_print(f"    📊 NDVI Nube: {mean_val:.4f} (Promedio de {len(valid_means)} días limpios)")
        return mean_val
    
    _safe_print(f"    ⚠️ Sin días válidos (nubes o sin datos) en {year}-{month:02d}")
    return None

def get_eodag_ndvi_ventana(geom_shapely, year, month, max_delta=2):
    """Busca en ventana temporal."""
    for delta in range(0, max_delta + 1):
        for signo in ([0] if delta == 0 else [1, -1]):
            m_int = month + (delta * signo)
            if 1 <= m_int <= 12:
                val = get_eodag_ndvi(geom_shapely, year, m_int)
                if val is not None:
                    return val, m_int, None
    return None, None, "No data"

def calcular_cv_eodag(geom_shapely, year, month):
    """Calcula el CV espacial real (stdDev/mean) usando datos físicos de Copernicus."""
    data = get_eodag_stats(geom_shapely, year, month)
    if not data:
        return 0.0, 0.0, 0.0

    valid_cvs = []
    last_mean, last_std = 0.0, 0.0
    
    for d in data:
        stats = d.get('outputs', {}).get('default', {}).get('bands', {}).get('B0', {}).get('stats', {})
        m = stats.get('mean')
        s = stats.get('stDev')
        
        if m is not None and s is not None and m != 'NaN' and s != 'NaN':
            try:
                mv = float(m)
                sv = float(s)
                if mv > 0.01 and not math.isnan(mv) and not math.isnan(sv):
                    valid_cvs.append(sv / mv)
                    last_mean, last_std = mv, sv
            except: pass
            
    if valid_cvs:
        avg_cv = sum(valid_cvs) / len(valid_cvs)
        return avg_cv, last_mean, last_std
    return 0.0, 0.0, 0.0

def zonificar_lote_eodag(geom_shapely, year, month, n_clusters=3):
    """
    Realiza zonificación real: 
    1. Baja el NDVI actual del lote vía Processing API.
    2. Aplica K-Means para clasificar píxeles.
    3. Vectoriza a polígonos para visualización.
    """
    import numpy as np
    from sklearn.cluster import KMeans
    import rasterio
    from rasterio.features import shapes
    import geopandas as gpd
    from shapely.geometry import shape

    _safe_print(f"  🚜 Generando zonificación A/B/C ({year}-{month:02d})...")
    
    token = get_cdse_token()
    if not token: return None

    # 1. Definir bbox y petición a la Processing API
    bounds = geom_shapely.bounds # (minx, miny, maxx, maxy)
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
                "bbox": [bounds[0], bounds[1], bounds[2], bounds[3]],
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
                             headers={'Authorization': f'Bearer {token}'}, json=payload, timeout=60)
        
        if resp.status_code != 200: return None
        
        with rasterio.MemoryFile(resp.content) as memfile:
            with memfile.open() as dataset:
                data = dataset.read(1)
                transform = dataset.transform
                
        # 2. Clustering K-Means
        valid_mask = (data > -1) & (data <= 1)
        pixels = data[valid_mask].reshape(-1, 1)
        if len(pixels) < n_clusters: return None
        
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = kmeans.fit_predict(pixels)
        
        # Ordenar etiquetas por valor de NDVI (0=Bajo, 2=Alto)
        centers = kmeans.cluster_centers_.flatten()
        idx_sorted = np.argsort(centers)
        label_map = {old: new for new, old in enumerate(idx_sorted)}
        
        # Guardar promedios reales por cluster para los popups
        cluster_means = {label_map[i]: centers[i] for i in range(n_clusters)}
        
        full_labels = np.full(data.shape, -1, dtype=np.int16)
        full_labels[valid_mask] = [label_map[l] for l in labels]
        
        # 3. Vectorización de Polígonos
        results_poly = []
        from rasterio.features import shapes
        for s, val in shapes(full_labels.astype(np.int16), mask=valid_mask, transform=transform):
            zona_label = ['C', 'B', 'A'][int(val)]
            results_poly.append({
                'type': 'Feature',
                'properties': {
                    'zona': zona_label,
                    'ndvi': float(cluster_means[int(val)])
                },
                'geometry': s
            })
        
        zones_gdf = gpd.GeoDataFrame.from_features(results_poly, crs="EPSG:4326")
        zones_gdf = zones_gdf[zones_gdf.geometry.intersects(geom_shapely)]
        zones_gdf.geometry = zones_gdf.geometry.intersection(geom_shapely)

        # 4. Generación de Puntos de Alta Densidad (Muestreo)
        # Tomamos píxeles válidos y los muestreamos para no saturar el mapa
        rows, cols = np.where(valid_mask)
        # Muestrear (ej. 1 de cada 4 píxeles si hay muchos, o ajuste dinámico)
        step = max(1, len(rows) // 400) 
        idx_sample = np.arange(0, len(rows), step)
        
        points_list = []
        for i in idx_sample:
            r, c = rows[i], cols[i]
            lon, lat = transform * (c, r)
            p = Point(lon, lat)
            if p.intersects(geom_shapely):
                val_ndvi = float(data[r, c])
                val_zona = ['C', 'B', 'A'][full_labels[r, c]]
                points_list.append({
                    'geometry': p,
                    'ndvi': val_ndvi,
                    'zona': val_zona
                })
        
        points_gdf = gpd.GeoDataFrame(points_list, crs="EPSG:4326")
        
        _safe_print(f"    ✅ Zonificación completada: {len(zones_gdf)} polígonos y {len(points_gdf)} puntos generados.")
        return zones_gdf, points_gdf

    except Exception as e:
        _safe_print(f"  ⚠️ Error en zonificación: {e}")
        return None
