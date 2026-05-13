# 🚀 GUÍA: Ejecutar Poligonización SIN GPU

## 📋 Resumen

El nuevo módulo `src/pipeline/poligonizador.py` funciona sin GPU porque:

- **Google Earth Engine** (Sentinel-2) → Corre en servidores Google ☁️
- **SAM** → Funciona en CPU (más lento pero funciona) ⚙️
- **Cálculos locales** → Numpy, pandas, etc. 💻

**Tiempo esperado (CPU):** ~3-5 segundos por polígono = ~30-45 min para 454 lotes

---

## ✅ PASO 1: Requisitos

### 1.1 Dependencias Python

```bash
# En tu terminal (desde la carpeta del proyecto)
pip install segment-anything earthengine-api geopandas shapely opencv-python-headless torch pandas numpy tqdm

# Verificar instalación
python -c "import ee; import torch; print(f'PyTorch device: {torch.device(\"cpu\")}')"
```

### 1.2 Autenticación Google Earth Engine (UNA SOLA VEZ)

```bash
earthengine authenticate
```

Esto abre un navegador → autentícate con tu cuenta Google → copia el token → pégalo en la terminal.

**Importante:** Necesitas:
- Cuenta Google (gmail)
- Acceso a Google Earth Engine (libre, pero requiere registro)

Si no tienes: https://signup.earthengine.google.com/

---

## ✅ PASO 2: Preparar Datos de Entrada

### 2.1 Formato esperado (CSV o XLSX)

Necesita estas columnas (flexible, el script las detecta):

```
| id    | latitude | longitude | fecha      | cultivo | localidad   |
|-------|----------|-----------|------------|---------|-------------|
| 1     | -38.037  | -58.289   | 2024-06-15 | riego   | Balcarce    |
| 2     | -37.818  | -57.957   | 2024-06-20 | soja    | Tres Arroyos|
| 3     | -37.915  | -58.107   | 2024-06-22 | maíz    | Mar Chiquita|
```

**Columnas reconocidas:**
- ID: `id`, `taype`, `numero`, `lote_id`
- Latitud: `lat`, `latitude`, `lat_dec`, `y`
- Longitud: `lon`, `longitude`, `lon_dec`, `x`
- Cultivo: `cultivo`, `crop`, `especie`
- Localidad: `localidad`, `location`, `ciudad`

### 2.2 Dónde poner el archivo

Opción A (Recomendado - uso de CLI):
```
C:\Users\sdari\Desktop\AgroIA_RAG HACKATON COPERNICUS\
├── data/
│   └── mis_lotes.xlsx  ← Pon aquí tu archivo
```

Opción B (Prueba rápida):
```
Poligonizacion/
├── mis_lotes.xlsx  ← Pon aquí
```

---

## ✅ PASO 3: Ejecutar el Script

### 3.1 Usar el módulo integrado (RECOMENDADO)

```bash
# Desde la raíz del proyecto
python -m src.pipeline.poligonizador --csv data/mis_lotes.xlsx --output resultados_v2

# Salidas generadas:
# resultados_v2.geojson          (Polígonos con todas las métricas)
# resultados_v2_log.csv          (Detalles por punto)
```

### 3.2 Usar directamente desde Poligonizacion/

```bash
cd Poligonizacion/
python poligonizador_final.py --csv mis_lotes.xlsx --output test_v1

# Salidas:
# test_v1.geojson
# test_v1_log.csv
# test_v1_mapa.html
```

### 3.3 Desde Python (Importar como módulo)

```python
from src.pipeline.poligonizador import Poligonizador

# Crear instancia
poly = Poligonizador()

# Cargar datos
if poly.cargar_datos('data/mis_lotes.xlsx'):
    # Ejecutar
    if poly.ejecutar(output_prefix='mi_resultado'):
        print("✅ Éxito")
        print(f"Polígonos válidos: {len(poly.features)}")
    else:
        print("❌ Error en ejecución")
else:
    print("❌ Error cargando datos")
```

---

## 🔍 PASO 4: Verificar Resultados

### 4.1 Comparar con validados

```bash
# Compara tu resultado con el de INTA (que sabemos que está bien)
python << 'EOF'
import json
import pandas as pd

# Cargar tus resultados
with open('resultados_v2.geojson') as f:
    mis_resultados = json.load(f)

# Cargar validados
with open('Poligonizacion/2DA CORRIDA PIVOTES/poligonos_definitivos.geojson') as f:
    validados = json.load(f)

print(f"Tus polígonos: {len(mis_resultados['features'])}")
print(f"Validados: {len(validados['features'])}")

# Estadísticas
mis_areas = [f['properties']['area_ha'] for f in mis_resultados['features']]
val_areas = [f['properties']['area_ha'] for f in validados['features']]

print(f"\nTus áreas: min={min(mis_areas):.1f}, max={max(mis_areas):.1f}, promedio={sum(mis_areas)/len(mis_areas):.1f} ha")
print(f"Validadas: min={min(val_areas):.1f}, max={max(val_areas):.1f}, promedio={sum(val_areas)/len(val_areas):.1f} ha")

# Verificar propiedades
mis_props = set(mis_resultados['features'][0]['properties'].keys())
val_props = set(validados['features'][0]['properties'].keys())

print(f"\nPropiedades que tienes: {mis_props}")
print(f"Propiedades que deberías tener: {val_props}")
print(f"¿Falta algo?: {val_props - mis_props}")

EOF
```

### 4.2 Ver el mapa

```bash
# Abrir en navegador
start resultados_v2_mapa.html
```

O si está en Poligonizacion/:
```bash
start Poligonizacion/test_v1_mapa.html
```

### 4.3 Verificar métricas

```bash
# Ver el CSV de log
head -20 resultados_v2_log.csv

# Análisis rápido
python << 'EOF'
import pandas as pd

df = pd.read_csv('resultados_v2_log.csv')

# Estados
print("Estados:")
print(df['estado'].value_counts())
print()

# SAM scores
print(f"SAM score promedio (OK): {df[df['estado']=='OK']['score'].mean():.3f}")
print(f"Error % promedio: {df[df['estado']=='OK']['error_pct'].mean():.1f}%")

EOF
```

---

## ⚠️ ERRORES COMUNES Y SOLUCIONES

### Error: "No module named 'segment_anything'"
```bash
pip install segment-anything
```

### Error: "earthengine: command not found"
```bash
pip install --upgrade earthengine-api
earthengine authenticate  # Luego autentica
```

### Error: "GDAL/fiona issues"
```bash
# En Windows, a veces ayuda:
pip install --upgrade gdal fiona geopandas
```

### Error: "GEE authentication failed"
```bash
# Verifica credenciales
earthengine whoami

# Si falla, re-autentica
earthengine authenticate
```

### Error: "No images found"
- Posiblemente la fecha está fuera de rango
- Intenta con fechas más recientes (últimos 60 días)
- Verifica que las coordenadas estén en Argentina

### Error: "SAM model download failed"
```bash
# Descarga manual
wget https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth

# Mueve a la carpeta del script
mv sam_vit_b_01ec64.pth .
```

---

## 📊 INTERPRETACIÓN DE SALIDAS

### resultados_v2.geojson

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {
        "id": "1",
        "area_ha": 36.09,           ← Área calculada
        "sam_score": 0.9621,        ← Confianza SAM (0-1, idealmente >0.85)
        "error_pct": 9.99,          ← Error vs área entrada (idealmente <30%)
        "cultivo": "riego",
        "localidad": "Balcarce"
      },
      "geometry": {
        "type": "Polygon",
        "coordinates": [...]        ← Polígono georreferenciado
      }
    }
  ]
}
```

### resultados_v2_log.csv

```
id,estado,area_ha,score,error_pct,segundos
1,OK,36.09,0.9621,9.99,3.2
2,OK,32.15,0.9662,0.01,3.4
3,AREA_MINIMA_FAIL,2.50,0.8945,75.0,2.1
4,SOBRE_SEGMENTADO,850.45,0.8102,250.0,2.5
```

**Estados:**
- `OK` = Válido, guardado en GeoJSON
- `SIN_IMAGEN` = No encontró imagen limpia en la fecha
- `AREA_MINIMA_FAIL` = Área < 5 ha (descartado)
- `SOBRE_SEGMENTADO` = Área > 800 ha (descartado)
- `SIN_CONTORNO` = SAM no encontró borde definido
- `CONTORNO_INVALIDO` = Geometría corrupta

---

## 🎯 BENCHMARK ESPERADO

Con CPU (sin GPU):

| Métrica | Esperado |
|---------|----------|
| Tiempo por polígono | 3-5 segundos |
| Total para 454 lotes | 30-45 minutos |
| SAM score promedio | 0.92+ |
| Hit rate (OK) | 75-85% |
| Error promedio | <20% |

---

## ✅ CHECKLIST: PRIMERAS PRUEBAS

- [ ] Instalé dependencias (`pip install ...`)
- [ ] Autentiqué GEE (`earthengine authenticate`)
- [ ] Preparé archivo CSV/XLSX con mis datos
- [ ] Ejecuté: `python -m src.pipeline.poligonizador --csv data/mis_lotes.xlsx --output test1`
- [ ] Verifiqué que generó: `test1.geojson`, `test1_log.csv`
- [ ] Comparé con validados (propiedades y estadísticas)
- [ ] SAM score está > 0.85
- [ ] Error % está < 30%

Si todo pasó ✅ → Pipeline listo para integración

Si algo falló → Reporta el error exacto y el archivo CSV de entrada

---

## 🔗 PRÓXIMO PASO

Una vez que esto funcione:

1. Integrar en **API FastAPI** (`src/ingesta/api.py`)
2. Crear **task queue** (Celery) para procesamiento asincrónico
3. Conectar con **RAG** para consultas de resultados
4. Dashboard de monitoreo

¿Necesitas ayuda con alguno de estos pasos?

