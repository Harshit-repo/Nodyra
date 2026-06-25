# MCP Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand Noodle's MCP surface from 10 static tools to a 26-tool API with Resources, Prompts, enhanced client nodes, rate limiting, and batch JSON-RPC.

**Architecture:** New server tools follow the existing `McpTool` dataclass in `tools.py`; two new modules (`resources.py`, `prompts.py`) split MCP capabilities cleanly; the router gains `resources/*` and `prompts/*` dispatch plus batch support; three new client node functions extend `packages/nodes/noodle_nodes/ai_v2/mcp.py`.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2 async, httpx (tests), mcp SDK (client nodes), pytest-asyncio.

## Global Constraints

- All new tool handlers: `async def _name(session: AsyncSession, user: User | None, args: dict) -> Any`
- Raise `McpToolError(message)` for user-visible errors — never `HTTPException`.
- RBAC: read tools → `permission=None`; mutations → `permission="workflow:write"`; run ops → `permission="workflow:run"`.
- Always `await log_audit(...)` before `await session.commit()` on mutations.
- Tests: `cd apps/api && python -m pytest tests/test_mcp_server.py -v`
- Use `rpc()`, `_tool_payload()`, `make_workflow()` helpers from the existing test file.
- No new DB migrations — all tools work against existing tables.

## File Map

| File | Action | Responsibility |
|---|---|---|
| `apps/api/app/mcp/tools.py` | Modify | 16 new handler functions + STATIC_TOOLS entries |
| `apps/api/app/mcp/resources.py` | **Create** | Resource catalogue + read handlers |
| `apps/api/app/mcp/prompts.py` | **Create** | Prompt catalogue + get handlers |
| `apps/api/app/mcp/protocol.py` | Modify | Add resources/prompts to SERVER_CAPABILITIES; listChanged=True |
| `apps/api/app/routers/mcp.py` | Modify | resources/*, prompts/*, rate limiting, batch JSON-RPC |
| `packages/nodes/noodle_nodes/ai_v2/mcp.py` | Modify | 3 new nodes + image content fix |
| `apps/api/tests/test_mcp_server.py` | Modify | Tests for all new tools + capabilities |

---

## Phase 1 — Observability Tools

### Task 1: `list_runs` and `get_run_events` tools

**Files:**
- Modify: `apps/api/app/mcp/tools.py`
- Modify: `apps/api/tests/test_mcp_server.py`

**Interfaces:**
- Produces: `list_runs(workflow_id?, status?, limit?) → {runs: [{run_id, workflow_id, status, trigger_type, mode, started_at, finished_at}]}`
- Produces: `get_run_events(run_id, limit?, after_sequence?) → {run_id, events: [{event_type, sequence, ts, node_id, payload}]}`

- [ ] **Step 1: Write failing tests**

Add to `apps/api/tests/test_mcp_server.py`:

```python
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
```

- [ ] **Step 2: Run to confirm they fail**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "list_runs or get_run_events" 2>&1 | tail -20
```
Expected: FAILED — `list_runs` / `get_run_events` unknown tool.

- [ ] **Step 3: Implement handlers in `tools.py`**

Add to imports at top of `apps/api/app/mcp/tools.py`:
```python
# (func and select are already imported; no new stdlib imports needed)
```

Add handler functions after `_get_run` (around line 220):

```python
async def _list_runs(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow_id = str(args.get("workflow_id") or "").strip()
    status_filter = str(args.get("status") or "").strip()
    limit = max(1, min(int(args.get("limit") or 20), 100))

    stmt = select(Run).order_by(Run.started_at.desc()).limit(limit)
    if workflow_id:
        stmt = stmt.where(Run.workflow_id == workflow_id)
    if status_filter:
        stmt = stmt.where(Run.status == status_filter)

    runs = (await session.scalars(stmt)).all()
    return {
        "runs": [
            {
                "run_id": r.id,
                "workflow_id": r.workflow_id,
                "status": r.status,
                "trigger_type": r.trigger_type,
                "mode": r.mode,
                "started_at": str(r.started_at),
                "finished_at": str(r.finished_at) if r.finished_at else None,
            }
            for r in runs
        ]
    }


async def _get_run_events(session: AsyncSession, user: User | None, args: dict) -> Any:
    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        raise McpToolError("run_id is required.")
    limit = max(1, min(int(args.get("limit") or 50), 200))
    after_seq = int(args.get("after_sequence") or 0)

    run = await session.get(Run, run_id)
    if run is None:
        raise McpToolError(f"Run not found: {run_id}")

    events = (
        await session.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.sequence > after_seq)
            .order_by(RunEvent.sequence)
            .limit(limit)
        )
    ).all()
    return {
        "run_id": run_id,
        "events": [
            {
                "event_type": e.event_type,
                "sequence": e.sequence,
                "ts": str(e.ts),
                "node_id": e.node_id,
                "payload": _truncated(e.payload),
            }
            for e in events
        ],
    }
```

Add to `STATIC_TOOLS` list (after the `get_run` entry):

```python
    McpTool(
        name="list_runs",
        description=(
            "List recent runs, optionally filtered by workflow_id and/or status. "
            "status values: running, queued, waiting, success, error, cancelled."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "Filter to one workflow."},
                "status": {"type": "string", "description": "Filter by run status."},
                "limit": {"type": "integer", "description": "Max results (1-100, default 20)."},
            },
        },
        permission=None,
        handler=_list_runs,
    ),
    McpTool(
        name="get_run_events",
        description=(
            "Full event log for a run (run_started, node_started, node_finished, "
            "run_error, etc.). Use after_sequence to page through large logs."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "limit": {"type": "integer", "description": "Max events (1-200, default 50)."},
                "after_sequence": {"type": "integer", "description": "Skip events at or before this sequence."},
            },
            "required": ["run_id"],
        },
        permission=None,
        handler=_get_run_events,
    ),
```

- [ ] **Step 4: Run tests to confirm pass**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "list_runs or get_run_events" 2>&1 | tail -20
```
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/mcp/tools.py apps/api/tests/test_mcp_server.py
git commit -m "feat(mcp): add list_runs and get_run_events tools"
```

---

### Task 2: `cancel_run` tool

**Files:**
- Modify: `apps/api/app/mcp/tools.py`
- Modify: `apps/api/tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `cancel_run` service from `app.services.runner`
- Produces: `cancel_run(run_id) → {run_id, status}`

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run to confirm fail**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "cancel_run" 2>&1 | tail -10
```

- [ ] **Step 3: Implement in `tools.py`**

Add to the import line for runner (replace existing):
```python
from app.services.runner import cancel_run as _runner_cancel_run, start_run
```

Add handler after `_run_workflow`:

```python
async def _cancel_run(session: AsyncSession, user: User | None, args: dict) -> Any:
    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        raise McpToolError("run_id is required.")
    run = await session.get(Run, run_id)
    if run is None:
        raise McpToolError(f"Run not found: {run_id}")
    status = await _runner_cancel_run(run_id)
    return {"run_id": run_id, "status": status or run.status}
```

Add to `STATIC_TOOLS` after `run_workflow`:

```python
    McpTool(
        name="cancel_run",
        description="Cancel a running or queued workflow run. Returns the resulting status.",
        input_schema={
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
        permission="workflow:run",
        handler=_cancel_run,
    ),
```

- [ ] **Step 4: Run tests**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "cancel_run" 2>&1 | tail -10
```
Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/mcp/tools.py apps/api/tests/test_mcp_server.py
git commit -m "feat(mcp): add cancel_run tool"
```

---

### Task 3: `get_workflow_stats` tool

**Files:**
- Modify: `apps/api/app/mcp/tools.py`
- Modify: `apps/api/tests/test_mcp_server.py`

**Interfaces:**
- Produces: `get_workflow_stats(workflow_id) → {workflow_id, total_runs, success_count, error_count, last_run_at}`

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run to confirm fail**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "workflow_stats" 2>&1 | tail -10
```

- [ ] **Step 3: Implement in `tools.py`**

Update the sqlalchemy import line to add `case`:
```python
from sqlalchemy import case, func, select
```

Add handler after `_get_run_events`:

```python
async def _get_workflow_stats(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow_id = str(args.get("workflow_id") or "").strip()
    if not workflow_id:
        raise McpToolError("workflow_id is required.")
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise McpToolError(f"Workflow not found: {workflow_id}")

    row = (
        await session.execute(
            select(
                func.count().label("total"),
                func.sum(case((Run.status == "success", 1), else_=0)).label("success_count"),
                func.sum(case((Run.status == "error", 1), else_=0)).label("error_count"),
                func.max(Run.started_at).label("last_run_at"),
            ).where(Run.workflow_id == workflow_id)
        )
    ).one()
    return {
        "workflow_id": workflow_id,
        "total_runs": row.total or 0,
        "success_count": row.success_count or 0,
        "error_count": row.error_count or 0,
        "last_run_at": str(row.last_run_at) if row.last_run_at else None,
    }
```

Add to `STATIC_TOOLS` after `cancel_run`:

```python
    McpTool(
        name="get_workflow_stats",
        description="Aggregate stats for a workflow: total runs, success/error counts, last run time.",
        input_schema={
            "type": "object",
            "properties": {"workflow_id": {"type": "string"}},
            "required": ["workflow_id"],
        },
        permission=None,
        handler=_get_workflow_stats,
    ),
```

- [ ] **Step 4: Run tests**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "workflow_stats" 2>&1 | tail -10
```
Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/mcp/tools.py apps/api/tests/test_mcp_server.py
git commit -m "feat(mcp): add get_workflow_stats tool"
```

---

## Phase 2 — Incremental Graph Editing

### Task 4: `patch_node` tool

**Files:**
- Modify: `apps/api/app/mcp/tools.py`
- Modify: `apps/api/tests/test_mcp_server.py`

**Interfaces:**
- Produces: `patch_node(workflow_id, node_id, params) → {workflow_id, node_id, params}`

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run to confirm fail**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "patch_node" 2>&1 | tail -10
```

- [ ] **Step 3: Implement in `tools.py`**

Add handler after the builder tools section (after `_publish_workflow`):

```python
async def _patch_node(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    node_id = str(args.get("node_id") or "").strip()
    if not node_id:
        raise McpToolError("node_id is required.")
    params = args.get("params")
    if not isinstance(params, dict):
        raise McpToolError("params must be a JSON object.")

    graph = _draft_graph(workflow)
    nodes = list(graph.get("nodes", []))
    for i, node in enumerate(nodes):
        if node.get("id") == node_id:
            merged = {**node.get("params", {}), **params}
            nodes[i] = {**node, "params": merged}
            workflow.draft_graph = {**graph, "nodes": nodes}
            await log_audit(
                session, "mcp_patch_node", "workflow", workflow.id, workflow.name,
                actor_id=user.id if user else None,
                actor_email=user.email if user else None,
            )
            await session.commit()
            return {"workflow_id": workflow.id, "node_id": node_id, "params": merged}
    raise McpToolError(f"Node not found in draft graph: {node_id}")
```

Add to `STATIC_TOOLS`:

```python
    McpTool(
        name="patch_node",
        description=(
            "Merge params into a single node in the draft graph without replacing the whole graph. "
            "Existing params not mentioned in the patch are preserved."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "node_id": {"type": "string"},
                "params": {"type": "object", "description": "Partial params to merge into the node."},
            },
            "required": ["workflow_id", "node_id", "params"],
        },
        permission="workflow:write",
        handler=_patch_node,
    ),
```

- [ ] **Step 4: Run tests**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "patch_node" 2>&1 | tail -10
```
Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/mcp/tools.py apps/api/tests/test_mcp_server.py
git commit -m "feat(mcp): add patch_node incremental graph editing tool"
```

---

### Task 5: `add_node` and `remove_node` tools

**Files:**
- Modify: `apps/api/app/mcp/tools.py`
- Modify: `apps/api/tests/test_mcp_server.py`

**Interfaces:**
- Produces: `add_node(workflow_id, node) → {workflow_id, node_id, node_count}`
- Produces: `remove_node(workflow_id, node_id) → {workflow_id, node_id, removed: true}`

- [ ] **Step 1: Write failing tests**

```python
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
    # add a second node and connect it
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
```

- [ ] **Step 2: Run to confirm fail**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "add_node or remove_node" 2>&1 | tail -15
```

- [ ] **Step 3: Implement in `tools.py`**

Add after `_patch_node`:

```python
async def _add_node(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    node = args.get("node")
    if not isinstance(node, dict):
        raise McpToolError("node must be a JSON object with id, type, params.")
    node_id = str(node.get("id") or "").strip()
    node_type = str(node.get("type") or "").strip()
    if not node_id or not node_type:
        raise McpToolError("node.id and node.type are required.")

    graph = _draft_graph(workflow)
    nodes = list(graph.get("nodes", []))
    if any(n.get("id") == node_id for n in nodes):
        raise McpToolError(f"Node id already exists in draft graph: {node_id!r}")

    known = {m.id for m in node_registry.manifests()}
    if (
        node_type not in known
        and node_type not in STRUCTURAL_NODE_TYPES
        and not node_type.startswith("user:")
    ):
        raise McpToolError(
            f"Unknown node type: {node_type!r}. Use list_node_types to discover valid ids."
        )

    nodes.append(node)
    workflow.draft_graph = {**graph, "nodes": nodes}
    await log_audit(
        session, "mcp_add_node", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {"workflow_id": workflow.id, "node_id": node_id, "node_count": len(nodes)}


async def _remove_node(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    node_id = str(args.get("node_id") or "").strip()
    if not node_id:
        raise McpToolError("node_id is required.")

    graph = _draft_graph(workflow)
    original_count = len(graph.get("nodes", []))
    nodes = [n for n in graph.get("nodes", []) if n.get("id") != node_id]
    if len(nodes) == original_count:
        raise McpToolError(f"Node not found in draft graph: {node_id!r}")

    edges = [
        e for e in graph.get("edges", [])
        if e.get("source") != node_id and e.get("target") != node_id
    ]
    workflow.draft_graph = {**graph, "nodes": nodes, "edges": edges}
    await log_audit(
        session, "mcp_remove_node", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {"workflow_id": workflow.id, "node_id": node_id, "removed": True}
```

Add both to `STATIC_TOOLS`:

```python
    McpTool(
        name="add_node",
        description=(
            "Add a single node to the draft graph. node = {id, type, params, position?}. "
            "node.type must be a valid id from list_node_types."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "node": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "type": {"type": "string"},
                        "params": {"type": "object"},
                        "position": {"type": "object"},
                    },
                    "required": ["id", "type"],
                },
            },
            "required": ["workflow_id", "node"],
        },
        permission="workflow:write",
        handler=_add_node,
    ),
    McpTool(
        name="remove_node",
        description="Remove a node and all its connected edges from the draft graph.",
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "node_id": {"type": "string"},
            },
            "required": ["workflow_id", "node_id"],
        },
        permission="workflow:write",
        handler=_remove_node,
    ),
```

- [ ] **Step 4: Run tests**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "add_node or remove_node" 2>&1 | tail -15
```
Expected: 4 PASSED.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/mcp/tools.py apps/api/tests/test_mcp_server.py
git commit -m "feat(mcp): add add_node and remove_node incremental editing tools"
```

---

### Task 6: `add_edge` and `remove_edge` tools

**Files:**
- Modify: `apps/api/app/mcp/tools.py`
- Modify: `apps/api/tests/test_mcp_server.py`

**Interfaces:**
- Produces: `add_edge(workflow_id, edge) → {workflow_id, edge_count}`
- Produces: `remove_edge(workflow_id, source, target, source_output?, target_input?) → {workflow_id, removed_count}`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run to confirm fail**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "add_edge or remove_edge" 2>&1 | tail -10
```

- [ ] **Step 3: Implement in `tools.py`**

Add after `_remove_node`:

```python
async def _add_edge(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    edge = args.get("edge")
    if not isinstance(edge, dict):
        raise McpToolError("edge must be a JSON object with source and target.")
    source = str(edge.get("source") or "").strip()
    target = str(edge.get("target") or "").strip()
    if not source or not target:
        raise McpToolError("edge.source and edge.target are required.")

    graph = _draft_graph(workflow)
    node_ids = {n.get("id") for n in graph.get("nodes", [])}
    if source not in node_ids:
        raise McpToolError(f"Source node not found: {source!r}")
    if target not in node_ids:
        raise McpToolError(f"Target node not found: {target!r}")

    edges = list(graph.get("edges", []))
    edges.append(edge)
    workflow.draft_graph = {**graph, "edges": edges}
    await log_audit(
        session, "mcp_add_edge", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {"workflow_id": workflow.id, "edge_count": len(edges)}


async def _remove_edge(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    source = str(args.get("source") or "").strip()
    target = str(args.get("target") or "").strip()
    if not source or not target:
        raise McpToolError("source and target are required.")
    source_output = args.get("source_output")
    target_input = args.get("target_input")

    graph = _draft_graph(workflow)
    edges = list(graph.get("edges", []))

    def _matches(e: dict) -> bool:
        if e.get("source") != source or e.get("target") != target:
            return False
        if source_output is not None and e.get("source_output") != source_output:
            return False
        if target_input is not None and e.get("target_input") != target_input:
            return False
        return True

    remaining = [e for e in edges if not _matches(e)]
    removed_count = len(edges) - len(remaining)
    if removed_count == 0:
        raise McpToolError(f"No matching edge found: {source!r} → {target!r}")

    workflow.draft_graph = {**graph, "edges": remaining}
    await log_audit(
        session, "mcp_remove_edge", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {"workflow_id": workflow.id, "removed_count": removed_count}
```

Add to `STATIC_TOOLS`:

```python
    McpTool(
        name="add_edge",
        description=(
            "Add an edge to the draft graph. "
            "edge = {source, target, source_output?, target_input?}. "
            "Both source and target node ids must already exist in the graph."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "edge": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "target": {"type": "string"},
                        "source_output": {"type": "string"},
                        "target_input": {"type": "string"},
                    },
                    "required": ["source", "target"],
                },
            },
            "required": ["workflow_id", "edge"],
        },
        permission="workflow:write",
        handler=_add_edge,
    ),
    McpTool(
        name="remove_edge",
        description=(
            "Remove an edge from the draft graph by source and target node ids. "
            "Optionally narrow with source_output / target_input when multiple edges connect the same pair."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "source": {"type": "string"},
                "target": {"type": "string"},
                "source_output": {"type": "string"},
                "target_input": {"type": "string"},
            },
            "required": ["workflow_id", "source", "target"],
        },
        permission="workflow:write",
        handler=_remove_edge,
    ),
```

- [ ] **Step 4: Run tests**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "add_edge or remove_edge" 2>&1 | tail -10
```
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/mcp/tools.py apps/api/tests/test_mcp_server.py
git commit -m "feat(mcp): add add_edge and remove_edge tools"
```

---

## Phase 3 — Workflow Lifecycle Tools

### Task 7: `delete_workflow` and `duplicate_workflow` tools

**Files:**
- Modify: `apps/api/app/mcp/tools.py`
- Modify: `apps/api/tests/test_mcp_server.py`

**Interfaces:**
- Produces: `delete_workflow(workflow_id) → {deleted: true, workflow_id}`
- Produces: `duplicate_workflow(workflow_id, name?) → {workflow_id, name, source_workflow_id}`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run to confirm fail**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "delete_workflow or duplicate_workflow" 2>&1 | tail -10
```

- [ ] **Step 3: Implement in `tools.py`**

Add after the builder tools section:

```python
async def _delete_workflow(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    await log_audit(
        session, "delete", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.delete(workflow)
    await session.commit()
    return {"deleted": True, "workflow_id": workflow.id}


async def _duplicate_workflow(session: AsyncSession, user: User | None, args: dict) -> Any:
    from app.routers.workflows import _global_env_id

    source = await _load_workflow(session, str(args.get("workflow_id") or ""))
    new_name = str(args.get("name") or "").strip() or f"{source.name} (copy)"
    graph = _draft_graph(source)

    new_wf = Workflow(
        name=new_name,
        environment_id=source.environment_id or await _global_env_id(session),
        draft_graph=dict(graph),
        published_version=1,
    )
    new_wf.versions.append(WorkflowVersion(version=1, graph=dict(graph)))
    session.add(new_wf)
    await log_audit(
        session, "duplicate", "workflow", new_wf.id, new_name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {
        "workflow_id": new_wf.id,
        "name": new_name,
        "source_workflow_id": source.id,
    }
```

Add to `STATIC_TOOLS`:

```python
    McpTool(
        name="delete_workflow",
        description="Permanently delete a workflow and all its runs, versions, and events.",
        input_schema={
            "type": "object",
            "properties": {"workflow_id": {"type": "string"}},
            "required": ["workflow_id"],
        },
        permission="workflow:write",
        handler=_delete_workflow,
    ),
    McpTool(
        name="duplicate_workflow",
        description=(
            "Clone a workflow's current draft graph into a new workflow. "
            "Optionally specify a name; defaults to '{original} (copy)'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "name": {"type": "string", "description": "Name for the new workflow."},
            },
            "required": ["workflow_id"],
        },
        permission="workflow:write",
        handler=_duplicate_workflow,
    ),
```

- [ ] **Step 4: Run tests**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "delete_workflow or duplicate_workflow" 2>&1 | tail -10
```
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/mcp/tools.py apps/api/tests/test_mcp_server.py
git commit -m "feat(mcp): add delete_workflow and duplicate_workflow lifecycle tools"
```

---

### Task 8: `toggle_workflow`, `list_workflow_versions`, and `rollback_workflow` tools

**Files:**
- Modify: `apps/api/app/mcp/tools.py`
- Modify: `apps/api/tests/test_mcp_server.py`

**Interfaces:**
- Produces: `toggle_workflow(workflow_id, active) → {workflow_id, active}`
- Produces: `list_workflow_versions(workflow_id) → {workflow_id, current_published_version, versions: [{id, version, notes, created_at}]}`
- Produces: `rollback_workflow(workflow_id, version) → {workflow_id, draft_restored_from_version}`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run to confirm fail**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "toggle_workflow or list_workflow_versions or rollback_workflow" 2>&1 | tail -10
```

- [ ] **Step 3: Implement in `tools.py`**

```python
async def _toggle_workflow(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    active = args.get("active")
    if not isinstance(active, bool):
        raise McpToolError("active must be a boolean (true or false).")
    workflow.active = active
    await log_audit(
        session, "toggle_active", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {"workflow_id": workflow.id, "active": workflow.active}


async def _list_workflow_versions(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow_id = str(args.get("workflow_id") or "").strip()
    if not workflow_id:
        raise McpToolError("workflow_id is required.")
    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise McpToolError(f"Workflow not found: {workflow_id}")

    versions = (
        await session.scalars(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .order_by(WorkflowVersion.version.desc())
        )
    ).all()
    return {
        "workflow_id": workflow_id,
        "current_published_version": workflow.published_version,
        "versions": [
            {"id": v.id, "version": v.version, "notes": v.notes, "created_at": str(v.created_at)}
            for v in versions
        ],
    }


async def _rollback_workflow(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow = await _load_workflow(session, str(args.get("workflow_id") or ""))
    version_num = args.get("version")
    if not isinstance(version_num, int):
        raise McpToolError("version must be an integer.")

    target = await session.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow.id,
            WorkflowVersion.version == version_num,
        )
    )
    if target is None:
        raise McpToolError(f"Version {version_num} not found for workflow {workflow.id!r}.")

    workflow.draft_graph = dict(target.graph or EMPTY_GRAPH)
    await log_audit(
        session, "rollback", "workflow", workflow.id, workflow.name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {
        "workflow_id": workflow.id,
        "draft_restored_from_version": version_num,
        "hint": "Draft replaced. Use run_workflow (use_draft=true) to test, then publish_workflow.",
    }
```

Add to `STATIC_TOOLS`:

```python
    McpTool(
        name="toggle_workflow",
        description="Activate or deactivate a workflow (controls whether scheduled triggers fire).",
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "active": {"type": "boolean"},
            },
            "required": ["workflow_id", "active"],
        },
        permission="workflow:write",
        handler=_toggle_workflow,
    ),
    McpTool(
        name="list_workflow_versions",
        description="List all published versions of a workflow, newest first.",
        input_schema={
            "type": "object",
            "properties": {"workflow_id": {"type": "string"}},
            "required": ["workflow_id"],
        },
        permission=None,
        handler=_list_workflow_versions,
    ),
    McpTool(
        name="rollback_workflow",
        description=(
            "Restore a published version's graph to the draft. Does not publish — "
            "call publish_workflow afterwards to make it permanent."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "version": {"type": "integer", "description": "Version number from list_workflow_versions."},
            },
            "required": ["workflow_id", "version"],
        },
        permission="workflow:write",
        handler=_rollback_workflow,
    ),
```

- [ ] **Step 4: Run tests**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "toggle_workflow or list_workflow_versions or rollback_workflow" 2>&1 | tail -10
```
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/mcp/tools.py apps/api/tests/test_mcp_server.py
git commit -m "feat(mcp): add toggle_workflow, list_workflow_versions, rollback_workflow tools"
```

---

## Phase 4 — Schedule Tools

### Task 9: `list_schedules`, `create_schedule`, `delete_schedule`, `toggle_schedule`

**Files:**
- Modify: `apps/api/app/mcp/tools.py`
- Modify: `apps/api/tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `Deployment` model from `app.models`
- Produces: all four schedule tools

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run to confirm fail**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "schedule" 2>&1 | tail -10
```

- [ ] **Step 3: Add `Deployment` to `tools.py` imports**

Update the models import line:
```python
from app.models import Deployment, NodeRun, Run, RunEvent, User, Workflow, WorkflowVersion
```

- [ ] **Step 4: Implement the four handlers in `tools.py`**

```python
async def _list_schedules(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow_id = str(args.get("workflow_id") or "").strip()
    active_filter = args.get("active")
    limit = max(1, min(int(args.get("limit") or 50), 200))

    stmt = select(Deployment).order_by(Deployment.updated_at.desc()).limit(limit)
    if workflow_id:
        stmt = stmt.where(Deployment.workflow_id == workflow_id)
    if isinstance(active_filter, bool):
        stmt = stmt.where(Deployment.active == active_filter)

    deployments = (await session.scalars(stmt)).all()
    return {
        "schedules": [
            {
                "schedule_id": d.id,
                "workflow_id": d.workflow_id,
                "name": d.name,
                "schedule_cron": d.schedule_cron,
                "schedule_interval": d.schedule_interval,
                "schedule_every": d.schedule_every,
                "schedule_tz": d.schedule_tz,
                "active": d.active,
                "last_fired": str(d.last_fired) if d.last_fired else None,
            }
            for d in deployments
        ]
    }


async def _create_schedule(session: AsyncSession, user: User | None, args: dict) -> Any:
    workflow_id = str(args.get("workflow_id") or "").strip()
    if not workflow_id:
        raise McpToolError("workflow_id is required.")
    name = str(args.get("name") or "").strip()
    if not name:
        raise McpToolError("name is required.")

    workflow = await session.get(Workflow, workflow_id)
    if workflow is None:
        raise McpToolError(f"Workflow not found: {workflow_id}")

    default_parameters = args.get("default_parameters") or {}
    if not isinstance(default_parameters, dict):
        raise McpToolError("default_parameters must be a JSON object.")

    deployment = Deployment(
        workflow_id=workflow_id,
        org_id=workflow.org_id,
        name=name,
        schedule_cron=str(args.get("schedule_cron") or ""),
        schedule_interval=str(args.get("schedule_interval") or "hours"),
        schedule_every=max(1, int(args.get("schedule_every") or 1)),
        schedule_tz=str(args.get("schedule_tz") or ""),
        default_parameters=default_parameters,
        active=True,
    )
    session.add(deployment)
    await log_audit(
        session, "create_schedule", "deployment", deployment.id, name,
        actor_id=user.id if user else None,
        actor_email=user.email if user else None,
    )
    await session.commit()
    return {"schedule_id": deployment.id, "workflow_id": workflow_id, "name": name}


async def _delete_schedule(session: AsyncSession, user: User | None, args: dict) -> Any:
    schedule_id = str(args.get("schedule_id") or "").strip()
    if not schedule_id:
        raise McpToolError("schedule_id is required.")
    deployment = await session.get(Deployment, schedule_id)
    if deployment is None:
        raise McpToolError(f"Schedule not found: {schedule_id}")
    await session.delete(deployment)
    await session.commit()
    return {"deleted": True, "schedule_id": schedule_id}


async def _toggle_schedule(session: AsyncSession, user: User | None, args: dict) -> Any:
    schedule_id = str(args.get("schedule_id") or "").strip()
    if not schedule_id:
        raise McpToolError("schedule_id is required.")
    active = args.get("active")
    if not isinstance(active, bool):
        raise McpToolError("active must be a boolean.")
    deployment = await session.get(Deployment, schedule_id)
    if deployment is None:
        raise McpToolError(f"Schedule not found: {schedule_id}")
    deployment.active = active
    await session.commit()
    return {"schedule_id": schedule_id, "active": active}
```

Add to `STATIC_TOOLS`:

```python
    McpTool(
        name="list_schedules",
        description="List cron schedules (deployments). Filter by workflow_id or active state.",
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "active": {"type": "boolean"},
                "limit": {"type": "integer", "description": "Max results (1-200, default 50)."},
            },
        },
        permission=None,
        handler=_list_schedules,
    ),
    McpTool(
        name="create_schedule",
        description=(
            "Create a cron schedule for a workflow. "
            "Supply schedule_cron (e.g. '0 * * * *') OR schedule_interval+schedule_every. "
            "The schedule starts active immediately."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "name": {"type": "string"},
                "schedule_cron": {"type": "string", "description": "Full cron expression (overrides interval fields)."},
                "schedule_interval": {"type": "string", "description": "minutes / hours / days / weeks."},
                "schedule_every": {"type": "integer", "description": "Multiplier for schedule_interval."},
                "schedule_tz": {"type": "string", "description": "IANA timezone, e.g. America/New_York."},
                "default_parameters": {"type": "object", "description": "Default trigger payload."},
            },
            "required": ["workflow_id", "name"],
        },
        permission="workflow:write",
        handler=_create_schedule,
    ),
    McpTool(
        name="delete_schedule",
        description="Permanently delete a cron schedule.",
        input_schema={
            "type": "object",
            "properties": {"schedule_id": {"type": "string"}},
            "required": ["schedule_id"],
        },
        permission="workflow:write",
        handler=_delete_schedule,
    ),
    McpTool(
        name="toggle_schedule",
        description="Activate or deactivate a cron schedule without deleting it.",
        input_schema={
            "type": "object",
            "properties": {
                "schedule_id": {"type": "string"},
                "active": {"type": "boolean"},
            },
            "required": ["schedule_id", "active"],
        },
        permission="workflow:write",
        handler=_toggle_schedule,
    ),
```

- [ ] **Step 5: Run tests**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "schedule" 2>&1 | tail -10
```
Expected: 2 PASSED.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/mcp/tools.py apps/api/tests/test_mcp_server.py
git commit -m "feat(mcp): add list_schedules, create_schedule, delete_schedule, toggle_schedule tools"
```

---

## Phase 5 — MCP Resources Capability

### Task 10: `resources.py` module + `resources/list` and `resources/read` in router

**Files:**
- **Create**: `apps/api/app/mcp/resources.py`
- Modify: `apps/api/app/mcp/protocol.py`
- Modify: `apps/api/app/routers/mcp.py`
- Modify: `apps/api/tests/test_mcp_server.py`

**Interfaces:**
- Produces: `list_resources(session) → list[dict]` with keys `{uri, name, description, mimeType}`
- Produces: `read_resource(session, uri) → dict` with keys `{uri, mimeType, text}`
- Raises: `ValueError(message)` when URI unknown (router converts to METHOD_NOT_FOUND)

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run to confirm fail**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "resources" 2>&1 | tail -15
```

- [ ] **Step 3: Create `apps/api/app/mcp/resources.py`**

```python
"""MCP resource catalogue — exposes Noodle data as readable context."""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Workflow
from noodle.sdk import registry as node_registry


async def list_resources(session: AsyncSession) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = [
        {
            "uri": "noodle://node-types",
            "name": "Node Type Catalogue",
            "description": "All available node types with ids, categories, and descriptions.",
            "mimeType": "application/json",
        }
    ]
    workflows = (
        await session.scalars(
            select(Workflow).order_by(Workflow.updated_at.desc()).limit(100)
        )
    ).all()
    for wf in workflows:
        resources.append(
            {
                "uri": f"noodle://workflow/{wf.id}",
                "name": wf.name,
                "description": f"Workflow graph and metadata for '{wf.name}'.",
                "mimeType": "application/json",
            }
        )
    return resources


async def read_resource(session: AsyncSession, uri: str) -> dict[str, Any]:
    """Return {uri, mimeType, text}. Raises ValueError for unknown URIs."""
    if uri == "noodle://node-types":
        types = [
            {
                "id": m.id,
                "name": m.name,
                "category": m.category,
                "description": m.description,
            }
            for m in node_registry.manifests()
            if not m.hidden and not m.deprecated
        ]
        return {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps({"node_types": types}, ensure_ascii=False),
        }

    if uri.startswith("noodle://workflow/"):
        workflow_id = uri[len("noodle://workflow/"):]
        workflow = await session.get(
            Workflow,
            workflow_id,
            options=[selectinload(Workflow.versions)],
            populate_existing=True,
        )
        if workflow is None:
            raise ValueError(f"Resource not found: {uri!r}")
        graph = workflow.draft_graph
        if graph is None and workflow.versions:
            graph = workflow.versions[-1].graph or {"nodes": [], "edges": []}
        return {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(
                {
                    "id": workflow.id,
                    "name": workflow.name,
                    "active": workflow.active,
                    "published_version": workflow.published_version,
                    "graph": graph or {"nodes": [], "edges": []},
                },
                ensure_ascii=False,
                default=str,
            ),
        }

    raise ValueError(f"Unknown resource URI: {uri!r}")
```

- [ ] **Step 4: Update `protocol.py` to declare resources capability**

In `apps/api/app/mcp/protocol.py`, change:
```python
SERVER_CAPABILITIES: dict[str, Any] = {"tools": {"listChanged": False}}
```
to:
```python
SERVER_CAPABILITIES: dict[str, Any] = {
    "tools": {"listChanged": True},
    "resources": {"subscribe": False, "listChanged": False},
}
```

- [ ] **Step 5: Add `resources/list` and `resources/read` dispatch to `routers/mcp.py`**

Add to imports:
```python
from app.mcp.resources import list_resources, read_resource
```

In the `mcp_post` function, after the `tools/call` handler block and before the final "unknown method" return, add:

```python
    if method == "resources/list":
        resources = await list_resources(session)
        return JSONResponse(jsonrpc_result(req_id, {"resources": resources}))

    if method == "resources/read":
        uri = str(params.get("uri") or "")
        if not uri:
            return JSONResponse(jsonrpc_error(req_id, INVALID_REQUEST, "uri is required."))
        try:
            content = await read_resource(session, uri)
        except ValueError as exc:
            return JSONResponse(jsonrpc_error(req_id, METHOD_NOT_FOUND, str(exc)))
        return JSONResponse(jsonrpc_result(req_id, {"contents": [content]}))
```

- [ ] **Step 6: Run tests**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "resources" 2>&1 | tail -15
```
Expected: 6 PASSED.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/mcp/resources.py apps/api/app/mcp/protocol.py apps/api/app/routers/mcp.py apps/api/tests/test_mcp_server.py
git commit -m "feat(mcp): add Resources capability with node-types and workflow resources"
```

---

## Phase 6 — MCP Prompts Capability

### Task 11: `prompts.py` module + `prompts/list` and `prompts/get` in router

**Files:**
- **Create**: `apps/api/app/mcp/prompts.py`
- Modify: `apps/api/app/mcp/protocol.py`
- Modify: `apps/api/app/routers/mcp.py`
- Modify: `apps/api/tests/test_mcp_server.py`

**Interfaces:**
- Produces: `list_prompts() → list[dict]` with keys `{name, description, arguments}`
- Produces: `get_prompt(name, arguments) → dict` with keys `{description, messages}` or `None` if unknown

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run to confirm fail**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "prompts" 2>&1 | tail -15
```

- [ ] **Step 3: Create `apps/api/app/mcp/prompts.py`**

```python
"""MCP prompts registry — pre-built prompt templates for workflow operations."""

from typing import Any

_PROMPTS: list[dict[str, Any]] = [
    {
        "name": "build_workflow",
        "description": "Step-by-step guide for building a new Noodle workflow via the MCP builder tools.",
        "arguments": [
            {"name": "description", "description": "What the workflow should do.", "required": True}
        ],
    },
    {
        "name": "debug_run",
        "description": "Diagnostic guide for investigating a failed or stuck workflow run.",
        "arguments": [
            {"name": "run_id", "description": "The run ID to debug.", "required": True}
        ],
    },
    {
        "name": "optimize_workflow",
        "description": "Review guide for improving an existing workflow's reliability and structure.",
        "arguments": [
            {"name": "workflow_id", "description": "The workflow to optimize.", "required": True}
        ],
    },
]


def list_prompts() -> list[dict[str, Any]]:
    return _PROMPTS


def get_prompt(name: str, arguments: dict[str, str]) -> dict[str, Any] | None:
    """Return {description, messages} or None when name is unknown."""
    if name == "build_workflow":
        desc = arguments.get("description", "")
        text = (
            f"You are building a Noodle workflow. Goal: {desc}\n\n"
            "Steps:\n"
            "1. list_node_types — discover available node types.\n"
            "2. create_workflow — create the workflow.\n"
            "3. get_node_type — inspect each node's required params before placing it.\n"
            "4. add_node — add each node individually (safer than set_workflow_graph).\n"
            "5. add_edge — connect nodes in execution order.\n"
            "6. validate_graph — confirm the graph is structurally valid.\n"
            "7. run_workflow (use_draft=true) — test the draft.\n"
            "8. publish_workflow — publish once the run succeeds.\n"
        )
    elif name == "debug_run":
        run_id = arguments.get("run_id", "")
        text = (
            f"Debugging run {run_id!r}.\n\n"
            f"Steps:\n"
            f"1. get_run run_id={run_id!r} — check per-node statuses and error messages.\n"
            f"2. get_run_events run_id={run_id!r} — inspect the full event log for pre-execution errors.\n"
            f"3. Identify the failing node_id and its error text.\n"
            f"4. get_node_type on the failing node's type to review its required params.\n"
            f"5. patch_node to fix the params, then run_workflow use_draft=true to retry.\n"
        )
    elif name == "optimize_workflow":
        wf_id = arguments.get("workflow_id", "")
        text = (
            f"Optimizing workflow {wf_id!r}.\n\n"
            f"Steps:\n"
            f"1. get_workflow workflow_id={wf_id!r} — review the current graph.\n"
            f"2. get_workflow_stats workflow_id={wf_id!r} — check success rate and run counts.\n"
            f"3. list_runs workflow_id={wf_id!r} status=error — find recent failures.\n"
            f"4. get_run_events on a failed run to diagnose the root cause.\n"
            f"5. patch_node or add_node/remove_node to restructure as needed.\n"
            f"6. validate_graph to confirm changes, then publish_workflow.\n"
        )
    else:
        return None

    return {
        "description": next(p["description"] for p in _PROMPTS if p["name"] == name),
        "messages": [
            {"role": "user", "content": {"type": "text", "text": text}}
        ],
    }
```

- [ ] **Step 4: Update `protocol.py` to add prompts capability**

Change `SERVER_CAPABILITIES` to:
```python
SERVER_CAPABILITIES: dict[str, Any] = {
    "tools": {"listChanged": True},
    "resources": {"subscribe": False, "listChanged": False},
    "prompts": {"listChanged": False},
}
```

- [ ] **Step 5: Add `prompts/list` and `prompts/get` dispatch to `routers/mcp.py`**

Add to imports:
```python
from app.mcp.prompts import get_prompt, list_prompts
```

After the `resources/read` block, add:

```python
    if method == "prompts/list":
        return JSONResponse(jsonrpc_result(req_id, {"prompts": list_prompts()}))

    if method == "prompts/get":
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        result = get_prompt(name, {str(k): str(v) for k, v in arguments.items()})
        if result is None:
            return JSONResponse(
                jsonrpc_error(req_id, METHOD_NOT_FOUND, f"Unknown prompt: {name!r}")
            )
        return JSONResponse(jsonrpc_result(req_id, result))
```

- [ ] **Step 6: Run tests**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "prompts" 2>&1 | tail -15
```
Expected: 5 PASSED.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/mcp/prompts.py apps/api/app/mcp/protocol.py apps/api/app/routers/mcp.py apps/api/tests/test_mcp_server.py
git commit -m "feat(mcp): add Prompts capability with build_workflow, debug_run, optimize_workflow templates"
```

---

## Phase 7 — MCP Client Node Enhancements

### Task 12: `mcp_list_resources` and `mcp_read_resource` client nodes

**Files:**
- Modify: `packages/nodes/noodle_nodes/ai_v2/mcp.py`
- Modify: `apps/api/tests/test_mcp_server.py`

**Interfaces:**
- Produces: `mcp_list_resources(credentials) → list[{uri, name, description, mime_type}]`
- Produces: `mcp_read_resource(credentials, resource_uri) → Any` (parsed JSON or raw text)

- [ ] **Step 1: Write failing tests**

Add loopback helpers to support resources. The `_LoopbackSession` class in the test file needs `list_resources` and `read_resource` methods. Add them after the existing `call_tool` method:

```python
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
```

Add tests:

```python
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
```

- [ ] **Step 2: Run to confirm fail**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "list_resources or read_resource" 2>&1 | tail -10
```

- [ ] **Step 3: Implement nodes in `packages/nodes/noodle_nodes/ai_v2/mcp.py`**

Add after `mcp_list_tools`:

```python
@node(
    name="MCP List Resources",
    id="mcp_list_resources",
    category=AI_CATEGORY,
    icon="ai",
    tool_side_effecting=False,
    params={"credentials": _CREDENTIAL_META},
)
async def mcp_list_resources(input: Any = None, credentials: Any = None) -> list[dict]:
    """List the resources an external MCP server exposes."""
    config = _config_from_credentials(credentials)
    async with _mcp_session(config) as session:
        listing = await session.list_resources()
    return [
        {
            "uri": str(r.uri),
            "name": getattr(r, "name", "") or "",
            "description": getattr(r, "description", "") or "",
            "mime_type": getattr(r, "mimeType", "") or "",
        }
        for r in listing.resources
    ]


@node(
    name="MCP Read Resource",
    id="mcp_read_resource",
    category=AI_CATEGORY,
    icon="ai",
    tool_side_effecting=False,
    params={
        "credentials": _CREDENTIAL_META,
        "resource_uri": {
            "description": "URI of the resource to read (e.g. noodle://workflow/abc123).",
        },
    },
)
async def mcp_read_resource(
    input: Any = None,
    credentials: Any = None,
    resource_uri: str = "",
) -> Any:
    """Read a resource from an external MCP server by URI."""
    uri = str(resource_uri or "").strip()
    if not uri and isinstance(input, str):
        uri = input.strip()
    if not uri:
        raise ValueError("mcp_read_resource: resource_uri is required")
    config = _config_from_credentials(credentials)
    async with _mcp_session(config) as session:
        result = await session.read_resource(uri)
    contents = getattr(result, "contents", None) or []
    if not contents:
        return None
    item = contents[0]
    text = getattr(item, "text", None)
    if text is not None:
        try:
            return json.loads(text)
        except ValueError:
            return text
    blob = getattr(item, "blob", None)
    if blob is not None:
        return {"_blob": True, "data": blob, "mime_type": getattr(item, "mimeType", "") or ""}
    return None
```

- [ ] **Step 4: Run tests**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "list_resources or read_resource" 2>&1 | tail -10
```
Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/ai_v2/mcp.py apps/api/tests/test_mcp_server.py
git commit -m "feat(mcp): add mcp_list_resources and mcp_read_resource client nodes"
```

---

### Task 13: `mcp_get_prompt` node + image content handling

**Files:**
- Modify: `packages/nodes/noodle_nodes/ai_v2/mcp.py`
- Modify: `apps/api/tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `_LoopbackSession.get_prompt()` added to test helpers
- Produces: `mcp_get_prompt(credentials, prompt_name, prompt_arguments?) → {description, messages}`
- Fixes: `_result_to_text` silently drops image content → replace with `_result_to_value` that returns image dicts

- [ ] **Step 1: Add `get_prompt` to `_LoopbackSession` in tests**

After `read_resource` in `_LoopbackSession`:

```python
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
```

- [ ] **Step 2: Write failing tests**

```python
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
```

- [ ] **Step 3: Run to confirm fail**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "get_prompt or image_result" 2>&1 | tail -10
```

- [ ] **Step 4: Implement in `packages/nodes/noodle_nodes/ai_v2/mcp.py`**

**4a: Replace `_result_to_text` with `_result_to_value`** (rename + expand):

Remove the existing `_result_to_text` function and replace it:

```python
def _result_to_value(result: Any) -> Any:
    """Convert an MCP tool result to a Python value.

    Handles text, image content items, and structuredContent.
    Returns image dicts for image content so callers don't silently lose data.
    """
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    items: list[Any] = []
    for item in getattr(result, "content", None) or []:
        if getattr(item, "type", None) == "image":
            items.append(
                {
                    "_image": True,
                    "data": getattr(item, "data", "") or "",
                    "mime_type": getattr(item, "mimeType", "") or "",
                }
            )
        else:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                items.append(text)
    if len(items) == 1:
        return items[0]
    return items if items else ""
```

**4b: Update `McpToolAdapter.invoke_async`** to use `_result_to_value`:

```python
    async def invoke_async(self, arguments: dict[str, Any]) -> str:
        try:
            session = await self._ensure_session()
            result = await session.call_tool(self._schema.name, dict(arguments or {}))
        except Exception:
            if self._exit_stack is not None:
                try:
                    await self._exit_stack.aclose()
                except Exception:
                    pass
            self._session = None
            self._exit_stack = None
            raise
        if getattr(result, "isError", False):
            value = _result_to_value(result)
            text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
            raise RuntimeError(text or f"{self._schema.name}: tool returned an error")
        value = _result_to_value(result)
        return json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
```

**4c: Update `mcp_call_tool`** to use `_result_to_value`:

Replace the `text = _result_to_text(result)` line (and the JSON parse logic after it) with:

```python
    value = _result_to_value(result)
    if getattr(result, "isError", False):
        text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
        raise RuntimeError(text or f"{name}: tool returned an error")
    return value
```

Remove the old `try: return json.loads(text)` block that followed.

**4d: Add `mcp_get_prompt` node:**

```python
@node(
    name="MCP Get Prompt",
    id="mcp_get_prompt",
    category=AI_CATEGORY,
    icon="ai",
    tool_side_effecting=False,
    params={
        "credentials": _CREDENTIAL_META,
        "prompt_name": {"description": "Name of the prompt to retrieve."},
        "prompt_arguments": {
            "widget": "code",
            "description": "Prompt arguments as a JSON object.",
        },
    },
)
async def mcp_get_prompt(
    input: Any = None,
    credentials: Any = None,
    prompt_name: str = "",
    prompt_arguments: Any = None,
) -> Any:
    """Retrieve a named prompt template from an external MCP server."""
    name = str(prompt_name or "").strip()
    if not name:
        raise ValueError("mcp_get_prompt: prompt_name is required")
    config = _config_from_credentials(credentials)
    args = _parse_arguments(prompt_arguments) if prompt_arguments else {}
    async with _mcp_session(config) as session:
        result = await session.get_prompt(name, args or None)
    messages = getattr(result, "messages", None) or []
    return {
        "description": getattr(result, "description", "") or "",
        "messages": [
            {
                "role": getattr(m, "role", "") or "",
                "content": (
                    getattr(getattr(m, "content", None), "text", None)
                    or str(getattr(m, "content", "") or "")
                ),
            }
            for m in messages
        ],
    }
```

- [ ] **Step 5: Run tests**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "get_prompt or image_result" 2>&1 | tail -10
```
Expected: 2 PASSED.

- [ ] **Step 6: Run full MCP test suite to check no regressions**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v 2>&1 | tail -20
```
Expected: all previous tests still pass.

- [ ] **Step 7: Commit**

```bash
git add packages/nodes/noodle_nodes/ai_v2/mcp.py apps/api/tests/test_mcp_server.py
git commit -m "feat(mcp): add mcp_get_prompt node; fix image content handling in client nodes"
```

---

## Phase 8 — Infrastructure

### Task 14: Rate limiting on `POST /mcp`

**Files:**
- Modify: `apps/api/app/routers/mcp.py`
- Modify: `apps/api/tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `allow(bucket, identifier, *, limit, window_seconds) -> bool` from `app.services.rate_limit`
- Limit: 120 requests per user (or IP for unauthenticated) per 60 seconds

- [ ] **Step 1: Write failing test**

```python
async def test_mcp_rate_limit(client: AsyncClient, monkeypatch) -> None:
    """After exceeding the per-IP rate limit, /mcp returns 429."""
    import app.routers.mcp as mcp_router

    call_count = 0

    def _deny_after_one(bucket, identifier, *, limit, window_seconds):
        nonlocal call_count
        call_count += 1
        return call_count <= 1  # first call allowed, rest denied

    monkeypatch.setattr(mcp_router, "_rate_allow", _deny_after_one)

    # first call passes
    r1 = await client.post("/mcp", json=rpc("ping"))
    assert r1.status_code == 200

    # second call is rate-limited
    r2 = await client.post("/mcp", json=rpc("ping"))
    assert r2.status_code == 429
```

- [ ] **Step 2: Run to confirm fail**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "rate_limit" 2>&1 | tail -10
```

- [ ] **Step 3: Implement in `routers/mcp.py`**

Add to imports:
```python
from app.services.rate_limit import allow as _rate_allow
```

In `mcp_post`, after the user resolution block (after the `elif settings.auth_required:` block) and before the JSON parse section, add:

```python
    # --- rate limiting ---
    identifier = user.id if user else (request.client.host if request.client else "anon")
    if not _rate_allow("mcp", identifier, limit=120, window_seconds=60):
        return Response(status_code=429, headers={"Retry-After": "60"})
```

- [ ] **Step 4: Run tests**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "rate_limit" 2>&1 | tail -10
```
Expected: 1 PASSED.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/routers/mcp.py apps/api/tests/test_mcp_server.py
git commit -m "feat(mcp): add rate limiting (120 req/min per user/IP) on POST /mcp"
```

---

### Task 15: Update `tools/list` to include tool count in `listChanged` capability

`listChanged: True` in `SERVER_CAPABILITIES` (set in Task 10) already tells clients they should re-list tools when they reconnect. No additional server-side push is needed for stateless HTTP transport. This task validates the flag is reflected correctly in `initialize`.

**Files:**
- Modify: `apps/api/tests/test_mcp_server.py`

- [ ] **Step 1: Add assertion to existing capabilities test**

In `test_initialize`, add after the `assert "tools" in result["capabilities"]` line:

```python
    assert result["capabilities"]["tools"]["listChanged"] is True
    assert "resources" in result["capabilities"]
    assert "prompts" in result["capabilities"]
```

- [ ] **Step 2: Run**

```
cd apps/api && python -m pytest tests/test_mcp_server.py::test_initialize -v 2>&1 | tail -10
```
Expected: PASSED (already set in Task 10).

- [ ] **Step 3: Commit**

```bash
git add apps/api/tests/test_mcp_server.py
git commit -m "test(mcp): assert listChanged=True and all three capabilities in initialize response"
```

---

### Task 16: Batch JSON-RPC support

**Files:**
- Modify: `apps/api/app/routers/mcp.py`
- Modify: `apps/api/tests/test_mcp_server.py`

**Interfaces:**
- Batch input: JSON array of JSON-RPC request objects
- Batch output: JSON array of responses (notifications omitted, as per spec)
- Existing test `test_batch_rejected` must be updated to expect success

- [ ] **Step 1: Update the batch-rejected test to reflect new behavior**

In `test_mcp_server.py`, replace `test_batch_rejected`:

```python
async def test_batch_requests(client: AsyncClient) -> None:
    """Batch of two requests returns two responses."""
    resp = await client.post(
        "/mcp",
        json=[
            rpc("ping", req_id=1),
            rpc("ping", req_id=2),
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 2
    ids = {item["id"] for item in body}
    assert ids == {1, 2}


async def test_batch_with_notification(client: AsyncClient) -> None:
    """Notifications in a batch produce no response entry."""
    resp = await client.post(
        "/mcp",
        json=[
            rpc("ping", req_id=1),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},  # no id
        ],
    )
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["id"] == 1


async def test_batch_all_notifications(client: AsyncClient) -> None:
    """A batch of only notifications returns 202."""
    resp = await client.post(
        "/mcp",
        json=[{"jsonrpc": "2.0", "method": "notifications/initialized"}],
    )
    assert resp.status_code == 202
```

- [ ] **Step 2: Run to confirm the old test was the only thing checking batch**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "batch" 2>&1 | tail -15
```
Expected: `test_batch_requests` and `test_batch_with_notification` FAIL; `test_batch_all_notifications` FAIL.

- [ ] **Step 3: Refactor `routers/mcp.py` to extract `_dispatch_single`**

The `mcp_post` handler's inner logic needs to be callable for each batch item. Extract it:

```python
async def _dispatch_single(
    body: dict,
    session: AsyncSession,
    user: User | None,
    request: Request,
) -> dict | None:
    """Handle one JSON-RPC message. Returns response dict or None for notifications."""
    if not isinstance(body, dict) or not isinstance(body.get("method"), str):
        return jsonrpc_error(None, INVALID_REQUEST, "Expected a JSON-RPC request object.")

    method = body["method"]
    params = body.get("params") if isinstance(body.get("params"), dict) else {}

    if "id" not in body:
        return None  # notification — no response

    req_id = body.get("id")

    if method == "initialize":
        return jsonrpc_result(req_id, initialize_result(params.get("protocolVersion")))
    if method == "ping":
        return jsonrpc_result(req_id, {})
    if method == "tools/list":
        tools = [tool.descriptor() for tool in STATIC_TOOLS]
        tools.extend(await list_workflow_tool_descriptors(session))
        return jsonrpc_result(req_id, {"tools": tools})
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        try:
            tool = get_tool(name)
            if tool is not None:
                await _check_permission(session, user, tool.permission)
                payload = await tool.handler(session, user, arguments)
                return jsonrpc_result(req_id, tool_result(payload))
            await _check_permission(session, user, "workflow:run")
            payload = await call_workflow_tool(session, user, name, arguments)
            if payload is not None:
                return jsonrpc_result(req_id, tool_result(payload))
            return jsonrpc_error(req_id, METHOD_NOT_FOUND, f"Unknown tool: {name}")
        except McpToolError as exc:
            return jsonrpc_result(req_id, tool_result(str(exc), is_error=True))
        except Exception as exc:  # noqa: BLE001
            logger.exception("mcp tool %s failed", name)
            return jsonrpc_result(
                req_id, tool_result(f"{type(exc).__name__}: {exc}", is_error=True)
            )
    if method == "resources/list":
        resources = await list_resources(session)
        return jsonrpc_result(req_id, {"resources": resources})
    if method == "resources/read":
        uri = str(params.get("uri") or "")
        if not uri:
            return jsonrpc_error(req_id, INVALID_REQUEST, "uri is required.")
        try:
            content = await read_resource(session, uri)
        except ValueError as exc:
            return jsonrpc_error(req_id, METHOD_NOT_FOUND, str(exc))
        return jsonrpc_result(req_id, {"contents": [content]})
    if method == "prompts/list":
        return jsonrpc_result(req_id, {"prompts": list_prompts()})
    if method == "prompts/get":
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        result = get_prompt(name, {str(k): str(v) for k, v in arguments.items()})
        if result is None:
            return jsonrpc_error(req_id, METHOD_NOT_FOUND, f"Unknown prompt: {name!r}")
        return jsonrpc_result(req_id, result)

    return jsonrpc_error(req_id, METHOD_NOT_FOUND, f"Unknown method: {method}")
```

Then replace the `mcp_post` body (after auth + rate-limit) with:

```python
    # --- parse ---
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(jsonrpc_error(None, PARSE_ERROR, "Invalid JSON."))

    # --- batch ---
    if isinstance(body, list):
        if not body:
            return Response(status_code=202)
        responses: list[dict] = []
        for item in body:
            result = await _dispatch_single(item, session, user, request)
            if result is not None:
                responses.append(result)
        if not responses:
            return Response(status_code=202)
        return JSONResponse(responses)

    # --- single ---
    if not isinstance(body, dict) or not isinstance(body.get("method"), str):
        return JSONResponse(
            jsonrpc_error(None, INVALID_REQUEST, "Expected a JSON-RPC request object.")
        )
    if "id" not in body:
        return Response(status_code=202)

    result = await _dispatch_single(body, session, user, request)
    return JSONResponse(result)
```

- [ ] **Step 4: Run tests**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v -k "batch" 2>&1 | tail -15
```
Expected: 3 PASSED.

- [ ] **Step 5: Run full suite**

```
cd apps/api && python -m pytest tests/test_mcp_server.py -v 2>&1 | tail -20
```
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/routers/mcp.py apps/api/tests/test_mcp_server.py
git commit -m "feat(mcp): add batch JSON-RPC support; refactor router into _dispatch_single"
```

---

## Final Verification

- [ ] Run full MCP test suite:
  ```
  cd apps/api && python -m pytest tests/test_mcp_server.py -v 2>&1 | tail -30
  ```

- [ ] Run full API test suite to check for regressions:
  ```
  cd apps/api && python -m pytest tests/ -x -q 2>&1 | tail -20
  ```

- [ ] Run packages tests:
  ```
  cd packages/nodes && python -m pytest tests/ -q -k "mcp" 2>&1 | tail -10
  ```

- [ ] Verify tools/list now returns all 26 tools:
  ```python
  # Quick smoke check
  import httpx, asyncio
  async def check():
      async with httpx.AsyncClient(base_url="http://localhost:8000") as c:
          r = await c.post("/mcp", json={"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}})
          tools = r.json()["result"]["tools"]
          print(f"Total tools: {len(tools)}")
          for t in sorted(tools, key=lambda x: x["name"]):
              print(f"  {t['name']}")
  asyncio.run(check())
  ```
  Expected: 26 tools listed (10 original + 16 new).

---

## Summary: Tools Added

| Tool | Phase | Permission |
|------|-------|------------|
| `list_runs` | 1 | None |
| `get_run_events` | 1 | None |
| `cancel_run` | 1 | workflow:run |
| `get_workflow_stats` | 1 | None |
| `patch_node` | 2 | workflow:write |
| `add_node` | 2 | workflow:write |
| `remove_node` | 2 | workflow:write |
| `add_edge` | 2 | workflow:write |
| `remove_edge` | 2 | workflow:write |
| `delete_workflow` | 3 | workflow:write |
| `duplicate_workflow` | 3 | workflow:write |
| `toggle_workflow` | 3 | workflow:write |
| `list_workflow_versions` | 3 | None |
| `rollback_workflow` | 3 | workflow:write |
| `list_schedules` | 4 | None |
| `create_schedule` | 4 | workflow:write |
| `delete_schedule` | 4 | workflow:write |
| `toggle_schedule` | 4 | workflow:write |

**Protocol additions:** Resources capability (2 methods), Prompts capability (2 methods), `listChanged: True`, batch JSON-RPC.

**Client nodes added:** `mcp_list_resources`, `mcp_read_resource`, `mcp_get_prompt`.

**Client nodes fixed:** `mcp_call_tool` now surfaces image content instead of dropping it silently.
