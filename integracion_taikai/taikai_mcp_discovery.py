import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client

async def main():
    async with sse_client("https://mcp.taikai.network/mcp") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            # List tools
            tools = await session.list_tools()
            print("\n--- TAIKAI MCP TOOLS ---")
            for tool in tools.tools:
                print(f"- {tool.name}: {tool.description}")
                print(f"  Schema: {tool.inputSchema}")
                print("-" * 30)

if __name__ == "__main__":
    asyncio.run(main())
