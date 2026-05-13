import pytest
import numpy as np
from src.pipeline.agro_math import calcular_score

def test_calcular_score_basico():
    """Verifica que el score se calcule correctamente con datos controlados."""
    # ndvi_critico=0.81 (vigor max ~40), horas_calor=0 (clima max 10)
    # ndvi_historico estable (estabilidad max 30, limpieza max 20)
    ndvi_hist = [0.8, 0.81, 0.79, 0.80, 0.82]
    res = calcular_score(ndvi_critico=0.81, horas_calor=0, ndvi_historico=ndvi_hist)
    
    assert res['total'] >= 85
    assert res['vigor'] > 35
    assert res['clima'] == 10.0

def test_calcular_score_bajo_potencial():
    """Verifica que un lote con bajo NDVI y estrés térmico tenga score bajo."""
    # ndvi_critico=0.3, horas_calor=50 (excede umbral 40)
    ndvi_hist = [0.3, 0.35, 0.28, 0.32]
    res = calcular_score(ndvi_critico=0.3, horas_calor=50, ndvi_historico=ndvi_hist)
    
    assert res['total'] < 50
    assert res['vigor'] < 15
    assert res['clima'] == 0.0

def test_limpieza_ia_detecta_outliers():
    """Verifica que IsolationForest detecta anomalías en el historial."""
    # Historial estable con un outlier extremo (0.05)
    ndvi_hist = [0.75, 0.76, 0.74, 0.75, 0.77, 0.05]
    res = calcular_score(ndvi_critico=0.75, horas_calor=0, ndvi_historico=ndvi_hist)
    
    # La limpieza debería penalizar el outlier
    assert res['limpieza'] < 20.0
    print(f"Score Limpieza con outlier: {res['limpieza']}")

def test_estabilidad_con_pocos_datos():
    """Verifica el comportamiento con historial insuficiente."""
    res = calcular_score(ndvi_critico=0.8, horas_calor=10, ndvi_historico=[0.8])
    # Debe devolver valores por defecto para estabilidad (15) y limpieza (10)
    assert res['estabilidad'] == 15.0
    assert res['limpieza'] == 10.0
