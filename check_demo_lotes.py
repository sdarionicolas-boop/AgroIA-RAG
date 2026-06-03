"""
Helper de pre-demo: chequea qué lotes están listos para grabar el video.
Verifica que metadata.score_desglose esté poblado y rankea los lotes por su
"potencial narrativo" (Clima bajo → activa alerta térmica → mejor demo).

Uso:
    python check_demo_lotes.py
"""
import sys
import json
from pathlib import Path

# Inyectar src/ en sys.path para reutilizar la config del proyecto
_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "src"))

import psycopg2
from src.utils.config import settings


def main():
    print(f"Conectando a Postgres: {settings.db_host}:{settings.db_port}/{settings.db_name} "
          f"como {settings.db_user}...")
    try:
        conn = psycopg2.connect(
            host=settings.db_host,
            port=settings.db_port,
            dbname=settings.db_name,
            user=settings.db_user,
            password=settings.db_password,
        )
    except Exception as e:
        print(f"❌ No se pudo conectar: {e}")
        sys.exit(1)

    print("✅ Conectado.\n")
    cur = conn.cursor()

    # ── 1. Lotes recientes con info clave ────────────────────────────────────
    cur.execute("""
        SELECT lote_id,
               metadata->'score_desglose' AS desglose,
               metadata->>'cv_espacial'   AS cv,
               score_total,
               updated_at
        FROM informes_lotes
        ORDER BY updated_at DESC
        LIMIT 10;
    """)
    rows = cur.fetchall()

    if not rows:
        print("⚠ No hay lotes en la base. Subí algún CSV/GeoJSON antes de filmar.")
        return

    print("=" * 100)
    print(f"{'LOTE':<35} {'SCORE':<7} {'CV':<8} {'CLIMA':<7} {'DESGLOSE':<8} {'LISTO_DEMO'}")
    print("=" * 100)

    candidatos = []
    for lote_id, desglose, cv, score, updated in rows:
        # Normalizar JSON
        desg_dict = desglose if isinstance(desglose, dict) else (json.loads(desglose) if desglose else {})
        clima = desg_dict.get("clima", None)
        tiene_desglose = bool(desg_dict)
        cv_val = float(cv) if cv else 0.0

        # Criterio "listo demo":
        #  - tiene score_desglose (sino, el RAG falla)
        #  - tiene CV > 0.05 (activa narrativa zonificación + recorrida a campo)
        listo = "✅" if (tiene_desglose and cv_val > 0.05) else "⚠"

        # Score narrativo: más bajo el clima → más impactante el demo
        clima_str = f"{clima:.1f}/10" if clima is not None else "N/D"
        desg_str = "OK" if tiene_desglose else "FALTA"

        print(f"{lote_id:<35} {score:<7} {cv_val:<8.3f} {clima_str:<7} {desg_str:<8} {listo}")

        if tiene_desglose and cv_val > 0.05:
            # Menor clima = mejor candidato narrativo
            candidatos.append((lote_id, clima if clima is not None else 10.0, score, cv_val))

    print("=" * 100)

    # ── 2. Recomendación de lote demo ─────────────────────────────────────────
    if candidatos:
        candidatos.sort(key=lambda x: x[1])  # por clima ascendente
        mejor = candidatos[0]
        print(f"\n🎬 LOTE RECOMENDADO PARA VIDEO: **{mejor[0]}**")
        print(f"   Score: {mejor[2]}/100  |  CV espacial: {mejor[3]:.3f}  |  Clima: {mejor[1]:.1f}/10")
        if mejor[1] <= 1.0:
            print("   → Alerta térmica activada → narrativa fuerte de estrés térmico documentado.")
        elif mejor[1] <= 5.0:
            print("   → Clima parcialmente castigado → narrativa moderada.")
        else:
            print("   → Clima alto → la narrativa va a centrarse en otros componentes (Limpieza/Estabilidad).")
    else:
        print("\n⚠ Ningún lote cumple los requisitos para demo óptima.")
        print("   Verificá que los lotes nuevos hayan sido procesados con el código actualizado")
        print("   y que tengan CV espacial > 0.05.")

    # ── 3. Diagnóstico de cualquier lote con desglose vacío ──────────────────
    cur.execute("""
        SELECT lote_id, updated_at
        FROM informes_lotes
        WHERE metadata->'score_desglose' IS NULL
           OR metadata->'score_desglose' = '{}'::jsonb
        ORDER BY updated_at DESC;
    """)
    sin_desglose = cur.fetchall()
    if sin_desglose:
        print(f"\n⚠ Lotes SIN score_desglose ({len(sin_desglose)} total):")
        for l, u in sin_desglose[:5]:
            print(f"   - {l} (actualizado: {u})")
        print("   Estos lotes NO van a activar el ranking pre-computado del RAG.")
        print("   Si querés usarlos, re-procesalos con el código actualizado.")

    cur.close()
    conn.close()
    print("\n✅ Listo. Usá el lote recomendado para el video.")


if __name__ == "__main__":
    main()
