# Plan de Validación Predictiva — Track B

> Documento técnico de planificación. Define el protocolo formal para
> evaluar si el Score AgroIA pre-evento predice el rinde real
> post-cosecha (kg/ha), sustituyendo al reporte deprecado
> [`reporte_correlacion_score_DEPRECATED_synthetic_ndvi.md`](../../outputs/reporte_correlacion_score_DEPRECATED_synthetic_ndvi.md).

**Estado:** plan. No contiene resultados experimentales.
**Audiencia:** equipo técnico AgroIA + reviewers externos (INTA, aseguradoras).
**Anclaje en el repo:** [AGENTS.md §13 — Diferenciación de Métricas](../../AGENTS.md) ya separa NDVI satelital (biomasa) de tasación humana (daño mecánico). Este plan operacionaliza esa filosofía con un protocolo medible.

---

## 1. Hipótesis

**H1 (predictiva):** El Score AgroIA computado sobre datos Sentinel-2 + NASA POWER de la ventana **pre-evento** explica una fracción operativamente útil de la varianza del **rinde real medido post-cosecha** (kg/ha), calculada por cultivo.

**Umbral de utilidad operacional propuesto:** R² ≥ 0.60 por cultivo en el conjunto de test temporal, con CI95% bootstrap que **no cruce 0.40**.

**H0:** El Score AgroIA no aporta información sobre rinde por encima del rinde histórico promedio del cultivo en la zona.

**Notas:**
- El umbral 0.60 se elige a partir del rango reportado en literatura para Sentinel-2 vs rinde en cultivos extensivos pampeanos (ver §8). **No es una predicción del resultado esperado; es la barra mínima para considerar el modelo útil para suscripción.**
- Para liquidación de siniestros aplica un umbral distinto (no cubierto por este plan): requeriría validación pareada delta-NDVI vs tasación pericial con N alto y control de senescencia fenológica.

---

## 2. Dataset mínimo requerido

### 2.1 Variables por lote

| Variable | Tipo | Fuente | Comentario |
|---|---|---|---|
| `lote_id` | str | interno | identificador único |
| `cultivo` | enum {trigo, maiz, soja, girasol} | declaración | filtra por modelo |
| `geometria` | polígono WGS84 | shapefile / KML del productor | NO punto GPS + buffer; se necesita límite real del lote |
| `fecha_siembra` | date | declaración | para acotar ventana fenológica |
| `fecha_cosecha` | date | declaración | corta ventana NDVI por madurez |
| `fecha_siniestro` | date \| null | declaración del productor | null = campaña sin siniestro declarado |
| `tipo_siniestro` | enum \| null | declaración | granizo / sequía / helada / inundación |
| `rinde_real_kg_ha` | float | monitor de cosecha o tasación post-cosecha | **bloqueante** — sin esto no hay validación |
| `metodo_rinde` | enum | declaración | {monitor, balanza, declaracion_jurada} — afecta el peso en el split |
| `estadio_al_siniestro` | enum \| null | declaración / inferencia fenológica | {V_X, R_X} — para análisis por estadío |

### 2.2 N mínimo por cultivo

Cálculo conservador para detectar R² ≥ 0.60 con potencia 0.80, α=0.05 (test bilateral):

| Cultivo | N mínimo train | N mínimo test (holdout temporal) | N total |
|---|---|---|---|
| Trigo | 50 | 20 | 70 |
| Maíz | 50 | 20 | 70 |
| Soja | 50 | 20 | 70 |
| Girasol | 30 | 15 | 45 (umbral más laxo: R² ≥ 0.50) |

**Total cross-cultivo estimado:** ~255 lotes con dato completo.

**Cobertura espacial:** al menos 2 zonas agroecológicas distintas por cultivo (e.g. Pampa Húmeda Norte + Pampa Subhúmeda) para no sobreajustar a una región.

**Cobertura temporal:** ≥3 campañas distintas; el holdout temporal debe ser la campaña más reciente disponible (no random split).

### 2.3 Estado actual del repo vs requisito

Según AGENTS.md §9: 26 lotes enriquecidos (19 INTA + 7 maestros). **Brecha estimada:** ~230 lotes con `rinde_real_kg_ha` por adquirir. Los 26 actuales no tienen rinde declarado explícito en `informes_lotes` (verificar `metadata` JSONB antes de descartar).

---

## 3. Protocolo estadístico

### 3.1 Split

- **Split temporal por campaña**, no random. Ejemplo: campañas 2021/22, 2022/23, 2023/24 → train; campaña 2024/25 → test.
- Dentro de train: **k-fold por lote** (k=5) para tuning de hiperparámetros del Score (umbrales por cultivo, peso del CV histórico), nunca por observación-lote.
- El test temporal **se toca una sola vez** al final del estudio. Resultado final con CI95%.

### 3.2 Métricas reportadas

Por cultivo y por tipo de siniestro (cuando aplique):

| Métrica | Por qué |
|---|---|
| R² (coef. determinación) | comparable con literatura |
| MAE (kg/ha) | error promedio en unidades del negocio |
| RMSE (kg/ha) | penaliza errores grandes (importante para suscripción) |
| MAPE (%) | error relativo, comparable entre cultivos |
| Pearson r + p-value | monotonía |
| Spearman ρ | robustez ante outliers |
| Bootstrap CI95% para R² y r | (1000 iter, semilla fija) |
| LOO-CV r | estabilidad del estimador con n acotado |
| Calibración por decil del Score | linealidad del mapeo Score→rinde |

### 3.3 Reglas de honestidad metodológica

1. **Sin fallback sintético.** Lotes sin NDVI Sentinel-2 real (CDSE) en la ventana se excluyen y se reportan en sección de exclusiones. **Nunca se completa con simulación**, ni siquiera para mantener N alto.
2. **Sin reuso del target en el input.** El % de daño del peritaje no puede aparecer del lado del Score. El Score se calcula con NDVI + clima + histórico **anteriores al siniestro**.
3. **Separación por cultivo obligatoria.** No se agregan correlaciones cross-cultivo; agregar mezcla la varianza inter-cultivo con la intra-cultivo y produce R artificialmente altos.
4. **N pequeño se reporta como tal.** Bloques con n < 30 se acompañan de bootstrap CI ancho y la frase "potencia estadística limitada; resultado preliminar".
5. **Test temporal único.** Si el R² en test es inferior al umbral, **no se hace tuning post-hoc**; se cierra el estudio con resultado negativo y se planifica re-validación con dataset ampliado.

### 3.4 Análisis complementario

- **Residuos vs estadío fenológico**: identificar si el modelo es sistemáticamente sesgado en madurez (caso trigo Nov en zona Córdoba: senescencia natural confunde delta-NDVI).
- **Residuos vs cobertura nubosa**: lotes con <X% píxeles válidos en la ventana tienen peor predicción.
- **Comparación contra baseline**: predecir rinde con (a) promedio histórico del lote, (b) promedio histórico de la zona, (c) modelo lineal sobre NDVI promedio sin Score. El Score debe **superar** al menos al baseline (a).

---

## 4. Plan de adquisición de datos

### 4.1 Candidatos prioritarios

| Fuente | Cultivos | Estimación N disponible | Riesgo de acceso | Acción |
|---|---|---|---|---|
| INTA Manfredi (EEA Córdoba) | maíz, soja, trigo | ~30–60 lotes experimentales con monitor de rinde por campaña | Bajo si hay convenio académico; Medio sin él | Contacto institucional; propuesta de co-publicación |
| INTA Marcos Juárez (EEA Córdoba) | maíz, soja, trigo, girasol | ~40–80 lotes con datos de rendimiento | Bajo–Medio | Idem Manfredi |
| Aseguradoras agrícolas (Sancor, La Segunda, ACA) | mix | Cartera con declaración jurada de rinde post-cosecha, ≥200 lotes/campaña | Alto: NDA + uso restringido + posiblemente datos sin geometría real | Acuerdo bilateral con anonimización; ofrecer validación cruzada como contraparte |
| Productores individuales con monitor John Deere / CaseIH / AGCO | mix | Variable | Bajo individualmente, Alto en escala | Vía cooperativas (ACA, AFA); convenios uno-a-uno |
| Programa BAGÓ / Aapresid datasets abiertos | siembra directa | Limitado | Bajo | Verificar si rinde está en formato abierto |

**Las cifras de N disponibles son estimaciones operativas, no compromisos confirmados.** Cada candidato requiere reunión técnica para confirmar geometría real, calidad del monitor de rinde y cobertura temporal antes de incluirlo en el dataset.

### 4.2 Timeline realista

| Semana | Hito |
|---|---|
| 1–2 | Reuniones iniciales INTA Manfredi + Marcos Juárez; acuerdo de NDA con 1 aseguradora candidata |
| 3–4 | Auditoría de los 26 lotes actuales: verificar si `metadata` JSONB tiene `rinde_kg_ha`; si sí, primer subset utilizable |
| 5–6 | Ingesta de datasets INTA con pipeline de validación (CSV → `scratch/validar_correlacion.py`) |
| 7–8 | Primera corrida cuantitativa por cultivo con N parcial; reporte intermedio |
| 9–10 | Ingesta aseguradora (si NDA cerrado); refresco del análisis |
| 11–12 | Corrida final con holdout temporal; redacción del paper interno + dashboard de validación continua |

**Slippage esperado:** ±4 semanas, dominado por (a) tiempo de NDA con aseguradora y (b) calidad del georreferenciamiento del monitor de cosecha (frecuente: shift de varios metros entre la traza del monitor y la geometría declarada del lote).

### 4.3 Riesgo principal

**Acceso al monitor de rinde con geometría real.** Riesgos secundarios:
- Productores comparten declaración jurada de rinde pero no la traza espacial del monitor → no se puede calibrar por zona del lote.
- Monitor reporta rinde a nivel lote pero no a nivel píxel → impide validación de zonificación A/B/C.
- Aseguradoras comparten rinde sin geometría real (solo "lote X, productor Y, partido Z") → invalida cualquier asociación con Sentinel-2.

**Mitigación:** descartar fuentes que no provean trazabilidad espacial mínima (polígono del lote + año de cosecha). No vale la pena ingestar datos sin geometría — contaminan el dataset.

---

## 5. Output esperado

1. **Paper técnico interno** (no presentación comercial). Audiencia: equipo + reviewers externos. Estructura: contexto → datos → métodos → resultados por cultivo → limitaciones → próximos pasos. Incluye exclusiones y resultados negativos.
2. **Dashboard de validación continua**: panel Streamlit (ya existe la base) con métricas refrescadas a medida que se ingestan campañas nuevas. Permite ver degradación del modelo en producción.
3. **Reporte por aseguradora** (cuando aplique): subset específico de la cartera del cliente con métricas sobre ese subset, no extrapolación de Manfredi/Marcos Juárez.

**Lo que el output NO va a ser:**
- Un único número de correlación.
- Una garantía de R² > 0.X.
- Un reemplazo de la tasación pericial para liquidación de siniestros.

---

## 6. Cómo se integra con el pipeline ya existente

- **Input format:** CSV con columnas obligatorias documentadas en `scratch/validar_correlacion.py` (`lote_id`, `cultivo`, `fecha_siniestro`, `fecha_cosecha`, `geometria_wkt`, `rinde_real_kg_ha`). El script aborta con mensaje claro si falta cualquier columna.
- **Computación NDVI:** [`src/pipeline/eodag_extractor.py`](../../src/pipeline/eodag_extractor.py) (CDSE Statistical API + caché SQLite). Sin modificación de la firma del modelo: este plan valida la fórmula actual del Score (AGENTS.md §5: Vigor 40% + Estabilidad 30% + Limpieza 20% + Clima 10%), no la rediseña.
- **Computación Score:** [`src/pipeline/agro_math.py`](../../src/pipeline/agro_math.py) con `CONFIG[cultivo]['umbral_clima']` actual.
- **Persistencia:** los lotes validados se cargan a `informes_lotes` con un flag `validation_cohort=True` en `metadata` JSONB, separable del dataset operacional.

---

## 7. Compromiso explícito

- **No se publican métricas hasta que el N por cultivo supere el mínimo de §2.2.**
- **No se acepta R alto sin CI bootstrap.**
- **No se reportan correlaciones agregadas cross-cultivo.**
- **Resultados negativos se publican igual.** Si el Score no supera el baseline (a) de §3.4, se documenta y se diseña una v2 del Score.

---

## 8. Precedentes de literatura

Para no calibrar expectativas en el vacío, referencias revisadas por pares sobre rendimiento de NDVI Sentinel-2 / multiespectral en cultivos extensivos pampeanos y comparables:

- **Lopresti et al. (2015).** _Estimation of wheat yield using SPOT-VEGETATION NDVI time series in Argentina's Pampa region._ International Journal of Applied Earth Observation and Geoinformation. R² reportado para trigo en Pampa: ~0.65–0.78.
- **Bolton & Friedl (2013).** _Forecasting crop yield using remotely sensed vegetation indices and crop phenology metrics._ Agricultural and Forest Meteorology. R² para maíz en zona templada: 0.7–0.85 con métricas integradas durante el período crítico.
- **Skakun et al. (2019).** _Early season large-area winter crop mapping and yield prediction using MODIS NDVI._ Remote Sensing of Environment. R² ~0.60–0.75 para trigo invernal.
- **Veloso et al. (2017).** _Understanding the temporal behavior of crops using Sentinel-1 and Sentinel-2-like data._ Remote Sensing of Environment. Caracteriza la firma temporal de NDVI por estadío para soja y maíz; útil para definir ventanas críticas por cultivo.

**Rango operativo a esperar en condiciones favorables (geometría real del lote, monitor de rinde calibrado, ventana NDVI sin nubes): R² 0.60–0.80 por cultivo.** Este rango se usa como referencia, **no como predicción del resultado AgroIA**, que se reportará tal cual salga.

---

## 9. Lo que cambia respecto al reporte deprecado

| Aspecto | Reporte deprecado | Este plan (Track B) |
|---|---|---|
| Target | `100 - Daño_Pond` (peritaje) | `rinde_real_kg_ha` (monitor o tasación post-cosecha) |
| NDVI | sintetizado algebraicamente desde `Daño_Pond` | medido sobre Sentinel-2 vía CDSE |
| Split | ninguno (mismas 20 obs para todo) | split temporal por campaña + k-fold en train |
| Agregación | trigo+maíz juntos | por cultivo, separadamente |
| Incertidumbre | p-value sin contexto | bootstrap CI95% + LOO + comparación con baseline |
| N | 20 (mezcla) | ≥45–70 por cultivo (target) |
| Geometría | punto GPS + buffer 500 m | polígono real del lote |
| Resultado negativo | no contemplado | publicado igual |

---

_Documento mantenido por: equipo AgroIA. Última revisión: 2026-06-08._
