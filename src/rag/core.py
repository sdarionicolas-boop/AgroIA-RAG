# src/rag/core.py
"""
AgroIA RAG — Core de consulta
=================================================
Expone las funciones principales para uso desde
la app Streamlit, el bot de Telegram, o cualquier
cliente interno:

  - consultar_agente(lote_id, pregunta, top_k)
  - fetch_context(lote_id, pregunta, top_k)
  - listar_lotes()
  - BASE_PROMPT  (importable para consistencia)

Estrategia de retrieval
-----------------------
informes_lotes tiene UNIQUE(lote_id) → siempre 1 fila por lote.
Para que top_k sea significativo, fetch_context combina:
  1. El registro consolidado de informes_lotes (embedding vectorial)
  2. Las N filas más relevantes de lote_historial (serie temporal)
Esto da contexto multi-año real al LLM en lugar de repetir
siempre el mismo único fragmento.
"""
import json
import logging
import psycopg2
import ollama
from utils.config import settings

logger = logging.getLogger(__name__)

# ============================================================================
# PROMPT BASE — importable desde otros módulos para mantener consistencia
# ============================================================================
BASE_PROMPT = (
    "Eres un Ingeniero Agrónomo experto del sistema AgroIA. "
    "Responde basándote estrictamente en el contexto recuperado. "
    "ADAPTA LA EXTENSIÓN: Si la pregunta es simple, responde de forma corta y directa. Si es compleja o pide análisis, sé profundo. "
    "No te limites a repetir números; analiza la relación entre ellos si es relevante. "
    "Si preguntan por el Score, desglosa los componentes (Vigor, Estabilidad, Limpieza, Clima) solo si es necesario para responder la duda. "
    "CITAS: Usa [ID-X] para referirte al informe actual y [Campaña YYYY] en lugar de [HIST-YYYY] para que sea más natural para el usuario. "
    "Si no hay información suficiente, indicá 'Dato no disponible'."
)


# ============================================================================
# CONEXIÓN
# ============================================================================
def _get_connection():
    return psycopg2.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
    )


# ============================================================================
# EMBEDDING
# ============================================================================
def _generate_embedding(text: str) -> list[float]:
    resp = ollama.embed(model=settings.embedding_model, input=text)
    return resp["embeddings"][0]


# ============================================================================
# RETRIEVAL — combina informe consolidado + historial por año
# ============================================================================
def fetch_context(lote_id: str, pregunta: str, top_k: int = 3) -> str:
    """
    Recupera contexto relevante para la pregunta dentro del lote dado.

    Estrategia de dos pasos:
      1. Busca en informes_lotes por similitud vectorial (siempre devuelve
         el registro consolidado del lote si existe).
      2. Enriquece con hasta (top_k - 1) filas de lote_historial ordenadas
         por relevancia temporal (años más recientes primero).

    De esta forma top_k controla la profundidad del contexto histórico
    entregado al LLM, no la cantidad de lotes distintos.

    Retorna un string listo para incluir en el prompt.
    """
    conn = _get_connection()
    cur = conn.cursor()
    try:
        fragmentos: list[str] = []

        # ── Paso 1: registro consolidado vía embedding ────────────────────────
        vec = _generate_embedding(pregunta)
        vec_str = f"[{', '.join(f'{v:.6f}' for v in vec)}]"
        cur.execute(
            """
            SELECT id, contenido_tecnico, ndvi_promedio, gdd_acumulados,
                   fecha, score_total, cultivo, superficie_ha
            FROM informes_lotes
            WHERE lote_id = %s
            ORDER BY embedding <=> %s::vector
            LIMIT 1
            """,
            (lote_id, vec_str),
        )
        row = cur.fetchone()
        if not row:
            return f"No hay datos registrados para el lote '{lote_id}'."

        id_, contenido, ndvi, gdd, fecha, score, cultivo, sup_ha = row
        fragmentos.append(
            f"[ID-{id_}] INFORME CONSOLIDADO | Lote: {lote_id} | "
            f"Cultivo: {cultivo} | Sup: {sup_ha} ha | Fecha: {fecha}\n"
            f"Score: {score}/100 | NDVI: {ndvi} | Estrés: {gdd}h\n"
            f"{contenido}"
        )

        # ── Paso 2: historial por año (top_k - 1 campañas más recientes) ─────
        anos_contexto = max(1, top_k - 1)
        cur.execute(
            """
            SELECT anio, cultivo, ndvi_critico, horas_calor,
                   score_total, score_vigor, score_estabilidad,
                   score_limpieza, score_clima, valido_para_score,
                   zonificacion_activa, puntos_zona_c
            FROM lote_historial
            WHERE lote_id = %s
            ORDER BY anio DESC
            LIMIT %s
            """,
            (lote_id, anos_contexto),
        )
        hist_rows = cur.fetchall()
        for h in hist_rows:
            (anio, cult, ndvi_c, horas, sc, vig, estab, limp, clim,
             valido, zona_act, zona_c_pts) = h
            valido_str = "Válido" if valido else "EXCLUIDO del Score"
            zona_str = f"Zonificado ({zona_c_pts} pts Zona C)" if zona_act else "Homogéneo"
            fragmentos.append(
                f"[Campaña {anio}] Registro histórico del año {anio} | Cultivo: {cult} | {valido_str}\n"
                f"  NDVI crítico: {ndvi_c} | Estrés: {horas}h | Score: {sc}/100\n"
                f"  Vigor: {vig} | Estabilidad: {estab} | "
                f"Limpieza: {limp} | Clima: {clim}\n"
                f"  Variabilidad espacial: {zona_str}"
            )

        return "\n\n".join(fragmentos)

    except Exception as e:
        logger.error(f"Error en fetch_context (lote={lote_id}): {e}")
        raise
    finally:
        cur.close()
        conn.close()


# ============================================================================
# GENERACIÓN
# ============================================================================
def consultar_agente(lote_id: str, pregunta: str, top_k: int = 3) -> str:
    """
    Pipeline RAG completo:
      1. Recupera contexto (informe consolidado + historial)
      2. Genera respuesta con el LLM configurado en settings
    """
    try:
        contexto = fetch_context(lote_id, pregunta, top_k)
        if contexto.startswith("No hay datos"):
            return f"⚠️ {contexto}"

        respuesta = ollama.chat(
            model=settings.generation_model,
            messages=[
                {"role": "system", "content": BASE_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Contexto recuperado:\n{contexto}\n\n"
                        f"Pregunta: {pregunta}"
                    ),
                },
            ],
            options={"temperature": 0.2, "num_predict": 1024},
        )
        return respuesta["message"]["content"]

    except Exception as e:
        logger.error(f"Error en consultar_agente (lote={lote_id}): {e}")
        return f"❌ Error al consultar el agente: {str(e)}"


# ============================================================================
# UTILIDADES
# ============================================================================
def listar_lotes() -> list[str]:
    """Retorna todos los lote_id únicos registrados en informes_lotes ORDER BY lote_id."""
    conn = _get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT DISTINCT lote_id FROM informes_lotes ORDER BY lote_id;")
        return [row[0] for row in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def get_historial_lote_raw(lote_id: str) -> list[dict]:
    """
    Devuelve la serie temporal completa de lote_historial como lista de dicts.
    Incluye todos los componentes del score por campaña.
    """
    conn = _get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT anio, cultivo, ndvi_critico, horas_calor,
                   score_total, score_vigor, score_estabilidad,
                   score_limpieza, score_clima, valido_para_score,
                   zonificacion_activa, puntos_zona_c
            FROM lote_historial
            WHERE lote_id = %s
            ORDER BY anio
        """, (lote_id,))
        rows = cur.fetchall()
        return [
            {
                "anio": r[0], "cultivo": r[1] or "N/D",
                "ndvi_critico": r[2] or 0.0, "horas_calor": r[3] or 0.0,
                "score_total": r[4] or 0, "score_vigor": r[5] or 0.0,
                "score_estabilidad": r[6] or 0.0, "score_limpieza": r[7] or 0.0,
                "score_clima": r[8] or 0.0,
                "valido_para_score": r[9] if r[9] is not None else True,
                "zonificacion_activa": r[10] or False,
                "puntos_zona_c": r[11] or 0,
            }
            for r in rows
        ]
    finally:
        cur.close()
        conn.close()


def get_datos_lote_raw(lote_id: str) -> dict | None:
    """
    Devuelve el registro consolidado de informes_lotes como dict.
    """
    conn = _get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, fecha, ndvi_promedio, gdd_acumulados,
                   score_total, cv_espacial, zona_activa, puntos_zona_c,
                   cultivo, superficie_ha, contenido_tecnico, metadata
            FROM informes_lotes WHERE lote_id = %s
        """, (lote_id,))
        row = cur.fetchone()
        if not row:
            return None
        raw_meta = row[11]
        meta = raw_meta if isinstance(raw_meta, dict) else (
            json.loads(raw_meta) if raw_meta else {}
        )
        return {
            "id": row[0], "fecha": row[1], "ndvi": row[2] or 0,
            "gdd": row[3] or 0, "score_total": row[4] or meta.get("score_total", 0),
            "cv": row[5] or meta.get("cv_espacial", 0),
            "zona_activa": row[6] or meta.get("zonificacion_activa", False),
            "puntos_zona_c": row[7] or meta.get("puntos_zona_c", 0),
            "cultivo": row[8] or meta.get("cultivo", "N/D"),
            "superficie_ha": row[9] or meta.get("superficie_ha", 0),
            "contenido": row[10],
            "score_desglose": meta.get("score_desglose", {}),
            "meta": meta,
        }
    finally:
        cur.close()
        conn.close()
