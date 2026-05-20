import sys
import requests
import json
from .agro_math import CONFIG

# Fix Unicode output on Windows cp1252 terminals
if sys.stdout.encoding and sys.stdout.encoding.lower().startswith('cp'):
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')

def construir_payload_v2(lote_id, cultivo, superficie_ha, fecha_analisis,
                          ndvi_critico, horas_calor,
                          score_total, score_vigor, score_estabilidad,
                          score_limpieza, score_clima,
                          cv_espacial, zona_activa, puntos_zona_c,
                          historial_full=None, version_agroia='2.5', centroide=None):
    """Construye el JSON para la API del RAG."""
    
    # Formatear historial completo si viene
    filas = []
    if historial_full:
        for yr, data in historial_full.items():
            filas.append({
                "anio": int(yr),
                "cultivo": cultivo,
                "ndvi_critico": round(float(data.get('ndvi', 0)), 4),
                "horas_calor": round(float(data.get('horas_calor', 0)), 1),
                "score_total": int(data.get('total', 0)),
                "score_vigor": round(float(data.get('vigor', 0)), 2),
                "score_estabilidad": round(float(data.get('estabilidad', 0)), 2),
                "score_limpieza": round(float(data.get('limpieza', 0)), 2),
                "score_clima": round(float(data.get('clima', 0)), 2),
                "valido_para_score": data.get('valido', True)
            })
    
    print(f"   [DEBUG_INGESTA] Filas generadas: {len(filas)}")
    
    # Construcción del contenido técnico detallado
    lineas_hist = []
    for f in filas:
        lineas_hist.append(
            f"  {f['anio']}: NDVI {f['ndvi_critico']:.3f} | Score {f['score_total']}/100 "
            f"(V:{f['score_vigor']:.1f} E:{f['score_estabilidad']:.1f} L:{f['score_limpieza']:.1f} C:{f['score_clima']:.1f})"
        )
    hist_str = "\n".join(lineas_hist) if lineas_hist else "  Sin historial."

    contenido = (
        f"AgroIA {lote_id.upper()} | {cultivo.upper()} | {superficie_ha:.2f} ha\n"
        f"Score consolidado: {score_total}/100\n"
        f"Componentes: Vigor {score_vigor:.1f}/40 | Estabilidad {score_estabilidad:.1f}/30 | "
        f"Limpieza {score_limpieza:.1f}/20 | Clima {score_clima:.1f}/10\n"
        f"CV espacial: {cv_espacial:.3f} | Zonificación: {'Activa' if zona_activa else 'Homogéneo'}\n"
        f"\nHistorial:\n{hist_str}"
    )
    
    return {
        'lote_id':           lote_id,
        'fecha':             str(fecha_analisis),
        'ndvi_promedio':     round(float(ndvi_critico), 4),
        'gdd_acumulados':    round(float(horas_calor), 1),
        'contenido_tecnico': contenido,
        'metadata': {
            'cultivo':             cultivo,
            'superficie_ha':       round(float(superficie_ha), 2),
            'score_total':         int(score_total),
            'score_desglose': {
                'vigor':       round(float(score_vigor), 2),
                'estabilidad': round(float(score_estabilidad), 2),
                'limpieza':    round(float(score_limpieza), 2),
                'clima':       round(float(score_clima), 2),
            },
            'cv_espacial':         round(float(cv_espacial), 4),
            'zonificacion_activa': bool(zona_activa),
            'puntos_zona_c':       int(puntos_zona_c),
            'version_agroia':      version_agroia,
            'centroide':           centroide,
        },
        'historial_anos': filas,
    }

def enviar_al_rag(api_url, secret_key, payload):
    """Envía el payload a la API local."""
    endpoint = f"{api_url.rstrip('/')}/ingesta"
    headers = {
        'Authorization': f'Bearer {secret_key}',
        'Content-Type': 'application/json'
    }
    try:
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"❌ Error al enviar al RAG: {e}")
        return None
