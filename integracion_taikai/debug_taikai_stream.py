import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv("config/.env")

TOKEN = os.getenv("TAIKAI_MCP_TOKEN")
URL = os.getenv("TAIKAI_MCP_URL")

async def debug_stream():
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "text/event-stream"
    }
    async with httpx.AsyncClient() as client:
        try:
            async with client.stream("GET", URL, headers=headers) as r:
                print(f"Status: {r.status_code}")
                print(f"Headers: {r.headers}")
                async for line in r.aiter_lines():
                    print(f"LINE: {line}")
                    if line.startswith("event: endpoint"):
                        print("¡Encontrado evento de endpoint!")
                    # Solo leemos unas pocas líneas para no quedarnos bloqueados
                    if "data" in line:
                        break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(debug_stream())
