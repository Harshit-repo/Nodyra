# Phase 4 — P1 Product Features (Audit-Prioritized)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the P0/P1 product items from the 2026-07-02 audit: MCP agent-quickstart docs (the marketing P0), manifest-driven AI-builder vocabulary, replay-from-node in the UI, MCP tool-call traces, a template gallery, per-workflow requirements, and a HuggingFace inference provider.

**Architecture:** Each task is an independent vertical slice (backend + tests, and UI where relevant) touching existing seams: the node registry (`node_registry.manifests()`), the run replay endpoint (already built), the integrations_v2 provider framework, and the React Query + zustand editor.

**Tech Stack:** FastAPI/SQLAlchemy/alembic, React 18 + React Query + vitest, integrations_v2 provider specs.

**Parent plan:** `docs/superpowers/plans/2026-07-02-nodyra-master-roadmap.md`
**Prerequisite:** Phase 2 merged — all names below are post-rename (`nodyra_nodes`, `nodyra://`, package `nodyra`). Tasks are independent; execute in any order, one branch + PR per task.

## Global Constraints

- Backend tests: `uv run pytest <paths>` green per task; `uv run ruff check <touched>` clean.
- Web: `npm run typecheck && npm test -- --run` green per task; follow existing patterns (React Query hooks in `apps/web/src/queries/`, native dialogs, existing CSS files — no new styling systems).
- No new npm dependencies in this phase. No new Python dependencies except Task 6 may add `packaging>=24` as a direct API dependency if it is not already present; requirement parsing is runtime API behavior and must not rely on a transitive dependency.
- UI text uses the product name **Nodyra**.
- Never `git add -A`; stage per task. Commits end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1 (P0): MCP agent quickstart — docs + Settings surfacing

The audit's highest-leverage item: the 45-tool MCP server exists; nobody is told. Deliver a copy-pasteable quickstart and a pointer card in Settings.

**Files:**
- Create: `docs/mcp-quickstart.md`
- Modify: `README.md`, `apps/web/src/SettingsPage.tsx`
- Test: extend the SettingsPage test file (find: `ls apps/web/src | grep -i settings`)

- [ ] **Step 1: Write `docs/mcp-quickstart.md`:**

````markdown
# Build Nodyra workflows with Claude (or any MCP agent)

Nodyra ships a full MCP server: 45 tools covering incremental graph editing
(add/patch/remove nodes and edges with optimistic concurrency), validation,
publishing, runs, schedules, and environments. Point an MCP-capable agent at
your instance and it can build, test, and deploy workflows you can watch
live on the canvas.

## 1. Create an API token

Settings → API tokens → New token. Scopes: `workflow:read`,
`workflow:write`, `workflow:run` (add `deployment:manage` if the agent
should publish). Copy the `ndpat_...` value.

## 2. Connect your agent

**Claude Code**

```bash
claude mcp add --transport http nodyra https://YOUR-INSTANCE/mcp \
  --header "Authorization: Bearer ndpat_YOUR_TOKEN"
```

**Claude Desktop / any JSON-config client** (`mcpServers` entry):

```json
{
  "mcpServers": {
    "nodyra": {
      "type": "http",
      "url": "https://YOUR-INSTANCE/mcp",
      "headers": { "Authorization": "Bearer ndpat_YOUR_TOKEN" }
    }
  }
}
```

**Cursor** (`.cursor/mcp.json`): same JSON shape as above.

## 3. Try it

Ask the agent:

> Using the nodyra tools, create a workflow called "PR digest" that runs
> every morning at 9, fetches open GitHub PRs, summarizes them with an LLM
> node, and posts the digest to Slack. Validate it and run it once with
> test data.

Open the workflow in Nodyra while the agent works — edits stream onto the
canvas, every change is journaled with revision history, and each run is
inspectable node-by-node.

## Notes for operators

- Tools enforce the token's scopes and role; the agent can never exceed the
  PAT you minted.
- All tool calls hit the audit log like any API call.
- Rate limit: 120 requests/min per principal on `/mcp`.
- If `auth_required=false` (open self-hosted instance), read-only MCP tools
  are anonymous by design — front `/mcp` with auth before exposing it
  publicly.
````

Verify the scope names against the real scope list (`grep -rn "workflow:run\|workflow:write" apps/api/app/security.py | head -5`) and the rate limit against `apps/api/app/routers/mcp.py` (`grep -n "120" apps/api/app/routers/mcp.py`); correct the doc if they differ.

- [ ] **Step 2: README section.** Add near the top of `README.md` (this is the wedge feature — placement matters, right after the intro):

```markdown
## Let Claude build your workflows

Nodyra has a first-class MCP server. Connect Claude Code in one line:

    claude mcp add --transport http nodyra https://your-instance/mcp \
      --header "Authorization: Bearer <api-token>"

…then ask for the workflow you want and watch it appear on the canvas —
editable, testable, and deployable. [Full guide](docs/mcp-quickstart.md).
```

- [ ] **Step 3: Settings card.** In `apps/web/src/SettingsPage.tsx`, find the MCP connections section (`grep -n -i "mcp" apps/web/src/SettingsPage.tsx`). Above or beside it, add a static help card following the page's existing section markup (reuse the surrounding section/card classNames verbatim):

```tsx
function McpAgentQuickstart() {
  const snippet = `{
  "mcpServers": {
    "nodyra": {
      "type": "http",
      "url": "${window.location.origin}/mcp",
      "headers": { "Authorization": "Bearer <your-api-token>" }
    }
  }
}`;
  return (
    <section className="settings-section">
      <h2>Connect an AI agent</h2>
      <p>
        Claude, Cursor, or any MCP client can build and run workflows on this
        instance. Create an API token above, then add this server config:
      </p>
      <pre className="settings-code">{snippet}</pre>
      <button
        type="button"
        onClick={() => navigator.clipboard.writeText(snippet)}
      >
        Copy config
      </button>
    </section>
  );
}
```

Adjust the two classNames to the file's real conventions (check siblings). Render `<McpAgentQuickstart />` adjacent to the MCP connections section.

- [ ] **Step 4: Test.** In the SettingsPage test file, add:

```tsx
it("shows the MCP agent quickstart with the instance URL", async () => {
  renderSettingsPage(); // reuse the file's existing render helper
  expect(await screen.findByText("Connect an AI agent")).toBeInTheDocument();
  expect(screen.getByText(/mcpServers/)).toBeInTheDocument();
});
```

Run in `apps/web`: `npm test -- --run Settings` → PASS; `npm run typecheck` → clean.

- [ ] **Step 5: Commit**

```bash
git add docs/mcp-quickstart.md README.md apps/web/src/SettingsPage.tsx <settings test file>
git commit -m "feat: MCP agent quickstart (docs, README wedge, Settings card)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2 (P1): AI-builder vocabulary — manifest-driven allowlist

Today `_ALLOWED_NODE_TYPES` in `apps/api/app/services/ai_builder.py:33` hardcodes ~60 of ~400 types; the builder can't place most of the library (including the existing telegram/discord/microsoft_teams providers). Derive the set from the registry.

**Files:**
- Modify: `apps/api/app/services/ai_builder.py` (allowlist + prompt catalog), same pattern in `agentic_builder.py` if it duplicates the set (`grep -n "_ALLOWED_NODE_TYPES" apps/api/app/services/agentic_builder.py`)
- Test: the AI-builder test file (find: `grep -rln "_ALLOWED_NODE_TYPES\|ai_builder" apps/api/tests | head -3`)

**Interfaces:**
- Consumes: `node_registry.manifests() -> list[NodeManifest]` (`from nodyra.sdk import registry`; manifests have `.id`, `.name`, and a description field — confirm its attribute name with `grep -n "description" packages/core/nodyra/sdk.py | head -5`), and `UNCONDITIONAL_UNSAFE: dict[str, str]` from `app.services.unsafe_nodes:30`.
- Produces: `allowed_node_types() -> frozenset[str]` and `node_catalog_for_prompt(max_chars: int = 12000) -> str` in `ai_builder.py`.

- [ ] **Step 1: Write the failing tests** (append to the AI-builder test file):

```python
def test_allowed_node_types_is_manifest_driven() -> None:
    from app.services.ai_builder import allowed_node_types

    allowed = allowed_node_types()
    # The old hardcoded list capped out around 60; the registry has ~400.
    assert len(allowed) > 300
    # Existing providers the static list excluded:
    assert "telegram_send_message_v2" in allowed or any(
        t.startswith("telegram_") for t in allowed
    )
    # Code stays allowed (it always was) but host-command execution never is:
    assert "code" in allowed
    assert "execute_command" not in allowed
    assert "ssh_execute" not in allowed


def test_node_catalog_for_prompt_is_bounded() -> None:
    from app.services.ai_builder import node_catalog_for_prompt

    catalog = node_catalog_for_prompt(max_chars=4000)
    assert len(catalog) <= 4000
    assert "http_request" in catalog
```

Run: `uv run pytest <ai builder test file> -k "manifest_driven or catalog" -v` → FAIL.

- [ ] **Step 2: Implement in `ai_builder.py`.** Replace the `_ALLOWED_NODE_TYPES = { ... }` literal with:

```python
def allowed_node_types() -> frozenset[str]:
    """Every registered node type the builder may propose.

    Manifest-driven so new providers are automatically buildable. Exclusions
    are the host-command tier of ``UNCONDITIONAL_UNSAFE`` — ``code`` stays
    allowed (it always was; activation still passes the unsafe-node approval
    gates), but nothing that runs arbitrary host commands or author SQL/
    Python outside the code-node sandbox path.
    """
    from nodyra.sdk import registry as node_registry

    from app.services.unsafe_nodes import UNCONDITIONAL_UNSAFE

    excluded = set(UNCONDITIONAL_UNSAFE) - {"code"}
    return frozenset(
        m.id for m in node_registry.manifests() if m.id not in excluded
    )


def node_catalog_for_prompt(max_chars: int = 12000) -> str:
    """Compact `type — name` catalog injected into the builder prompt so the
    model knows the real vocabulary. Truncates deterministically (sorted) at
    *max_chars*."""
    from nodyra.sdk import registry as node_registry

    allowed = allowed_node_types()
    lines = sorted(
        f"{m.id} — {m.name}" for m in node_registry.manifests() if m.id in allowed
    )
    out: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > max_chars:
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)
```

Update the validation at ~line 411 from `if node.type not in _ALLOWED_NODE_TYPES:` to `if node.type not in allowed_node_types():` (compute once per request into a local, not per node). Find where the builder's system prompt enumerates node types (`grep -n "node type" apps/api/app/services/ai_builder.py`) and inject `node_catalog_for_prompt()` there instead of any hardcoded enumeration. Apply the same swap in `agentic_builder.py` if it has its own copy.

- [ ] **Step 3: Run the builder suites**

Run: `uv run pytest apps/api/tests -k "ai_builder or agentic" -q`
Expected: PASS. Existing tests asserting a *rejected* type may now pass validation — if a test asserted e.g. `telegram` was rejected purely because of the old list, invert it to assert acceptance; if it asserted `execute_command` rejection, it must still pass.

- [ ] **Step 4: Ruff + commit**

```bash
uv run ruff check apps/api/app/services/ai_builder.py apps/api/app/services/agentic_builder.py
git add apps/api/app/services/ai_builder.py apps/api/app/services/agentic_builder.py <test file>
git commit -m "feat(ai-builder): manifest-driven node vocabulary (~60 -> ~400 types)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3 (P1): Replay-from-node button (backend is done — surface it)

`POST /runs/{run_id}/replay` already accepts `from_node_id` (`apps/api/app/routers/runs.py:403`). Add the UI affordance on failed node rows.

**Files:**
- Modify: `apps/web/src/api.ts` (add `replayRun`), the run-detail node row component (find it: `grep -rn "node_runs" apps/web/src/editor --include='*.tsx' -l` — likely `WorkflowHistory.tsx` or a run sidecar component), and the queries file that owns run invalidation (`grep -rn "runs" apps/web/src/queries/index.ts | head`)
- Test: colocated component test

- [ ] **Step 1: API helper in `api.ts`** (match the file's existing fetch-wrapper style — read 2–3 neighboring functions first and mirror them exactly):

```ts
export async function replayRun(
  runId: string,
  fromNodeId?: string,
): Promise<{ run_id: string }> {
  return request(`/runs/${runId}/replay`, {
    method: "POST",
    body: JSON.stringify(fromNodeId ? { from_node_id: fromNodeId } : {}),
  });
}
```

(`request` = whatever the file's shared helper is named; the response shape is `RunReplayResponse` — confirm its fields with `grep -n "class RunReplayResponse" -A 5 apps/api/app/schemas.py` and type it accordingly.)

- [ ] **Step 2: Mutation hook** in the queries module, mirroring an existing mutation:

```ts
export function useReplayRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ runId, fromNodeId }: { runId: string; fromNodeId?: string }) =>
      replayRun(runId, fromNodeId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["runs"] });
    },
  });
}
```

Match the file's real query-key convention (`grep -n "queryKey" apps/web/src/queries/index.ts | head -10`) — invalidate whatever key run lists/details actually use.

- [ ] **Step 3: Button in the node row.** In the run-detail node list, render on rows whose `status === "error"`:

```tsx
{nodeRun.status === "error" && (
  <button
    type="button"
    className="node-run-replay"
    title="Re-run from this node, reusing upstream outputs"
    onClick={() => replayMutation.mutate({ runId: run.id, fromNodeId: nodeRun.node_id })}
    disabled={replayMutation.isPending}
  >
    Replay from here
  </button>
)}
```

Reuse the row's existing button styling class if one exists; otherwise add `.node-run-replay` to the CSS file the component already imports, matching neighboring button rules.

- [ ] **Step 4: Component test** (mirror the structure of an existing test in the same directory — same render helpers/mocks):

```tsx
it("replays from a failed node", async () => {
  // render run detail with node_runs: [{node_id: "n2", status: "error", ...}]
  // click "Replay from here"
  // assert fetch/mock called with /runs/<id>/replay and {from_node_id: "n2"}
});
```

Fill in using the file's existing mock-server pattern (msw or fetch mock — copy a neighboring test verbatim and adapt).

- [ ] **Step 5:** Run in `apps/web`: `npm run typecheck && npm test -- --run` → clean/green.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src
git commit -m "feat(web): replay-from-node button on failed node runs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4 (P1): MCP tool-call trace in run debug

Agent-driven runs call external MCP tools through `mcp_tool` nodes; failures are currently opaque. Attach a structured trace at the managed `mcp_tool` node boundary around `ctx.call_mcp_tool(...)`; the existing DataPanel then shows it with zero UI work (it renders output JSON), plus an optional dedicated section.

**Files:**
- Modify: `packages/nodes/nodyra_nodes/mcp_tool.py`
- Test: the mcp_tool node test file (find: `grep -rln "mcp_tool" packages/nodes/tests apps/api/tests | head -3`)

**Interfaces:**
- Produces: node output gains `"_mcp_trace": {"connection_id": str, "tool": str, "duration_ms": int, "arguments_preview": str, "result_preview": str, "is_error": bool}`. If the original tool result is a dict, merge `_mcp_trace` into it; otherwise return `{"result": <original>, "_mcp_trace": ...}` so every successful call has a stable trace location. Previews are JSON dumps truncated to 2000 chars; secrets never appear because connection headers/credentials are not part of arguments.

- [ ] **Step 1: Read `packages/nodes/nodyra_nodes/mcp_tool.py`** to find the execute function: where it calls the MCP client (`call_tool`) and what it currently returns.

- [ ] **Step 2: Failing test** (append to the node's test file, reusing its existing fake-client fixture):

```python
def test_mcp_tool_output_includes_trace(...existing fixture args...) -> None:
    # invoke the node exactly as the neighbouring happy-path test does
    result = <existing invocation>
    trace = result["_mcp_trace"]
    assert trace["tool"] == "<tool name the fixture uses>"
    assert trace["is_error"] is False
    assert trace["duration_ms"] >= 0
    assert len(trace["arguments_preview"]) <= 2000
```

Run → FAIL (`KeyError: '_mcp_trace'`).

- [ ] **Step 3: Implement.** In the execute path, around the `call_tool` invocation:

```python
    import json as _json
    import time as _time

    def _preview(obj: object, limit: int = 2000) -> str:
        try:
            s = _json.dumps(obj, default=str)
        except (TypeError, ValueError):
            s = repr(obj)
        return s[:limit]

    started = _time.monotonic()
    # ... existing call_tool(...) ...
    duration_ms = int((_time.monotonic() - started) * 1000)
```

and merge into the returned output dict:

```python
    output = result if isinstance(result, dict) else {"result": result}
    output["_mcp_trace"] = {
        "connection_id": conn_id,
        "tool": tool_name,
        "duration_ms": duration_ms,
        "arguments_preview": _preview(arguments),
        "result_preview": _preview(result),
        "is_error": bool(is_error),
    }
```

On the error path (tool returned `isError` or the call raised), include the same trace in the raised error message as JSON if the node framework has no structured error-output channel. Add one test for success trace and one test proving a raised MCP error includes `"_mcp_trace"` with `"is_error": true`; do not call the task complete with trace-on-success only.

- [ ] **Step 4:** Run: `uv run pytest packages/nodes/tests -k "mcp_tool" -q && uv run pytest apps/api/tests -k "mcp" -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/nodyra_nodes/mcp_tool.py <test file>
git commit -m "feat(mcp): structured _mcp_trace on mcp_tool node outputs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5 (P1): Template gallery at create-time

**Files:**
- Create: `apps/api/app/data/templates/__init__.py` (empty), `apps/api/app/data/templates/webhook_to_slack.json`, `apps/api/app/data/templates/daily_report_email.json`, `apps/api/app/data/templates/api_poll_transform.json`
- Create: `apps/api/app/routers/templates.py`
- Modify: `apps/api/app/main.py` (register router — mirror how a small router like `export` is included)
- Create: `apps/api/tests/test_templates.py`
- Modify (web): the create-workflow entry point (find: `grep -rn "New workflow\|createWorkflow" apps/web/src --include='*.tsx' -l | head -3`), `apps/web/src/api.ts`, queries module
- Test (web): colocated gallery test

**Interfaces:**
- Produces API: `GET /templates` → `[{id, name, description, tags}]`; `POST /templates/{template_id}/instantiate` `{"name": str}` → `201 {"id": <workflow id>, "name": str}` (requires `workflow:write`).

- [ ] **Step 1: Template JSONs.** Each file: `{"id", "name", "description", "tags", "graph": {"nodes": [...], "edges": [...]}}` using only real node types/params. `webhook_to_slack.json`:

```json
{
  "id": "webhook_to_slack",
  "name": "Webhook → Slack alert",
  "description": "Receive a JSON webhook, format a message, post it to Slack.",
  "tags": ["alerting", "starter"],
  "graph": {
    "nodes": [
      {"id": "trigger", "type": "webhook_trigger", "name": "Incoming webhook",
       "params": {}, "position": [0, 0]},
      {"id": "format", "type": "code", "name": "Format message",
       "params": {"code": "output = {'text': f\"New event: {input.get('event', 'unknown')}\"}"},
       "position": [260, 0]},
      {"id": "notify", "type": "slack_send_message_v2", "name": "Post to Slack",
       "params": {"channel": "#alerts", "text": "{{format.text}}"},
       "position": [520, 0]}
    ],
    "edges": [
      {"source": "trigger", "target": "format"},
      {"source": "format", "target": "notify"}
    ]
  }
}
```

Before writing all three, verify the param/expression conventions against a real exported graph: `grep -n "params" apps/api/tests/test_workflows.py | head -10` and copy an existing test graph's node/edge shape exactly (including `source_output`/`target_input` if required). `daily_report_email.json`: `schedule_trigger` → `http_request` (GET a URL param) → `edit_fields` → `smtp_send_email`. `api_poll_transform.json`: `schedule_trigger` → `http_request` → `code` transform. Same shape discipline.

- [ ] **Step 2: Failing API tests** `apps/api/tests/test_templates.py`:

```python
"""Template gallery endpoints."""
from __future__ import annotations


async def test_list_templates(client) -> None:
    r = await client.get("/templates")
    assert r.status_code == 200
    ids = {t["id"] for t in r.json()}
    assert "webhook_to_slack" in ids
    assert all({"id", "name", "description", "tags"} <= set(t) for t in r.json())


async def test_instantiate_creates_workflow(client) -> None:
    r = await client.post("/templates/webhook_to_slack/instantiate",
                          json={"name": "My alert flow"})
    assert r.status_code == 201, r.text
    wf_id = r.json()["id"]
    detail = await client.get(f"/workflows/{wf_id}")
    graph = detail.json()["draft_graph"]
    assert {n["type"] for n in graph["nodes"]} == {
        "webhook_trigger", "code", "slack_send_message_v2"
    }


async def test_instantiate_unknown_template_404(client) -> None:
    r = await client.post("/templates/nope/instantiate", json={"name": "x"})
    assert r.status_code == 404
```

(Match the fixture names/style of `apps/api/tests/test_workflows.py` — if its client fixture is sync or named differently, mirror it.) Run → FAIL (404 on /templates).

- [ ] **Step 3: Implement `apps/api/app/routers/templates.py`:**

```python
"""Curated workflow templates: list + instantiate.

Templates are repo-shipped JSON graphs (no user content, no DB table).
Instantiation reuses the same validation + creation path as workflow import.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session  # mirror the import used by routers/export.py
from app.models import Environment, Workflow, WorkflowVersion
from app.security import require_permission  # mirror routers/export.py
from nodyra.engine.types import GraphError
from nodyra.engine.validation import _validate_graph
from nodyra.models import WorkflowGraph
from nodyra.sdk import registry as node_registry

router = APIRouter(tags=["templates"])
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "data" / "templates"


class TemplateSummary(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str]


class InstantiateRequest(BaseModel):
    name: str


class InstantiateResponse(BaseModel):
    id: str
    name: str


@lru_cache(maxsize=1)
def _load_templates() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(_TEMPLATE_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out[data["id"]] = data
    return out


@router.get("/templates", response_model=list[TemplateSummary])
async def list_templates() -> list[TemplateSummary]:
    return [
        TemplateSummary(id=t["id"], name=t["name"],
                        description=t["description"], tags=t["tags"])
        for t in _load_templates().values()
    ]


@router.post(
    "/templates/{template_id}/instantiate",
    response_model=InstantiateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def instantiate_template(
    template_id: str,
    body: InstantiateRequest,
    session: AsyncSession = Depends(get_session),
) -> InstantiateResponse:
    tpl = _load_templates().get(template_id)
    if tpl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown template")
    graph = WorkflowGraph(**tpl["graph"])
    try:
        _validate_graph(graph, node_registry)
    except GraphError as exc:  # repo-shipped templates must always validate
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"Template invalid: {exc}"
        ) from exc
    env = await session.scalar(
        select(Environment).where(Environment.is_global.is_(True)).limit(1)
    )
    workflow = Workflow(
        name=body.name,
        environment_id=env.id if env is not None else None,
        draft_graph=graph.model_dump(),
    )
    session.add(workflow)
    await session.flush()
    return InstantiateResponse(id=workflow.id, name=workflow.name)
```

Fix the three "mirror routers/export.py" imports against reality (`head -30 apps/api/app/routers/export.py`) — session dependency, permission helper, and whether new workflows need `versions.append(WorkflowVersion(...))` at create time (export.py's import endpoint pins version 1; a *draft* creation like this should match whatever `POST /workflows` does — check `routers/workflows.py` create handler and copy its persistence pattern including audit logging).

Register in `main.py` next to the export router include.

- [ ] **Step 4:** Run: `uv run pytest apps/api/tests/test_templates.py -v` → PASS (also confirms all shipped templates validate against the registry).

- [ ] **Step 5: Web gallery.** `api.ts`: `listTemplates()` (GET `/templates`), `instantiateTemplate(id, name)` (POST). Query hook `useTemplates()` + mutation, mirroring existing hooks. In the create-workflow entry point, replace the bare create with a two-option surface (keep it lightweight — the existing dialog component, one extra view):

```tsx
// inside the create dialog: "Blank workflow" button (existing behavior)
// plus a template grid:
{templates.map((t) => (
  <button key={t.id} type="button" className="template-card"
    onClick={() => instantiate.mutate(
      { id: t.id, name: t.name },
      { onSuccess: (wf) => navigate(`/workflows/${wf.id}`) },
    )}
  >
    <strong>{t.name}</strong>
    <span>{t.description}</span>
  </button>
))}
```

Style `.template-card` in the stylesheet the dialog already uses (grid of bordered cards, hover state — match existing card styles). Follow the navigation helper the file already uses after creating a blank workflow.

- [ ] **Step 6: Web test:** gallery renders template names from a mocked `/templates` and clicking instantiates + navigates (copy the file's existing mock + router-test pattern).

Run in `apps/web`: `npm run typecheck && npm test -- --run` → green.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/data/templates apps/api/app/routers/templates.py apps/api/app/main.py apps/api/tests/test_templates.py apps/web/src
git commit -m "feat: template gallery — 3 starter templates, list/instantiate API, create-time picker

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6 (P1): Per-workflow requirements + preflight

Workflows gain a `requirements` list (PEP 508 lines). Preflight reports which are missing from the workflow's environment — the #1 Python-env footgun from the audit.

**Files:**
- Create: `apps/api/alembic/versions/0080_workflow_requirements.py`
- Modify: `apps/api/app/models.py` (Workflow), `apps/api/app/schemas.py` (workflow detail/update schemas), the workflow update handler in `apps/api/app/routers/workflows.py`, `apps/api/app/services/package_preflight.py`, `apps/api/pyproject.toml` if `packaging` is absent
- Test: `apps/api/tests/test_workflow_requirements.py` (new)
- Web: workflow settings panel textarea (find where `environment_id` is edited: `grep -rn "environment_id" apps/web/src/editor --include='*.tsx' -l | head -3`)

- [ ] **Step 1: Migration.** Copy the header/imports style of `apps/api/alembic/versions/0079_environment_build_jobs.py` (revision chaining!), then:

```python
"""workflow.requirements: per-workflow PEP 508 requirement lines."""

# revision identifiers
revision = "0080"
down_revision = "0079"   # confirm against 0079's actual revision string

def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("requirements", sa.JSON(), nullable=False, server_default="[]"),
    )

def downgrade() -> None:
    op.drop_column("workflows", "requirements")
```

Confirm the table name (`grep -n "__tablename__" apps/api/app/models.py | grep -i workflow`) and the exact revision id format used by 0079 (some repos use hashes, not numbers — copy the real values).

- [ ] **Step 2: Model + schemas.** `models.py` Workflow: `requirements: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")` (mirror the column style of an existing JSON column like `draft_graph`). Schemas: add `requirements: list[str] = []` to the workflow detail response schema and the update request schema (find both: `grep -n "draft_graph" apps/api/app/schemas.py`).

- [ ] **Step 3: Failing tests** `apps/api/tests/test_workflow_requirements.py`:

```python
"""Per-workflow requirements: persistence, validation, preflight merge."""
from __future__ import annotations


async def test_requirements_roundtrip(client) -> None:
    wf = (await client.post("/workflows", json={"name": "req-wf"})).json()
    r = await client.put(
        f"/workflows/{wf['id']}",
        json={"requirements": ["pandas>=2.0", "requests"]},
    )
    assert r.status_code == 200, r.text
    detail = (await client.get(f"/workflows/{wf['id']}")).json()
    assert detail["requirements"] == ["pandas>=2.0", "requests"]


async def test_invalid_requirement_line_422(client) -> None:
    wf = (await client.post("/workflows", json={"name": "req-bad"})).json()
    r = await client.put(
        f"/workflows/{wf['id']}",
        json={"requirements": ["pandas >=== nope!!"]},
    )
    assert r.status_code == 422


def test_preflight_reports_missing_workflow_requirements() -> None:
    from app.services.package_preflight import missing_workflow_requirements

    missing = missing_workflow_requirements(
        workflow_requirements=["pandas>=2.0", "definitely-not-installed-xyz"],
        installed={"pandas": "2.2.0"},
    )
    assert missing == ["definitely-not-installed-xyz"]
```

(Adapt the client fixture to the house style, as in Task 5. `installed` mapping shape: check what `package_preflight.py` already works with — `grep -n "def " apps/api/app/services/package_preflight.py` — and make the new function consume the same structure it already has for environments.) Run → FAIL.

- [ ] **Step 4: Implement.**

Validation in the PUT handler (where other fields are applied — find the update handler in `routers/workflows.py`):

```python
    if body.requirements is not None:
        from packaging.requirements import InvalidRequirement, Requirement

        for line in body.requirements:
            try:
                Requirement(line)
            except InvalidRequirement as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"Invalid requirement {line!r}: {exc}",
                ) from exc
        workflow.requirements = body.requirements
```

Verify `uv run python -c "import packaging.requirements"`. Even if that passes via a transitive dependency, add `packaging>=24` to `apps/api/pyproject.toml` unless it is already declared directly; this code path is runtime API behavior and must have an explicit dependency.

Preflight in `package_preflight.py`:

```python
def missing_workflow_requirements(
    *, workflow_requirements: list[str], installed: dict[str, str]
) -> list[str]:
    """Requirement lines whose distribution is absent from *installed*
    (name-level check; version-specifier mismatches are surfaced by the
    environment's own preflight when it installs)."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    have = {canonicalize_name(n) for n in installed}
    missing: list[str] = []
    for line in workflow_requirements:
        try:
            req = Requirement(line)
        except Exception:
            missing.append(line)
            continue
        if canonicalize_name(req.name) not in have:
            missing.append(line)
    return missing
```

Then wire it into the existing preflight surface: find where environment preflight results are assembled for a workflow (`grep -rn "preflight" apps/api/app/routers/environments.py apps/api/app/routers/workflows.py | head`) and append a `workflow_requirements_missing` field to that response using the function above with the environment's installed-package mapping. Also surface it at run start if preflight already gates runs — follow the existing gating pattern only; do not invent a new gate.

- [ ] **Step 5: Web textarea.** In the panel where `environment_id` is edited, add below it (reusing the panel's label/field classes):

```tsx
<label className="...">Python requirements (one per line)</label>
<textarea
  className="..."
  rows={4}
  placeholder={"pandas>=2.0\nrequests"}
  value={requirementsText}
  onChange={(e) => setRequirementsText(e.target.value)}
  onBlur={() =>
    updateWorkflow.mutate({
      requirements: requirementsText.split("\n").map((s) => s.trim()).filter(Boolean),
    })
  }
/>
```

Wire through the same update mutation the panel already uses for other fields; display `workflow_requirements_missing` from the preflight response as a warning list if the panel already shows preflight state.

- [ ] **Step 6:** Run: `uv run pytest apps/api/tests/test_workflow_requirements.py apps/api/tests/test_workflows.py -q` and web `npm run typecheck && npm test -- --run` → green. Also `uv run alembic -c apps/api/alembic.ini upgrade head` on the dev DB (or trust the test-suite migration path if tests migrate automatically — check conftest).

- [ ] **Step 7: Commit**

```bash
git add apps/api/alembic/versions/0080_workflow_requirements.py apps/api/app/models.py apps/api/app/schemas.py apps/api/app/routers/workflows.py apps/api/app/services/package_preflight.py apps/api/tests/test_workflow_requirements.py apps/web/src
git commit -m "feat: per-workflow requirements with preflight missing-package report

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7 (P1): HuggingFace Inference provider

The audit's remaining genuinely-missing wishlist node (telegram/discord/teams already exist as v2 providers). Two operations: text generation (chat-completions) and embeddings, via the HF Inference API.

**Files:**
- Create: `packages/nodes/nodyra_nodes/integrations_v2/providers/huggingface/__init__.py`, `.../huggingface/operations.py`
- Modify: `packages/nodes/nodyra_nodes/__init__.py` (add the provider import alongside the ~45 existing `..._v2` imports)
- Test: `packages/nodes/tests/test_huggingface_v2.py` (new)

**Interfaces:**
- Consumes: `OperationSpec/OperationParamSpec/IntegrationSpec/ResourceSpec` from `nodyra_nodes.integrations_v2.specs`, `register_integration`/`register_operation` from `...registry`, `ProviderTransport` from `...transport`, `CredentialSpec` from `nodyra.models` — exactly as `providers/slack/operations.py` uses them.
- Produces: node types `huggingface_text_generation_v2`, `huggingface_embeddings_v2`; credential type `huggingface_api` with field `api_token`.

- [ ] **Step 1: Study the reference provider.** Read `packages/nodes/nodyra_nodes/integrations_v2/providers/slack/operations.py` fully (specs at top, `_transport` helper at ~314, executors at ~423, registration + `IntegrationSpec` at ~624). The HF provider mirrors this file's structure exactly.

- [ ] **Step 2: Failing tests** `packages/nodes/tests/test_huggingface_v2.py` (copy the harness of an existing v2 provider test — `packages/nodes/tests/test_airtable_v2.py` shows the manifest assertion style; find how executor tests stub `ProviderTransport.request` with `grep -rn "ProviderTransport" packages/nodes/tests | head -5` and reuse that fixture):

```python
"""HuggingFace v2 provider: manifests + executors."""
from __future__ import annotations

from nodyra.sdk import registry


def test_manifests_registered() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    assert "huggingface_text_generation_v2" in manifests
    assert "huggingface_embeddings_v2" in manifests


def test_text_generation_calls_chat_completions(transport_stub) -> None:
    from nodyra_nodes.integrations_v2.providers.huggingface.operations import (
        text_generation,
    )

    transport_stub.respond({"choices": [{"message": {"content": "hi there"}}]})
    out = text_generation(
        credentials={"api_token": "hf_x"},
        model="meta-llama/Llama-3.1-8B-Instruct",
        prompt="say hi",
    )
    assert out["text"] == "hi there"
    assert transport_stub.last.path == "/v1/chat/completions"


def test_embeddings_returns_vectors(transport_stub) -> None:
    from nodyra_nodes.integrations_v2.providers.huggingface.operations import (
        embeddings,
    )

    transport_stub.respond([[0.1, 0.2], [0.3, 0.4]])
    out = embeddings(
        credentials={"api_token": "hf_x"},
        model="sentence-transformers/all-MiniLM-L6-v2",
        texts=["a", "b"],
    )
    assert len(out["embeddings"]) == 2
```

(`transport_stub` = whatever the existing tests' stub fixture is actually called; adapt names/assertion attributes to it.) Run → FAIL.

- [ ] **Step 3: Implement `providers/huggingface/operations.py`:**

```python
"""HuggingFace Inference v2 operation specs and executors."""
from __future__ import annotations

from typing import Any

from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.errors import ProviderError
from nodyra_nodes.integrations_v2.registry import register_integration, register_operation
from nodyra_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationParamSpec,
    OperationSpec,
    ResourceSpec,
)
from nodyra_nodes.integrations_v2.transport import ProviderTransport

HF_API_BASE = "https://router.huggingface.co"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="huggingface_api",
            key="*",
            label="HuggingFace API token",
            fields=["api_token"],
            multi=True,
        ),
        description="HuggingFace access token (hf_...).",
    )


def _transport(credentials: Any) -> ProviderTransport:
    token = (credentials or {}).get("api_token", "")
    if not token:
        raise ValueError("huggingface: credentials are required")
    return ProviderTransport(
        provider="huggingface",
        base_url=HF_API_BASE,
        default_headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )


HF_TEXT_GENERATION_SPEC = OperationSpec(
    node_id="huggingface_text_generation_v2",
    name="HuggingFace Text Generation",
    provider="huggingface",
    resource="inference",
    operation="text_generation",
    description="Generate text with any chat model on HuggingFace Inference.",
    icon="brand:huggingface",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="model",
            required=True,
            placeholder="meta-llama/Llama-3.1-8B-Instruct",
        ),
        OperationParamSpec(
            name="prompt",
            required=True,
            placeholder="Prompt text. Supports expressions.",
        ),
        OperationParamSpec(
            name="max_tokens", type="number", group="Options",
            description="Maximum tokens to generate (default 512).",
        ),
        OperationParamSpec(
            name="temperature", type="number", group="Options",
        ),
    ),
)


def text_generation(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    model: str = "",
    prompt: str = "",
    max_tokens: float | None = None,
    temperature: float | None = None,
) -> Any:
    if not model:
        raise ValueError("huggingface_text_generation_v2: model is required")
    if not prompt:
        raise ValueError("huggingface_text_generation_v2: prompt is required")
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(max_tokens) if max_tokens else 512,
    }
    if temperature is not None:
        body["temperature"] = float(temperature)
    result = _transport(credentials).request(
        "POST",
        "/v1/chat/completions",
        operation="text_generation",
        json_body=body,
    )
    try:
        text = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(
            provider="huggingface", operation="text_generation",
            message=f"unexpected response shape: {result!r:.200}",
        ) from exc
    return {"text": text, "model": model, "raw": result}


HF_EMBEDDINGS_SPEC = OperationSpec(
    node_id="huggingface_embeddings_v2",
    name="HuggingFace Embeddings",
    provider="huggingface",
    resource="inference",
    operation="embeddings",
    description="Compute sentence embeddings via HuggingFace Inference.",
    icon="brand:huggingface",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="model",
            required=True,
            placeholder="sentence-transformers/all-MiniLM-L6-v2",
        ),
        OperationParamSpec(
            name="texts", type="array", required=True,
            description="List of strings to embed.",
        ),
    ),
)


def embeddings(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    model: str = "",
    texts: list[str] | None = None,
) -> Any:
    if not model:
        raise ValueError("huggingface_embeddings_v2: model is required")
    if not texts:
        raise ValueError("huggingface_embeddings_v2: texts is required")
    result = _transport(credentials).request(
        "POST",
        f"/hf-inference/models/{model}/pipeline/feature-extraction",
        operation="embeddings",
        json_body={"inputs": texts},
    )
    return {"embeddings": result, "model": model, "count": len(texts)}


HUGGINGFACE_INTEGRATION = IntegrationSpec(
    id="huggingface",
    name="HuggingFace",
    description="Text generation and embeddings on HuggingFace Inference.",
    icon="brand:huggingface",
    credential_types=("huggingface_api",),
    resources=(
        ResourceSpec(
            id="inference",
            name="Inference",
            operations=(HF_TEXT_GENERATION_SPEC, HF_EMBEDDINGS_SPEC),
        ),
    ),
)

register_operation(HF_TEXT_GENERATION_SPEC, text_generation, node_registry=None)
register_operation(HF_EMBEDDINGS_SPEC, embeddings, node_registry=None)
register_integration(HUGGINGFACE_INTEGRATION)
```

Adjust to reality where the framework differs (exact `ProviderError` signature — `grep -n "class ProviderError" -A 8 packages/nodes/nodyra_nodes/integrations_v2/errors.py`; whether `CredentialSpec` needs `test_service`; whether `register_operation` takes other kwargs — mirror slack exactly). `__init__.py`:

```python
"""HuggingFace v2 provider."""
from nodyra_nodes.integrations_v2.providers.huggingface import operations as operations
```

and add the import line in `nodyra_nodes/__init__.py` alphabetically among the `..._v2` imports.

- [ ] **Step 4:** Run: `uv run pytest packages/nodes/tests/test_huggingface_v2.py packages/nodes/tests -k "integrations_v2 or manifest" -q` → PASS (registry-wide manifest tests confirm no collisions).

- [ ] **Step 5: Ruff + commit**

```bash
uv run ruff check packages/nodes/nodyra_nodes/integrations_v2/providers/huggingface packages/nodes/tests/test_huggingface_v2.py
git add packages/nodes/nodyra_nodes/integrations_v2/providers/huggingface packages/nodes/nodyra_nodes/__init__.py packages/nodes/tests/test_huggingface_v2.py
git commit -m "feat(nodes): HuggingFace Inference v2 provider (text generation + embeddings)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8 (P1/S): Document GitOps sync

The GitHub workflow-sync feature is ahead of competitors and undocumented.

**Files:**
- Create: `docs/gitops.md`
- Modify: `README.md` (one feature bullet linking it)

- [ ] **Step 1: Extract the real feature surface.** `grep -rn "github" apps/api/app/routers --include='*.py' -il`, then read the matching router's docstrings and endpoint list; also `grep -rn -i "github\|git sync" apps/web/src/SettingsPage.tsx | head`. Build the doc from what actually exists — every claim in the doc must trace to an endpoint or UI element you saw.

- [ ] **Step 2: Write `docs/gitops.md`** covering, in this order: what syncs (workflow definitions/versions), setup (credential/app connection as implemented), directory/file layout in the repo, push/pull or webhook flow as implemented, conflict behavior, and a CI example — running `nodyra run start --watch` (Phase 3) against a synced workflow after merge. Any capability that does *not* exist gets a single "not yet supported" line rather than silence.

- [ ] **Step 3:** README feature list: add `- **GitOps**: two-way GitHub sync for workflow definitions — [docs/gitops.md](docs/gitops.md)` (wording adjusted to the verified reality).

- [ ] **Step 4: Commit**

```bash
git add docs/gitops.md README.md
git commit -m "docs: GitOps sync guide

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
