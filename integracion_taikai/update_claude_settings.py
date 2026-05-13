import json
import os

path = r"C:\Users\sdari\.claude\settings.json"
new_config = {
    "mcpServers": {
        "taikai": {
            "url": "https://mcp.taikai.network/mcp"
        }
    }
}

try:
    if os.path.exists(path):
        with open(path, "r") as f:
            data = json.load(f)
    else:
        data = {}

    if "mcpServers" not in data:
        data["mcpServers"] = {}
    
    data["mcpServers"]["taikai"] = new_config["mcpServers"]["taikai"]

    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print("Configuración de Claude actualizada con éxito.")
except Exception as e:
    print(f"Error al actualizar configuración: {e}")
