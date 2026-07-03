"""REST and MCP surfaces for workflow execution isolation."""

import json

import app.mcp.tools as mcp_tools
import app.routers.runs as runs_router

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


def rpc(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def tool_call(name: str, arguments: dict) -> dict:
    return rpc("tools/call", {"name": name, "arguments": arguments})


def tool_payload(resp) -> dict:
    result = resp.json()["result"]
    assert result["isError"] is False, result["content"][0]["text"]
    return json.loads(result["content"][0]["text"])


async def test_workflow_settings_accepts_execution_mode(client):
    wf = (await client.post("/workflows", json={"name": "REST mode"})).json()
    resp = await client.patch(
        f"/workflows/{wf['id']}", json={"execution_mode": "sandboxed"}
    )
    assert resp.status_code == 200
    assert resp.json()["execution_mode"] == "sandboxed"


async def test_workflow_settings_rejects_bad_mode(client):
    wf = (await client.post("/workflows", json={"name": "Bad mode"})).json()
    resp = await client.patch(f"/workflows/{wf['id']}", json={"execution_mode": "yolo"})
    assert resp.status_code == 422


async def test_workflow_settings_accepts_sandbox_resources(client):
    wf = (await client.post("/workflows", json={"name": "Resources"})).json()
    resp = await client.patch(
        f"/workflows/{wf['id']}",
        json={"sandbox_resources": {"memory_mb": 2048}},
    )
    assert resp.status_code == 200
    assert resp.json()["sandbox_resources"] == {"memory_mb": 2048}


async def test_run_workflow_sandbox_flag_stamps_run(client, monkeypatch):
    captured = {}

    async def fake_start_run(*args, **kwargs):
        captured.update(kwargs)
        return "run-1"

    monkeypatch.setattr(runs_router, "start_run", fake_start_run)
    wf = (await client.post("/workflows", json={"name": "Run sandbox"})).json()
    resp = await client.post(f"/workflows/{wf['id']}/run", json={"sandbox": True})
    assert resp.status_code == 202
    assert captured["execution_mode"] == "sandboxed"


async def test_mcp_create_workflow_sets_mode_and_resources(client):
    created = tool_payload(
        await client.post(
            "/mcp",
            json=tool_call(
                "create_workflow",
                {
                    "name": "MCP mode",
                    "execution_mode": "sandboxed",
                    "sandbox_resources": {"memory_mb": 1024},
                },
            ),
        )
    )
    detail = (await client.get(f"/workflows/{created['workflow_id']}")).json()
    assert detail["execution_mode"] == "sandboxed"
    assert detail["sandbox_resources"] == {"memory_mb": 1024}


async def test_mcp_update_workflow_settings_sets_mode(client):
    wf = (await client.post("/workflows", json={"name": "MCP update"})).json()
    result = tool_payload(
        await client.post(
            "/mcp",
            json=tool_call(
                "update_workflow_settings",
                {"workflow_id": wf["id"], "execution_mode": "sandboxed"},
            ),
        )
    )
    assert result["execution_mode"] == "sandboxed"


async def test_mcp_run_workflow_sandbox_flag_stamps_run(client, monkeypatch):
    captured = {}

    async def fake_start_run(*args, **kwargs):
        captured.update(kwargs)
        return "run-mcp"

    monkeypatch.setattr(mcp_tools, "start_run", fake_start_run)
    wf = (await client.post("/workflows", json={"name": "MCP run"})).json()
    result = tool_payload(
        await client.post(
            "/mcp",
            json=tool_call(
                "run_workflow",
                {
                    "workflow_id": wf["id"],
                    "sandbox": True,
                    "wait_seconds": 0,
                },
            ),
        )
    )
    assert result["status"] == "running"
    assert captured["execution_mode"] == "sandboxed"
