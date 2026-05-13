#!/usr/bin/env python3
"""
AgroIA RAG — Launcher unificado
================================
Arranca los tres servicios del sistema con un solo comando:

  python start.py            → levanta API + Streamlit + Bot
  python start.py --api      → solo API FastAPI
  python start.py --ui       → solo Streamlit
  python start.py --bot      → solo bot Telegram
  python start.py --check    → solo verifica prerequisitos y sale

Ctrl+C detiene todos los procesos limpiamente.
"""

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

# ── Rutas base ────────────────────────────────────────────────────────────────
ROOT    = Path(__file__).resolve().parent
SRC     = ROOT / "src"
LOGS    = ROOT / "logs"
LOGS.mkdir(exist_ok=True)

# ── Colores ANSI (desactivados en Windows sin soporte) ────────────────────────
_WIN = sys.platform == "win32"
def _c(code: str, text: str) -> str:
    return text if _WIN else f"\033[{code}m{text}\033[0m"

OK   = lambda t: _c("32;1", t)   # verde
WARN = lambda t: _c("33;1", t)   # amarillo
ERR  = lambda t: _c("31;1", t)   # rojo
INFO = lambda t: _c("36;1", t)   # cyan
BOLD = lambda t: _c("1",    t)   # negrita

# ── Configuración de servicios ────────────────────────────────────────────────
PYTHON = sys.executable

SERVICES = {
    "api": {
        "label":   "FastAPI  (ingesta)",
        "cmd":     [PYTHON, "-m", "uvicorn", "ingesta.api:app",
                    "--host", "0.0.0.0", "--port", "8000", "--reload"],
        "cwd":     SRC,
        "log":     LOGS / "api.log",
        "url":     "http://localhost:8000/health",
        "emoji":   "🔌",
    },
    "ui": {
        "label":   "Streamlit (dashboard)",
        "cmd":     [PYTHON, "-m", "streamlit", "run", str(SRC / "streamlit_app.py"),
                    "--server.port", "8501", "--server.headless", "true"],
        "cwd":     SRC,
        "log":     LOGS / "streamlit.log",
        "url":     "http://localhost:8501",
        "emoji":   "🌾",
    },
    "bot": {
        "label":   "Telegram  (bot)",
        "cmd":     [PYTHON, str(SRC / "bot" / "telegram_main.py")],
        "cwd":     ROOT,
        "log":     LOGS / "telegram_bot.log",
        "url":     None,
        "emoji":   "🤖",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# CHECKS DE PREREQUISITOS
# ─────────────────────────────────────────────────────────────────────────────
def check_prerequisites() -> bool:
    """Verifica BD, Ollama y token de Telegram. Retorna True si todo está OK."""
    import importlib.util
    import urllib.request
    import urllib.error

    ok = True
    print(BOLD("\n── Verificando prerequisitos ─────────────────────────────"))

    # 1. Dependencias Python
    deps = ["fastapi", "uvicorn", "streamlit", "psycopg2", "ollama",
            "telegram", "pydantic_settings", "pandas", "matplotlib", "numpy"]
    faltantes = []
    for dep in deps:
        if importlib.util.find_spec(dep) is None:
            faltantes.append(dep)
    if faltantes:
        print(ERR(f"  ✗ Dependencias faltantes: {', '.join(faltantes)}"))
        print(WARN(f"    → pip install -r {ROOT / 'requirements.txt'} --break-system-packages"))
        ok = False
    else:
        print(OK("  ✓ Dependencias Python"))

    # 2. PostgreSQL (psycopg2 connect)
    try:
        sys.path.insert(0, str(SRC))
        from utils.config import settings
        import psycopg2
        conn = psycopg2.connect(
            host=settings.db_host, port=settings.db_port,
            dbname=settings.db_name, user=settings.db_user,
            password=settings.db_password, connect_timeout=3,
        )
        conn.close()
        print(OK(f"  ✓ PostgreSQL  ({settings.db_host}:{settings.db_port}/{settings.db_name})"))
    except Exception as e:
        print(ERR(f"  ✗ PostgreSQL  → {e}"))
        print(WARN( "    → ¿Docker corriendo? docker start postgres-agri"))
        ok = False

    # 3. Ollama
    try:
        from utils.config import settings as s
        url = s.ollama_url + "/api/tags"
        req = urllib.request.urlopen(url, timeout=3)
        import json
        data = json.loads(req.read())
        modelos = [m["name"] for m in data.get("models", [])]
        for modelo in [s.embedding_model, s.generation_model]:
            if any(modelo in m for m in modelos):
                print(OK(f"  ✓ Ollama modelo '{modelo}'"))
            else:
                print(WARN(f"  ⚠ Ollama modelo '{modelo}' no encontrado"))
                print(WARN(f"    → ollama pull {modelo}"))
                # No es fatal: el modelo puede estar con nombre ligeramente distinto
    except Exception as e:
        print(ERR(f"  ✗ Ollama ({s.ollama_url}) → {e}"))
        print(WARN( "    → ¿Ollama corriendo? ollama serve"))
        ok = False

    # 4. Token de Telegram
    try:
        from utils.config import settings as s
        if not s.telegram_token or s.telegram_token == "TU_TOKEN_AQUI":
            print(WARN("  ⚠ TELEGRAM_TOKEN no configurado — bot no va a funcionar"))
            print(WARN("    → Editá config/.env y pegá tu token de @BotFather"))
        else:
            print(OK( "  ✓ TELEGRAM_TOKEN configurado"))
    except Exception:
        pass

    # 5. Clave de ingesta
    try:
        from utils.config import settings as s
        if not s.ingesta_secret_key or s.ingesta_secret_key == "mi_clave_super_secreta":
            print(WARN("  ⚠ INGESTA_SECRET_KEY usa el valor demo"))
            print(WARN("    → Cambiala en config/.env antes de exponer la API"))
        else:
            print(OK( "  ✓ INGESTA_SECRET_KEY configurada"))
    except Exception:
        pass

    print()
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# LANZAMIENTO Y GESTIÓN DE PROCESOS
# ─────────────────────────────────────────────────────────────────────────────
_procs: dict[str, subprocess.Popen] = {}


def launch(name: str) -> None:
    cfg = SERVICES[name]
    log_file = open(cfg["log"], "a", encoding="utf-8")
    proc = subprocess.Popen(
        cfg["cmd"],
        cwd=cfg["cwd"],
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    _procs[name] = proc
    print(f"  {cfg['emoji']}  {BOLD(cfg['label']):<28} PID {INFO(str(proc.pid))}"
          f"  log → {cfg['log'].name}")


def wait_for_http(url: str, timeout: int = 15) -> bool:
    """Polling liviano hasta que el endpoint responde 200."""
    import urllib.request, urllib.error
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.8)
    return False


def stop_all(signum=None, frame=None) -> None:
    print(BOLD("\n\n── Deteniendo servicios ──────────────────────────────────"))
    for name, proc in _procs.items():
        cfg = SERVICES[name]
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            print(f"  {cfg['emoji']}  {cfg['label']} — {WARN('detenido')}")
    print(OK("  Todo limpio. ¡Hasta la próxima!\n"))
    sys.exit(0)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="AgroIA RAG — Launcher unificado",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--api",   action="store_true", help="Solo FastAPI")
    parser.add_argument("--ui",    action="store_true", help="Solo Streamlit")
    parser.add_argument("--bot",   action="store_true", help="Solo Telegram bot")
    parser.add_argument("--pipeline", nargs="+", metavar=("SHP", "CULTIVO"),
                        help="Análisis local de un shapefile/GeoJSON: <ruta.shp> [cultivo]")
    parser.add_argument("--batch-geojson", nargs="+", metavar=("GEOJSON", "CULTIVO"),
                        help="Batch desde GeoJSON del poligonizador: <ruta.geojson> [cultivo] [limit]")
    parser.add_argument("--check", action="store_true", help="Solo verificar prerequisitos")
    parser.add_argument("--skip-checks", action="store_true",
                        help="Saltar la verificación de prerequisitos")
    args = parser.parse_args()

    # Inyectar src al path (necesario para ambos modos pipeline)
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    # ── Pipeline Local — un único shapefile/GeoJSON ───────────────────────────
    if args.pipeline:
        shp_path = args.pipeline[0]
        cultivo = args.pipeline[1] if len(args.pipeline) > 1 else "maiz"
        from pipeline import run_full_analysis
        try:
            run_full_analysis(shp_path, cultivo=cultivo)
            sys.exit(0)
        except Exception as e:
            print(ERR(f"\n❌ Error en el pipeline: {e}"))
            sys.exit(1)

    # ── Batch — GeoJSON del poligonizador → cada polígono individualmente ─────
    if args.batch_geojson:
        geojson_path = args.batch_geojson[0]
        cultivo_default = args.batch_geojson[1] if len(args.batch_geojson) > 1 else "maiz"
        limit = int(args.batch_geojson[2]) if len(args.batch_geojson) > 2 else None
        from pipeline import run_batch_from_geojson
        try:
            resultados = run_batch_from_geojson(
                geojson_path,
                cultivo_default=cultivo_default,
                limit=limit,
            )
            ok = sum(1 for r in resultados if r["status"] == "OK")
            print(INFO(f"Batch finalizado: {ok}/{len(resultados)} exitosos."))
            sys.exit(0)
        except Exception as e:
            print(ERR(f"\n❌ Error en batch: {e}"))
            sys.exit(1)

    print(BOLD("\n╔══════════════════════════════════════════╗"))
    print(BOLD("║        AgroIA RAG — Launcher v1          ║"))
    print(BOLD("╚══════════════════════════════════════════╝"))

    # ── Prerequisitos ──────────────────────────────────────────────────────────
    if not args.skip_checks:
        checks_ok = check_prerequisites()
        if args.check:
            sys.exit(0 if checks_ok else 1)
        if not checks_ok:
            print(WARN("⚠  Algunos prerequisitos fallaron. "
                        "Usá --skip-checks para arrancar de todas formas.\n"))
            sys.exit(1)
    elif args.check:
        print(WARN("--check y --skip-checks son contradictorios."))
        sys.exit(1)

    # ── Determinar qué levantar ────────────────────────────────────────────────
    any_flag = args.api or args.ui or args.bot
    to_launch = []
    if args.api or not any_flag:
        to_launch.append("api")
    if args.ui or not any_flag:
        to_launch.append("ui")
    if args.bot or not any_flag:
        to_launch.append("bot")

    # ── Registrar señales de shutdown ─────────────────────────────────────────
    signal.signal(signal.SIGINT,  stop_all)
    signal.signal(signal.SIGTERM, stop_all)

    # ── Lanzar servicios ───────────────────────────────────────────────────────
    print(BOLD("── Arrancando servicios ──────────────────────────────────"))
    for name in to_launch:
        launch(name)

    # ── Esperar a que la API esté lista antes de mostrar URLs ─────────────────
    print()
    if "api" in to_launch:
        sys.stdout.write(INFO("  Esperando que FastAPI levante..."))
        sys.stdout.flush()
        if wait_for_http("http://localhost:8000/health"):
            print(OK(" ✓"))
        else:
            print(WARN(" (no respondió en 15s, revisá logs/api.log)"))

    # ── URLs de acceso ─────────────────────────────────────────────────────────
    print(BOLD("\n── Servicios disponibles ─────────────────────────────────"))
    if "api" in to_launch:
        print(f"  🔌  API FastAPI   →  {INFO('http://localhost:8000')}")
        print(f"      Docs         →  {INFO('http://localhost:8000/docs')}")
        print(f"      Health       →  {INFO('http://localhost:8000/health')}")
    if "ui" in to_launch:
        print(f"  🌾  Streamlit     →  {INFO('http://localhost:8501')}")
    if "bot" in to_launch:
        print(f"  🤖  Telegram bot  →  corriendo en polling")
    print()
    print(BOLD(f"  Logs en:  {LOGS}/"))
    print(BOLD("  Ctrl+C para detener todo\n"))

    # ── Loop de monitoreo ─────────────────────────────────────────────────────
    # Detecta si algún proceso murió inesperadamente y avisa
    CHECK_INTERVAL = 10  # segundos
    try:
        while True:
            time.sleep(CHECK_INTERVAL)
            for name, proc in list(_procs.items()):
                if proc.poll() is not None:
                    cfg = SERVICES[name]
                    print(ERR(
                        f"\n  ⚠  {cfg['emoji']} {cfg['label']} "
                        f"terminó inesperadamente (exit {proc.returncode})"
                    ))
                    print(WARN(f"     → Revisá {cfg['log']}"))
                    del _procs[name]
            if not _procs:
                print(ERR("\n  Todos los servicios terminaron. Saliendo."))
                sys.exit(1)
    except KeyboardInterrupt:
        stop_all()


if __name__ == "__main__":
    main()
