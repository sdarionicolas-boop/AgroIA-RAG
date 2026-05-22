# src/streamlit_app.py
import sys
from pathlib import Path

# Inyectar src al path para poder importar utils y rag
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import streamlit as st
import psycopg2
import ollama
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
from src.pipeline import run_full_analysis, run_batch_from_geojson
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

        prompt_comparacion = (
            f"=== LOTE A: {lote_a} ===\n{ctx_a}\n\n"
            f"=== LOTE B: {lote_b} ===\n{ctx_b}"
        )

        resp = ollama.chat(
            model=settings.generation_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        BASE_PROMPT + "\n"
                        "Estás realizando una CONSULTORÍA AGRONÓMICA COMPARATIVA. Sé profundo.\n"
                        "Estructura tu respuesta estrictamente así:\n"
                        "1. TABLA COMPARATIVA: Cruce de métricas (Score, NDVI, Estrés, Componentes).\n"
                        "2. ANÁLISIS DE RENDIMIENTO: Explica por qué un lote tiene mejor vigor o estabilidad que el otro basándote en los datos.\n"
                        "3. FACTORES LIMITANTES: Identifica qué está frenando el potencial de cada lote (Clima, Limpieza, etc.).\n"
                        "4. CONCLUSIÓN Y RECOMENDACIÓN: Cuál tiene mejor perspectiva y qué acción agronómica sugerirías."
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
            options={"temperature": 0.3, "num_predict": 1536},
        )
        return resp["message"]["content"]
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
                    ["🗺️ Mapa de Lotes", "🔍 Inspeccionar un Lote", "⚖️ Comparar Lote A vs B", "🏆 Ranking Global", "🛰️ Siniestros (Eventualidades)"], 
                    index=0)
    st.divider()

    if st.button("🗑️ Limpiar Base de Datos", use_container_width=True):
        reset_database()
        st.success("Base de datos reiniciada.")
        st.rerun()

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
                            poly_pipe = AgroIAPipeline()
                            poly_pipe.cargar_datos(tmp_path)
                            # Inyectar el cultivo seleccionado a los datos cargados si no lo tienen
                            if poly_pipe.df is not None:
                                poly_pipe.df['cultivo'] = cultivo_default
                            out_prefix = os.path.join(tempfile.gettempdir(), f"agroia_st_{int(time.time())}")
                            poly_pipe.ejecutar(output_prefix=out_prefix)
                            geojson_path = f"{out_prefix}.geojson"
                        else:
                            st.warning("⚠️ Modelo SAM no encontrado. Usando delineación geométrica (buffer)...")
                            for idx, row in df_input.iterrows():
                                lid = str(row[id_col]) if id_col else f"LOTE_{idx+1:03d}"
                                lat, lon = float(row[lat_col]), float(row[lon_col])
                                st.write(f"  → Procesando {lid}...")
                                # Buffer de 0.005 grados (~500m) para simular un lote de ~25ha
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
                        # Flujo directo: GeoJSON cargado → GEE (analisis) → RAG
                        geojson_path = tmp_path
                        st.write(f"📂 Archivo {suffix} cargado, saltando delineacion SAM...")

                    st.write("🛰️ Ejecutando analisis satelital y scoring...")
                    res_batch = run_batch_from_geojson(geojson_path, cultivo_default=cultivo_default, push_to_rag=True)

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
                    st.info("💡 Posibles causas: GEE sin autenticar, sin imágenes Sentinel-2 disponibles para las fechas, o conexión a internet inestable.")

            except Exception as e:
                st.error(f"❌ Error en el proceso: {e}")
            finally:
                if os.path.exists(tmp_path): os.remove(tmp_path)

    st.divider()

    if not lotes_info and modo != "🗺️ Mapa de Lotes" and modo != "🛰️ Siniestros (Eventualidades)":
        st.warning("📭 No hay lotes cargados en la base de datos.")
        st.info("Subí un archivo CSV para empezar.")
        st.stop()
    if modo == "🔍 Inspeccionar un Lote":
        lote_a = st.selectbox("Seleccioná el lote", lote_ids)
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
    st.caption("AgroIA v2.4.0 | GEE Sentinel-2 + NASA POWER")

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
                r = _req.delete(
                    f"http://localhost:{settings.api_port if hasattr(settings, 'api_port') else 8000}/lotes/{lote_borrar}",
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
                r = _req.delete(
                    f"http://localhost:{settings.api_port if hasattr(settings, 'api_port') else 8000}/lotes",
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
        meta = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta or '{}')
        c = meta.get("centroide", [])
        if c:
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
    c4.metric("📐 CV espacial", f"{datos['cv']:.3f}" if datos['cv'] else "N/D")
    sd = datos["score_desglose"]
    if sd:
        cols = st.columns(4)
        cols[0].progress(int(sd.get("vigor", 0) / 40 * 100), text=f"Vigor: {sd.get('vigor', 0):.1f}/40")
        cols[1].progress(int(sd.get("estabilidad", 0) / 30 * 100), text=f"Estabilidad: {sd.get('estabilidad', 0):.1f}/30")
        cols[2].progress(int(sd.get("limpieza", 0) / 20 * 100), text=f"Limpieza IA: {sd.get('limpieza', 0):.1f}/20")
        cols[3].progress(int(sd.get("clima", 0) / 10 * 100), text=f"Clima: {sd.get('clima', 0):.1f}/10")
    if datos["zona_activa"]:
        st.info(f"🗺️ Zonificación A/B/C activa — **{datos['puntos_zona_c']} puntos críticos** detectados en Zona C")
    else:
        st.success("✅ Lote homogéneo — sin zonas diferenciadas")
    
    # ── Visualización de Mapa Detallado ──────────────────────────────────────
    st.divider()
    st.subheader("🗺️ Mapa Detallado de Lote")
    # Los mapas se generan en src/outputs porque la app corre con CWD=src
    outputs_dir = Path(__file__).resolve().parent / "outputs"
    mapa_path = outputs_dir / f"Mapa_{lote_a}.html"
    
    if mapa_path.exists():
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
    Este módulo evalúa el impacto de eventos climáticos (granizo, viento, etc.) comparando 
    la salud del cultivo **antes y después** de una fecha específica, ponderada por su etapa fenológica.
    """)

    with st.expander("📝 Configuración del Análisis", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            fecha_ev = st.date_input("Fecha del Evento", value=datetime.now() - timedelta(days=30))
            tipo_ev = st.selectbox("Tipo de Evento", ["granizo", "viento", "inundacion", "sequia", "helada"])
        with col2:
            cultivo_ev = st.selectbox("Cultivo afectado", ["maiz", "soja", "trigo", "girasol", "cebada"])
            nombre_caso = st.text_input("Nombre del Caso / Referencia", "Siniestro_001")

        up_file = st.file_uploader("Subir Polígono del Lote (GeoJSON, KML, SHP)", type=["geojson", "kml", "kmz", "shp", "zip"])

    if up_file and st.button("🚀 Ejecutar Peritaje Satelital"):
        # Guardar archivo temporal
        suffix = Path(up_file.name).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(up_file.getvalue())
            tmp_path = tmp.name

        try:
            with st.spinner("⏳ Analizando imágenes Sentinel-2 y Baseline histórico..."):
                # Inicializar GEE
                from src.pipeline.gee_extractor import init_gee
                init_gee(project_id=settings.gee_project_id)

                # Cargar geometría
                try:
                    if suffix.lower() in ['.kml', '.kmz', '.zip']:
                        read_path = tmp_path
                        if suffix.lower() in ['.zip', '.kmz']: read_path = f"zip://{tmp_path}"
                        gdf_ev = gpd.read_file(read_path)
                    else:
                        gdf_ev = gpd.read_file(tmp_path)
                except Exception as e:
                    if suffix.lower() in ['.kml', '.kmz']:
                        st.info("⚠ Driver KML no detectado. Usando parser manual...")
                        from src.utils.kml_fallback import parse_kml_manual
                        manual_data = parse_kml_manual(tmp_path)
                        if manual_data:
                            tmp_json = f"{tmp_path}_tmp.json"
                            with open(tmp_json, 'w') as f: json.dump(manual_data, f)
                            gdf_ev = gpd.read_file(tmp_json)
                            os.remove(tmp_json)
                        else:
                            raise ValueError("No se pudo parsear el archivo KML de forma manual.")
                    else:
                        raise e

                geom_ee, coords = cargar_poligono_desde_gdf(gdf_ev)

                # Ejecutar Pipeline
                res = run_eventualidades(
                    geom_ee=geom_ee,
                    fecha_evento=fecha_ev.strftime('%Y-%m-%d'),
                    cultivo=cultivo_ev,
                    tipo_evento=tipo_ev,
                    caso_nombre=nombre_caso
                )

                # ── Resultados ───────────────────────────────────────────────
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

                st.subheader("🛰️ Comparativa Satelital y Mapa de Calor")
                panel_img = plot_comparativa_ndvi(res)
                if panel_img:
                    st.image(panel_img, caption="NDVI Pre-Evento vs Post-Evento vs Severidad", use_container_width=True)

                m_ev = generar_mapa_folium(res)
                st_folium(m_ev, height=600, width="100%", key="mapa_siniestro")

                # ── Detalle Técnico ─────────────────────────────────────────
                with st.expander("📊 Ver detalle técnico de índices"):
                    col_t1, col_t2 = st.columns(2)
                    col_t1.write(f"**NDVI Pre-evento:** {res['ndvi_pre_val']}")
                    col_t1.write(f"**NDVI Post-evento:** {res['ndvi_post_val']}")
                    col_t2.write(f"**Baseline (3 años):** {res['baseline_val']}")
                    col_t2.write(f"**Delta Ajustado:** {res['delta_adj_val']}")
                    
                    st.info(f"Metodología: ΔNDVI Relativo ({res['delta_rel_val']}%) × Peso Fenológico ({res['peso_fenologico']}) × Factor Conservador (0.92)")

        except Exception as e:
            st.error(f"❌ Error en el análisis: {e}")
        finally:
            if os.path.exists(tmp_path): os.remove(tmp_path)
