# src/bot/telegram_main.py
import logging
import re
import sys
import time
from pathlib import Path
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# === Inyectar src al path (para que funcione desde cualquier lugar) ===
project_root = Path(__file__).resolve().parent.parent.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# === Importar configuración centralizada ===
from utils.config import settings
from rag.core import consultar_agente, listar_lotes, get_historial_lote_raw

# === Logging ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(project_root / "logs" / "telegram_bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ============================================================================
# CACHE DE LOTES
# listar_lotes() hace una query a la BD. Con un bot activo, llamarla en cada
# mensaje es innecesario. Se cachea con un TTL de 5 minutos: suficiente para
# detectar lotes nuevos sin saturar la BD.
# ============================================================================
_lotes_cache: list[str] = []
_lotes_cache_ts: float = 0.0
_LOTES_CACHE_TTL: float = 300.0  # segundos


def _get_lotes_cached() -> list[str]:
    global _lotes_cache, _lotes_cache_ts
    if time.time() - _lotes_cache_ts > _LOTES_CACHE_TTL:
        try:
            _lotes_cache = listar_lotes()
            _lotes_cache_ts = time.time()
            logger.debug(f"Cache de lotes actualizado: {_lotes_cache}")
        except Exception as e:
            logger.error(f"Error al actualizar cache de lotes: {e}")
            # Si falla, devuelve lo que había (puede estar vacío al arrancar)
    return _lotes_cache


def _invalidar_cache_lotes():
    """Fuerza recarga del cache en la próxima llamada. Útil tras /lote."""
    global _lotes_cache_ts
    _lotes_cache_ts = 0.0


# ============================================================================
# PARSER DE LOTE_ID
# Estrategias en orden de prioridad:
#  1. Argumento explícito  →  /lote NOMBRE_LOTE
#  2. Mención en el texto  →  "... lote X ..." / "... lote: X ..."
#  3. Sesión previa        →  context.user_data["lote_activo"]
# ============================================================================
def _extraer_lote_del_texto(texto: str, lotes_disponibles: list[str]) -> str | None:
    """
    Busca en el texto libre si el usuario mencionó algún lote conocido.
    Primero intenta el patrón 'lote <nombre>', luego prueba coincidencia
    directa (case-insensitive) contra los lotes en base de datos.
    """
    # Patrón explícito: "lote X", "lote: X", "lote_X"
    patron = re.search(r'lote[_:\s]+([A-Za-z0-9_\-]+)', texto, re.IGNORECASE)
    if patron:
        candidato = patron.group(1).upper()
        # Verificar que exista en BD
        for lote in lotes_disponibles:
            if lote.upper() == candidato:
                return lote

    # Búsqueda directa: el nombre del lote aparece en el texto tal cual
    texto_upper = texto.upper()
    for lote in lotes_disponibles:
        if lote.upper() in texto_upper:
            return lote

    return None


# ============================================================================
# TECLADOS (UX PARA CAMPO)
# ============================================================================
def get_main_keyboard():
    """Teclado permanente con botones grandes para uso con el pulgar."""
    keyboard = [
        [KeyboardButton("📋 Mis Lotes"), KeyboardButton("🚜 Lote Activo")],
        [KeyboardButton("📊 Ver Historial"), KeyboardButton("❓ Ayuda")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_lotes_inline_keyboard(lotes):
    """Menú de botones para seleccionar lote con un toque."""
    keyboard = []
    # Agrupar de a 2 botones por fila para que sean grandes
    for i in range(0, len(lotes), 2):
        row = [InlineKeyboardButton(lotes[i], callback_data=f"sel_lote:{lotes[i]}")]
        if i + 1 < len(lotes):
            row.append(InlineKeyboardButton(lotes[i+1], callback_data=f"sel_lote:{lotes[i+1]}"))
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

# ============================================================================
# COMANDOS Y HANDLERS
# ============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌾 *Bienvenido a AgroIA*\n\n"
        "He configurado botones grandes para que puedas navegar fácilmente "
        "mientras estás en el campo.\n\n"
        "¿Qué querés consultar hoy?",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📋 *Guía de uso rápido:*\n\n"
        "1️⃣ Tocá *📋 Mis Lotes* para ver la lista.\n"
        "2️⃣ Seleccioná uno tocando su nombre.\n"
        "3️⃣ Una vez seleccionado, podés pedir el *Historial* o simplemente "
        "escribir tu duda (ej: _¿cómo viene el NDVI?_).\n\n"
        "💡 *Tip:* Podés cambiar de lote en cualquier momento seleccionando otro de la lista."
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_main_keyboard())

async def listar_lotes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _invalidar_cache_lotes()
    lotes = _get_lotes_cached()
    if not lotes:
        await update.message.reply_text("📭 No hay lotes cargados.")
        return
    
    await update.message.reply_text(
        "🚜 *Seleccioná un lote para trabajar:*",
        parse_mode="Markdown",
        reply_markup=get_lotes_inline_keyboard(lotes)
    )

async def lote_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la selección de lote desde los botones inline."""
    query = update.callback_query
    await query.answer()
    
    lote_id = query.data.split(":")[1]
    context.user_data["lote_activo"] = lote_id
    
    await query.edit_message_text(
        f"✅ Lote activo: *{lote_id}*\n\n"
        "Ahora podés:\n"
        "• Ver el historial con el botón de abajo.\n"
        "• Escribirme una pregunta técnica.",
        parse_mode="Markdown"
    )

async def ver_lote_activo_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lote_id = context.user_data.get("lote_activo")
    if not lote_id:
        await update.message.reply_text("⚠️ No tenés un lote seleccionado. Tocá *📋 Mis Lotes*.")
        return
    
    await update.message.reply_text(
        f"🚜 Estás trabajando sobre: *{lote_id}*\n\n"
        "¿Qué información necesitás?",
        parse_mode="Markdown"
    )

async def historial_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lote_id = context.user_data.get("lote_activo")
    if not lote_id:
        await update.message.reply_text("⚠️ Seleccioná un lote primero con *📋 Mis Lotes*.")
        return

    await update.message.reply_text(f"⏳ Generando informe histórico para {lote_id}...")
    
    try:
        filas = get_historial_lote_raw(lote_id)
        if not filas:
            await update.message.reply_text(f"📭 Sin historial para {lote_id}.")
            return

        lineas = [f"📊 *Historial: {lote_id}*\n"]
        lineas.append("`AÑO  NDVI  ESTRÉS  SCORE`")
        for h in filas:
            lineas.append(f"`{h['anio']}  {h['ndvi_critico']:.2f}  {h['horas_calor']:>5.1f}h  {h['score_total']:>3}/100`")
        
        await update.message.reply_text("\n".join(lineas), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def manejar_texto_libre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    
    # Mapear botones del teclado principal a funciones
    if texto == "📋 Mis Lotes":
        return await listar_lotes_cmd(update, context)
    if texto == "🚜 Lote Activo":
        return await ver_lote_activo_info(update, context)
    if texto == "📊 Ver Historial":
        return await historial_cmd(update, context)
    if texto == "❓ Ayuda":
        return await ayuda(update, context)
    
    # Si no es un botón, es una pregunta para la IA
    await manejar_pregunta(update, context)

# (mantener funciones originales: _extraer_lote_del_texto, manejar_pregunta, etc. pero integrarlas)

    texto = update.message.text.strip()
    user_id = update.effective_user.id
    logger.info(f"Usuario {user_id}: '{texto}'")

    # 1. Intentar extraer lote del texto (usa cache — sin query extra por mensaje)
    lotes_disponibles = _get_lotes_cached()
    lote_del_texto = _extraer_lote_del_texto(texto, lotes_disponibles)

    if lote_del_texto:
        # Actualizar sesión con el lote mencionado
        context.user_data["lote_activo"] = lote_del_texto
        lote_id = lote_del_texto
    else:
        # 2. Usar lote de la sesión
        lote_id = context.user_data.get("lote_activo")
        # Verificar que el lote de sesión siga existiendo en la BD
        if lote_id and lote_id not in lotes_disponibles:
            logger.warning(f"Lote de sesión '{lote_id}' ya no existe en BD. Limpiando sesión.")
            context.user_data.pop("lote_activo", None)
            lote_id = None

    if not lote_id:
        # 3. No hay lote: pedir al usuario que seleccione uno
        lista = "\n".join(f"  • {l}" for l in lotes_disponibles) or "  (vacío)"
        await update.message.reply_text(
            "🤔 No pude identificar el lote al que te referís.\n\n"
            f"Usá /lote <nombre> para seleccionar uno:\n{lista}"
        )
        return

    try:
        await update.message.reply_text("⏳ Consultando base de conocimientos...")
        respuesta = consultar_agente(lote_id, texto, top_k=3)
        await update.message.reply_text(respuesta, parse_mode="Markdown")
        logger.info(f"Respuesta enviada a usuario {user_id} (lote: {lote_id})")
    except Exception as e:
        logger.error(f"Error procesando pregunta: {e}")
        await update.message.reply_text("⚠️ Ocurrió un error. Reintentá en unos segundos.")


# ============================================================================
# FUNCIÓN PRINCIPAL
# ============================================================================
def main():
    if not settings.telegram_token:
        logger.error("❌ TELEGRAM_TOKEN no configurado en config/.env")
        return

    app = Application.builder().token(settings.telegram_token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ayuda", ayuda))
    app.add_handler(CommandHandler("lotes", listar_lotes_cmd))
    app.add_handler(CommandHandler("lote", ver_lote_activo_info))
    app.add_handler(CommandHandler("historial", historial_cmd))
    
    # Handler para botones del teclado y texto libre
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_texto_libre))
    
    # Handler para selección de lotes vía botones inline
    app.add_handler(CallbackQueryHandler(lote_callback_handler, pattern="^sel_lote:"))

    logger.info(f"🤖 Bot iniciado con token: {settings.telegram_token[:10]}...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
