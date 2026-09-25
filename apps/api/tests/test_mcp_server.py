import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from tests.mcp_approval_helpers import mcp_post as _mcp_post

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

APPROVED = {"approved_by_user": True}


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
    resp = await _mcp_post(client,
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
    assert result["serverInfo"]["name"] == "nodyra"
    assert "get_workflow_authoring_guide" in result["instructions"]
    assert "tools" in result["capabilities"]
    assert result["capabilities"]["tools"]["listChanged"] is False
    assert "resources" in result["capabilities"]
    assert "prompts" in result["capabilities"]


async def test_initialize_latest_protocol(client: AsyncClient) -> None:
    response = await _mcp_post(client,
        json=rpc(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        ),
    )
    assert response.json()["result"]["protocolVersion"] == "2025-11-25"


async def test_invalid_origin_is_rejected(client: AsyncClient) -> None:
    response = await _mcp_post(client, headers={"Origin": "https://evil.example"}, json=rpc("ping")
    )
    assert response.status_code == 403


async def test_unsupported_protocol_header_is_rejected(client: AsyncClient) -> None:
    response = await _mcp_post(client,
        headers={"MCP-Protocol-Version": "2099-01-01"},
        json=rpc("ping"),
    )
    assert response.status_code == 400


async def test_protected_resource_metadata(client: AsyncClient) -> None:
    response = await client.get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code == 200
    assert response.json()["resource"].endswith("/mcp")
    assert "workflow:run" in response.json()["scopes_supported"]


async def test_notification_returns_202(client: AsyncClient) -> None:
    resp = await _mcp_post(client, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert resp.status_code == 202


async def test_get_is_405(client: AsyncClient) -> None:
    assert (await client.get("/mcp")).status_code == 405


async def test_batch_requests_are_rejected(client: AsyncClient) -> None:
    """Streamable HTTP accepts exactly one MCP message per POST."""
    resp = await _mcp_post(client,
        json=[
            rpc("ping", req_id=1),
            rpc("ping", req_id=2),
        ],
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == -32600


async def test_batch_with_notification_is_rejected(client: AsyncClient) -> None:
    resp = await _mcp_post(client,
        json=[
            rpc("ping", req_id=1),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ],
    )
    assert resp.status_code == 400


async def test_batch_all_notifications_is_rejected(client: AsyncClient) -> None:
    resp = await _mcp_post(client,
        json=[{"jsonrpc": "2.0", "method": "notifications/initialized"}],
    )
    assert resp.status_code == 400


async def test_tools_list_contains_static_tools(client: AsyncClient) -> None:
    resp = await _mcp_post(client, json=rpc("tools/list"))
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert {
        "run_workflow",
        "set_workflow_graph",
        "list_node_types",
        "get_workflow_authoring_guide",
        "get_node_contracts",
    } <= names
    descriptor = next(
        item for item in resp.json()["result"]["tools"] if item["name"] == "run_workflow"
    )
    assert descriptor["outputSchema"]["type"] == "object"
    assert descriptor["annotations"]["openWorldHint"] is True
    assert descriptor["execution"]["taskSupport"] == "forbidden"


async def test_mcp_tools_pagination_pages_dynamic_workflow_tools_without_full_materialization(
    client: AsyncClient,
    monkeypatch,
) -> None:
    import app.routers.mcp as mcp_router

    calls: list[tuple[int, int, datetime | None, str | None]] = []
    anchor_time = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)

    async def fake_page(
        _session,
        *,
        limit: int,
        offset: int = 0,
        after_updated_at: datetime | None = None,
        after_id: str | None = None,
    ):
        calls.append((offset, limit, after_updated_at, after_id))
        tools = [
            {
                "name": f"workflow_tool_{offset + i}",
                "description": "dynamic",
                "inputSchema": {"type": "object", "additionalProperties": True},
            }
            for i in range(limit)
        ]
        return tools, (anchor_time, "anchor-workflow"), True

    monkeypatch.setattr(mcp_router, "list_workflow_tool_descriptor_page", fake_page)
    static_count = len(mcp_router.STATIC_TOOLS)
    cursor = mcp_router._cursor(static_count + 250)

    resp = await _mcp_post(client, json=rpc("tools/list", {"cursor": cursor}))
    body = resp.json()["result"]

    assert calls == [(250, mcp_router.TOOL_PAGE_SIZE, None, None)]
    assert len(body["tools"]) == mcp_router.TOOL_PAGE_SIZE
    assert body["tools"][0]["name"] == "workflow_tool_250"
    assert "nextCursor" in body

    await _mcp_post(client, json=rpc("tools/list", {"cursor": body["nextCursor"]})
    )
    assert calls[-1] == (
        0,
        mcp_router.TOOL_PAGE_SIZE,
        anchor_time,
        "anchor-workflow",
    )


async def test_mcp_rejects_oversized_or_excessive_cursors(client: AsyncClient) -> None:
    import app.routers.mcp as mcp_router

    excessive = mcp_router._cursor(mcp_router.MAX_CURSOR_OFFSET + 1)
    for cursor in ("x" * (mcp_router.MAX_CURSOR_LENGTH + 1), excessive):
        response = await _mcp_post(client, json=rpc("tools/list", {"cursor": cursor})
        )
        assert response.json()["error"]["code"] == -32602
        assert response.json()["error"]["message"] == "Invalid pagination cursor"


async def test_dynamic_workflow_keyset_is_stable_across_mutations(
    client: AsyncClient,
) -> None:
    from sqlalchemy import delete, update

    import app.mcp.tools as mcp_tools
    from app.models import Workflow

    workflow_ids: list[str] = []
    for index in range(4):
        workflow_id = await make_workflow(client, f"Keyset {index}")
        workflow_ids.append(workflow_id)
        response = await client.put(
            f"/workflows/{workflow_id}",
            json={
                "mcp_enabled": True,
                "mcp_tool_name": f"keyset_{workflow_id}",
            },
        )
        assert response.status_code == 200

    tied_timestamp = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    async with mcp_tools.SessionLocal() as session:
        await session.execute(
            update(Workflow)
            .where(Workflow.id.in_(workflow_ids))
            .values(updated_at=tied_timestamp)
        )
        await session.commit()
        first_tools, anchor, has_more = (
            await mcp_tools.list_workflow_tool_descriptor_page(session, limit=2)
        )

    ordered_ids = sorted(workflow_ids)
    assert [tool["name"] for tool in first_tools] == [
        f"keyset_{workflow_id}" for workflow_id in ordered_ids[:2]
    ]
    assert anchor is not None
    assert has_more is True

    # Delete a row before the anchor and add a newer row. Offset pagination
    # would now shift; the keyset must continue with the two untouched rows.
    async with mcp_tools.SessionLocal() as session:
        await session.execute(delete(Workflow).where(Workflow.id == ordered_ids[0]))
        await session.commit()
    inserted_id = await make_workflow(client, "Inserted while paging")
    response = await client.put(
        f"/workflows/{inserted_id}",
        json={"mcp_enabled": True, "mcp_tool_name": f"keyset_{inserted_id}"},
    )
    assert response.status_code == 200

    async with mcp_tools.SessionLocal() as session:
        second_tools, _, second_has_more = (
            await mcp_tools.list_workflow_tool_descriptor_page(
                session,
                limit=2,
                after_updated_at=anchor[0],
                after_id=anchor[1],
            )
        )

    assert [tool["name"] for tool in second_tools] == [
        f"keyset_{workflow_id}" for workflow_id in ordered_ids[2:]
    ]
    assert f"keyset_{inserted_id}" not in {tool["name"] for tool in second_tools}
    assert second_has_more is False


async def test_workflow_authoring_guide_tool(client: AsyncClient) -> None:
    data = _tool_payload(
        await _mcp_post(client,
            json=rpc(
                "tools/call",
                {
                    "name": "get_workflow_authoring_guide",
                    "arguments": {
                        "goal": "create an inventory API",
                        "detail": "full",
                    },
                },
            ),
        )
    )
    assert data["goal"] == "create an inventory API"
    assert "graph_contract" in data
    assert "api_endpoint" in data["trigger_recipes"]
    assert any("schedule" in item["name"] for item in data["important_tools"])


async def test_get_node_contracts_returns_llm_guidance(client: AsyncClient) -> None:
    data = _tool_payload(
        await _mcp_post(client,
            json=rpc(
                "tools/call",
                {
                    "name": "get_node_contracts",
                    "arguments": {"node_types": ["api_endpoint", "code"]},
                },
            ),
        )
    )
    contracts = {contract["id"]: contract for contract in data["contracts"]}
    assert {"api_endpoint", "code"} <= set(contracts)
    assert "outputs_override" in json.dumps(
        contracts["api_endpoint"]["llm_guidance"], sort_keys=True
    )
    assert contracts["code"]["graph_node_shape"]["type"] == "code"


async def test_get_node_type_includes_llm_guidance(client: AsyncClient) -> None:
    data = _tool_payload(
        await _mcp_post(client,
            json=rpc(
                "tools/call",
                {"name": "get_node_type", "arguments": {"node_type": "schedule_trigger"}},
            ),
        )
    )
    assert "llm_guidance" in data
    assert "production_notes" in data["llm_guidance"]
    assert data["graph_node_shape"]["type"] == "schedule_trigger"


async def test_tool_arguments_are_validated(client: AsyncClient) -> None:
    response = await _mcp_post(client,
        json=rpc("tools/call", {"name": "create_workflow", "arguments": {}}),
    )
    result = response.json()["result"]
    assert result["isError"] is True
    assert "name" in result["content"][0]["text"]


async def test_validate_graph_rejects_cycles(client: AsyncClient) -> None:
    graph = {
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "params": {}},
            {"id": "code", "type": "code", "params": {"code": "output = input"}},
        ],
        "edges": [
            {"source": "trigger", "target": "code"},
            {"source": "code", "target": "trigger"},
        ],
    }
    response = await _mcp_post(client,
        json=rpc("tools/call", {"name": "validate_graph", "arguments": {"graph": graph}}),
    )
    result = response.json()["result"]
    assert result["isError"] is True
    assert "cycle" in result["content"][0]["text"].lower()


async def test_validate_graph_accepts_outputs_override_ports(client: AsyncClient) -> None:
    graph = {
        "nodes": [
            {
                "id": "api",
                "type": "api_endpoint",
                "params": {
                    "base_path": "items",
                    "routes": [{"method": "GET", "path": "/{sku}", "output": "lookup"}],
                },
                "outputs_override": ["lookup"],
            },
            {"id": "code", "type": "code", "params": {"code": "output = input"}},
        ],
        "edges": [
            {
                "source": "api",
                "source_output": "lookup",
                "target": "code",
                "target_input": "input",
            }
        ],
    }
    response = await _mcp_post(client,
        json=rpc("tools/call", {"name": "validate_graph", "arguments": {"graph": graph}}),
    )
    result = response.json()["result"]
    assert result["isError"] is False, result["content"][0]["text"]


def _tool_payload(resp) -> dict:
    """Parse a tools/call response's text content as JSON."""
    result = resp.json()["result"]
    assert result["isError"] is False, result["content"][0]["text"]
    return json.loads(result["content"][0]["text"])


async def test_build_and_run_workflow_via_mcp(client: AsyncClient) -> None:
    created = _tool_payload(
        await _mcp_post(client,
            json=rpc("tools/call", {"name": "create_workflow", "arguments": {"name": "Via MCP"}}),
        )
    )
    workflow_id = created["workflow_id"]

    set_resp = _tool_payload(
        await _mcp_post(client,
            json=rpc(
                "tools/call",
                {
                    "name": "set_workflow_graph",
                    "arguments": {
                        "workflow_id": workflow_id,
                        "graph": MANUAL_GRAPH,
                        **APPROVED,
                    },
                },
            ),
        )
    )
    assert set_resp["node_count"] == 1

    run = _tool_payload(
        await _mcp_post(client,
            json=rpc(
                "tools/call",
                {"name": "run_workflow", "arguments": {"workflow_id": workflow_id}},
            ),
        )
    )
    assert run["status"] == "success"
    assert "run_id" in run


async def test_destructive_mcp_tool_requires_explicit_approval(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Approval WF")
    resp = await _mcp_post(client, approve=False,
        json=rpc(
            "tools/call",
            {
                "name": "set_workflow_graph",
                "arguments": {"workflow_id": workflow_id, "graph": MANUAL_GRAPH},
            },
        ),
    )
    result = resp.json()["result"]
    assert result["isError"] is True
    assert "human_approval_required" in result["content"][0]["text"]


async def test_set_graph_rejects_unknown_node_type(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Bad Graph WF")
    resp = await _mcp_post(client,
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
                    **APPROVED,
                },
            },
        ),
    )
    result = resp.json()["result"]
    assert result["isError"] is True
    assert "Unknown node types" in result["content"][0]["text"]


async def test_set_graph_accepts_switch_rule_output_ports(client: AsyncClient) -> None:
    # switch declares only `fallback` statically; branch ports come from the
    # rules param and must validate without an outputs_override.
    workflow_id = await make_workflow(client, "Switch Graph WF")
    graph = {
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {"data": {"kind": "vip"}}},
            {"id": "s", "type": "switch", "params": {"field": "kind", "rules": {"vip": "vip", "free": "free"}}},
            {"id": "v", "type": "code", "params": {"code": "output = {'got': input}"}},
        ],
        "edges": [
            {"source": "t", "source_output": "main", "target": "s", "target_input": "input"},
            {"source": "s", "source_output": "vip", "target": "v", "target_input": "input"},
        ],
    }
    resp = await _mcp_post(client,
        json=rpc(
            "tools/call",
            {
                "name": "set_workflow_graph",
                "arguments": {"workflow_id": workflow_id, "graph": graph, **APPROVED},
            },
        ),
    )
    result = resp.json()["result"]
    assert result["isError"] is False, result["content"][0]["text"]


async def test_workflow_exposed_as_dynamic_tool(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Dyn Tool WF")
    await client.post(f"/workflows/{workflow_id}/publish", json={})
    await client.put(
        f"/workflows/{workflow_id}",
        json={"mcp_enabled": True, "mcp_tool_name": "dyn_tool_wf"},
    )

    listing = await _mcp_post(client, json=rpc("tools/list"))
    names = {t["name"] for t in listing.json()["result"]["tools"]}
    assert "dyn_tool_wf" in names

    run = _tool_payload(
        await _mcp_post(client,
            json=rpc("tools/call", {"name": "dyn_tool_wf", "arguments": {}}),
        )
    )
    assert run["status"] == "success"


async def test_unknown_tool_is_method_not_found(client: AsyncClient) -> None:
    resp = await _mcp_post(client, json=rpc("tools/call", {"name": "nope_tool", "arguments": {}})
    )
    assert resp.json()["error"]["code"] == -32601


async def test_list_runs_empty(client: AsyncClient) -> None:
    resp = await _mcp_post(client,
        json=rpc("tools/call", {"name": "list_runs", "arguments": {"limit": 5}}),
    )
    data = _tool_payload(resp)
    assert "runs" in data
    assert isinstance(data["runs"], list)


async def test_list_runs_filtered_by_workflow(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "ListRuns WF")
    # trigger a run
    await _mcp_post(client,
        json=rpc("tools/call", {"name": "run_workflow", "arguments": {"workflow_id": workflow_id}}),
    )
    data = _tool_payload(
        await _mcp_post(client,
            json=rpc("tools/call", {"name": "list_runs", "arguments": {"workflow_id": workflow_id}}),
        )
    )
    assert len(data["runs"]) >= 1
    assert all(r["workflow_id"] == workflow_id for r in data["runs"])


async def test_get_run_events(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Events WF")
    run_data = _tool_payload(
        await _mcp_post(client,
            json=rpc("tools/call", {"name": "run_workflow", "arguments": {"workflow_id": workflow_id}}),
        )
    )
    run_id = run_data["run_id"]
    data = _tool_payload(
        await _mcp_post(client,
            json=rpc("tools/call", {"name": "get_run_events", "arguments": {"run_id": run_id}}),
        )
    )
    assert data["run_id"] == run_id
    assert isinstance(data["events"], list)


async def test_get_run_events_missing_run(client: AsyncClient) -> None:
    resp = await _mcp_post(client,
        json=rpc("tools/call", {"name": "get_run_events", "arguments": {"run_id": "doesnotexist"}}),
    )
    assert resp.json()["result"]["isError"] is True


# ---------------------------------------------------------------------------
# Loopback: MCP client nodes against Nodyra's own /mcp server (B3)
# ---------------------------------------------------------------------------


class _LoopbackSession:
    """Minimal MCP client session that speaks to the test app's /mcp route."""

    def __init__(self, client: AsyncClient) -> None:
        self._client = client
        self._seq = 0

    async def _post(self, method: str, params: dict) -> dict:
        self._seq += 1
        resp = await _mcp_post(self._client,
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
        await _mcp_post(client,
            json=rpc("tools/call", {"name": "run_workflow", "arguments": {"workflow_id": workflow_id}}),
        )
    )
    # Run already finished (success) — cancel returns its terminal status
    data = _tool_payload(
        await _mcp_post(client,
            json=rpc("tools/call", {"name": "cancel_run", "arguments": {"run_id": run_data["run_id"]}}),
        )
    )
    assert data["run_id"] == run_data["run_id"]
    assert data["status"] in {"success", "cancelled", "error"}


async def test_cancel_run_missing(client: AsyncClient) -> None:
    resp = await _mcp_post(client,
        json=rpc("tools/call", {"name": "cancel_run", "arguments": {"run_id": "ghost"}}),
    )
    assert resp.json()["result"]["isError"] is True


async def test_get_workflow_stats(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Stats WF")
    await _mcp_post(client,
        json=rpc("tools/call", {"name": "run_workflow", "arguments": {"workflow_id": workflow_id}}),
    )
    data = _tool_payload(
        await _mcp_post(client,
            json=rpc("tools/call", {"name": "get_workflow_stats", "arguments": {"workflow_id": workflow_id}}),
        )
    )
    assert data["workflow_id"] == workflow_id
    assert data["total_runs"] >= 1
    assert data["success_count"] >= 1
    assert data["last_run_at"] is not None


async def test_get_workflow_stats_missing_workflow(client: AsyncClient) -> None:
    resp = await _mcp_post(client,
        json=rpc("tools/call", {"name": "get_workflow_stats", "arguments": {"workflow_id": "ghost"}}),
    )
    assert resp.json()["result"]["isError"] is True


async def test_patch_node(client: AsyncClient) -> None:
    from app.services import events

    workflow_id = await make_workflow(client, "Patch WF")
    # patch the trigger node's params
    data = _tool_payload(
        await _mcp_post(client,
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
    assert data["graph_revision"] == 2
    workflow_events = events.workflow_broker._events[workflow_id]
    assert workflow_events[-1]["type"] == "workflow_graph_changed"
    assert workflow_events[-1]["origin"] == "mcp"
    assert workflow_events[-1]["operation"] == "patch_node"
    assert workflow_events[-1]["graph_revision"] == 2
    assert workflow_events[-1]["patch"] == {
        "type": "node_updated",
        "node_id": "t",
        "param_keys": ["label"],
    }

    revisions = _tool_payload(
        await _mcp_post(client,
            json=rpc(
                "tools/call",
                {
                    "name": "list_workflow_revisions",
                    "arguments": {"workflow_id": workflow_id},
                },
            ),
        )
    )
    assert revisions["current_graph_revision"] == 2
    assert revisions["revisions"][0]["operation"] == "patch_node"
    assert revisions["revisions"][0]["patch"]["param_keys"] == ["label"]


async def test_patch_node_rejects_stale_graph_revision(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Stale Patch WF")
    resp = await _mcp_post(client,
        json=rpc(
            "tools/call",
            {
                "name": "patch_node",
                "arguments": {
                    "workflow_id": workflow_id,
                    "node_id": "t",
                    "params": {"label": "stale"},
                    "expected_graph_revision": 0,
                },
            },
        ),
    )
    result = resp.json()["result"]
    assert result["isError"] is True
    assert "expected graph_revision 0" in result["content"][0]["text"]

    detail = (await client.get(f"/workflows/{workflow_id}")).json()
    assert detail["graph_revision"] == 1
    assert detail["graph"]["nodes"][0]["params"] == {}


async def test_patch_node_missing_node(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "PatchMiss WF")
    resp = await _mcp_post(client,
        json=rpc(
            "tools/call",
            {"name": "patch_node", "arguments": {"workflow_id": workflow_id, "node_id": "nope", "params": {}}},
        ),
    )
    assert resp.json()["result"]["isError"] is True


async def test_add_and_remove_node(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "AddRemove WF")

    add_data = _tool_payload(
        await _mcp_post(client,
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
        await _mcp_post(client,
            json=rpc(
                "tools/call",
                {
                    "name": "remove_node",
                    "arguments": {"workflow_id": workflow_id, "node_id": "n2", **APPROVED},
                },
            ),
        )
    )
    assert rm_data["removed"] is True


async def test_add_node_duplicate_id(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "DupNode WF")
    resp = await _mcp_post(client,
        json=rpc(
            "tools/call",
            {"name": "add_node", "arguments": {"workflow_id": workflow_id, "node": {"id": "t", "type": "manual_trigger", "params": {}}}},
        ),
    )
    assert resp.json()["result"]["isError"] is True


async def test_add_node_unknown_type(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "UnkType WF")
    resp = await _mcp_post(client,
        json=rpc(
            "tools/call",
            {"name": "add_node", "arguments": {"workflow_id": workflow_id, "node": {"id": "x", "type": "no_such_type", "params": {}}}},
        ),
    )
    assert resp.json()["result"]["isError"] is True


async def test_remove_node_also_removes_edges(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "EdgeClean WF")
    # Add second node and an edge between them using add_node + add_edge
    await _mcp_post(client,
        json=rpc("tools/call", {"name": "add_node", "arguments": {"workflow_id": workflow_id, "node": {"id": "n2", "type": "manual_trigger", "params": {}}}}),
    )
    await _mcp_post(client,
        json=rpc("tools/call", {"name": "add_edge", "arguments": {"workflow_id": workflow_id, "edge": {"source": "t", "target": "n2"}}}),
    )
    await _mcp_post(client,
        json=rpc(
            "tools/call",
            {
                "name": "remove_node",
                "arguments": {"workflow_id": workflow_id, "node_id": "n2", **APPROVED},
            },
        ),
    )
    graph_data = _tool_payload(
        await _mcp_post(client, json=rpc("tools/call", {"name": "get_workflow", "arguments": {"workflow_id": workflow_id}}))
    )
    assert all(e.get("target") != "n2" for e in graph_data["graph"]["edges"])


async def test_add_and_remove_edge(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "EdgeCRUD WF")
    await _mcp_post(client,
        json=rpc("tools/call", {"name": "add_node", "arguments": {"workflow_id": workflow_id, "node": {"id": "n2", "type": "manual_trigger", "params": {}}}}),
    )

    add_data = _tool_payload(
        await _mcp_post(client,
            json=rpc("tools/call", {"name": "add_edge", "arguments": {"workflow_id": workflow_id, "edge": {"source": "t", "target": "n2"}}}),
        )
    )
    assert add_data["edge_count"] == 1

    rm_data = _tool_payload(
        await _mcp_post(client,
            json=rpc(
                "tools/call",
                {
                    "name": "remove_edge",
                    "arguments": {
                        "workflow_id": workflow_id,
                        "source": "t",
                        "target": "n2",
                        **APPROVED,
                    },
                },
            ),
        )
    )
    assert rm_data["removed_count"] == 1


async def test_add_edge_missing_source_node(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "EdgeBad WF")
    resp = await _mcp_post(client,
        json=rpc("tools/call", {"name": "add_edge", "arguments": {"workflow_id": workflow_id, "edge": {"source": "ghost", "target": "t"}}}),
    )
    assert resp.json()["result"]["isError"] is True


async def test_remove_edge_not_found(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "EdgeNotFound WF")
    resp = await _mcp_post(client,
        json=rpc(
            "tools/call",
            {
                "name": "remove_edge",
                "arguments": {
                    "workflow_id": workflow_id,
                    "source": "t",
                    "target": "ghost",
                    **APPROVED,
                },
            },
        ),
    )
    assert resp.json()["result"]["isError"] is True


async def test_delete_workflow(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "ToDelete WF")
    data = _tool_payload(
        await _mcp_post(client,
            json=rpc(
                "tools/call",
                {
                    "name": "delete_workflow",
                    "arguments": {"workflow_id": workflow_id, **APPROVED},
                },
            ),
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
        await _mcp_post(client,
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
        await _mcp_post(client, json=rpc("tools/call", {"name": "get_workflow", "arguments": {"workflow_id": data["workflow_id"]}}))
    )
    assert len(copy_data["graph"]["nodes"]) == 1


async def test_duplicate_workflow_default_name(client: AsyncClient) -> None:
    source_id = await make_workflow(client, "Original WF")
    data = _tool_payload(
        await _mcp_post(client,
            json=rpc("tools/call", {"name": "duplicate_workflow", "arguments": {"workflow_id": source_id}}),
        )
    )
    assert "Original WF" in data["name"]
    assert "(copy)" in data["name"]


async def test_toggle_workflow(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Toggle WF")
    data = _tool_payload(
        await _mcp_post(client,
            json=rpc("tools/call", {"name": "toggle_workflow", "arguments": {"workflow_id": workflow_id, "active": True, "approved_by_user": True}}),
        )
    )
    assert data["active"] is True
    data2 = _tool_payload(
        await _mcp_post(client,
            json=rpc("tools/call", {"name": "toggle_workflow", "arguments": {"workflow_id": workflow_id, "active": False, "approved_by_user": True}}),
        )
    )
    assert data2["active"] is False


async def test_list_workflow_versions(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Versions WF")
    await client.post(f"/workflows/{workflow_id}/publish", json={"notes": "v2"})
    data = _tool_payload(
        await _mcp_post(client,
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
        await _mcp_post(client,
            json=rpc(
                "tools/call",
                {
                    "name": "rollback_workflow",
                    "arguments": {"workflow_id": workflow_id, "version": 1, **APPROVED},
                },
            ),
        )
    )
    assert data["draft_restored_from_version"] == 1


async def test_schedule_crud(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Sched WF")
    publish = await client.post(f"/workflows/{workflow_id}/publish", json={})
    assert publish.status_code == 200

    created = _tool_payload(
        await _mcp_post(client,
            json=rpc(
                "tools/call",
                {
                    "name": "create_schedule",
                    "arguments": {
                        "workflow_id": workflow_id,
                        "name": "Hourly run",
                        "schedule_cron": "0 * * * *",
                        "schedule_tz": "UTC",
                        **APPROVED,
                    },
                },
            ),
        )
    )
    assert "schedule_id" in created
    schedule_id = created["schedule_id"]

    # list
    listing = _tool_payload(
        await _mcp_post(client,
            json=rpc("tools/call", {"name": "list_schedules", "arguments": {"workflow_id": workflow_id}}),
        )
    )
    assert any(s["schedule_id"] == schedule_id for s in listing["schedules"])

    updated = _tool_payload(
        await _mcp_post(client,
            json=rpc(
                "tools/call",
                {
                    "name": "update_schedule",
                    "arguments": {
                        "schedule_id": schedule_id,
                        "name": "Every two hours",
                        "schedule_cron": "0 */2 * * *",
                        **APPROVED,
                    },
                },
            ),
        )
    )
    assert updated["name"] == "Every two hours"

    # toggle off
    toggled = _tool_payload(
        await _mcp_post(client,
            json=rpc(
                "tools/call",
                {
                    "name": "toggle_schedule",
                    "arguments": {"schedule_id": schedule_id, "active": False, **APPROVED},
                },
            ),
        )
    )
    assert toggled["active"] is False

    # delete
    deleted = _tool_payload(
        await _mcp_post(client,
            json=rpc(
                "tools/call",
                {
                    "name": "delete_schedule",
                    "arguments": {"schedule_id": schedule_id, **APPROVED},
                },
            ),
        )
    )
    assert deleted["deleted"] is True


async def test_create_schedule_missing_workflow(client: AsyncClient) -> None:
    resp = await _mcp_post(client,
        json=rpc(
            "tools/call",
            {
                "name": "create_schedule",
                "arguments": {"workflow_id": "ghost", "name": "Bad", **APPROVED},
            },
        ),
    )
    assert resp.json()["result"]["isError"] is True


# ---------------------------------------------------------------------------
# Resources capability (Task 10)
# ---------------------------------------------------------------------------


async def test_resources_in_capabilities(client: AsyncClient) -> None:
    resp = await _mcp_post(client,
        json=rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}),
    )
    caps = resp.json()["result"]["capabilities"]
    assert "resources" in caps


async def test_resources_list(client: AsyncClient) -> None:
    resp = await _mcp_post(client, json=rpc("resources/list"))
    result = resp.json()["result"]
    assert "resources" in result
    uris = {r["uri"] for r in result["resources"]}
    assert "nodyra://workflow-authoring-guide" in uris
    assert "nodyra://node-types" in uris


async def test_resource_templates_list(client: AsyncClient) -> None:
    response = await _mcp_post(client, json=rpc("resources/templates/list"))
    templates = response.json()["result"]["resourceTemplates"]
    uri_templates = {template["uriTemplate"] for template in templates}
    assert "nodyra://workflow/{workflow_id}" in uri_templates
    assert "nodyra://node-type/{node_type}" in uri_templates


async def test_resources_list_includes_workflows(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Resource WF")
    resp = await _mcp_post(client, json=rpc("resources/list"))
    uris = {r["uri"] for r in resp.json()["result"]["resources"]}
    assert f"nodyra://workflow/{workflow_id}" in uris


async def test_resources_read_node_types(client: AsyncClient) -> None:
    resp = await _mcp_post(client,
        json=rpc("resources/read", {"uri": "nodyra://node-types"}),
    )
    result = resp.json()["result"]
    assert "contents" in result
    text = result["contents"][0]["text"]
    data = json.loads(text)
    assert "node_types" in data
    assert "llm_guidance" in data["node_types"][0]


async def test_resources_read_workflow_authoring_guide(client: AsyncClient) -> None:
    resp = await _mcp_post(client,
        json=rpc("resources/read", {"uri": "nodyra://workflow-authoring-guide"}),
    )
    result = resp.json()["result"]
    data = json.loads(result["contents"][0]["text"])
    assert "authoring_sequence" in data
    assert "api_endpoint" in data["trigger_recipes"]


async def test_resources_read_node_type_contract(client: AsyncClient) -> None:
    resp = await _mcp_post(client,
        json=rpc("resources/read", {"uri": "nodyra://node-type/api_endpoint"}),
    )
    result = resp.json()["result"]
    data = json.loads(result["contents"][0]["text"])
    assert data["id"] == "api_endpoint"
    assert "outputs_override" in json.dumps(data["llm_guidance"], sort_keys=True)


async def test_resources_read_workflow(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "ReadRes WF")
    resp = await _mcp_post(client,
        json=rpc("resources/read", {"uri": f"nodyra://workflow/{workflow_id}"}),
    )
    result = resp.json()["result"]
    data = json.loads(result["contents"][0]["text"])
    assert data["id"] == workflow_id
    assert "graph" in data


async def test_resources_read_unknown_uri(client: AsyncClient) -> None:
    resp = await _mcp_post(client,
        json=rpc("resources/read", {"uri": "nodyra://nonexistent"}),
    )
    assert resp.json()["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# Prompts capability (Task 11)
# ---------------------------------------------------------------------------


async def test_prompts_in_capabilities(client: AsyncClient) -> None:
    resp = await _mcp_post(client,
        json=rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}),
    )
    assert "prompts" in resp.json()["result"]["capabilities"]


async def test_prompts_list(client: AsyncClient) -> None:
    resp = await _mcp_post(client, json=rpc("prompts/list"))
    result = resp.json()["result"]
    assert "prompts" in result
    names = {p["name"] for p in result["prompts"]}
    assert {"build_workflow", "debug_run", "optimize_workflow"} <= names


async def test_prompts_get_build_workflow(client: AsyncClient) -> None:
    resp = await _mcp_post(client,
        json=rpc("prompts/get", {"name": "build_workflow", "arguments": {"description": "send a daily email"}}),
    )
    result = resp.json()["result"]
    assert "messages" in result
    assert len(result["messages"]) >= 1
    text = result["messages"][0]["content"]["text"]
    assert "send a daily email" in text
    assert "get_workflow_authoring_guide" in text
    assert "get_node_contracts" in text
    assert "outputs_override" in text


async def test_prompts_validate_required_arguments(client: AsyncClient) -> None:
    response = await _mcp_post(client,
        json=rpc("prompts/get", {"name": "build_workflow", "arguments": {}}),
    )
    assert response.json()["error"]["code"] == -32602


async def test_prompts_get_debug_run(client: AsyncClient) -> None:
    resp = await _mcp_post(client,
        json=rpc("prompts/get", {"name": "debug_run", "arguments": {"run_id": "abc123"}}),
    )
    result = resp.json()["result"]
    assert "abc123" in result["messages"][0]["content"]["text"]


async def test_prompts_get_unknown(client: AsyncClient) -> None:
    resp = await _mcp_post(client,
        json=rpc("prompts/get", {"name": "nonexistent_prompt", "arguments": {}}),
    )
    assert resp.json()["error"]["code"] == -32601


async def test_client_nodes_loopback_against_own_server(
    client: AsyncClient, monkeypatch
) -> None:
    from nodyra_nodes.ai_v2 import mcp as mcp_module

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
    from nodyra_nodes.ai_v2 import mcp as mcp_module

    @asynccontextmanager
    async def _loopback(config):
        yield _LoopbackSession(client)

    monkeypatch.setattr(mcp_module, "_mcp_session", _loopback)

    creds = {"url": "https://loopback.invalid/mcp"}
    resources = await mcp_module.mcp_list_resources(credentials=creds)
    assert any(r["uri"] == "nodyra://node-types" for r in resources)


async def test_mcp_read_resource_loopback(client: AsyncClient, monkeypatch) -> None:
    from nodyra_nodes.ai_v2 import mcp as mcp_module

    @asynccontextmanager
    async def _loopback(config):
        yield _LoopbackSession(client)

    monkeypatch.setattr(mcp_module, "_mcp_session", _loopback)

    creds = {"url": "https://loopback.invalid/mcp"}
    data = await mcp_module.mcp_read_resource(credentials=creds, resource_uri="nodyra://node-types")
    assert isinstance(data, dict)
    assert "node_types" in data


async def test_mcp_get_prompt_loopback(client: AsyncClient, monkeypatch) -> None:
    from nodyra_nodes.ai_v2 import mcp as mcp_module

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

    from nodyra_nodes.ai_v2 import mcp as mcp_module

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


async def test_mcp_rate_limit(client: AsyncClient, monkeypatch) -> None:
    """After exceeding the per-IP rate limit, /mcp returns 429."""
    import app.routers.mcp as mcp_router

    call_count = 0

    async def _deny_after_one(bucket, identifier, *, limit, window_seconds):
        nonlocal call_count
        call_count += 1
        return call_count <= 1  # first call allowed, rest denied

    monkeypatch.setattr(mcp_router, "_rate_allow", _deny_after_one)

    r1 = await _mcp_post(client, json=rpc("ping"))
    assert r1.status_code == 200

    r2 = await _mcp_post(client, json=rpc("ping"))
    assert r2.status_code == 429


# ---------------------------------------------------------------------------
# New tool smoke tests
# ---------------------------------------------------------------------------


async def _tool(client: AsyncClient, name: str, args: dict, *, approve: bool = True) -> dict:
    resp = await _mcp_post(client, approve=approve, json=rpc("tools/call", {"name": name, "arguments": args}))
    assert resp.status_code == 200
    return resp.json()["result"]


async def test_rename_workflow(client: AsyncClient) -> None:
    wf_id = await make_workflow(client, "Old Name")
    result = await _tool(client, "rename_workflow", {"workflow_id": wf_id, "name": "New Name"})
    assert result["content"][0]["text"]
    data = json.loads(result["content"][0]["text"])
    assert data["name"] == "New Name"


async def test_get_node(client: AsyncClient) -> None:
    wf_id = await make_workflow(client)
    result = await _tool(client, "get_node", {"workflow_id": wf_id, "node_id": "t"})
    data = json.loads(result["content"][0]["text"])
    assert data["id"] == "t"
    assert data["type"] == "manual_trigger"


async def test_get_node_missing(client: AsyncClient) -> None:
    wf_id = await make_workflow(client)
    result = await _tool(client, "get_node", {"workflow_id": wf_id, "node_id": "no_such"})
    assert result["isError"] is True


async def test_rename_node(client: AsyncClient) -> None:
    wf_id = await make_workflow(client)
    result = await _tool(client, "rename_node", {"workflow_id": wf_id, "node_id": "t", "label": "Start"})
    data = json.loads(result["content"][0]["text"])
    assert data["label"] == "Start"
    # Confirm label persisted
    node = json.loads((await _tool(client, "get_node", {"workflow_id": wf_id, "node_id": "t"}))["content"][0]["text"])
    assert node["label"] == "Start"


async def test_move_node(client: AsyncClient) -> None:
    wf_id = await make_workflow(client)
    result = await _tool(client, "move_node", {"workflow_id": wf_id, "node_id": "t", "x": 100.0, "y": 200.0})
    data = json.loads(result["content"][0]["text"])
    assert data["position"]["x"] == 100.0
    assert data["position"]["y"] == 200.0


async def test_create_code_node(client: AsyncClient) -> None:
    wf_id = await make_workflow(client)
    result = await _tool(client, "create_code_node", {
        "workflow_id": wf_id, "node_id": "transform", "code": "output = input * 2", "label": "Double",
        "approved_by_user": True,
    })
    data = json.loads(result["content"][0]["text"])
    assert data["node_id"] == "transform"
    assert data["node_count"] == 2  # manual_trigger + transform
    # confirm it's actually a code node
    node = json.loads((await _tool(client, "get_node", {"workflow_id": wf_id, "node_id": "transform"}))["content"][0]["text"])
    assert node["type"] == "code"
    assert node["label"] == "Double"


async def test_update_code(client: AsyncClient) -> None:
    wf_id = await make_workflow(client)
    await _tool(client, "create_code_node", {"workflow_id": wf_id, "node_id": "fn", "code": "output = 1", "approved_by_user": True})
    result = await _tool(client, "update_code", {"workflow_id": wf_id, "node_id": "fn", "code": "output = 42", "approved_by_user": True})
    data = json.loads(result["content"][0]["text"])
    assert data["node_id"] == "fn"
    node = json.loads((await _tool(client, "get_node", {"workflow_id": wf_id, "node_id": "fn"}))["content"][0]["text"])
    assert node["params"]["code"] == "output = 42"


async def test_update_code_on_non_code_node(client: AsyncClient) -> None:
    wf_id = await make_workflow(client)
    result = await _tool(client, "update_code", {"workflow_id": wf_id, "node_id": "t", "code": "output = 1"})
    assert result["isError"] is True


async def test_list_environments(client: AsyncClient) -> None:
    result = await _tool(client, "list_environments", {})
    data = json.loads(result["content"][0]["text"])
    assert "environments" in data


async def test_apply_workflow_patch_atomic(client: AsyncClient) -> None:
    wf_id = await make_workflow(client)
    current = json.loads(
        (await _tool(client, "get_workflow", {"workflow_id": wf_id}))["content"][0]["text"]
    )
    revision = current["graph_revision"]
    operations = [
        {
            "op": "add_node",
            "node": {
                "id": "code",
                "type": "code",
                "params": {"code": "output = input"},
                "position": {"x": 260, "y": 0},
            },
        },
        {
            "op": "add_edge",
            "edge": {"source": "t", "target": "code"},
        },
    ]
    preview = await _tool(
        client,
        "preview_workflow_patch",
        {"workflow_id": wf_id, "operations": operations, "expected_graph_revision": revision},
    )
    preview_data = json.loads(preview["content"][0]["text"])
    assert preview_data["valid"] is True
    assert preview_data["change_count"] == 2

    applied = await _tool(
        client,
        "apply_workflow_patch",
        {
            "workflow_id": wf_id,
            "operations": operations,
            "expected_graph_revision": revision,
            **APPROVED,
        },
    )
    applied_data = json.loads(applied["content"][0]["text"])
    assert applied_data["node_count"] == 2
    assert applied_data["edge_count"] == 1
    assert applied_data["graph_revision"] == revision + 1

    stale = await _tool(
        client,
        "apply_workflow_patch",
        {
            "workflow_id": wf_id,
            "operations": [{"op": "rename_node", "node_id": "t", "label": "Start"}],
            "expected_graph_revision": revision,
            **APPROVED,
        },
    )
    assert stale["isError"] is True


async def test_preview_workflow_patch_reports_invalid_without_saving(client: AsyncClient) -> None:
    wf_id = await make_workflow(client)
    before = json.loads(
        (await _tool(client, "get_workflow", {"workflow_id": wf_id}))["content"][0]["text"]
    )
    preview = await _tool(
        client,
        "preview_workflow_patch",
        {
            "workflow_id": wf_id,
            "operations": [
                {"op": "add_edge", "edge": {"source": "ghost", "target": "t"}},
            ],
        },
    )
    preview_data = json.loads(preview["content"][0]["text"])
    assert preview_data["valid"] is False
    assert "missing node" in preview_data["error"].lower()
    after = json.loads(
        (await _tool(client, "get_workflow", {"workflow_id": wf_id}))["content"][0]["text"]
    )
    assert after["graph_revision"] == before["graph_revision"]
    assert len(after["graph"]["edges"]) == 0


async def test_search_node_catalog_and_suggest_config(client: AsyncClient) -> None:
    search = await _tool(client, "search_node_catalog", {"query": "manual", "limit": 5})
    search_data = json.loads(search["content"][0]["text"])
    assert any(node["id"] == "manual_trigger" for node in search_data["nodes"])

    suggested = await _tool(
        client,
        "suggest_node_config",
        {"node_type": "manual_trigger", "node_id": "start", "x": 10, "y": 20},
    )
    suggested_data = json.loads(suggested["content"][0]["text"])
    assert suggested_data["node"]["id"] == "start"
    assert suggested_data["node"]["type"] == "manual_trigger"
    assert suggested_data["node"]["position"] == {"x": 10.0, "y": 20.0}


async def test_environment_package_tools(client: AsyncClient, monkeypatch) -> None:
    async def fake_build_environment(env_id: str) -> None:
        return None

    monkeypatch.setattr(
        "app.services.backends.build_environment",
        fake_build_environment,
    )
    created = await _tool(
        client,
        "create_environment",
        {"name": "MCP Env", "packages": ["numpy==2.0.0"], **APPROVED},
    )
    created_data = json.loads(created["content"][0]["text"])
    env_id = created_data["id"]
    build_job_id = created_data["build_job_id"]
    assert created_data["build_started"] is True
    assert build_job_id
    assert created_data["build_job_status"] == "queued"
    assert created_data["packages"] == ["numpy==2.0.0"]

    build_job = await _tool(
        client,
        "get_environment_build_job",
        {"build_job_id": build_job_id, "environment_id": env_id},
    )
    build_job_data = json.loads(build_job["content"][0]["text"])
    assert build_job_data["id"] == build_job_id
    assert build_job_data["status"] == "queued"

    added = await _tool(
        client,
        "add_environment_package",
        {"environment_id": env_id, "package": "requests==2.32.0", **APPROVED},
    )
    added_data = json.loads(added["content"][0]["text"])
    assert "requests==2.32.0" in added_data["packages"]

    removed = await _tool(
        client,
        "remove_environment_package",
        {"environment_id": env_id, "package": "requests", **APPROVED},
    )
    removed_data = json.loads(removed["content"][0]["text"])
    assert all("requests" not in package for package in removed_data["packages"])

    set_result = await _tool(
        client,
        "set_environment_packages",
        {
            "environment_id": env_id,
            "packages": ["pandas==2.2.2", "pandas>=2"],
            **APPROVED,
        },
    )
    set_data = json.loads(set_result["content"][0]["text"])
    assert set_data["packages"] == ["pandas>=2"]

    jobs = await _tool(
        client,
        "list_environment_build_jobs",
        {"environment_id": env_id, "limit": 10},
    )
    jobs_data = json.loads(jobs["content"][0]["text"])
    assert len(jobs_data["build_jobs"]) >= 1


async def test_create_environment_validation_error_is_tool_error(client: AsyncClient) -> None:
    result = await _tool(
        client,
        "create_environment",
        {"name": "Bad Env", "python_version": "2.7", **APPROVED},
    )
    assert result["isError"] is True
    assert "invalid environment request" in result["content"][0]["text"].lower()


async def test_list_credentials(client: AsyncClient) -> None:
    result = await _tool(client, "list_credentials", {})
    data = json.loads(result["content"][0]["text"])
    assert "credentials" in data


async def test_set_error_handler(client: AsyncClient) -> None:
    wf_id = await make_workflow(client, "Main WF")
    err_wf_id = await make_workflow(client, "Error WF")
    result = await _tool(client, "set_error_handler", {"workflow_id": wf_id, "error_workflow_id": err_wf_id})
    data = json.loads(result["content"][0]["text"])
    assert data["error_workflow_id"] == err_wf_id
    # clear it
    result2 = await _tool(client, "set_error_handler", {"workflow_id": wf_id, "error_workflow_id": None})
    data2 = json.loads(result2["content"][0]["text"])
    assert data2["error_workflow_id"] is None


async def test_enable_disable_mcp_tool(client: AsyncClient) -> None:
    wf_id = await make_workflow(client, "My Agent")
    en = await _tool(client, "enable_mcp_tool", {"workflow_id": wf_id, "tool_name": "my_agent", "description": "Does stuff"})
    en_data = json.loads(en["content"][0]["text"])
    assert en_data["mcp_enabled"] is True
    assert en_data["tool_name"] == "my_agent"
    dis = await _tool(client, "disable_mcp_tool", {"workflow_id": wf_id})
    dis_data = json.loads(dis["content"][0]["text"])
    assert dis_data["mcp_enabled"] is False


async def test_enable_mcp_tool_rejects_tenant_local_name_collision(client: AsyncClient) -> None:
    first = await make_workflow(client, "First Agent")
    second = await make_workflow(client, "Second Agent")
    enabled = await _tool(
        client, "enable_mcp_tool", {"workflow_id": first, "tool_name": "same_name"}
    )
    assert enabled["isError"] is False
    collision = await _tool(
        client, "enable_mcp_tool", {"workflow_id": second, "tool_name": "same_name"}
    )
    assert collision["isError"] is True
    assert "already" in collision["content"][0]["text"].lower()


async def test_enable_mcp_tool_returns_generated_name(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Generated Name")
    result = await _tool(client, "enable_mcp_tool", {"workflow_id": workflow_id})
    data = json.loads(result["content"][0]["text"])
    assert data["tool_name"].startswith("workflow_generated_name_")


async def test_workflow_settings_and_version_tools(client: AsyncClient) -> None:
    workflow_id = await make_workflow(client, "Versioned")
    published = await client.post(f"/workflows/{workflow_id}/publish", json={})
    assert published.status_code == 200
    version = published.json()["version"]

    settings_result = await _tool(
        client,
        "update_workflow_settings",
        {
            "workflow_id": workflow_id,
            "allow_concurrent": False,
            "run_timeout_seconds": 45,
            "artifact_retention_days": 3,
            "mcp_parameters_schema": {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
        },
    )
    settings_data = json.loads(settings_result["content"][0]["text"])
    assert settings_data["allow_concurrent"] is False
    assert settings_data["run_timeout_seconds"] == 45
    assert settings_data["artifact_retention_days"] == 3

    version_result = await _tool(
        client,
        "get_workflow_version",
        {"workflow_id": workflow_id, "version": version},
    )
    version_data = json.loads(version_result["content"][0]["text"])
    assert version_data["version"] == version
    assert version_data["graph"]["nodes"]

    diff_result = await _tool(
        client,
        "diff_workflow_versions",
        {"workflow_id": workflow_id, "from_version": version},
    )
    diff_data = json.loads(diff_result["content"][0]["text"])
    assert diff_data["changed"] is False


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("update_code", {"node_id": "fn", "code": "import os"}),
        ("create_code_node", {"node_id": "new_fn", "code": "output = 1"}),
        ("toggle_workflow", {"active": True}),
    ],
)
async def test_consequential_tools_refuse_over_the_wire_without_approval(
    client: AsyncClient, tool: str, arguments: dict
) -> None:
    """The approval gate has to hold at the server, not only in the source.

    test_mcp_approval_consistency.py checks that the gate is *present* on these
    tools by reading the source. That would still pass if the helper stopped
    refusing. This drives the real endpoint and asserts a refusal comes back.
    """
    wf_id = await make_workflow(client)
    await _tool(
        client,
        "create_code_node",
        {"workflow_id": wf_id, "node_id": "fn", "code": "output = 1", "approved_by_user": True},
    )

    result = await _tool(client, tool, {"workflow_id": wf_id, **arguments}, approve=False)
    assert result["isError"] is True, (
        f"{tool} ran without approved_by_user, so an agent reaches it without "
        f"involving the human the gate exists for"
    )
    text = result["content"][0]["text"]
    assert "approved_by_user" in text, (
        f"{tool} refused but did not say what the caller must do: {text!r}"
    )


def _patch_tool_handler(monkeypatch, mcp_router, name: str, handler) -> None:
    """Swap one tool's handler. McpTool is a frozen dataclass, so replace it."""
    import dataclasses

    original = mcp_router.get_tool
    swapped = dataclasses.replace(original(name), handler=handler)
    monkeypatch.setattr(
        mcp_router,
        "get_tool",
        lambda tool_name: swapped if tool_name == name else original(tool_name),
    )


async def test_service_error_reaches_the_model_instead_of_an_error_id(
    client: AsyncClient, monkeypatch
) -> None:
    """A 4xx service error must arrive as its own message.

    Running a workflow whose environment lacks a package raised
    PackageNotInstalled, which is neither McpToolError nor HTTPException, so
    it fell into the blanket handler and the model got
    "Internal tool error (reference abc123)." The one sentence that says
    which package and which node was left in the server log.
    """
    from app.exceptions import PackageNotInstalled
    from app.routers import mcp as mcp_router

    message = (
        "This workflow's environment is missing packages required by its "
        "nodes: statsmodels>=0.14 (needed by decompose)."
    )

    async def _boom(session, user, arguments):
        raise PackageNotInstalled(message)

    _patch_tool_handler(monkeypatch, mcp_router, "list_workflows", _boom)

    result = (
        await _mcp_post(client, json=rpc("tools/call", {"name": "list_workflows", "arguments": {}})
        )
    ).json()["result"]

    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "statsmodels>=0.14" in text
    assert "decompose" in text
    assert "Internal tool error" not in text


async def test_unexpected_error_still_hides_behind_a_reference(
    client: AsyncClient, monkeypatch
) -> None:
    """Genuine bugs stay opaque — only 4xx service errors are passed through."""
    from app.routers import mcp as mcp_router

    async def _boom(session, user, arguments):
        raise RuntimeError("psycopg: connection string contains a password")

    _patch_tool_handler(monkeypatch, mcp_router, "list_workflows", _boom)

    result = (
        await _mcp_post(client, json=rpc("tools/call", {"name": "list_workflows", "arguments": {}})
        )
    ).json()["result"]

    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "Internal tool error" in text
    assert "password" not in text


async def test_running_an_unpublished_workflow_says_so(client: AsyncClient) -> None:
    """A never-published draft must not report a missing trigger.

    Creating a workflow leaves an empty version 1 behind, so running the
    published version of a workflow whose draft was never published executed
    that empty graph and failed with "Workflow needs a trigger to run." — and
    the trigger node is sitting right there in the draft.
    """
    workflow_id = await make_workflow(client, "Never published")

    result = (
        await _mcp_post(client,
            json=rpc(
                "tools/call",
                {
                    "name": "run_workflow",
                    "arguments": {"workflow_id": workflow_id, "use_draft": False},
                },
            ),
        )
    ).json()["result"]

    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "has no nodes" in text
    assert "publish_workflow" in text
    assert "trigger" not in text.lower()


async def test_running_the_draft_of_an_unpublished_workflow_works(
    client: AsyncClient,
) -> None:
    """The remedy the message offers has to actually work."""
    workflow_id = await make_workflow(client, "Draft runnable")

    result = (
        await _mcp_post(client,
            json=rpc(
                "tools/call",
                {
                    "name": "run_workflow",
                    "arguments": {"workflow_id": workflow_id, "use_draft": True},
                },
            ),
        )
    ).json()["result"]

    assert result["isError"] is False, result["content"][0]["text"]


async def test_validating_an_empty_workflow_is_not_valid(client: AsyncClient) -> None:
    """A brand-new workflow must not validate as ready.

    The trigger check was skipped when a graph had no nodes at all, so
    validate_workflow_graph answered {"valid": true} for an empty draft — at
    the one moment the caller most needs to hear that nothing is there yet.
    """
    workflow_id = (await client.post("/workflows", json={"name": "Empty"})).json()["id"]

    payload = _tool_payload_allowing_error(
        await _mcp_post(client,
            json=rpc(
                "tools/call",
                {
                    "name": "validate_workflow_graph",
                    "arguments": {"workflow_id": workflow_id},
                },
            ),
        )
    )

    assert payload["valid"] is False
    assert "no nodes" in payload["error"]


def _tool_payload_allowing_error(resp) -> dict:
    """validate_workflow_graph reports invalidity in its payload, not isError."""
    return json.loads(resp.json()["result"]["content"][0]["text"])
