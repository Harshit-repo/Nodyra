# MS3 + MS4 Concrete Implementation Plan

**Date:** 2026-06-29  
**Branch:** To be created from `main` after `fix/backend-production-readiness-p0-p1` merges  
**Status:** Spec — ready for implementation  
**Pre-requisites:** MS1 (P0 security fixes) ✅ and MS2 (AI + SDK + checkpoints) ✅ are both fully merged

---

## Baseline State (What's Already Done)

Before writing tasks, what MS2 actually shipped (so we don't re-do it):

| Feature | Status | Location |
|---------|--------|----------|
| Durable execution checkpoints | ✅ DONE | `Run.checkpoint`, `_save_checkpoint()` |
| nodyra-client SDK + CLI | ✅ DONE | `packages/client/nodyra_client/` |
| AI explain endpoint | ✅ DONE | `GET /workflows/{id}/explain` |
| Multi-turn AI refinement | ✅ DONE | `POST /workflows/{id}/ai-draft` mode=refine |
| Workflow test generation | ✅ DONE | `POST /workflows/{id}/generate-tests` |
| Execution timeline | ✅ DONE | `apps/web/src/editor/ExecutionTimeline.tsx` |
| Expression autocomplete | ✅ DONE | `apps/web/src/editor/fields/ExpressionAutocomplete.tsx` |
| Node palette descriptions | ✅ DONE | Hover tooltips in `NodePalette.tsx` |
| WorkflowsPage static onboarding | ✅ PARTIAL | `WorkflowsPage.tsx:893` — static 3-step list, not interactive editor overlay |

**Visual diff components** (`WorkflowDiffView.tsx`, `DiffNode.tsx`, `DiffSummaryBar.tsx`) exist for version history but are **not wired into `AiDraftModal.tsx`**.

---

## MS3: Differentiated Product (6 weeks)

### Overview

MS3 makes Noodle genuinely unique vs every competitor. The five slices:

| Slice | Feature | Effort | Strategic Value |
|-------|---------|--------|-----------------|
| 3A | Interactive Editor Onboarding | 3 days | Users discover the canvas |
| 3B | Visual AI Diff in Draft Modal | 3 days | Trust in AI changes |
| 3C | MCP Client — External Tools as Nodes | 2 weeks | Every MCP server = Noodle node library |
| 3D | Typed Code Node I/O | 1 week | Python-native contracts |
| 3E | AI-Generated Custom Typed Nodes | 2 weeks | Biggest differentiator |

---

### Slice 3A: Interactive Editor Onboarding (3 days)

**Gap:** `WorkflowsPage` has a static 3-step list. The editor itself has no tutorial. Users who open a blank canvas have no guidance.

**Target UX:**
- First-time editor visit triggers a 5-step interactive overlay
- Each step spotlights a specific UI element with a tooltip pointing at it
- User can dismiss at any time; state stored in localStorage
- Triggered on: new workflow creation OR first visit with empty canvas

**Files to create:**
- `apps/web/src/editor/OnboardingTour.tsx` — the overlay component
- `apps/web/src/editor/useOnboardingTour.ts` — step state hook

**Files to modify:**
- `apps/web/src/editor/Canvas.tsx` — mount `<OnboardingTour>` after editor ready
- `apps/web/src/editor/Canvas.tsx:1228` — remove the static empty-canvas div, replace with tour-aware empty state

**Step definitions:**

```typescript
// apps/web/src/editor/useOnboardingTour.ts
// Targets use data-tour-id attributes — stable contracts, not CSS class names.
export const TOUR_STEPS = [
  {
    id: "canvas",
    target: "[data-tour-id=\"canvas\"]",
    title: "Your workflow canvas",
    body: "Drag nodes here to build automation workflows. Double-click anywhere to quick-add a node.",
    position: "center",
  },
  {
    id: "palette",
    target: "[data-tour-id=\"palette\"]",
    title: "Node palette",
    body: "Browse 70+ built-in nodes — HTTP, code, AI, data, integrations. Drag any node onto the canvas.",
    position: "right",
  },
  {
    id: "quickadd",
    target: "[data-tour-id=\"toolbar\"]",
    title: "Quick-add shortcut",
    body: "Press Tab anywhere on the canvas to open the node search and insert a node at your cursor.",
    position: "bottom",
  },
  {
    id: "run",
    target: "[data-tour-id=\"run-button\"]",
    title: "Run your workflow",
    body: "Click Run to execute all nodes. Live output appears on each node tile as it completes.",
    position: "bottom",
  },
  {
    id: "ai",
    target: "[data-tour-id=\"ai-draft-button\"]",
    title: "AI workflow builder",
    body: "Type what you want to automate in plain English. AI generates the full workflow graph — editable.",
    position: "bottom",
  },
] as const;
```

**Stable target anchors:** Do NOT use CSS class names as tour targets — class names are refactored frequently. Instead add `data-tour-id` attributes to target elements:

```tsx
// apps/web/src/editor/Canvas.tsx — add to the pane wrapper
<div data-tour-id="canvas" ...>

// apps/web/src/editor/NodePalette.tsx — add to palette root
<div data-tour-id="palette" ...>

// apps/web/src/editor/Toolbar.tsx — add to toolbar root
<div data-tour-id="toolbar" ...>

// apps/web/src/editor/Toolbar.tsx — add to Run button
<button data-tour-id="run-button" ...>

// apps/web/src/editor/Toolbar.tsx — add to AI draft button  
<button data-tour-id="ai-draft-button" ...>
```

**Component structure:**
```typescript
// apps/web/src/editor/OnboardingTour.tsx
// Props: { steps: TourStep[]; onComplete: () => void; onSkip: () => void }
// Renders: semi-transparent overlay with a spotlight cut-out (CSS clip-path: polygon)
// pointing at the target element.
// Uses getBoundingClientRect() on the target selector; re-computes on window resize.
// Edge cases:
//   - Target not in DOM yet: poll with requestAnimationFrame (max 2s) then skip step
//   - Target off-screen: scroll into view before positioning tooltip
//   - Viewport resize: reposition tooltip via ResizeObserver
// Steps: prev/next/skip buttons. "X" dismisses permanently.
```

**Acceptance criteria:**
- [ ] First-time editor visit shows tour step 1 automatically
- [ ] Each step spotlight correctly positions against its target element
- [ ] User can click Next/Previous/Skip
- [ ] Dismissed state persists across page reloads (localStorage)
- [ ] Does NOT show if localStorage key `noodle-editor-tour-v1` is set
- [ ] Spotlight target element is fully interactive during its step; overlay blocks interaction outside the spotlight (standard onboarding tour behavior)
- [ ] Tour works at 1280px and 1920px viewport widths

---

### Slice 3B: Visual AI Diff in Draft Modal (3 days)

**Gap:** `AiDraftModal.tsx` shows a text summary of AI changes. `WorkflowDiffView.tsx` already renders side-by-side node diffs for version history — but they are not connected.

**Target UX:**
- AI draft modal gains a "Visual Diff" tab alongside existing "Preview" and "Description" tabs
- Tab shows: green nodes added, red nodes removed, yellow nodes modified
- Per-node diff uses `NodeParamDiffPanel.tsx` to show param-level changes
- User can accept or reject individual node changes before applying

**Files to modify:**

1. `apps/web/src/AiDraftModal.tsx`:
   - Import `WorkflowDiffView` from `./editor/WorkflowDiffView`
   - Import `diffWorkflowGraphs` from `./editor/diffWorkflowGraphs`
   - Add "Visual Diff" tab (second tab after "Summary")
   - Pass `currentGraph` (from store) and `proposedGraph` (from AI response) to diff

2. `apps/web/src/editor/WorkflowDiffView.tsx`:
   - Currently takes `baseVersion`/`compareVersion` as `WorkflowVersion` objects
   - Add an alternative props signature: `baseGraph: WorkflowGraph; compareGraph: WorkflowGraph`
   - Factor out internal `DiffCanvas` that works on plain graphs

3. `apps/web/src/AiDraftModal.tsx` — per-change accept/reject:
   ```typescript
   const [rejectedNodeIds, setRejectedNodeIds] = useState<Set<string>>(new Set());
   
   function applyWithFilters(
     currentGraph: WorkflowGraph,
     proposedGraph: WorkflowGraph,
   ): WorkflowGraph {
     const diff = diffWorkflowGraphs(currentGraph, proposedGraph);
     const currentById = new Map(currentGraph.nodes.map(n => [n.id, n]));
     
     const resultNodes: GraphNode[] = [];
     
     // Visit all proposed nodes
     for (const node of proposedGraph.nodes) {
       const change = diff.nodeChanges[node.id];
       if (!change || change.type === "unchanged") {
         resultNodes.push(node); // pass through
       } else if (change.type === "added") {
         if (!rejectedNodeIds.has(node.id)) resultNodes.push(node); // rejected additions are dropped
       } else if (change.type === "modified") {
         // Rejected modifications: restore original; accepted: use proposed
         resultNodes.push(rejectedNodeIds.has(node.id) ? currentById.get(node.id)! : node);
       }
     }
     
     // Rejected removals: add the original node back
     for (const removedId of diff.removedNodeIds ?? []) {
       if (rejectedNodeIds.has(removedId) && currentById.has(removedId)) {
         resultNodes.push(currentById.get(removedId)!);
       }
     }
     
     // Remove edges whose source or target no longer exists
     const validNodeIds = new Set(resultNodes.map(n => n.id));
     const filteredEdges = proposedGraph.edges.filter(
       e => validNodeIds.has(e.source) && validNodeIds.has(e.target)
     );
     // Restore edges that connected rejected-removal nodes back to the graph
     const restoredEdges = currentGraph.edges.filter(
       e => validNodeIds.has(e.source) && validNodeIds.has(e.target) &&
            !filteredEdges.some(fe => fe.id === e.id)
     );
     return { nodes: resultNodes, edges: [...filteredEdges, ...restoredEdges] };
   }
   ```
   
   **Three change types handled:**
   - **Added node rejected:** dropped entirely + orphaned edges removed
   - **Modified node rejected:** original node restored in-place; node ID unchanged so existing edges remain valid
   - **Removed node rejected:** original node added back; original edges reconnected if both endpoints still present

**Files to create:**
- None — reuse all existing diff components

**API:** No backend change needed. Diff is computed client-side from the AI response payload (which already returns the full proposed graph).

**Acceptance criteria:**
- [ ] "Visual Diff" tab appears in AI draft modal when there are changes
- [ ] Added nodes shown with green border; removed nodes with red; modified nodes with yellow
- [ ] `NodeParamDiffPanel` shows which params changed and their old → new values
- [ ] User can click "Reject" on individual node changes
- [ ] "Apply" only applies accepted changes
- [ ] If all changes rejected: Apply is disabled
- [ ] Visual diff matches what text summary describes (regression check)

---

### Slice 3C: MCP Client — External Tools as Canvas Nodes (2 weeks)

**What this unlocks:** Every MCP server in existence becomes a Noodle integration. A user can connect to their company's internal MCP server and immediately get its tools as drag-and-drop nodes — with zero code.

#### Data Model

New table `mcp_connections`:
```sql
-- apps/api/alembic/versions/XXXX_mcp_connections.py
CREATE TABLE mcp_connections (
    id          TEXT PRIMARY KEY DEFAULT gen_ulid(),
    org_id      TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,           -- e.g. https://mcp.example.com/
    transport   TEXT NOT NULL DEFAULT 'streamable-http',  -- 'streamable-http' | 'sse'
    auth_type   TEXT NOT NULL DEFAULT 'none',             -- 'none' | 'bearer' | 'header'
    auth_secret TEXT,                    -- single-field Fernet-encrypted with org KEK
    headers     JSONB NOT NULL DEFAULT '{}'::jsonb,
    tool_cache  JSONB,                   -- cached tool list from last discover
    last_synced_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_mcp_connections_org ON mcp_connections(org_id);
-- RLS: same pattern as other org-scoped tables
ALTER TABLE mcp_connections ENABLE ROW LEVEL SECURITY;
CREATE POLICY mcp_connections_org ON mcp_connections USING (org_id = current_setting('app.org_id', true));
```

ORM model in `apps/api/app/models.py`:
```python
class MCPConnection(Base):
    __tablename__ = "mcp_connections"
    id: Mapped[str] = mapped_column(Text, primary_key=True, default=gen_ulid)
    org_id: Mapped[str] = mapped_column(Text, ForeignKey("organizations.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    transport: Mapped[str] = mapped_column(Text, nullable=False, default="streamable-http")
    auth_type: Mapped[str] = mapped_column(Text, nullable=False, default="none")
    auth_secret: Mapped[str | None] = mapped_column(Text, nullable=True)  # single-field Fernet-encrypted with org KEK
    headers: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    tool_cache: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
```

#### Backend Service

New file `apps/api/app/services/mcp_client.py`:
```python
"""MCP client: connects to external MCP servers, discovers tools, executes calls."""

import httpx
from typing import Any

from app.models import MCPConnection

_JSON_TYPE_TO_PORT_KIND: dict[str, str] = {
    "string": "string",
    "number": "number",
    "integer": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}


def _build_auth_headers(conn: MCPConnection, decrypted_secret: str | None) -> dict[str, str]:
    headers = dict(conn.headers or {})
    if conn.auth_type == "bearer" and decrypted_secret:
        headers["Authorization"] = f"Bearer {decrypted_secret}"
    elif conn.auth_type == "header" and decrypted_secret:
        # auth_secret stored as "Header-Name:value" — colon is mandatory
        # Validate this format on CREATE/PATCH: if ":" not in auth_secret, return 422
        # with message: "auth_secret for auth_type='header' must be 'Header-Name:value'"
        k, _, v = decrypted_secret.partition(":")
        headers[k.strip()] = v.strip()
    return headers


def _unwrap_mcp_result(result: dict) -> Any:
    """Normalize MCP tool result to a plain Python value."""
    content = result.get("content", [])
    if not content:
        return result
    # Single text item → return the string directly
    if len(content) == 1 and content[0].get("type") == "text":
        return content[0]["text"]
    # Multiple items or non-text → return the content list
    return content


class MCPError(Exception):
    """Raised when an MCP server returns a JSON-RPC error response."""


async def discover_tools(conn: MCPConnection, *, decrypted_secret: str | None) -> list[dict]:
    """Call tools/list on the remote MCP server. Returns raw tool manifests."""
    headers = _build_auth_headers(conn, decrypted_secret)
    async with httpx.AsyncClient(timeout=10.0) as client:
        # MCP streamable-HTTP: POST with JSON-RPC 2.0
        resp = await client.post(
            conn.url.rstrip("/"),
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Content-Type": "application/json", **headers},
        )
        resp.raise_for_status()
        data = resp.json()
    # JSON-RPC 2.0 errors are returned in the "error" field, not as HTTP errors
    if "error" in data:
        raise MCPError(data["error"].get("message", "MCP tools/list error"))
    return data.get("result", {}).get("tools", [])


async def call_tool(
    conn: MCPConnection,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    decrypted_secret: str | None,
) -> Any:
    """Execute a single MCP tool call and return its result."""
    headers = _build_auth_headers(conn, decrypted_secret)
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            conn.url.rstrip("/"),
            json={
                "jsonrpc": "2.0", "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
            headers={"Content-Type": "application/json", **headers},
        )
        resp.raise_for_status()
        data = resp.json()
    # JSON-RPC 2.0 errors are returned in the "error" field, not as HTTP errors
    if "error" in data:
        raise MCPError(data["error"].get("message", "MCP tools/call error"))
    result = data.get("result", {})
    return _unwrap_mcp_result(result)


def mcp_tool_to_node_manifest(tool: dict, conn_id: str) -> dict:
    """Convert an MCP tool definition to a Noodle NodeManifest dict."""
    schema = tool.get("inputSchema", {})
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    return {
        "id": f"mcp:{conn_id}:{tool['name']}",
        "name": tool.get("title") or tool["name"],
        "category": "MCP",
        "description": tool.get("description", ""),
        "input_kinds": {
            k: _JSON_TYPE_TO_PORT_KIND.get(v.get("type", ""), "any")
            for k, v in props.items()
        },
        "output_kinds": {"main": "any"},
        "params": [
            {
                "name": k,
                "label": k.replace("_", " ").title(),
                "type": v.get("type", "any"),
                "required": k in required,
                "description": v.get("description", ""),
            }
            for k, v in props.items()
        ],
        "mcp_connection_id": conn_id,
        "mcp_tool_name": tool["name"],
    }
```

#### Backend Router

New file `apps/api/app/routers/mcp_connections.py`:
```
GET    /mcp-connections          — list org's MCP connections
POST   /mcp-connections          — create connection (encrypts auth_secret)
GET    /mcp-connections/{id}     — get one
PATCH  /mcp-connections/{id}     — update
DELETE /mcp-connections/{id}     — delete
POST   /mcp-connections/{id}/sync — discover tools, update tool_cache
GET    /mcp-connections/{id}/tools — return tool_cache as NodeManifest list
```

**Key: `/sync` endpoint:**
```python
@router.post("/{connection_id}/sync")
async def sync_mcp_connection(
    connection_id: str,
    session: AsyncSession = Depends(get_session),
    current_user = Depends(get_current_user),
    _: None = Depends(require_permission("mcp_connection:manage")),
):
    # _load_conn_with_secret handles org-scoped lookup + single-field Fernet decryption
    try:
        conn, secret = await _load_conn_with_secret(connection_id, current_user.org_id, session)
    except ValueError:
        raise HTTPException(404)
    tools = await discover_tools(conn, decrypted_secret=secret)
    # Enforce cache size limit
    if len(tools) > 500:
        tools = tools[:500]
    conn.tool_cache = tools
    conn.last_synced_at = datetime.now(UTC)
    await session.commit()
    return {"tools_discovered": len(tools), "tools": tools}
```

**`_load_conn_with_secret`** (defined in `mcp_client.py`) handles both the org-scoped lookup and the Fernet decryption in one call. The `/sync` endpoint and all other consumers should use this instead of inlining decryption. See the full implementation in the `_load_conn_with_secret` section above.

#### Node Execution

**Architectural note:** Node packages (`packages/nodes`) are standalone — they CANNOT import from `app.services`. The MCP tool call must be dispatched through `RuntimeContext`, which is provided by the runner (the only component that has DB/service access). This mirrors how credentials work today (`ctx.credential("key-name")` resolves the value via a platform hook rather than a direct DB import).

New node type `mcp_tool` in `packages/nodes/noodle_nodes/mcp_tool.py`:
```python
@node(
    id="mcp_tool",
    name="MCP Tool",
    category="MCP",
    description="Execute a tool from an external MCP server",
    input_kinds={"main": "any"},
    output_kinds={"main": "any"},
    params=[
        NodeParam("connection_id", label="MCP Connection", type="mcp_connection"),
        NodeParam("tool_name", label="Tool Name", type="string"),
        NodeParam("arguments", label="Arguments", type="object"),
    ],
)
async def mcp_tool(input: Any = None, *, ctx: RuntimeContext) -> Any:
    params = ctx.node_params
    conn_id = params.get("connection_id")
    tool_name = params.get("tool_name")
    arguments = params.get("arguments") or {}
    
    # Resolve {{ }} expression refs from upstream node outputs
    from noodle.expr import eval_expr_dict
    resolved_args = eval_expr_dict(arguments, ctx.node_inputs.get("main", {}))
    
    # Dispatch through the platform hook (implemented in runner's RuntimeContext)
    # RuntimeContext.call_mcp_tool() is a new abstract method added to the protocol.
    return await ctx.call_mcp_tool(conn_id, tool_name, resolved_args)
```

**New `RuntimeContext` method** (add to `packages/core/noodle/engine/types.py`):
```python
class RuntimeContext(Protocol):
    # ... existing methods ...
    async def call_mcp_tool(self, connection_id: str, tool_name: str, arguments: dict) -> Any:
        """Platform hook: resolve MCP connection and execute tool call."""
        ...
```

**Runner implementation** (add to `apps/api/app/services/runner.py`, `_NodeRuntimeContext`):
```python
async def call_mcp_tool(self, connection_id: str, tool_name: str, arguments: dict) -> Any:
    from app.services.mcp_client import call_tool, _load_conn_with_secret
    conn, secret = await _load_conn_with_secret(connection_id, self._org_id, self._session)
    return await call_tool(conn, tool_name, arguments, decrypted_secret=secret)
```

**`_load_conn_with_secret`** must be defined in `apps/api/app/services/mcp_client.py`:
```python
async def _load_conn_with_secret(
    connection_id: str,
    org_id: str,
    session: AsyncSession,
) -> tuple[MCPConnection, str | None]:
    """Load an MCPConnection and decrypt its auth_secret. Raises 404 if not found."""
    from sqlalchemy import select
    from app.models import MCPConnection
    conn = await session.scalar(
        select(MCPConnection).where(
            MCPConnection.id == connection_id,
            MCPConnection.org_id == org_id,
        )
    )
    if conn is None:
        raise ValueError(f"MCPConnection {connection_id!r} not found")
    if not conn.auth_secret:
        return conn, None
    # auth_secret is single-field Fernet-encrypted with the org KEK.
    # This is NOT the two-field DEK pattern used for workflow Credentials
    # (which stores encrypted_dek + token as two columns).
    # For a single TEXT field, the encryption is: Fernet(org_kek).encrypt(secret.encode())
    # Decryption: grep how the runner decrypts single-field secrets (e.g. look at
    # how `Credential.value` is handled in services/credentials.py or runner.py).
    org_kek = await _get_org_kek(org_id, session)  # verify function name in org_keys.py
    from cryptography.fernet import Fernet
    secret = Fernet(org_kek).decrypt(conn.auth_secret.encode()).decode()
    return conn, secret
```

**Note on single-field Fernet encryption:** `auth_secret` uses `Fernet(org_kek).encrypt(raw_secret.encode()).decode()` on write, and `Fernet(org_kek).decrypt(stored.encode()).decode()` on read. This is the same single-field pattern used for `sso_configs.client_secret`. It is simpler than the two-field DEK pattern (used for `credentials.value` where each credential gets its own random DEK). Verify `_get_org_kek(org_id, session)` function name against `services/org_keys.py` — it loads the org's KEK from the `org_keys` table.

#### Frontend

1. **Settings → MCP Connections page:**
   - New route `/settings/mcp-connections`
   - Form: name, URL, transport, auth type, auth secret/header
   - "Sync Tools" button → calls `/sync`, shows discovered tools count
   - Connection list with last-synced time

2. **Node palette integration:**
   - When MCP connections exist with `tool_cache`, populate palette with a "MCP" category
   - Tools appear as nodes with their MCP description
   - Dragging adds `mcp_tool` node with `connection_id` + `tool_name` pre-populated

3. **Canvas rendering:**
   - `mcp_tool` nodes render with a plug icon and connection name as subtitle

**Files to create:**
- `apps/api/app/services/mcp_client.py`
- `apps/api/app/routers/mcp_connections.py`
- `apps/api/alembic/versions/XXXX_mcp_connections.py`
- `packages/nodes/noodle_nodes/mcp_tool.py`
- `apps/web/src/settings/McpConnectionsPage.tsx`
- `apps/web/src/editor/nodes/McpToolNode.tsx`
- `apps/api/tests/test_mcp_connections.py`

**Files to modify:**
- `apps/api/app/models.py` — add `MCPConnection`
- `apps/api/app/routers/__init__.py` / `main.py` — register new router
- `apps/api/app/security.py` — add `mcp_connection:manage` permission
- `apps/web/src/editor/NodePalette.tsx` — fetch MCP tools and inject as nodes
- `apps/web/src/App.tsx` / routing — add settings page route
- `packages/core/noodle/engine/types.py` — add `call_mcp_tool` to `RuntimeContext` protocol
- `apps/api/app/services/runner.py` — implement `call_mcp_tool` on `_NodeRuntimeContext`

**Security requirements:**
- `auth_secret` single-field Fernet-encrypted with the org KEK (NOT the two-field DEK pattern used by `Credential.value`)
- SSRF guard: MCP server URL must pass the existing egress check (`assert_public_http_url()` or `check_egress_url()` — confirm name against current `egress.py`) unless `NOODLE_ALLOW_PRIVATE_EGRESS=true`
- Tool arguments: expression resolution happens before dispatch; raw credential values must never be passed as arguments to external servers (enforce by checking argument values against credential patterns)
- Timeout: 30s per tool call, configurable via env `MCP_TOOL_TIMEOUT_SECONDS=30`
- `tool_cache` size limit: truncate to 500 tools max per connection (enforced in `/sync` endpoint)
- **V1 transport restriction:** Only `streamable-http` transport implemented in V1. The `transport` field is stored and validated but SSE transport (event-stream-based) is deferred to V2. Surface a clear error if a user sets `transport=sse`: `"SSE transport is not yet supported; use streamable-http"`.
- **`arguments` param UX limitation (V1):** The `mcp_tool` node uses a single free-form `arguments: object` param rather than dynamically rendering individual fields from the tool's `inputSchema`. Users must manually construct the JSON object. Dynamic per-tool param rendering (reading the tool schema from `tool_cache` and generating Inspector fields) is a V2 improvement.

**Acceptance criteria:**
- [ ] User can add an MCP connection (URL + optional auth) in Settings
- [ ] "Sync" discovers tools and shows count
- [ ] Discovered tools appear in canvas palette under "MCP" category
- [ ] Dragging a tool onto canvas adds a pre-configured `mcp_tool` node
- [ ] Node executes the tool call at runtime and routes output to connected nodes
- [ ] SSRF guard blocks private IP tool calls by default
- [ ] Auth secret encrypted at rest, redacted from logs
- [ ] Test: `test_mcp_connection_sync_discovers_tools` (mock MCP server)
- [ ] Test: `test_mcp_tool_node_executes_call`
- [ ] Test: `test_mcp_connection_ssrf_blocked`

---

### Slice 3D: Typed Code Node I/O (1 week)

**Gap:** Code node uses `Any` for all inputs and outputs. No wiring-time contract, no validation.

**Design:** Add optional `input_schema` and `output_schema` JSON Schema fields to code node params. Validate at execution time; surface errors as node validation warnings at wiring time.

#### Schema Storage

No new DB column needed. Schema lives in `GraphNode.params`:
```json
{
  "code": "output = input['score'] * 1.5",
  "input_schema": {
    "type": "object",
    "properties": {
      "score": {"type": "number"},
      "user_id": {"type": "integer"}
    },
    "required": ["user_id"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "result": {"type": "number"},
      "category": {"type": "string", "enum": ["low", "medium", "high"]}
    }
  }
}
```

#### Backend Validation

**Schema injection defense:** User-controlled schemas must not contain `$ref`, `$schema`, or `$id` pointing to external URLs — this would cause jsonschema to fetch external resources during validation. Strip these before validating:

```python
def _sanitize_schema(schema: dict) -> dict:
    """Remove keys that could trigger external resource fetching."""
    blocked = {"$ref", "$schema", "$id"}
    def _clean(val: Any) -> Any:
        if isinstance(val, dict):
            return {k: _clean(v) for k, v in val.items() if k not in blocked}
        if isinstance(val, list):
            return [_clean(item) for item in val]
        return val
    return {k: _clean(v) for k, v in schema.items() if k not in blocked}
```

In `packages/core/noodle/engine/node_exec.py`, in `_run_one_node()`:
```python
# BEFORE exec: validate wired inputs against input_schema
input_schema = params.get("input_schema")
if input_schema and isinstance(node_input, dict):
    try:
        jsonschema.validate(node_input, _sanitize_schema(input_schema))
    except jsonschema.ValidationError as exc:
        raise NodeValidationError(
            f"Input validation failed: {exc.message}",
            node_id=node.id,
        ) from exc

# AFTER exec: validate output against output_schema
output_schema = params.get("output_schema")
if output_schema and isinstance(output, dict):
    try:
        jsonschema.validate(output, _sanitize_schema(output_schema))
    except jsonschema.ValidationError as exc:
        raise NodeValidationError(
            f"Output validation failed: {exc.message}",
            node_id=node.id,
        ) from exc
```

**Note:** `jsonschema.validate()` is synchronous. For V1, this is acceptable since node payloads are bounded in size. If very large payloads become common, wrap in `asyncio.to_thread()` later.

New exception: `NodeValidationError(NodeError)` in `packages/core/noodle/engine/types.py`.

#### Graph Validation (pre-execution)

In `packages/core/noodle/engine/validation.py`, `validate_graph()`:
- If a code node has `output_schema`, infer the port kinds from the schema's `properties` types
- If a downstream node expects a typed port, validate schema compatibility at build-plan time
- Emit `ValidationWarning` (not error) for schema mismatches — warn but don't block

`ValidationWarning` is a new dataclass in `packages/core/noodle/engine/types.py`:
```python
@dataclass
class ValidationWarning:
    node_id: str
    message: str
    severity: Literal["warning"] = "warning"
```
`validate_graph()` returns `list[ValidationWarning]` in addition to raising `NodeValidationError` for hard failures. The runner logs warnings but does not fail the run.

#### Frontend — Schema Editor

New component `apps/web/src/editor/fields/CodeNodeSchemaEditor.tsx`:
- Tab in code node Inspector: "Schema" alongside "Code" and "Config"
- JSON Schema builder UI: add property name, select type from dropdown (string, number, integer, boolean, array, object, enum)
- "Required" checkbox per property
- Raw JSON toggle for power users
- Live preview shows: what ports will be typed, what validation will run

**Port kind inference:** When schema set, update node's displayed output ports dynamically:
- `output.result` with type `number` → amber "number" badge on port
- `output.category` with enum → cyan "enum" badge

**Dependency:** Add `jsonschema>=4.23` to `packages/core/pyproject.toml`. It is already a transitive dependency of several API packages but must be declared explicitly for the core package.

**Files to create:**
- `apps/web/src/editor/fields/CodeNodeSchemaEditor.tsx`
- `apps/api/tests/test_code_node_typed_io.py`

**Files to modify:**
- `packages/core/pyproject.toml` — add `jsonschema>=4.23`
- `packages/core/noodle/engine/node_exec.py` — pre/post validation
- `packages/core/noodle/engine/types.py` — `NodeValidationError`
- `packages/core/noodle/engine/validation.py` — schema-aware port checking
- `apps/web/src/editor/Inspector.tsx` — add Schema tab for code nodes
- `packages/core/tests/test_engine_validation.py` — new schema tests

**Acceptance criteria:**
- [ ] Code node with `input_schema` — invalid input raises `NodeValidationError` with field name
- [ ] Code node with `output_schema` — invalid output raises `NodeValidationError`
- [ ] Valid inputs pass through without overhead
- [ ] Schema editor UI: can add/remove properties, set types, mark required
- [ ] Port badge shows type annotation when schema set
- [ ] No regression: code node without schema works as before (Any)
- [ ] Test: `test_code_node_input_schema_rejects_wrong_type`
- [ ] Test: `test_code_node_output_schema_rejects_wrong_output`
- [ ] Test: `test_code_node_no_schema_unchanged`

---

### Slice 3E: AI-Generated Custom Typed Nodes (2 weeks)

**What this unlocks:** User types "I need a node that calls the Resend API to send emails with attachments" → AI generates a complete `@node`-decorated Python function with typed ports → appears in palette under "AI Generated" → draggable, editable, versioned.

#### Backend

New mode `generate_node` in `apps/api/app/services/ai_builder.py`:

```python
async def generate_custom_node(
    description: str,
    *,
    session: AsyncSession,
    org_id: str,
    existing_node_ids: list[str] | None = None,
) -> dict:
    """Generate a @node-decorated Python function from a natural language description.
    
    Returns:
        {
          "code": str,             # Complete @node function source
          "node_id": str,          # Suggested node ID (e.g. "resend_send_email")
          "node_name": str,        # Human name (e.g. "Resend Send Email")
          "input_ports": dict,     # {port_name: kind}
          "output_ports": dict,
          "is_template": bool,     # True if LLM unavailable and a TODO stub was returned
          "warnings": list[str],   # e.g. ["blocked import 'os' removed"]
        }
    """
    system_msg = _build_node_gen_system_prompt()
    user_msg = f"Generate a Noodle @node function that: {description}"
    
    # _call_llm_simple already exists in ai_builder.py:649.
    # It returns dict | None (parsed JSON from LLM). The system prompt must instruct the LLM
    # to respond with {"code": "<python source>"} so we can extract result["code"] here.
    # None return = no LLM configured → use fallback template.
    try:
        result = await _call_llm_simple(user_msg, system=system_msg)
    except Exception:
        result = None
    if result is None:
        return _generate_fallback_template(description)
    raw_code = result.get("code", "")
    return _parse_and_validate_node_code(raw_code, existing_node_ids or [])
```

The system prompt provides:
- The `@node` decorator API and `NodeParam` schema
- The `RuntimeContext` API (`ctx.org_id`, `ctx.node_params`, `ctx.run_id`)
- The credential access pattern (`credential("my-api-key")`)
- 3 example `@node` functions (HTTP call, data transform, AI call)
- The `PortDataKind` enum (so AI uses real types)
- Constraint: no `os`, `socket`, `subprocess` imports
- Output format: raw Python code only, no markdown fences

New route in `apps/api/app/routers/code_modules.py`:
```
POST /code-modules/generate-node
Body: {
  description: str,
  scope: "environment"|"global",   -- "workflow" scope removed: generated nodes are reusable
  scope_id?: str                   -- environment_id for "environment" scope
}
→ Returns generated code + metadata (including is_template: bool)
→ Client displays code in Monaco editor for review/editing, then calls POST /code-modules to register
```

**Note on scope:** Workflow-scoped generated nodes are removed from the design — they add complexity without benefit. If a user generates a node, they almost certainly want to reuse it. Scope choices are `environment` (available within one environment) or `global` (available org-wide).

The registered node gets `type = "user:{scope}:{function_name}"` and appears in palette under "AI Generated" with a sparkle badge. The sparkle badge state is stored as `metadata.ai_generated = true` in the `CodeModule` row's existing `metadata` JSONB field (no new column needed).

#### Validation Pipeline

`_parse_and_validate_node_code()` runs these checks before returning:
1. `ast.parse(code)` — syntax check
2. AST walk: verify `@node` decorator is present
3. Extract `id=`, `name=`, `category=`, `input_kinds=`, `output_kinds=` from decorator kwargs
4. Verify no blocked imports (`os`, `socket`, `subprocess`, etc.)
5. If LLM returns markdown fences, strip them
6. If validation fails → retry once with error feedback appended to prompt
7. If retry fails → return fallback template with `TODO` stubs

#### Deterministic Fallback

No LLM available → generate a template:
```python
# Generated by Noodle (no AI — fill in the TODO sections)
@node(
    id="custom__{node_name}",
    name="{Node Name}",
    category="AI Generated",
    description="{description}",
    input_kinds={"main": "any"},
    output_kinds={"main": "any"},
)
def {node_name}(input: Any = None, *, ctx: RuntimeContext) -> Any:
    # TODO: implement "{description}"
    output = input
    return output
```

#### Frontend

1. **Palette "Generate Node" button:**
   - At bottom of palette: "+ Generate Node" (pencil + sparkle icon)
   - Opens modal: `GenerateNodeModal.tsx`
   - Input: description textarea + scope selector (environment/global — NOT workflow)
   - Shows generated code in Monaco editor (editable before saving)
   - "Regenerate" button
   - "Save as Node" → `POST /code-modules/generate-node` + `POST /code-modules`

2. **Palette "AI Generated" category:**
   - Shows all user-generated nodes with sparkle badge
   - Hover shows original description + generation date

3. **Node tile in canvas:**
   - Has sparkle badge in corner
   - Click badge → opens original description + "Regenerate" button

**Files to create:**
- `apps/web/src/editor/GenerateNodeModal.tsx`
- `apps/api/tests/test_generate_node.py`

**Files to modify:**
- `apps/api/app/services/ai_builder.py` — `generate_custom_node()` + system prompt
- `apps/api/app/routers/code_modules.py` — new generate route
- `apps/api/app/schemas.py` — `GenerateNodeRequest` / `GenerateNodeResponse`
- `apps/web/src/editor/NodePalette.tsx` — "AI Generated" category + generate button

**Acceptance criteria:**
- [ ] POST `/code-modules/generate-node` with description returns Python code with `@node` decorator
- [ ] Generated code passes AST validation (no blocked imports)
- [ ] Saving registers node in palette immediately
- [ ] Saved node executes in a workflow
- [ ] Fallback template returned when no LLM configured
- [ ] `id` collision detection: generate unique ID if name already registered
- [ ] Test: `test_generate_node_from_description_has_valid_syntax`
- [ ] Test: `test_generate_node_blocks_os_imports`
- [ ] Test: `test_generate_node_fallback_when_no_llm`
- [ ] Test: `test_generated_node_executes_in_workflow`

---

### MS3 Gate

All criteria must be green before starting MS4:

- [ ] Interactive editor tour fires for first-time users, can be dismissed, persists
- [ ] AI draft modal has "Visual Diff" tab; per-node accept/reject works
- [ ] At least one external MCP server connectable; tools appear as palette nodes
- [ ] MCP tool node executes at runtime; output flows to connected nodes
- [ ] Code node with `input_schema` + `output_schema` validates at execution
- [ ] `POST /code-modules/generate-node` returns valid, executable `@node` code
- [ ] Generated node appears in palette and can be dragged onto canvas
- [ ] All CI lanes green; all existing tests pass

---

## MS4: Enterprise & Scale (8 weeks)

### Overview

MS4 transforms Noodle from a compelling solo/team product into one that enterprise buyers can trust. Five slices, ordered by impact:

| Slice | Feature | Effort | Strategic Value |
|-------|---------|--------|-----------------|
| 4A | SAML/OIDC SSO | 2.5 weeks | Enterprise table stakes |
| 4B | Advanced RBAC + Audit Logs | 1.5 weeks | Compliance + accountability |
| 4C | External KMS (Vault, AWS KMS, GCP KMS) | 1.5 weeks | Enterprise key management |
| 4D | Real-Time Agentic Build Loop | 2 weeks | Flagship AI differentiator |
| 4E | Community Node Registry | 1.5 weeks | Ecosystem + distribution |

---

### Slice 4A: SAML/OIDC SSO (2.5 weeks)

`Feature.SSO` already exists in `services/licensing.py`. The gating mechanism is built. What's missing is the implementation.

#### Architecture

**OIDC flow** (Google, Okta, Azure AD with OpenID Connect):
```
Browser                    Noodle API                    IdP (OIDC)
   │                           │                                │
   │── GET /auth/sso/start ──► │                                │
   │                           │  build authorization URL       │
   │◄─ 302 to IdP login ───── │                                │
   │                           │                         [user logs in]
   │◄─ 302 callback?code=... ─ │◄─────────────────────────────│
   │── GET /auth/sso/callback ► │                                │
   │                           │── POST token_endpoint ────────►│
   │                           │◄─ {access_token, id_token} ── │
   │                           │  validate id_token (nonce/sig) │
   │◄─ Set JWT cookie ──────── │                                │
```

**SAML flow** (enterprise IdPs: Okta, Azure AD, PingFederate):
```
Browser                    Noodle API                    IdP (SAML)
   │                           │                                │
   │── GET /auth/sso/start ──► │                                │
   │                           │  build SAMLRequest (AuthnRequest)
   │◄─ 302 to IdP + SAMLRequest│                                │
   │── POST to IdP SSO URL ───►│                         [user logs in]
   │◄─ HTML form + SAMLResponse│                                │
   │── POST /auth/sso/acs ────►│                                │
   │                           │  verify SAMLResponse sig       │
   │                           │  extract NameID + attributes   │
   │◄─ Set JWT cookie ──────── │                                │
```

These are two completely separate flows. The `protocol` field in `sso_configs` (`'oidc'` or `'saml'`) determines which path executes. The `/auth/sso/start` endpoint branches on `sso_config.protocol`; the callback endpoints are separate (`/auth/sso/callback` for OIDC, `/auth/sso/acs` for SAML).

#### Data Model

New table `sso_configs`:
```sql
CREATE TABLE sso_configs (
    id              TEXT PRIMARY KEY DEFAULT gen_ulid(),
    org_id          TEXT NOT NULL UNIQUE REFERENCES organizations(id) ON DELETE CASCADE,
    protocol        TEXT NOT NULL DEFAULT 'oidc',  -- 'saml' | 'oidc'
    -- OIDC fields
    client_id       TEXT,
    client_secret   TEXT,           -- single-field Fernet-encrypted with org KEK (NOT two-field DEK)
    discovery_url   TEXT,           -- e.g. https://accounts.google.com/.well-known/openid-configuration
    -- SAML fields (IdP-provided values)
    idp_entity_id   TEXT,           -- IdP entity ID (not SP entity ID)
    idp_sso_url     TEXT,           -- IdP single sign-on URL
    idp_certificate TEXT,           -- IdP public cert (PEM) for signature validation
    -- Common
    email_domain    TEXT,           -- e.g. "acme.com" — used for login-page auto-detection
    attribute_map   JSONB NOT NULL DEFAULT '{}'::jsonb,  -- IdP claim → Noodle field
    jit_provisioning BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX sso_configs_email_domain ON sso_configs(email_domain) WHERE email_domain IS NOT NULL;
```

ORM model in `apps/api/app/models.py`:
```python
class SSOConfig(Base):
    __tablename__ = "sso_configs"
    id: Mapped[str] = mapped_column(Text, primary_key=True, default=gen_ulid)
    org_id: Mapped[str] = mapped_column(Text, ForeignKey("organizations.id", ondelete="CASCADE"), unique=True)
    protocol: Mapped[str] = mapped_column(Text, nullable=False, default="oidc")
    client_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_secret: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet-encrypted
    discovery_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    idp_entity_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    idp_sso_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    idp_certificate: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    attribute_map: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    jit_provisioning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
```

**Column naming:** Columns are explicitly prefixed `idp_` (IdP entity ID, IdP SSO URL, IdP certificate) to distinguish IdP-provided values from SP (Noodle's own) values. The SP entity ID and ACS URL are computed from `settings.base_url` at runtime — they don't need columns.

#### OIDC Flow (implement first — covers 90% of enterprise IdPs)

Dependencies: `python-jose` (already present for JWT), `httpx` (already present), `authlib` (add to `apps/api/pyproject.toml`).

**State storage:** OIDC state and nonce must be persisted server-side to prevent CSRF and replay attacks. Use Redis: `noodle:sso:state:{state}` → `{"nonce": ..., "org_id": ...}` with 600s TTL. Do NOT store state only in a cookie — cookie-based state is vulnerable to CSRF with certain IdP configurations. Redis is already available; this avoids a separate `sso_pending_states` table and migration.

```python
# apps/api/app/services/sso.py

async def oidc_authorization_url(sso_config: SSOConfig, *, session: AsyncSession) -> str:
    """Build the OIDC authorization redirect URL. Returns the redirect URL only.
    The state value is already persisted in Redis — callers don't need it."""
    discovery = await _fetch_oidc_discovery(sso_config.discovery_url)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    # Persist state+nonce in Redis with 10-min TTL for callback validation
    await redis_client.set(
        f"noodle:sso:state:{state}",
        json.dumps({"nonce": nonce, "org_id": sso_config.org_id}),
        ex=600,
    )
    params = {
        "client_id": sso_config.client_id,
        "redirect_uri": f"{settings.base_url}/auth/sso/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
    }
    return f"{discovery['authorization_endpoint']}?{urllib.parse.urlencode(params)}"

async def oidc_exchange_code(
    sso_config: SSOConfig, code: str, state: str, *, session: AsyncSession
) -> dict:
    """Exchange authorization code for tokens, validate nonce, return user claims."""
    # Validate state and retrieve nonce (single-use: delete atomically via GET+DEL)
    raw = await redis_client.getdel(f"noodle:sso:state:{state}")
    if raw is None:
        raise AuthError("Invalid or expired SSO state — CSRF protection")
    pending = json.loads(raw)
    if pending["org_id"] != sso_config.org_id:
        raise AuthError("SSO state org mismatch")
    nonce = pending["nonce"]
    discovery = await _fetch_oidc_discovery(sso_config.discovery_url)
    client_secret = await _decrypt_client_secret(sso_config, session)
    tokens = await _token_exchange(discovery, sso_config, code, client_secret)
    # Validate ID token: signature, iss, aud, exp, iat, nonce
    claims = await _validate_id_token(tokens["id_token"], discovery, nonce=nonce)
    return {
        "email": claims["email"],
        "name": claims.get("name") or claims.get("email"),
        "sub": claims["sub"],
    }


_discovery_cache: dict[str, tuple[float, dict]] = {}
_discovery_lock = asyncio.Lock()
_jwks_cache: dict[str, tuple[float, dict]] = {}

async def _fetch_oidc_discovery(discovery_url: str) -> dict:
    """Fetch and cache the OIDC discovery document (1h TTL). Thread-safe via lock."""
    import time
    now = time.monotonic()
    # Fast path: return cached doc without taking the lock
    if discovery_url in _discovery_cache:
        cached_at, doc = _discovery_cache[discovery_url]
        if now - cached_at < 3600:
            return doc
    async with _discovery_lock:
        # Re-check under lock — another coroutine may have fetched while we waited
        if discovery_url in _discovery_cache:
            cached_at, doc = _discovery_cache[discovery_url]
            if now - cached_at < 3600:
                return doc
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(discovery_url)
            resp.raise_for_status()
            doc = resp.json()
        _discovery_cache[discovery_url] = (time.monotonic(), doc)
        return doc


async def _decrypt_client_secret(sso_config: SSOConfig, session: AsyncSession) -> str | None:
    """Decrypt sso_config.client_secret (single-field Fernet-encrypted with org KEK)."""
    if not sso_config.client_secret:
        return None
    # Same single-field Fernet pattern as auth_secret in mcp_connections.
    # Verify _get_org_kek() function name against org_keys.py.
    from cryptography.fernet import Fernet
    org_kek = await _get_org_kek(sso_config.org_id, session)
    return Fernet(org_kek).decrypt(sso_config.client_secret.encode()).decode()


async def _token_exchange(
    discovery: dict,
    sso_config: SSOConfig,
    code: str,
    client_secret: str | None,
) -> dict:
    """POST authorization code to token endpoint. Returns {access_token, id_token, ...}."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            discovery["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": sso_config.client_id,
                "client_secret": client_secret,
                "redirect_uri": f"{settings.base_url}/auth/sso/callback",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _validate_id_token(id_token: str, discovery: dict, *, nonce: str) -> dict:
    """Validate OIDC ID token using authlib. Raises AuthError on any failure.
    Verifies: signature (using JWKS from discovery), iss, aud, exp, iat, nonce.
    Must be async because JWKS fetch is async (cached with 1h TTL in _jwks_cache)."""
    import time
    from authlib.jose import JsonWebToken, JsonWebKey
    # Fetch JWKS with same caching strategy as discovery doc
    jwks_uri = discovery["jwks_uri"]
    now = time.monotonic()
    if jwks_uri not in _jwks_cache or now - _jwks_cache[jwks_uri][0] >= 3600:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(jwks_uri)
            resp.raise_for_status()
        _jwks_cache[jwks_uri] = (time.monotonic(), resp.json())
    jwks_data = _jwks_cache[jwks_uri][1]
    jwt = JsonWebToken(["RS256", "ES256"])
    claims = jwt.decode(id_token, JsonWebKey.import_key_set(jwks_data))
    claims.validate()  # validates exp, iat, iss
    if claims.get("nonce") != nonce:
        raise AuthError("ID token nonce mismatch — replay attack suspected")
    if "email" not in claims:
        raise AuthError("ID token missing email claim")
    return dict(claims)
```

Routes in `apps/api/app/routers/auth.py`:
```
GET  /auth/sso/start?org_slug=acme         — look up org by slug → look up sso_config by org_id → redirect to IdP
GET  /auth/sso/callback?code=...&state=... — exchange code (org_id recovered from Redis state), create session
GET  /auth/sso/detect?email=...            — return {has_sso: bool, org_slug?} for login-page detection (GET, not POST — idempotent read)
GET  /auth/sso/metadata?org_slug=acme      — SAML SP metadata XML
POST /auth/sso/acs                         — SAML assertion consumer
```

**`/auth/sso/start` org resolution:** `org_slug` → `organizations.slug` → `organizations.id` → `sso_configs.org_id`. The `org_id` is then carried in the Redis state payload so `/auth/sso/callback` doesn't need a slug parameter.

**`organizations.slug` migration:** Run `grep -r "slug" apps/api/app/models.py` before implementing. If a `slug` column doesn't exist, add `apps/api/alembic/versions/XXXX_org_slug.py` to 4A files-to-create. If it exists, no migration needed. The slug must be `UNIQUE NOT NULL` with a `CHECK (slug ~ '^[a-z0-9-]+$')` constraint.

Admin routes in `apps/api/app/routers/admin.py`:
```
GET    /admin/sso                — get org SSO config
POST   /admin/sso                — create/update SSO config
DELETE /admin/sso                — remove SSO config
POST   /admin/sso/test           — test IdP connection
```

#### SAML Flow (implement second, week 2)

Dependencies: `python3-saml` (chosen over `pysaml2` — lighter, actively maintained, easier FastAPI integration). Add to `apps/api/pyproject.toml`.

- `GET /auth/sso/metadata` → returns SP metadata XML (entity ID, ACS URL, certificate)
- `POST /auth/sso/acs` → validates SAMLResponse, extracts NameID + attribute claims

#### JIT Provisioning

When SSO user logs in for first time:
```python
# apps/api/app/services/sso.py
async def get_or_create_sso_user(claims: dict, sso_config: SSOConfig, session: AsyncSession) -> User:
    email = claims["email"].lower()
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        if not sso_config.jit_provisioning:
            raise AuthError("JIT provisioning disabled; user must be pre-invited")
        user = User(
            email=email,
            name=claims.get("name", email),
            email_verified=True,   # SSO = pre-verified by IdP
            sso_subject=claims["sub"],  # new column on User (see migration below)
        )
        session.add(user)
        await session.flush()  # get user.id before creating membership
    
    # Always ensure org membership exists — handles both new JIT users AND
    # existing users from other orgs who are logging into this org for the first time.
    existing_membership = await session.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.org_id == sso_config.org_id,
        )
    )
    if existing_membership is None:
        if not sso_config.jit_provisioning:
            raise AuthError("JIT provisioning disabled; user must be pre-invited")
        session.add(Membership(user_id=user.id, org_id=sso_config.org_id, role="member"))
        await session.flush()
    return user
```

#### Frontend

New admin page `apps/web/src/settings/SSOSettingsPage.tsx`:
- Protocol selector (OIDC / SAML)
- OIDC: Discovery URL + Client ID + Client Secret
- SAML: Entity ID + SSO URL + Certificate paste
- "Test Connection" button
- "Download SP Metadata" for SAML
- Login page: auto-detect SSO org from email domain, show "Sign in with SSO" button

**Files to create:**
- `apps/api/app/services/sso.py`
- `apps/api/alembic/versions/XXXX_sso_configs.py` — creates `sso_configs` table only (pending states use Redis, no DB table needed)
- `apps/api/alembic/versions/XXXX_user_sso_subject.py` — adds `sso_subject TEXT` column to `users`
- `apps/web/src/settings/SSOSettingsPage.tsx`
- `apps/api/tests/test_sso_oidc.py`
- `apps/api/tests/test_sso_saml.py`

**Files to modify:**
- `apps/api/app/models.py` — `SSOConfig` model + `User.sso_subject` column (no `SSOPendingState` model — Redis-backed)
- `apps/api/app/routers/auth.py` — SSO routes
- `apps/api/app/services/licensing.py` — wire `Feature.SSO` gate to SSO routes
- `apps/api/pyproject.toml` — add `authlib`, `python3-saml`
- `apps/web/src/auth/LoginPage.tsx` — SSO button + email domain detection (`GET /auth/sso/detect?email=...` → returns `{has_sso: bool, org_slug?: string}` if domain matches)

**Acceptance criteria:**
- [ ] OIDC: Okta config → "Test Connection" → success
- [ ] OIDC: Browser flow → redirect → login → JWT cookie → authenticated
- [ ] JIT: First SSO login creates user + org membership
- [ ] JIT: Subsequent SSO login reuses existing user
- [ ] SAML: SP metadata XML generated correctly
- [ ] SAML: SAMLResponse validated + user extracted
- [ ] `Feature.SSO` gate: SSO routes 402 if org doesn't have feature
- [ ] Test: `test_oidc_callback_creates_new_user_jit`
- [ ] Test: `test_saml_acs_validates_signature`
- [ ] Test: `test_sso_without_feature_flag_returns_402`

---

### Slice 4B: Advanced RBAC + Audit Logs (1.5 weeks)

`Feature.ADVANCED_RBAC` and `Feature.AUDIT_LOGS` are gated but unimplemented.

#### Current State

Current roles: `owner`, `admin`, `member`, `viewer`. Permissions are coarse-grained (`workflow:read`, `workflow:run`, etc.).

#### Advanced RBAC

**Custom roles** with per-resource permission sets:

```sql
-- New table: custom_roles
CREATE TABLE custom_roles (
    id          TEXT PRIMARY KEY DEFAULT gen_ulid(),
    org_id      TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    permissions JSONB NOT NULL DEFAULT '[]',  -- list of permission strings
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX custom_roles_org_name ON custom_roles(org_id, name);

-- Extend memberships: custom_role_id nullable FK — ON DELETE SET NULL reverts member to built-in role
ALTER TABLE memberships ADD COLUMN custom_role_id TEXT REFERENCES custom_roles(id) ON DELETE SET NULL;
```

Permission strings extend the existing set:
```python
# apps/api/app/security.py

# Permissions assignable to custom roles — operational permissions only.
# Admin/billing/SSO permissions are EXCLUDED: they remain locked to built-in roles.
CUSTOM_ROLE_PERMISSION_REGISTRY = {
    "workflow:read", "workflow:write", "workflow:run", "workflow:delete",
    "workflow:publish",        # can publish but not delete
    "run:cancel_others",       # can cancel runs they didn't start
    "credential:read_names",   # can see credential names but not values
    "credential:create",       # can create credentials
    "audit:read",              # can view audit log
    "mcp_connection:manage",   # can create/delete MCP connections
}

# Built-in role permission maps (NOT overridable by custom roles)
_BUILTIN_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner":  frozenset(CUSTOM_ROLE_PERMISSION_REGISTRY | {"admin:users", "admin:sso", "admin:billing", "node_registry:install"}),
    "admin":  frozenset(CUSTOM_ROLE_PERMISSION_REGISTRY | {"admin:users", "node_registry:install"}),
    "member": frozenset({"workflow:read", "workflow:write", "workflow:run", "credential:create"}),
    "viewer": frozenset({"workflow:read"}),
}
```

`node_registry:install` is explicitly listed in `owner` and `admin` built-in maps but NOT in `CUSTOM_ROLE_PERMISSION_REGISTRY` — custom roles cannot grant registry install access.

New `require_permission()` behavior:
- If user has `custom_role_id` → check `custom_roles.permissions` ONLY (custom role REPLACES the built-in role for permission checks; the user's `role` string field is used only as fallback display label)
- Else → look up `_BUILTIN_ROLE_PERMISSIONS[user.role]`

**Semantic clarity:** A custom role is a complete replacement, not an additive layer. If a user has `custom_role_id=X` and `role="member"`, they get only what role X defines — NOT member permissions plus role X permissions. This prevents privilege escalation through stacking. Exception: `admin:*` permissions are never in any custom role, so they cannot be granted regardless of stacking.

Admin UI (`apps/web/src/settings/RolesPage.tsx`):
- Create custom role: name + checkbox grid of permissions
- Assign custom role to members
- Built-in roles locked (can't modify)

#### Audit Log

`apps/api/app/services/audit.py` already has `log_audit()`. Extend:

```sql
-- Already exists: audit_logs table
-- Verify columns: id, org_id, user_id, action, resource_type, resource_id, 
--                 metadata, ip_address, user_agent, created_at

-- Add: session_id column for correlating multi-step operations
ALTER TABLE audit_logs ADD COLUMN session_id TEXT;
ALTER TABLE audit_logs ADD COLUMN actor_type TEXT DEFAULT 'user';  -- 'user' | 'runner' | 'system'
```

New audit log viewer in `apps/web/src/settings/AuditLogPage.tsx`:
- Filterable by: user, action type, resource type, date range
- Exportable as CSV
- Retention: configurable (default 90 days)

Route: `GET /admin/audit-logs?user_id=...&action=...&resource_type=...&from=...&to=...`

**Python keyword note:** `from` is a reserved keyword in Python. The FastAPI route parameter must use `Query(alias="from")`:
```python
from_: datetime | None = Query(default=None, alias="from")
to: datetime | None = Query(default=None)
```

**Migrations required (both must be listed in files-to-create):**
- `apps/api/alembic/versions/XXXX_custom_roles.py` — creates `custom_roles` table + adds `custom_role_id` to `memberships` (ON DELETE SET NULL FK)
- `apps/api/alembic/versions/XXXX_audit_log_actor.py` — adds `session_id TEXT` and `actor_type TEXT DEFAULT 'user'` to `audit_logs`

Both are required. Do not combine into a single migration file — keeping them separate makes downgrade cleaner.

**Custom role safety:** `permissions` JSONB array is validated against `PERMISSION_REGISTRY` on write — unknown permission strings are rejected with 422. Prevents typo'd permissions from silently granting nothing (or granting everything if check is inverted).

**Custom role deletion cascade:** FK from `memberships.custom_role_id` should be `ON DELETE SET NULL` — deleting a role reverts affected members to their built-in role, not deletes their membership.

**Audit retention:** Configurable via `AUDIT_LOG_RETENTION_DAYS=90` env var. A background task (add to the existing `stuck_run_detector` loop or a new `maintenance_loop`) runs nightly and deletes rows older than the configured period.

**Files to create:**
- `apps/api/alembic/versions/XXXX_custom_roles.py` — `custom_roles` table + `memberships.custom_role_id` FK
- `apps/api/alembic/versions/XXXX_audit_log_actor.py` — `session_id` + `actor_type` columns
- `apps/web/src/settings/RolesPage.tsx` — custom role management UI
- `apps/web/src/settings/AuditLogPage.tsx` — audit log viewer + CSV export
- `apps/api/tests/test_custom_roles.py`
- `apps/api/tests/test_audit_log.py`

**Files to modify:**
- `apps/api/app/models.py` — `CustomRole` model + `Membership.custom_role_id`
- `apps/api/app/security.py` — `CUSTOM_ROLE_PERMISSION_REGISTRY`, updated `_BUILTIN_ROLE_PERMISSIONS`, `require_permission()` custom-role branch
- `apps/api/app/routers/admin.py` — custom role CRUD routes + audit log route
- `apps/web/src/App.tsx` / routing — add roles + audit log pages

**Acceptance criteria:**
- [ ] Custom role with specific permissions: user gets exactly those permissions
- [ ] Unknown permission string in role creation → 422 (not silently accepted)
- [ ] Deleting a custom role → affected members fall back to built-in role (membership preserved)
- [ ] Audit log records: logins, workflow creates/deletes/publishes, run starts, credential access
- [ ] Audit log UI filterable + exportable as CSV
- [ ] Audit log rows older than `AUDIT_LOG_RETENTION_DAYS` are purged by maintenance loop
- [ ] `Feature.AUDIT_LOGS` gate: audit log API returns 402 without feature
- [ ] `Feature.ADVANCED_RBAC` gate: custom role creation returns 402 without feature
- [ ] Test: `test_custom_role_grants_specific_permissions`
- [ ] Test: `test_custom_role_unknown_permission_rejected`
- [ ] Test: `test_audit_log_records_workflow_delete`

---

### Slice 4C: External KMS (Vault, AWS KMS, GCP KMS) (1.5 weeks)

`Feature.EXTERNAL_KMS` is gated. Current: master KEK lives in `SECRET_KEY` env var.

#### Architecture

```
┌─────────────────────────────────────────────────────┐
│  Credential Encryption (current + new)               │
│                                                      │
│  Current:   SECRET_KEY → HKDF → org KEK            │
│                                                      │
│  With KMS:  KMS Provider → decrypt(org KEK cipher) │
│             org KEK → DEK → credential value        │
└─────────────────────────────────────────────────────┘
```

The org KEK is encrypted with the master KEK. With external KMS, the master KEK is managed by the provider — Noodle never holds it plaintext.

#### KMS Provider Interface

New file `apps/api/app/services/kms/`:

```python
# apps/api/app/services/kms/base.py
from abc import ABC, abstractmethod

class KMSProvider(ABC):
    """Each provider handles key selection internally via its own config.
    No key_id in the interface — 'which key to use' is a provider-level concern,
    not a call-site concern. Vault uses key_name from config; AWS uses key_id from
    config; GCP uses key_name from config. This avoids callers needing to know
    the provider's key addressing scheme.
    """
    @abstractmethod
    async def encrypt(self, plaintext: bytes) -> bytes: ...
    
    @abstractmethod
    async def decrypt(self, ciphertext: bytes) -> bytes: ...
    
    @abstractmethod
    async def health_check(self) -> bool: ...

# apps/api/app/services/kms/vault.py   — HashiCorp Vault Transit
# apps/api/app/services/kms/aws_kms.py — AWS KMS (boto3; key ARN from config)
# apps/api/app/services/kms/gcp_kms.py — GCP Cloud KMS (google-cloud-kms)
# apps/api/app/services/kms/env_kms.py — existing behavior wrapped as KMSProvider
```

**`EnvKMSProvider`** wraps the current `org_keys.py` Fernet logic:
```python
# apps/api/app/services/kms/env_kms.py
class EnvKMSProvider(KMSProvider):
    """Wraps the existing SECRET_KEY → Fernet → encrypt/decrypt pattern.
    No network calls; zero config change for existing deployments.
    crypto.py uses Fernet (NOT raw AES-GCM). wrap_org_kek / unwrap_org_kek
    are the existing Fernet-backed helpers — delegate to them.
    """
    
    async def encrypt(self, plaintext: bytes) -> bytes:
        from app.services.crypto import wrap_org_kek
        # wrap_org_kek(key: bytes) -> str (Fernet token); encode to bytes for interface
        return wrap_org_kek(plaintext).encode()
    
    async def decrypt(self, ciphertext: bytes) -> bytes:
        from app.services.crypto import unwrap_org_kek
        # unwrap_org_kek(token: str) -> bytes
        return unwrap_org_kek(ciphertext.decode())
    
    async def health_check(self) -> bool:
        return True  # always healthy — no external dependency

# NOTE: No __init__ needed — wrap_org_kek / unwrap_org_kek use the global
# _fernet() singleton which derives from settings.secret_key automatically.
```

**`org_keys.py` refactor** (add to files-to-modify for 4C): The current inline encrypt/decrypt logic in `org_keys.py` is replaced with calls to `get_kms_provider().encrypt(...)` / `get_kms_provider().decrypt(...)`. `get_kms_provider()` returns a singleton based on `settings.kms_provider`.

**Vault implementation:**
```python
class VaultKMSProvider(KMSProvider):
    def __init__(self, vault_url: str, token: str, mount: str, key_name: str):
        self.vault_url = vault_url.rstrip("/")
        self.token = token
        self.mount = mount
        self.key_name = key_name
        # httpx.AsyncClient is not thread-safe to share across coroutines without
        # connection pooling care. For V1, create a new client per request (inside
        # encrypt/decrypt). For V2, use a single shared client with AsyncClient(limits=...).
        self._client = httpx.AsyncClient(timeout=10.0)
    
    async def encrypt(self, plaintext: bytes) -> bytes:
        b64 = base64.b64encode(plaintext).decode()
        resp = await self._client.post(
            f"{self.vault_url}/v1/{self.mount}/encrypt/{self.key_name}",
            json={"plaintext": b64},
            headers={"X-Vault-Token": self.token},
        )
        resp.raise_for_status()
        return resp.json()["data"]["ciphertext"].encode()
    
    async def decrypt(self, ciphertext: bytes) -> bytes:
        resp = await self._client.post(
            f"{self.vault_url}/v1/{self.mount}/decrypt/{self.key_name}",
            json={"ciphertext": ciphertext.decode()},
            headers={"X-Vault-Token": self.token},
        )
        resp.raise_for_status()
        return base64.b64decode(resp.json()["data"]["plaintext"])

# Known limitation: VaultKMSProvider uses a static token. Production deployments
# should use Vault AppRole or Kubernetes auth for automatic token rotation.
# Static token is acceptable for V1; document in deployment guide.
```

Config additions to `apps/api/app/config.py`:
```python
kms_provider: Literal["env", "vault", "aws", "gcp"] = "env"
vault_url: str | None = None
vault_token: str | None = None
vault_transit_mount: str = "transit"
vault_transit_key: str = "noodle-master"
aws_kms_key_id: str | None = None
aws_kms_region: str = "us-east-1"
gcp_kms_key_name: str | None = None  # projects/.../cryptoKeys/... (NOT cryptoKeyVersions — let GCP manage versioning)
```

Admin UI (`apps/web/src/settings/KMSSettingsPage.tsx`):
- Provider selector: Local (env) / Vault / AWS KMS / GCP KMS
- Provider-specific config fields
- "Test Connection" → calls `GET /admin/kms/health`
- Key rotation UI (future: trigger re-encryption with new master key)

**Critical — migration path for existing credentials:** When switching from `kms_provider=env` to an external KMS, existing credentials were encrypted with the env-var-derived master key. A one-time re-encryption script must be provided:

```python
# scripts/migrate_kms.py
# Usage: uv run python scripts/migrate_kms.py --from=env --to=vault
# 1. Decrypt each org's KEK using the old provider
# 2. Re-encrypt it using the new provider
# 3. Update the stored cipher in org_keys table
# Credentials themselves (DEK-encrypted) don't change — only the org KEK envelope changes.
```

This script must be run before switching `kms_provider` in env vars, not after. Document in deployment runbook. The script should be idempotent (re-running is safe).

**AWS authentication:** AWS KMS uses boto3 which picks up credentials from environment (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, or instance IAM role). No explicit config needed — document as "requires AWS credentials in environment or IAM role attached to instance."

**Files to create:**
- `apps/api/app/services/kms/base.py`
- `apps/api/app/services/kms/vault.py`
- `apps/api/app/services/kms/aws_kms.py`
- `apps/api/app/services/kms/gcp_kms.py`
- `apps/api/app/services/kms/env_kms.py`
- `apps/api/app/services/kms/__init__.py` — `get_kms_provider()` singleton factory
- `scripts/migrate_kms.py`
- `apps/web/src/settings/KMSSettingsPage.tsx`
- `apps/api/tests/test_kms.py`

**Files to modify:**
- `apps/api/app/config.py` — `kms_provider`, `vault_url`, `vault_token`, `vault_transit_mount`, `vault_transit_key`, `aws_kms_key_id`, `aws_kms_region`, `gcp_kms_key_name`
- `apps/api/app/services/org_keys.py` — replace inline encrypt/decrypt with `get_kms_provider().encrypt()` / `.decrypt()`
- `apps/api/app/services/licensing.py` — wire `Feature.EXTERNAL_KMS` gate to KMS config routes
- `apps/api/pyproject.toml` — add `boto3`, `google-cloud-kms` as optional deps

**Acceptance criteria:**
- [ ] `kms_provider=vault`: credentials encrypted with Vault Transit, decrypted at runtime
- [ ] `kms_provider=aws`: AWS KMS `Encrypt` / `Decrypt` calls work
- [ ] Existing `kms_provider=env` behavior unchanged (no regression)
- [ ] `scripts/migrate_kms.py --from=env --to=vault` re-encrypts org KEKs correctly
- [ ] `Feature.EXTERNAL_KMS` gate: KMS config API returns 402 without feature
- [ ] KMS failure → clear error (not silent) → credential access raises `CredentialUnavailableError`
- [ ] Test: `test_vault_kms_encrypt_decrypt_roundtrip` (mock Vault server)
- [ ] Test: `test_kms_migration_script_re_encrypts_kek` (mock old + new provider)
- [ ] Test: `test_kms_failure_blocks_credential_access`

---

### Slice 4D: Real-Time Agentic Build Loop (2 weeks)

**What this is:** An AI that builds a workflow, runs it with test data, reads the output, identifies problems, fixes the workflow, and re-runs — autonomously, until it converges or times out. The user watches the build loop in real time.

**Why this is the flagship MS4 feature:** No competitor does this. LangGraph/Flowise/Dify do agentic things, but they can't build editable, production workflows. n8n's AI is one-shot. Noodle's would be the only system that lets AI own the full loop: build → execute → inspect → repair → repeat.

#### Architecture

```
User: "Build a workflow that fetches GitHub PRs, scores them by complexity, 
       sends a Slack message for any with score > 7"

Agentic Loop (max 5 iterations):
  Iteration 1:
    → AI builds draft workflow (existing ai_builder.py)
    → Run with test data (existing runner)
    → Read output + node errors
    → If perfect: return workflow
    → Else: identify failing nodes, prepare fix

  Iteration 2:
    → AI fixes specific nodes (existing refine mode)
    → Re-run
    → Read output
    → If perfect: return workflow
    → Else: prepare next fix

  Max iterations reached → return best attempt with explanation
```

#### Backend

New endpoint:
```
POST /workflows/{id}/agentic-build
Body: {
  goal: str,              -- what the workflow should do
  test_data: dict | null, -- sample input for test runs
  max_iterations: int,    -- default 5, hard capped at 5
}
→ Server-Sent Events stream with progress updates
```

SSE events:
```json
{"type": "iteration_start", "iteration": 1, "action": "draft"}
{"type": "graph_updated", "graph": {...}, "explanation": "Added 3 nodes..."}
{"type": "run_started", "run_id": "..."}
{"type": "run_complete", "run_id": "...", "status": "completed", "outputs": {...}}
{"type": "run_failed", "run_id": "...", "errors": [{"node_id": "...", "error": "..."}]}
{"type": "fix_planned", "target_nodes": [...], "diagnosis": "..."}
{"type": "iteration_start", "iteration": 2, "action": "fix"}
{"type": "converged", "iterations": 2, "final_graph": {...}}
{"type": "max_iterations_reached", "best_graph": {...}, "remaining_errors": [...]}
```

Note: `"status": "completed"` matches the `RunStatus` enum value used in the existing runner — use the same string to avoid frontend confusion between SSE event status and DB status.

**`"status": "error"` event** — add this to the contract for LLM failures:
```json
{"type": "error", "message": "LLM call failed: rate limit exceeded"}
```

New service `apps/api/app/services/agentic_builder.py`:
```python
async def run_agentic_build_loop(
    workflow_id: str,
    goal: str,
    test_data: dict | None,
    *,
    max_iterations: int,
    session_factory,
    org_id: str,
    event_callback: Callable[[dict], Awaitable[None]],
    cancel_event: asyncio.Event,  # set by caller when SSE client disconnects
) -> dict:
    """Run the build→execute→diagnose→fix loop. Yields SSE events via event_callback."""
    
    current_graph = await _get_draft_graph(workflow_id, session_factory)
    latest_graph = current_graph          # tracks most recent AI-generated graph
    failing_nodes: list[str] = []         # initialize before loop (used from iteration 2+)
    error_details: list[dict] = []
    
    for iteration in range(1, max_iterations + 1):
        if cancel_event.is_set():
            break  # client disconnected — stop cleanly; latest_graph is the best attempt
        
        await event_callback({"type": "iteration_start", "iteration": iteration, "action": "draft" if iteration == 1 else "fix"})
        
        try:
            if iteration == 1:
                new_graph, explanation = await _ai_draft(goal, workflow_id, session_factory)
            else:
                new_graph, explanation = await _ai_fix(
                    goal, workflow_id, current_graph, failing_nodes, error_details, session_factory
                )
        except Exception as exc:
            await event_callback({"type": "error", "message": f"LLM call failed: {exc}"})
            return latest_graph
        
        latest_graph = new_graph  # always update — latest is the best we have
        await event_callback({"type": "graph_updated", "graph": new_graph, "explanation": explanation})
        await _save_draft_graph(workflow_id, new_graph, session_factory)
        
        run_id = await _start_test_run(workflow_id, test_data, org_id, session_factory)
        await event_callback({"type": "run_started", "run_id": run_id})
        
        # _wait_for_run polls with asyncio.sleep(0.5) intervals — non-blocking
        outcome = await _wait_for_run(run_id, timeout=120, cancel_event=cancel_event,
                                      session_factory=session_factory)
        
        # "completed" matches the RunStatus enum value in the existing runner
        if outcome["status"] == "completed":
            await event_callback({"type": "converged", "iterations": iteration, "final_graph": new_graph})
            return new_graph
        
        failing_nodes, error_details = _extract_failures(outcome)
        await event_callback({"type": "run_failed", "run_id": run_id, "errors": error_details})
        current_graph = new_graph  # next iteration fixes from this point
    
    # Return latest_graph (the most recently generated version), not original current_graph
    await event_callback({"type": "max_iterations_reached", "best_graph": latest_graph, "remaining_errors": error_details})
    return latest_graph
```

**Helper function signatures** (all in `apps/api/app/services/agentic_builder.py`):

```python
async def _get_draft_graph(workflow_id: str, session_factory) -> dict:
    """Load the current draft graph from the workflow's `draft` field."""
    async with session_factory() as session:
        wf = await session.scalar(select(Workflow).where(Workflow.id == workflow_id))
        return wf.draft or {"nodes": [], "edges": []}

async def _save_draft_graph(workflow_id: str, graph: dict, session_factory) -> None:
    """Persist the draft graph to the workflow row (same field the editor uses)."""
    async with session_factory() as session:
        wf = await session.scalar(select(Workflow).where(Workflow.id == workflow_id))
        wf.draft = graph
        await session.commit()

async def _ai_draft(
    goal: str,
    workflow_id: str,
    session_factory,
) -> tuple[dict, str]:
    """Generate the initial graph from a goal string.
    Delegates to build_workflow_draft() — the actual function in ai_builder.py.
    Returns (graph_dict, explanation)."""
    from app.schemas import AiWorkflowDraftRequest
    from app.services.ai_builder import build_workflow_draft
    async with session_factory() as session:
        result = await build_workflow_draft(
            session, workflow_id,
            AiWorkflowDraftRequest(prompt=goal, mode="draft"),
        )
    return result.graph.model_dump(), result.explanation or ""


async def _ai_fix(
    goal: str,
    workflow_id: str,
    current_graph: dict,
    failing_nodes: list[str],
    error_details: list[dict],
    session_factory,
) -> tuple[dict, str]:
    """Fix failing nodes in the current graph.
    Uses mode="refine" with target_node_ids so the LLM focuses on the broken nodes.
    Returns (graph_dict, explanation)."""
    from noodle.models import WorkflowGraph
    from app.schemas import AiWorkflowDraftRequest
    from app.services.ai_builder import build_workflow_draft
    error_summary = "; ".join(
        f"{e['node_id']}: {e['error']}" for e in error_details
    )
    fix_prompt = f"Goal: {goal}\n\nFix these failing nodes: {error_summary}"
    async with session_factory() as session:
        result = await build_workflow_draft(
            session, workflow_id,
            AiWorkflowDraftRequest(
                prompt=fix_prompt,
                mode="refine",
                current_graph=WorkflowGraph.model_validate(current_graph),
                target_node_ids=failing_nodes,
            ),
        )
    return result.graph.model_dump(), result.explanation or ""

async def _start_test_run(
    workflow_id: str,
    test_data: dict | None,
    org_id: str,
    session_factory,
) -> str:
    """Insert a Run row and enqueue it. Returns the new run_id.
    Mirrors what POST /workflows/{id}/run does internally — call the same
    service-layer function (e.g. `enqueue_workflow_run()` in services/runner.py)
    rather than duplicating the DB insert logic. Verify the function name
    by grepping for where workflow runs are created in the existing router."""
    async with session_factory() as session:
        # Pattern: create Run row, set status="queued", flush, enqueue
        # Verify against existing run-creation code in routers/workflows.py
        run = Run(
            id=gen_ulid(),
            workflow_id=workflow_id,
            org_id=org_id,
            status="queued",
            input=test_data or {},
        )
        session.add(run)
        await session.commit()
        # Enqueue in Redis queue (or DB queue if Redis unavailable)
        from app.services.queue_redis import enqueue as redis_enqueue
        await redis_enqueue(run.id)
        return run.id

async def _wait_for_run(
    run_id: str,
    timeout: float,
    cancel_event: asyncio.Event,
    session_factory,   # explicit parameter — NOT a closure variable
) -> dict:
    """Poll run status until terminal (completed/failed/cancelled) or timeout.
    Returns {"status": str, "node_results": dict, "error": str | None}."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if cancel_event.is_set():
            return {"status": "cancelled", "node_results": {}, "error": None}
        async with session_factory() as session:
            run = await session.get(Run, run_id)
        if run and run.status in ("completed", "failed", "cancelled"):
            return {
                "status": run.status,
                "node_results": run.node_results or {},
                "error": run.error,
            }
        await asyncio.sleep(0.5)
    return {"status": "failed", "node_results": {}, "error": "timeout"}

def _extract_failures(outcome: dict) -> tuple[list[str], list[dict]]:
    """Extract failing node IDs and their error details from a run outcome dict."""
    failing_nodes: list[str] = []
    error_details: list[dict] = []
    for node_id, result in (outcome.get("node_results") or {}).items():
        if result.get("status") == "error":
            failing_nodes.append(node_id)
            error_details.append({"node_id": node_id, "error": result.get("error", "")})
    return failing_nodes, error_details
```

**Note on `_ai_fix` mode:** Uses `mode="refine"` on the existing `build_workflow_draft()` in `ai_builder.py` with `target_node_ids=failing_nodes`. The `_refine_workflow()` path already handles targeted node repair from a conversation prompt. No new function needed in `ai_builder.py` — `mode="refine"` with a fix prompt and `target_node_ids` is sufficient for the agentic loop's repair iterations.

**SSE router integration** (in `apps/api/app/routers/agentic_build.py`):
```python
@router.post("/{workflow_id}/agentic-build")
async def agentic_build(workflow_id: str, req: AgenticBuildRequest, ...):
    cancel_event = asyncio.Event()
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    
    async def event_callback(event: dict) -> None:
        await queue.put(event)
    
    async def run_loop_and_signal_done():
        try:
            await run_agentic_build_loop(
                workflow_id=workflow_id,
                goal=req.goal,
                test_data=req.test_data,
                max_iterations=req.max_iterations,
                session_factory=session_factory,
                org_id=current_org_id,
                event_callback=event_callback,
                cancel_event=cancel_event,
            )
        except asyncio.CancelledError:
            pass
        finally:
            await queue.put(None)  # sentinel: signals stream() to stop
    
    async def stream():
        task = asyncio.create_task(run_loop_and_signal_done())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # prevent proxy/browser timeout
                    continue
                if event is None:
                    break
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            cancel_event.set()  # client disconnected — signal loop to stop
            task.cancel()
    
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

**Note:** `X-Accel-Buffering: no` prevents nginx from buffering SSE responses.

**LLM cost guardrail:** Each iteration makes up to 2 LLM calls. With `max_iterations=5`, that's up to 10 calls per invocation. Add to the `AgenticBuildRequest` schema:
```python
max_iterations: int = Field(default=5, ge=1, le=5)  # hard cap at 5 — not user-overridable above 5
```
Also add per-org rate limiting: max 20 agentic build iterations per org per hour, enforced at the route level using the existing rate-limit infrastructure.

#### Frontend

New modal/panel `apps/web/src/editor/AgenticBuildPanel.tsx`:
- Goal textarea at top
- Test data JSON editor
- "Start Build Loop" button
- Live progress:
  - Iteration counter (1 of 5)
  - Current action badge ("Drafting..." / "Running..." / "Fixing...")
  - Graph diff panel showing what changed each iteration
  - Run output preview after each test run
  - Error highlight: failing nodes shown with red border on mini-canvas
- "Accept this version" button after convergence (or after any iteration)
- "Cancel loop" button

**Files to create:**
- `apps/api/app/services/agentic_builder.py`
- `apps/api/app/routers/agentic_build.py`
- `apps/web/src/editor/AgenticBuildPanel.tsx`
- `apps/api/tests/test_agentic_build.py`

**Acceptance criteria:**
- [ ] Agentic loop makes at most `max_iterations` iterations
- [ ] Each iteration emits correct SSE events
- [ ] Graph is updated in DB after each iteration
- [ ] Test run failures are correctly parsed into `failing_nodes`
- [ ] On convergence: final graph saved, `converged` event emitted
- [ ] Frontend shows live iteration progress
- [ ] User can accept any intermediate version
- [ ] Cancellation stops the loop cleanly
- [ ] Test: `test_agentic_loop_converges_in_2_iterations` (mock AI + mock runner)
- [ ] Test: `test_agentic_loop_stops_at_max_iterations`
- [ ] Test: `test_agentic_loop_cancel_stops_cleanly`

---

### Slice 4E: Community Node Registry (1.5 weeks)

**What this is:** A curated, searchable registry of `@node` packages that users can install into their Noodle instance with one click. Like npm, but for Noodle nodes.

#### Architecture

Two components:
1. **Registry server** (separate, simple FastAPI app OR GitHub-backed JSON index) — stores node package metadata
2. **Noodle client** — can browse and install packages from registry

For MVP: GitHub-backed registry (a JSON file at a known URL, updated via PR). No separate server needed.

```
Registry (github.com/noodle-registry/packages/index.json):
{
  "packages": [
    {
      "id": "noodle-stripe-nodes",
      "name": "Stripe Nodes",
      "description": "Nodes for Stripe payment operations",
      "author": "community",
      "version": "1.2.0",
      "nodes": ["stripe_charge", "stripe_refund", "stripe_webhook"],
      "install_url": "https://github.com/author/noodle-stripe-nodes",
      "pypi_package": "noodle-stripe-nodes",
    }
  ]
}
```

#### Backend

New routes in `apps/api/app/routers/node_registry.py`:
```
GET  /node-registry/search?q=...&category=...  — proxy to registry index + local filter
GET  /node-registry/packages/{id}              — single package details
POST /node-registry/install                    — enqueue package install (async — returns 202)
  Body: {package_id: str, environment_id: str | null}
  → Returns immediately with {install_id: str, status: "pending"}
GET  /node-registry/installs/{install_id}      — poll install status: pending|installing|ready|failed
```

**Install flow is async** — venv rebuilds can take 30s–2 min for packages with heavy dependencies:
1. Validate package is in registry index (reject unknown packages)
2. Add `pypi_package` to `environment.packages` array immediately
3. Return 202 with `install_id`
4. Background task: call `ensure_environment_ready(environment_id)` 
5. When venv ready: mark install as `ready`; frontend polls and refreshes palette
6. On failure: mark as `failed` with error message; remove package from env list (rollback)

**Entry point convention** (documented for community developers):
```toml
# In community package pyproject.toml:
[project.entry-points."noodle.nodes"]
stripe = "noodle_stripe_nodes:register"
```

```python
# noodle_stripe_nodes/__init__.py
def register():
    # @node decorators auto-register on import — importing the module is sufficient.
    # Do NOT also import individual symbols (stripe_charge, stripe_refund etc.):
    # that would create duplicate imports and potentially double-register nodes.
    import noodle_stripe_nodes.nodes  # noqa: F401
```

#### Security

**Important:** Installed PyPI packages are NOT covered by the AST sandbox. The AST sandbox only applies to code strings authored by users directly (code nodes, AI-generated node functions). A `noodle-*` package from PyPI runs compiled Python with full interpreter access — it can do anything the process can do.

The security model for 4E is **registry governance, not sandbox containment:**
- The GitHub-backed registry index is the security perimeter. Packages must be submitted via PR and reviewed by a Noodle maintainer before appearing in the index.
- Review checklist: no network calls to unexpected hosts, no file system writes outside designated paths, no `__import__` tricks, dependencies vendored or pinned.
- Package source is published on GitHub — users can inspect what they install.
- `NOODLE_ALLOW_REGISTRY=false` (default: `true`) disables registry for environments that need maximum security.
- `node_registry:install` is an admin-only permission. Assign it ONLY to built-in admin/owner roles — it must NOT appear in the `PERMISSION_REGISTRY` list available for custom roles.
- Long-term (MS4+): packages can be signed with a Noodle-managed key and signature verified on install.

#### Frontend

New page `apps/web/src/settings/NodeRegistryPage.tsx`:
- Search bar (filters registry index)
- Package cards: name, description, node list, author, version
- "Install" button → opens environment selector → installs
- "Installed" badge for already-installed packages
- Tab: "Installed" / "Browse"

**Install job tracking:** `POST /node-registry/install` returns `{install_id: str}`. The `install_id` is a Redis key: `noodle:registry:install:{id}` → `{"status": "pending"|"installing"|"ready"|"failed", "error": "...", "environment_id": "..."}` with 1-hour TTL. No new DB table needed.

**Files to create:**
- `apps/api/app/routers/node_registry.py`
- `apps/web/src/settings/NodeRegistryPage.tsx`
- `apps/api/tests/test_node_registry.py`
- GitHub repo: `noodle-registry/packages` (separate repo, index.json)
- `docs/community-nodes.md` — developer guide for publishing nodes

**Files to modify:**
- `apps/api/app/security.py` — add `node_registry:install` to `_BUILTIN_ROLE_PERMISSIONS["owner"]` and `["admin"]` ONLY (not to `CUSTOM_ROLE_PERMISSION_REGISTRY`)
- `apps/web/src/App.tsx` — add registry route

**Acceptance criteria:**
- [ ] Search returns matching packages from registry index
- [ ] Install flow: package added to env → venv rebuilt → new nodes in palette
- [ ] Installed packages show "Installed" badge
- [ ] `node_registry:install` permission required to install
- [ ] `NOODLE_ALLOW_REGISTRY=false` disables registry features
- [ ] Community dev guide published (how to create and submit a package)
- [ ] Test: `test_registry_search_returns_packages` (mock registry URL)
- [ ] Test: `test_registry_install_adds_to_env_packages`

---

### MS4 Gate

- [ ] OIDC SSO: login with Google/Okta/Azure AD works end-to-end
- [ ] SAML SSO: SP metadata valid; SAMLResponse accepted
- [ ] Custom role with specific permissions: user gets exactly those permissions
- [ ] Audit log records key actions; exportable as CSV
- [ ] Vault/AWS KMS: credentials encrypted/decrypted via external KMS
- [ ] Agentic build loop: 3-iteration test converges and saves valid workflow
- [ ] Node registry: install a community package → nodes appear in palette
- [ ] All `Feature.*` gates work correctly (402 without feature)
- [ ] All CI lanes green

---

## Sequencing and Dependencies

```
Week 1:   3A (onboarding, 3d)  +  3B (visual diff, 3d)   → quick wins, parallel
Week 2-3: 3C (MCP client)                                   → blocks nothing but is large
Week 4:   3D (typed code node I/O)                         → prerequisite for 3E typing
Week 5-6: 3E (AI-generated custom nodes)                   → uses 3D typing

MS3 complete: Week 6

Week 7-9: 4A (SAML/OIDC SSO)              → enterprise gate
Week 10: 4B (Advanced RBAC + Audit Logs)  → compliance layer
Week 11: 4C (External KMS)               → start after 4B is feature-complete (sequential)
Week 12-13: 4D (Agentic Build Loop)      → flagship AI feature, independent
Week 14: 4E (Community Node Registry)    → ecosystem, independent

MS4 complete: Week 14
```

---

## What We're NOT Building in MS3/MS4

Explicitly out of scope:
1. **Noodle Cloud** — Managed hosting. Requires infrastructure investment beyond the scope of this spec. Design separately when beta metrics justify it.
2. **Multi-region execution dispatching** — Single-region is sufficient through 1000+ concurrent workflows.
3. **Remote debugging (pdb over WebSocket)** — P3 technical complexity, low adoption leverage.
4. **Jupyter notebook export/import** — Useful but not differentiating. P2 for MS4+ if demand exists from beta.
5. **Async code node** — Valuable technically but minimal user-visible impact vs effort.
6. **400+ integration nodes** — Do not build. Top 30 covered; MCP + AI code = unlimited integrations.

---

## Testing Strategy

Every slice follows this pattern:

| Layer | What to Test | Tool |
|-------|-------------|------|
| Unit | New functions in isolation | pytest + mocks |
| Integration | End-to-end route with real Postgres | pytest + AsyncClient + PG_URL |
| Frontend | Component rendering + interactions | vitest + @testing-library |
| E2E | Full browser smoke for each gate item | Playwright (manual CI step) |

**SQLite is NOT suitable for integration tests in MS3/MS4.** Slices 3C, 4A, 4B, 4C all use Postgres-specific features (`JSONB`, `gen_ulid()`, `ON DELETE SET NULL` with partial indexes, Row Level Security policies). SQLite silently drops or ignores these. All integration tests must run against a real Postgres instance (use the `PG_URL` env var already wired in CI).

For purely algorithmic unit tests (no DB) — `_sanitize_schema()`, `_parse_and_validate_node_code()`, `_extract_failures()`, `applyWithFilters()` — SQLite is fine since they don't touch the DB at all.

**Regression suite** — run after every slice before merging:
```bash
uv run pytest packages/core -x -q
uv run pytest apps/api -x -q -m "not postgres"
cd apps/web && npm test -- --run && npm run typecheck
```

**Migration testing** — required for every slice that adds an Alembic migration (3C, 4A, 4B, 4C):
```bash
# Apply all migrations on a blank DB — catches missing columns, constraint errors, etc.
uv run alembic -c apps/api/alembic.ini upgrade head
# Confirm downgrade works (prevents one-way migration traps)
uv run alembic -c apps/api/alembic.ini downgrade -1
uv run alembic -c apps/api/alembic.ini upgrade head
```
This runs in CI on a real Postgres instance, not SQLite. Add as a required CI step alongside the existing test matrix.
