import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv("config/.env")

TOKEN = os.getenv("TAIKAI_MCP_TOKEN")
URL = os.getenv("TAIKAI_MCP_URL")

async def test_auth():
    print(f"Probando autenticación con token: {TOKEN[:4]}...{TOKEN[-4:]}")
    
    async with httpx.AsyncClient() as client:
        # Intento 1: Query Param
        try:
            r1 = await client.get(f"{URL}?token={TOKEN}")
            print(f"Intento Query Param: Status {r1.status_code}")
        except Exception as e:
            print(f"Intento Query Param error: {e}")
        
        # Intento 2: Bearer Header + Accept
        headers = {
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "text/event-stream"
        }
        try:
            async with client.stream("GET", URL, headers=headers) as r2:
                print(f"Intento Bearer Header + Accept: Status {r2.status_code}")
        except Exception as e:
            print(f"Intento Bearer Header + Accept error: {e}")

        # Intento 3: x-api-key
        headers = {
            "x-api-key": TOKEN,
            "Accept": "text/event-stream"
        }
        try:
            async with client.stream("GET", URL, headers=headers) as r3:
                print(f"Intento x-api-key: Status {r3.status_code}")
        except Exception as e:
            print(f"Intento x-api-key error: {e}")

if __name__ == "__main__":
    asyncio.run(test_auth())
