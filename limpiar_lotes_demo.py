#!/usr/bin/env python3
"""
limpiar_lotes_demo.py
─────────────────────
Escanea la BD vía API, identifica lotes de prueba/demo y los elimina.

Criterios de detección:
  1. lote_id está en la lista de lotes conocidos de archivos demo
  2. lote_id contiene palabras clave de prueba (demo, prueba, test, establecimiento)
  3. score_clima idéntico en TODOS los años (placeholder hardcodeado)
  4. Historial vacío (sin campañas registradas)

Uso:
  python limpiar_lotes_demo.py              # modo preview (no borra)
  python limpiar_lotes_demo.py --confirmar  # elimina los lotes detectados
"""

import os
import sys
import requests

# ── Configuración ──────────────────────────────────────────────────────────────
API_URL    = "http://localhost:8000"
SECRET_KEY = os.getenv("INGESTA_SECRET_KEY", "")   # configurar en config/.env

HEADERS = {
    "Authorization": f"Bearer {SECRET_KEY}",
    "Content-Type":  "application/json",
}

# Lotes conocidos de archivos demo (verificados manualmente)
LOTES_DEMO_CONOCIDOS = {
    "TAYPE_LOTE_001",
    "TAYPE_LOTE_002",
    "INTA_PIVOTE_001",
    "INTA_PIVOTE_002",
    "INTA_PIVOTE_003",
    "Establecimiento_Demo_2026",
    "Establecimiento Demo 2026",
    "maizsuperprueba",
}

# Palabras clave en el lote_id que indican lote de prueba
KEYWORDS_PRUEBA = ["demo", "prueba", "test", "establecimiento", "ejemplo"]


# ── Lógica de detección ────────────────────────────────────────────────────────

def es_lote_demo(lote_id: str, detalle: dict) -> tuple[bool, str]:
    """
    Retorna (True, motivo) si el lote es de prueba, (False, '') si no.
    """
    # 1. Lista conocida
    if lote_id in LOTES_DEMO_CONOCIDOS:
        return True, "lote_id en lista demo conocida"

    # 2. Keyword en el nombre
    lote_lower = lote_id.lower()
    for kw in KEYWORDS_PRUEBA:
        if kw in lote_lower:
            return True, f"lote_id contiene '{kw}'"

    # 3. Historial vacío
    historial = detalle.get("historial", [])
    if not historial:
        return True, "historial vacío (sin campañas)"

    # 4. score_clima idéntico en todos los años (placeholder)
    climas = [h.get("score_clima") for h in historial if h.get("score_clima") is not None]
    if len(climas) >= 3 and len(set(climas)) == 1:
        return True, f"score_clima constante en todos los años ({climas[0]})"

    return False, ""


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    confirmar = "--confirmar" in sys.argv

    print("═" * 60)
    print("  AgroIA — Limpieza de Lotes Demo/Prueba")
    print(f"  Modo: {'ELIMINACIÓN REAL' if confirmar else 'PREVIEW (sin borrar)'}")
    print("═" * 60)

    # 1. Obtener lista de lotes
    try:
        r = requests.get(f"{API_URL}/lotes", headers=HEADERS, timeout=10)
        r.raise_for_status()
        lotes = r.json()
    except Exception as e:
        print(f"\n❌ No se pudo conectar a la API: {e}")
        print("   ¿Está corriendo? → python start.py --api")
        sys.exit(1)

    print(f"\n  Total lotes en BD: {len(lotes)}\n")

    # 2. Escanear cada lote
    a_eliminar = []
    a_conservar = []

    for lote in lotes:
        lote_id = lote.get("lote_id") or lote.get("id", "")

        # Obtener detalle completo para revisar historial
        try:
            r2 = requests.get(f"{API_URL}/lotes/{lote_id}", headers=HEADERS, timeout=10)
            detalle = r2.json() if r2.ok else {}
        except Exception:
            detalle = {}

        es_demo, motivo = es_lote_demo(lote_id, detalle)

        if es_demo:
            a_eliminar.append((lote_id, motivo))
        else:
            a_conservar.append(lote_id)

    # 3. Reporte
    print(f"  ✅ Lotes reales (conservar): {len(a_conservar)}")
    for lid in sorted(a_conservar):
        print(f"     · {lid}")

    print(f"\n  🗑️  Lotes demo/prueba (eliminar): {len(a_eliminar)}")
    for lid, motivo in sorted(a_eliminar):
        print(f"     · {lid}  ← {motivo}")

    if not a_eliminar:
        print("\n  No hay lotes para eliminar.")
        return

    # 4. Eliminar si se confirmó
    if not confirmar:
        print(f"\n  ⚠  Para eliminar los {len(a_eliminar)} lotes, ejecutá:")
        print(f"     python limpiar_lotes_demo.py --confirmar\n")
        return

    print(f"\n  Eliminando {len(a_eliminar)} lotes...")
    ok = 0
    fail = 0
    for lote_id, motivo in a_eliminar:
        try:
            r = requests.delete(f"{API_URL}/lotes/{lote_id}", headers=HEADERS, timeout=10)
            if r.ok:
                print(f"  ✓ Eliminado: {lote_id}")
                ok += 1
            else:
                print(f"  ✗ Falló {lote_id}: HTTP {r.status_code}")
                fail += 1
        except Exception as e:
            print(f"  ✗ Error en {lote_id}: {e}")
            fail += 1

    print(f"\n{'═'*60}")
    print(f"  Eliminados: {ok}  |  Fallidos: {fail}")
    print(f"  Lotes reales restantes en BD: {len(a_conservar)}")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()
