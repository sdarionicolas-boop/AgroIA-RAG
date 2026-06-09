"""
demo/record_cdse_states_demo.py
===============================

Prepara los 3 estados de banner que el enunciado de la Tarea 4.5 pide
screenshotear (FRESH, CACHED_EXPIRED_FALLBACK, NO_DATA_AVAILABLE).

Como NO podés grabar screenshots desde este entorno automatizado,
este script hace dos cosas:

  1. Genera un demo/cdse_states_preview.html con los 3 banners
     renderizados estáticamente (HTML+CSS, sin Streamlit). Sirve como
     mockup visual para incluir en el deck o como referencia para
     comparar contra los banners reales en Streamlit.

  2. Imprime los pasos manuales para forzar cada estado en la app real
     y grabar el screenshot correspondiente. Usa monkey-patches del
     extractor (vía un módulo demo/cdse_force_state.py opcional) o las
     instrucciones manuales abajo.

Uso:
    python demo/record_cdse_states_demo.py
    python demo/record_cdse_states_demo.py --html-only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


BANNER_HTML = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><title>AgroIA — Banners CDSE</title>
<style>
body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
       background: #f6f7f9; margin: 0; padding: 32px; color: #222; }}
.container {{ max-width: 880px; margin: 0 auto; }}
h1 {{ margin-bottom: 4px; }}
.subtitle {{ color: #666; margin-bottom: 32px; font-size: 14px; }}
.banner {{ display: flex; align-items: flex-start; gap: 12px;
           padding: 14px 18px; border-radius: 6px; margin-bottom: 18px;
           border-left: 6px solid; font-size: 14.5px; line-height: 1.45; }}
.banner .icon {{ font-size: 22px; flex-shrink: 0; }}
.banner .body {{ flex: 1; }}
.banner code {{ background: rgba(0,0,0,0.05); padding: 1px 6px; border-radius: 3px;
                font-family: ui-monospace, Consolas, monospace; font-size: 13px; }}
.banner b {{ display: block; margin-bottom: 4px; }}

.fresh                    {{ background: #ecfdf3; border-color: #16a34a; color: #14532d; }}
.cached_valid             {{ background: #f0f9ff; border-color: #0284c7; color: #0c4a6e; }}
.cached_stale             {{ background: #fefce8; border-color: #ca8a04; color: #713f12; }}
.cached_expired_fallback  {{ background: #fff7ed; border-color: #ea580c; color: #7c2d12; }}
.no_data_available        {{ background: #fef2f2; border-color: #dc2626; color: #7f1d1d; }}

.legend {{ font-size: 12px; color: #888; margin-top: 24px; line-height: 1.55; }}
</style>
</head><body><div class="container">
<h1>AgroIA — Banners de estado del extractor CDSE</h1>
<p class="subtitle">Preview estático de los 5 estados (Tarea 4.5). Comparar contra los banners reales en Streamlit.</p>

<div class="banner fresh"><div class="icon">✅</div><div class="body">
<b>FRESH (sin banner)</b>
En operación normal, Streamlit no muestra ningún banner. La API CDSE respondió en el primer intento.
<br><i>(Solo se renderiza la cabecera estándar del análisis.)</i>
</div></div>

<div class="banner cached_valid"><div class="icon">💾</div><div class="body">
<b>CACHED_VALID (sin banner)</b>
La caché local está vigente (edad menor al 90% del TTL). No se muestra banner — la app considera caché vigente como operación normal.
<br><i>(Sin notificación al usuario.)</i>
</div></div>

<div class="banner cached_stale"><div class="icon">🟡</div><div class="body">
<b>CACHED_STALE</b>
<b>Datos parcialmente actualizados.</b> Una o más consultas vinieron de caché próxima a expirar (&gt;90% del TTL). Sigue siendo confiable; el próximo análisis intentará refrescar contra la API CDSE.
</div></div>

<div class="banner cached_expired_fallback"><div class="icon">🟠</div><div class="body">
<b>CACHED_EXPIRED_FALLBACK</b>
<b>API CDSE no responde — usando caché expirada.</b> Algunas estadísticas se sirvieron desde respuestas anteriores con TTL vencido (&gt;48 h). El análisis es indicativo; reintentá en ~5 min para datos frescos.
</div></div>

<div class="banner no_data_available"><div class="icon">🔴</div><div class="body">
<b>NO_DATA_AVAILABLE</b>
<b>Sin datos satelitales disponibles.</b> Ni la API CDSE ni la caché local pudieron responder. Reintentá en ~5 min. Si persiste, verificá credenciales CDSE en <code>config/.env</code> y la conectividad de red.
</div></div>

<div class="legend">
Orden de severidad (de menos a más grave):<br>
<code>FRESH</code> &lt; <code>CACHED_VALID</code> &lt; <code>CACHED_STALE</code> &lt; <code>CACHED_EXPIRED_FALLBACK</code> &lt; <code>NO_DATA_AVAILABLE</code>.<br>
El banner que se muestra corresponde al estado <b>peor</b> de toda la corrida (no a la última consulta).
</div>

</div></body></html>
"""


PASOS_MANUALES = """\
====================================================================
📹 PASOS PARA GRABAR SCREENSHOTS DE LOS 3 BANNERS PEDIDOS
====================================================================

El enunciado pide screenshots de: FRESH, CACHED_EXPIRED_FALLBACK,
NO_DATA_AVAILABLE.

Banner 1 — FRESH (operación normal)
-----------------------------------
1. Asegurate de tener config/.env con credenciales CDSE válidas y conexión.
2. python start.py
3. Modo 'Siniestros' → cargar un punto GPS válido (ej: -32.70, -62.10).
4. Ejecutar análisis.
5. Resultado esperado: NO se renderiza banner (estado FRESH es silencioso).
6. Screenshot: la pantalla de resultados SIN banner amarillo/naranja/rojo.

Banner 2 — CACHED_EXPIRED_FALLBACK
----------------------------------
Opción A (sin tocar red):
  1. Asegurate de tener al menos una entrada en data/cache_copernicus.db
     para el lote que vas a consultar (corrida previa exitosa).
  2. Forzá expiración: actualizá el timestamp en la BD a un valor antiguo:
       sqlite3 data/cache_copernicus.db
       UPDATE statistics_cache SET timestamp = strftime('%s','now') - 200000;
       .quit
  3. Desconectá la red o exportá un EODAG__COP_DATASPACE__AUTH__CREDENTIALS_PASSWORD inválido
     para forzar fallo de auth.
  4. Volvé a correr el análisis sobre el MISMO lote.
  5. Resultado esperado: banner naranja "API CDSE no responde — usando caché expirada".
  6. Screenshot.

Opción B (mock controlado):
  Levantar Streamlit con la variable de entorno AGROIA_FORCE_CDSE_STATE=cached_expired_fallback
  (NOTA: esto requiere agregar un branch de testing en el extractor; pendiente
  como follow-up si se quiere flujo 100% controlado para video).

Banner 3 — NO_DATA_AVAILABLE
----------------------------
1. Asegurate de NO tener caché previa para el (geom, year, month) que vas a usar:
     sqlite3 data/cache_copernicus.db "DELETE FROM statistics_cache;"
2. Desconectá la red.
3. Levantá Streamlit con red desconectada.
4. Modo 'Siniestros' → punto y fecha que NUNCA hayas consultado antes.
5. Resultado esperado: banner rojo "Sin datos satelitales disponibles".
6. Screenshot.

====================================================================
PREVIEW HTML
====================================================================
Se generó un preview estático con los 5 estados renderizados en HTML:
  demo/cdse_states_preview.html

Abrilo en el navegador para tener una referencia visual de cómo se
deberían ver los banners reales antes de grabar.
"""


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--html-only", action="store_true",
                        help="Solo genera el preview HTML, no imprime pasos.")
    args = parser.parse_args()

    html_path = _root / "demo" / "cdse_states_preview.html"
    html_path.write_text(BANNER_HTML, encoding="utf-8")
    print(f"✅ Preview HTML generado: {html_path}")

    if not args.html_only:
        print(PASOS_MANUALES)

    return 0


if __name__ == "__main__":
    sys.exit(main())
