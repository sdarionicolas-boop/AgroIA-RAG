# Validación de Señal Satelital — Córdoba 2018 (Track B-minus)

_Generado: 2026-06-09 20:00 (hora local del runner)_

## Pregunta

¿La caída relativa del NDVI medida en Sentinel-2 (CDSE) entre el mes anterior y el mes posterior al siniestro correlaciona con el **Daño_Pond del peritaje en campo**, dentro de cada cohorte (cultivo × estadío fenológico)?

**Esto NO es Track B.** Track B exige rinde post-cosecha (`rinde_real_kg_ha`) y este dataset no lo tiene. Acá se valida la **señal satelital de daño**, no la predicción de rinde.

## Parámetros

- Dataset: `Cordoba/` (148 peritajes reales 2018)
- Procesados: **148** lotes con NDVI CDSE real
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
| cebada × llenado_grano | 1 | — | — | — | — | — | _n=1 < 5, no se calculan estadísticos_ |
| cebada × madurez | 1 | — | — | — | — | — | _n=1 < 5, no se calculan estadísticos_ |
| girasol × llenado_grano | 1 | — | — | — | — | — | _n=1 < 5, no se calculan estadísticos_ |
| girasol × reproductivo_inicio | 1 | — | — | — | — | — | _n=1 < 5, no se calculan estadísticos_ |
| girasol × vegetativo_avanzado | 1 | — | — | — | — | — | _n=1 < 5, no se calculan estadísticos_ |
| maiz × llenado_grano | 5 | +0.198 | 0.7491 | +0.000 | -0.009 | [-1.000, +1.000] | +0.061 |
| maiz × reproductivo_inicio | 8 | +0.041 | 0.9228 | -0.286 | +0.002 | [-0.887, +0.816] | +0.037 |
| maiz × vegetativo_avanzado | 18 | -0.216 | 0.3904 | -0.188 | -0.214 | [-0.594, +0.201] | -0.215 |
| maiz × vegetativo_temprano | 7 | — | — | — | — | — | _desviación nula_ |
| soja × desconocido | 1 | — | — | — | — | — | _n=1 < 5, no se calculan estadísticos_ |
| soja × llenado_grano | 16 | +0.293 | 0.2713 | +0.294 | +0.269 | [-0.146, +0.593] | +0.289 |
| soja × madurez | 3 | — | — | — | — | — | _n=3 < 5, no se calculan estadísticos_ |
| soja × reproductivo_inicio | 12 | +0.384 | 0.2180 | +0.423 | +0.379 | [-0.106, +0.718] | +0.384 |
| soja × vegetativo_avanzado | 2 | — | — | — | — | — | _n=2 < 5, no se calculan estadísticos_ |
| soja × vegetativo_temprano | 21 | +0.034 | 0.8821 | -0.183 | +0.016 | [-0.404, +0.442] | +0.033 |
| sorgo × vegetativo_temprano | 3 | — | — | — | — | — | _n=3 < 5, no se calculan estadísticos_ |
| trigo × llenado_grano | 10 | +0.276 | 0.4404 | +0.339 | +0.266 | [-0.621, +0.857] | +0.274 |
| trigo × madurez | 20 | -0.046 | 0.8466 | -0.048 | -0.041 | [-0.372, +0.354] | -0.046 |
| trigo × reproductivo_inicio | 17 | +0.577 | 0.0153 | +0.320 | +0.549 | [-0.213, +0.910] | +0.575 |

## Resultado global (todos los cultivos × estadíos)

- N = **148**
- Pearson R = **+0.1823** (p = 2.6556e-02)
- Spearman ρ = **+0.1254** (p = 1.2873e-01)
- Bootstrap CI95% = [+0.055, +0.291]

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
|      1489 | trigo     | madurez             | 2018-12-10        |          19 |   0.528622 |    0.351031 |    33.5952  | 2018-11          | 2019-01           |
|      1491 | trigo     | madurez             | 2018-12-10        |          19 |   0.572923 |    0.24347  |    57.5038  | 2018-11          | 2019-01           |
|      1493 | trigo     | madurez             | 2018-12-10        |          15 |   0.513424 |    0.533178 |    -3.84757 | 2018-11          | 2019-01           |
|      1504 | trigo     | madurez             | 2018-12-10        |          15 |   0.491644 |    0.467674 |     4.87548 | 2018-11          | 2019-01           |
|      1521 | maiz      | vegetativo_avanzado | 2018-12-10        |          29 |   0.416554 |    0.73409  |   -76.2293  | 2018-11          | 2019-01           |
|      1522 | soja      | vegetativo_temprano | 2018-12-10        |          19 |   0.260702 |    0.748198 |  -186.994   | 2018-11          | 2019-01           |
|      1552 | soja      | vegetativo_temprano | 2018-12-10        |          18 |   0.59787  |    0.579486 |     3.07483 | 2018-11          | 2019-01           |
|      1558 | maiz      | vegetativo_avanzado | 2018-12-10        |          29 |   0.492175 |    0.606087 |   -23.1447  | 2018-11          | 2019-01           |
|      1560 | maiz      | vegetativo_avanzado | 2018-12-10        |          17 |   0.479629 |    0.605844 |   -26.3153  | 2018-11          | 2019-01           |
|      1565 | maiz      | vegetativo_avanzado | 2018-12-10        |          29 |   0.42457  |    0.648419 |   -52.7236  | 2018-11          | 2019-01           |
|      1566 | maiz      | vegetativo_avanzado | 2018-12-10        |          25 |   0.327499 |    0.679521 |  -107.488   | 2018-11          | 2019-01           |
|      1573 | maiz      | reproductivo_inicio | 2018-12-11        |          24 |   0.477564 |    0.575981 |   -20.608   | 2018-11          | 2019-01           |
|      2187 | soja      | vegetativo_temprano | 2019-01-05        |          19 |   0.277529 |    0.822647 |  -196.418   | 2018-12          | 2019-02           |
|      2297 | maiz      | vegetativo_avanzado | 2019-01-06        |          22 |   0.263791 |    0.623589 |  -136.395   | 2018-12          | 2019-02           |
|      2819 | soja      | llenado_grano       | 2019-02-08        |          24 |   0.571675 |    0.702519 |   -22.8878  | 2019-01          | 2019-03           |
|      2930 | soja      | llenado_grano       | 2019-02-08        |          28 |   0.693126 |    0.646426 |     6.73756 | 2019-01          | 2019-03           |
|      3007 | soja      | llenado_grano       | 2019-02-21        |          17 |   0.668478 |    0.728327 |    -8.95308 | 2019-01          | 2019-03           |
|      3022 | soja      | reproductivo_inicio | 2018-12-11        |          21 |   0.446539 |    0.626343 |   -40.2662  | 2018-11          | 2019-01           |
|      3269 | soja      | madurez             | 2019-04-10        |          23 |   0.721262 |    0.533893 |    25.9779  | 2019-03          | 2019-05           |
|      3270 | soja      | madurez             | 2019-04-10        |          23 |   0.721262 |    0.533893 |    25.9779  | 2019-03          | 2019-05           |
|      3278 | soja      | reproductivo_inicio | 2019-01-05        |          16 |   0.266548 |    0.868224 |  -225.729   | 2018-12          | 2019-02           |
|      3279 | soja      | reproductivo_inicio | 2019-01-05        |          16 |   0.266548 |    0.868224 |  -225.729   | 2018-12          | 2019-02           |
|       421 | trigo     | llenado_grano       | 2018-10-18        |          31 |   0.600173 |    0.281327 |    53.1257  | 2018-09          | 2018-11           |
|       428 | trigo     | reproductivo_inicio | 2018-10-17        |          42 |   0.397496 |    0.444117 |   -11.7287  | 2018-09          | 2018-11           |
|      1478 | soja      | vegetativo_temprano | 2018-12-10        |          38 |   0.286717 |    0.518983 |   -81.0087  | 2018-11          | 2019-01           |
|      1487 | maiz      | reproductivo_inicio | 2018-12-10        |          42 |   0.520139 |    0.731741 |   -40.6817  | 2018-11          | 2019-01           |
|      1488 | maiz      | reproductivo_inicio | 2018-12-10        |          41 |   0.534099 |    0.628862 |   -17.7426  | 2018-11          | 2019-01           |
|      1490 | trigo     | madurez             | 2018-12-10        |          35 |   0.514559 |    0.586727 |   -14.0252  | 2018-11          | 2019-01           |
|      1555 | maiz      | vegetativo_avanzado | 2018-12-10        |          31 |   0.50783  |    0.701624 |   -38.1612  | 2018-11          | 2019-01           |
|      1585 | soja      | vegetativo_temprano | 2018-12-10        |          40 |   0.261463 |    0.47961  |   -83.4331  | 2018-11          | 2019-01           |

_(mostrando primeras 30 de 148 filas; ver JSON para set completo)_
