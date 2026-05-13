import numpy as np
from sklearn.ensemble import IsolationForest
from .gee_extractor import get_gee_ndvi

CONFIG = {
    "maiz": {
        "tbase":         10,
        "umbral_calor":  35,
        "umbral_clima":  40,
        "mes_critico":   1,
        "pesos": {10: 0.2, 11: 0.5, 12: 1.0, 1: 1.0, 2: 0.6, 3: 0.2},
        "color":         "#2D6A4F",
        "biblio":        "INTA Marcos Juárez / Univ. Nebraska",
        "ndvi_min":      0.25,
        "ndvi_max":      0.92,
    },
    "soja": {
        "tbase":         10,
        "umbral_calor":  35,
        "umbral_clima":  35,
        "mes_critico":   2,
        "pesos": {11: 0.3, 12: 0.7, 1: 1.0, 2: 1.0, 3: 0.5, 4: 0.2},
        "color":         "#40916C",
        "biblio":        "INTA Marcos Juárez",
        "ndvi_min":      0.25,
        "ndvi_max":      0.90,
    },
    "trigo": {
        "tbase":         0,
        "umbral_calor":  30,
        "umbral_clima":  30,
        "mes_critico":   10,
        "pesos": {6: 0.1, 7: 0.2, 8: 0.4, 9: 1.0, 10: 1.0, 11: 0.5},
        "color":         "#C29B0C",
        "biblio":        "INTA Pergamino",
        "ndvi_min":      0.20,
        "ndvi_max":      0.88,
    },
}

def validar_ndvi(ndvi_val, cultivo, year, mes):
    """Valida que el NDVI esté dentro de rangos plausibles."""
    conf = CONFIG.get(cultivo, CONFIG['maiz'])
    if ndvi_val is None or (isinstance(ndvi_val, float) and np.isnan(ndvi_val)):
        return 'nulo', f"⚠ {year}/{mes:02d}: NDVI nulo."
    if ndvi_val < conf['ndvi_min']:
        return 'sospechoso_bajo', f"⚠ {year}/{mes:02d}: NDVI={ndvi_val:.3f} bajo mínimo."
    if ndvi_val > conf['ndvi_max']:
        return 'sospechoso_alto', f"⚠ {year}/{mes:02d}: NDVI={ndvi_val:.3f} muy alto."
    return 'ok', f"✓ {year}/{mes:02d}: NDVI={ndvi_val:.3f} plausible."

def get_gee_ndvi_validado(geom_ee, year, month, cultivo):
    """Obtiene NDVI de GEE y lo valida inmediatamente."""
    try:
        val = get_gee_ndvi(geom_ee, year, month)
        status, msg = validar_ndvi(val, cultivo, year, month)
        if status in ('sospechoso_bajo', 'nulo'): return None, status, msg
        return val, status, msg
    except Exception as e: return None, 'error', f"Error GEE: {e}"

def calcular_score(ndvi_critico, horas_calor, ndvi_historico, umbral_clima=40):
    """Calcula el Score AgroIA (0-100)."""
    vigor = np.clip(ndvi_critico / 0.9, 0.0, 1.0) * 40.0
    if len(ndvi_historico) >= 3:
        arr = np.array(ndvi_historico)
        cv  = np.std(arr) / np.mean(arr) if np.mean(arr) > 0 else 1.0
        estabilidad = np.clip(1.0 - cv / 0.45, 0.0, 1.0) * 30.0
    else: estabilidad = 15.0
    
    if len(ndvi_historico) >= 4:
        iso = IsolationForest(contamination=0.2, random_state=42)
        labels = iso.fit_predict(np.array(ndvi_historico).reshape(-1, 1))
        limpieza = np.clip(1.0 - (labels == -1).sum() / len(labels) / 0.40, 0.0, 1.0) * 20.0
    else: limpieza = 10.0
    
    clima = np.clip(1.0 - horas_calor / umbral_clima, 0.0, 1.0) * 10.0
    total = int(round(vigor + estabilidad + limpieza + clima))
    
    return {"total": total, "vigor": round(vigor, 1), "estabilidad": round(estabilidad, 1),
            "limpieza": round(limpieza, 1), "clima": round(clima, 1)}
