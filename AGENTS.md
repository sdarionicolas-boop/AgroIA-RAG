# AGENTS.md — AgroIA RAG · Hackathon COPERNICUS LAC 2026
> Documento de referencia técnica para agentes IA y colaboradores.
> Fecha de última actualización: 2026-05-20 — post-enriquecimiento masivo, utilidades de limpieza

---

## 1. ¿Qué es este proyecto?

**AgroIA** es un sistema de diagnóstico agronómico automatizado que combina:
- Imágenes satelitales Sentinel-2 (Copernicus) vía Google Earth Engine
- Datos climáticos de NASA POWER
- Delineado automático de polígonos de lotes con SAM (Segment Anything Model)
- Un motor RAG (Retrieval-Augmented Generation) con PostgreSQL + pgvector + Ollama

El sistema toma como entrada el shapefile de un lote agrícola y produce un informe técnico completo (PDF + mapa HTML interactivo) con Score AgroIA (0–100), serie histórica NDVI, estrés térmico, y zonificación A/B/C. Los resultados se persisten en una base vectorial y son consultables en lenguaje natural desde un dashboard Streamlit o un bot de Telegram.

El proyecto participa en el **Hackathon COPERNICUS LAC 2026**.
**URL del Proyecto:** [AgroIA en TAIKAI](https://taikai.network/en/copernicus/hackathons/copernicus-hackathon-argentina/projects/agroia-risk-score-siniestros-para-agricultura-extensiva)

---

## 2. Mapa de la carpeta

```
AgroIA_RAG HACKATON COPERNICUS/
│
├── AGENTS.md                          ← este archivo
├── start.py                           ← launcher unificado (API + UI + Bot + Pipeline)
├── limpiar_lotes_demo.py              ← utilidad para purgar lotes de prueba de la BD
├── requirements.txt                   ← dependencias Python del backend
├── 01_migrate_schema.sql              ← schema PostgreSQL + pgvector (idempotente)
├── hackaton_final_demo.mp4            ← Video demo principal (entrega final)
│
├── config/
│   ├── .env                           ← variables de entorno (NO commitear)
│   └── config.py                      ← copia/alias de configuración
│
├── src/                               ← BACKEND PRINCIPAL
│   ├── utils/
│   │   ├── config.py                  ← Settings (pydantic-settings) — fuente de verdad
│   │   └── loader.py                  ← insertar_informe() → upsert en BD
│   ├── rag/
│   │   └── core.py                    ← motor de consulta LLM + Contexto
│   ├── ingesta/
│   │   ├── api.py                     ← FastAPI v2.0: /ingesta, /lotes, /ingesta/geojson
│   │   └── app.py                     ← entrada alternativa uvicorn
│   ├── pipeline/                      ← MÓDULO DE ANÁLISIS (v2.5)
│   │   ├── __init__.py                ← run_full_analysis() — punto de entrada
│   │   ├── gee_extractor.py           ← init_gee, NDVI Sentinel-2 SR
│   │   ├── nasa_power.py              ← get_nasa_climate_safe()
│   │   ├── agro_math.py               ← Score AgroIA y validación NDVI
│   │   ├── reporter.py                ← build_report() PDF, generar_mapa_offline() HTML
│   │   ├── comparative_reporter.py    ← Generación de rankings multi-lote
│   │   ├── eventualidades.py          ← Detección de anomalías y granizo (Experimental)
│   │   ├── validator.py               ← Motor de certificación de precisión (Benchmark)
│   │   ├── poligonizador.py           ← Lógica de integración con SAM
│   │   └── ingesta.py                 ← Enviar resultados al RAG
│   ├── bot/
│   │   └── telegram_main.py           ← Bot Telegram con comandos e IA
│   └── streamlit_app.py               ← Dashboard central con mapas e IA
│
├── Poligonizacion/                    ← Sistema de delineado (SAM)
│   ├── poligonizador_final.py         ← Script core de poligonización
│   ├── 1ER CORRIDA/                   ← Polígonos zona TAYPE (Maíz)
│   └── 2DA CORRIDA PIVOTES/           ← Polígonos pivotes (INTA Balcarce)
│
├── data/
│   ├── json_entrante/                 ← GeoJSONs y CSVs de demo
│   └── exports/                       ← exportaciones de la BD
│
├── docs/                              ← Documentación adicional (Guías, comparativas)
└── outputs/                           ← Reportes PDF y Mapas generados
```

---

## 3. Arquitectura del sistema

### Flujo end-to-end (estado actual)

```
[Puntos GPS / shapefile]        [Archivo GeoJSON Masivo]
         │                              │
         ▼ (Automático: SAM)            ▼ (Automático: API)
[Delineado — SAM]             [FastAPI — /ingesta/geojson]
  Output: GeoJSON                       │
         │                              │
         └───────────────┬──────────────┘
                         ▼
[Pipeline de Análisis — MOTOR v2.5]
  python start.py --pipeline <ruta.shp> [cultivo]
  - GEE Sentinel-2 SR (NDVI histórico, últimos 6 años)
  - NASA POWER (estrés térmico)
  - Score AgroIA + Zonificación A/B/C
  - Ingesta AUTOMÁTICA al RAG (pgvector)
```

---

## 4. Base de datos — Schema v2

### Tabla `informes_lotes`
UNIQUE(lote_id). Incluye `metadata` (JSONB) y `embedding` (vector 768).

### Tabla `lote_historial`
Serie temporal. UNIQUE(lote_id, anio). El campo es `anio` (ASCII, sin tilde).

---

## 5. Score AgroIA — Fórmula

```
Score (0-100) = Vigor (40%) + Estabilidad (30%) + Limpieza (20%) + Clima (10%)
```

- **Vigor** — NDVI promedio normalizado (Sentinel-2 SR, mes crítico por cultivo)
- **Estabilidad** — inverso del CV del NDVI histórico
- **Limpieza** — Isolation Forest (contamination=0.2), penaliza outliers
- **Clima** — horas de calor acumuladas (NASA POWER)

---

## 6. API REST — Endpoints

**Base URL:** `http://localhost:8000`
**Auth:** `Authorization: Bearer <INGESTA_SECRET_KEY>`

| Método | Endpoint | Descripción |
|---|---|---|
| POST | /ingesta/geojson | Ingesta masiva desde GeoJSON (asíncrona) |
| GET | /lotes | Lista todos los lotes cargados |
| GET | /lotes/{lote_id} | Informe + historial de un lote |
| DELETE | /lotes/{lote_id} | Borra un lote específico |

---

## 7. Módulo RAG — Funcionamiento

Usa `pgvector` para similitud de coseno y `gemma3:4b` vía Ollama.

Funciones clave de `src/rag/core.py`:
- `consultar_agente(pregunta)` — respuesta LLM con contexto RAG
- `get_datos_lote_raw(lote_id)` — informe consolidado del lote

---

## 8. Launcher Maestro — `start.py`

### Comandos de Ejecución

| Comando | Acción |
|---|---|
| `python start.py` | Levanta API, Streamlit y Bot en paralelo |
| `python start.py --check` | Verifica dependencias, BD, Ollama y GEE |
| `python start.py --pipeline <shp> [cultivo]` | Analiza un lote localmente e ingesta al RAG |
| `python start.py --batch-geojson <file> [cult] [lim]` | Procesa múltiples polígonos desde un GeoJSON |

**Ejemplo Batch:**
```bash
python start.py --batch-geojson "Poligonizacion/2DA CORRIDA PIVOTES/poligonos_definitivos.geojson" maiz 10
```

---

## 9. Datos existentes — Estado actual (2026-05-20)

La base de datos cuenta con **26 lotes enriquecidos** con datos reales (2023-2025).
- **19 lotes** provienen de la carga masiva de polígonos del INTA (prefijo `POLIGONO_`).
- **7 lotes** son casos maestro (TAYPE, El Molino, etc.) con historial completo validado.

---

## 10. Limpieza de Base de Datos

Para mantener la calidad del demo, existe el script `limpiar_lotes_demo.py`:
```bash
python limpiar_lotes_demo.py            # Vista previa de lo que se borraría
python limpiar_lotes_demo.py --confirmar # Ejecuta la limpieza real
```
Borra lotes sin historial, con scores constantes (placeholders) o que contengan "demo/test" en su ID.

---

## 11. Cómo levantar el sistema (Workflow rápido)

1. Asegurar que PostgreSQL y Ollama estén corriendo.
2. `python start.py --check`
3. `python start.py`
4. Acceder al dashboard en `http://localhost:8501`.

---

## 12. Registro de Breaking Changes (v2)

**ASCII Obligatorio:** Todas las claves de los diccionarios de historial deben usar `anio` y `historial_anos`. Las tildes en claves JSON causan fallos en el motor RAG.

---

## 13. Filosofía de Validación (Benchmarking)

Para asegurar la integridad del sistema ante el jurado, se ha adoptado una postura de **"Diferenciación de Métricas"**:
- **NDVI Satelital:** Mide vigor fotosintético (biomasa viva).
- **Tasación Humana:** Mide daño mecánico visual (grano caído, espiga rota).
- **Interpretación del desvío:** En etapas de madurez (ej. Trigo en Noviembre), es normal encontrar una subestimación del satélite frente al reporte de campo. Esto no es un error del sistema, sino la captura de dos fenómenos biológicos distintos. El sistema provee una **segunda opinión objetiva** para mitilar el sesgo humano.

---

## Instrucción clave para Agentes

Priorizá siempre la modularidad de `src/pipeline/`. Si vas a realizar un análisis, usá `run_full_analysis` de `src.pipeline` en lugar de reimplementar la lógica.
Consultá `config/.env` para las credenciales de GEE antes de intentar procesar nuevos lotes.
