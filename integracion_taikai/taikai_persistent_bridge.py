import asyncio
import os
import json
import logging
from mcp import ClientSession
from mcp.client.sse import sse_client
from dotenv import load_dotenv

# Configurar logging para ver qué pasa internamente
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("taikai-bridge")

load_dotenv("config/.env")

TOKEN = os.getenv("TAIKAI_MCP_TOKEN")
URL = "https://mcp.taikai.network/mcp"

async def connect_and_list():
    if not TOKEN:
        print("ERROR: TAIKAI_MCP_TOKEN no configurado.")
        return

    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "text/event-stream",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    print(f"Intentando conexión persistente a {URL}...")
    
    try:
        # Aumentamos los timeouts para evitar cierres prematuros
        async with sse_client(URL, headers=headers, timeout=15.0, sse_read_timeout=60.0) as (read, write):
            print("Conexión de red establecida.")
            async with ClientSession(read, write) as session:
                print("Iniciando handshake MCP...")
                await session.initialize()
                print("¡Handshake exitoso!")
                
                print("Solicitando lista de herramientas...")
                tools = await session.list_tools()
                
                print(f"\n✅ CONECTADO EXITOSAMENTE A TAIKAI MCP")
                print(f"Herramientas encontradas: {len(tools.tools)}")
                for t in tools.tools:
                    print(f"- {t.name}: {t.description}")
                
                # Guardamos la lista de herramientas para referencia del usuario
                with open("taikai_tools_manifest.json", "w") as f:
                    json.dump([{"name": t.name, "description": t.description, "inputSchema": t.inputSchema} for t in tools.tools], f, indent=2)
                
    except Exception as e:
        print(f"\n❌ ERROR DE CONEXIÓN: {type(e).__name__}")
        print(f"Detalle: {e}")
        if "RemoteProtocolError" in str(e):
            print("Sugerencia: El servidor de TAIKAI cerró la conexión. Verifica si el MCP está habilitado en tu perfil de TAIKAI.")

if __name__ == "__main__":
    asyncio.run(connect_and_list())
