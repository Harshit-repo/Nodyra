"""E2E test for MS3 MCP Client flow."""
import httpx
import json

API = "http://localhost:8000"
import os
TOKEN = os.environ.get("NOODLE_TOKEN", "REPLACE_WITH_YOUR_TOKEN")
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

client = httpx.Client(timeout=10)

# 1. List MCP connections (should be empty)
r = client.get(f"{API}/api/mcp-connections", headers=HEADERS)
print(f"1. GET /api/mcp-connections: {r.status_code} — {r.json()}")

# 2. Create MCP connection to our test server
body = {
    "name": "Test MCP Server",
    "url": "http://host.docker.internal:9999",
    "transport": "streamable-http",
    "auth_type": "none",
}
r = client.post(f"{API}/api/mcp-connections", json=body, headers=HEADERS)
print(f"2. POST /api/mcp-connections: {r.status_code}")
if r.status_code == 200:
    conn = r.json()
    conn_id = conn["id"]
    print(f"   Created: id={conn_id}")
else:
    print(f"   Error: {r.text}")
    # Try to get error details
    conn_id = None

# 3. Sync tools
if conn_id:
    r = client.post(f"{API}/api/mcp-connections/{conn_id}/sync", headers=HEADERS)
    print(f"3. POST /sync: {r.status_code} — {r.json() if r.status_code == 200 else r.text}")

# 4. Get tools as node manifests
if conn_id:
    r = client.get(f"{API}/api/mcp-connections/{conn_id}/tools", headers=HEADERS)
    print(f"4. GET /tools: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"   Tools count: {len(data) if isinstance(data, list) else data}")
