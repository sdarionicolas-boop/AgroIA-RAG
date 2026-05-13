# 🔍 DIAGNÓSTICO: Comparativa Resultados vs Validados

## 📊 HALLAZGOS PRINCIPALES

### ❌ Problema 1: Validación Incompleta

**Tu `resultados.geojson` (Notebook Master):**
```
- 454 features (TODOS, incluyendo fallidos)
- Rango de áreas: 0.05 ha → 2380.22 ha ❌
- Promedio: 262.48 ha
- Propiedades: id, area_ha (MÍNIMO)
```

**Validado `pivotes_definitivos.geojson`:**
```
- 340 features (SOLO OK = 85% tasa de éxito)
- Rango de áreas: 30 ha → 157.78 ha ✅
- Promedio: 51.41 ha
- Propiedades: id, area_ha, cultivo, localidad, error_pct, sam_score ✅
```

### 🔴 Problema 2: Falta de Filtrado

El script `poligonizador_final.py` TIENE validación:
```python
min_area_ha: 5
max_area_ha: 800

if real_ha < min_area_ha:
    estado = 'AREA_MIN_FAIL'
elif real_ha > max_area_ha:
    estado = 'SOBRE_SEGMENTADO'
```

Pero tu notebook Master GENERÓ:
- Polígonos de **0.05 ha** (viola min_area_ha=5)
- Polígonos de **2380 ha** (viola max_area_ha=800)

**Conclusión:** El notebook Master NO está aplicando validación correctamente.

### 🔴 Problema 3: Métricas Incompletas

El notebook Master solo guarda:
```json
{"properties": {"id": "0", "area_ha": 36.09}}
```

Debería guardar:
```json
{
  "properties": {
    "id": "0",
    "area_ha": 36.09,
    "sam_score": 0.9621,      // Confianza del modelo
    "error_pct": 9.99,        // Desviación vs entrada
    "cultivo": "riego",       // Tipo de cultivo
    "localidad": "Balcarce"   // Ubicación
  }
}
```

---

## 🛠️ CAUSA RAÍZ

El notebook Master (`AgroIA_Poligonizador_Master.ipynb`) está **incompleto**:

```python
# ❌ LO QUE ESTÁ HACIENDO AHORA:
for idx, row in tqdm(df.iterrows()):
    ndvi, bbox = gee.descargar_ndvi(...)
    res = sam.segmentar(...)
    if res['status'] == 'OK':
        ha = calcular_area_ha(res['poligono'], row['lat'])
        features.append({
            "properties": {"id": idx, "area_ha": ha},  # ❌ FALTA TODO
            "geometry": mapping(res['poligono'])
        })

# ✅ LO QUE DEBERÍA HACER:
for idx, row in tqdm(df.iterrows()):
    ndvi, bbox = gee.descargar_ndvi(...)
    res = sam.segmentar(...)
    if res['status'] == 'OK':
        ha = calcular_area_ha(res['poligono'], row['lat'])
        
        # Validación
        if ha < 5:
            continue  # Descartar
        if ha > 800:
            continue  # Descartar
        
        features.append({
            "properties": {
                "id": row['id'],
                "area_ha": ha,
                "sam_score": res['score'],       # ✅ AGREGAR
                "error_pct": ...,               # ✅ AGREGAR
                "cultivo": row['cultivo'],      # ✅ AGREGAR
                "localidad": row['localidad']   # ✅ AGREGAR
            },
            "geometry": mapping(res['poligono'])
        })
```

---

## 🚨 ERRORES QUE PROBABLEMENTE VISTE

1. **KeyError: 'cultivo'** → Esperaba cultivo en datos de entrada
2. **KeyError: 'localidad'** → Esperaba localidad en datos de entrada
3. **TypeError en SAM** → Errores de formato de imagen
4. **GEE timeout** → Intentos múltiples fallaron

---

## ✅ SOLUCIÓN RECOMENDADA

### PASO 1: Usar el script productivo (Opción A - Rápido)

```bash
# En tu máquina local (sin GPU needed - GEE es cloud)
python poligonizador_final.py --csv datos.xlsx --output resultados_v2

# Salida esperada:
# resultados_v2.geojson       (340 polígonos válidos + métricas)
# resultados_v2_log.csv       (métricas por punto)
# resultados_v2_mapa.html     (visualización)
```

**Ventajas:**
- ✅ Ya está validado (TAYPE + INTA)
- ✅ Completo con todas las métricas
- ✅ Python puro, sin Colab
- ✅ Funciona sin GPU (GEE es cloud)

### PASO 2: Integrar en src/ (Opción B - Correcto)

```
src/pipeline/poligonizador/
├── __init__.py
├── core.py                   # Lógica SAM + GEE
├── config.py                 # Configuración
├── validator.py              # Validaciones
├── reporter.py               # Generación de reportes
└── cli.py                    # CLI
```

### PASO 3: Conectar con pipeline principal

```python
# src/pipeline/ingesta.py
from src.pipeline.poligonizador import Poligonizador

# En pipeline de análisis:
poligonizador = Poligonizador()
polygon = poligonizador.delinear(lat, lon, area_ref)  # Punto GPS → Polígono
```

---

## 📋 CHECKLIST: PRÓXIMOS PASOS

- [ ] **Descarga `poligonizador_final.py` a tu máquina**
- [ ] **Instala dependencias:** `pip install segment-anything earthengine-api geopandas`
- [ ] **Ejecuta con datos INTA:** `python poligonizador_final.py --csv <tu_archivo> --output test_v1`
- [ ] **Compara resultados:** `resultados_v1.geojson` vs `2DA CORRIDA PIVOTES/poligonos_definitivos.geojson`
- [ ] **Si funciona:** Integra en `src/pipeline/poligonizador.py`
- [ ] **Si no funciona:** Reporta error específico con el output del script

---

## 🔧 CONFIGURACIÓN REQUERIDA

Asegúrate que `poligonizador_final.py` tenga:

```python
Config.GEE = {
    'project': 'applied-oxygen-459415-e2',  # ✅ Ya configurado
    'buffer_m': 2500,
    'max_nubes_pct': 20,
    'dias_ventana': 60,
}

Config.POLYGON = {
    'min_area_ha': 5,       # No procesas lotes < 5 ha
    'max_area_ha': 800,     # No procesas lotes > 800 ha
    'tolerancia_fuga': 1.5, # Penalización por sobre-segmentación
}
```

---

## 📌 NOTA SOBRE GPU

**NO NECESITAS GPU porque:**
- Google Earth Engine → Corre en servidores Google (cloud)
- SAM → Funciona en CPU (lento pero funciona)
- NASA POWER → API cloud

**GPU solo ACELERA SAM:**
- CPU: ~3-5 segundos por polígono
- GPU (T4 Colab): ~1-2 segundos por polígono

Para 454 lotes:
- CPU: ~30 min
- GPU: ~15 min

**Recomendación:** Usa Colab SOLO para corridas grandes (1000+ lotes).
Para integración en producción: Script Python local + cron job.

