# 🌾 AgroIA — Diagnóstico Agronómico con Inteligencia Artificial

> **"Un punto GPS. Un Score Agronómico. Decisiones basadas en datos en 60 segundos."**

[![Hackathon](https://img.shields.io/badge/Hackathon-CopernicusLAC%20%233-blue)](https://taikai.network/copernicuslac-panama/hackathons/seguridad-alimentaria-2026/projects/cmnjc68qc03xa7d3bqrr8td76/idea)
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
- 🎯 **Hit Rate de hasta 85%** en delineación automática (validado con datasets de TAYPE e INTA).
- 📉 **Detección de Anomalías**: Uso de `IsolationForest` para filtrar fallas satelitales (nubes/errores).
- 🌍 **Escalabilidad**: Listo para procesar miles de lotes de forma asíncrona.

---

## 🚀 Quick Start (Launcher Unificado)

Para levantar todo el ecosistema:

```bash
# 1. Clonar el repositorio y configurar el entorno
git clone https://github.com/sdarionicolas-boop/AgroIA-RAG.git
cd AgroIA-RAG
cp config/.env.example config/.env # Ajustar con tus credenciales

# 2. Levantar la base de datos (PostgreSQL + pgvector)
# Si no tienes Docker Compose instalado, usa docker run
docker run -d --name postgres-agri -p 5432:5432 -e POSTGRES_PASSWORD=postgres ankane/pgvector

# 3. Verificar y arrancar el sistema
python start.py --check
python start.py
```

- **Dashboard Streamlit**: `http://localhost:8501`
- **Documentación API**: `http://localhost:8000/docs`
- **Telegram Bot**: Busca tu bot configurado en `@BotFather`.

---

## 📖 Documentación completa
Para guías detalladas, referencias de API y arquitectura del sistema, visita:
[https://mintlify.wiki/sdarionicolas-boop/AgroIA-RAG/introduction](https://mintlify.wiki/sdarionicolas-boop/AgroIA-RAG/introduction)

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
El sistema cuenta con un **Motor de Certificación de Precisión** (`--validate`) que audita el pipeline contra datasets históricos:
- **Hit Rate**: 85.6% (TAYPE) / 74.9% (INTA).
- **SAM Score promedio**: 0.962.
- **Error de área promedio**: 8.5% (INTA) vs referencia manual.
- **Fidelidad Satelital**: Validado contra siniestros de Córdoba (2018), diferenciando vigor fotosintético de daño mecánico.

---

## 📞 Contacto
**Darío Nicolás** - [LinkedIn](https://www.linkedin.com/in/darionicolas/) | [TAIKAI](https://taikai.network/es/darinic97)

## ⚖️ Licencia
Este proyecto está bajo la Licencia **Apache 2.0**. Consulta el archivo [LICENSE](./LICENSE) para más detalles.

© 2026 Darío Nicolás Sánchez Leguizamón.

*Desarrollado para el Hackathon COPERNICUS LAC 2026.*
