# Proyecto: AgroIA - Diagnóstico Automatizado de Daño Agrícola

## Descripción Larga (Pitch)
AgroIA es una plataforma integral de diagnóstico agronómico que democratiza la agricultura de precisión para productores pequeños y medianos en LATAM. Utilizando datos satelitales de la constelación Copernicus (Sentinel-2) y modelos de inteligencia artificial como SAM (Segment Anything Model) de Meta, AgroIA automatiza procesos que antes tomaban horas, reduciéndolos a segundos.

## Problema
En Latinoamérica, la falta de un Sistema de Identificación de Parcelas (LPIS) público obliga a empresas de seguros y agrotechs a digitalizar manualmente cada lote, un proceso costoso y propenso a errores. Esto limita la adopción de seguros paramétricos y herramientas de monitoreo avanzado.

## Solución
Nuestro pipeline "Headless" permite:
1. **Delineado Automático:** De puntos GPS a polígonos georreferenciados en <10 segundos.
2. **Análisis Ecofisiológico:** Integración de NASA POWER y Sentinel-2 para evaluar estrés térmico e hídrico.
3. **Asistente Experto (RAG):** Un motor de consulta basado en LLM (Gemma 3) que "conoce" la historia de cada lote.

## Tecnología
- **Copernicus Data:** Sentinel-2 SR (L2A) para series temporales de NDVI.
- **AI/ML:** SAM (vit_b) para segmentación, YOLO para detección de cultivos, Isolation Forest para limpieza de outliers.
- **Backend:** Python (FastAPI), PostgreSQL + pgvector para la base de datos de conocimiento.
- **Frontend:** Streamlit para el dashboard y Bot de Telegram para acceso en campo.

## Impacto
- Reducción de costos operativos: de USD 15/lote a USD 0.50/lote.
- Escalabilidad: Capacidad de procesar miles de lotes de forma asíncrona.
- Accesibilidad: Diseñado para funcionar en entornos con baja conectividad mediante reportes offline (PDF/HTML).

---
*Este proyecto participa en el Hackathon COPERNICUS LAC 2026.*
