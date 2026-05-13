# AgroIA - Guia de Uso

## Que es AgroIA

AgroIA es un sistema de diagnostico agronomico que analiza lotes usando imagenes satelitales (Sentinel-2) y datos climaticos (NASA POWER). Genera un Score de 0 a 100 que indica el estado productivo de cada lote, y permite consultar en lenguaje natural sobre los resultados.

Todo corre en tu maquina. No necesitas conexion permanente ni pagas por consulta.

---

## Requisitos

- Windows 10/11, Linux o Mac
- Python 3.10+
- PostgreSQL con extension pgvector
- Ollama instalado con el modelo `gemma3:4b` y `nomic-embed-text`
- 8 GB RAM minimo
- Conexion a internet solo para la descarga inicial de datos satelitales

### Instalacion rapida

```bash
# 1. Clonar el repositorio
git clone https://github.com/sdarionicolas-boop/AgroIA-RAG.git
cd AgroIA-RAG

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar credenciales
# Copiar config/.env.example a config/.env y completar con tus datos:
#   DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
#   GEE_PROJECT_ID (tu proyecto en Google Earth Engine)

# 4. Crear tablas en PostgreSQL
psql -U tu_usuario -d tu_base -f 01_migrate_schema.sql

# 5. Descargar modelos de Ollama
ollama pull gemma3:4b
ollama pull nomic-embed-text

# 6. Verificar que todo esta listo
python start.py --check
```

---

## Como cargar lotes

### Opcion A: Desde un archivo GeoJSON

Si tenes un archivo GeoJSON con los poligonos de tus lotes:

```bash
python start.py --batch-geojson data/mis_lotes.geojson maiz
```

El sistema procesa cada poligono: descarga NDVI historico, calcula estres termico, genera el Score AgroIA y guarda todo en la base de datos.

### Opcion B: Desde Google Colab

Si no tenes los poligonos, podes usar el notebook de Colab para generar los poligonos automaticamente desde puntos GPS:

1. Abri `colab/AGROIA_EXTENSIVOS.ipynb` en Google Colab
2. Subi tu archivo CSV con columnas: `lat`, `lon`, `cultivo`
3. Ejecuta todas las celdas
4. El notebook envia los resultados directamente a tu API local

### Opcion C: Ingesta individual via API

```bash
curl -X POST http://localhost:8000/ingesta \
  -H "Authorization: Bearer TU_CLAVE" \
  -H "Content-Type: application/json" \
  -d '{
    "lote_id": "CAMPO_SUR_01",
    "fecha": "2025-03-15",
    "cultivo": "soja",
    "superficie_ha": 120,
    "ndvi_historico": [0.72, 0.68, 0.75, 0.80, 0.71],
    "gdd_acumulados": 890,
    "metadata": {"localidad": "Balcarce"}
  }'
```

---

## Como usar el Dashboard

Iniciar el dashboard:

```bash
python start.py --ui
```

Abrir en el navegador: `http://localhost:8501`

### Modo 1: Inspeccionar un Lote

1. En el panel izquierdo, selecciona "Inspeccionar un Lote"
2. Elegir el lote del dropdown "Selecciona el lote"
3. Se muestra:
   - **Score AgroIA (0-100):** Calificacion general del lote
   - **NDVI critico:** Valor de NDVI en el mes mas importante para el cultivo
   - **Estres termico:** Horas de calor acumuladas por encima del umbral
   - **CV espacial:** Coeficiente de variacion dentro del lote (heterogeneidad)

4. Desglose del Score:
   - **Vigor (40 puntos):** Basado en el NDVI del mes critico
   - **Estabilidad (30 puntos):** Que tan consistente fue el lote entre campanas
   - **Limpieza IA (20 puntos):** Penaliza si hubo campanas anomalas (nubes, errores)
   - **Clima (10 puntos):** Penaliza por estres termico acumulado

5. Mas abajo: grafico de evolucion historica del NDVI y del score por campana

### Modo 2: Comparar Lote A vs Lote B

1. Selecciona "Comparar Lote A vs B"
2. Elegir dos lotes diferentes
3. El sistema muestra una tabla comparativa y genera un analisis automatico explicando las diferencias

### Modo 3: Ranking Global

1. Selecciona "Ranking Global"
2. Se muestran todos los lotes ordenados por score
3. Grafico de potencial vs. heterogeneidad
4. Podes exportar un PDF comparativo

### Chat con el Asistente AgroIA

En el modo "Inspeccionar un Lote", abajo del grafico historico hay un chat. Ejemplos de preguntas:

- "Por que este lote tiene bajo score de clima?"
- "Que zona deberia priorizar para fertilizacion?"
- "Como evoluciono el NDVI en los ultimos 3 anos?"
- "Que cultivo le conviene a este lote?"

El asistente responde basandose en los datos reales del lote, no inventa.

---

## Como interpretar el Score AgroIA

| Score | Interpretacion | Accion sugerida |
|-------|---------------|-----------------|
| 85-100 | Excelente | Mantener manejo actual |
| 70-84 | Bueno | Monitorear zonas debiles |
| 50-69 | Regular | Revisar fertilizacion y riego |
| 30-49 | Deficiente | Intervencion urgente |
| 0-29 | Critico | Evaluar cambio de uso o cultivo |

### Componentes del Score

**Vigor (40%)** - NDVI del mes critico normalizado. Un NDVI de 0.9 o mas otorga el puntaje maximo.
- Maiz: mes critico = enero (floracion)
- Soja: mes critico = febrero
- Trigo: mes critico = octubre

**Estabilidad (30%)** - Mide la consistencia entre campanas. Si el CV historico es bajo (el lote rinde parejo todos los anos), obtiene puntaje alto.

**Limpieza IA (20%)** - Usa IsolationForest para detectar campanas anomalas. Si una campana tiene valores muy distintos al resto (por nubes, errores de sensor o un evento climatico extremo), se penaliza menos al lote.

**Clima (10%)** - Horas de calor por encima del umbral del cultivo. Maiz y soja: 35C. Trigo: 30C. Menos horas de estres = mejor puntaje.

---

## Zonificacion A/B/C

Cuando el coeficiente de variacion espacial supera 0.05, el sistema divide automaticamente el lote en 3 zonas usando K-means:

- **Zona A (verde):** Mayor vigor, prioridad baja de intervencion
- **Zona B (amarillo):** Vigor medio, monitorear
- **Zona C (rojo):** Menor vigor, puntos criticos que requieren atencion

Los "puntos criticos" detectados en Zona C se reportan en el dashboard.

---

## Reportes

### PDF

El sistema genera reportes PDF automaticos con:
- Datos del lote (cultivo, superficie, localidad)
- Score AgroIA con desglose
- Graficos de NDVI historico
- Mapa del lote con zonificacion
- Tabla de fuentes de datos

### Mapa HTML

Se genera un mapa interactivo offline (archivo .html) que podes abrir en cualquier navegador sin conexion. Util para llevar al campo en una tablet.

---

## Bot de Telegram

Si prefieres consultar desde el celular:

```bash
python start.py --bot
```

Comandos:
- `/start` - Iniciar el bot
- `/lotes` - Ver lista de lotes cargados
- `/lote NOMBRE` - Ver score de un lote especifico
- `/consultar` - Hacer una pregunta al asistente

---

## Solucionar problemas

### "No se pudo conectar a la base de datos"
Verificar que PostgreSQL este corriendo y que las credenciales en `config/.env` sean correctas.

### "Ollama no responde"
Verificar que Ollama este corriendo: `ollama list`. Si no aparecen modelos: `ollama pull gemma3:4b`.

### "GEE authentication failed"
Ejecutar `earthengine authenticate` y seguir las instrucciones del navegador.

### El score de un lote parece incorrecto
Verificar la fecha de los datos. Si el NDVI fue calculado fuera del mes critico del cultivo, el score de Vigor sera bajo. Revisar que el cultivo asignado sea correcto.

### El chat no responde o tarda mucho
El LLM local (Gemma 3 4B) puede tardar 15-70 segundos en responder dependiendo del hardware. Es normal en maquinas sin GPU.

---

## Estructura de archivos

```
AgroIA-RAG/
├── src/
│   ├── streamlit_app.py      Dashboard principal
│   ├── ingesta/api.py        API REST (FastAPI)
│   ├── pipeline/
│   │   ├── agro_math.py      Calculo del Score AgroIA
│   │   ├── gee_extractor.py  Descarga de Sentinel-2
│   │   ├── nasa_power.py     Datos climaticos
│   │   ├── reporter.py       Generacion de PDF y mapas
│   │   └── poligonizador.py  Delineacion automatica (SAM)
│   ├── rag/core.py           Motor de consultas RAG
│   └── bot/telegram_main.py  Bot de Telegram
├── config/.env               Credenciales (no compartir)
├── start.py                  Launcher unificado
└── 01_migrate_schema.sql     Schema de base de datos
```

---

## Contacto

Proyecto desarrollado para el Hackathon Copernicus LAC - Seguridad Alimentaria 2026.

Repositorio: https://github.com/sdarionicolas-boop/AgroIA-RAG
