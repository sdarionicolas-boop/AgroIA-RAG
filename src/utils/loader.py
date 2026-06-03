# src/utils/loader.py
"""
AgroIA RAG — Loader v2 (ASCII-SAFE)
Escribe en DOS tablas coordinadas por cada corrida de Colab:
- informes_lotes  : upsert por lote_id (embedding + contenido RAG)
- lote_historial  : upsert por lote_id + anio (serie temporal)
"""
import json
import logging
import psycopg2
from psycopg2.extras import Json
from utils.config import settings

logger = logging.getLogger(__name__)

# ============================================================================
# EMBEDDING
# ============================================================================
def generate_embedding(text: str) -> list[float]:
    """Genera embedding de 768 dimensiones con nomic-embed-text vía API REST de Ollama."""
    import requests
    url = f"{settings.ollama_url.rstrip('/')}/api/embed"
    payload = {
        "model": settings.embedding_model,
        "input": text
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()["embeddings"][0]
    except Exception as e:
        logger.error(f"Error conectando a Ollama en {url}: {e}")
        raise

# ============================================================================
# CONEXIÓN
# ============================================================================
def get_connection():
    return psycopg2.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
    )

# ============================================================================
# INSERTAR INFORME CONSOLIDADO (upsert en ambas tablas)
# ============================================================================
def insertar_informe(payload: dict) -> int:
    """
    Recibe el payload consolidado de Colab y escribe en:
    1. informes_lotes  → upsert por lote_id (embedding + contenido RAG)
    2. lote_historial  → upsert por lote_id + anio (serie temporal)
    Retorna el ID del registro en informes_lotes.
    """
    lote_id = payload["lote_id"]
    metadata = payload.get("metadata", {})
    historial = payload.get("historial_anos", [])  # ASCII ONLY
    
    print(f"   [DEBUG_LOADER] Lote: {lote_id} | Historial size: {len(historial)}")
    if len(historial) > 0:
        print(f"   [DEBUG_LOADER] Primer año: {historial[0].get('anio')}")

    # ── 1. Generar embedding del contenido técnico consolidado ────────────────
    contenido = payload.get("contenido_tecnico", "")
    logger.info(f"Generando embedding para lote '{lote_id}' ({len(contenido)} chars)...")
    embedding = generate_embedding(contenido)
    vec_str = f"[{', '.join(f'{v:.6f}' for v in embedding)}]"

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                # ── 2. Upsert en informes_lotes ───────────────────────────────
                cur.execute("""
                    INSERT INTO informes_lotes (
                        lote_id, fecha, ndvi_promedio, gdd_acumulados,
                        score_total, cv_espacial, zona_activa, puntos_zona_c,
                        cultivo, superficie_ha,
                        contenido_tecnico, metadata, embedding, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, now()
                    )
                    ON CONFLICT (lote_id) DO UPDATE SET
                        fecha             = EXCLUDED.fecha,
                        ndvi_promedio     = EXCLUDED.ndvi_promedio,
                        gdd_acumulados    = EXCLUDED.gdd_acumulados,
                        score_total       = EXCLUDED.score_total,
                        cv_espacial       = EXCLUDED.cv_espacial,
                        zona_activa       = EXCLUDED.zona_activa,
                        puntos_zona_c     = EXCLUDED.puntos_zona_c,
                        cultivo           = EXCLUDED.cultivo,
                        superficie_ha     = EXCLUDED.superficie_ha,
                        contenido_tecnico = EXCLUDED.contenido_tecnico,
                        metadata          = EXCLUDED.metadata,
                        embedding         = EXCLUDED.embedding,
                        updated_at        = now()
                    RETURNING id
                """, (
                    lote_id,
                    payload.get("fecha"),
                    payload.get("ndvi_promedio"),
                    payload.get("gdd_acumulados"),
                    metadata.get("score_total"),
                    metadata.get("cv_espacial"),
                    metadata.get("zonificacion_activa", False),
                    metadata.get("puntos_zona_c", 0),
                    metadata.get("cultivo"),
                    metadata.get("superficie_ha"),
                    contenido,
                    Json(metadata),
                    vec_str,
                ))
                informe_id = cur.fetchone()[0]
                logger.info(f"informes_lotes → ID {informe_id} (lote: {lote_id})")

                # ── 3. Upsert en lote_historial (una fila por año) ────────────
                for row in historial:
                    cur.execute("""
                        INSERT INTO lote_historial (
                            lote_id, anio, cultivo,
                            ndvi_critico, horas_calor,
                            score_total, score_vigor, score_estabilidad,
                            score_limpieza, score_clima,
                            valido_para_score, superficie_ha,
                            cv_espacial, zonificacion_activa, puntos_zona_c,
                            updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()
                        )
                        ON CONFLICT (lote_id, anio) DO UPDATE SET
                            cultivo             = EXCLUDED.cultivo,
                            ndvi_critico        = EXCLUDED.ndvi_critico,
                            horas_calor         = EXCLUDED.horas_calor,
                            score_total         = EXCLUDED.score_total,
                            score_vigor         = EXCLUDED.score_vigor,
                            score_estabilidad   = EXCLUDED.score_estabilidad,
                            score_limpieza      = EXCLUDED.score_limpieza,
                            score_clima         = EXCLUDED.score_clima,
                            valido_para_score   = EXCLUDED.valido_para_score,
                            superficie_ha       = EXCLUDED.superficie_ha,
                            cv_espacial         = EXCLUDED.cv_espacial,
                            zonificacion_activa = EXCLUDED.zonificacion_activa,
                            puntos_zona_c       = EXCLUDED.puntos_zona_c,
                            updated_at          = now()
                    """, (
                        lote_id,
                        row["anio"],  # ← ASCII: anio (no año)
                        row.get("cultivo"),
                        row.get("ndvi_critico"),
                        row.get("horas_calor"),
                        row.get("score_total"),
                        row.get("score_vigor"),
                        row.get("score_estabilidad"),
                        row.get("score_limpieza"),
                        row.get("score_clima"),
                        row.get("valido_para_score", True),
                        row.get("superficie_ha"),
                        row.get("cv_espacial"),
                        row.get("zonificacion_activa", False),
                        row.get("puntos_zona_c", 0),
                    ))
                logger.info(f"lote_historial → {len(historial)} filas (lote: {lote_id})")
        return informe_id
    except Exception as e:
        logger.error(f"Error en insertar_informe: {e}")
        raise
    finally:
        conn.close()