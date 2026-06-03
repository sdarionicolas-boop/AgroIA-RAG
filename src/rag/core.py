# src/rag/core.py
"""
AgroIA RAG — Core de consulta (Final Demo Version)
=================================================
Refactorizado para estabilidad total en Docker:
- Uso de requests directo (sin librería ollama).
- Manejo de nulos en retrieval.
- Configuración dinámica de host y modelo.
"""
import json
import logging
import psycopg2
import requests

try:
    from src.utils.config import settings
except ImportError:
    # Fallback cuando src/ está en sys.path (Streamlit, tests)
    from utils.config import settings

logger = logging.getLogger(__name__)

# ============================================================================
# PROMPT BASE
# ============================================================================
BASE_PROMPT = (
    "Eres un asistente agronómico experto del sistema AgroIA. "
    "Responde basándote estrictamente en el contexto recuperado. "
    "ADAPTA LA EXTENSIÓN: Si la pregunta es simple, responde de forma corta y directa. Si es compleja o pide análisis, sé profundo. "
    "No te limites a repetir números; analiza la relación entre ellos si es relevante.\n\n"
    "GLOSARIO TÉCNICO — Usá estas definiciones con PRECISIÓN, no por intuición semántica:\n"
    "• VIGOR (0-40 pts): NDVI crítico actual normalizado contra 0.9. Mide POTENCIAL PRODUCTIVO del año en curso. "
    "Bajo Vigor = cultivo con menor desarrollo de biomasa este año.\n"
    "• ESTABILIDAD (0-30 pts): inverso del coeficiente de variación temporal del NDVI histórico. "
    "Mide CONSISTENCIA INTERANUAL del rinde. Bajo = lote errático entre campañas.\n"
    "• LIMPIEZA (0-20 pts): salida de un IsolationForest sobre la serie NDVI histórica. "
    "Mide CALIDAD DEL DATO SATELITAL — penaliza años outliers (nubosidad, sombras, fallas de captura). "
    "IMPORTANTE: NO se refiere a malezas, limpieza del cultivo ni manejo de yuyos. Si baja, indica problemas en la confiabilidad de la serie satelital, no en el manejo del lote.\n"
    "• CLIMA (0-10 pts): horas sobre umbral térmico crítico (NASA POWER) vs umbral de penalización del cultivo. "
    "Bajo Clima = estrés térmico documentado en la ventana fenológica crítica.\n"
    "• CV ESPACIAL: coeficiente de variación intra-lote del NDVI. CV > 0.05 dispara zonificación A/B/C.\n"
    "• NDVI crítico: NDVI medio del lote en el mes fenológico más sensible (varía por cultivo).\n\n"
    "RAZONAMIENTO: Si un componente del Score está bajo, atribuyelo a SU CAUSA REAL según el glosario, "
    "no a su nombre coloquial. Si la pregunta confunde términos, aclaralo en la respuesta.\n\n"
    "REGLA DE COMPARACIÓN DE COMPONENTES: Cuando se pregunte cuál componente del Score está más castigado, "
    "o cuál afecta más al resultado, SIEMPRE armá primero una tabla comparativa con los CUATRO componentes "
    "(Vigor, Estabilidad, Limpieza, Clima) mostrando: valor obtenido / máximo posible / porcentaje del máximo. "
    "Recién DESPUÉS de la tabla, elegí el más castigado en términos relativos (% más bajo del máximo). "
    "NO saltes a una narrativa de un solo componente sin haber comparado los cuatro cuantitativamente.\n\n"
    "REGLA DE SIGNIFICANCIA: Cuando compares Score entre años, declará si la diferencia es relevante. "
    "Una variación de ±3 puntos o menor está dentro del ruido típico del sistema y NO debe interpretarse como cambio agronómico real. "
    "Solo diferencias >5 puntos ameritan análisis causal profundo.\n\n"
    "REGLA DE COHERENCIA EN RECOMENDACIONES: Si CV ESPACIAL > 0.05, una recorrida a campo SIEMPRE es útil "
    "(porque hay zonas diferenciadas dentro del lote que conviene inspeccionar). "
    "No recomiendes 'esperar' por motivos de variabilidad espacial — la variabilidad espacial es justamente "
    "la razón para ir al campo, no para evitarlo.\n\n"
    "CITAS: Usa [ID-X] para referirte al informe actual y [Campaña YYYY] para historial. "
    "Si no hay información suficiente, indicá 'Dato no disponible'."
)

# ============================================================================
# CONEXIÓN DB
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
# EMBEDDING (REST)
# ============================================================================
def _generate_embedding(text: str) -> list[float]:
    """Genera embedding vía API REST de Ollama."""
    url = f"{settings.ollama_url.rstrip('/')}/api/embed"
    payload = {"model": settings.embedding_model, "input": text}
    try:
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()["embeddings"][0]
    except Exception as e:
        logger.error(f"Error en embedding RAG: {e}")
        # Fallback para no romper el proceso si el embedding falla temporalmente
        return [0.0] * 768

# ============================================================================
# RETRIEVAL
# ============================================================================
def _build_score_ranking(score_desglose: dict, horas_calor: float, cultivo: str) -> str:
    """
    Pre-computa el ranking de componentes del Score por % del máximo (ascendente).
    Devuelve un bloque de texto listo para inyectar al contexto del LLM.

    Esto evita que modelos chicos (gemma3:4b) fallen al hacer matemática de ranking.
    """
    if not score_desglose:
        return ""

    vigor = float(score_desglose.get("vigor", 0) or 0)
    estab = float(score_desglose.get("estabilidad", 0) or 0)
    limp = float(score_desglose.get("limpieza", 0) or 0)
    clima = float(score_desglose.get("clima", 0) or 0)

    componentes = [
        ("Vigor", vigor, 40),
        ("Estabilidad", estab, 30),
        ("Limpieza", limp, 20),
        ("Clima", clima, 10),
    ]
    # Ordenar ascendente por % del máximo (el primero = el más castigado)
    componentes_ranked = sorted(componentes, key=lambda x: x[1] / x[2] if x[2] else 0)

    tabla = "\n".join([
        f"  {i+1}. {nombre}: {valor:.1f}/{maximo} ({(valor/maximo*100):.1f}% del máximo)"
        for i, (nombre, valor, maximo) in enumerate(componentes_ranked)
    ])

    nombre_mas, valor_mas, max_mas = componentes_ranked[0]
    pct_mas = valor_mas / max_mas * 100

    # Detectar alerta térmica explícita
    umbrales_clima = {"maiz": 40, "soja": 35, "trigo": 30, "girasol": 40, "cebada": 30, "sorgo": 45, "mani": 35}
    umbral = umbrales_clima.get(cultivo.lower(), 40)
    alerta_termica = ""
    if clima <= 1.0:  # clima rota cuando horas_calor >= umbral
        alerta_termica = (
            f"\n⚠ ALERTA TÉRMICA: Componente Clima en {clima:.1f}/10. "
            f"Horas sobre umbral térmico ({horas_calor:.1f}h) igualan o superan el umbral de penalización "
            f"({umbral}h para {cultivo}). El estrés térmico es la causa principal documentada de la caída del Score."
        )

    return (
        "=== RANKING PRE-COMPUTADO DE COMPONENTES (de MÁS castigado a MENOS) ===\n"
        f"{tabla}\n\n"
        f"→ COMPONENTE MÁS CASTIGADO: **{nombre_mas}** ({pct_mas:.1f}% del máximo).\n"
        f"  Usá EXACTAMENTE este ordenamiento en tu respuesta. NO recalcules ni cambies el orden."
        f"{alerta_termica}\n"
        "=== FIN RANKING ===\n"
    )


def _evaluar_significancia_score(score_actual: int, scores_historicos: list) -> str:
    """Pre-evalúa si una variación de Score es significativa o ruido."""
    if not scores_historicos:
        return ""
    score_prev = scores_historicos[0] if scores_historicos else None
    if score_prev is None:
        return ""
    delta = abs(score_actual - score_prev)
    if delta <= 3:
        return (
            f"\n=== SIGNIFICANCIA ESTADÍSTICA ===\n"
            f"Variación Score: {score_prev} → {score_actual} = {score_actual - score_prev:+d} pts.\n"
            f"Esta diferencia ({delta} pts) está dentro del ruido típico del sistema (±3 pts).\n"
            f"NO debe interpretarse como un cambio agronómico significativo.\n"
            f"=== FIN SIGNIFICANCIA ===\n"
        )
    elif delta > 5:
        return (
            f"\n=== SIGNIFICANCIA ESTADÍSTICA ===\n"
            f"Variación Score: {score_prev} → {score_actual} = {score_actual - score_prev:+d} pts.\n"
            f"Esta diferencia ({delta} pts) supera el umbral de ruido. AMERITA análisis causal profundo.\n"
            f"=== FIN SIGNIFICANCIA ===\n"
        )
    return ""


def fetch_context(lote_id: str, pregunta: str, top_k: int = 3) -> str:
    conn = _get_connection()
    cur = conn.cursor()
    try:
        fragmentos: list[str] = []
        vec = _generate_embedding(pregunta)
        vec_str = f"[{', '.join(f'{v:.6f}' for v in vec)}]"

        # ── 1. Intento de búsqueda de alta confianza (> 0.7) ──────────────────
        cur.execute("""
            SELECT id, contenido_tecnico, ndvi_promedio, gdd_acumulados,
                   fecha, score_total, cultivo, superficie_ha, metadata,
                   (1 - (embedding <=> %s::vector)) as similitud
            FROM informes_lotes
            WHERE lote_id = %s
            AND (1 - (embedding <=> %s::vector)) > 0.7
            ORDER BY similitud DESC
            LIMIT 1
        """, (vec_str, lote_id, vec_str))

        row = cur.fetchone()
        disclaimer = ""

        # ── 2. Fallback: búsqueda por lote_id sin umbral (si el embedding falla) ──
        if not row:
            cur.execute("""
                SELECT id, contenido_tecnico, ndvi_promedio, gdd_acumulados,
                       fecha, score_total, cultivo, superficie_ha, metadata, 0.0
                FROM informes_lotes
                WHERE lote_id = %s
                LIMIT 1
            """, (lote_id,))
            row = cur.fetchone()
            if row:
                disclaimer = "[Aviso: Respuesta basada en datos generales por baja confianza en la consulta semántica]\n"

        if not row:
            return f"No hay datos registrados para el lote '{lote_id}'."

        id_, contenido, ndvi, gdd, fecha, score, cultivo, sup_ha, raw_meta, sim = row

        # Parsear metadata para extraer score_desglose
        try:
            meta = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta or '{}')
        except Exception:
            meta = {}
        score_desglose = meta.get("score_desglose", {})

        fragmentos.append(
            f"{disclaimer}"
            f"[ID-{id_}] INFORME CONSOLIDADO (Confianza: {sim:.2f}) | Lote: {lote_id} | "
            f"Cultivo: {cultivo} | Sup: {sup_ha} ha | Fecha: {fecha}\n"
            f"Score: {score}/100 | NDVI: {ndvi} | Estrés: {gdd}h\n{contenido}"
        )

        # ── 3. RANKING PRE-COMPUTADO (evita que el LLM falle al rankear) ──────
        ranking_block = _build_score_ranking(score_desglose, float(gdd or 0), cultivo or "maiz")
        if ranking_block:
            fragmentos.append(ranking_block)

        # ── 4. Historial + significancia pre-evaluada ─────────────────────────
        cur.execute("""
            SELECT anio, cultivo, ndvi_critico, horas_calor, score_total
            FROM lote_historial WHERE lote_id = %s ORDER BY anio DESC LIMIT %s
        """, (lote_id, max(1, top_k - 1)))

        historial_rows = cur.fetchall()
        for h in historial_rows:
            fragmentos.append(f"[Campaña {h[0]}] Registro: {h[1]} | NDVI: {h[2]} | Score: {h[4]}/100")

        # Evaluar significancia del cambio respecto al año anterior
        scores_prev = [h[4] for h in historial_rows if h[4] is not None]
        sig_block = _evaluar_significancia_score(int(score) if score else 0, scores_prev)
        if sig_block:
            fragmentos.append(sig_block)

        return "\n\n".join(fragmentos)
    finally:
        cur.close()
        conn.close()

# ============================================================================
# GENERACIÓN (REST)
# ============================================================================
def _post_ollama_chat(url: str, payload: dict, timeout: int):
    """Wrapper de requests.post con retry on 404 (probable nombre de modelo)."""
    resp = requests.post(url, json=payload, timeout=timeout)
    if resp.status_code == 404:
        payload["model"] = f"{payload['model']}:latest"
        resp = requests.post(url, json=payload, timeout=timeout)
    return resp


def consultar_agente(lote_id: str, pregunta: str, top_k: int = 3) -> str:
    try:
        contexto = fetch_context(lote_id, pregunta, top_k)
        if contexto.startswith("No hay datos"): return f"⚠️ {contexto}"

        url = f"{settings.ollama_url.rstrip('/')}/api/chat"
        payload = {
            "model": settings.generation_model,
            "messages": [
                {"role": "system", "content": BASE_PROMPT},
                {"role": "user", "content": f"Contexto:\n{contexto}\n\nPregunta: {pregunta}"},
            ],
            "stream": False,
            "keep_alive": "10m",   # mantener el modelo en VRAM/RAM entre consultas
            "options": {
                "temperature": 0.2,
                "num_predict": 1024,   # techo de tokens para evitar runaway
                "num_ctx": 4096        # contexto suficiente sin saturar CPU
            }
        }

        # Intento principal con timeout extendido (cold start de gemma3:4b en CPU puede tardar 60-120s)
        try:
            resp = _post_ollama_chat(url, payload, timeout=240)
        except requests.exceptions.ReadTimeout:
            # Re-intento warm: ya hay un primer request que precalentó el modelo.
            logger.warning("Primer intento timeout — reintentando (modelo ya cargado en RAM)")
            resp = _post_ollama_chat(url, payload, timeout=180)

        resp.raise_for_status()
        return resp.json()["message"]["content"]
    except requests.exceptions.ReadTimeout:
        return ("⏳ El modelo tardó demasiado en responder. "
                "Probá una pregunta más corta, o esperá unos segundos a que el modelo termine de cargarse en memoria.")
    except Exception as e:
        return f"❌ Error en Asistente IA: {str(e)}"

# ============================================================================
# UTILIDADES
# ============================================================================
def listar_lotes():
    conn = _get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT DISTINCT lote_id FROM informes_lotes ORDER BY lote_id;")
        return [row[0] for row in cur.fetchall()]
    finally:
        cur.close()
        conn.close()

def get_historial_lote_raw(lote_id):
    """Retorna el historial de un lote desde la tabla lote_historial."""
    conn = _get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT anio, cultivo, ndvi_critico, horas_calor, score_total,
                   score_vigor, score_estabilidad, score_limpieza, score_clima,
                   valido_para_score, puntos_zona_c
            FROM lote_historial
            WHERE lote_id = %s
            ORDER BY anio DESC
        """, (lote_id,))
        rows = cur.fetchall()
        return [
            {
                "anio": r[0], "cultivo": r[1], "ndvi_critico": r[2],
                "horas_calor": r[3], "score_total": r[4],
                "score_vigor": r[5], "score_estabilidad": r[6],
                "score_limpieza": r[7], "score_clima": r[8],
                "valido": r[9], "zona_c_pts": r[10]
            }
            for r in rows
        ]
    finally:
        cur.close()
        conn.close()

def get_datos_lote_raw(lote_id):
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
        if not row: return None
        
        raw_meta = row[11]
        meta = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta or '{}')
        
        return {
            "id": row[0], 
            "fecha": row[1], 
            "ndvi": row[2] or 0, 
            "gdd": row[3] or 0,
            "score_total": row[4] or meta.get("score_total", 0), 
            "cv": row[5] or meta.get("cv_espacial", 0),
            "zona_activa": row[6] or meta.get("zonificacion_activa", False),
            "puntos_zona_c": row[7] or meta.get("puntos_zona_c", 0),
            "cultivo": row[8] or meta.get("cultivo", "N/D"), 
            "superficie_ha": row[9] or meta.get("superficie_ha", 0),
            "contenido": row[10],
            "score_desglose": meta.get("score_desglose", {}),
            "meta": meta
        }
    finally:
        cur.close()
        conn.close()
