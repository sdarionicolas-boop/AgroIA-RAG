import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv("config/.env")

TOKEN = os.getenv("TAIKAI_MCP_TOKEN")
URL = os.getenv("TAIKAI_MCP_URL")

async def test_json():
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/json"
    }
    async with httpx.AsyncClient() as client:
        r = await client.get(URL, headers=headers)
        print(f"Status: {r.status_code}")
        print(f"Body: {r.text}")

if __name__ == "__main__":
    asyncio.run(test_json())
