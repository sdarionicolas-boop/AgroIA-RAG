# 🌾 AgroIA — Diagnóstico Agronómico con Inteligencia Artificial

> **"Un punto GPS. Un Score Agronómico. Decisiones basadas en datos en 60 segundos."**

[![Hackathon](https://img.shields.io/badge/Hackathon-CopernicusLAC%20%233-blue)](https://taikai.network/en/copernicus/hackathons/copernicus-hackathon-argentina/projects/agroia-risk-score-siniestros-para-agricultura-extensiva)
[![Docker](https://img.shields.io/badge/Docker-Ready-green)](./docker-compose.yml)
[![Tests](https://img.shields.io/badge/Tests-Passing-brightgreen)](./tests/test_agro_math.py)

## ⚠️ El Problema
En Latinoamérica, la falta de un sistema público de identificación de parcelas (LPIS) genera una fricción enorme para el sector AgTech y de Seguros. Digitalizar un lote manualmente toma entre **15 y 30 minutos**, es propenso a errores y costoso. Sin una delineación precisa, el análisis satelital pierde su valor.

## 💡 La Solución
**AgroIA** automatiza el ciclo de vida del diagnóstico agronómico, desde la delineación hasta el análisis experto:

1.  **Delineación Automática (SAM + Sentinel-2)**: De un punto GPS a un polígono preciso en segundos.
2.  **Motor de Score AgroIA (0-100)**: Evaluación multivariable (Vigor, Estabilidad, Limpieza IA y Clima).
3.  **Soberanía de Datos**: Todo el procesamiento (incluyendo el LLM vía Ollama) puede ejecutarse de forma local o en entornos controlados.
4.  **Asistente RAG**: Un experto agronómico digital que "conoce" la historia de cada lote.

## 🚀 Impacto Real
- ⏱️ **60 segundos** de procesamiento total vs **30 min** manual.
- 🎯 **75% de precisión** en delineación automática (validado con INTA Balcarce).
- 📉 **Detección de Anomalías**: Uso de `IsolationForest` para filtrar fallas satelitales (nubes/errores).
- 🌍 **Escalabilidad**: Listo para procesar miles de lotes de forma asíncrona.

---

## 🛠️ Quick Start (Docker)

Para levantar todo el ecosistema (Postgres + API + Dashboard):

```bash
# 1. Clonar el repositorio
git clone https://github.com/tu-usuario/agroia-rag.git
cd agroia-rag

# 2. Configurar el entorno (ajustar .env con tus credenciales de GEE)
cp config/.env.example config/.env

# 3. Levantar con Docker Compose
docker-compose up -d
```

- **Dashboard Streamlit**: `http://localhost:8501`
- **Documentación API**: `http://localhost:8000/docs`

---

## 🏗️ Arquitectura Técnica

- **Backend**: FastAPI + Python 3.10.
- **Base de Datos**: PostgreSQL + `pgvector` para el motor de búsqueda semántica.
- **Satélite**: Google Earth Engine (Sentinel-2 SR) + NASA POWER.
- **IA/ML**: 
    - **SAM (Segment Anything Model)** para visión.
    - **IsolationForest** para limpieza de outliers.
    - **Gemma 3 (Ollama)** para el motor RAG.
- **Frontend**: Streamlit + Bot de Telegram.

---

## 📊 Métricas de Validación
El sistema ha sido validado contra datasets reales de **TAYPE Siniestros** (313 puntos) e **INTA Balcarce** (454 puntos):
- **Hit Rate**: 85.6% (TAYPE) / 74.9% (INTA).
- **SAM Score promedio**: 0.962.
- **Error de área promedio**: 9.8% vs referencia manual.

---

## 📞 Contacto
**Darío Nicolás** - [LinkedIn](https://www.linkedin.com/in/tu-perfil/) | [TAIKAI](https://taikai.network/es/darinic97)

*Desarrollado para el Hackathon COPERNICUS LAC 2026.*
