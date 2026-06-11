import json
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


# ---------------------------------------------------------------------------
# Protocol + end-to-end MCP server tests
# ---------------------------------------------------------------------------


async def test_initialize(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        json=rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        ),
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"]["name"] == "noodle"
    assert "tools" in result["capabilities"]


async def test_notification_returns_202(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert resp.status_code == 202


async def test_get_is_405(client: AsyncClient) -> None:
    assert (await client.get("/mcp")).status_code == 405


async def test_batch_rejected(client: AsyncClient) -> None:
    resp = await client.post("/mcp", json=[rpc("ping")])
    assert resp.json()["error"]["code"] == -32600


async def test_tools_list_contains_static_tools(client: AsyncClient) -> None:
    resp = await client.post("/mcp", json=rpc("tools/list"))
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert {"run_workflow", "set_workflow_graph", "list_node_types"} <= names


def _tool_payload(resp) -> dict:
    """Parse a tools/call response's text content as JSON."""
    result = resp.json()["result"]
    assert result["isError"] is False, result["content"][0]["text"]
    return json.loads(result["content"][0]["text"])


async def test_build_and_run_workflow_via_mcp(client: AsyncClient) -> None:
    created = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "create_workflow", "arguments": {"name": "Via MCP"}}),
        )
    )
    workflow_id = created["workflow_id"]

    set_resp = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc(
                "tools/call",
                {
                    "name": "set_workflow_graph",
                    "arguments": {"workflow_id": workflow_id, "graph": MANUAL_GRAPH},
                },
            ),
        )
    )
    assert set_resp["node_count"] == 1

    run = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc(
                "tools/call",
                {"name": "run_workflow", "arguments": {"workflow_id": workflow_id}},
            ),
        )
    )
    assert run["status"] == "success"
    assert "run_id" in run


async def test_set_graph_rejects_unknown_node_type(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Bad Graph WF")
    resp = await client.post(
        "/mcp",
        json=rpc(
            "tools/call",
            {
                "name": "set_workflow_graph",
                "arguments": {
                    "workflow_id": workflow_id,
                    "graph": {
                        "nodes": [{"id": "x", "type": "no_such_node", "params": {}}],
                        "edges": [],
                    },
                },
            },
        ),
    )
    result = resp.json()["result"]
    assert result["isError"] is True
    assert "Unknown node types" in result["content"][0]["text"]


async def test_workflow_exposed_as_dynamic_tool(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Dyn Tool WF")
    await client.post(f"/workflows/{workflow_id}/publish", json={})
    await client.put(
        f"/workflows/{workflow_id}",
        json={"mcp_enabled": True, "mcp_tool_name": "dyn_tool_wf"},
    )

    listing = await client.post("/mcp", json=rpc("tools/list"))
    names = {t["name"] for t in listing.json()["result"]["tools"]}
    assert "dyn_tool_wf" in names

    run = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "dyn_tool_wf", "arguments": {}}),
        )
    )
    assert run["status"] == "success"


async def test_unknown_tool_is_method_not_found(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp", json=rpc("tools/call", {"name": "nope_tool", "arguments": {}})
    )
    assert resp.json()["error"]["code"] == -32601
