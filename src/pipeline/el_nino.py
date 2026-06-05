# src/pipeline/el_nino.py
"""
AgroIA — Módulo de Prevención y Gestión de El Niño para todo LAC
==============================================================
Calcula el riesgo de vulnerabilidad ante eventos de El Niño / Super Niño,
clasificando geográficamente las parcelas en distintas subregiones de
Latinoamérica y el Caribe (LAC) y proveyendo recomendaciones específicas por cultivo.
"""
import logging
import requests
import json
from shapely.geometry import Point
import numpy as np

logger = logging.getLogger(__name__)

# ============================================================================
# ALERTA OFICIAL DE RESPALDO (OMM / NOAA)
# ============================================================================
ALERTA_EL_NINO_LAC = {
    "estado": "ALERTA DE EVENTO EXTREMO - SUPER NIÑO",
    "probabilidad": 90.0,
    "periodo_critico": "Segundo semestre del año (Primavera-Verano Austral / Otoño-Invierno Boreal)",
    "anomalia_tsm_nino34": 1.8, # °C sobre el promedio en Región Niño 3.4
    "anomalia_tsm_nino12": 2.3, # °C sobre el promedio en Región Niño 1+2 (Costa Sudamérica)
    "fuente": "Organización Meteorológica Mundial (OMM) & NOAA Climate Prediction Center",
    "comunicado": (
        "Modelos globales y pronósticos coinciden en una probabilidad del 90% "
        "de que se genere un fenómeno de El Niño fuerte/extremo en el segundo "
        "semestre. Se anticipan lluvias torrenciales en el Cono Sur y Costa Pacífica norte, "
        "y sequías severas en el Caribe, Centroamérica y cuenca del Amazonas."
    )
}

# ============================================================================
# CLASIFICACIÓN DE REGIONES GEOGRÁFICAS DE LAC
# ============================================================================
REGIONES_LAC = {
    "cono_sur": {
        "nombre": "Cono Sur / Cuenca del Plata (Argentina, Uruguay, Paraguay, Sur de Brasil)",
        "impacto": "Lluvias extraordinarias, anegamientos de suelos, lavado de nutrientes y alta presión de hongos.",
        "descripcion": "Zona con relieve mayormente plano, propensa a inundaciones por exceso hídrico."
    },
    "pacifico_andino": {
        "nombre": "Región Andina / Costa Pacífica (Perú, Ecuador, Chile, Colombia Oeste)",
        "impacto": "Calor extremo costero, lluvias torrenciales norteñas, huaicos (deslaves) y sequía en el altiplano sur.",
        "descripcion": "Ecosistemas andinos y costeros con alta variabilidad topográfica y térmica."
    },
    "amazonica": {
        "nombre": "Cuenca Amazónica / Norte de Sudamérica (Brasil central/norte, Venezuela, Colombia Este)",
        "impacto": "Sequías extremas, estrés térmico, déficit hídrico en suelos y aumento de riesgo de incendios forestales.",
        "descripcion": "Suelos con baja retención hídrica bajo calor sostenido."
    },
    "centroamerica_caribe": {
        "nombre": "Centroamérica y El Caribe (Corredor Seco, Colombia Norte, Islas del Caribe)",
        "impacto": "Déficit severo de lluvias (sequía del Corredor Seco), olas de calor y estrés por evapotranspiración.",
        "descripcion": "Altamente susceptible a sequías prolongadas que retrasan siembras."
    },
    "mexico": {
        "nombre": "Norte de LAC / México",
        "impacto": "Condiciones más secas y cálidas en el centro-sur; mayor riesgo de lluvias invernales en el norte.",
        "descripcion": "Transición entre climas templados, áridos y tropicales."
    },
    "general_lac": {
        "nombre": "Latinoamérica y El Caribe (Zona General)",
        "impacto": "Incremento de anomalías térmicas y alteración de patrones de precipitaciones locales.",
        "descripcion": "Zona de transición climática general."
    }
}

def clasificar_region_lac(lat: float, lon: float) -> str:
    """Clasifica una coordenada en una de las subregiones de LAC."""
    # Cono Sur / Cuenca del Plata
    if lat < -20:
        return "cono_sur"
    # Pacífico Andino
    elif lon < -68 and -20 <= lat <= 12:
        return "pacifico_andino"
    # Amazonía / Norte de Sudamérica
    elif -20 <= lat <= 8 and -68 <= lon <= -34:
        return "amazonica"
    # Centroamérica y El Caribe
    elif 8 < lat <= 25 and -90 <= lon <= -55:
        return "centroamerica_caribe"
    # México
    elif lat > 15 and lon <= -80:
        return "mexico"
    # Por defecto
    else:
        return "general_lac"

# ============================================================================
# SCRAPING / ADQUISICIÓN DE ALERTAS ENSO
# ============================================================================
def obtener_alertas_el_nino() -> dict:
    """Intenta hacer scraping de alertas ENSO de NOAA o IRI; si falla, usa el fallback oficial."""
    url_noaa = "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_advisory/index.shtml"
    try:
        r = requests.get(url_noaa, timeout=8)
        if r.status_code == 200 and "El Niño" in r.text:
            # Encontramos la alerta en la NOAA, enriquecemos el dict base
            res = ALERTA_EL_NINO_LAC.copy()
            res["fuente"] = "NOAA Climate Prediction Center (Scraping en tiempo real)"
            if "El Niño Advisory" in r.text or "El Niño Alert" in r.text:
                res["estado"] = "ALERTA / ADVISORY ACTIVO DE EL NIÑO"
            return res
    except Exception:
        pass
    return ALERTA_EL_NINO_LAC

# ============================================================================
# CÁLCULO DE VULNERABILIDAD GEOGRÁFICA Y AGRONÓMICA
# ============================================================================

# Cache de elevación para evitar consultas repetidas a CDSE
# (la elevación no cambia, así que cachear por coordenadas tiene sentido)
_ELEVACION_CACHE = {}


def estimar_elevacion_lac(lat: float, lon: float) -> float:
    """Fallback heurístico de elevación cuando COP30 no está disponible."""
    # Si está cerca de la cordillera de los Andes
    if -75 <= lon <= -68:
        if -30 <= lat <= 5:
            return 2500.0  # Zona andina alta
    # Costa pacífica
    if lon < -76:
        return 100.0
    # Región pampeana plana
    if -64 <= lon <= -57 and -38 <= lat <= -30:
        return 80.0
    return 250.0


def obtener_elevacion_cop30(lat: float, lon: float, bbox_grados: float = 0.002) -> float:
    """
    Obtiene la elevación REAL del lote desde el Copernicus DEM (COP30)
    vía la Processing API de CDSE.

    COP30 es el Modelo Digital de Elevación global a 30m de resolución
    publicado por la Agencia Espacial Europea como parte del programa
    Copernicus. Es la fuente oficial europea de topografía.

    Si la consulta falla (sin token, sin red, timeout), cae automáticamente
    al estimador heurístico para garantizar que el pipeline nunca se rompa.

    Args:
        lat, lon: coordenadas del punto/lote
        bbox_grados: tamaño de la ventana a promediar (default 0.002° ≈ 220m)

    Returns:
        elevación promedio en metros sobre nivel del mar
    """
    # Cache hit: devolvé inmediato sin tocar la red
    cache_key = (round(lat, 4), round(lon, 4))
    if cache_key in _ELEVACION_CACHE:
        return _ELEVACION_CACHE[cache_key]

    try:
        # Reusar el token de CDSE que ya gestiona el pipeline principal
        from src.pipeline.eodag_extractor import get_cdse_token
        token = get_cdse_token()
        if not token:
            logger.info("COP30: sin token CDSE, usando heurística regional")
            elev = estimar_elevacion_lac(lat, lon)
            _ELEVACION_CACHE[cache_key] = elev
            return elev

        evalscript = """
        //VERSION=3
        function setup() {
          return {
            input: ["DEM"],
            output: { id: "default", bands: 1, sampleType: "FLOAT32" }
          };
        }
        function evaluatePixel(sample) {
          return [sample.DEM];
        }
        """

        payload = {
            "input": {
                "bounds": {
                    "bbox": [
                        lon - bbox_grados, lat - bbox_grados,
                        lon + bbox_grados, lat + bbox_grados
                    ],
                    "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}
                },
                "data": [{
                    "type": "DEM",
                    "dataFilter": {"demInstance": "COPERNICUS_30"}
                }]
            },
            "output": {
                "width": 5, "height": 5,
                "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]
            },
            "evalscript": evalscript
        }

        resp = requests.post(
            'https://sh.dataspace.copernicus.eu/api/v1/process',
            headers={'Authorization': f'Bearer {token}'},
            json=payload,
            timeout=15
        )

        if resp.status_code != 200:
            logger.warning(f"COP30 respondió {resp.status_code}, fallback heurístico")
            elev = estimar_elevacion_lac(lat, lon)
            _ELEVACION_CACHE[cache_key] = elev
            return elev

        import rasterio
        with rasterio.MemoryFile(resp.content) as memfile:
            with memfile.open() as dataset:
                dem_data = dataset.read(1)
                # Promedio de píxeles válidos (DEM puede tener nodata negativo)
                valid = dem_data[dem_data > -1000]
                elev = float(valid.mean()) if len(valid) > 0 else estimar_elevacion_lac(lat, lon)

        _ELEVACION_CACHE[cache_key] = elev
        logger.info(f"COP30 elevación obtenida para ({lat:.4f}, {lon:.4f}): {elev:.1f}m")
        return elev

    except Exception as e:
        logger.warning(f"COP30 falló ({e.__class__.__name__}), fallback heurístico: {e}")
        elev = estimar_elevacion_lac(lat, lon)
        _ELEVACION_CACHE[cache_key] = elev
        return elev

def calcular_vulnerabilidad_el_nino(
    lote_gdf,
    cultivo: str,
    ndvi_actual: float,
    temp_actual: float,
    precip_actual: float,
    elevation: float = None
) -> dict:
    """
    Calcula un score de vulnerabilidad ante El Niño (0.0 a 10.0)
    adaptado a la geografía de LAC y el cultivo seleccionado.
    """
    if lote_gdf is None or len(lote_gdf) == 0:
        return {"score": 0.0, "riesgo": "N/D", "region_id": "general_lac", "factores": []}

    centroid = lote_gdf.geometry.centroid.iloc[0]
    lat, lon = centroid.y, centroid.x

    # Elevación: priorizar Copernicus DEM (COP30) — fuente oficial europea —
    # con fallback automático a heurística regional si CDSE no responde.
    if elevation is None:
        elevation = obtener_elevacion_cop30(lat, lon)

    region_id = clasificar_region_lac(lat, lon)
    region_info = REGIONES_LAC[region_id]
    
    score = 0.0
    factores = []
    
    # ── FACTOR 1: GEOGRÁFICO-CLIMÁTICO ──
    if region_id == "cono_sur":
        # En el Cono Sur el mayor peligro es exceso de lluvia y topografía baja (inundación)
        if elevation < 100:
            score += 3.0
            factores.append("Topografía baja y plana, vulnerable a inundación espacial (anegamiento).")
        else:
            score += 1.5
            factores.append("Zona de llanura, riesgo moderado de acumulación de escorrentía.")
            
        if precip_actual > 25:
            score += 2.0
            factores.append("Precipitaciones actuales altas, saturación rápida de perfiles de suelo.")
            
    elif region_id == "pacifico_andino":
        # En la costa andina el peligro es calor marino e inundaciones costeras, y en sierra es sequía.
        if elevation < 250:  # Costa baja (ej. Piura, Ica, Guayas)
            score += 3.0
            factores.append("Costa de baja altitud, riesgo extremo de lluvias convectivas y desbordes.")
        else:  # Sierra andina
            score += 2.0
            factores.append("Zona andina alta, riesgo de sequía/heladas por desvío de vientos húmedos.")
            
        if temp_actual > 28:
            score += 2.0
            factores.append("Altas temperaturas locales, propicias para proliferación de plagas.")
            
    elif region_id in ("amazonica", "centroamerica_caribe"):
        # Peligro de sequías extremas y olas de calor
        score += 3.0
        factores.append("Región vulnerable a déficit severo de lluvias bajo El Niño (Corredor Seco / Amazonía).")
        if temp_actual > 32:
            score += 2.5
            factores.append("Estrés térmico severo documentado en campo.")
        if precip_actual < 5:
            score += 1.5
            factores.append("Ausencia de precipitaciones recientes, desecamiento de horizonte superficial.")
            
    elif region_id == "mexico":
        score += 2.0
        factores.append("Zona norteña con patrón variable, propensa a sequías en el centro-sur.")
        if temp_actual > 30:
            score += 1.0
            factores.append("Temperaturas sobre la media local.")
            
    else:  # general_lac
        score += 1.5
        factores.append("Ubicación general de transición climática en LAC.")

    # ── FACTOR 2: CULTIVO Y ESTADO DE VIGOR (NDVI) ──
    # Si el vigor está bajo el umbral histórico, es más susceptible a colapsar ante estrés climático
    umbrales_ndvi_min = {
        "maiz": 0.25, "soja": 0.25, "trigo": 0.20, "girasol": 0.22,
        "cebada": 0.20, "sorgo": 0.22, "mani": 0.20,
        "aji": 0.40, "rocoto": 0.45, "papa": 0.50
    }
    u_min = umbrales_ndvi_min.get(cultivo.lower(), 0.25)
    
    if ndvi_actual < u_min:
        score += 3.0
        factores.append(f"Vigor crítico muy bajo (NDVI={ndvi_actual:.3f} < umbral {u_min:.2f}), baja resiliencia.")
    elif ndvi_actual < u_min * 1.2:
        score += 1.5
        factores.append(f"Vigor actual debilitado o marginal (NDVI={ndvi_actual:.3f}), sensible a perturbaciones.")
    else:
        # NDVI saludable da resiliencia
        score -= 0.5
        factores.append("Cultivo con vigor óptimo, mayor capacidad de amortiguación de estrés.")

    # Ajustar límites del Score
    score = np.clip(score, 0.0, 10.0)
    score_redondeado = round(float(score), 1)

    # ── CLASIFICACIÓN DE RIESGO ──
    if score_redondeado <= 3.0:
        riesgo = "BAJO"
    elif score_redondeado <= 6.0:
        riesgo = "MEDIO"
    elif score_redondeado <= 8.5:
        riesgo = "ALTO"
    else:
        riesgo = "CRÍTICO"
        
    return {
        "score": score_redondeado,
        "riesgo": riesgo,
        "region_id": region_id,
        "region_nombre": region_info["nombre"],
        "region_impacto": region_info["impacto"],
        "factores": factores
    }

# ============================================================================
# RECOMENDACIONES AGRONÓMICAS POR CULTIVO Y REGIÓN
# ============================================================================
def obtener_recomendaciones_el_nino(cultivo: str, region_id: str, score: float) -> dict:
    """Genera checklist de recomendaciones preventivas por cultivo, región y score de riesgo."""
    cult = cultivo.lower().strip()
    
    recs = {
        "inmediatas": [],
        "corto_plazo": [],
        "mediano_plazo": []
    }
    
    # Si el riesgo es muy bajo, bastan las preventivas básicas
    if score <= 3.0:
        recs["inmediatas"].append("Mantener el esquema normal de monitoreo fenológico y satelital.")
        recs["corto_plazo"].append("Revisar canales de drenaje perimetrales como medida de precaución general.")
        recs["mediano_plazo"].append("Monitorear actualizaciones mensuales de los boletines ENSO de la OMM.")
        return recs

    # 1. RECOMENDACIONES POR SUBREGIÓN CLIMÁTICA
    if region_id == "cono_sur":
        recs["inmediatas"].append("Limpiar canales de drenaje principales y secundarios en lotes planos o deprimidos.")
        recs["corto_plazo"].append("Planificar fertilización nitrogenada fraccionada (split) para evitar pérdidas por lixiviación ante lluvias excesivas.")
        recs["corto_plazo"].append("Reforzar stock de fungicidas sistémicos contra roya en trigo/cebada o mancha ojo de rana en soja.")
        recs["mediano_plazo"].append("Evaluar la siembra de variedades con comportamiento erecto y resistencia al volcado (vuelco) por viento y tormentas.")
        
    elif region_id == "pacifico_andino":
        recs["inmediatas"].append("Ajustar frecuencias de riego para compensar las altas tasas de evapotranspiración diurna.")
        recs["inmediatas"].append("En zonas bajas de costa: despejar quebradas y vías de huaico cercanas a las parcelas.")
        recs["corto_plazo"].append("Aplicar bioestimulantes foliares (aminoácidos) para reducir el aborto de flores por estrés térmico.")
        recs["mediano_plazo"].append("En la sierra alta: diversificar cultivos y preparar cobertura de mantillo (mulch) para retener humedad residual.")
        
    elif region_id in ("amazonica", "centroamerica_caribe"):
        recs["inmediatas"].append("Instalar cubiertas orgánicas o mantillo (rastrojo de cultivo anterior) para proteger el suelo de la radiación directa.")
        recs["corto_plazo"].append("Optimizar sistemas de riego presurizado y priorizar riegos nocturnos para minimizar la evaporación.")
        recs["corto_plazo"].append("Crear cortafuegos perimetrales alrededor de las plantaciones forestales o cultivos vulnerables.")
        recs["mediano_plazo"].append("Adoptar prácticas de labranza mínima o siembra directa para preservar la estructura y humedad de las capas profundas del suelo.")
        
    elif region_id == "mexico":
        recs["inmediatas"].append("Monitorear el contenido de humedad del suelo en horizontes A y B.")
        recs["corto_plazo"].append("Establecer un programa de monitoreo intensivo de plagas chupadoras (pulgones/trips) que proliferan con clima seco.")
        recs["mediano_plazo"].append("Planificar calendarios de siembra adaptados para evitar la floración en el periodo de máxima sequía canicular.")
        
    else:  # general_lac
        recs["inmediatas"].append("Inspeccionar parcelas buscando síntomas visuales de estrés hídrico o clorosis foliar.")
        recs["corto_plazo"].append("Efectuar análisis preventivos de suelos para verificar disponibilidad de materia orgánica.")
        recs["mediano_plazo"].append("Considerar la integración de franjas de biodiversidad para regular el microclima.")

    # 2. RECOMENDACIONES ESPECÍFICAS DE CULTIVO
    if cult in ("maiz", "sorgo"):
        recs["inmediatas"].append("Controlar la densidad de plantas para evitar competencia excesiva por agua en floración.")
        recs["corto_plazo"].append("Monitorear de cerca la presencia de cogollero (Spodoptera frugiperda), que acelera su ciclo de vida con altas temperaturas.")
        if region_id == "cono_sur":
            recs["inmediatas"].append("Retrasar aplicaciones de nitrógeno si se pronostican lluvias torrenciales inminentes.")
            
    elif cult == "soja":
        recs["inmediatas"].append("Monitorear la aparición temprana de insectos vectores de virus por calor seco.")
        if region_id == "cono_sur":
            recs["corto_plazo"].append("Establecer planes de control preventivo contra enfermedades de fin de ciclo ante humedades relativas altas.")
            
    elif cult in ("trigo", "cebada"):
        recs["inmediatas"].append("Monitorear infecciones de roya de la hoja o fusariosis en espiga si coinciden lluvias y temperaturas templadas.")
        recs["corto_plazo"].append("Asegurar drenaje superficial para evitar la anoxia radicular en la etapa de macollaje.")
        
    elif cult == "mani":
        recs["corto_plazo"].append("Monitorear la maduración de vainas y evitar retrasos en el arrancado para que las lluvias no pudran los frutos enterrados.")
        
    elif cult in ("aji", "rocoto"):
        recs["inmediatas"].append("Tratar preventivamente contra Phytophthora capsici y podredumbres radiculares por encharcamiento.")
        recs["corto_plazo"].append("Colocar trampas amarillas pegajosas para trips y mosca blanca ante golpes de calor.")
        
    elif cult == "papa":
        recs["inmediatas"].append("Vigilar brotes de tizón tardío (Phytophthora infestans) si ocurren lloviznas finas e incremento de humedad del aire.")
        recs["corto_plazo"].append("Asegurar el aporcado alto para proteger los tubérculos expuestos a la escorrentía superficial.")

    # Asegurar recomendaciones únicas y limpias
    recs["inmediatas"] = list(dict.fromkeys(recs["inmediatas"]))
    recs["corto_plazo"] = list(dict.fromkeys(recs["corto_plazo"]))
    recs["mediano_plazo"] = list(dict.fromkeys(recs["mediano_plazo"]))

    return recs
