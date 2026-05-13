import asyncio
import os
import json
from mcp import ClientSession
from mcp.client.sse import sse_client
from dotenv import load_dotenv

load_dotenv("config/.env")

TOKEN = os.getenv("TAIKAI_MCP_TOKEN")
URL = "https://mcp.taikai.network/mcp"

async def list_tools_verbose():
    if not TOKEN:
        print("ERROR: TAIKAI_MCP_TOKEN no encontrado en config/.env")
        return

    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "text/event-stream",
        "User-Agent": "Mozilla/5.0 (Gemini CLI; AgroIA Hackaton)"
    }
    
    print(f"Conectando a {URL}...")
    try:
        async with sse_client(URL, headers=headers) as (read, write):
            print("Conexión SSE OK. Creando sesión...")
            async with ClientSession(read, write) as session:
                print("Inicializando handshake...")
                # El initialize() envía el JSON-RPC initialize
                init_result = await session.initialize()
                print("Handshake completado.")
                
                print("Solicitando lista de herramientas...")
                tools = await session.list_tools()
                print(f"ÉXITO: Se encontraron {len(tools.tools)} herramientas.")
                for t in tools.tools:
                    print(f"- {t.name}: {t.description}")
                return tools
    except Exception as e:
        print(f"ERROR CRÍTICO: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(list_tools_verbose())
