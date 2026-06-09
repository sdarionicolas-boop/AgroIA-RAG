# src/streamlit_app.py
import sys
from pathlib import Path

# Inyectar src al path para poder importar utils y rag
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

# Evitar ValueError: signal only works in main thread al importar eodag en hilos secundarios de Streamlit
import signal
_original_signal = signal.signal
def _safe_signal(signalnum, handler):
    try:
        return _original_signal(signalnum, handler)
    except ValueError as e:
        if "signal only works in main thread" in str(e):
            return None
        raise e
signal.signal = _safe_signal

import streamlit as st
import psycopg2
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import folium
from streamlit_folium import st_folium
import streamlit.components.v1 as components
import tempfile
import os
import json
from datetime import datetime, timedelta

# Habilitar soporte para KML/KMZ
try:
    import fiona
    fiona.drvsupport.supported_drivers['KML'] = 'rw'
    fiona.drvsupport.supported_drivers['LIBKML'] = 'rw'
except ImportError:
    fiona = None
except:
    pass

# Importar el pipeline real
from src.pipeline import run_full_analysis, run_batch_from_geojson, OUTPUTS_DIR
# Importar el poligonizador (necesitaremos inyectar el path si no está)
from Poligonizacion.poligonizador_final import AgroIAPipeline

# Evitar advertencias de downcasting de pandas
pd.set_option('future.no_silent_downcasting', True)

from src.utils.config import settings  # config centralizada desde .env

# RAG centralizado — evita duplicar lógica de retrieval y generación
from src.rag.core import (
    consultar_agente,   # pipeline RAG completo (embedding + historial + LLM)
    fetch_context,      # retrieval puro (sin LLM), útil para debug
    get_datos_lote_raw, # datos consolidados del lote sin pasar por LLM
    BASE_PROMPT,        # prompt centralizado compartido con el bot
)

# Importar funciones del comparador
from src.pipeline.comparative_reporter import plot_ranking_chart, plot_scatter_variability, build_comparative_report
from src.pipeline.eventualidades import run_eventualidades, generar_mapa_folium, cargar_poligono_desde_gdf

# ================= CONFIGURACIÓN UI =================
ZONA_COLORS = {"A": "#40916C", "B": "#F4A261", "C": "#D62828"}
# ====================================================

# ============================================================================
# DB HELPERS (solo lo que NO está en rag.core — consultas de UI específicas)
# ============================================================================
def get_db_connection():
    return psycopg2.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
    )

def get_distinct_lotes():
    """Lista de lotes únicos cargados con sus coordenadas."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT lote_id, cultivo, superficie_ha, score_total, updated_at, cv_espacial, metadata
                FROM informes_lotes ORDER BY lote_id
            """)
            return cur.fetchall()

def get_datos_lote(lote_id: str) -> dict | None:
    """
    Alias de rag.core.get_datos_lote_raw con campos extra para la UI.
    Llama al core y agrega las claves adicionales que solo usa Streamlit.
    """
    datos = get_datos_lote_raw(lote_id)
    if not datos:
        return None
    meta = datos["meta"]
    datos.update({
        "ndvi_historico":   meta.get("ndvi_historico", {}),
        "horas_calor_hist": meta.get("horas_calor_hist", {}),
        "scores_por_año":   meta.get("scores_por_año", {}),
        "anos_excluidos":   meta.get("anos_excluidos", []),
        "centroide":        meta.get("centroide", []),
        "gdd":              datos.get("gdd", 0),   # alias explícito para la UI
    })
    return datos

def get_historial_lote(lote_id: str) -> pd.DataFrame | None:
    """Serie temporal desde lote_historial — una fila por año."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT anio, ndvi_critico, horas_calor,
                       score_total, score_vigor, score_estabilidad,
                       score_limpieza, score_clima,
                       valido_para_score, zonificacion_activa, puntos_zona_c
                FROM lote_historial WHERE lote_id = %s ORDER BY anio
            """, (lote_id,))  # ← ASCII: anio
            rows = cur.fetchall()
            if not rows:
                return None
            df = pd.DataFrame(rows, columns=[
                "anio", "ndvi", "horas_calor", "score", "vigor", "estabilidad",
                "limpieza", "clima", "valido", "zona_activa", "zona_c_pts"
            ])
            return df.rename(columns={"anio": "año"})  # ← Renombrar para UI

def reset_database():
    """Borra todos los lotes e historial de la base de datos."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE lote_historial, informes_lotes RESTART IDENTITY;")
        conn.commit()

# ============================================================================
# COMPARACIÓN DE LOTES
# (consultar_agente y fetch_context vienen de rag.core — ver imports arriba)
# ============================================================================
def _build_comparative_block(lote_a: str, lote_b: str, da: dict, db: dict) -> str:
    """
    Construye una TABLA COMPARATIVA pre-computada entre dos lotes.
    Garantiza que los valores de A y B nunca se crucen y declara explícitamente
    qué lote gana en cada componente. Inyectado al contexto para que el LLM
    (gemma3:4b) solo tenga que narrarlo, no calcularlo.
    """
    desg_a = da.get("score_desglose", {}) or {}
    desg_b = db.get("score_desglose", {}) or {}

    def _f(v, fmt=".3f"):
        try: return format(float(v), fmt)
        except (TypeError, ValueError): return "N/D"

    def _pct(val, maxv):
        try:
            v = float(val); m = float(maxv)
            return f"{v/m*100:.1f}%" if m else "N/D"
        except (TypeError, ValueError):
            return "N/D"

    score_a = float(da.get("score_total", 0) or 0)
    score_b = float(db.get("score_total", 0) or 0)
    ndvi_a  = float(da.get("ndvi", 0) or 0)
    ndvi_b  = float(db.get("ndvi", 0) or 0)
    gdd_a   = float(da.get("gdd", 0) or 0)
    gdd_b   = float(db.get("gdd", 0) or 0)
    cv_a    = float(da.get("cv", 0) or 0)
    cv_b    = float(db.get("cv", 0) or 0)

    componentes = [
        ("Vigor",       desg_a.get("vigor", 0),       desg_b.get("vigor", 0),       40),
        ("Estabilidad", desg_a.get("estabilidad", 0), desg_b.get("estabilidad", 0), 30),
        ("Limpieza",    desg_a.get("limpieza", 0),    desg_b.get("limpieza", 0),    20),
        ("Clima",       desg_a.get("clima", 0),       desg_b.get("clima", 0),       10),
    ]

    # Tabla markdown
    table = (
        f"| Métrica            | {lote_a} | {lote_b} |\n"
        f"|--------------------|----------|----------|\n"
        f"| Score total        | {score_a:.0f}/100  | {score_b:.0f}/100  |\n"
        f"| NDVI crítico       | {ndvi_a:.3f}      | {ndvi_b:.3f}      |\n"
        f"| Estrés térmico (h) | {gdd_a:.1f}       | {gdd_b:.1f}       |\n"
        f"| CV espacial        | {cv_a:.3f}        | {cv_b:.3f}        |\n"
    )
    for name, va, vb, maxv in componentes:
        va_f, vb_f = float(va or 0), float(vb or 0)
        table += (f"| {name:<18} | {va_f:.1f}/{maxv} ({_pct(va_f, maxv)}) "
                  f"| {vb_f:.1f}/{maxv} ({_pct(vb_f, maxv)}) |\n")

    # Significancia del cambio de Score
    score_diff = score_a - score_b
    if abs(score_diff) <= 3:
        sig = (f"⚠ DIFERENCIA DE SCORE: {score_diff:+.0f} pts — dentro del ruido típico (≤3 pts). "
               f"NO debe interpretarse como ventaja productiva real entre los lotes.")
    elif abs(score_diff) > 5:
        ganador_score = lote_a if score_a > score_b else lote_b
        sig = (f"→ DIFERENCIA DE SCORE: {score_diff:+.0f} pts a favor de {ganador_score} — "
               f"diferencia significativa (>5 pts), amerita análisis causal profundo.")
    else:
        sig = f"→ DIFERENCIA DE SCORE: {score_diff:+.0f} pts — diferencia leve, marginal."

    # Ganadores por componente (línea por línea, sin ambigüedad)
    ganadores = []
    # NDVI
    if abs(ndvi_a - ndvi_b) < 0.01: ganadores.append(f"  • NDVI crítico: empate técnico ({ndvi_a:.3f})")
    elif ndvi_a > ndvi_b:           ganadores.append(f"  • NDVI crítico: gana **{lote_a}** ({ndvi_a:.3f} vs {ndvi_b:.3f})")
    else:                            ganadores.append(f"  • NDVI crítico: gana **{lote_b}** ({ndvi_b:.3f} vs {ndvi_a:.3f})")
    # Estrés (menor es mejor)
    if abs(gdd_a - gdd_b) < 0.5: ganadores.append(f"  • Estrés térmico: empate técnico ({gdd_a:.1f}h)")
    elif gdd_a < gdd_b:          ganadores.append(f"  • Estrés térmico: **{lote_a}** sufre MENOS ({gdd_a:.1f}h vs {gdd_b:.1f}h)")
    else:                         ganadores.append(f"  • Estrés térmico: **{lote_b}** sufre MENOS ({gdd_b:.1f}h vs {gdd_a:.1f}h)")
    # Componentes del Score (mayor es mejor)
    for name, va, vb, maxv in componentes:
        va_f, vb_f = float(va or 0), float(vb or 0)
        if abs(va_f - vb_f) < 0.1:
            ganadores.append(f"  • {name}: empate técnico ({va_f:.1f}/{maxv})")
        elif va_f > vb_f:
            ganadores.append(f"  • {name}: gana **{lote_a}** ({va_f:.1f}/{maxv} vs {vb_f:.1f}/{maxv})")
        else:
            ganadores.append(f"  • {name}: gana **{lote_b}** ({vb_f:.1f}/{maxv} vs {va_f:.1f}/{maxv})")

    return (
        "=== TABLA COMPARATIVA PRE-COMPUTADA (USAR EXACTAMENTE ESTOS VALORES) ===\n"
        f"{table}\n"
        f"{sig}\n\n"
        "GANADOR POR MÉTRICA (no inviertas estos comparativos):\n"
        + "\n".join(ganadores) +
        "\n=== FIN TABLA COMPARATIVA ===\n"
    )


def comparar_dos_lotes(lote_a: str, lote_b: str) -> str:
    """
    Compara dos lotes usando el RAG centralizado.
    Recupera el contexto de cada lote por separado y los combina
    en un único prompt de comparación para el LLM.
    """
    try:
        da = get_datos_lote(lote_a)
        db = get_datos_lote(lote_b)
        if not da or not db:
            return "⚠️ Faltan datos para alguno de los lotes."

        # Contexto enriquecido para cada lote (informe + historial)
        ctx_a = fetch_context(lote_a, "score NDVI vigor estabilidad historial", top_k=4)
        ctx_b = fetch_context(lote_b, "score NDVI vigor estabilidad historial", top_k=4)

        # Bloque comparativo pre-computado en Python (evita errores de ranking del LLM)
        comparative_block = _build_comparative_block(lote_a, lote_b, da, db)

        prompt_comparacion = (
            f"{comparative_block}\n\n"
            f"=== DETALLE LOTE A: {lote_a} ===\n{ctx_a}\n\n"
            f"=== DETALLE LOTE B: {lote_b} ===\n{ctx_b}"
        )

        import requests
        url = f"{settings.ollama_url.rstrip('/')}/api/chat"
        payload = {
            "model": settings.generation_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        BASE_PROMPT + "\n"
                        "Estás realizando una CONSULTORÍA AGRONÓMICA COMPARATIVA. Sé profundo.\n"
                        "REGLA CRÍTICA DE COMPARACIÓN: El usuario te entregó una TABLA COMPARATIVA PRE-COMPUTADA "
                        "y una lista de GANADOR POR MÉTRICA. Estos datos son la VERDAD ABSOLUTA — "
                        "NO los recalcules, NO inviertas los comparativos, NO confundas qué lote tiene cuál valor. "
                        "Simplemente narrá lo que dice la tabla y explicá AGRONÓMICAMENTE las implicancias.\n"
                        "Estructura tu respuesta estrictamente así:\n"
                        "1. TABLA COMPARATIVA: Reproducí la tabla pre-computada tal cual (mismos valores, mismo orden).\n"
                        "2. ANÁLISIS DE RENDIMIENTO: Para cada métrica, narrá el ganador según la lista pre-computada. "
                        "NUNCA digas que un lote gana en algo si la lista dice lo contrario.\n"
                        "3. FACTORES LIMITANTES: Identificá qué componente arrastra más a cada lote (el que tiene "
                        "MENOR % del máximo según la tabla).\n"
                        "4. CONCLUSIÓN Y RECOMENDACIÓN: Cuál lote tiene mejor perspectiva. "
                        "Si la diferencia de Score está dentro del ruido (≤3), declaralo explícitamente."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Realiza un análisis comparativo exhaustivo entre estos dos lotes:\n{prompt_comparacion}\n\n"
                        "¿Cuál tiene mejor potencial productivo y por qué?"
                    ),
                },
            ],
            "stream": False,
            "keep_alive": "10m",
            "options": {"temperature": 0.3, "num_predict": 1536, "num_ctx": 4096},
        }

        try:
            resp = requests.post(url, json=payload, timeout=240)
        except requests.exceptions.ReadTimeout:
            # Reintento warm (modelo ya cargado en RAM tras el primer intento)
            resp = requests.post(url, json=payload, timeout=180)
        resp.raise_for_status()
        return resp.json()["message"]["content"]
    except requests.exceptions.ReadTimeout:
        return ("⏳ El modelo tardó demasiado en responder la comparación. "
                "Probá de nuevo en unos segundos (el modelo ya queda en memoria).")
    except Exception as e:
        return f"❌ Error en comparación: {e}"

# ============================================================================
# GRÁFICOS
# ============================================================================
def plot_ndvi_score(df: pd.DataFrame, lote_id: str):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 5), sharex=True)
    fig.patch.set_facecolor("white")
    válidos = df[df["valido"] == True]
    excluidos = df[df["valido"] == False]
    ax1.plot(válidos["año"], válidos["ndvi"], "o-", color="#40916C", linewidth=2, markersize=7, label="NDVI válido")
    if not excluidos.empty:
        ax1.scatter(excluidos["año"], excluidos["ndvi"], color="#ADB5BD", marker="X", s=80, zorder=4, label="Excluido")
    if len(válidos) >= 2:
        media, sigma = válidos["ndvi"].mean(), válidos["ndvi"].std()
        ax1.axhline(media, color="#2D6A4F", linestyle="--", alpha=0.7, linewidth=1.2, label=f"Media: {media:.3f}")
        ax1.axhspan(media - sigma, media + sigma, color="#D8F3DC", alpha=0.3)
    ax1.set_ylabel("NDVI crítico", fontsize=9)
    ax1.set_ylim(0, 1)
    ax1.legend(fontsize=8, loc="lower right")
    ax1.grid(axis="y", linestyle=":", alpha=0.4)
    ax1.set_title(f"Evolución histórica — {lote_id}", fontsize=11, loc="left")
    
    # Manejo de Nones en el Score para evitar errores en comparaciones y gráficos
    scores_limpios = df["score"].fillna(0).tolist()
    colores = ["#40916C" if s >= 70 else "#F4A261" if s >= 45 else "#D62828" for s in scores_limpios]
    
    ax2.bar(df["año"], scores_limpios, color=colores, edgecolor="white", linewidth=0.5, width=0.6)
    ax2.axhline(70, color="#40916C", linestyle="--", alpha=0.5, linewidth=1)
    ax2.axhline(45, color="#F4A261", linestyle="--", alpha=0.5, linewidth=1)
    ax2.set_ylabel("Score AgroIA", fontsize=9)
    ax2.set_ylim(0, 100)
    ax2.grid(axis="y", linestyle=":", alpha=0.4)
    plt.tight_layout()
    return fig

def plot_score_componentes(df: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(10, 3.5))
    fig.patch.set_facecolor("white")
    años = df["año"].tolist()
    
    # Asegurar que no haya Nones en los componentes
    vigor = df["vigor"].fillna(0).tolist()
    estab = df["estabilidad"].fillna(0).tolist()
    limp  = df["limpieza"].fillna(0).tolist()
    clima = df["clima"].fillna(0).tolist()
    
    x = np.arange(len(años))
    w = 0.6
    ax.bar(x, vigor, w, label="Vigor (40)", color="#40916C")
    ax.bar(x, estab, w, bottom=vigor, label="Estabilidad (30)", color="#2D6A4F")
    ax.bar(x, limp, w, bottom=[a+b for a,b in zip(vigor, estab)], label="Limpieza IA (20)", color="#74C69D")
    ax.bar(x, clima, w, bottom=[a+b+c for a,b,c in zip(vigor, estab, limp)], label="Clima (10)", color="#F4A261")
    ax.set_xticks(x)
    ax.set_xticklabels(años, fontsize=9)
    ax.set_ylim(0, 100)
    ax.set_ylabel("Puntos", fontsize=9)
    ax.set_title("Composición del Score por campaña", fontsize=10, loc="left")
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    ax.axhline(70, color="#40916C", linestyle="--", alpha=0.4, linewidth=1)
    ax.axhline(45, color="#D62828", linestyle="--", alpha=0.4, linewidth=1)
    plt.tight_layout()
    return fig

def plot_comparacion(da: dict, db: dict, nombre_a: str, nombre_b: str):
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    fig.patch.set_facecolor("white")
    ax = axes[0]
    lotes = [nombre_a, nombre_b]
    # Asegurar que los scores no sean None
    scores = [da.get("score_total", 0) or 0, db.get("score_total", 0) or 0]
    cols = ["#40916C" if s >= 70 else "#F4A261" if s >= 45 else "#D62828" for s in scores]
    bars = ax.bar(lotes, scores, color=cols, edgecolor="white", width=0.4)
    for bar, s in zip(bars, scores):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1, f"{s}/100", ha="center", fontsize=11, fontweight="bold")
    ax.set_ylim(0, 110)
    ax.set_title("Score AgroIA", fontsize=10)
    ax.axhline(70, color="#40916C", linestyle="--", alpha=0.4)
    ax.axhline(45, color="#D62828", linestyle="--", alpha=0.4)
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    ax2 = axes[1]
    componentes = ["Vigor\n(40)", "Estabilidad\n(30)", "Limpieza\n(20)", "Clima\n(10)"]
    vals_a = [da["score_desglose"].get("vigor", 0), da["score_desglose"].get("estabilidad", 0), da["score_desglose"].get("limpieza", 0), da["score_desglose"].get("clima", 0)]
    vals_b = [db["score_desglose"].get("vigor", 0), db["score_desglose"].get("estabilidad", 0), db["score_desglose"].get("limpieza", 0), db["score_desglose"].get("clima", 0)]
    x = np.arange(len(componentes))
    ax2.bar(x - 0.2, vals_a, 0.35, label=nombre_a, color="#40916C")
    ax2.bar(x + 0.2, vals_b, 0.35, label=nombre_b, color="#F4A261")
    ax2.set_xticks(x)
    ax2.set_xticklabels(componentes, fontsize=8)
    ax2.set_title("Componentes del Score", fontsize=10)
    ax2.legend(fontsize=8)
    ax2.grid(axis="y", linestyle=":", alpha=0.4)
    plt.tight_layout()
    return fig

# ============================================================================
# INTERFAZ STREAMLIT
# ============================================================================
st.set_page_config(page_title="AgroIA Asistente", page_icon="🌾", layout="wide")
st.title("🌾 AgroIA — Asistente de Diagnóstico Agronómico")

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    # Logo del proyecto
    logo_path = Path(__file__).resolve().parent / "utils" / "logo_agroia.png"
    if logo_path.exists():
        st.image(str(logo_path), use_container_width=True)
    else:
        st.markdown("### 🌾 AgroIA")

    st.markdown("### Configuración")
    lotes_info = get_distinct_lotes()
    lote_ids = [r[0] for r in lotes_info] if lotes_info else []
    
    modo = st.radio("Modo de análisis: ",
                    ["🗺️ Mapa de Lotes", "🔍 Inspeccionar un Lote", "⚖️ Comparar Lote A vs B", "🏆 Ranking Global", "🛰️ Siniestros (Eventualidades)", "⚠️ Prevención Super Niño"],
                    index=0)
    st.divider()


    if st.button("🗑️ Limpiar Base de Datos", use_container_width=True):
        reset_database()
        st.success("Base de datos reiniciada.")
        st.rerun()

    st.divider()

    # ── COMPLIANCE Y ARQUITECTURA B2B ──
    with st.expander("🛡️ Seguridad y Compliance B2B"):
        st.markdown("""
        **Modelo de Despliegue Híbrido**
        
        AgroIA protege la confidencialidad de tu cartera:
        - **Nube Pública (Copernicus)**: Solo se envían coordenadas geográficas anónimas para procesar NDVI.
        - **Servidores Propios (On-Premise / Local)**: La base de datos relacional/vectorial y el motor RAG (Ollama) corren localmente en la infraestructura de la aseguradora, impidiendo la fuga de información sensible.
        """)

    st.divider()

    # ── CARGA DE ARCHIVOS ────────────────────────────────────────────────────
    st.markdown("### 📂 Carga y Procesamiento")
    tipo_carga = st.radio("Tipo de archivo:", ["CSV/XLSX (puntos GPS)", "Polígonos (GeoJSON, SHP, KML)"], horizontal=True, label_visibility="collapsed")

    cultivo_default = "maiz"
    if tipo_carga == "CSV/XLSX (puntos GPS)":
        uploaded_file = st.file_uploader("Subir CSV con puntos GPS", type=["csv", "xlsx"])
    else:
        uploaded_file = st.file_uploader("Subir Polígonos", type=["geojson", "json", "shp", "kml", "kmz", "zip"])
        cultivo_default = st.selectbox("Cultivo para estos lotes:", ["maiz", "soja", "trigo", "girasol", "cebada", "sorgo", "mani"])

    if uploaded_file is not None:
        if st.button("🚀 Iniciar Procesamiento"):
            # Guardar archivo temporalmente
            suffix = Path(uploaded_file.name).suffix
            if suffix.lower() in [".zip", ".kmz", ".shp", ".kml"]:
                tmp_dir = Path(tempfile.gettempdir())
                tmp_path = str(tmp_dir / uploaded_file.name)
                with open(tmp_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
            else:
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
                    tmp_file.write(uploaded_file.getvalue())
                    tmp_path = tmp_file.name

            try:
                import time
                with st.status("🚜 Procesando Lotes...", expanded=True) as status:

                    if tipo_carga == "CSV/XLSX (puntos GPS)":
                        st.write("📐 Iniciando delineación de parcelas...")
                        df_input = pd.read_excel(tmp_path) if tmp_path.endswith('.xlsx') else pd.read_csv(tmp_path)
                        
                        # Mapeo de columnas
                        lat_col = next((c for c in df_input.columns if c.lower() in ['lat', 'latitude', 'latitud']), None)
                        lon_col = next((c for c in df_input.columns if c.lower() in ['lon', 'longitude', 'longitud', 'lng']), None)
                        id_col = next((c for c in df_input.columns if c.lower() in ['id', 'lote_id', 'nombre']), None)
                        
                        if not lat_col or not lon_col:
                            st.error("No se encontraron columnas de Latitud/Longitud.")
                            st.stop()

                        from shapely.geometry import Point, mapping
                        sam_path = _root / "sam_vit_b_01ec64.pth"
                        sam_available = sam_path.exists()
                        features = []

                        if sam_available:
                            st.write("🧠 Modelo SAM detectado. Usando delineación inteligente...")
                            try:
                                poly_pipe = AgroIAPipeline()
                                poly_pipe.cargar_datos(tmp_path)
                                if poly_pipe.df is not None:
                                    poly_pipe.df['cultivo'] = cultivo_default
                                out_prefix = os.path.join(tempfile.gettempdir(), f"agroia_st_{int(time.time())}")
                                generated_file = poly_pipe.ejecutar(output_prefix=out_prefix)
                            except Exception as e:
                                st.warning(f"⚠️ Error en la delineación inteligente con SAM: {e}")
                                generated_file = None
                            
                            if generated_file and os.path.exists(generated_file):
                                geojson_path = generated_file
                            else:
                                st.warning("⚠️ La delineación inteligente con SAM falló o no tiene suficiente confianza. Aplicando fallback de buffer geométrico automático...")
                                for idx, row in df_input.iterrows():
                                    lid = str(row[id_col]) if id_col else f"LOTE_{idx+1:03d}"
                                    lat, lon = float(row[lat_col]), float(row[lon_col])
                                    st.write(f"  → Aplicando buffer para {lid}...")
                                    poly_geom = Point(lon, lat).buffer(0.005).envelope
                                    features.append({
                                        "type": "Feature",
                                        "properties": {
                                            "id": lid,
                                            "cultivo": str(row.get('cultivo', cultivo_default)).lower(),
                                            "localidad": str(row.get('localidad', 'Desconocida'))
                                        },
                                        "geometry": mapping(poly_geom)
                                    })
                                    time.sleep(0.2)
                                
                                out_prefix = os.path.join(tempfile.gettempdir(), f"agroia_st_{int(time.time())}")
                                geojson_path = f"{out_prefix}.geojson"
                                with open(geojson_path, 'w') as f:
                                    json.dump({"type": "FeatureCollection", "features": features}, f)
                        else:
                            st.warning("⚠️ Modelo SAM no encontrado. Usando delineación geométrica (buffer)...")
                            for idx, row in df_input.iterrows():
                                lid = str(row[id_col]) if id_col else f"LOTE_{idx+1:03d}"
                                lat, lon = float(row[lat_col]), float(row[lon_col])
                                st.write(f"  → Procesando {lid}...")
                                # Envelope de buffer(0.005°): rectángulo de ~1110 m × ~909 m
                                # en latitud pampeana → área aproximada ~80–100 ha.
                                # (El comentario histórico "~500 m / ~25 ha" estaba mal:
                                # 0.005° es el RADIO del círculo, no el lado del cuadrado.)
                                poly_geom = Point(lon, lat).buffer(0.005).envelope
                                features.append({
                                    "type": "Feature",
                                    "properties": {
                                        "id": lid,
                                        "cultivo": str(row.get('cultivo', cultivo_default)).lower(),
                                        "localidad": str(row.get('localidad', 'Desconocida'))
                                    },
                                    "geometry": mapping(poly_geom)
                                })
                                time.sleep(0.2)
                            
                            out_prefix = os.path.join(tempfile.gettempdir(), f"agroia_st_{int(time.time())}")
                            geojson_path = f"{out_prefix}.geojson"
                            with open(geojson_path, 'w') as f:
                                json.dump({"type": "FeatureCollection", "features": features}, f)

                        if not os.path.exists(geojson_path):
                            st.error("Error al generar geometría. Revisa los datos de entrada.")
                            st.stop()
                    else:
                        # Flujo directo: GeoJSON cargado → Copernicus CDSE (analisis) → RAG
                        geojson_path = tmp_path
                        st.write(f"📂 Archivo {suffix} cargado, saltando delineacion SAM...")

                    st.write("🛰️ Ejecutando analisis satelital y scoring...")
                    
                    progress_bar = st.progress(0)
                    progress_text = st.empty()

                    def update_ui(idx, total, name):
                        pct = idx / total
                        progress_bar.progress(pct)
                        progress_text.markdown(f"**[{idx}/{total}]** Procesando: `{name}`...")

                    res_batch = run_batch_from_geojson(
                        geojson_path, 
                        cultivo_default=cultivo_default, 
                        push_to_rag=True,
                        progress_callback=update_ui
                    )

                    ok_count = sum(1 for r in res_batch if r["status"] == "OK")
                    fail_count = len(res_batch) - ok_count
                    status.update(label=f"✅ Procesamiento finalizado: {ok_count} lotes cargados", state="complete", expanded=False)

                if ok_count > 0:
                    st.success(f"Se procesaron {ok_count} lotes correctamente.")
                    if fail_count > 0:
                        with st.expander(f"⚠️ {fail_count} lotes sin datos satelitales"):
                            for r in res_batch:
                                if r["status"] != "OK":
                                    st.write(f"- **{r['lote_id']}**: {r['status']}")
                    st.balloons()
                    st.rerun()
                else:
                    st.warning("No se proceso ningun lote con exito.")
                    with st.expander("🔍 Detalle de errores por lote"):
                        for r in res_batch:
                            st.write(f"- **{r['lote_id']}**: {r['status']}")
                    st.info("💡 Posibles causas: Error de autenticación en CDSE, sin imágenes Sentinel-2 disponibles para las fechas, o conexión a internet inestable.")

            except Exception as e:
                st.error(f"❌ Error en el proceso: {e}")
            finally:
                if os.path.exists(tmp_path): os.remove(tmp_path)

    st.divider()

    if not lotes_info and modo != "🗺️ Mapa de Lotes" and modo != "🛰️ Siniestros (Eventualidades)" and modo != "⚠️ Prevención Super Niño":
        st.warning("📭 No hay lotes cargados en la base de datos.")
        st.info("Subí un archivo CSV para empezar.")
        st.stop()
    if modo in ("🔍 Inspeccionar un Lote", "⚠️ Prevención Super Niño"):
        lote_a = st.selectbox("Seleccioná el lote", lote_ids)
        if modo == "🔍 Inspeccionar un Lote":
            ver_historial = st.checkbox("📈 Evolución histórica", value=True)
            ver_componentes = st.checkbox("📊 Componentes del Score", value=True)
    elif modo == "⚖️ Comparar Lote A vs B":
        col1, col2 = st.columns(2)
        with col1:
            lote_a = st.selectbox("Lote A", lote_ids, key="ca")
        with col2:
            otros = [l for l in lote_ids if l != lote_a]
            lote_b = st.selectbox("Lote B", otros if otros else lote_ids, key="cb")
    elif modo == "🏆 Ranking Global":
        st.info("Ranking comparativo de todos los lotes en la base de datos.")
        if st.button("📄 Generar Reporte PDF"):
            try:
                # Reutilizamos la lógica de comparar_lotes.py pero adaptada a Streamlit
                lotes_raw = get_distinct_lotes()
                df_ranking = pd.DataFrame(lotes_raw, columns=["lote_id", "cultivo", "superficie_ha", "score_total", "updated_at", "cv_espacial", "metadata"])
                
                output_dir = Path("outputs")
                output_dir.mkdir(exist_ok=True)
                pdf_path = output_dir / "Ranking_Comparativo_AgroIA.pdf"
                
                build_comparative_report(df_ranking, str(pdf_path))
                
                with open(pdf_path, "rb") as f:
                    st.download_button(
                        label="⬇️ Descargar Ranking (PDF)",
                        data=f.read(),
                        file_name="Ranking_Comparativo_AgroIA.pdf",
                        mime="application/pdf"
                    )
            except Exception as e:
                st.error(f"Error al generar PDF: {e}")
    st.divider()
    st.caption("AgroIA v2.5.0 | Copernicus CDSE (Sentinel-2) + NASA POWER")

    # ── Administración de base de datos ──────────────────────────────────────
    with st.expander("🗑️ Administración de datos", expanded=False):
        st.caption("Usá estas opciones para limpiar datos de prueba antes de la demo.")

        st.markdown("**Borrar un lote específico**")
        lote_borrar = st.selectbox(
            "Seleccioná el lote a eliminar",
            lote_ids,
            key="admin_lote_borrar"
        )
        confirmar_uno = st.checkbox(
            f'Confirmo que quiero borrar "{lote_borrar}" y todo su historial',
            key="confirmar_uno"
        )
        if st.button("🗑️ Borrar este lote", disabled=not confirmar_uno, key="btn_borrar_uno"):
            try:
                import requests as _req
                # Usa settings.api_url (resuelve a http://api:8000 en Docker, http://localhost:8000 local)
                r = _req.delete(
                    f"{settings.api_url.rstrip('/')}/lotes/{lote_borrar}",
                    headers={"Authorization": f"Bearer {settings.ingesta_secret_key}"},
                    timeout=10,
                )
                if r.status_code == 200:
                    d = r.json()
                    st.success(f'✅ Lote "{lote_borrar}" eliminado ({d.get("historial_eliminado", 0)} registros históricos).')
                    st.rerun()
                elif r.status_code == 404:
                    st.warning(f'Lote "{lote_borrar}" no encontrado.')
                else:
                    st.error(f"Error {r.status_code}: {r.text}")
            except Exception as e:
                st.error(f"❌ Error al conectar con la API: {e}")

        st.divider()

        st.markdown("**Limpiar toda la base de datos**")
        st.warning("⚠️ Esta acción elimina todos los lotes y su historial. Es irreversible.")
        confirmar_todo = st.checkbox(
            "Confirmo que quiero borrar TODOS los lotes",
            key="confirmar_todo"
        )
        if st.button("🧹 Limpiar base de datos completa", disabled=not confirmar_todo, key="btn_borrar_todo",
                     type="primary"):
            try:
                import requests as _req
                # Usa settings.api_url (resuelve a http://api:8000 en Docker, http://localhost:8000 local)
                r = _req.delete(
                    f"{settings.api_url.rstrip('/')}/lotes",
                    headers={"Authorization": f"Bearer {settings.ingesta_secret_key}"},
                    timeout=10,
                )
                if r.status_code == 200:
                    d = r.json()
                    st.success(f'✅ Base de datos limpiada: {d.get("lotes_eliminados", 0)} lote(s) eliminados.')
                    st.rerun()
                else:
                    st.error(f"Error {r.status_code}: {r.text}")
            except Exception as e:
                st.error(f"❌ Error al conectar con la API: {e}")


# ── Modo: Mapa ───────────────────────────────────────────────────────────────
if modo == "🗺️ Mapa de Lotes":
    st.subheader("🗺️ Visualizador Geográfico de la Cartera")
    st.markdown("Mapa interactivo con la ubicación y estado de salud de todos los lotes analizados.")
    
    # Calcular centro promedio de los lotes cargados
    if lotes_info:
        coords = []
        for r in lotes_info:
            meta = r[6] if isinstance(r[6], dict) else json.loads(r[6] or '{}')
            c = meta.get("centroide", [])
            if c: coords.append(c)
        
        if coords:
            lat_c = sum(p[0] for p in coords) / len(coords)
            lon_c = sum(p[1] for p in coords) / len(coords)
            start_loc = [lat_c, lon_c]
            zoom = 12
        else:
            start_loc, zoom = [-36.0, -61.0], 6
    else:
        start_loc, zoom = [-36.0, -61.0], 6

    m = folium.Map(location=start_loc, zoom_start=zoom, tiles="CartoDB positron")
    
    # Añadir marcadores de lotes existentes
    for r in lotes_info:
        lid, cult, sup, sc, _, _, raw_meta = r
        # Aseguramos que meta sea un diccionario
        meta = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta or '{}')
        
        # BUSQUEDA DEL CENTROIDE: 
        c = meta.get("centroide", [])
        if isinstance(c, dict):
            c = [c.get('lat', 0), c.get('lon', 0)]
        
        if c and len(c) == 2 and c[0] != 0:
            color = "green" if sc >= 70 else "orange" if sc >= 45 else "red"
            folium.Marker(
                location=c,
                popup=f"<b>Lote:</b> {lid}<br><b>Cultivo:</b> {cult}<br><b>Score:</b> {sc}/100",
                tooltip=f"{lid} ({sc} pts)",
                icon=folium.Icon(color=color, icon="leaf")
            ).add_to(m)

    st_folium(m, height=700, width="100%", key="mapa_full")
    
    st.info("💡 Haz clic en los marcadores para ver un resumen rápido. Para un diagnóstico profundo, usa la pestaña 'Inspeccionar un Lote'.")

# ── Modo: Inspeccionar ───────────────────────────────────────────────────────
elif modo == "🔍 Inspeccionar un Lote":
    datos = get_datos_lote(lote_a)
    if not datos:
        st.warning(f"Sin datos para **{lote_a}**. Verificá la ingesta desde Colab.")
        st.stop()
    st.subheader(f"📋 {lote_a} — {datos['cultivo'].capitalize()} | {datos['superficie_ha']} ha")
    st.caption(f"Último análisis: {datos['fecha']} | ID DB: {datos['id']}")
    c1, c2, c3, c4 = st.columns(4)
    score = datos.get("score_total", 0)
    score_delta = None
    df_hist = get_historial_lote(lote_a)
    if df_hist is not None and len(df_hist) >= 2:
        # Aseguramos que score_prev no sea None antes de convertir a int
        val_prev = df_hist.iloc[-2].get("score")
        if score is not None and val_prev is not None:
            score_delta = int(score) - int(val_prev)
    c1.metric("🏆 Score AgroIA", f"{score}/100", delta=f"{score_delta:+d} vs año ant." if score_delta is not None else None)
    c2.metric("🛰️ NDVI crítico", f"{datos['ndvi']:.3f}")
    c3.metric("🌡️ Estrés térmico", f"{datos['gdd']:.1f} h")
    c4.metric("📐 CV espacial", f"{datos['cv']:.3f}" if datos.get('cv') is not None else "N/D")
    
    # ── Ficha de Auditoría en Cascada (IsolationForest → VigorDAE) ───────────
    st.divider()
    st.markdown("### 🔬 Auditoría de Serie Satelital — Cascada de IA")
    st.caption(
        "AgroIA audita la calidad del NDVI histórico en dos pasos: "
        "**(1)** un IsolationForest detecta outliers rápidos en el pipeline, "
        "**(2)** si la confianza cae, se recomienda derivar a **VigorDAE**, un autoencoder LSTM "
        "entrenado para reconstruir series satelitales contaminadas por nubes y sombras."
    )

    col_inf, col_audit = st.columns([2, 1])

    with col_inf:
        # Pre-auditoría interna: IsolationForest ya ejecutado en el pipeline.
        confiabilidad = 100
        n_invalidos = 0
        if df_hist is not None:
            n_invalidos = len(df_hist[df_hist["valido"] == False])
            confiabilidad = max(0, 100 - (n_invalidos * 20))

        # Mostrar el componente Limpieza del Score (output real del IsolationForest)
        limpieza_score = datos.get("score_desglose", {}).get("limpieza", None)
        limpieza_txt = f" · **Limpieza IA (Score): {limpieza_score:.1f}/20**" if limpieza_score is not None else ""

        if confiabilidad >= 80:
            st.success(
                f"✅ **Pre-Auditoría IsolationForest: {confiabilidad}% confiable**{limpieza_txt}\n\n"
                f"La serie temporal de NDVI no presenta años outliers detectables. "
                f"El Score se calculó sobre datos confiables — no se requiere capa de corrección adicional."
            )
        else:
            st.error(
                f"🚨 **Pre-Auditoría IsolationForest: {confiabilidad}% confiable**{limpieza_txt}\n\n"
                f"Se detectaron **{n_invalidos} año(s) anómalos** en la serie histórica "
                f"(probable contaminación por nubes/sombras no filtradas por la máscara SCL).\n\n"
                f"➡ **Derivación recomendada a VigorDAE** — el autoencoder LSTM reconstruirá los puntos "
                f"sospechosos para validar si el Score subestima o sobreestima el potencial real del lote."
            )
            st.info(
                "💡 Descargá el CSV de la serie en el panel de la derecha y subilo a la HF Space de VigorDAE. "
                "Los valores reconstruidos pueden re-ingresarse al pipeline para recalcular el Score con datos auditados."
            )

    with col_audit:
        st.markdown("**🧠 Capa 2 — VigorDAE**")
        st.caption("Autoencoder LSTM para reconstrucción profunda de series satelitales.")
        if df_hist is not None:
            # Preparar CSV para VigorDAE (formato compatible con la HF Space)
            csv_data = df_hist[["año", "ndvi", "score"]].to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 1. Descargar serie del lote",
                data=csv_data,
                file_name=f"serie_vigor_{lote_a}.csv",
                mime="text/csv",
                use_container_width=True,
                help="CSV con (año, ndvi, score) — formato esperado por VigorDAE."
            )
            st.markdown(
                """
                <a href="https://huggingface.co/spaces/sdarionicolas-boop/AgroIA-VigorDAE"
                   target="_blank" style="text-decoration: none;">
                  <button style="background-color: #3fb950; color: white; border: none;
                                 padding: 8px 12px; border-radius: 4px; font-weight: bold;
                                 width: 100%; cursor: pointer; margin-top: 8px;">
                    🚀 2. Abrir VigorDAE LSTM
                  </button>
                </a>
                """,
                unsafe_allow_html=True
            )

    # ── Ficha de Alerta Temprana ─────────────────────────────────────────────
    if df_hist is not None and len(df_hist) >= 2:
        ndvi_actual = float(datos['ndvi'])
        ndvi_hist_vals = pd.to_numeric(df_hist.iloc[:-1]['ndvi'], errors='coerce').dropna().tolist()
        if ndvi_hist_vals:
            from pipeline.agro_math import detectar_alerta_ndvi
            alerta = detectar_alerta_ndvi(ndvi_actual, ndvi_hist_vals)
            if alerta['alerta']:
                st.error(f"🚨 **FICHA DE ALERTA TEMPRANA**\n\n{alerta['msg']}")
            elif alerta['caida_pct'] > 5:
                st.warning(f"⚠️ **Observación de Vigor:** Se detecta una tendencia a la baja del {alerta['caida_pct']}% respecto al promedio histórico.")

    sd = datos["score_desglose"]
    if sd:
        cols = st.columns(4)
        cols[0].progress(int(sd.get("vigor", 0) / 40 * 100), text=f"Vigor: {sd.get('vigor', 0):.1f}/40")
        cols[1].progress(int(sd.get("estabilidad", 0) / 30 * 100), text=f"Estabilidad: {sd.get('estabilidad', 0):.1f}/30")
        cols[2].progress(int(sd.get("limpieza", 0) / 20 * 100), text=f"Limpieza IA: {sd.get('limpieza', 0):.1f}/20")
        cols[3].progress(int(sd.get("clima", 0) / 10 * 100), text=f"Clima: {sd.get('clima', 0):.1f}/10")
    # ── Lógica de Estado de Zonificación ─────────────────────────────────────
    # OUTPUTS_DIR es la única fuente de verdad para mapas/PDFs (Docker o local)
    mapa_path = OUTPUTS_DIR / f"Mapa_{lote_a}.html"
    mapa_existe = mapa_path.exists()

    if datos.get("zona_activa") or (mapa_existe and datos.get("cv", 0) > 0.05):
        st.info(f"🗺️ **Zonificación A/B/C activa** — Se detectaron variaciones significativas de vigor. **{datos.get('puntos_zona_c', 0)} puntos críticos** en Zona C.")
    elif datos.get("cv") is not None and datos.get("cv") < 0.05:
        st.success("✅ **Lote Homogéneo** — La variabilidad espacial es baja. No requiere manejo diferenciado.")
    elif mapa_existe:
         st.info(f"🗺️ **Mapa de Variabilidad disponible** — Inspeccioná el mapa detallado para ver la distribución de vigor.")
    else:
        st.warning("⏳ **Zonificación Pendiente** — El análisis de variabilidad espacial requiere el procesamiento de imágenes de alta resolución (Sentinel-2).")
    
    # ── Visualización de Mapa Detallado ──────────────────────────────────────
    st.divider()
    st.subheader("🗺️ Mapa Detallado de Lote")
    
    if mapa_existe:
        with open(mapa_path, "r", encoding="utf-8") as f:
            html_content = f.read()
            components.html(html_content, height=500, scrolling=True)
        
        col_down, _ = st.columns([2, 3])
        with col_down:
            with open(mapa_path, "rb") as f:
                st.download_button(
                    label="🌐 Descargar Mapa Interactivo (HTML)",
                    data=f,
                    file_name=f"Mapa_{lote_a}.html",
                    mime="text/html",
                    use_container_width=True
                )
        st.caption("Mapa interactivo con polígono, satélite y zonificación (si aplica).")
    else:
        st.info("💡 No se encontró el mapa detallado offline. Se muestra ubicación general:")
        if datos.get("centroide"):
            m_single = folium.Map(location=datos["centroide"], zoom_start=15, tiles='OpenStreetMap')
            folium.Marker(datos["centroide"], popup=lote_a).add_to(m_single)
            st_folium(m_single, height=400, width="100%", key=f"map_mini_{lote_a}")

    st.divider()
    if df_hist is not None and not df_hist.empty:
        if ver_historial:
            st.subheader("📈 Evolución histórica")
            st.pyplot(plot_ndvi_score(df_hist, lote_a))
        if ver_componentes:
            st.subheader("📊 Componentes del Score por campaña")
            st.pyplot(plot_score_componentes(df_hist))
        with st.expander("🗂️ Tabla detallada por campaña"):
            df_show = df_hist[["año", "ndvi", "horas_calor", "score",
                               "vigor", "estabilidad", "limpieza", "clima",
                               "valido", "zona_c_pts"]].copy()
            df_show.columns = ["Año", "NDVI", "Estrés (h)", "Score",
                               "Vigor", "Estab.", "Limp.", "Clima",
                               "Válido", "Pts Zona C"]
            # Convertir a numérico para evitar errores en round()
            for col in ["NDVI", "Estrés (h)", "Score", "Vigor", "Estab.", "Limp.", "Clima"]:
                df_show[col] = pd.to_numeric(df_show[col], errors='coerce')
                
            df_show["NDVI"] = df_show["NDVI"].round(3)
            df_show["Estrés (h)"] = df_show["Estrés (h)"].round(1)
            for col in ["Vigor", "Estab.", "Limp.", "Clima"]:
                df_show[col] = df_show[col].round(1)
            st.dataframe(df_show, use_container_width=True, hide_index=True)
    else:
        st.info("Cargá datos desde Colab para ver la evolución histórica.")
    st.divider()
    st.subheader("🤖 Consulta al Asistente AgroIA")
    if "msgs" not in st.session_state:
        st.session_state.msgs = []
    for m in st.session_state.msgs:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
    if pregunta := st.chat_input(f"Preguntá sobre {lote_a}..."):
        st.session_state.msgs.append({"role": "user", "content": pregunta})
        with st.chat_message("user"):
            st.markdown(pregunta)
        with st.chat_message("assistant"):
            with st.spinner("Consultando base de conocimientos..."):
                resp = consultar_agente(lote_a, pregunta)
            st.markdown(resp)
        st.session_state.msgs.append({"role": "assistant", "content": resp})

# ── Modo: Comparar ───────────────────────────────────────────────────────────
elif modo == "⚖️ Comparar Lote A vs B":
    st.subheader(f"⚔️ Comparativa: {lote_a}  vs  {lote_b}")
    st.caption("Se comparan los datos más recientes de cada lote.")
    da = get_datos_lote(lote_a)
    db = get_datos_lote(lote_b)
    if not da or not db:
        st.warning("Faltan datos para alguno de los lotes.")
        st.stop()
    col_a, col_sep, col_b = st.columns([5, 1, 5])
    with col_a:
        st.markdown(f"**{lote_a}** — {da['cultivo'].capitalize()}")
        st.metric("Score", f"{da['score_total']}/100")
        st.metric("NDVI", f"{da['ndvi']:.3f}")
        st.metric("Estrés", f"{da['gdd']:.1f}h")
        st.metric("Zona C pts", da["puntos_zona_c"])
    with col_sep:
        st.markdown("<div style='border-left:1px solid #dee2e6;height:150px;margin:auto'></div>", unsafe_allow_html=True)
    with col_b:
        st.markdown(f"**{lote_b}** — {db['cultivo'].capitalize()}")
        st.metric("Score", f"{db['score_total']}/100", delta=f"{db['score_total'] - da['score_total']:+d}")
        st.metric("NDVI", f"{db['ndvi']:.3f}", delta=f"{db['ndvi'] - da['ndvi']:+.3f}")
        st.metric("Estrés", f"{db['gdd']:.1f}h", delta=f"{db['gdd'] - da['gdd']:+.1f}h")
        st.metric("Zona C pts", db["puntos_zona_c"])
    st.divider()
    st.pyplot(plot_comparacion(da, db, lote_a, lote_b))
    st.divider()
    st.subheader("🤖 Análisis comparativo IA")
    if st.button("🚀 Ejecutar análisis comparativo"):
        with st.spinner("Analizando diferencias agronómicas..."):
            resultado = comparar_dos_lotes(lote_a, lote_b)
        st.markdown(resultado)
    df_a = get_historial_lote(lote_a)
    df_b = get_historial_lote(lote_b)
    if df_a is not None and df_b is not None:
        with st.expander("📊 Evolución histórica comparada"):
            fig, ax = plt.subplots(figsize=(10, 3.5))
            ax.plot(df_a["año"], df_a["ndvi"], "o-", color="#40916C", label=lote_a, linewidth=2)
            ax.plot(df_b["año"], df_b["ndvi"], "s--", color="#F4A261", label=lote_b, linewidth=2)
            ax.set_ylabel("NDVI crítico")
            ax.set_title("NDVI histórico comparado")
            ax.legend()
            ax.grid(axis="y", linestyle=":", alpha=0.4)
            plt.tight_layout()
            st.pyplot(fig)

# ── Modo: Ranking Global ─────────────────────────────────────────────────────
elif modo == "🏆 Ranking Global":
    st.subheader("🏆 Ranking Global de Lotes")
    lotes_raw = get_distinct_lotes()
    if not lotes_raw:
        st.warning("No hay lotes para mostrar.")
        st.stop()

    df_ranking = pd.DataFrame(lotes_raw, columns=["lote_id", "cultivo", "superficie_ha", "score_total", "updated_at", "cv_espacial", "metadata"])
    df_ranking = df_ranking.sort_values("score_total", ascending=False)

    # Resumen rápido
    c1, c2, c3 = st.columns(3)
    c1.metric("Lotes Analizados", len(df_ranking))
    c2.metric("Superficie Total", f"{df_ranking['superficie_ha'].sum():.1f} ha")
    c3.metric("Score Promedio", f"{df_ranking['score_total'].mean():.1f}/100")

    st.divider()

    col_chart, col_scatter = st.columns(2)
    with col_chart:
        st.markdown("**Ranking por Potencial**")
        st.pyplot(plot_ranking_chart(df_ranking))
    with col_scatter:
        st.markdown("**Potencial vs. Heterogeneidad**")
        st.pyplot(plot_scatter_variability(df_ranking))

    st.divider()
    st.markdown("**Detalle de la Cartera**")
    df_show = df_ranking[["lote_id", "cultivo", "superficie_ha", "score_total", "cv_espacial"]].copy()
    df_show.columns = ["Lote ID", "Cultivo", "Superficie (ha)", "Score", "CV Espacial"]
    st.dataframe(df_show, use_container_width=True, hide_index=True)

# ── Modo: Siniestros (Eventualidades) ────────────────────────────────────────
elif modo == "🛰️ Siniestros (Eventualidades)":
    st.subheader("🛰️ Evaluación Satelital de Siniestros (Eventualidades)")
    st.markdown("""
    Este módulo evalúa el impacto de eventos climáticos comparando la salud del cultivo 
    **antes y después** del siniestro, con delineación automática de lotes (SAM).
    """)

    # Inicializar estado si no existe
    if 'siniestro_res' not in st.session_state:
        st.session_state.siniestro_res = None
    if 'gdf_ev_cache' not in st.session_state:
        st.session_state.gdf_ev_cache = None

    with st.expander("📝 Configuración del Análisis", expanded=st.session_state.siniestro_res is None):
        st.info("💡 **Formatos:** ZIP (Shapefile), GeoJSON, KML o KMZ.")
        up_file = st.file_uploader("Subir Polígono o Puntos del Lote", type=["geojson", "kml", "kmz", "shp", "zip"])

        if up_file:
            # Cargar archivo a memoria (cachear para no re-leer)
            suffix = Path(up_file.name).suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(up_file.getvalue())
                tmp_path = tmp.name

            try:
                # Intentar leer el archivo
                read_path = tmp_path
                if suffix.lower() in ['.zip', '.kmz']: read_path = f"zip://{tmp_path}"
                
                try:
                    import fiona
                    with fiona.Env(SHAPE_RESTORE_SHX='YES'):
                        gdf_raw = gpd.read_file(read_path)
                except:
                    gdf_raw = gpd.read_file(read_path)
                
                if gdf_raw.crs is None:
                    gdf_raw = gdf_raw.set_crs("EPSG:4326")
                elif gdf_raw.crs.to_epsg() != 4326:
                    gdf_raw = gdf_raw.to_crs("EPSG:4326")
                
                st.session_state.gdf_ev_cache = gdf_raw
            except Exception as e:
                # Fallback KML manual
                if suffix.lower() in ['.kml', '.kmz']:
                    from src.utils.kml_fallback import parse_kml_manual
                    manual_data = parse_kml_manual(tmp_path)
                    if manual_data:
                        tmp_json = f"{tmp_path}_tmp.json"
                        with open(tmp_json, 'w') as f: json.dump(manual_data, f)
                        st.session_state.gdf_ev_cache = gpd.read_file(tmp_json)
                        os.remove(tmp_json)
                    else:
                        st.error(f"No se pudo parsear el archivo: {e}")
                else:
                    st.error(f"Error al leer archivo: {e}")
            finally:
                if os.path.exists(tmp_path): os.remove(tmp_path)

        # Si tenemos datos cargados, configurar el peritaje
        if st.session_state.gdf_ev_cache is not None:
            gdf = st.session_state.gdf_ev_cache
            st.write(f"📂 Archivo con **{len(gdf)}** registro(s) detectado.")
            
            # Selector de punto/lote si hay varios
            id_col = next((c for c in gdf.columns if c.lower() in ['name', 'id', 'lote', 'nombre']), gdf.columns[0])
            options = [f"{i}: {row[id_col]}" for i, row in gdf.iterrows()]
            selected_idx = st.selectbox("Seleccionar registro a analizar:", range(len(options)), format_func=lambda x: options[x])
            
            # Extraer fila seleccionada
            target_row = gdf.iloc[[selected_idx]]
            
            # Autodetectar Fecha
            date_col = next((c for c in gdf.columns if c.lower() in ['fecha_de_s', 'fecha', 'timestamp', 'date']), None)
            default_date = datetime.now() - timedelta(days=30)
            if date_col and pd.notna(target_row.iloc[0][date_col]):
                val = target_row.iloc[0][date_col]
                try:
                    if isinstance(val, str): 
                        # Intentar formatos comunes
                        for fmt in ['%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y']:
                            try:
                                default_date = datetime.strptime(val, fmt)
                                break
                            except: continue
                    else: default_date = pd.to_datetime(val)
                    st.success(f"📅 Fecha detectada en archivo: **{default_date.strftime('%d/%m/%Y')}**")
                except: pass

            # Autodetectar Cultivo
            cult_col = next((c for c in gdf.columns if c.lower() in ['cultivo', 'crop']), None)
            cult_list = ["maiz", "soja", "trigo", "girasol", "cebada"]
            default_cult_idx = 0
            if cult_col and pd.notna(target_row.iloc[0][cult_col]):
                c_val = str(target_row.iloc[0][cult_col]).lower()
                if c_val in cult_list: default_cult_idx = cult_list.index(c_val)
                st.success(f"🌾 Cultivo detectado en archivo: **{c_val.capitalize()}**")

            col1, col2 = st.columns(2)
            with col1:
                fecha_ev = st.date_input("Confirmar Fecha del Siniestro", value=default_date)
                tipo_ev = st.selectbox("Tipo de Siniestro", ["granizo", "viento", "inundacion", "sequia", "helada"])
            with col2:
                cultivo_ev = st.selectbox("Confirmar Cultivo", cult_list, index=default_cult_idx)
                nombre_caso = st.text_input("Nombre de Referencia", f"Siniestro_{target_row.iloc[0][id_col]}")

            # ── TASK 2: CONFIGURACIÓN DE DELINEACIÓN Y FALLBACK ──
            gdf_ev = target_row.copy()
            geom_type = gdf_ev.geometry.iloc[0].geom_type
            metodo_delineacion = "🧠 Automático (SAM con Fallback)"
            manual_polygon_gdf = None

            if geom_type == 'Point':
                st.markdown("##### 📐 Configuración del Contorno del Lote")
                metodo_delineacion = st.radio(
                    "Selecciona cómo definir los límites del lote para esta demo:",
                    ["🧠 Automático (SAM con Fallback)", "📐 Envolvente desde Punto GPS (~80 ha)", "✍️ Dibujar Polígono en Mapa"],
                    index=0,
                    key="metodo_del_radio"
                )
                
                if metodo_delineacion == "✍️ Dibujar Polígono en Mapa":
                    st.info("✍️ **Instrucciones:** Usá la herramienta de dibujo (polígono o rectángulo) a la izquierda del mapa para trazar el lote alrededor del punto rojo. Al finalizar, hacé clic en 'Ejecutar Peritaje' más abajo.")
                    pt = gdf_ev.geometry.iloc[0]
                    m_draw = folium.Map(
                        location=[pt.y, pt.x], zoom_start=15, 
                        tiles='https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}',
                        attr='Google Satellite Hybrid'
                    )
                    folium.Marker([pt.y, pt.x], popup="Punto Siniestro", icon=folium.Icon(color='red', icon='info-sign')).add_to(m_draw)
                    
                    from folium.plugins import Draw
                    draw_plugin = Draw(
                        export=False,
                        position='topleft',
                        draw_options={
                            'polyline': False,
                            'polygon': True,
                            'circle': False,
                            'rectangle': True,
                            'marker': False,
                            'circlemarker': False
                        },
                        edit_options={'remove': True}
                    )
                    draw_plugin.add_to(m_draw)
                    
                    output_draw = st_folium(m_draw, height=350, width="100%", key="mapa_delineacion_manual")
                    
                    if output_draw and output_draw.get("all_drawings"):
                        drawings = output_draw.get("all_drawings")
                        if drawings:
                            last_geom = drawings[-1]["geometry"]
                            try:
                                from shapely.geometry import shape
                                from src.pipeline.sam_fallback import validate_user_polygon
                                poly_shape = shape(last_geom)
                                validation = validate_user_polygon(poly_shape)
                                if validation.valid:
                                    manual_polygon_gdf = gpd.GeoDataFrame(
                                        [{'id': nombre_caso}],
                                        geometry=[validation.geometry],
                                        crs="EPSG:4326",
                                    )
                                    if validation.reasons:
                                        st.info(
                                            "✓ Geometría capturada y reparada: "
                                            + "; ".join(validation.reasons)
                                        )
                                    else:
                                        st.success("✓ Geometría dibujada capturada correctamente.")
                                else:
                                    st.error(
                                        "❌ Polígono inválido — no se acepta: "
                                        + "; ".join(validation.reasons)
                                        + ". Volvé a dibujarlo sin auto-intersecciones y con área "
                                        "entre 0.5 ha y 50 000 ha."
                                    )
                            except Exception as e:
                                st.error(f"Error procesando la geometría dibujada: {e}")

            btn_run = st.button("🚀 Ejecutar Peritaje Inteligente", type="primary")
            
            if st.session_state.siniestro_res is not None:
                if st.button("🧹 Limpiar Resultados"):
                    st.session_state.siniestro_res = None
                    st.rerun()

            if btn_run:
                try:
                    with st.spinner("⏳ Iniciando Pipeline: Delineación + Análisis Copernicus CDSE..."):
                        # Inicializar EODAG
                        from src.pipeline.eodag_extractor import init_eodag
                        init_eodag()

                        import time
                        pt = target_row.geometry.iloc[0]

                        if geom_type == 'Point':
                            if metodo_delineacion == "✍️ Dibujar Polígono en Mapa":
                                if manual_polygon_gdf is not None:
                                    gdf_ev = manual_polygon_gdf
                                    try:
                                        gdf_ev_proj = gdf_ev.to_crs(gdf_ev.estimate_utm_crs())
                                        area_ha = gdf_ev_proj.geometry.area.iloc[0] / 10000.0
                                        gdf_ev['area_ha'] = area_ha
                                    except Exception:
                                        gdf_ev['area_ha'] = 25.0
                                    st.success(f"✅ Usando lote dibujado manualmente: {gdf_ev.iloc[0].get('area_ha', 25.0):.1f} ha")
                                else:
                                    st.error("❌ No se ha capturado ningún polígono dibujado. Dibujá el lote en el mapa antes de presionar Ejecutar.")
                                    st.stop()
                            elif metodo_delineacion == "📐 Envolvente desde Punto GPS (~80 ha)":
                                from src.pipeline.sam_fallback import approximate_area_ha
                                st.info("📐 Generando envolvente geométrica alrededor del punto GPS...")
                                poly_geom = pt.buffer(0.005).envelope
                                buffer_area = approximate_area_ha(poly_geom)
                                gdf_ev = gpd.GeoDataFrame(
                                    [{'id': nombre_caso, 'area_ha': round(buffer_area, 1)}],
                                    geometry=[poly_geom], crs="EPSG:4326",
                                )
                                st.success(f"✅ Envolvente generada: ~{buffer_area:.1f} ha (rectángulo ~1110 m × ~909 m)")
                            else:
                                st.info("🧠 Iniciando delineación automática con SAM...")
                                sam_fallback_reason = None
                                try:
                                    temp_csv = os.path.join(tempfile.gettempdir(), f"sam_st_{int(time.time())}.csv")
                                    pd.DataFrame([{'id': nombre_caso, 'lat': pt.y, 'lon': pt.x, 'fecha': fecha_ev.strftime('%Y-%m-%d')}]).to_csv(temp_csv, index=False)

                                    from Poligonizacion.poligonizador_final import AgroIAPipeline
                                    poly_pipe = AgroIAPipeline()
                                    poly_pipe.cargar_datos(temp_csv)
                                    out_prefix = os.path.join(tempfile.gettempdir(), f"sam_out_{int(time.time())}")
                                    poly_pipe.ejecutar(output_prefix=out_prefix)

                                    geojson_path = f"{out_prefix}.geojson"
                                    if not os.path.exists(geojson_path):
                                        raise ValueError("SAM no produjo archivo de salida.")
                                    gdf_ev_candidate = gpd.read_file(geojson_path)
                                    if gdf_ev_candidate.empty:
                                        raise ValueError("SAM devolvió GeoDataFrame vacío.")

                                    # Validar geometría devuelta por SAM (is_valid + área plausible)
                                    from src.pipeline.sam_fallback import validate_user_polygon
                                    sam_geom = gdf_ev_candidate.geometry.iloc[0]
                                    validation = validate_user_polygon(sam_geom)
                                    if not validation.valid:
                                        sam_fallback_reason = (
                                            "SAM produjo geometría no utilizable: "
                                            + "; ".join(validation.reasons)
                                        )
                                    else:
                                        gdf_ev = gdf_ev_candidate
                                        st.success(
                                            f"✅ Lote delineado con SAM: "
                                            f"{gdf_ev.iloc[0].get('area_ha', 0):.1f} ha"
                                        )
                                    if os.path.exists(temp_csv):
                                        os.remove(temp_csv)
                                except Exception as e:
                                    sam_fallback_reason = f"{type(e).__name__}: {e}"

                                if sam_fallback_reason is not None:
                                    from src.pipeline.sam_fallback import approximate_area_ha
                                    st.warning(
                                        f"⚠️ Delineación SAM no aceptada ({sam_fallback_reason}). "
                                        "Aplicando fallback de envolvente geométrica."
                                    )
                                    poly_geom = pt.buffer(0.005).envelope
                                    fb_area = approximate_area_ha(poly_geom)
                                    gdf_ev = gpd.GeoDataFrame(
                                        [{'id': nombre_caso, 'area_ha': round(fb_area, 1)}],
                                        geometry=[poly_geom], crs="EPSG:4326",
                                    )
                                    st.success(f"✅ Fallback de envolvente geométrica aplicado: ~{fb_area:.1f} ha")

                        # ── Análisis Satelital ──
                        geom_ee, coords = cargar_poligono_desde_gdf(gdf_ev)
                        res = run_eventualidades(
                            geom_shapely=geom_ee,
                            fecha_evento=fecha_ev.strftime('%Y-%m-%d'),
                            cultivo=cultivo_ev,
                            tipo_evento=tipo_ev,
                            caso_nombre=nombre_caso
                        )
                        if res is None:
                            st.error("⚠️ No se pudieron obtener datos satelitales suficientes para el análisis. Esto puede deberse a que la fecha del siniestro es muy reciente (y el mes posterior aún no cuenta con imágenes Sentinel-2 disponibles) o a problemas de conexión con CDSE.")
                        else:
                            from src.pipeline.eventualidades import generar_puntos_muestreo
                            res['df_muestreo'] = generar_puntos_muestreo(res)
                            st.session_state.siniestro_res = res
                            st.rerun()
                except Exception as e:
                    st.error(f"❌ Error: {e}")

    # ── Mostrar Resultados (si existen en el estado) ─────────────────────────
    if st.session_state.siniestro_res:
        res = st.session_state.siniestro_res

        st.divider()
        st.success(f"### Resultado: {res['clasificacion']}")
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Daño Ponderado", f"{res['dano_pond_val']}%")
        c2.metric("Área Afectada", f"{res['area_afectada']:.1f} ha")
        c3.metric("Confianza Datos", res['confianza'])
        c4.metric("Etapa Detectada", res['etapa_desc'])

        # ── Visualizaciones ─────────────────────────────────────────
        st.subheader("📊 Visualización de Impacto")
        v_col1, v_col2 = st.columns([1, 1])
        
        from src.pipeline.eventualidades import plot_comparativa_ndvi, plot_donut_dano
        
        with v_col1:
            donut_img = plot_donut_dano(res)
            if donut_img:
                st.image(donut_img, caption="Distribución de Daño en el Lote", use_container_width=True)
        
        with v_col2:
            st.markdown(f"**Análisis de Severidad:**")
            st.write(f"- 🟡 **Leve:** {res['area_leve']:.1f} ha")
            st.write(f"- 🟠 **Moderado:** {res['area_moderada']:.1f} ha")
            st.write(f"- 🔴 **Severo:** {res['area_severa']:.1f} ha")
            st.info(f"El **{res['area_afectada']/max(res['area_total'],0.1)*100:.1f}%** del lote presenta algún grado de afectación.")

        st.subheader("🛰️ Comparativa Satelital y Gráficos de Severidad")
        panel_img = plot_comparativa_ndvi(res)
        if panel_img:
            st.image(panel_img, caption="Comparativa de Vigor (NDVI) y Niveles de Daño", use_container_width=True)

        st.subheader("🖼️ Prueba Visual: Mapa de Calor NDVI (Antes vs Después)")
        from src.pipeline.eventualidades import plot_mapa_calor_ndvi_antes_despues
        with st.spinner("⏳ Descargando rasters espaciales y generando mapa de calor antes/después..."):
            heat_map_img = plot_mapa_calor_ndvi_antes_despues(res)
            if heat_map_img:
                st.image(heat_map_img, caption="Mapa de calor NDVI: Vigor vegetativo real antes y después del siniestro", use_container_width=True)
            else:
                st.warning("⚠️ No se pudieron obtener los rasters espaciales detallados para esta zona/fecha desde CDSE. Mostrando visor interactivo de vectores.")

        # Asegurar que df_muestreo esté en res (por si es de una sesión antigua o carga previa)
        if 'df_muestreo' not in res or res['df_muestreo'] is None or res['df_muestreo'].empty:
            from src.pipeline.eventualidades import generar_puntos_muestreo
            res['df_muestreo'] = generar_puntos_muestreo(res)

        m_ev = generar_mapa_folium(res, df_muestreo=res['df_muestreo'])
        
        # Guardar copia física en el disco (directorio outputs)
        out_html = OUTPUTS_DIR / f"Mapa_{res['caso_nombre']}.html"
        try:
            m_ev.save(str(out_html))
        except Exception as e:
            pass

        # Botón para descargar el HTML interactivo directamente desde el navegador
        try:
            import io
            html_data = io.BytesIO()
            m_ev.save(html_data, close_file=False)
            html_bytes = html_data.getvalue()
            st.download_button(
                label="📥 Descargar Visor del Mapa (HTML)",
                data=html_bytes,
                file_name=f"visor_agroia_{res['caso_nombre']}.html",
                mime="text/html",
                key="download_mapa_html"
            )
            st.write(f"💾 *Copia guardada en el disco:* `{out_html}`")
        except Exception as e:
            st.write(f"💾 *Copia guardada en:* `{out_html}`")

        st_folium(m_ev, height=600, width="100%", key="mapa_siniestro_persistente")

        # ── Detalle Técnico ─────────────────────────────────────────
        with st.expander("📊 Ver detalle técnico de índices"):
            col_t1, col_t2 = st.columns(2)
            col_t1.write(f"**NDVI Pre-evento:** {res['ndvi_pre_val']}")
            col_t1.write(f"**NDVI Post-evento:** {res['ndvi_post_val']}")
            col_t2.write(f"**Baseline (3 años):** {res['baseline_val']}")
            col_t2.write(f"**Delta Ajustado:** {res['delta_adj_val']}")
            
            st.info(f"Metodología: ΔNDVI Relativo ({res['delta_rel_val']}%) × Peso Fenológico ({res['peso_fenologico']}) × Factor Conservador (0.92)")

# ── Modo: Prevención Super Niño ──────────────────────────────────────────
elif modo == "⚠️ Prevención Super Niño":
    st.subheader("⚠️ Prevención y Gestión ante el Fenómeno 'Super Niño' (LAC)")
    st.markdown(
        """
        El módulo de **Gestión de Crisis Super Niño** evalúa la vulnerabilidad de las parcelas
        para toda Latinoamérica y el Caribe (LAC) según su geolocalización, altitud, vigor satelital (NDVI)
        y cultivo, basándose en la alerta oficial de la OMM (90% de probabilidad de evento extremo en el segundo semestre).
        """
    )
    
    # ── Panel de Alerta Global ──
    from src.pipeline.el_nino import obtener_alertas_el_nino
    alerta_c = obtener_alertas_el_nino()
    
    st.error(
        f"🚨 **{alerta_c['estado']}** — Probabilidad: **{alerta_c['probabilidad']}%**\n\n"
        f"**Período Crítico:** {alerta_c['periodo_critico']}\n\n"
        f"**Anomalía Térmica Oceánica:** Pacífico Ecuatorial Central (Niño 3.4): **+{alerta_c['anomalia_tsm_nino34']:.1f}°C** | Costa Sudamericana (Niño 1+2): **+{alerta_c['anomalia_tsm_nino12']:.1f}°C**\n\n"
        f"**Detalles:** {alerta_c['comunicado']}\n\n"
        f"Fuentes oficiales: *{alerta_c['fuente']}*"
    )
    
    st.divider()
    
    # Obtener datos del lote seleccionado
    datos = get_datos_lote(lote_a)
    if not datos:
        st.warning(f"Sin datos para **{lote_a}**.")
        st.stop()
        
    st.markdown(f"### 📋 Reporte de Vulnerabilidad: **{lote_a}**")
    st.markdown(f"**Cultivo:** {datos['cultivo'].capitalize()} | **Superficie:** {datos['superficie_ha']} ha | **Ubicación:** {datos['centroide'][0]:.4f}°, {datos['centroide'][1]:.4f}°")
    
    # Extraer el score calculado de El Niño
    el_nino = datos.get("meta", {}).get("el_nino", {})
    
    if not el_nino:
        try:
            from src.pipeline.el_nino import calcular_vulnerabilidad_el_nino, obtener_recomendaciones_el_nino
            import geopandas as gpd
            from shapely.geometry import Point
            
            c = datos.get("centroide", [])
            # Fallback a coordenadas por defecto si no tiene centroide
            if not c or len(c) < 2 or c[0] == 0:
                c = [-36.0, -61.0] # Argentina Pampeana como fallback
            
            lat, lon = c[0], c[1]
            geom = Point(lon, lat)
            lote_gdf = gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326")
            
            # Calcular al vuelo
            el_nino = calcular_vulnerabilidad_el_nino(
                lote_gdf, 
                datos["cultivo"], 
                datos.get("ndvi", 0.5), 
                25.0, # temp_actual default
                10.0, # precip_actual default
                elevation=None
            )
            recs = obtener_recomendaciones_el_nino(datos["cultivo"], el_nino["region_id"], el_nino["score"])
            el_nino["recomendaciones"] = recs
            st.caption("💡 *Nota: Diagnóstico calculado en tiempo real (datos históricos heredados).*")
        except Exception as e:
            st.warning(f"No se pudo calcular la vulnerabilidad: {e}")
            st.stop()
        
    score_nino = el_nino.get("score", 0.0)
    riesgo_nino = el_nino.get("riesgo", "N/D")
    region_nombre = el_nino.get("region_nombre", "N/D")
    region_impacto = el_nino.get("region_impacto", "N/D")
    factores = el_nino.get("factores", [])
    recs = el_nino.get("recomendaciones", {})
    
    # Visualizar barra de color
    c1, c2 = st.columns([1, 2])
    with c1:
        st.metric(label="📊 Score de Vulnerabilidad", value=f"{score_nino}/10", delta=f"Riesgo {riesgo_nino}", delta_color="inverse" if riesgo_nino in ("ALTO", "CRÍTICO") else "normal")
    with c2:
        color_bar = "red" if riesgo_nino in ("ALTO", "CRÍTICO") else "orange" if riesgo_nino == "MEDIO" else "green"
        st.progress(int(score_nino * 10), text=f"Nivel de Exposición: {riesgo_nino}")
        
    # Tarjeta de detalles regionales
    st.info(
        f"🗺️ **Región LAC Determinada:** {region_nombre}\n\n"
        f"💥 **Amenazas Principales:** {region_impacto}"
    )
    
    # Factores analizados
    with st.expander("🔍 Factores de Riesgo Analizados en el Lote", expanded=True):
        for f in factores:
            st.markdown(f"- {f}")
            
    # Recomendaciones accionables
    st.markdown("### 🛠️ Plan de Mitigación y Prevención")
    t_inm, t_cp, t_mp = st.tabs(["⚡ Tareas Inmediatas", "📅 Corto Plazo (Campaña)", "🌲 Mediano Plazo"])
    
    with t_inm:
        st.markdown("**Acciones operativas urgentes:**")
        for idx, r in enumerate(recs.get("inmediatas", [])):
            st.checkbox(r, key=f"rec_inm_{lote_a}_{idx}", value=False)
            
    with t_cp:
        st.markdown("**Tareas clave a implementar en la campaña en curso:**")
        for idx, r in enumerate(recs.get("corto_plazo", [])):
            st.checkbox(r, key=f"rec_cp_{lote_a}_{idx}", value=False)
            
    with t_mp:
        st.markdown("**Estrategias de mediano y largo plazo para resiliencia climática:**")
        for idx, r in enumerate(recs.get("mediano_plazo", [])):
            st.checkbox(r, key=f"rec_mp_{lote_a}_{idx}", value=False)
            
    # Chat RAG especializado para El Niño
    st.divider()
    st.subheader(f"💬 Consultas de Contingencia sobre {lote_a}")
    st.markdown("Pregúntale a la IA sobre planes de contingencia, laboreo de suelos, variedades o fertilización específica para mitigar el impacto en esta parcela.")
    
    if "nino_msgs" not in st.session_state:
        st.session_state.nino_msgs = []
        
    for m in st.session_state.nino_msgs:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            
    if pregunta := st.chat_input(f"Preguntar a AgroIA sobre gestión de El Niño en {lote_a}..."):
        st.session_state.nino_msgs.append({"role": "user", "content": pregunta})
        with st.chat_message("user"):
            st.markdown(pregunta)
        with st.chat_message("assistant"):
            with st.spinner("Generando recomendaciones adaptativas..."):
                prompt_enriquecido = f"Con respecto a la alerta de El Niño / Super Niño y los riesgos de este lote, {pregunta}"
                resp = consultar_agente(lote_a, prompt_enriquecido)
            st.markdown(resp)
        st.session_state.nino_msgs.append({"role": "assistant", "content": resp})
