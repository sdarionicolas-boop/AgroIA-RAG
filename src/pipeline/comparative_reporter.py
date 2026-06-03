# src/pipeline/comparative_reporter.py
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image,
    Table, TableStyle, HRFlowable, PageBreak
)
from reportlab.pdfgen import canvas as rl_canvas

# Paleta AgroIA
C_PRIMARY    = HexColor("#1B4332")
C_SECONDARY  = HexColor("#40916C")
C_ACCENT_BG  = HexColor("#D8F3DC")
C_ALERT      = HexColor("#D62828")
C_WARN       = HexColor("#F4A261")
C_NEUTRAL    = HexColor("#F8F9FA")
C_TEXT       = HexColor("#212529")
C_MUTED      = HexColor("#6C757D")
C_BORDER     = HexColor("#DEE2E6")

VERSION = "1.1.0" # Final Demo Version

def build_styles():
    return {
        "h1": ParagraphStyle("H1", fontSize=24, fontName="Helvetica-Bold", textColor=white, leading=28, spaceAfter=12),
        "h2": ParagraphStyle("H2", fontSize=12, fontName="Helvetica-Bold", textColor=white, spaceAfter=10, spaceBefore=10, backColor=C_PRIMARY, leftIndent=-10, rightIndent=-10, borderPad=5),
        "body": ParagraphStyle("Body", fontSize=9, fontName="Helvetica", textColor=C_TEXT, leading=12),
        "th": ParagraphStyle("TH", fontSize=8, fontName="Helvetica-Bold", textColor=white, alignment=TA_CENTER),
        "tc": ParagraphStyle("TC", fontSize=8, fontName="Helvetica", textColor=C_TEXT, alignment=TA_CENTER),
        "caption": ParagraphStyle("Caption", fontSize=7, fontName="Helvetica-Oblique", textColor=C_MUTED, alignment=TA_CENTER, spaceBefore=4),
    }

def _sanitize_df(df):
    """Limpia el dataframe de nulos para evitar errores de graficación."""
    df = df.copy()
    cols_numericas = ["score_total", "cv_espacial", "superficie_ha"]
    for col in cols_numericas:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    return df

def plot_ranking_chart(df_lotes):
    """Genera un gráfico de barras comparativo de scores (Safe for Demo)."""
    df_lotes = _sanitize_df(df_lotes)
    df_lotes = df_lotes.sort_values("score_total", ascending=True)
    
    fig, ax = plt.subplots(figsize=(10, max(4, len(df_lotes)*0.5 + 2)))
    fig.patch.set_facecolor('white')
    
    scores = df_lotes["score_total"].values
    colors = ['#40916C' if s >= 70 else '#F4A261' if s >= 45 else '#D62828' for s in scores]
    bars = ax.barh(df_lotes["lote_id"], scores, color=colors, edgecolor='white')
    
    for bar in bars:
        width = bar.get_width()
        ax.text(width + 1, bar.get_y() + bar.get_height()/2, f"{int(width)}/100", va='center', fontsize=9, fontweight='bold', color='#212529')
        
    ax.set_title("Ranking de Lotes por Score AgroIA", fontsize=12, fontweight='bold', pad=15)
    ax.set_xlim(0, 110)
    ax.grid(axis='x', linestyle=':', alpha=0.5)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    plt.tight_layout()
    return fig

def plot_scatter_variability(df_lotes):
    """Scatter plot: Score vs Variabilidad (Safe for Demo)."""
    df_lotes = _sanitize_df(df_lotes)
    fig, ax = plt.subplots(figsize=(8, 6))
    fig.patch.set_facecolor('white')
    
    for _, row in df_lotes.iterrows():
        color = '#40916C' if row["score_total"] >= 70 else '#F4A261' if row["score_total"] >= 45 else '#D62828'
        ax.scatter(row["cv_espacial"], row["score_total"], s=row["superficie_ha"]*10 + 20, alpha=0.7, color=color, edgecolors='white')
        ax.text(row["cv_espacial"], row["score_total"] + 1.5, row["lote_id"], fontsize=7, ha='center')

    ax.set_xlabel("Variabilidad Espacial (CV)")
    ax.set_ylabel("Score AgroIA")
    ax.set_title("Relación Potencial vs. Heterogeneidad", fontsize=11, fontweight='bold')
    ax.axhline(70, color='#40916C', linestyle='--', alpha=0.3)
    ax.axhline(45, color='#F4A261', linestyle='--', alpha=0.3)
    ax.grid(True, linestyle=':', alpha=0.4)
    plt.tight_layout()
    return fig

def build_comparative_report(df_lotes, output_path="outputs/comparativa_lotes.pdf"):
    df_lotes = _sanitize_df(df_lotes)
    styles = build_styles()
    doc = SimpleDocTemplate(output_path, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
    story = []
    fecha_str = datetime.now().strftime("%d/%m/%Y")

    # Header
    story.append(Paragraph("Reporte Comparativo de Lotes", styles["h1"]))
    story.append(HRFlowable(width="100%", thickness=2, color=C_SECONDARY, spaceAfter=10))
    story.append(Paragraph(f"Fecha de generación: {fecha_str} | AgroIA Intelligence V{VERSION}", styles["body"]))
    story.append(Spacer(1, 0.5*cm))

    if df_lotes.empty:
        story.append(Paragraph("No hay lotes cargados para analizar.", styles["body"]))
        doc.build(story)
        return output_path

    # Resumen Ejecutivo
    total_ha = df_lotes["superficie_ha"].sum()
    score_avg = df_lotes["score_total"].mean()
    story.append(Paragraph("1. Resumen de Cartera", styles["h2"]))
    story.append(Paragraph(
        f"Se analizaron un total de <b>{len(df_lotes)} lotes</b> que suman <b>{total_ha:.1f} hectáreas</b>. "
        f"El score promedio ponderado de la cartera es de <b>{score_avg:.1f}/100</b>.",
        styles["body"]
    ))
    story.append(Spacer(1, 0.4*cm))

    # Ranking Table
    story.append(Paragraph("2. Ranking por Potencial (Score)", styles["h2"]))
    data = [[Paragraph(h, styles["th"]) for h in ["Lote ID", "Cultivo", "Superficie", "CV", "Score"]]]
    df_sorted = df_lotes.sort_values("score_total", ascending=False)
    for _, row in df_sorted.iterrows():
        data.append([
            Paragraph(str(row["lote_id"]), styles["tc"]),
            Paragraph(str(row.get("cultivo", "N/D")).capitalize(), styles["tc"]),
            Paragraph(f"{row['superficie_ha']:.1f} ha", styles["tc"]),
            Paragraph(f"{row['cv_espacial']:.3f}", styles["tc"]),
            Paragraph(f"<b>{int(row['score_total'])}</b>", styles["tc"])
        ])
    
    t = Table(data, colWidths=[4*cm, 3*cm, 3*cm, 3*cm, 3*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), C_PRIMARY),
        ('GRID', (0, 0), (-1, -1), 0.5, C_BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, C_NEUTRAL])
    ]))
    story.append(t)
    story.append(Spacer(1, 0.6*cm))

    # Charts
    try:
        from .reporter import fig_to_rl_image
        story.append(fig_to_rl_image(plot_ranking_chart(df_lotes), width_cm=16))
        story.append(Paragraph("Gráfico 1: Comparativa de performance relativa entre lotes.", styles["caption"]))
        story.append(Spacer(1, 1*cm))
        
        story.append(fig_to_rl_image(plot_scatter_variability(df_lotes), width_cm=14))
        story.append(Paragraph("Gráfico 2: Posicionamiento estratégico (Potencial vs Variabilidad).", styles["caption"]))
    except Exception as e:
        story.append(Paragraph(f"[Error al generar gráficos: {e}]", styles["body"]))
    
    story.append(PageBreak())
    story.append(Paragraph("3. Recomendaciones de Cartera", styles["h2"]))
    
    top_lote = df_sorted.iloc[0]
    worst_lote = df_sorted.iloc[-1]
    story.append(Paragraph(f"• <b>Prioridad de Inversión:</b> El lote <b>{top_lote['lote_id']}</b> presenta las mejores condiciones para maximizar rinde.", styles["body"]))
    story.append(Paragraph(f"• <b>Alerta de Riesgo:</b> El lote <b>{worst_lote['lote_id']}</b> requiere un planteo defensivo por bajo score relativo.", styles["body"]))
    
    doc.build(story)
    return output_path
