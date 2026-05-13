-- ============================================================================
-- AgroIA RAG — Schema completo v2 (ASCII-SAFE)
--
-- Este archivo es idempotente: puede correrse en una BD vacía (setup inicial)
-- o sobre una BD existente (migración). Todos los CREATE/ALTER usan IF NOT EXISTS
-- o DROP CONSTRAINT IF EXISTS para no fallar si ya están aplicados.
--
-- Cómo ejecutar:
--   Docker:  Get-Content 01_migrate_schema.sql | docker exec -i postgres-agri psql -U agri_user -d agri_db
--   Linux:   psql -U agri_user -d agri_db -f 01_migrate_schema.sql
-- ============================================================================

-- Habilitar extensión pgvector (requerida para embeddings)
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================================
-- TABLA informes_lotes
-- Un registro por lote (estado actual). Contiene el embedding vectorial
-- para búsqueda semántica RAG. Se hace upsert en cada ingesta desde Colab.
-- ============================================================================
CREATE TABLE IF NOT EXISTS informes_lotes (
    id               SERIAL PRIMARY KEY,
    lote_id          VARCHAR(100) NOT NULL,
    fecha            DATE,
    ndvi_promedio    DOUBLE PRECISION,
    gdd_acumulados   DOUBLE PRECISION,
    -- Columnas de score y variabilidad espacial
    score_total      INTEGER,
    cv_espacial      DOUBLE PRECISION,
    zona_activa      BOOLEAN DEFAULT FALSE,
    puntos_zona_c    INTEGER DEFAULT 0,
    cultivo          VARCHAR(50),
    superficie_ha    DOUBLE PRECISION,
    -- Contenido textual para RAG
    contenido_tecnico TEXT,
    metadata         JSONB,
    embedding        vector(768),   -- nomic-embed-text: 768 dimensiones
    -- Timestamps
    created_at       TIMESTAMP DEFAULT now(),
    updated_at       TIMESTAMP DEFAULT now()
);

-- Constraint de unicidad por lote (upsert completo, un registro por lote)
-- Si ya existía con (lote_id, fecha), primero lo eliminamos
ALTER TABLE informes_lotes DROP CONSTRAINT IF EXISTS unique_lote_fecha;
ALTER TABLE informes_lotes ADD CONSTRAINT IF NOT EXISTS unique_lote_id UNIQUE (lote_id);

-- Columnas añadidas en v2 (no-op si ya existen)
ALTER TABLE informes_lotes
    ADD COLUMN IF NOT EXISTS score_total     INTEGER,
    ADD COLUMN IF NOT EXISTS cv_espacial     DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS zona_activa     BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS puntos_zona_c   INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cultivo         VARCHAR(50),
    ADD COLUMN IF NOT EXISTS superficie_ha   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS updated_at      TIMESTAMP DEFAULT now();

-- Índices para búsquedas frecuentes
CREATE INDEX IF NOT EXISTS idx_lotes_lote_id  ON informes_lotes (lote_id);
CREATE INDEX IF NOT EXISTS idx_lotes_cultivo  ON informes_lotes (cultivo);
CREATE INDEX IF NOT EXISTS idx_lotes_score    ON informes_lotes (score_total);

-- Limpiar duplicados previos (solo aplica en migración desde v1)
-- Conserva el registro con mayor id por lote_id
DELETE FROM informes_lotes
WHERE id NOT IN (
    SELECT MAX(id) FROM informes_lotes GROUP BY lote_id
);

-- ============================================================================
-- TABLA lote_historial
-- Serie temporal: una fila por lote + año de campaña.
-- Alimentada por el campo historial_años[] del payload de ingesta.
-- Usada por Streamlit para gráficos históricos y por el RAG para contexto.
-- ============================================================================
CREATE TABLE IF NOT EXISTS lote_historial (
    id                  SERIAL PRIMARY KEY,
    lote_id             VARCHAR(100) NOT NULL,
    anio                INTEGER      NOT NULL,  -- ASCII: anio (no año)
    cultivo             VARCHAR(50),
    ndvi_critico        DOUBLE PRECISION,
    horas_calor         DOUBLE PRECISION,
    score_total         INTEGER,
    score_vigor         DOUBLE PRECISION,
    score_estabilidad   DOUBLE PRECISION,
    score_limpieza      DOUBLE PRECISION,
    score_clima         DOUBLE PRECISION,
    valido_para_score   BOOLEAN DEFAULT TRUE,
    superficie_ha       DOUBLE PRECISION,
    cv_espacial         DOUBLE PRECISION,
    zonificacion_activa BOOLEAN DEFAULT FALSE,
    puntos_zona_c       INTEGER DEFAULT 0,
    created_at          TIMESTAMP DEFAULT now(),
    updated_at          TIMESTAMP DEFAULT now(),
    CONSTRAINT unique_lote_anio UNIQUE (lote_id, anio)  -- ASCII
);

-- Índices para queries frecuentes de Streamlit y RAG
CREATE INDEX IF NOT EXISTS idx_historial_lote    ON lote_historial (lote_id);
CREATE INDEX IF NOT EXISTS idx_historial_anio    ON lote_historial (anio);
CREATE INDEX IF NOT EXISTS idx_historial_cultivo ON lote_historial (cultivo);
CREATE INDEX IF NOT EXISTS idx_historial_score   ON lote_historial (score_total);

-- ============================================================================
-- VERIFICACIÓN FINAL
-- ============================================================================
SELECT 'informes_lotes' AS tabla, COUNT(*) AS registros FROM informes_lotes
UNION ALL
SELECT 'lote_historial', COUNT(*) FROM lote_historial;
