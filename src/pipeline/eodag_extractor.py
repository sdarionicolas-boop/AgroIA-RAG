# src/pipeline/eodag_extractor.py
import os
import sys
import requests
import json
import calendar
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
from datetime import datetime
from shapely.geometry import mapping, Point


# ===========================================================================
# Tarea 4 — Estados degradados de la consulta CDSE
# ===========================================================================
class CDSEDataState(str, Enum):
    """Estado de la última respuesta del extractor CDSE.

    Orden de severidad (de menos a más grave):
      FRESH < CACHED_VALID < CACHED_STALE < CACHED_EXPIRED_FALLBACK < NO_DATA_AVAILABLE
    """
    FRESH = "fresh"
    """API CDSE respondió y los datos son nuevos en este turno."""
    CACHED_VALID = "cached_valid"
    """Caché local vigente (dentro del TTL nominal)."""
    CACHED_STALE = "cached_stale"
    """Caché vigente pero próxima a expirar (>= 90% del TTL consumido). Usable pero alerta."""
    CACHED_EXPIRED_FALLBACK = "cached_expired_fallback"
    """Caché expirada, devuelta porque la API CDSE no respondió tras todos los reintentos."""
    NO_DATA_AVAILABLE = "no_data_available"
    """Ni caché ni API respondieron. El sistema no tiene datos para servir."""


_STATE_SEVERITY = {
    CDSEDataState.FRESH: 0,
    CDSEDataState.CACHED_VALID: 1,
    CDSEDataState.CACHED_STALE: 2,
    CDSEDataState.CACHED_EXPIRED_FALLBACK: 3,
    CDSEDataState.NO_DATA_AVAILABLE: 4,
}


@dataclass(frozen=True)
class CDSEResult:
    """Resultado tipado de una consulta CDSE con telemetría."""
    data: Any
    state: CDSEDataState
    cache_age_seconds: Optional[float]
    attempts: int
    message: str
    year: Optional[int] = None
    month: Optional[int] = None


# Retry exponencial — 3 intentos antes del fallback de caché
RETRY_DELAYS_SECONDS: tuple[float, ...] = (2.0, 8.0, 30.0)
"""Esperas entre reintentos (segundos). Total acumulado: 40 s ≈ 0.7 min."""

# TTL de caché para el año actual (años pasados → permanente)
CACHE_TTL_SECONDS_CURRENT_YEAR: int = 48 * 3600
STALE_FRACTION: float = 0.9
"""Por encima de este % del TTL consumido, la caché se marca STALE y se intenta refresh."""

# Tiempo sugerido de espera para reintento manual (mensaje al usuario)
RETRY_SUGGESTION_MINUTES: int = 5

# Telemetría: lista en memoria de los CDSEResult de la última sesión.
# Resetear con reset_cdse_state_log() al inicio de cada corrida de análisis.
_CDSE_STATE_LOG: list[CDSEResult] = []


def reset_cdse_state_log() -> None:
    """Limpia el log de estados (llamar al inicio de un nuevo análisis)."""
    _CDSE_STATE_LOG.clear()


def get_cdse_state_log() -> list[CDSEResult]:
    """Copia inmutable del log de estados de la última corrida."""
    return list(_CDSE_STATE_LOG)


def get_worst_cdse_state() -> CDSEDataState:
    """Estado peor (más severo) entre los registrados desde el último reset.

    Si no hay registros, devuelve FRESH (caso neutral — no se hizo ninguna
    consulta todavía).
    """
    if not _CDSE_STATE_LOG:
        return CDSEDataState.FRESH
    return max(_CDSE_STATE_LOG, key=lambda r: _STATE_SEVERITY[r.state]).state


def _record_state(result: CDSEResult) -> CDSEResult:
    """Append al log + return passthrough."""
    _CDSE_STATE_LOG.append(result)
    return result


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

import sqlite3
import hashlib
import io
import geopandas as gpd

def get_cache_db():
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cache_dir = os.path.join(base_dir, "data")
    os.makedirs(cache_dir, exist_ok=True)
    db_path = os.path.join(cache_dir, "cache_copernicus.db")
    conn = sqlite3.connect(db_path, timeout=10)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS statistics_cache (
            cache_key TEXT PRIMARY KEY,
            response_json TEXT,
            timestamp INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS process_cache (
            cache_key TEXT PRIMARY KEY,
            zones_geojson TEXT,
            points_geojson TEXT,
            timestamp INTEGER
        )
    """)
    conn.commit()
    return conn

def get_cache_key(geom, year, month):
    try:
        from shapely.ops import transform
        def round_coords(x, y, z=None):
            return tuple(round(c, 5) for c in (x, y))
        geom_rounded = transform(round_coords, geom)
        wkt = geom_rounded.wkt
    except Exception:
        wkt = geom.wkt
    key_str = f"{wkt}_{year}_{month}"
    return hashlib.md5(key_str.encode('utf-8')).hexdigest()

def query_cache_stats(cache_key):
    conn = None
    try:
        conn = get_cache_db()
        cursor = conn.cursor()
        cursor.execute("SELECT response_json, timestamp FROM statistics_cache WHERE cache_key = ?", (cache_key,))
        row = cursor.fetchone()
        if row:
            return row[0], row[1]
    except Exception as e:
        _safe_print(f"  ⚠️ Error leyendo caché: {e}")
    finally:
        if conn: conn.close()
    return None, None

def save_cache_stats(cache_key, response_json):
    import time
    conn = None
    try:
        conn = get_cache_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO statistics_cache (cache_key, response_json, timestamp) VALUES (?, ?, ?)",
            (cache_key, response_json, int(time.time()))
        )
        conn.commit()
    except Exception as e:
        _safe_print(f"  ⚠️ Error guardando en caché: {e}")
    finally:
        if conn: conn.close()

def query_cache_process(cache_key):
    conn = None
    try:
        conn = get_cache_db()
        cursor = conn.cursor()
        cursor.execute("SELECT zones_geojson, points_geojson, timestamp FROM process_cache WHERE cache_key = ?", (cache_key,))
        row = cursor.fetchone()
        if row:
            return row[0], row[1], row[2]
    except Exception as e:
        _safe_print(f"  ⚠️ Error leyendo caché de zonificación: {e}")
    finally:
        if conn: conn.close()
    return None, None, None

def save_cache_process(cache_key, zones_geojson, points_geojson):
    import time
    conn = None
    try:
        conn = get_cache_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO process_cache (cache_key, zones_geojson, points_geojson, timestamp) VALUES (?, ?, ?, ?)",
            (cache_key, zones_geojson, points_geojson, int(time.time()))
        )
        conn.commit()
    except Exception as e:
        _safe_print(f"  ⚠️ Error guardando en caché de zonificación: {e}")
    finally:
        if conn: conn.close()

def _build_statistics_payload(geom_shapely, year, month):
    """Construye el payload de la Statistical API para un (geom, year, month)."""
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
      return { default: [ndvi], dataMask: [mask] };
    }
    """
    return {
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
                "histograms": {"default": {"binBy": "VALUE", "low": -1, "high": 1, "nBins": 10}},
                "statistics": {"default": {"stDev": True, "mean": True, "min": True, "max": True}}
            }
        }
    }


def _attempt_cdse_stats_post(payload, token, timeout=45):
    """Un único POST. Retorna (data_or_None, error_message_or_None)."""
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    try:
        resp = requests.post(
            'https://sh.dataspace.copernicus.eu/api/v1/statistics',
            headers=headers, json=payload, timeout=timeout,
        )
        if resp.status_code == 200:
            return resp.json().get('data', []), None
        return None, f"HTTP {resp.status_code}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def get_eodag_stats_with_state(geom_shapely, year, month) -> CDSEResult:
    """Consulta CDSE con retry exponencial y devuelve CDSEResult.

    Lógica:
      1. Lee caché local. Si vigente → CACHED_VALID o CACHED_STALE (si >= 90% TTL).
      2. Si caché STALE o expirada: intenta hasta 3 POST con backoff
         (2 s, 8 s, 30 s). Si alguno tiene éxito → FRESH y persiste en caché.
      3. Si todos fallan y hay caché previa → CACHED_EXPIRED_FALLBACK.
      4. Si todos fallan y no hay caché → NO_DATA_AVAILABLE.

    Garantiza: nunca tira excepción al caller; siempre devuelve CDSEResult.
    """
    cache_key = get_cache_key(geom_shapely, year, month)
    cached_data, timestamp = query_cache_stats(cache_key)
    current_year = datetime.now().year
    is_past_year = (year < current_year)
    ttl_seconds = CACHE_TTL_SECONDS_CURRENT_YEAR
    now = time.time()
    cache_age = (now - timestamp) if (timestamp is not None) else None

    # 1. Caché disponible y vigente → CACHED_VALID o CACHED_STALE
    if cached_data is not None:
        if is_past_year:
            _safe_print(f"    💾 [CACHÉ-PASADO] {year}-{month:02d}: caché permanente.")
            return _record_state(CDSEResult(
                data=json.loads(cached_data), state=CDSEDataState.CACHED_VALID,
                cache_age_seconds=cache_age, attempts=0,
                message=f"caché permanente (año {year} < año actual {current_year}).",
                year=year, month=month,
            ))
        if cache_age is not None and cache_age < ttl_seconds * STALE_FRACTION:
            _safe_print(f"    💾 [CACHÉ] {year}-{month:02d}: caché vigente.")
            return _record_state(CDSEResult(
                data=json.loads(cached_data), state=CDSEDataState.CACHED_VALID,
                cache_age_seconds=cache_age, attempts=0,
                message=f"caché vigente (edad {cache_age/3600:.1f} h, TTL {ttl_seconds/3600:.0f} h).",
                year=year, month=month,
            ))
        if cache_age is not None and cache_age < ttl_seconds:
            # 90%-100% del TTL: STALE pero usable. Devolver y NO intentar refresh
            # bloqueante — el caller puede consultar el estado y decidir.
            _safe_print(f"    🟡 [STALE] {year}-{month:02d}: caché cerca de expirar ({cache_age/3600:.1f}/{ttl_seconds/3600:.0f} h).")
            return _record_state(CDSEResult(
                data=json.loads(cached_data), state=CDSEDataState.CACHED_STALE,
                cache_age_seconds=cache_age, attempts=0,
                message=f"caché stale ({cache_age/3600:.1f}/{ttl_seconds/3600:.0f} h consumidas).",
                year=year, month=month,
            ))
        # cache_age >= ttl → expirado; cae al loop de retry abajo
        _safe_print(f"    ⏳ [CACHÉ] {year}-{month:02d}: expiró (edad {cache_age/3600:.1f} h). Re-consultando API CDSE...")

    # 2. Necesitamos la API. Obtener token primero (su retry está adentro).
    token = get_cdse_token()
    if not token:
        if cached_data is not None:
            _safe_print(f"    🟠 [FALLBACK] {year}-{month:02d}: sin token. Usando caché expirada.")
            return _record_state(CDSEResult(
                data=json.loads(cached_data), state=CDSEDataState.CACHED_EXPIRED_FALLBACK,
                cache_age_seconds=cache_age, attempts=0,
                message="auth CDSE fallida; servido desde caché expirada.",
                year=year, month=month,
            ))
        _safe_print(f"    🔴 [NO-DATA] {year}-{month:02d}: sin token y sin caché.")
        return _record_state(CDSEResult(
            data=None, state=CDSEDataState.NO_DATA_AVAILABLE,
            cache_age_seconds=None, attempts=0,
            message=(
                f"sin auth CDSE y sin caché previa. "
                f"Reintenta en ~{RETRY_SUGGESTION_MINUTES} min."
            ),
            year=year, month=month,
        ))

    # 3. Loop de retry exponencial sobre el POST a Statistical API
    payload = _build_statistics_payload(geom_shapely, year, month)
    last_error = None
    attempts_done = 0
    for attempt, delay in enumerate(RETRY_DELAYS_SECONDS, start=1):
        attempts_done = attempt
        data, err = _attempt_cdse_stats_post(payload, token)
        if data is not None:
            save_cache_stats(cache_key, json.dumps(data))
            _safe_print(f"    ✅ [FRESH] {year}-{month:02d}: respuesta CDSE en intento {attempt}/{len(RETRY_DELAYS_SECONDS)}.")
            return _record_state(CDSEResult(
                data=data, state=CDSEDataState.FRESH,
                cache_age_seconds=None, attempts=attempt,
                message=f"FRESH en intento {attempt}/{len(RETRY_DELAYS_SECONDS)}.",
                year=year, month=month,
            ))
        last_error = err
        _safe_print(f"    ⚠️ CDSE intento {attempt}/{len(RETRY_DELAYS_SECONDS)} falló: {err}")
        # No dormir tras el último intento
        if attempt < len(RETRY_DELAYS_SECONDS):
            time.sleep(delay)

    # 4. Todos los intentos fallaron → fallback a caché o NO_DATA
    if cached_data is not None:
        _safe_print(
            f"    🟠 [FALLBACK] {year}-{month:02d}: {attempts_done} intentos fallaron "
            f"(último: {last_error}). Usando caché expirada."
        )
        return _record_state(CDSEResult(
            data=json.loads(cached_data), state=CDSEDataState.CACHED_EXPIRED_FALLBACK,
            cache_age_seconds=cache_age, attempts=attempts_done,
            message=f"API CDSE no respondió ({attempts_done} intentos, último error: {last_error}). Servido desde caché expirada.",
            year=year, month=month,
        ))
    _safe_print(
        f"    🔴 [NO-DATA] {year}-{month:02d}: {attempts_done} intentos fallaron sin caché previa."
    )
    return _record_state(CDSEResult(
        data=None, state=CDSEDataState.NO_DATA_AVAILABLE,
        cache_age_seconds=None, attempts=attempts_done,
        message=(
            f"API CDSE no respondió tras {attempts_done} intentos "
            f"(último error: {last_error}). Sin caché previa. "
            f"Reintenta en ~{RETRY_SUGGESTION_MINUTES} min."
        ),
        year=year, month=month,
    ))


def get_eodag_stats(geom_shapely, year, month):
    """API compatible: devuelve la data (dict|list) o None.

    Internamente usa get_eodag_stats_with_state(); el estado queda
    accesible vía get_cdse_state_log() / get_worst_cdse_state().
    """
    result = get_eodag_stats_with_state(geom_shapely, year, month)
    return result.data

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
    Usa caché con TTL de 48h para el año actual y permanente para años anteriores.
    """
    import numpy as np
    from sklearn.cluster import KMeans
    import rasterio
    from rasterio.features import shapes
    import geopandas as gpd
    from shapely.geometry import shape
    import time
    import io

    cache_key = get_cache_key(geom_shapely, year, month)
    cached_zones, cached_points, timestamp = query_cache_process(cache_key)
    
    current_year = datetime.now().year
    is_past_year = (year < current_year)
    ttl_seconds = 48 * 3600
    
    if cached_zones is not None and cached_points is not None:
        now = time.time()
        if is_past_year or (now - timestamp < ttl_seconds):
            _safe_print(f"    💾 [CACHÉ] Usando zonificación A/B/C recuperada de caché local ({year}-{month:02d}).")
            zones_gdf = gpd.read_file(io.StringIO(cached_zones))
            points_gdf = gpd.read_file(io.StringIO(cached_points))
            return zones_gdf, points_gdf
        else:
            _safe_print(f"    ⏳ [CACHÉ] Caché de zonificación para {year}-{month:02d} expiró (TTL 48h). Re-consultando API CDSE...")

    token = get_cdse_token()
    if not token:
        if cached_zones is not None and cached_points is not None:
            _safe_print(f"    ⚠️ [FALLBACK] Error CDSE. Usando zonificación expirada de caché local.")
            zones_gdf = gpd.read_file(io.StringIO(cached_zones))
            points_gdf = gpd.read_file(io.StringIO(cached_points))
            return zones_gdf, points_gdf
        return None

    bounds = geom_shapely.bounds
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
        
        if resp.status_code != 200:
            if cached_zones is not None and cached_points is not None:
                _safe_print(f"    ⚠️ [FALLBACK] Error API CDSE ({resp.status_code}). Usando zonificación expirada.")
                zones_gdf = gpd.read_file(io.StringIO(cached_zones))
                points_gdf = gpd.read_file(io.StringIO(cached_points))
                return zones_gdf, points_gdf
            return None
        
        with rasterio.MemoryFile(resp.content) as memfile:
            with memfile.open() as dataset:
                data = dataset.read(1)
                transform = dataset.transform
                
        valid_mask = (data > -1) & (data <= 1)
        pixels = data[valid_mask].reshape(-1, 1)
        if len(pixels) < n_clusters: return None
        
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = kmeans.fit_predict(pixels)
        
        centers = kmeans.cluster_centers_.flatten()
        idx_sorted = np.argsort(centers)
        label_map = {old: new for new, old in enumerate(idx_sorted)}
        
        cluster_means = {label_map[i]: centers[i] for i in range(n_clusters)}
        
        full_labels = np.full(data.shape, -1, dtype=np.int16)
        full_labels[valid_mask] = [label_map[l] for l in labels]
        
        results_poly = []
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

        rows, cols = np.where(valid_mask)
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
        
        save_cache_process(cache_key, zones_gdf.to_json(), points_gdf.to_json())
        
        _safe_print(f"    ✅ Zonificación completada y guardada en caché: {len(zones_gdf)} polígonos y {len(points_gdf)} puntos generados.")
        return zones_gdf, points_gdf

    except Exception as e:
        _safe_print(f"  ⚠️ Error en zonificación: {e}")
        if cached_zones is not None and cached_points is not None:
            _safe_print(f"    ⚠️ [FALLBACK] Excepción en Processing API. Usando zonificación expirada.")
            zones_gdf = gpd.read_file(io.StringIO(cached_zones))
            points_gdf = gpd.read_file(io.StringIO(cached_points))
            return zones_gdf, points_gdf
        return None
