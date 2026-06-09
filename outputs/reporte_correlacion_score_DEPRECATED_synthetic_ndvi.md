# [DEPRECATED] Reporte de Certificación de Precisión del Score AgroIA

> [!CAUTION]
> **REPORTE INVALIDADO — NO USAR EN MATERIAL COMERCIAL, ACTUARIAL NI ACADÉMICO.**
>
> Defectos detectados en auditoría (junio 2026):
>
> 1. **NDVI sintetizado, no medido.** El script generador (`scratch/validar_correlacion.py`, versión previa) calculaba el "NDVI del mes crítico" como `baseline × (1 - daño/100 × 0.75) + ruido`, donde `daño` es el ground truth del peritaje. El Score se computó sobre ese NDVI sintético; correlacionarlo contra `100 - daño` mide la consistencia algebraica de la fórmula de síntesis, no capacidad predictiva sobre Sentinel-2 real.
> 2. **Output derivado del mismo proceso físico que el input.** `Daño_Pond` del peritaje y el NDVI post-evento describen ambos la pérdida de biomasa fotosintéticamente activa; aunque el NDVI hubiese sido medido, usarlo como input del Score y al daño como target es circular.
> 3. **Sin holdout temporal.** Las 20 observaciones se usaron simultáneamente para construir el Score y para "validarlo". No hay train/test split por fecha de campaña.
> 4. **n=20 con desviación intra-cultivo nula.** 8 lotes de trigo con NDVI 0.73–0.78 y Daño=0% no aportan información discriminativa; la dispersión observada proviene de la mezcla trigo+maíz, no de capacidad predictiva intra-cultivo.
>
> Reemplazado por: protocolo de validación predictiva en [`docs/validation/predictive_validation_plan.md`](../docs/validation/predictive_validation_plan.md) (Track B).
> Script refactorizado en [`scratch/validar_correlacion.py`](../scratch/validar_correlacion.py) (exige rinde_real_kg_ha, NDVI real CDSE, LOO-CV, bootstrap; sin fallback sintético).

---

## [Contenido histórico — preservado como evidencia de auditoría]

Este análisis valida científicamente la capacidad del **Score AgroIA (0-100)** para predecir el **rendimiento final** de un lote tras la ocurrencia de un siniestro climático (granizo/sequía/helada). El estudio se realizó sobre **20 lotes del INTA en la Provincia de Córdoba** con peritajes e informes de campo verificados.

## Metodología
- **Rendimiento Real (%)**: `100.0 - Daño_Pond` ( ground truth establecido por los peritos tasadores de la aseguradora en campo).
- **Score AgroIA**: Calculado de forma automatizada mediante la fórmula multivariable del sistema:
  $$\text{Score} = \text{Vigor} (40\%) + \text{Estabilidad} (30\%) + \text{Limpieza} (20\%) + \text{Clima} (10\%)$$
  donde el Vigor se extrajo del NDVI del mes crítico afectado y el Clima de los registros climáticos de la plataforma.

## Métricas del Análisis de Correlación
- **Coeficiente de Correlación de Pearson ($R$):** `0.9763`
- **Significancia Estadística (p-value):** `2.0280e-13` (Confianza estadística superior al 99.9%)

## Matriz de Datos Validada
| Lote | Cultivo | Daño Real (%) | Rendimiento Real (%) | NDVI mes crítico | Score AgroIA |
|---|---|---|---|---|---|
| 346 | Trigo | 0.0% | 100.0% | 0.775 | **79/100** |
| 752 | Trigo | 0.0% | 100.0% | 0.746 | **78/100** |
| 1044 | Trigo | 0.0% | 100.0% | 0.73 | **78/100** |
| 822 | Trigo | 0.0% | 100.0% | 0.744 | **78/100** |
| 1155 | Trigo | 0.0% | 100.0% | 0.76 | **78/100** |
| 923 | Trigo | 1.0% | 99.0% | 0.776 | **79/100** |
| 1176 | Trigo | 2.0% | 98.0% | 0.759 | **79/100** |
| 1398 | Maiz | 4.0% | 96.0% | 0.761 | **80/100** |
| 1097 | Trigo | 5.0% | 95.0% | 0.756 | **78/100** |
| 1098 | Maiz | 15.0% | 85.0% | 0.718 | **77/100** |
| 818 | Trigo | 16.0% | 84.0% | 0.662 | **72/100** |
| 173 | Trigo | 18.0% | 82.0% | 0.642 | **72/100** |
| 704 | Trigo | 20.0% | 80.0% | 0.655 | **73/100** |
| 1768 | Maiz | 24.0% | 76.0% | 0.659 | **71/100** |
| 749 | Trigo | 27.0% | 73.0% | 0.608 | **67/100** |
| 106 | Trigo | 31.0% | 69.0% | 0.607 | **66/100** |
| 1636 | Maiz | 38.0% | 62.0% | 0.582 | **65/100** |
| 1067 | Trigo | 44.0% | 56.0% | 0.486 | **57/100** |
| 335 | Trigo | 54.0% | 46.0% | 0.46 | **55/100** |
| 856 | Trigo | 62.0% | 38.0% | 0.386 | **47/100** |

## Conclusión Comercial para Aseguradoras (Escudo de Ventas)
> [!NOTE]
> **Resumen del Estudio:** La correlación de **0.9763** supera holgadamente el umbral de **0.80** requerido por los actuarios de seguros corporativos. Esto demuestra que la calibración de pesos del Score de AgroIA es altamente predictiva del daño económico en campo, permitiendo a la aseguradora:
> 1. Realizar **pre-liquidaciones automáticas** en el 70% de los siniestros de rutina.
> 2. Reducir los costos operativos de peritos presenciales.
> 3. Disminuir los tiempos de pago al asegurado de 15 días a solo 48 horas.
