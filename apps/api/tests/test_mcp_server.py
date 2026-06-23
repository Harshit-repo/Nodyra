import json
from contextlib import asynccontextmanager

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


async def test_list_runs_empty(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        json=rpc("tools/call", {"name": "list_runs", "arguments": {"limit": 5}}),
    )
    data = _tool_payload(resp)
    assert "runs" in data
    assert isinstance(data["runs"], list)


async def test_list_runs_filtered_by_workflow(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "ListRuns WF")
    # trigger a run
    await client.post(
        "/mcp",
        json=rpc("tools/call", {"name": "run_workflow", "arguments": {"workflow_id": workflow_id}}),
    )
    data = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "list_runs", "arguments": {"workflow_id": workflow_id}}),
        )
    )
    assert len(data["runs"]) >= 1
    assert all(r["workflow_id"] == workflow_id for r in data["runs"])


async def test_get_run_events(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Events WF")
    run_data = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "run_workflow", "arguments": {"workflow_id": workflow_id}}),
        )
    )
    run_id = run_data["run_id"]
    data = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "get_run_events", "arguments": {"run_id": run_id}}),
        )
    )
    assert data["run_id"] == run_id
    assert isinstance(data["events"], list)


async def test_get_run_events_missing_run(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        json=rpc("tools/call", {"name": "get_run_events", "arguments": {"run_id": "doesnotexist"}}),
    )
    assert resp.json()["result"]["isError"] is True


# ---------------------------------------------------------------------------
# Loopback: MCP client nodes against Noodle's own /mcp server (B3)
# ---------------------------------------------------------------------------


class _LoopbackSession:
    """Minimal MCP client session that speaks to the test app's /mcp route."""

    def __init__(self, client: AsyncClient) -> None:
        self._client = client
        self._seq = 0

    async def _post(self, method: str, params: dict) -> dict:
        self._seq += 1
        resp = await self._client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": self._seq, "method": method, "params": params},
        )
        data = resp.json()
        assert "error" not in data, data
        return data["result"]

    async def list_tools(self):
        from types import SimpleNamespace

        result = await self._post("tools/list", {})
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name=t["name"],
                    description=t.get("description"),
                    inputSchema=t.get("inputSchema"),
                )
                for t in result["tools"]
            ]
        )

    async def call_tool(self, name: str, arguments: dict):
        from types import SimpleNamespace

        result = await self._post(
            "tools/call", {"name": name, "arguments": arguments}
        )
        return SimpleNamespace(
            content=[SimpleNamespace(text=c["text"]) for c in result["content"]],
            structuredContent=result.get("structuredContent"),
            isError=result["isError"],
        )

    async def list_resources(self):
        from types import SimpleNamespace
        result = await self._post("resources/list", {})
        return SimpleNamespace(
            resources=[
                SimpleNamespace(
                    uri=r["uri"],
                    name=r.get("name", ""),
                    description=r.get("description", ""),
                    mimeType=r.get("mimeType", ""),
                )
                for r in result["resources"]
            ]
        )

    async def read_resource(self, uri: str):
        from types import SimpleNamespace
        result = await self._post("resources/read", {"uri": uri})
        contents = result.get("contents", [])
        return SimpleNamespace(
            contents=[
                SimpleNamespace(
                    uri=c.get("uri", ""),
                    mimeType=c.get("mimeType", ""),
                    text=c.get("text"),
                    blob=c.get("blob"),
                )
                for c in contents
            ]
        )

    async def get_prompt(self, name: str, arguments: dict | None = None):
        from types import SimpleNamespace
        result = await self._post("prompts/get", {"name": name, "arguments": arguments or {}})
        messages = result.get("messages", [])
        return SimpleNamespace(
            description=result.get("description", ""),
            messages=[
                SimpleNamespace(
                    role=m.get("role", ""),
                    content=SimpleNamespace(
                        type=m.get("content", {}).get("type", "text"),
                        text=m.get("content", {}).get("text", ""),
                    ),
                )
                for m in messages
            ],
        )


async def test_cancel_run(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Cancel WF")
    run_data = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "run_workflow", "arguments": {"workflow_id": workflow_id}}),
        )
    )
    # Run already finished (success) — cancel returns its terminal status
    data = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "cancel_run", "arguments": {"run_id": run_data["run_id"]}}),
        )
    )
    assert data["run_id"] == run_data["run_id"]
    assert data["status"] in {"success", "cancelled", "error"}


async def test_cancel_run_missing(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        json=rpc("tools/call", {"name": "cancel_run", "arguments": {"run_id": "ghost"}}),
    )
    assert resp.json()["result"]["isError"] is True


async def test_get_workflow_stats(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Stats WF")
    await client.post(
        "/mcp",
        json=rpc("tools/call", {"name": "run_workflow", "arguments": {"workflow_id": workflow_id}}),
    )
    data = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "get_workflow_stats", "arguments": {"workflow_id": workflow_id}}),
        )
    )
    assert data["workflow_id"] == workflow_id
    assert data["total_runs"] >= 1
    assert data["success_count"] >= 1
    assert data["last_run_at"] is not None


async def test_get_workflow_stats_missing_workflow(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        json=rpc("tools/call", {"name": "get_workflow_stats", "arguments": {"workflow_id": "ghost"}}),
    )
    assert resp.json()["result"]["isError"] is True


async def test_patch_node(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Patch WF")
    # patch the trigger node's params
    data = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc(
                "tools/call",
                {
                    "name": "patch_node",
                    "arguments": {
                        "workflow_id": workflow_id,
                        "node_id": "t",
                        "params": {"label": "patched"},
                    },
                },
            ),
        )
    )
    assert data["node_id"] == "t"
    assert data["params"]["label"] == "patched"


async def test_patch_node_missing_node(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "PatchMiss WF")
    resp = await client.post(
        "/mcp",
        json=rpc(
            "tools/call",
            {"name": "patch_node", "arguments": {"workflow_id": workflow_id, "node_id": "nope", "params": {}}},
        ),
    )
    assert resp.json()["result"]["isError"] is True


async def test_add_and_remove_node(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "AddRemove WF")

    add_data = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc(
                "tools/call",
                {
                    "name": "add_node",
                    "arguments": {
                        "workflow_id": workflow_id,
                        "node": {"id": "n2", "type": "manual_trigger", "params": {}},
                    },
                },
            ),
        )
    )
    assert add_data["node_id"] == "n2"
    assert add_data["node_count"] == 2

    rm_data = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc(
                "tools/call",
                {"name": "remove_node", "arguments": {"workflow_id": workflow_id, "node_id": "n2"}},
            ),
        )
    )
    assert rm_data["removed"] is True


async def test_add_node_duplicate_id(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "DupNode WF")
    resp = await client.post(
        "/mcp",
        json=rpc(
            "tools/call",
            {"name": "add_node", "arguments": {"workflow_id": workflow_id, "node": {"id": "t", "type": "manual_trigger", "params": {}}}},
        ),
    )
    assert resp.json()["result"]["isError"] is True


async def test_add_node_unknown_type(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "UnkType WF")
    resp = await client.post(
        "/mcp",
        json=rpc(
            "tools/call",
            {"name": "add_node", "arguments": {"workflow_id": workflow_id, "node": {"id": "x", "type": "no_such_type", "params": {}}}},
        ),
    )
    assert resp.json()["result"]["isError"] is True


async def test_remove_node_also_removes_edges(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "EdgeClean WF")
    # Add second node and an edge between them using add_node + add_edge
    await client.post(
        "/mcp",
        json=rpc("tools/call", {"name": "add_node", "arguments": {"workflow_id": workflow_id, "node": {"id": "n2", "type": "manual_trigger", "params": {}}}}),
    )
    await client.post(
        "/mcp",
        json=rpc("tools/call", {"name": "add_edge", "arguments": {"workflow_id": workflow_id, "edge": {"source": "t", "target": "n2"}}}),
    )
    await client.post(
        "/mcp",
        json=rpc("tools/call", {"name": "remove_node", "arguments": {"workflow_id": workflow_id, "node_id": "n2"}}),
    )
    graph_data = _tool_payload(
        await client.post("/mcp", json=rpc("tools/call", {"name": "get_workflow", "arguments": {"workflow_id": workflow_id}}))
    )
    assert all(e.get("target") != "n2" for e in graph_data["graph"]["edges"])


async def test_add_and_remove_edge(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "EdgeCRUD WF")
    await client.post(
        "/mcp",
        json=rpc("tools/call", {"name": "add_node", "arguments": {"workflow_id": workflow_id, "node": {"id": "n2", "type": "manual_trigger", "params": {}}}}),
    )

    add_data = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "add_edge", "arguments": {"workflow_id": workflow_id, "edge": {"source": "t", "target": "n2"}}}),
        )
    )
    assert add_data["edge_count"] == 1

    rm_data = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "remove_edge", "arguments": {"workflow_id": workflow_id, "source": "t", "target": "n2"}}),
        )
    )
    assert rm_data["removed_count"] == 1


async def test_add_edge_missing_source_node(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "EdgeBad WF")
    resp = await client.post(
        "/mcp",
        json=rpc("tools/call", {"name": "add_edge", "arguments": {"workflow_id": workflow_id, "edge": {"source": "ghost", "target": "t"}}}),
    )
    assert resp.json()["result"]["isError"] is True


async def test_remove_edge_not_found(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "EdgeNotFound WF")
    resp = await client.post(
        "/mcp",
        json=rpc("tools/call", {"name": "remove_edge", "arguments": {"workflow_id": workflow_id, "source": "t", "target": "ghost"}}),
    )
    assert resp.json()["result"]["isError"] is True


async def test_delete_workflow(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "ToDelete WF")
    data = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "delete_workflow", "arguments": {"workflow_id": workflow_id}}),
        )
    )
    assert data["deleted"] is True
    assert data["workflow_id"] == workflow_id
    # verify gone
    resp = await client.get(f"/workflows/{workflow_id}")
    assert resp.status_code == 404


async def test_duplicate_workflow(client: AsyncClient) -> None:
    source_id = await make_workflow(client, "Source WF")
    data = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc(
                "tools/call",
                {"name": "duplicate_workflow", "arguments": {"workflow_id": source_id, "name": "Copy WF"}},
            ),
        )
    )
    assert data["source_workflow_id"] == source_id
    assert data["name"] == "Copy WF"
    assert data["workflow_id"] != source_id

    # copy should have the same graph
    copy_data = _tool_payload(
        await client.post("/mcp", json=rpc("tools/call", {"name": "get_workflow", "arguments": {"workflow_id": data["workflow_id"]}}))
    )
    assert len(copy_data["graph"]["nodes"]) == 1


async def test_duplicate_workflow_default_name(client: AsyncClient) -> None:
    source_id = await make_workflow(client, "Original WF")
    data = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "duplicate_workflow", "arguments": {"workflow_id": source_id}}),
        )
    )
    assert "Original WF" in data["name"]
    assert "(copy)" in data["name"]


async def test_toggle_workflow(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Toggle WF")
    data = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "toggle_workflow", "arguments": {"workflow_id": workflow_id, "active": True}}),
        )
    )
    assert data["active"] is True
    data2 = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "toggle_workflow", "arguments": {"workflow_id": workflow_id, "active": False}}),
        )
    )
    assert data2["active"] is False


async def test_list_workflow_versions(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Versions WF")
    await client.post(f"/workflows/{workflow_id}/publish", json={"notes": "v2"})
    data = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "list_workflow_versions", "arguments": {"workflow_id": workflow_id}}),
        )
    )
    assert data["workflow_id"] == workflow_id
    assert len(data["versions"]) >= 2
    assert data["versions"][0]["version"] > data["versions"][-1]["version"]  # desc order


async def test_rollback_workflow(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Rollback WF")
    # publish v2 with a different graph
    await client.post(f"/workflows/{workflow_id}/publish", json={"notes": "v2"})

    data = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "rollback_workflow", "arguments": {"workflow_id": workflow_id, "version": 1}}),
        )
    )
    assert data["draft_restored_from_version"] == 1


async def test_schedule_crud(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Sched WF")

    created = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc(
                "tools/call",
                {
                    "name": "create_schedule",
                    "arguments": {
                        "workflow_id": workflow_id,
                        "name": "Hourly run",
                        "schedule_cron": "0 * * * *",
                        "schedule_tz": "UTC",
                    },
                },
            ),
        )
    )
    assert "schedule_id" in created
    schedule_id = created["schedule_id"]

    # list
    listing = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "list_schedules", "arguments": {"workflow_id": workflow_id}}),
        )
    )
    assert any(s["schedule_id"] == schedule_id for s in listing["schedules"])

    # toggle off
    toggled = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "toggle_schedule", "arguments": {"schedule_id": schedule_id, "active": False}}),
        )
    )
    assert toggled["active"] is False

    # delete
    deleted = _tool_payload(
        await client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "delete_schedule", "arguments": {"schedule_id": schedule_id}}),
        )
    )
    assert deleted["deleted"] is True


async def test_create_schedule_missing_workflow(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        json=rpc("tools/call", {"name": "create_schedule", "arguments": {"workflow_id": "ghost", "name": "Bad"}}),
    )
    assert resp.json()["result"]["isError"] is True


# ---------------------------------------------------------------------------
# Resources capability (Task 10)
# ---------------------------------------------------------------------------


async def test_resources_in_capabilities(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        json=rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}),
    )
    caps = resp.json()["result"]["capabilities"]
    assert "resources" in caps


async def test_resources_list(client: AsyncClient) -> None:
    resp = await client.post("/mcp", json=rpc("resources/list"))
    result = resp.json()["result"]
    assert "resources" in result
    uris = {r["uri"] for r in result["resources"]}
    assert "noodle://node-types" in uris


async def test_resources_list_includes_workflows(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Resource WF")
    resp = await client.post("/mcp", json=rpc("resources/list"))
    uris = {r["uri"] for r in resp.json()["result"]["resources"]}
    assert f"noodle://workflow/{workflow_id}" in uris


async def test_resources_read_node_types(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        json=rpc("resources/read", {"uri": "noodle://node-types"}),
    )
    result = resp.json()["result"]
    assert "contents" in result
    text = result["contents"][0]["text"]
    data = json.loads(text)
    assert "node_types" in data


async def test_resources_read_workflow(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "ReadRes WF")
    resp = await client.post(
        "/mcp",
        json=rpc("resources/read", {"uri": f"noodle://workflow/{workflow_id}"}),
    )
    result = resp.json()["result"]
    data = json.loads(result["contents"][0]["text"])
    assert data["id"] == workflow_id
    assert "graph" in data


async def test_resources_read_unknown_uri(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        json=rpc("resources/read", {"uri": "noodle://nonexistent"}),
    )
    assert resp.json()["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# Prompts capability (Task 11)
# ---------------------------------------------------------------------------


async def test_prompts_in_capabilities(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        json=rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}),
    )
    assert "prompts" in resp.json()["result"]["capabilities"]


async def test_prompts_list(client: AsyncClient) -> None:
    resp = await client.post("/mcp", json=rpc("prompts/list"))
    result = resp.json()["result"]
    assert "prompts" in result
    names = {p["name"] for p in result["prompts"]}
    assert {"build_workflow", "debug_run", "optimize_workflow"} <= names


async def test_prompts_get_build_workflow(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        json=rpc("prompts/get", {"name": "build_workflow", "arguments": {"description": "send a daily email"}}),
    )
    result = resp.json()["result"]
    assert "messages" in result
    assert len(result["messages"]) >= 1
    assert "send a daily email" in result["messages"][0]["content"]["text"]


async def test_prompts_get_debug_run(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        json=rpc("prompts/get", {"name": "debug_run", "arguments": {"run_id": "abc123"}}),
    )
    result = resp.json()["result"]
    assert "abc123" in result["messages"][0]["content"]["text"]


async def test_prompts_get_unknown(client: AsyncClient) -> None:
    resp = await client.post(
        "/mcp",
        json=rpc("prompts/get", {"name": "nonexistent_prompt", "arguments": {}}),
    )
    assert resp.json()["error"]["code"] == -32601


async def test_client_nodes_loopback_against_own_server(
    client: AsyncClient, monkeypatch
) -> None:
    from noodle_nodes.ai_v2 import mcp as mcp_module

    @asynccontextmanager
    async def _loopback(config):
        yield _LoopbackSession(client)

    monkeypatch.setattr(mcp_module, "_mcp_session", _loopback)

    creds = {"url": "https://loopback.invalid/mcp"}
    tools = await mcp_module.mcp_list_tools(credentials=creds)
    assert any(t["name"] == "list_workflows" for t in tools)

    workflow_id = await make_workflow(client, "Loopback WF")
    out = await mcp_module.mcp_call_tool(
        credentials=creds,
        tool_name="run_workflow",
        arguments={"workflow_id": workflow_id},
    )
    assert out["status"] == "success"


async def test_mcp_list_resources_loopback(client: AsyncClient, monkeypatch) -> None:
    from noodle_nodes.ai_v2 import mcp as mcp_module

    @asynccontextmanager
    async def _loopback(config):
        yield _LoopbackSession(client)

    monkeypatch.setattr(mcp_module, "_mcp_session", _loopback)

    creds = {"url": "https://loopback.invalid/mcp"}
    resources = await mcp_module.mcp_list_resources(credentials=creds)
    assert any(r["uri"] == "noodle://node-types" for r in resources)


async def test_mcp_read_resource_loopback(client: AsyncClient, monkeypatch) -> None:
    from noodle_nodes.ai_v2 import mcp as mcp_module

    @asynccontextmanager
    async def _loopback(config):
        yield _LoopbackSession(client)

    monkeypatch.setattr(mcp_module, "_mcp_session", _loopback)

    creds = {"url": "https://loopback.invalid/mcp"}
    data = await mcp_module.mcp_read_resource(credentials=creds, resource_uri="noodle://node-types")
    assert isinstance(data, dict)
    assert "node_types" in data


async def test_mcp_get_prompt_loopback(client: AsyncClient, monkeypatch) -> None:
    from noodle_nodes.ai_v2 import mcp as mcp_module

    @asynccontextmanager
    async def _loopback(config):
        yield _LoopbackSession(client)

    monkeypatch.setattr(mcp_module, "_mcp_session", _loopback)

    creds = {"url": "https://loopback.invalid/mcp"}
    data = await mcp_module.mcp_get_prompt(
        credentials=creds,
        prompt_name="build_workflow",
        prompt_arguments={"description": "archive old files"},
    )
    assert "messages" in data
    assert "archive old files" in data["messages"][0]["content"]


async def test_mcp_call_tool_image_result(client: AsyncClient, monkeypatch) -> None:
    """mcp_call_tool returns image dict when server responds with image content."""
    from types import SimpleNamespace
    from noodle_nodes.ai_v2 import mcp as mcp_module

    @asynccontextmanager
    async def _loopback(config):
        class _ImageSession:
            async def initialize(self): pass
            async def call_tool(self, name, arguments):
                return SimpleNamespace(
                    content=[SimpleNamespace(type="image", data="abc123==", mimeType="image/png")],
                    structuredContent=None,
                    isError=False,
                )
        yield _ImageSession()

    monkeypatch.setattr(mcp_module, "_mcp_session", _loopback)

    creds = {"url": "https://loopback.invalid/mcp"}
    result = await mcp_module.mcp_call_tool(credentials=creds, tool_name="screenshot", arguments={})
    assert result["_image"] is True
    assert result["data"] == "abc123=="
    assert result["mime_type"] == "image/png"
