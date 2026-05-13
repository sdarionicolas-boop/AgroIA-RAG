import asyncio
import os
import json
from mcp import ClientSession
from mcp.client.sse import sse_client
from dotenv import load_dotenv

load_dotenv("config/.env")

TOKEN = os.getenv("TAIKAI_MCP_TOKEN")
URL = "https://mcp.taikai.network/mcp"

async def main():
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "text/event-stream"
    }
    print(f"Iniciando conexión a {URL}...")
    try:
        async with sse_client(URL, headers=headers) as (read, write):
            print("Conexión SSE establecida. Inicializando sesión...")
            async with ClientSession(read, write) as session:
                await session.initialize()
                print("Sesión inicializada correctamente.")
                
                tools = await session.list_tools()
                print(f"Encontradas {len(tools.tools)} herramientas.")
                for t in tools.tools:
                    print(f"- {t.name}: {t.description}")
    except Exception as e:
        print(f"FALLO: {e}")

if __name__ == "__main__":
    asyncio.run(main())
