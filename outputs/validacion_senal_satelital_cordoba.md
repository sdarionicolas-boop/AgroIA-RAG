# Validación de Señal Satelital — Córdoba 2018 (Track B-minus)

_Generado: 2026-06-09 14:31 (hora local del runner)_

## Pregunta

¿La caída relativa del NDVI medida en Sentinel-2 (CDSE) entre el mes anterior y el mes posterior al siniestro correlaciona con el **Daño_Pond del peritaje en campo**, dentro de cada cohorte (cultivo × estadío fenológico)?

**Esto NO es Track B.** Track B exige rinde post-cosecha (`rinde_real_kg_ha`) y este dataset no lo tiene. Acá se valida la **señal satelital de daño**, no la predicción de rinde.

## Parámetros

- Dataset: `Cordoba/` (215 peritajes reales 2018)
- Procesados: **215** lotes con NDVI CDSE real
- Excluidos por falta de Sentinel-2: **0**
- Buffer envolvente alrededor del punto GPS: ~80 ha (rectángulo ~1110 m × ~909 m)
- Ventana NDVI: pre = mes anterior al siniestro, post = mes posterior
- Bootstrap: 1000 iter (semilla 42)

- Estado CDSE peor de la corrida: **`cached_valid`**

## Limitaciones declaradas

1. **Geometría = punto GPS + buffer envolvente ~80 ha.** La señal NDVI se diluye con caminos, vecindades y mezcla de cultivos. Un R bajo en una cohorte no significa que la metodología falle: significa que el buffer no captura el lote real. Para análisis actuarial se requiere polígono del catastro (ver decisión SAM-only-en-B2C en AGENTS.md §13.1).
2. **Cohorte dominante:** trigo en Sept-Oct 2018, fenologías Encañazón / Espigazón / Grano Lechoso. Resultados sobre maíz/soja son referenciales por N bajo.
3. **Daño_Pond** ya integra '% del lote afectado × % de daño dentro de la franja'. No es un rinde medido, es un ponderado pericial subjetivo (pero independiente del NDVI).
4. **Sin holdout temporal.** Todos los registros son de la misma campaña; este reporte no estima generalización a campañas futuras.

## Resultados por cohorte (cultivo × estadío)

| Cohorte | N | Pearson R | p | Spearman ρ | Bootstrap R̄ | CI95% | LOO R̄ |
|---|---|---|---|---|---|---|---|
| maiz × llenado_grano | 1 | — | — | — | — | — | _n=1 < 5, no se calculan estadísticos_ |
| maiz × reproductivo_inicio | 3 | — | — | — | — | — | _n=3 < 5, no se calculan estadísticos_ |
| maiz × vegetativo_avanzado | 12 | -0.115 | 0.7224 | -0.094 | -0.091 | [-0.494, +0.445] | -0.112 |
| maiz × vegetativo_temprano | 36 | +0.272 | 0.1084 | +0.056 | +0.261 | [-0.010, +0.480] | +0.272 |
| soja × madurez | 1 | — | — | — | — | — | _n=1 < 5, no se calculan estadísticos_ |
| soja × vegetativo_avanzado | 2 | — | — | — | — | — | _n=2 < 5, no se calculan estadísticos_ |
| soja × vegetativo_temprano | 13 | +0.432 | 0.1400 | +0.444 | +0.391 | [-0.253, +0.823] | +0.429 |
| trigo × llenado_grano | 75 | +0.105 | 0.3715 | +0.131 | +0.100 | [-0.080, +0.284] | +0.105 |
| trigo × madurez | 41 | -0.055 | 0.7317 | -0.261 | -0.073 | [-0.314, +0.154] | -0.056 |
| trigo × reproductivo_inicio | 3 | — | — | — | — | — | _n=3 < 5, no se calculan estadísticos_ |
| trigo × vegetativo_trigo | 28 | -0.623 | 0.0004 | -0.496 | -0.605 | [-0.828, -0.246] | -0.622 |

## Resultado global (todos los cultivos × estadíos)

- N = **215**
- Pearson R = **+0.2101** (p = 1.9572e-03)
- Spearman ρ = **+0.1328** (p = 5.1901e-02)
- Bootstrap CI95% = [+0.112, +0.303]

## Interpretación

- **p > 0.05 → no se rechaza H0** dentro de esa cohorte.
- **CI95% que cruza 0** → coeficiente no distinguible de cero.
- **LOO R̄ alejado del R muestral** → presencia de puntos influyentes / inestabilidad.
- Resultados estables (R > 0.4, CI95% no cruza 0, LOO ≈ R) para una cohorte sugieren que la señal NDVI delta pre/post es útil para esa fenología; resultados débiles sugieren que el buffer 80 ha está diluyendo la señal en esos casos.

## Lotes excluidos

(ninguno)

## Matriz de datos procesados (NDVI real CDSE)

|   lote_id | cultivo   | estadio             | fecha_siniestro   |   dano_pond |   ndvi_pre |   ndvi_post |   delta_rel | pre_year_month   | post_year_month   |
|----------:|:----------|:--------------------|:------------------|------------:|-----------:|------------:|------------:|:-----------------|:------------------|
|        71 | trigo     | vegetativo_trigo    | 2018-09-18        |           4 |   0.381564 |    0.352134 |     7.71302 | 2018-08          | 2018-10           |
|       134 | trigo     | vegetativo_trigo    | 2018-09-18        |           4 |   0.381564 |    0.352134 |     7.71302 | 2018-08          | 2018-10           |
|       165 | trigo     | vegetativo_trigo    | 2018-09-18        |           5 |   0.309512 |    0.437804 |   -41.4497  | 2018-08          | 2018-10           |
|       174 | trigo     | vegetativo_trigo    | 2018-09-18        |           5 |   0.309512 |    0.437804 |   -41.4497  | 2018-08          | 2018-10           |
|       265 | trigo     | llenado_grano       | 2018-10-08        |           3 |   0.594997 |    0.377488 |    36.5563  | 2018-09          | 2018-11           |
|       330 | trigo     | llenado_grano       | 2018-10-08        |           3 |   0.543002 |    0.357257 |    34.207   | 2018-09          | 2018-11           |
|       332 | trigo     | llenado_grano       | 2018-10-08        |           3 |   0.489819 |    0.422779 |    13.6866  | 2018-09          | 2018-11           |
|       346 | trigo     | reproductivo_inicio | 2018-10-17        |           0 |   0.538845 |    0.264691 |    50.8781  | 2018-09          | 2018-11           |
|       347 | trigo     | llenado_grano       | 2018-10-17        |           2 |   0.380023 |    0.339814 |    10.5806  | 2018-09          | 2018-11           |
|       383 | trigo     | llenado_grano       | 2018-10-17        |           0 |   0.567177 |    0.364639 |    35.7098  | 2018-09          | 2018-11           |
|       384 | trigo     | llenado_grano       | 2018-10-17        |           0 |   0.615644 |    0.245366 |    60.1449  | 2018-09          | 2018-11           |
|       414 | trigo     | llenado_grano       | 2018-10-17        |           0 |   0.615644 |    0.245366 |    60.1449  | 2018-09          | 2018-11           |
|       442 | maiz      | vegetativo_temprano | 2018-10-17        |           0 |   0.349122 |    0.315855 |     9.5288  | 2018-09          | 2018-11           |
|       547 | trigo     | llenado_grano       | 2018-10-17        |           3 |   0.574015 |    0.317106 |    44.7565  | 2018-09          | 2018-11           |
|       603 | maiz      | vegetativo_temprano | 2018-10-17        |           0 |   0.1938   |    0.3255   |   -67.9564  | 2018-09          | 2018-11           |
|       604 | maiz      | vegetativo_temprano | 2018-10-17        |           0 |   0.214549 |    0.238511 |   -11.1682  | 2018-09          | 2018-11           |
|       619 | maiz      | vegetativo_temprano | 2018-10-17        |           0 |   0.231324 |    0.417389 |   -80.4349  | 2018-09          | 2018-11           |
|       621 | maiz      | vegetativo_temprano | 2018-10-17        |           0 |   0.349122 |    0.315855 |     9.5288  | 2018-09          | 2018-11           |
|       711 | maiz      | vegetativo_temprano | 2018-11-10        |           5 |   0.198432 |    0.530687 |  -167.44    | 2018-10          | 2018-12           |
|       712 | maiz      | vegetativo_temprano | 2018-11-10        |           5 |   0.198432 |    0.530687 |  -167.44    | 2018-10          | 2018-12           |
|       714 | maiz      | vegetativo_temprano | 2018-11-10        |           5 |   0.335505 |    0.378674 |   -12.8669  | 2018-10          | 2018-12           |
|       720 | trigo     | llenado_grano       | 2018-11-10        |           4 |   0.416868 |    0.326746 |    21.6188  | 2018-10          | 2018-12           |
|       722 | trigo     | llenado_grano       | 2018-11-10        |           4 |   0.416868 |    0.326746 |    21.6188  | 2018-10          | 2018-12           |
|       728 | maiz      | vegetativo_temprano | 2018-11-10        |           5 |   0.198432 |    0.530687 |  -167.44    | 2018-10          | 2018-12           |
|       729 | maiz      | vegetativo_temprano | 2018-11-10        |           5 |   0.198432 |    0.530687 |  -167.44    | 2018-10          | 2018-12           |
|       745 | trigo     | llenado_grano       | 2018-11-10        |           0 |   0.432944 |    0.419953 |     3.00081 | 2018-10          | 2018-12           |
|       746 | trigo     | llenado_grano       | 2018-11-10        |           0 |   0.285196 |    0.305264 |    -7.0365  | 2018-10          | 2018-12           |
|       752 | trigo     | llenado_grano       | 2018-11-10        |           0 |   0.30997  |    0.263868 |    14.873   | 2018-10          | 2018-12           |
|       754 | maiz      | vegetativo_temprano | 2018-11-10        |           0 |   0.250807 |    0.548251 |  -118.594   | 2018-10          | 2018-12           |
|       759 | maiz      | vegetativo_temprano | 2018-11-10        |           0 |   0.250807 |    0.548251 |  -118.594   | 2018-10          | 2018-12           |

_(mostrando primeras 30 de 215 filas; ver JSON para set completo)_
