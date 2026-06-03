# src/pipeline/reporter.py
import os
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
from io import BytesIO
from datetime import datetime
import folium
from folium import plugins
import tempfile
from pypdf import PdfWriter, PdfReader
from sklearn.ensemble import IsolationForest

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image,
    Table, TableStyle, HRFlowable, KeepTogether, PageBreak
)
from reportlab.pdfgen import canvas as rl_canvas

# Importar CONFIG para las tablas del final
from .agro_math import CONFIG, calcular_score

# ============================================================================
# PALETA Y CONSTANTES
# ============================================================================
C_PRIMARY    = HexColor("#1B4332")
C_SECONDARY  = HexColor("#40916C")
C_ACCENT_BG  = HexColor("#D8F3DC")
C_ALERT      = HexColor("#D62828")
C_WARN       = HexColor("#F4A261")
C_GOLD       = HexColor("#E9C46A")
C_NEUTRAL    = HexColor("#F8F9FA")
C_TEXT       = HexColor("#212529")
C_MUTED      = HexColor("#6C757D")
C_BORDER     = HexColor("#DEE2E6")

ZONA_COLORS  = {"A": "#40916C", "B": "#F4A261", "C": "#D62828"}
ZONA_LABELS  = {"A": "Zona A — Alto potencial", "B": "Zona B — Variable", "C": "Zona C — Limitante"}

VERSION      = "2.5.0"
PAGE_W, PAGE_H = A4

# ============================================================================
# ESTILOS
# ============================================================================
def build_styles():
    return {
        "cover_title": ParagraphStyle("CoverTitle", fontSize=32, fontName="Helvetica-Bold", textColor=white, leading=36, spaceAfter=6),
        "cover_sub":   ParagraphStyle("CoverSub",   fontSize=13, fontName="Helvetica",      textColor=HexColor("#D8F3DC"), leading=18, spaceAfter=4),
        "cover_meta":  ParagraphStyle("CoverMeta",  fontSize=9,  fontName="Helvetica",      textColor=HexColor("#95D5B2"), leading=13),
        "h2": ParagraphStyle("H2", fontSize=11, fontName="Helvetica-Bold", textColor=white, spaceAfter=10, spaceBefore=4, backColor=C_PRIMARY, leftIndent=-16, rightIndent=-16, borderPad=7),
        "h3": ParagraphStyle("H3", fontSize=10, fontName="Helvetica-Bold", textColor=C_PRIMARY, spaceAfter=4, spaceBefore=10),
        "body": ParagraphStyle("Body", fontSize=9, fontName="Helvetica", textColor=C_TEXT, leading=14, spaceAfter=5, alignment=TA_JUSTIFY),
        "body_bold": ParagraphStyle("BodyBold", fontSize=9, fontName="Helvetica-Bold", textColor=C_TEXT, leading=14, spaceAfter=4),
        "alert_red":  ParagraphStyle("AlertRed",  fontSize=9, fontName="Helvetica-Bold", textColor=C_ALERT, spaceAfter=4, leftIndent=8),
        "alert_warn": ParagraphStyle("AlertWarn", fontSize=9, fontName="Helvetica-Bold", textColor=HexColor("#8B4513"), spaceAfter=4, leftIndent=8),
        "alert_ok":   ParagraphStyle("AlertOk",   fontSize=9, fontName="Helvetica",      textColor=HexColor("#1B4332"), spaceAfter=4, leftIndent=8),
        "caption": ParagraphStyle("Caption", fontSize=7.5, fontName="Helvetica-Oblique", textColor=C_MUTED, spaceAfter=8, alignment=TA_CENTER),
        "meta":    ParagraphStyle("Meta",    fontSize=7,   fontName="Helvetica",          textColor=C_MUTED, alignment=TA_CENTER),
        "table_header": ParagraphStyle("TH", fontSize=8.5, fontName="Helvetica-Bold", textColor=white, alignment=TA_CENTER),
        "table_cell":   ParagraphStyle("TC", fontSize=8.5, fontName="Helvetica",      textColor=C_TEXT, alignment=TA_CENTER),
        "score_big":    ParagraphStyle("ScoreBig",   fontSize=48, fontName="Helvetica-Bold", textColor=white, alignment=TA_CENTER, leading=52),
        "score_label":  ParagraphStyle("ScoreLabel", fontSize=10, fontName="Helvetica",      textColor=HexColor("#D8F3DC"), alignment=TA_CENTER),
        "log": ParagraphStyle("Log", fontSize=7, fontName="Courier", textColor=C_MUTED, leading=10, spaceAfter=2),
    }

def fig_to_rl_image(fig, width_cm=16, dpi=150):
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor="white", edgecolor="none")
    buf.seek(0)
    plt.close(fig)
    w = width_cm * cm
    h = w * (fig.get_figheight() / fig.get_figwidth())
    return Image(buf, width=w, height=h)

# ============================================================================
# GRÁFICOS
# ============================================================================
def plot_score_gauge(score_dict):
    total = score_dict["total"]
    color_score = "#40916C" if total >= 70 else "#F4A261" if total >= 45 else "#D62828"
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), gridspec_kw={'width_ratios': [1, 1.4]})
    fig.patch.set_facecolor('white')
    ax = axes[0]
    ax.set_aspect('equal')
    ax.axis('off')
    theta_start = np.pi
    theta_end   = 0
    theta_score = theta_start - (total / 100) * np.pi
    theta_bg = np.linspace(theta_start, theta_end, 200)
    r_out, r_in = 1.0, 0.62
    ax.fill_between(np.concatenate([r_out * np.cos(theta_bg), r_in * np.cos(theta_bg[::-1])]),
                    np.concatenate([r_out * np.sin(theta_bg), r_in * np.sin(theta_bg[::-1])]),
                    color="#E9ECEF", zorder=1)
    theta_fill = np.linspace(theta_start, theta_score, 200)
    ax.fill_between(np.concatenate([r_out * np.cos(theta_fill), r_in * np.cos(theta_fill[::-1])]),
                    np.concatenate([r_out * np.sin(theta_fill), r_in * np.sin(theta_fill[::-1])]),
                    color=color_score, zorder=2)
    ax.text(0, 0.18, str(total), ha='center', va='center', fontsize=42, fontweight='bold', color=color_score)
    ax.text(0, -0.08, "/ 100", ha='center', va='center', fontsize=13, color='#6C757D')
    ax.text(0, -0.30, "Score AgroIA", ha='center', va='center', fontsize=10, color='#495057')
    ax.set_xlim(-1.2, 1.2)
    ax.set_ylim(-0.5, 1.2)
    ax2 = axes[1]
    ax2.set_facecolor('white')
    componentes = [("Vigor", score_dict["vigor"], 40, "#40916C"),
                   ("Estabilidad", score_dict["estabilidad"], 30, "#2D6A4F"),
                   ("Limpieza IA", score_dict["limpieza"], 20, "#74C69D"),
                   ("Clima", score_dict["clima"], 10, "#F4A261")]
    for i, (label, val, max_val, color) in enumerate(componentes):
        ax2.barh(i, max_val, color="#E9ECEF", height=0.55, zorder=1)
        ax2.barh(i, val, color=color, height=0.55, zorder=2)
        ax2.text(max_val + 0.5, i, f"{val:.1f}/{max_val}", va='center', fontsize=9, color='#495057')
        ax2.text(-0.5, i, label, va='center', ha='right', fontsize=9, color='#212529', fontweight='bold')
    ax2.set_xlim(-8, 48)
    ax2.set_ylim(-0.6, len(componentes) - 0.4)
    ax2.axis('off')
    plt.tight_layout(pad=1.0)
    return fig

def plot_ndvi_historico(ndvi_hist, ndvi_hist_bruto, anos_excluidos, cultivo, conf, año_actual):
    fig, ax = plt.subplots(figsize=(12, 4))
    fig.patch.set_facecolor('white')
    ax.set_facecolor('#FAFAFA')
    years_all    = sorted(ndvi_hist_bruto.keys())
    ndvi_valid   = [ndvi_hist.get(y) for y in years_all]
    vals_validos = [v for v in ndvi_valid if v is not None]
    media = np.mean(vals_validos) if vals_validos else None
    xs_v = [y for y, v in zip(years_all, ndvi_valid) if v is not None]
    ys_v = [v for v in ndvi_valid if v is not None]
    ax.plot(xs_v, ys_v, 'o-', color='#40916C', linewidth=2, markersize=7, zorder=3, label='NDVI válido (en Score)')
    xs_e = [y for y in anos_excluidos if ndvi_hist_bruto.get(y) is not None]
    ys_e = [ndvi_hist_bruto[y] for y in xs_e]
    if xs_e:
        ax.scatter(xs_e, ys_e, color='#ADB5BD', marker='X', s=80, zorder=4, label='Excluido (no confiable)')
    if len(vals_validos) >= 3:
        sigma = np.std(vals_validos)
        ax.axhspan(media - sigma, media + sigma, color='#D8F3DC', alpha=0.4, label='±1σ histórico')
    if media:
        ax.axhline(media, color='#2D6A4F', linestyle='--', linewidth=1.2, alpha=0.7, label=f'Media: {media:.3f}')
    if año_actual in ndvi_hist:
        ax.scatter([año_actual], [ndvi_hist[año_actual]], color='#D62828', s=120, zorder=5, label=f'Año actual ({año_actual})')
    mes_nombre = {1:"Enero", 2:"Feb", 3:"Mar", 10:"Oct", 11:"Nov", 12:"Dic"}
    mes_str = mes_nombre.get(conf['mes_critico'], f"Mes {conf['mes_critico']}")
    ax.set_title(f"NDVI en ventana crítica ({mes_str}) — {cultivo.capitalize()} | Historial {min(years_all)}–{max(years_all)}", fontsize=10, loc='left', pad=8)
    ax.set_ylabel("NDVI medio del lote", fontsize=9)
    ax.set_ylim(0, 1.0)
    ax.tick_params(labelsize=8)
    ax.legend(fontsize=8, loc='lower right', framealpha=0.8)
    ax.grid(axis='y', linestyle=':', alpha=0.4)
    plt.tight_layout()
    return fig

def plot_estres_termico(horas_calor_hist, conf, año_actual):
    years = sorted(horas_calor_hist.keys())
    horas = [horas_calor_hist[y] for y in years]
    media = np.mean([h for h in horas if h > 0]) if any(h > 0 for h in horas) else 0
    colores = ['#D62828' if y == año_actual else '#F4A261' if h > media * 1.2 else '#74C69D' for y, h in zip(years, horas)]
    fig, ax = plt.subplots(figsize=(11, 3.8))
    fig.patch.set_facecolor('white')
    ax.set_facecolor('#FAFAFA')
    bars = ax.bar(years, horas, color=colores, edgecolor='white', linewidth=0.5, width=0.65)
    for bar, h in zip(bars, horas):
        if h > 0:
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.2, f"{h:.1f}h", ha='center', va='bottom', fontsize=8, color='#495057')
    if media > 0:
        ax.axhline(media, color='#2D6A4F', linestyle='--', linewidth=1.2, label=f'Media: {media:.1f}h')
    umbral = conf['umbral_calor']
    ax.set_title(f"Horas estimadas sobre {umbral}°C en ventana crítica (sinusoidal NASA POWER)", fontsize=10, loc='left', pad=8)
    ax.set_ylabel("Horas sobre umbral", fontsize=9)
    ax.tick_params(labelsize=8)
    if media > 0:
        ax.legend(fontsize=8)
    ax.grid(axis='y', linestyle=':', alpha=0.4)
    plt.tight_layout()
    return fig

# ============================================================================
# TEXTO DINÁMICO
# ============================================================================
def generar_texto_diagnostico(res):
    score   = res["score"]["total"]
    cultivo = res["cultivo"].capitalize()
    ha      = res["hectareas"]
    ndvi    = res["ndvi_critico_actual"]
    horas   = res["horas_calor_actual"]
    cv      = res["cv"]
    año     = max(res["ndvi_historico"].keys())
    nivel   = "alto" if score >= 70 else "intermedio" if score >= 45 else "bajo"
    zona_str = (
        "La variabilidad espacial interna (CV={:.2f}) es suficiente para activar la <b>zonificación A/B/C</b> del lote.".format(cv)
        if res["es_variable"] else
        "El lote es <b>espacialmente homogéneo</b> (CV={:.3f}), toda la superficie responde de manera uniforme.".format(cv)
    )
    return (f"El lote de <b>{ha} ha</b> de <b>{cultivo}</b> obtuvo un <b>Score AgroIA de {score}/100</b> "
            f"para la campaña <b>{año}</b>, calificando como potencial <b>{nivel}</b>. "
            f"El NDVI mediano en la ventana crítica fue de <b>{ndvi:.3f}</b>. "
            f"La estimación sinusoidal sobre datos NASA POWER detectó "
            f"<b>{horas:.1f} horas por encima de {res['conf']['umbral_calor']}°C</b> "
            f"en el mes de mayor riesgo térmico. {zona_str}")

def generar_recomendaciones(res):
    styles = build_styles()
    recs   = []
    score  = res["score"]["total"]
    horas  = res["horas_calor_actual"]
    conf   = res["conf"]
    umbral = conf["umbral_calor"]
    ndvi   = res["ndvi_critico_actual"]
    if score >= 70:
        recs.append((styles["alert_ok"], "✓ <b>Zona de Alto Potencial:</b> mantener la inversión en insumos al nivel máximo. El historial de NDVI valida la respuesta consistente del suelo."))
    elif score >= 45:
        recs.append((styles["alert_warn"], "▲ <b>Potencial Intermedio:</b> evaluar ajuste de densidad de siembra y fertilización nitrogenada según disponibilidad hídrica esperada."))
    else:
        recs.append((styles["alert_red"], "✗ <b>Zona Limitante:</b> planteo defensivo recomendado. No sobre-invertir en insumos variables. Priorizar cobertura de riesgo (seguro agrícola)."))
    NDVI_ALERTA = {"maiz": 0.50, "soja": 0.45, "trigo": 0.40}
    umbral_ndvi = NDVI_ALERTA.get(res["cultivo"], 0.50)
    if ndvi < umbral_ndvi:
        ndvi_hist_vals = list(res["ndvi_historico"].values())
        ndvi_max_hist  = max(ndvi_hist_vals) if ndvi_hist_vals else ndvi
        caida_pct      = (1 - ndvi / ndvi_max_hist) * 100 if ndvi_max_hist > 0 else 0
        recs.append((styles["alert_warn"],
            f"▲ <b>NDVI bajo en ventana crítica:</b> el valor registrado ({ndvi:.3f}) está por debajo del umbral "
            f"({umbral_ndvi}) para {res['cultivo']}. Caída del {caida_pct:.0f}% respecto al mejor año histórico "
            f"({ndvi_max_hist:.3f}). <b>Se recomienda recorrida de campo.</b>"))
    if horas > 10:
        recs.append((styles["alert_red"], f"✗ <b>Alerta de Sopladura:</b> {horas:.1f} horas estimadas sobre {umbral}°C en ventana crítica. Riesgo de aborto de granos documentado por literatura INTA/Nebraska ({conf['biblio']}). Revisar campaña con seguro."))
    elif horas > 5:
        recs.append((styles["alert_warn"], f"▲ <b>Estrés Térmico Moderado:</b> {horas:.1f} horas sobre {umbral}°C. Monitorear llenado de granos."))
    else:
        recs.append((styles["alert_ok"], f"✓ <b>Sin estrés térmico significativo</b> en ventana crítica ({horas:.1f}h sobre {umbral}°C)."))
    if res["anos_excluidos"]:
        recs.append((styles["alert_warn"], f"▲ <b>Datos excluidos:</b> los años {res['anos_excluidos']} fueron descartados del Score por NDVI fuera de rango."))
    return recs

# ============================================================================
# HEADER / FOOTER
# ============================================================================
class PageDecorator:
    def __init__(self, cultivo, lote_id, score, fecha):
        self.cultivo  = cultivo.capitalize()
        self.lote_id  = lote_id
        self.score    = score
        self.fecha    = fecha
        self._is_first = True

    def __call__(self, canv, doc):
        if self._is_first:
            self._is_first = False
            return
        canv.saveState()
        w = PAGE_W
        canv.setFillColor(C_PRIMARY)
        canv.rect(1.8*cm, PAGE_H - 1.5*cm, w - 3.6*cm, 0.4*cm, fill=1, stroke=0)
        canv.setFont("Helvetica-Bold", 8)
        canv.setFillColor(white)
        canv.drawString(2.2*cm, PAGE_H - 1.28*cm, f"AgroIA Report — {self.cultivo} | {self.lote_id}")
        canv.setFont("Helvetica", 8)
        canv.drawRightString(w - 2.2*cm, PAGE_H - 1.28*cm, f"Score: {self.score}/100")
        canv.setFillColor(C_MUTED)
        canv.setFont("Helvetica", 7)
        canv.drawString(1.8*cm, 1.1*cm, f"Generado: {self.fecha} | v{VERSION} | NASA POWER + Copernicus CDSE Sentinel-2")
        canv.drawRightString(w - 1.8*cm, 1.1*cm, f"Pág. {doc.page}")
        canv.setStrokeColor(C_BORDER)
        canv.setLineWidth(0.3)
        canv.line(1.8*cm, 1.5*cm, w - 1.8*cm, 1.5*cm)
        canv.restoreState()

# ============================================================================
# PORTADA
# ============================================================================
def build_cover_page(canv, res, fecha_str, lote_id):
    w, h = PAGE_W, PAGE_H
    score = res["score"]["total"]
    color_score = HexColor("#40916C") if score >= 70 else HexColor("#F4A261") if score >= 45 else HexColor("#D62828")
    canv.setFillColor(C_PRIMARY)
    canv.rect(0, 0, w, h, fill=1, stroke=0)
    canv.setFillColor(C_SECONDARY)
    canv.rect(0, 0, w, 2.8*cm, fill=1, stroke=0)
    cx, cy, r = w - 4.5*cm, h - 5.5*cm, 2.6*cm
    canv.setFillColor(color_score)
    canv.circle(cx, cy, r, fill=1, stroke=0)
    canv.setFont("Helvetica-Bold", 36)
    canv.setFillColor(white)
    canv.drawCentredString(cx, cy + 0.3*cm, str(score))
    canv.setFont("Helvetica", 11)
    canv.drawCentredString(cx, cy - 0.7*cm, "/ 100")
    canv.setFont("Helvetica", 9)
    canv.setFillColor(HexColor("#D8F3DC"))
    canv.drawCentredString(cx, cy - 1.4*cm, "Score AgroIA")
    canv.setFont("Helvetica-Bold", 30)
    canv.setFillColor(white)
    canv.drawString(2*cm, h - 3.5*cm, "AgroIA Report")
    canv.setFont("Helvetica", 16)
    canv.setFillColor(HexColor("#95D5B2"))
    canv.drawString(2*cm, h - 4.3*cm, f"Gestión de Riesgo Agronómico — {res['cultivo'].capitalize()}")
    canv.setFont("Helvetica-Bold", 11)
    canv.setFillColor(HexColor("#D8F3DC"))
    canv.drawString(2*cm, h - 5.8*cm, "Datos del lote")
    canv.setLineWidth(0.5)
    canv.setStrokeColor(C_SECONDARY)
    canv.line(2*cm, h - 5.95*cm, 10*cm, h - 5.95*cm)
    detalles = [
        ("ID del lote",       lote_id),
        ("Superficie",        f"{res['hectareas']} ha"),
        ("Cultivo",           res['cultivo'].capitalize()),
        ("Coordenadas",       f"{res['centroide'][0]:.4f}°, {res['centroide'][1]:.4f}°"),
        ("Proyección UTM",    f"EPSG:{res.get('epsg_utm', 'N/D')}"),
        ("Período analizado", f"{min(res['ndvi_historico'].keys())}–{max(res['ndvi_historico'].keys())}"),
    ]
    y = h - 6.5*cm
    for label, valor in detalles:
        canv.setFont("Helvetica", 9)
        canv.setFillColor(HexColor("#95D5B2"))
        canv.drawString(2*cm, y, f"{label}:")
        canv.setFont("Helvetica-Bold", 9)
        canv.setFillColor(white)
        canv.drawString(6.5*cm, y, valor)
        y -= 0.6*cm
    canv.setFont("Helvetica-Bold", 10)
    canv.setFillColor(HexColor("#D8F3DC"))
    canv.drawString(2*cm, h - 10.4*cm, "Composición del Score")
    canv.line(2*cm, h - 10.6*cm, 10*cm, h - 10.6*cm)
    comp = [("40% Vigor", f"{res['score']['vigor']:.1f} pts"),
            ("30% Estabilidad", f"{res['score']['estabilidad']:.1f} pts"),
            ("20% Limpieza IA", f"{res['score']['limpieza']:.1f} pts"),
            ("10% Clima", f"{res['score']['clima']:.1f} pts")]
    y = h - 11.2*cm
    for label, valor in comp:
        canv.setFont("Helvetica", 8.5)
        canv.setFillColor(HexColor("#95D5B2"))
        canv.drawString(2*cm, y, label)
        canv.setFont("Helvetica-Bold", 8.5)
        canv.setFillColor(white)
        canv.drawRightString(9.5*cm, y, valor)
        y -= 0.5*cm
    canv.setFont("Helvetica", 8)
    canv.setFillColor(white)
    canv.drawCentredString(w/2, 2.0*cm, f"Generado: {fecha_str}  |  v{VERSION}  |  NASA POWER · Copernicus CDSE Sentinel-2 · IA (IsolationForest)")
    canv.setFont("Helvetica", 7.5)
    canv.setFillColor(HexColor("#95D5B2"))
    canv.drawCentredString(w/2, 1.3*cm, "Estimaciones basadas en interpolación sinusoidal Tmax/Tmin. Citar: INTA Marcos Juárez / Univ. Nebraska.")

# ============================================================================
# ASSEMBLER PRINCIPAL
# ============================================================================
def build_report(res, output_path=None, lote_id=None):
    styles    = build_styles()
    cultivo   = res["cultivo"]
    año_act   = max(res["ndvi_historico"].keys())
    fecha_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    umbral_clima = res['conf'].get('umbral_clima', 40)

    if lote_id is None:
        lote_id = f"Lote {cultivo.capitalize()} {año_act}"
    if output_path is None:
        output_path = f"AgroIA_{cultivo}_{año_act}.pdf"

    cover_path = tempfile.mktemp(suffix=".pdf")
    body_path  = tempfile.mktemp(suffix=".pdf")

    c = rl_canvas.Canvas(cover_path, pagesize=A4)
    build_cover_page(c, res, fecha_str, lote_id)
    c.showPage()
    c.save()

    decorator = PageDecorator(cultivo, lote_id, res["score"]["total"], fecha_str)
    doc = SimpleDocTemplate(body_path, pagesize=A4,
                            leftMargin=1.8*cm, rightMargin=1.8*cm,
                            topMargin=2.2*cm, bottomMargin=2.0*cm)
    story = []

    # Sección 1: Diagnóstico
    story.append(Paragraph("1. Diagnóstico integrado del lote", styles["h2"]))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(generar_texto_diagnostico(res), styles["body"]))
    story.append(Spacer(1, 0.4*cm))
    story.append(Paragraph("Composición del Score AgroIA", styles["h3"]))
    story.append(fig_to_rl_image(plot_score_gauge(res["score"]), width_cm=15))
    story.append(Paragraph("Fig. 1 — Score AgroIA (0–100) y desglose: Vigor (40%), Estabilidad (30%), Limpieza IA (20%), Clima (10%).", styles["caption"]))
    story.append(Spacer(1, 0.5*cm))

    # Sección 2: NDVI
    story.append(Paragraph("2. Análisis satelital — NDVI histórico (Copernicus CDSE Sentinel-2)", styles["h2"]))
    story.append(Spacer(1, 0.2*cm))
    años_validos = len(res["ndvi_historico"])
    años_excl    = len(res.get("anos_excluidos", []))
    story.append(Paragraph(
        f"Serie construida con <b>{años_validos} años válidos</b> de mosaicos medianos Sentinel-2 SR. "
        f"{f'<b>{años_excl} años excluidos</b> por NDVI fuera de rango agronómico.' if años_excl else ''}",
        styles["body"]))
    story.append(Spacer(1, 0.3*cm))
    story.append(fig_to_rl_image(plot_ndvi_historico(res["ndvi_historico"], res.get("ndvi_hist_bruto", res["ndvi_historico"]), res.get("anos_excluidos", []), cultivo, res["conf"], año_act), width_cm=16))
    story.append(Paragraph(f"Fig. 2 — NDVI mediano en ventana crítica (mes {res['conf']['mes_critico']}) por año.", styles["caption"]))
    story.append(Spacer(1, 0.5*cm))

    # Tabla NDVI simple
    story.append(Paragraph("Detalle por campaña", styles["h3"]))
    tabla_data = [[Paragraph(h, styles["table_header"]) for h in ["Año", "NDVI crítico", "Estado", "En Score"]]]
    ndvi_bruto = res.get("ndvi_hist_bruto", res["ndvi_historico"])
    for yr in sorted(ndvi_bruto.keys()):
        val      = ndvi_bruto.get(yr)
        en_score = "Sí" if yr in res["ndvi_historico"] else "No"
        estado   = "Válido" if yr in res["ndvi_historico"] else "Excluido" if yr in res.get("anos_excluidos", []) else "Sin dato"
        tabla_data.append([Paragraph(str(yr), styles["table_cell"]),
                           Paragraph(f"{val:.3f}" if val else "—", styles["table_cell"]),
                           Paragraph(estado, styles["table_cell"]),
                           Paragraph(en_score, styles["table_cell"])])
    tabla = Table(tabla_data, colWidths=[3*cm, 4*cm, 4*cm, 3.5*cm])
    tabla.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), C_PRIMARY), ("TEXTCOLOR", (0,0), (-1,0), white),
                                ("ROWBACKGROUNDS", (0,1), (-1,-1), [white, C_ACCENT_BG]),
                                ("GRID", (0,0), (-1,-1), 0.3, C_BORDER),
                                ("ALIGN", (0,0), (-1,-1), "CENTER"), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
                                ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5)]))
    story.append(tabla)
    story.append(Spacer(1, 0.6*cm))

    # Tabla histórica de scores
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph("Historial de Scores por Campaña", styles["h3"]))
    story.append(Spacer(1, 0.2*cm))
    anos_hist = sorted(res["ndvi_historico"].keys())
    filas = [[Paragraph(h, styles["table_header"]) for h in
              ["Año", "NDVI", "Estrés (h)", "Vigor", "Estab.", "Limp.", "Clima", "Score"]]]
    for a in anos_hist:
        ndvi  = res["ndvi_historico"][a]
        horas = res["horas_calor_hist"].get(a, 0)
        previos = [res["ndvi_historico"][y] for y in anos_hist if y < a]
        
        # Usar la lógica centralizada de calcular_score
        s = calcular_score(ndvi, horas, previos + [ndvi], umbral_clima)
        
        vigor = s["vigor"]
        est   = s["estabilidad"]
        limp  = s["limpieza"]
        clima = s["clima"]
        total = s["total"]
        
        filas.append([Paragraph(str(a), styles["table_cell"]),
                      Paragraph(f"{ndvi:.3f}", styles["table_cell"]),
                      Paragraph(f"{horas:.1f}", styles["table_cell"]),
                      Paragraph(f"{vigor:.1f}", styles["table_cell"]),
                      Paragraph(f"{est:.1f}", styles["table_cell"]),
                      Paragraph(f"{limp:.1f}", styles["table_cell"]),
                      Paragraph(f"{clima:.1f}", styles["table_cell"]),
                      Paragraph(f"{total}/100", styles["table_cell"])])
                      
    tabla_hist = Table(filas, colWidths=[1.5*cm, 1.6*cm, 1.9*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.5*cm, 1.5*cm])
    tabla_hist.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), C_PRIMARY), ("TEXTCOLOR", (0,0), (-1,0), white),
                                     ("ROWBACKGROUNDS", (0,1), (-1,-1), [white, C_ACCENT_BG]),
                                     ("GRID", (0,0), (-1,-1), 0.3, C_BORDER),
                                     ("ALIGN", (0,0), (-1,-1), "CENTER"), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
                                     ("FONTSIZE", (0,0), (-1,-1), 7.5),
                                     ("TOPPADDING", (0,0), (-1,-1), 4), ("BOTTOMPADDING", (0,0), (-1,-1), 4)]))
    story.append(tabla_hist)
    story.append(Spacer(1, 0.3*cm))

    # Sección 3: Clima
    story.append(Paragraph("3. Análisis climático — Estrés térmico NASA POWER", styles["h2"]))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        f"Fuente: NASA POWER (~0.5°). Método: interpolación sinusoidal T(h) = Tmean + Tamp·cos(π·(h−14)/12). "
        f"Umbral térmico: <b>{res['conf']['umbral_calor']}°C</b>. "
        f"Umbral de penalización Score Clima: <b>{umbral_clima}h</b> "
        f"(ref. {res['conf']['biblio']}).",
        styles["body"]))
    story.append(Spacer(1, 0.3*cm))
    story.append(fig_to_rl_image(plot_estres_termico(res["horas_calor_hist"], res["conf"], año_act), width_cm=15))
    story.append(Paragraph(
        f"Fig. 3 — Horas sobre {res['conf']['umbral_calor']}°C en mes crítico (mes {res['conf']['mes_critico']}).", styles["caption"]))
    story.append(Spacer(1, 0.6*cm))

    # Sección 4: Recomendaciones
    story.append(Paragraph("4. Recomendaciones de manejo", styles["h2"]))
    story.append(Spacer(1, 0.2*cm))
    for estilo, texto in generar_recomendaciones(res):
        story.append(Paragraph(texto, estilo))
        story.append(Spacer(1, 0.15*cm))
        
    if res["es_variable"]:
        story.append(Spacer(1, 0.3*cm))
        story.append(Paragraph("Zonificación A/B/C activada", styles["h3"]))
        zona_tabla = [
            [Paragraph(h, styles["table_header"]) for h in ["Zona", "Descripción", "Recomendación de manejo"]],
            [Paragraph("A", styles["table_cell"]), Paragraph("Alto potencial", styles["table_cell"]), Paragraph("Invertir al máximo.", styles["table_cell"])],
            [Paragraph("B", styles["table_cell"]), Paragraph("Potencial variable", styles["table_cell"]), Paragraph("Planteo estándar.", styles["table_cell"])],
            [Paragraph("C", styles["table_cell"]), Paragraph("Zona limitante", styles["table_cell"]), Paragraph("Planteo defensivo.", styles["table_cell"])],
        ]
        zt = Table(zona_tabla, colWidths=[2*cm, 6*cm, 6.5*cm])
        zt.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), C_PRIMARY),
            ("BACKGROUND", (0,1), (0,1), HexColor(ZONA_COLORS["A"])),
            ("BACKGROUND", (0,2), (0,2), HexColor(ZONA_COLORS["B"])),
            ("BACKGROUND", (0,3), (0,3), HexColor(ZONA_COLORS["C"])),
            ("TEXTCOLOR", (0,1), (0,-1), white),
            ("ROWBACKGROUNDS", (1,1), (-1,-1), [white, C_NEUTRAL, white]),
            ("GRID", (0,0), (-1,-1), 0.3, C_BORDER),
            ("ALIGN", (0,0), (-1,-1), "CENTER"), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("TOPPADDING", (0,0), (-1,-1), 6), ("BOTTOMPADDING", (0,0), (-1,-1), 6)]))
        story.append(zt)
    else:
        story.append(Spacer(1, 0.2*cm))
        story.append(Paragraph(f"✓ <b>Lote Homogéneo (CV = {res['cv']:.3f}):</b> manejo uniforme apropiado.", styles["alert_ok"]))

    # Sección 5: Metodología
    story.append(PageBreak())
    story.append(Paragraph("5. Cuadro metodológico y trazabilidad", styles["h2"]))
    story.append(Spacer(1, 0.3*cm))
    met_tabla = [
        [Paragraph(h, styles["table_header"]) for h in ["Módulo", "Fuente", "Método", "Limitación conocida"]],
        [Paragraph("NDVI histórico", styles["table_cell"]), Paragraph("Copernicus CDSE Sentinel-2 L2A", styles["table_cell"]), Paragraph("Statistical API + máscara SCL", styles["table_cell"]), Paragraph("Nubosidad", styles["table_cell"])],
        [Paragraph("Estrés térmico", styles["table_cell"]), Paragraph("NASA POWER (~0.5°)", styles["table_cell"]), Paragraph("Sinusoidal", styles["table_cell"]), Paragraph("Resolución espacial", styles["table_cell"])],
        [Paragraph("Score Vigor", styles["table_cell"]), Paragraph("CDSE NDVI", styles["table_cell"]), Paragraph("Normalización", styles["table_cell"]), Paragraph("Tope 0.9", styles["table_cell"])],
    ]
    mt = Table(met_tabla, colWidths=[3.2*cm, 3.5*cm, 4.8*cm, 3*cm])
    mt.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), C_PRIMARY),
                             ("ROWBACKGROUNDS", (0,1), (-1,-1), [white, C_ACCENT_BG]),
                             ("GRID", (0,0), (-1,-1), 0.3, C_BORDER),
                             ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
                             ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5),
                             ("FONTSIZE", (0,0), (-1,-1), 7.5)]))
    story.append(mt)
    
    if res.get("log"):
        story.append(Spacer(1, 0.5*cm))
        story.append(Paragraph("Log de procesamiento", styles["h3"]))
        for entrada in res["log"]:
            story.append(Paragraph(entrada, styles["log"]))
            
    story.append(Spacer(1, 0.8*cm))
    story.append(HRFlowable(width="100%", thickness=0.3, color=C_BORDER, spaceAfter=4))
    story.append(Paragraph(
        f"AgroIA Report v{VERSION} — Generado: {fecha_str} — "
        f"NASA POWER · Copernicus CDSE Sentinel-2 · sklearn IsolationForest", styles["meta"]))

    doc.build(story, onFirstPage=decorator, onLaterPages=decorator)

    writer = PdfWriter()
    for path in [cover_path, body_path]:
        reader = PdfReader(path)
        for page in reader.pages:
            writer.add_page(page)
    with open(output_path, "wb") as f:
        writer.write(f)
    os.unlink(cover_path)
    os.unlink(body_path)
    return output_path

def generar_mapa_offline(lote_gdf, cultivo="maiz", score=None, conf=None, zonas_gdf=None, output_path="mapa.html", puntos_gdf=None):
    centroide = lote_gdf.geometry.iloc[0].centroid
    m = folium.Map(location=[centroide.y, centroide.x], zoom_start=15, tiles='OpenStreetMap')
    folium.TileLayer(
        tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
        attr='Google Satellite', name='Satelite', overlay=False
    ).add_to(m)

    # Limpiar columnas no serializables (Timestamp, etc.) antes de pasar a Folium
    gdf_clean = lote_gdf[['geometry']].copy()
    folium.GeoJson(gdf_clean, name="Lote", style_function=lambda x: {'fillColor': 'none', 'color': 'white', 'weight': 3, 'fillOpacity': 0}).add_to(m)
    
    if puntos_gdf is not None and not puntos_gdf.empty:
        for zona in ['A', 'B', 'C']:
            color = ZONA_COLORS[zona]
            subset = puntos_gdf[puntos_gdf['zona'] == zona]
            if subset.empty: continue
            
            fg = folium.FeatureGroup(name=ZONA_LABELS[zona])
            for _, row in subset.iterrows():
                lat, lon = row.geometry.y, row.geometry.x
                popup_txt = f"<b>Ambiente {zona}</b><br>NDVI: {row['ndvi']:.3f}<br>{ZONA_LABELS[zona]}<br>Lat: {lat:.5f}<br>Lon: {lon:.5f}"
                folium.CircleMarker(
                    location=[lat, lon],
                    radius=5,
                    color=color,
                    fill=True,
                    fillColor=color,
                    fill_opacity=0.7,
                    weight=3,
                    tooltip=popup_txt
                ).add_to(fg)
            fg.add_to(m)
    elif zonas_gdf is not None:
        # Fallback por si no hay puntos pero hay polígonos
        for zona in ['A', 'B', 'C']:
            color = ZONA_COLORS[zona]
            subset = zonas_gdf[zonas_gdf['zona'] == zona]
            if subset.empty: continue
            
            # 1. Dibujar polígonos de zona (el área completa)
            folium.GeoJson(
                subset,
                name=ZONA_LABELS[zona],
                style_function=lambda x, c=color: {
                    'fillColor': c,
                    'color': 'white',
                    'weight': 1,
                    'fillOpacity': 0.4
                },
                tooltip=f"Zona {zona}"
            ).add_to(m)

            # 2. Agregar "Puntos de Acción" (Centroide de la parte más grande de cada zona)
            # Solo ponemos un punto por zona para no saturar el mapa
            largest_poly = max(subset.geometry, key=lambda p: p.area)
            centroid = largest_poly.centroid
            ndvi_val = subset.iloc[0]['ndvi']
            
            fg_points = folium.FeatureGroup(name=f"Puntos de Acción - {zona}")
            popup_txt = f"<b>PUNTO DE ACCIÓN {zona}</b><br>Recomendación: {zona}<br>NDVI: {ndvi_val:.3f}"
            
            folium.CircleMarker(
                location=[centroid.y, centroid.x],
                radius=8,
                color=color,
                fill=True,
                fill_opacity=0.9,
                popup=folium.Popup(popup_txt, max_width=200)
            ).add_to(fg_points)
            fg_points.add_to(m)
    
    folium.LayerControl().add_to(m)
    m.save(output_path)
    return output_path
