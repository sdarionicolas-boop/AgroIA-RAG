import asyncio
import os
import json
from mcp import ClientSession
from mcp.client.sse import sse_client
from dotenv import load_dotenv

load_dotenv("config/.env")

TAIKAI_URL = "https://mcp.taikai.network/mcp"
# Si el token es necesario como header o query param, lo ajustaremos.
# Por ahora asumimos que se puede pasar via Authorization header si el SDK lo permite, 
# o que el usuario nos dará la URL con el token incluido si es necesario.
TOKEN = os.getenv("TAIKAI_MCP_TOKEN")

async def run_taikai_command(tool_name, arguments):
    if not TOKEN:
        print("ERROR: TAIKAI_MCP_TOKEN no encontrado en config/.env")
        return

    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "text/event-stream"
    }
    
    try:
        async with sse_client(TAIKAI_URL, headers=headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                print(f"Ejecutando herramienta: {tool_name}...")
                result = await session.call_tool(tool_name, arguments)
                print("Resultado:", result)
                return result
    except Exception as e:
        print(f"Error al conectar con TAIKAI MCP: {e}")

async def list_tools():
    if not TOKEN:
        print("ERROR: TAIKAI_MCP_TOKEN no encontrado en config/.env")
        return

    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "text/event-stream"
    }
    try:
        async with sse_client(TAIKAI_URL, headers=headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                return tools
    except Exception as e:
        print(f"Error al listar herramientas: {e}")

if __name__ == "__main__":
    # Script de prueba
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        tools = asyncio.run(list_tools())
        if tools:
            for t in tools.tools:
                print(f"- {t.name}: {t.description}")
