import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv("config/.env")

TOKEN = os.getenv("TAIKAI_MCP_TOKEN")
URL = "https://mcp.taikai.network/mcp"

async def debug_endpoint():
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "text/event-stream"
    }
    print(f"Probando conexión cruda a {URL}...")
    async with httpx.AsyncClient() as client:
        try:
            async with client.stream("GET", URL, headers=headers, timeout=30) as r:
                print(f"Status: {r.status_code}")
                # El protocolo MCP vía SSE suele enviar un evento 'endpoint' 
                # con la URL donde se deben enviar los POST (JSON-RPC)
                async for line in r.aiter_lines():
                    print(f"RECIBIDO: {line}")
                    if "event: endpoint" in line:
                        print(">>> ¡Evento de endpoint detectado!")
                    if "data:" in line:
                        # El dato suele ser la URL del endpoint de POST
                        break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(debug_endpoint())
