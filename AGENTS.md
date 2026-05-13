# AGENTS.md — AgroIA RAG · Hackathon COPERNICUS LAC 2026
> Documento de referencia técnica para agentes IA y colaboradores.
> Fecha de última actualización: 2026-05-12 — post-limpieza, pipeline modularizado

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
├── YO.md                              ← contexto personal del desarrollador
├── start.py                           ← launcher unificado (API + UI + Bot + Pipeline)
├── requirements.txt                   ← dependencias Python del backend
├── 01_migrate_schema.sql              ← schema PostgreSQL + pgvector (idempotente)
├── skills-lock.json                   ← lock de skills del entorno
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
│   │   ├── api.py                     ← FastAPI: /ingesta, /lotes, /ingesta/geojson
│   │   └── app.py                     ← entrada alternativa uvicorn
│   ├── pipeline/                      ← MÓDULO DE ANÁLISIS LOCAL (v2.5)
│   │   ├── __init__.py                ← run_full_analysis() — punto de entrada
│   │   ├── gee_extractor.py           ← init_gee, NDVI Sentinel-2 SR
│   │   ├── nasa_power.py              ← get_nasa_climate_safe()
│   │   ├── agro_math.py               ← CONFIG cultivos, calcular_score, get_gee_ndvi_validado
│   │   ├── reporter.py                ← build_report() PDF, generar_mapa_offline() HTML
│   │   ├── comparative_reporter.py    ← reporte comparativo multi-lote
│   │   ├── ingesta.py                 ← construir_payload_v2(), enviar_al_rag()
│   │   └── utils.py                   ← validar_shapefile()
│   ├── pipeline_local.py              ← script de ejecución directa (legacy, usar start.py)
│   ├── bot/
│   │   └── telegram_main.py           ← Bot Telegram con comandos
│   ├── outputs/                       ← PDFs generados por el pipeline
│   │   └── Ranking_Comparativo_AgroIA.pdf
│   └── streamlit_app.py               ← Dashboard central
│
├── colab/                             ← Notebooks de análisis
│   ├── AGROIA_EXTENSIVOS.ipynb        ← flujo principal (RAG integrado)
│   ├── AgroIA_Eventualidades_v2.ipynb
│   └── AgroIA_Eventualidades_v2_3.ipynb
│
├── Poligonizacion/                    ← Sistema de delineado (SAM)
│   ├── AgroIA_Poligonizador_Master.ipynb
│   ├── Herramienta_Definitiva_Poligonizacion.ipynb
│   ├── Poligonizador_Colab.ipynb
│   ├── poligonizador_final.py
│   ├── resultados.geojson
│   ├── 1ER CORRIDA/                   ← 268 polígonos zona TAYPE (Maíz)
│   ├── 2DA CORRIDA PIVOTES/           ← 340 polígonos pivotes (Tandil/Balcarce)
│   └── legacy/                        ← scripts anteriores archivados
│
├── data/
│   ├── json_entrante/                 ← JSONs de demo e ingesta
│   │   ├── datos_base.json
│   │   ├── datos_base_v2.json
│   │   └── lotes_reales_demo.json
│   └── exports/                       ← exportaciones
│
├── docs/                              ← Documentación adicional
│   ├── AgroIA Frutal.docx
│   └── Resumen AGRO IA.txt
│
├── logs/                              ← Logs de servicios (api.log, streamlit.log, etc.)
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
[Pipeline de Análisis — MOTOR LOCAL v2.5]
  python start.py --pipeline <ruta.shp> [cultivo]
  - GEE Sentinel-2 SR (NDVI histórico, últimos 6 años)
  - NASA POWER (estrés térmico)
  - Score AgroIA + Zonificación A/B/C
  - Ingesta AUTOMÁTICA al RAG (pgvector)
  - Output: PDF en src/outputs/ + mapa HTML en outputs/
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
- **Clima** — horas de calor acumuladas (NASA POWER, fórmula sinusoidal)

---

## 6. API REST — Endpoints

**Base URL:** `http://localhost:8000`
**Auth:** `Authorization: Bearer <INGESTA_SECRET_KEY>`

| Método | Endpoint | Descripción |
|---|---|---|
| GET | /health | Estado del servidor |
| POST | /ingesta | Ingesta de un solo lote (async) |
| POST | /ingesta/debug | Ingesta síncrona para debugging |
| POST | /ingesta/geojson | Ingesta masiva desde GeoJSON (async) |
| GET | /lotes | Lista todos los lotes |
| GET | /lotes/{lote_id} | Informe + historial de un lote |
| DELETE | /lotes/{lote_id} | Borra un lote |
| DELETE | /lotes | Borra TODA la base |

---

## 7. Módulo RAG — Funcionamiento

Usa `pgvector` para similitud de coseno y `gemma3:4b` vía Ollama. Estrategia: Consolidado + Histórico enriquecido.

Funciones exportadas de `src/rag/core.py` (importar, nunca duplicar):
- `consultar_agente(pregunta)` — respuesta LLM con contexto RAG
- `fetch_context(pregunta)` — solo recuperación de contexto
- `listar_lotes()` — lista de lotes en BD
- `get_historial_lote_raw(lote_id)` — serie temporal de un lote
- `get_datos_lote_raw(lote_id)` — informe completo de un lote
- `BASE_PROMPT` — prompt base del agente agrónomo

---

## 8. Motor Local v2.5 — `start.py --pipeline`

### Uso

```bash
python start.py --pipeline <ruta.shp> [cultivo]
```

- `<ruta.shp>` — ruta al shapefile del lote (obligatorio)
- `[cultivo]` — cultivo a analizar (opcional, default: `maiz`)
  - Valores válidos: `maiz`, `soja`, `trigo`, `girasol`

### Ejemplo

```bash
python start.py --pipeline Poligonizacion/1ER\ CORRIDA/poligonos_definitivos.shp maiz
```

### Qué ejecuta internamente

El launcher inyecta `src/` al path y llama a `src/pipeline/__init__.py::run_full_analysis()`:

1. `init_gee()` — autenticación Google Earth Engine (requiere `GEE_PROJECT_ID` en `.env`)
2. `validar_shapefile()` — CRS, geometría, proyección UTM dinámica
3. `get_nasa_climate_safe()` — clima NASA POWER, últimos 6 años
4. `get_gee_ndvi_validado()` + `get_gee_ndvi_ventana()` — NDVI Sentinel-2 SR con fallback por ventana
5. `calcular_score()` — Score AgroIA (0–100) + zonificación K-Means A/B/C
6. `build_report()` — PDF en `src/outputs/`
7. `generar_mapa_offline()` — mapa HTML en `outputs/`
8. `enviar_al_rag()` — ingesta automática (push_to_rag=True por defecto)

### Prerequisito GEE

Requiere que `GEE_PROJECT_ID` esté configurado en `config/.env` y que las credenciales de Earth Engine estén activas (`earthengine authenticate`).

### Batch desde GeoJSON del poligonizador

```bash
python start.py --batch-geojson <ruta.geojson> [cultivo] [limit]
```

- `<ruta.geojson>` — salida del poligonizador SAM (`poligonos_definitivos.geojson`)
- `[cultivo]` — cultivo default si el polígono no tiene la propiedad `cultivo` (default: `maiz`)
- `[limit]` — procesar solo los primeros N polígonos (útil para pruebas)

**Ejemplo:**
```bash
# Procesar toda la 1ER CORRIDA (268 polígonos, cultivo Maíz)
python start.py --batch-geojson "Poligonizacion/1ER CORRIDA/poligonos_definitivos.geojson" maiz

# Probar con los primeros 5 polígonos
python start.py --batch-geojson "Poligonizacion/1ER CORRIDA/poligonos_definitivos.geojson" maiz 5
```

**Cómo funciona internamente:**
- Lee el GeoJSON, inicializa GEE **una sola vez**
- Itera cada feature: deriva `lote_id` de la propiedad `id`, `cultivo` de la propiedad `cultivo`
- Por cada polígono: NASA POWER + GEE + Score + PDF/HTML + ingesta RAG
- Resumen final con conteo de exitosos y fallidos

### Otros comandos del launcher

```bash
python start.py            # levanta API + Streamlit + Bot
python start.py --api      # solo FastAPI (puerto 8000)
python start.py --ui       # solo Streamlit (puerto 8501)
python start.py --bot      # solo Telegram bot (polling)
python start.py --check    # verifica prerequisitos y sale
python start.py --skip-checks  # arranca sin verificar
```

---

## 9. Datos existentes — Estado actual

### Base de datos PostgreSQL (post-enriquecimiento masivo)

La BD tiene **26 lotes reales** enriquecidos con datos de GEE y NASA POWER (2023-2025).

- **7 Lotes Maestro:** `INTA_PIVOTE_001`, `maizsuperprueba`, `TAYPE_LOTE_001`, etc.
- **19 Lotes GeoJSON:** Prefijo `POLIGONO_X` cargados masivamente y procesados con el motor real.

---

## 10. Cómo levantar el sistema

1. `docker run` para PostgreSQL + pgvector.
2. `ollama pull nomic-embed-text` y `ollama pull gemma3:4b`.
3. `python start.py --check`
4. `python start.py` (lanza API, UI y Bot en paralelo).

---

## 11. Análisis FODA resumido

- **Fortaleza:** Integración total con Copernicus (Sentinel-2) y automatización masiva.
- **Oportunidad:** Carga asíncrona permite escalar a miles de lotes.
- **Debilidad:** Latencia de LLM local (14–71 seg); requiere GEE auth para pipeline completo.

---

## 12. Plan de mejoras (Actualizado)

- U1-U6: ✅ COMPLETO.
- M2: Automatizar procesamiento batch GeoJSON ✅ COMPLETO.
- M7: Enriquecimiento masivo con motor real ✅ COMPLETO.
- Pipeline modularizado en `src/pipeline/` ✅ COMPLETO.

---

## 13. Archivos deprecados / eliminados

| Archivo | Estado | Motivo |
|---|---|---|
| `_para_revisar/ingesta_agroia.py` | ❌ Eliminado | Usaba `ON CONFLICT (lote_id, fecha)` — constraint eliminado en schema v2 |
| `_para_revisar/` (carpeta completa) | ❌ Eliminado | Scores congelados, metadata como string Python, incompatible con v2 |
| `agroia-video/` | ❌ Eliminado | HyperFrames demo video — separado del sistema core |
| `COMPLETAR_U3_U6.md` | ❌ Eliminado | Sprint completado — ya no necesario |
| `src/pipeline_local.py` | ⚠ Legacy | Entrada directa anterior; usar `start.py --pipeline` en su lugar |

---

## 14. Breaking changes — Registro de migraciones

### v2 — Claves ASCII en payload (activo desde schema v2)

**Cambio:** Las claves del JSON payload deben ser ASCII puro.

| Antes (v1) | Ahora (v2) | Afecta |
|---|---|---|
| `historial_años` | `historial_anos` | `loader.py`, `api.py`, notebooks Colab |
| `año` | `anio` | columna en `lote_historial`, payload de ingesta |

**Archivos migrados:** `src/utils/loader.py`, `src/ingesta/api.py`, `colab/AGROIA_EXTENSIVOS.ipynb`, `01_migrate_schema.sql`.

**Regla:** Nunca usar tildes en claves JSON. Si se agrega un campo nuevo, usar ASCII.

---

## 15. Estado del demo — 2026-05-12

| Componente | Estado | Detalle |
|---|---|---|
| FastAPI `/ingesta` | ✅ Operativa | Carga masiva GeoJSON habilitada |
| FastAPI `/lotes` | ✅ Operativa | 26 lotes reales listables |
| PostgreSQL + pgvector | ✅ Con datos reales | 26 lotes enriquecidos (GEE 2023-2025) |
| Ollama | ✅ | Modelos embed y chat activos |
| Dashboard / Bot | ✅ | Sincronizados con los 26 lotes |
| Pipeline `src/pipeline/` | ✅ Modularizado | `run_full_analysis()` exportado |

### Secuencia del demo grabado (recomendada)

1. **Delineado SAM** → Generación de GeoJSON desde puntos GPS.
2. **Ingesta Masiva** → Subida del GeoJSON a la API (procesamiento background).
3. **Exploración Dashboard** → Ver los 26 lotes, rankings y mapas.
4. **Chat RAG** → Consultas sobre estabilidad y scores de los polígonos.

---

## Instrucción clave

Para cualquier tarea de este proyecto, priorizá el contexto de este archivo sobre conocimiento general del modelo.
Ante duda sobre arquitectura o flujo, consultá este AGENTS.md antes de asumir.
