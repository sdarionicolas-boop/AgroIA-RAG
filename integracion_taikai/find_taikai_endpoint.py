import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv("config/.env")
TOKEN = os.getenv("TAIKAI_MCP_TOKEN")
URL = "https://mcp.taikai.network/mcp"

async def get_endpoint():
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "text/event-stream"
    }
    print(f"Buscando endpoint en {URL}...")
    try:
        # Usamos un cliente con limites muy relajados
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream("GET", URL, headers=headers) as response:
                print(f"Status: {response.status_code}")
                # Leemos los chunks manualmente para evitar errores de protocolo si es posible
                async for line in response.aiter_lines():
                    print(f"RAW LINE: {line}")
                    if line.startswith("event: endpoint"):
                        # La siguiente linea suele ser data: URL
                        pass
                    if line.startswith("data:"):
                        endpoint = line.replace("data:", "").strip()
                        print(f"!!! ENDPOINT ENCONTRADO: {endpoint}")
                        return endpoint
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    endpoint = asyncio.run(get_endpoint())
    if endpoint:
        # Guardar para uso futuro
        with open("taikai_endpoint.txt", "w") as f:
            f.write(endpoint)
