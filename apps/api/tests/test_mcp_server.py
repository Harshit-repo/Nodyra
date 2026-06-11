import re

from httpx import AsyncClient

MANUAL_GRAPH = {
    "nodes": [
        {
            "id": "t",
            "type": "manual_trigger",
            "params": {},
            "position": {"x": 0, "y": 0},
        }
    ],
    "edges": [],
}


def rpc(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}


async def make_workflow(client: AsyncClient, name: str = "Mcp WF") -> str:
    workflow_id = (await client.post("/workflows", json={"name": name})).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": MANUAL_GRAPH})
    return workflow_id


async def test_workflow_mcp_settings_roundtrip(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client)
    resp = await client.put(
        f"/workflows/{workflow_id}",
        json={
            "mcp_enabled": True,
            "mcp_tool_name": "my_tool",
            "mcp_description": "Does a thing",
            "mcp_parameters_schema": {"type": "object", "properties": {}},
        },
    )
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["mcp_enabled"] is True
    assert detail["mcp_tool_name"] == "my_tool"
    assert detail["mcp_description"] == "Does a thing"
    assert detail["mcp_parameters_schema"]["type"] == "object"


async def test_workflow_mcp_tool_name_validated(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client)
    resp = await client.put(
        f"/workflows/{workflow_id}", json={"mcp_tool_name": "bad name!"}
    )
    assert resp.status_code == 400
