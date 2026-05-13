import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv("config/.env")

TOKEN = os.getenv("TAIKAI_MCP_TOKEN")
URL = os.getenv("TAIKAI_MCP_URL")

async def test_auth_brute():
    async with httpx.AsyncClient() as client:
        variants = [
            ("Bearer", {"Authorization": f"Bearer {TOKEN}"}),
            ("Token", {"Authorization": f"Token {TOKEN}"}),
            ("Key", {"Authorization": f"Key {TOKEN}"}),
            ("x-api-key", {"x-api-key": TOKEN}),
            ("x-taikai-token", {"x-taikai-token": TOKEN}),
            ("x-mcp-token", {"x-mcp-token": TOKEN}),
        ]
        
        for name, headers in variants:
            if name != "Bearer": continue
            headers["Accept"] = "text/event-stream"
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            try:
                print(f"Probando {name} con User-Agent de Chrome...")
                async with client.stream("GET", URL, headers=headers, timeout=20) as r:
                    print(f"{name}: {r.status_code}")
                    async for line in r.aiter_lines():
                        print(f"  LINE: '{line}'")
                        if line.strip():
                            break
            except Exception as e:
                print(f"{name} Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_auth_brute())
