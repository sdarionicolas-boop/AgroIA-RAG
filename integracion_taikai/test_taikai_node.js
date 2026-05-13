const EventSource = require('eventsource');
const { Client } = require('@modelcontextprotocol/sdk/client/index.js');
const { SSEClientTransport } = require('@modelcontextprotocol/sdk/client/sse.js');

async function main() {
    const transport = new SSEClientTransport(new URL('https://mcp.taikai.network/mcp'), {
        eventSourceInitDict: {
            headers: {
                'Authorization': 'Bearer YOUR_TAIKAI_TOKEN'
            }
        }
    });

    const client = new Client({
        name: 'agroia-agent',
        version: '1.0.0'
    }, {
        capabilities: {}
    });

    console.log('Conectando al MCP de TAIKAI...');
    try {
        await client.connect(transport);
        console.log('Conexión establecida.');

        const tools = await client.listTools();
        console.log('Herramientas disponibles:');
        console.log(JSON.stringify(tools, null, 2));

        await client.close();
    } catch (error) {
        console.error('Error:', error);
    }
}

main();
