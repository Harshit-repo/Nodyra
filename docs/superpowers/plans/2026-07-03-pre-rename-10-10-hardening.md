# Pre-Rename 10/10 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every gap that keeps Artifacts (8), MCP server (8.5), MCP client (7.5), Engine (9), Queue/durable execution (9), Workers (9), Sandbox (8), Security (8.5), Deployment (8), and Architecture (9) below 10/10 — executed **before** the Nodyra rename so the Phase 2 sweep renames finished code.

**Architecture:** Every task is a vertical slice on an existing seam: the `Artifact` metadata table + pluggable backends, the `RuntimeContext` platform hooks, the engine's kwargs-threaded node executor, the durable `RunQueueEntry` queue, the fail-closed `sandbox_policy` layer, and the React Query + zustand web app. No new services, no new frameworks, no schema rewrites — three additive alembic migrations total.

**Tech Stack:** FastAPI / SQLAlchemy async / alembic, pytest (+pytest-asyncio auto mode), React 18 + React Query + vitest, docker compose, GitHub Actions.

**Source audit:** `FABLE5_NODYRA_10_10_FULL_SYSTEM_AUDIT_AND_UPGRADE_PLAN.md` (repo root).
**Sequencing:** This entire plan lands **before** `docs/superpowers/plans/2026-07-02-phase2-nodyra-rename.md`. The rename's mechanical sweep will convert every name this plan introduces.

## Global Constraints

- **Pre-rename naming:** all identifiers, docs, env vars, and UI copy use the current names (`noodle`, `Noodle`, `NOODLE_*`). Do NOT write "Nodyra" anywhere — the Phase 2 sweep converts mechanically.
- Backend gates per task: `uv run pytest <touched test paths> -q` green, then `uv run ruff check <touched files>` clean. Run commands from the repo root `D:\noodle` (or its POSIX mount) unless a step says otherwise.
- Web gates per task (web tasks only): `npm run typecheck && npm test -- --run` green from `apps/web`. No new npm dependencies. No new Python dependencies.
- Migrations: this plan owns revisions `0080`, `0081`, `0082` (linear chain from `0079_environment_build_jobs`). Never edit an existing revision.
- Follow existing patterns: React Query hooks live in `apps/web/src/queries/index.ts`, HTTP methods on the `export const api = {` object in `apps/web/src/api.ts`, permissions via `require_permission(...)` from `app.security`.
- Never `git add -A` — the tree holds unrelated in-flight work (`LoginPage.tsx`, `WorkflowsPage.tsx`, `brand/`). Stage exactly the files each task names.
- Every commit message ends with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- If a step's anchor text is not found verbatim (file drifted), STOP and report — do not improvise a different location.

---

# Area A — Artifacts (8 → 10)

### Task 1: SHA-256 checksums on artifacts

**Files:**
- Modify: `packages/core/noodle/artifacts.py` (in `LocalArtifactStore.write_bytes`, lines ~109–155)
- Modify: `apps/api/app/models.py` (class `Artifact`, ~line 928)
- Modify: `apps/api/app/services/artifacts.py` (`_row_from_ref`)
- Modify: `apps/api/app/schemas.py` (`ArtifactInfo`, ~line 455), `apps/api/app/routers/artifacts.py` (`_info`)
- Create: `apps/api/alembic/versions/0080_artifact_checksum.py`
- Test: `packages/core/tests/test_artifact_checksum.py` (new), extend `apps/api/tests/` only via existing suites passing

**Interfaces:**
- Produces: artifact refs and `Artifact` rows carry `checksum_sha256: str | None` (64-char lowercase hex). Task 2/3 display it; nothing else depends on it.

- [ ] **Step 1: Write the failing test** — create `packages/core/tests/test_artifact_checksum.py`:

```python
"""write_bytes stamps a sha256 checksum into the artifact ref."""

import hashlib

from noodle.artifacts import LocalArtifactStore
from noodle.context import current_node_id


def test_write_bytes_stamps_checksum(tmp_path):
    token = current_node_id.set("node-1")
    try:
        store = LocalArtifactStore(tmp_path, run_id="run-1")
        payload = b"hello artifact"
        ref = store.write_bytes(payload, name="a.txt", content_type="text/plain")
    finally:
        current_node_id.reset(token)
    assert ref["checksum_sha256"] == hashlib.sha256(payload).hexdigest()
```

- [ ] **Step 2: Verify it fails:** `uv run pytest packages/core/tests/test_artifact_checksum.py -q` → FAIL (`KeyError: 'checksum_sha256'`).

- [ ] **Step 3: Implement in `packages/core/noodle/artifacts.py`.** At the top of the file add `import hashlib` to the existing import block. In `write_bytes`, immediately after `payload = bytes(data)` add nothing; then find this exact block:

```python
        ref: dict[str, Any] = {
            ARTIFACT_MARKER: True,
            "version": ARTIFACT_VERSION,
            "artifact_id": artifact_id,
```

and add one key to the dict (after the `"size_bytes"` line):

```python
            "checksum_sha256": hashlib.sha256(payload).hexdigest(),
```

- [ ] **Step 4:** `uv run pytest packages/core/tests/test_artifact_checksum.py -q` → PASS.

- [ ] **Step 5: Model column.** In `apps/api/app/models.py`, class `Artifact`, directly after the `size_bytes` mapped column add:

```python
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
```

- [ ] **Step 6: Migration.** Create `apps/api/alembic/versions/0080_artifact_checksum.py`:

```python
"""Add artifacts.checksum_sha256.

Revision ID: 0080_artifact_checksum
Revises: 0079_environment_build_jobs
"""

import sqlalchemy as sa
from alembic import op

revision = "0080_artifact_checksum"
down_revision = "0079_environment_build_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "artifacts", sa.Column("checksum_sha256", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("artifacts", "checksum_sha256")
```

- [ ] **Step 7: Persist it.** In `apps/api/app/services/artifacts.py`, function `_row_from_ref`, add after the `size_bytes=...` argument line:

```python
        checksum_sha256=(str(ref["checksum_sha256"]) if ref.get("checksum_sha256") else None),
```

- [ ] **Step 8: Expose it.** In `apps/api/app/schemas.py` class `ArtifactInfo` add `checksum_sha256: str | None = None` after `size_bytes`. In `apps/api/app/routers/artifacts.py` function `_info`, add `checksum_sha256=row.checksum_sha256,` mirroring how the other fields are passed.

- [ ] **Step 9: Full verification:**

```bash
uv run pytest packages/core/tests/test_artifact_checksum.py apps/api/tests -q -k "artifact" && uv run ruff check packages/core/noodle/artifacts.py apps/api/app/models.py apps/api/app/services/artifacts.py apps/api/app/routers/artifacts.py apps/api/app/schemas.py apps/api/alembic/versions/0080_artifact_checksum.py
```

Expected: all green. Also confirm single head: `cd apps/api && uv run alembic heads` → `0080_artifact_checksum (head)`.

- [ ] **Step 10: Commit:** `git add` the seven files above; message `feat(artifacts): stamp and persist sha256 checksums`.

---

### Task 2: Workspace-level artifact list API

**Files:**
- Modify: `apps/api/app/routers/artifacts.py`, `apps/api/app/schemas.py`
- Test: `apps/api/tests/test_artifact_browser.py` (new)

**Interfaces:**
- Produces: `GET /artifacts?workflow_id=&run_id=&kind=&q=&limit=&offset=` → `ArtifactListResponse{items: list[ArtifactInfo], total: int}`. Task 3 (UI) consumes this exact shape.

- [ ] **Step 1: Failing test** — `apps/api/tests/test_artifact_browser.py`. Read `apps/api/tests/test_artifacts.py` first and copy its fixture usage (client + seeded run/artifact) exactly; then add:

```python
async def test_list_artifacts_workspace(client, seeded_artifact):
    resp = await client.get("/artifacts", params={"limit": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert any(a["id"] == seeded_artifact.id for a in body["items"])


async def test_list_artifacts_filters_by_kind(client, seeded_artifact):
    resp = await client.get("/artifacts", params={"kind": "definitely-not-a-kind"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
```

If `test_artifacts.py` has no reusable seeded-artifact fixture, create one in the new file: insert an `Artifact` row directly via the test session (mirror how `test_artifacts.py` seeds rows) with `name="report.csv"`, `kind="table"`.

- [ ] **Step 2:** `uv run pytest apps/api/tests/test_artifact_browser.py -q` → FAIL (405 or 404 — no such route).

- [ ] **Step 3: Schema.** In `apps/api/app/schemas.py`, after `ArtifactInfo` add:

```python
class ArtifactListResponse(BaseModel):
    items: list[ArtifactInfo]
    total: int
```

- [ ] **Step 4: Route.** In `apps/api/app/routers/artifacts.py` add (imports: `func`, `Run` model, `Query` from fastapi, `ArtifactListResponse`):

```python
@router.get("", response_model=ArtifactListResponse)
async def list_artifacts(
    workflow_id: str | None = None,
    run_id: str | None = None,
    kind: str | None = None,
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> ArtifactListResponse:
    """Workspace-wide artifact browser. Org scoping comes from the tenancy
    filter on the ORM select; previews are already redacted at write time."""
    stmt = select(Artifact)
    count_stmt = select(func.count()).select_from(Artifact)
    if workflow_id:
        stmt = stmt.join(Run, Artifact.run_id == Run.id).where(Run.workflow_id == workflow_id)
        count_stmt = count_stmt.join(Run, Artifact.run_id == Run.id).where(
            Run.workflow_id == workflow_id
        )
    if run_id:
        stmt = stmt.where(Artifact.run_id == run_id)
        count_stmt = count_stmt.where(Artifact.run_id == run_id)
    if kind:
        stmt = stmt.where(Artifact.kind == kind)
        count_stmt = count_stmt.where(Artifact.kind == kind)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(Artifact.name.ilike(pattern))
        count_stmt = count_stmt.where(Artifact.name.ilike(pattern))
    total = int(await session.scalar(count_stmt) or 0)
    rows = (
        await session.scalars(
            stmt.order_by(Artifact.created_at.desc()).limit(limit).offset(offset)
        )
    ).all()
    return ArtifactListResponse(items=[_info(r) for r in rows], total=total)
```

Register it ABOVE the `GET /{artifact_id}` route in the file if route ordering matters (FastAPI matches `""` before path params only when declared first — place this function directly after `list_run_artifacts`).

- [ ] **Step 5:** `uv run pytest apps/api/tests/test_artifact_browser.py apps/api/tests/test_artifacts.py -q` → PASS. `uv run ruff check apps/api/app/routers/artifacts.py apps/api/app/schemas.py`.

- [ ] **Step 6: Commit:** `feat(artifacts): workspace artifact list endpoint with filters + pagination`.

---

### Task 3: Artifact browser UI page

**Files:**
- Create: `apps/web/src/ArtifactsPage.tsx`
- Modify: `apps/web/src/api.ts`, `apps/web/src/App.tsx`, `apps/web/src/shell/` nav (see step 4), `apps/web/src/types.ts`
- Test: `apps/web/src/ArtifactsPage.test.tsx` (new)

**Interfaces:**
- Consumes: `GET /artifacts` from Task 2 (`{items, total}`), existing `GET /artifacts/{id}/download`.

- [ ] **Step 1: types + api.** In `apps/web/src/types.ts` find the existing `ArtifactInfo` type (grep `ArtifactInfo`); add `checksum_sha256?: string | null;`. In `apps/web/src/api.ts`, inside the `export const api = {` object, add (mirror the adjacent methods' fetch helper — read two neighbors first and use the same request function):

```ts
  listArtifacts(params: {
    workflow_id?: string; run_id?: string; kind?: string; q?: string;
    limit?: number; offset?: number;
  }): Promise<{ items: ArtifactInfo[]; total: number }> {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "")
        .map(([k, v]) => [k, String(v)]),
    );
    return request(`/artifacts?${qs}`);
  },
```

(`request` = whatever helper the neighboring methods use — match exactly.)

- [ ] **Step 2: Page component.** Create `apps/web/src/ArtifactsPage.tsx` — follow `ExecutionsPage.tsx` for layout classes/EmptyState usage. Content: search input (name `q`, debounced 300 ms), kind `<select>` (options: all/table/binary/image/report/dataset), table with Name / Kind / Size / Run / Created / Download button hitting `/artifacts/{id}/download` (use the same download-link pattern as the existing artifact download in `apps/web/src/editor/DataPanel.tsx` — grep `download` there and reuse), offset-based Prev/Next pager over `total`. Use `useQuery({ queryKey: ["artifacts", filters], queryFn: () => api.listArtifacts(filters) })`.

- [ ] **Step 3: Test.** `apps/web/src/ArtifactsPage.test.tsx` — mirror the mocking style of `ExecutionsPage`'s test if one exists, else `WorkflowsPage.test.tsx`: mock `api.listArtifacts` to resolve `{items:[{id:"a1",name:"report.csv",kind:"table",content_type:"text/csv",size_bytes:12,metadata:{},preview:null,created_at:new Date().toISOString()}], total:1}`; render; assert `report.csv` appears; assert empty state text renders when `{items:[],total:0}`.

- [ ] **Step 4: Route + nav.** In `apps/web/src/App.tsx` add a lazy import mirroring the neighbors and a route inside the `<Route element={<HomeLayout />}>` block, after `/executions`:

```tsx
<Route path="/artifacts" element={<PageErrorBoundary><ArtifactsPage /></PageErrorBoundary>} />
```

Find the sidebar/nav that lists "Executions" (`grep -rn "Executions" apps/web/src/shell apps/web/src/HomeLayout.tsx`) and add an "Artifacts" entry directly after it, copying the exact markup of the Executions entry.

- [ ] **Step 5: Verify:** `cd apps/web && npm run typecheck && npm test -- --run` → green.

- [ ] **Step 6: Commit:** `feat(web): workspace artifact browser page`.

---

### Task 4: Cache LocalBackend.stats()

**Files:**
- Modify: `apps/api/app/services/artifact_backends.py` (class `LocalBackend.stats`, ~line 178)
- Test: `apps/api/tests/test_artifact_backends_stats.py` (new)

- [ ] **Step 1: Failing test:**

```python
"""LocalBackend.stats() is cached — repeated calls don't re-walk the tree."""

from app.services import artifact_backends
from app.services.artifact_backends import LocalBackend


def test_stats_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact_backends.settings, "artifacts_dir", str(tmp_path))
    artifact_backends._stats_cache = None
    backend = LocalBackend()
    (tmp_path / "f.bin").write_bytes(b"12345")
    first = backend.stats()
    assert first["bytes"] == 5
    (tmp_path / "g.bin").write_bytes(b"12345")
    assert backend.stats()["bytes"] == 5  # cached — file not visible yet
    artifact_backends._stats_cache = None
    assert backend.stats()["bytes"] == 10  # cache cleared — fresh walk
```

- [ ] **Step 2:** run it → FAIL (`AttributeError: _stats_cache` or second assert fails at 10).

- [ ] **Step 3: Implement.** In `artifact_backends.py` add near the `_BACKENDS` registry: `import time` (top of file) and

```python
# stats() walks the whole artifacts tree; /ops callers poll it. 60 s TTL cache.
_STATS_TTL_SECONDS = 60.0
_stats_cache: tuple[float, dict] | None = None
```

Wrap the existing `LocalBackend.stats` body:

```python
    def stats(self) -> dict[str, Any]:
        global _stats_cache
        now = time.monotonic()
        if _stats_cache is not None and now - _stats_cache[0] < _STATS_TTL_SECONDS:
            return dict(_stats_cache[1])
        result = self._stats_uncached()
        _stats_cache = (now, dict(result))
        return result

    def _stats_uncached(self) -> dict[str, Any]:
        # (the previous stats() body moves here unchanged)
```

- [ ] **Step 4:** test PASS; `uv run pytest apps/api/tests -q -k artifact` still green; ruff clean. **Commit:** `perf(artifacts): 60s TTL cache on LocalBackend.stats()`.

---

# Area B — MCP (server 8.5, client 7.5 → 10)

### Task 5: MCP quickstart doc + Settings surfacing

**Files:**
- Create: `docs/mcp-quickstart.md`
- Modify: `README.md`, `apps/web/src/SettingsPage.tsx`
- Test: extend `apps/web/src/SettingsPage.test.tsx`

- [ ] **Step 1: Write `docs/mcp-quickstart.md`** (complete file; Noodle names — the rename sweep converts):

````markdown
# Build Noodle workflows with Claude (or any MCP agent)

Noodle ships a full MCP server: 61 tools covering incremental graph editing
(add/patch/remove nodes and edges with optimistic concurrency), validation,
publishing, runs, schedules, environments, and run approvals. Point an
MCP-capable agent at your instance and it can build, test, and deploy
workflows you can watch live on the canvas.

## 1. Create an API token

Settings → API tokens → New token. Scopes: `workflow:read`, `workflow:write`,
`workflow:run` (add `deployment:manage` if the agent should publish). Copy
the `ndpat_...` value.

## 2. Connect your agent

**Claude Code:**

```bash
claude mcp add noodle --transport http http://localhost:8000/mcp \
  --header "Authorization: Bearer ndpat_YOUR_TOKEN"
```

**Claude Desktop / Cursor / any streamable-HTTP client** — add to the MCP
config:

```json
{
  "mcpServers": {
    "noodle": {
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "headers": { "Authorization": "Bearer ndpat_YOUR_TOKEN" }
    }
  }
}
```

## 3. Try it

Ask the agent: *"List my Noodle workflows"*, then *"Create a workflow that
fetches https://api.github.com/repos/astral-sh/uv, extracts the star count
with a Code node, and runs it"*. Useful tool names: `list_workflows`,
`create_workflow`, `add_node`, `add_edge`, `validate_workflow_graph`,
`run_workflow`, `get_run`.

## 4. Safety model

- Tokens are org-scoped; tools honour the token's scopes.
- Destructive tools (`delete_workflow`, rollbacks) require an explicit
  `approve: true` argument.
- Graph edits accept `expected_graph_revision` for optimistic concurrency —
  agents editing alongside humans get a conflict error instead of clobbering.

## Expose a workflow AS an MCP tool

Any published workflow can itself become an MCP tool for agents:
`enable_mcp_tool` with a JSON-schema for its parameters. External MCP
servers can likewise become nodes inside workflows: Settings →
MCP Connections.
````

- [ ] **Step 2: README pointer.** In `README.md`, after the "Why Noodle" section heading block, insert:

```markdown
## Build workflows with an AI agent (MCP)

Noodle is an MCP server. Claude, Cursor, or any MCP client can create, edit,
validate, run, and publish workflows through 61 tools — see
[docs/mcp-quickstart.md](docs/mcp-quickstart.md) for a one-paste setup.
```

- [ ] **Step 3: Settings card.** In `apps/web/src/SettingsPage.tsx`, locate the API-tokens section (`grep -n "API token" apps/web/src/SettingsPage.tsx`). Directly after that section's closing element add a card using the page's existing section markup (copy the section wrapper of API tokens):

```tsx
<section className="settings-section">
  <h2>MCP server</h2>
  <p className="muted">
    Connect Claude or any MCP agent to build workflows in this workspace.
    Endpoint: <code>{window.location.origin}/mcp</code> — authenticate with an
    API token from the section above.
  </p>
  <p className="muted">
    Full instructions: <code>docs/mcp-quickstart.md</code> in your install.
  </p>
</section>
```

(Adjust wrapper class names to exactly match the neighboring sections — read them first.)

- [ ] **Step 4: Test.** In `SettingsPage.test.tsx` add one assertion to an existing render test: `expect(screen.getByText(/MCP server/)).toBeInTheDocument();`

- [ ] **Step 5:** `cd apps/web && npm run typecheck && npm test -- --run` green. **Commit:** `docs+web: MCP quickstart and Settings surfacing`.

---

### Task 6: MCP tool-call trace in node debug

**Files:**
- Modify: `packages/nodes/noodle_nodes/mcp_tool.py`
- Test: extend `packages/nodes/tests/test_mcp_tool_node.py`

**Interfaces:**
- Produces: `node_finished.debug.mcp_calls: list[{connection_id, tool, duration_ms, status, error?}]` — persisted automatically with NodeRun debug and rendered by the existing debug viewers. No other task depends on it.

- [ ] **Step 1: Failing test** — append to `packages/nodes/tests/test_mcp_tool_node.py`:

```python
from noodle.context import node_debug


async def test_mcp_tool_records_call_trace(recorded_calls):
    debug: dict = {}
    token = node_debug.set(debug)
    try:
        ctx = RuntimeContext(
            run_id="r1", workflow_id="w1",
            node_params={"connection_id": "conn-1", "tool_name": "echo",
                         "arguments": {"a": 1}},
            node_inputs={},
        )
        await mcp_tool(input=None, ctx=ctx)
    finally:
        node_debug.reset(token)
    (call,) = debug["mcp_calls"]
    assert call["tool"] == "echo"
    assert call["connection_id"] == "conn-1"
    assert call["status"] == "success"
    assert isinstance(call["duration_ms"], int)


async def test_mcp_tool_records_failed_call(recorded_calls):
    async def boom(connection_id, tool_name, arguments):
        raise RuntimeError("server unreachable")

    set_call_mcp_tool_impl(boom)
    debug: dict = {}
    token = node_debug.set(debug)
    try:
        ctx = RuntimeContext(
            run_id="r1", workflow_id="w1",
            node_params={"connection_id": "conn-1", "tool_name": "echo"},
            node_inputs={},
        )
        with pytest.raises(RuntimeError):
            await mcp_tool(input=None, ctx=ctx)
    finally:
        node_debug.reset(token)
    assert debug["mcp_calls"][0]["status"] == "error"
```

Add `import pytest` and `set_call_mcp_tool_impl` to the test file's imports if missing.

- [ ] **Step 2:** run → FAIL (`KeyError: 'mcp_calls'`).

- [ ] **Step 3: Implement.** Replace the tail of `mcp_tool()` (the `return await ctx.call_mcp_tool(...)` line) with:

```python
    import time

    from noodle.context import node_debug

    debug = node_debug.get() or {}
    trace: dict[str, Any] = {"connection_id": conn_id, "tool": tool_name}
    started = time.monotonic()
    try:
        result = await ctx.call_mcp_tool(conn_id, tool_name, resolved_args)
    except Exception as exc:
        trace["status"] = "error"
        trace["error"] = f"{type(exc).__name__}: {exc}"
        raise
    else:
        trace["status"] = "success"
        return result
    finally:
        trace["duration_ms"] = int((time.monotonic() - started) * 1000)
        if isinstance(debug, dict):
            debug.setdefault("mcp_calls", []).append(trace)
```

Move the `import time` / `node_debug` imports to the module top (ruff will demand it).

- [ ] **Step 4:** `uv run pytest packages/nodes/tests/test_mcp_tool_node.py -q` → 5+ PASS; ruff clean. **Commit:** `feat(mcp): per-call trace recorded in mcp_tool node debug`.

---

### Task 7: Per-connection enable flag + tool allowlist

**Files:**
- Modify: `apps/api/app/models.py` (class `MCPConnection`, ~line 1364), `apps/api/app/services/mcp_client.py`, `apps/api/app/routers/mcp_connections.py`
- Create: `apps/api/alembic/versions/0081_mcp_connection_policy.py`
- Test: `apps/api/tests/test_mcp_connection_policy.py` (new)

**Interfaces:**
- Produces: `MCPConnection.enabled: bool` (default True), `MCPConnection.allowed_tools: list[str] | None` (None = all tools). `mcp_client.ensure_tool_allowed(conn, tool_name)` raises `ValueError` on violation. Task 9's runner hook change consumes `ensure_tool_allowed`.

- [ ] **Step 1: Failing test** — `apps/api/tests/test_mcp_connection_policy.py` (copy DB-session fixture style from `apps/api/tests/test_mcp_connections.py`):

```python
import pytest

from app.models import MCPConnection
from app.services.mcp_client import ensure_tool_allowed


def _conn(**kw) -> MCPConnection:
    return MCPConnection(
        org_id="default", name="t", url="http://mcp.example/mcp", **kw
    )


def test_disabled_connection_rejected():
    with pytest.raises(ValueError, match="disabled"):
        ensure_tool_allowed(_conn(enabled=False), "echo")


def test_allowlist_blocks_unlisted_tool():
    conn = _conn(allowed_tools=["echo", "search"])
    ensure_tool_allowed(conn, "echo")  # no raise
    with pytest.raises(ValueError, match="not in this connection's allowed tools"):
        ensure_tool_allowed(conn, "delete_everything")


def test_null_allowlist_allows_all():
    ensure_tool_allowed(_conn(allowed_tools=None), "anything")
```

- [ ] **Step 2:** run → FAIL (import error).

- [ ] **Step 3: Model.** In `models.py`, class `MCPConnection`, after the `headers` column add:

```python
    # Call-time policy: a disabled connection rejects every call; a non-null
    # allowed_tools list restricts calls to exactly those tool names.
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_text("1")
    )
    allowed_tools: Mapped[list | None] = mapped_column(JSON, nullable=True)
```

(Check the file's existing imports: it already imports `Boolean`/`JSON` — grep; use the same server_default idiom as neighboring boolean columns; if neighbors use `server_default="1"` strings, mirror that instead of `sa_text`.)

- [ ] **Step 4: Migration** — `apps/api/alembic/versions/0081_mcp_connection_policy.py`:

```python
"""MCP connection call policy: enabled flag + tool allowlist.

Revision ID: 0081_mcp_connection_policy
Revises: 0080_artifact_checksum
"""

import sqlalchemy as sa
from alembic import op

revision = "0081_mcp_connection_policy"
down_revision = "0080_artifact_checksum"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "mcp_connections",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
    )
    op.add_column("mcp_connections", sa.Column("allowed_tools", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("mcp_connections", "allowed_tools")
    op.drop_column("mcp_connections", "enabled")
```

- [ ] **Step 5: Enforcement helper.** In `apps/api/app/services/mcp_client.py` add:

```python
def ensure_tool_allowed(conn: MCPConnection, tool_name: str) -> None:
    """Raise ValueError when this connection's call policy forbids the tool.

    Enforced at every execution path (runner platform hook); the UI uses the
    same fields to grey out tools. None allowlist = all tools allowed.
    """
    if getattr(conn, "enabled", True) is False:
        raise ValueError(f"MCP connection {conn.name!r} is disabled")
    allowed = getattr(conn, "allowed_tools", None)
    if allowed is not None and tool_name not in allowed:
        raise ValueError(
            f"MCP tool {tool_name!r} is not in this connection's allowed tools"
        )
```

And enforce in the runner: in `apps/api/app/services/runner.py`, inside `_mcp_call_impl` (grep `_load_conn_with_secret`), between loading the connection and calling `_call_tool`, insert:

```python
                        from app.services.mcp_client import ensure_tool_allowed

                        ensure_tool_allowed(conn, tool_name)
```

- [ ] **Step 6: Router.** In `apps/api/app/routers/mcp_connections.py`: add `enabled: bool = True` and `allowed_tools: list[str] | None = None` to the create/update payload models (read the file — it uses Pydantic request models or dict payloads; mirror exactly), assign them in `create_mcp_connection`/`update_mcp_connection`, and add both keys to `_row_to_dict`.

- [ ] **Step 7:** `uv run pytest apps/api/tests/test_mcp_connection_policy.py apps/api/tests/test_mcp_connections.py -q` → PASS; `cd apps/api && uv run alembic heads` → `0081_mcp_connection_policy (head)`; ruff clean. **Commit:** `feat(mcp): per-connection enable flag and tool allowlist, enforced at call time`.

---

### Task 8: MCP client response caps + configurable timeout

**Files:**
- Modify: `apps/api/app/config.py`, `apps/api/app/services/mcp_client.py`, `apps/api/app/services/runner.py`
- Test: `apps/api/tests/test_mcp_client_limits.py` (new)

**Interfaces:**
- Produces: settings `mcp_tool_timeout_seconds: float = 30.0`, `mcp_max_response_bytes: int = 5 * 1024 * 1024`; `call_tool` raises `MCPError` on oversized responses.

- [ ] **Step 1: Failing test** — `apps/api/tests/test_mcp_client_limits.py` (use `pytest_httpx`'s `httpx_mock` fixture, already a dev dependency; mirror mocking style in `test_mcp_connections.py`):

```python
import pytest

from app.models import MCPConnection
from app.services.mcp_client import MCPError, call_tool


async def test_call_tool_rejects_oversized_response(httpx_mock, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "mcp_max_response_bytes", 64)
    httpx_mock.add_response(
        json={"jsonrpc": "2.0", "id": 1,
              "result": {"content": [{"type": "text", "text": "x" * 500}]}},
    )
    conn = MCPConnection(org_id="default", name="t", url="http://mcp.example/mcp")
    with pytest.raises(MCPError, match="response too large"):
        await call_tool(conn, "echo", {}, decrypted_secret=None)
```

Note: `resolve_pinned` performs DNS resolution — if it rejects the fake host in tests, monkeypatch `noodle_nodes.httpx_security.resolve_pinned` the same way `test_mcp_connections.py` does (read that file first; reuse its approach verbatim).

- [ ] **Step 2:** run → FAIL (no `mcp_max_response_bytes`).

- [ ] **Step 3: Config.** In `config.py`, after the `mcp_oauth_client_secret` field add:

```python
    # MCP client (external servers): per-call timeout and a hard cap on the
    # JSON-RPC response body so a hostile server can't balloon worker memory.
    mcp_tool_timeout_seconds: float = 30.0
    mcp_max_response_bytes: int = 5 * 1024 * 1024
```

- [ ] **Step 4: Enforce.** In `mcp_client.py` `call_tool`, change the signature default to `timeout_seconds: float | None = None` and at the top of the body add `timeout_seconds = timeout_seconds or settings.mcp_tool_timeout_seconds` (add `from app.config import settings` import). After `resp.raise_for_status()` and before `resp.json()` insert:

```python
        if len(resp.content) > settings.mcp_max_response_bytes:
            raise MCPError(
                f"MCP response too large ({len(resp.content)} bytes; "
                f"cap {settings.mcp_max_response_bytes})"
            )
```

Apply the same size check in `discover_tools` after its `raise_for_status()`.

- [ ] **Step 5:** test PASS; `uv run pytest apps/api/tests -q -k mcp` green; ruff clean. **Commit:** `feat(mcp): response size cap + configurable client timeout`.

---

### Task 9: MCP tool discovery panel in the editor

**Files:**
- Create: `apps/web/src/editor/McpToolsSection.tsx`
- Modify: `apps/web/src/editor/NodePalette.tsx`, `apps/web/src/api.ts`
- Test: `apps/web/src/editor/McpToolsSection.test.tsx` (new)

**Interfaces:**
- Consumes: `GET /mcp-connections` and `GET /mcp-connections/{id}/tools` (existing routes — verify shapes with `grep -n "list_mcp_tools" -A 20 apps/api/app/routers/mcp_connections.py` before writing the fetchers).
- Produces: `McpToolsSection({ onAddNode })` — calls `onAddNode(params: {connection_id, tool_name})`, which NodePalette wires to its existing add-node path with node type `"mcp_tool"`.

- [ ] **Step 1: api methods.** In `api.ts` add to the `api` object (matching neighbor style):

```ts
  listMcpConnections(): Promise<Array<{ id: string; name: string; enabled?: boolean }>> {
    return request(`/mcp-connections`);
  },
  listMcpConnectionTools(id: string): Promise<Array<{ name: string; description?: string }>> {
    return request(`/mcp-connections/${id}/tools`);
  },
```

Adjust return-shape unwrapping to what the router actually returns (read `_row_to_dict` / `list_mcp_tools` first — if tools come wrapped as `{tools: [...]}`, unwrap here so the component gets a flat array).

- [ ] **Step 2: Component.** Create `McpToolsSection.tsx`:

```tsx
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

export function McpToolsSection({
  onAddNode,
}: {
  onAddNode: (params: { connection_id: string; tool_name: string }) => void;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  const conns = useQuery({
    queryKey: ["mcp-connections"],
    queryFn: () => api.listMcpConnections(),
  });
  const tools = useQuery({
    queryKey: ["mcp-connection-tools", openId],
    queryFn: () => api.listMcpConnectionTools(openId!),
    enabled: openId !== null,
  });
  if (conns.isLoading || !conns.data?.length) return null;
  return (
    <div className="palette-section" data-testid="mcp-tools-section">
      <h3>MCP tools</h3>
      {conns.data.map((c) => (
        <div key={c.id}>
          <button
            type="button"
            className="palette-item"
            onClick={() => setOpenId(openId === c.id ? null : c.id)}
            disabled={c.enabled === false}
            title={c.enabled === false ? "Connection disabled" : undefined}
          >
            {c.name}
          </button>
          {openId === c.id &&
            (tools.data ?? []).map((t) => (
              <button
                key={t.name}
                type="button"
                className="palette-item palette-item-nested"
                title={t.description}
                onClick={() => onAddNode({ connection_id: c.id, tool_name: t.name })}
              >
                {t.name}
              </button>
            ))}
        </div>
      ))}
    </div>
  );
}
```

Match `palette-section` / `palette-item` class names to what NodePalette actually uses (read its render first; substitute its real class names).

- [ ] **Step 3: Integrate.** In `NodePalette.tsx`, find where category sections render (grep `category`) and where a palette click adds a node (grep for the add/drag handler — it will call something like `onAdd(type, params?)`). Render `<McpToolsSection onAddNode={(p) => /* the same add call with type "mcp_tool" and params {connection_id: p.connection_id, tool_name: p.tool_name} */} />` after the last built-in section. Use the exact existing add function; if it doesn't accept initial params, extend it with an optional `initialParams` argument defaulted `{}` and thread it to the node-creation site.

- [ ] **Step 4: Test.** `McpToolsSection.test.tsx`: mock both api methods; render with `onAddNode` spy; click a connection then a tool; assert spy called with `{connection_id, tool_name}`. Assert component renders `null` when connections list is empty.

- [ ] **Step 5:** `cd apps/web && npm run typecheck && npm test -- --run` green. **Commit:** `feat(web): MCP tool discovery panel — add external tools to canvas preconfigured`.

---

# Area C — Engine (9 → 10)

### Task 10: Run deadline caps every node's timeout

**Files:**
- Modify: `packages/core/noodle/context.py`, `packages/core/noodle/engine/node_exec.py` (~line 700), `packages/core/noodle/engine/scheduler.py` (`_execute_impl`)
- Test: `packages/core/tests/test_run_deadline.py` (new)

**Interfaces:**
- Produces: ContextVar `noodle.context.run_deadline: float | None` (monotonic timestamp). Set by `_execute_impl` for the run's duration; read by `_run_one_node` to cap each node's effective timeout at the remaining run budget. Mirrors the existing `cancel_event` ContextVar pattern.

- [ ] **Step 1: Failing test** — `packages/core/tests/test_run_deadline.py` (copy graph/registry fixture idioms from `packages/core/tests/test_engine_validation.py` / any engine execution test — read one first):

```python
"""A node started before the run deadline is timed out AT the deadline,
instead of running arbitrarily long past it."""

import time

import pytest

from noodle.engine import execute
from noodle.models import GraphNode, WorkflowGraph
from noodle.sdk import NodeRegistry, node


@pytest.fixture
def registry():
    reg = NodeRegistry()

    @node(id="sleepy", name="Sleepy", category="test", registry=reg)
    def sleepy(input=None):
        time.sleep(10)
        return {"main": {"ok": True}}

    return reg


async def test_run_deadline_bounds_running_node(registry):
    graph = WorkflowGraph(
        nodes=[GraphNode(id="n1", type="sleepy", params={})], edges=[]
    )
    start = time.monotonic()
    result = await execute(graph, registry, run_timeout_seconds=1.0)
    elapsed = time.monotonic() - start
    assert elapsed < 5.0  # not the node's full 10 s
    assert str(result.status) == "error"
    assert "timed out" in (result.nodes["n1"].error or "")
```

Adjust the `@node` registration call to the SDK's actual signature (read how other engine tests register test nodes — if `@node` doesn't take `registry=`, use the same registration mechanism they use).

- [ ] **Step 2:** run → FAIL (takes ~10 s or node succeeds past deadline).

- [ ] **Step 3: ContextVar.** In `packages/core/noodle/context.py`, alongside `cancel_event`, add:

```python
# Monotonic timestamp after which no node may keep running. Set by the
# engine's _execute_impl when run_timeout_seconds is given; read by
# _run_one_node to cap each node's effective timeout at the remaining budget.
run_deadline: ContextVar[float | None] = ContextVar("noodle_run_deadline", default=None)
```

- [ ] **Step 4: Set it.** In `scheduler.py` `_execute_impl`, the deadline is already computed (`run_deadline = time.monotonic() + run_timeout_seconds`, ~line 612). Import `run_deadline as run_deadline_var` from `noodle.context` and wrap the `_execute_nodes` call:

```python
    deadline_token = run_deadline_var.set(run_deadline)
    try:
        run_status = await _execute_nodes(
            ...existing kwargs unchanged...
        )
    finally:
        run_deadline_var.reset(deadline_token)
```

- [ ] **Step 5: Cap the node timeout.** In `node_exec.py` ~line 701, after:

```python
    timeout = _node_timeout(
        graph_node.type, graph_node.timeout_seconds, default_timeouts
    )
```

add:

```python
    # Run-level deadline: a node may never outlive the run's remaining budget.
    # min() with any per-node timeout; a node starting with <=0 remaining gets
    # a tiny positive timeout so the normal timeout error path reports it.
    _deadline = run_deadline.get()
    if _deadline is not None:
        remaining = max(_deadline - time.monotonic(), 0.001)
        timeout = remaining if timeout is None else min(timeout, remaining)
```

(`from noodle.context import run_deadline` joins the existing context import at the top; `time` is already imported.)

- [ ] **Step 6:** new test PASS, then the full engine suite: `uv run pytest packages/core/tests -q` → all green (existing deadline tests must still pass — the "refuse to start" behaviour in the scheduler is unchanged). Ruff clean. **Commit:** `feat(engine): run deadline caps in-flight node timeouts`.

---

### Task 11: `checkpoint_truncated` run event

**Files:**
- Modify: `apps/api/app/services/runner.py` (`_save_checkpoint` ~line 204, `on_event` caller ~line 1185, final flush ~line 1477)
- Test: `apps/api/tests/test_checkpoint_truncated_event.py` (new)

- [ ] **Step 1: Failing test:**

```python
"""Oversized checkpoints emit one checkpoint_truncated event per run."""

from app.services import runner as runner_mod
from app.services.runner import _save_checkpoint


async def test_save_checkpoint_reports_truncation(monkeypatch):
    saved = {}

    class _FakeSession:
        async def execute(self, *a, **k): saved["hit"] = True
        async def commit(self): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(runner_mod, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(runner_mod, "_MAX_CHECKPOINT_BYTES", 64)
    truncated = await _save_checkpoint(
        "run-1", {"n1": {"main": {"blob": "x" * 500}}}, {"n1"}, "n1"
    )
    assert truncated is True


async def test_save_checkpoint_small_not_truncated(monkeypatch):
    class _FakeSession:
        async def execute(self, *a, **k): pass
        async def commit(self): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(runner_mod, "SessionLocal", lambda: _FakeSession())
    truncated = await _save_checkpoint("run-1", {"n1": {"main": {"a": 1}}}, {"n1"}, "n1")
    assert truncated is False
```

- [ ] **Step 2:** run → FAIL (`_save_checkpoint` returns `None`).

- [ ] **Step 3: Implement.** Change `_save_checkpoint`'s signature return to `-> bool` and docstring line "Returns True when the payload had to be truncated to fit." Return `False` on the two early-exit paths and at the end of the happy path; in the truncation branch (`if approx > _MAX_CHECKPOINT_BYTES:`) set a local `was_truncated = True` and return it after the DB write. Then in `_execute_run_impl`: add `checkpoint_truncated_warned = False` next to the other run-locals (~line 1112); at BOTH `_save_checkpoint` call sites capture the return and publish once:

```python
                    was_truncated = await _save_checkpoint(...)
                    checkpoint_debouncer.mark_persisted()
                    if was_truncated and not checkpoint_truncated_warned:
                        checkpoint_truncated_warned = True
                        broker.publish(run_id, {
                            "type": "checkpoint_truncated",
                            "run_id": run_id,
                            "detail": "run state exceeds the 1 MiB checkpoint cap; "
                                      "crash-resume may recompute some nodes",
                        })
```

(`nonlocal checkpoint_truncated_warned` inside `on_event`.)

- [ ] **Step 4:** tests PASS; `uv run pytest apps/api/tests -q -k checkpoint` green; ruff clean. **Commit:** `feat(engine): surface checkpoint truncation as a run event`.

---

### Task 12: Default code-node timeout 600 s

**Files:**
- Modify: `apps/api/app/config.py` (~line 225), `.env.example`
- Test: `apps/api/tests/test_code_timeout_default.py` (new)

- [ ] **Step 1: Failing test:**

```python
from app.config import Settings


def test_code_node_timeout_defaults_to_600():
    assert Settings(_env_file=None).code_node_timeout_seconds == 600.0
```

- [ ] **Step 2:** FAIL (`0.0 != 600.0`).

- [ ] **Step 3:** In `config.py` change `code_node_timeout_seconds: float = 0.0` to `600.0` and rewrite its comment:

```python
    # Default per-node timeout (seconds) for ``code`` nodes when the node
    # doesn't set its own ``timeout_seconds``. Defaults to 600 so a hung code
    # node can't wedge a run for the stuck-run grace period. Set 0 to remove
    # the cap for long-running data jobs (bounded then only by
    # ``workflow_run_timeout_seconds`` / the run deadline).
    code_node_timeout_seconds: float = 600.0
```

In `.env.example`, add under the runtime section:

```bash
# Per-node cap for Code nodes (seconds). 0 = uncapped. Default 600.
#CODE_NODE_TIMEOUT_SECONDS=600
```

- [ ] **Step 4:** new test PASS, then `uv run pytest apps/api/tests -q -k "config or timeout"` → any test asserting the old `0.0` default must be updated to 600.0 (update assertions only, never behaviour). **Commit:** `feat(engine): default 600s code-node timeout`.

---

# Area D — Queue / Durable Execution (9 → 10)

### Task 13: Soak test script + runbook

**Files:**
- Create: `scripts/soak_test.py`, `docs/soak-testing.md`
- Test: the script IS the test — it must also be import-safe (`uv run python -c "import scripts.soak_test"` not required; just `uv run ruff check scripts/soak_test.py`).

**Interfaces:**
- Consumes: live API (`--base-url`, `--token`), the compose stack, docker CLI for worker kills.
- Produces: exit 0 with a printed report when invariants hold; exit 1 listing violations.

- [ ] **Step 1: Write `scripts/soak_test.py`** (complete):

```python
"""Durable-queue soak test: run storm + cancel storm + worker kill.

Usage (against the compose stack):
    docker compose -f deploy/docker-compose.yml up -d
    NOODLE_TOKEN=... uv run python scripts/soak_test.py \
        --base-url http://localhost:8000 --runs 100 --cancel-ratio 0.2 \
        --kill-container noodle-worker-1

Invariants asserted:
  I1  every started run reaches a terminal status (success/error/cancelled)
      within --settle-seconds of the storm ending;
  I2  no queue entry is left leased/running after settling;
  I3  the worker kill loses zero runs (they re-lease and finish).
Exit 0 = all invariants hold; exit 1 = violations printed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time

import httpx

TERMINAL = {"success", "error", "cancelled"}

WORKFLOW_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual_trigger", "params": {}, "position": {"x": 0, "y": 0}},
        {
            "id": "c",
            "type": "code",
            "params": {"code": "import time\ntime.sleep(2)\nreturn {'ok': True}"},
            "position": {"x": 200, "y": 0},
        },
    ],
    "edges": [
        {"id": "e1", "source": "t", "source_output": "main",
         "target": "c", "target_input": "main"},
    ],
}


def _headers() -> dict:
    token = os.environ.get("NOODLE_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


async def make_workflow(client: httpx.AsyncClient) -> str:
    r = await client.post("/workflows", json={"name": f"soak-{int(time.time())}"})
    r.raise_for_status()
    wf_id = r.json()["id"]
    r = await client.put(f"/workflows/{wf_id}", json={"graph": WORKFLOW_GRAPH})
    r.raise_for_status()
    return wf_id


async def start_run(client: httpx.AsyncClient, wf_id: str) -> str | None:
    r = await client.post(f"/workflows/{wf_id}/runs", json={})
    if r.status_code >= 400:
        print(f"  start rejected ({r.status_code}): {r.text[:120]}")
        return None
    return r.json()["run_id"] if "run_id" in r.json() else r.json().get("id")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--cancel-ratio", type=float, default=0.2)
    ap.add_argument("--kill-container", default="")
    ap.add_argument("--settle-seconds", type=float, default=180.0)
    args = ap.parse_args()

    async with httpx.AsyncClient(
        base_url=args.base_url, headers=_headers(), timeout=30.0
    ) as client:
        wf_id = await make_workflow(client)
        print(f"workflow {wf_id}; storming {args.runs} runs")

        run_ids: list[str] = []
        cancelled: set[str] = set()
        for i in range(args.runs):
            rid = await start_run(client, wf_id)
            if rid:
                run_ids.append(rid)
                if (i % max(int(1 / max(args.cancel_ratio, 0.01)), 1)) == 0:
                    await client.delete(f"/runs/{rid}")
                    cancelled.add(rid)
            if args.kill_container and i == args.runs // 2:
                print(f"killing worker {args.kill_container} mid-storm")
                subprocess.run(["docker", "kill", args.kill_container], check=False)
                subprocess.run(["docker", "start", args.kill_container], check=False)
            await asyncio.sleep(0.05)

        print(f"storm done ({len(run_ids)} started, {len(cancelled)} cancel requests); settling")
        deadline = time.monotonic() + args.settle_seconds
        pending = set(run_ids)
        statuses: dict[str, str] = {}
        while pending and time.monotonic() < deadline:
            for rid in list(pending):
                r = await client.get(f"/runs/{rid}")
                if r.status_code == 200:
                    st = r.json().get("status", "")
                    statuses[rid] = st
                    if st in TERMINAL:
                        pending.discard(rid)
            if pending:
                await asyncio.sleep(3)

        violations: list[str] = []
        if pending:
            violations.append(
                f"I1 violated: {len(pending)} run(s) not terminal after "
                f"{args.settle_seconds}s: "
                + ", ".join(f"{r}={statuses.get(r)}" for r in sorted(pending)[:10])
            )
        r = await client.get("/ops/queue")
        if r.status_code == 200:
            stats = r.json()
            for key in ("leased", "running"):
                if int(stats.get(key, 0)) > 0:
                    violations.append(f"I2 violated: queue reports {key}={stats[key]}")
        else:
            print(f"note: /ops/queue returned {r.status_code}; skipping I2")

        by_status: dict[str, int] = {}
        for st in statuses.values():
            by_status[st] = by_status.get(st, 0) + 1
        print(f"terminal breakdown: {by_status}")
        if violations:
            print("\nSOAK FAILED:")
            for v in violations:
                print(f"  - {v}")
            return 1
        print("\nSOAK PASSED: all runs terminal, queue drained, worker kill survived.")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

Before finalizing, verify each endpoint path against the routers (`grep -n "post\|delete\|get" apps/api/app/routers/runs.py apps/api/app/routers/workflows.py apps/api/app/routers/ops.py | head -40`): the run-start route, run-cancel route (`DELETE /runs/{id}` vs `POST /runs/{id}/cancel`), the queue stats route (`/ops/queue`), and the workflow create/update payload shapes. Correct the script to the real paths/shapes — this step is mandatory, not optional.

- [ ] **Step 2: Write `docs/soak-testing.md`:** how to bring up compose, mint a token, run the three profiles (baseline `--runs 50 --cancel-ratio 0`, cancel storm `--cancel-ratio 0.3`, kill test `--kill-container noodle-worker-1`), what each invariant means, and that the run's output must be pasted into the PR that claims soak evidence. Include the exact commands from the script docstring.

- [ ] **Step 3: Verify locally:** `uv run ruff check scripts/soak_test.py` clean; if Docker + compose stack are available, run the baseline profile and paste output into the commit body. If Docker is NOT available in the execution environment, state that explicitly in the commit body — do not fake a run.

- [ ] **Step 4: Commit:** `test(queue): soak test script + runbook (worker kill, cancel storm)`.

---

### Task 14: Bound the single-flight lock map

**Files:**
- Modify: `apps/api/app/services/runner.py` (`_workflow_single_flight_locks`, ~line 137, and its use ~line 646)
- Test: `apps/api/tests/test_single_flight_lock_bound.py` (new)

- [ ] **Step 1: Failing test:**

```python
import asyncio

from app.services import runner as runner_mod


def test_lock_map_evicts_unlocked_entries():
    runner_mod._workflow_single_flight_locks.clear()
    for i in range(runner_mod._SINGLE_FLIGHT_LOCKS_MAX + 10):
        runner_mod._workflow_single_flight_locks[f"wf-{i}"] = asyncio.Lock()
    runner_mod._prune_single_flight_locks()
    assert len(runner_mod._workflow_single_flight_locks) <= runner_mod._SINGLE_FLIGHT_LOCKS_MAX
```

- [ ] **Step 2:** FAIL (no `_SINGLE_FLIGHT_LOCKS_MAX`).

- [ ] **Step 3: Implement** in `runner.py` next to the dict:

```python
# SQLite-only single-flight locks accumulate one entry per workflow ever run
# in this process. Prune unlocked entries above this bound (locked entries are
# in active use and always kept).
_SINGLE_FLIGHT_LOCKS_MAX = 1024


def _prune_single_flight_locks() -> None:
    if len(_workflow_single_flight_locks) <= _SINGLE_FLIGHT_LOCKS_MAX:
        return
    for wf_id in [
        k for k, lk in _workflow_single_flight_locks.items() if not lk.locked()
    ]:
        del _workflow_single_flight_locks[wf_id]
        if len(_workflow_single_flight_locks) <= _SINGLE_FLIGHT_LOCKS_MAX:
            break
```

Call `_prune_single_flight_locks()` in `_start_run_impl` immediately before `_workflow_single_flight_locks.setdefault(...)` (~line 648).

- [ ] **Step 4:** test PASS; `uv run pytest apps/api/tests -q -k single_flight` green; ruff clean. **Commit:** `fix(queue): bound the SQLite single-flight lock map`.

---

# Area E — Workers (9 → 10)

### Task 15: Remove the empty `apps/worker` shell

**Files:**
- Delete: `apps/worker/` (contains only `worker/__pycache__` and a `tests/` dir)

- [ ] **Step 1: Prove it is dead.** Run all three; every one must come back empty (excluding `apps/worker` itself):

```bash
grep -rn "apps.worker\|apps/worker" --include="*.py" --include="*.toml" --include="*.yml" --include="*.yaml" --include="*.cfg" . | grep -v "^./apps/worker" | grep -v node_modules | grep -v "\.venv"
ls apps/worker/tests
git ls-files apps/worker
```

If `apps/worker/tests` contains any test files, STOP and report — they must be relocated deliberately, not deleted.

- [ ] **Step 2:** `git rm -r apps/worker` (if `git ls-files apps/worker` was empty — i.e. untracked — use `rm -rf apps/worker` instead and note it).

- [ ] **Step 3:** `uv run pytest apps/api/tests -q -x --co -q | tail -2` (collection still works) and `uv sync --all-packages` succeeds. **Commit:** `chore(workers): remove empty apps/worker shell (real worker is app.worker_main)`.

---

### Task 16: Worker /metrics HTTP listener

**Files:**
- Modify: `apps/api/app/config.py`, `apps/api/app/worker_main.py`
- Test: `apps/api/tests/test_worker_metrics_server.py` (new)

**Interfaces:**
- Produces: setting `worker_metrics_port: int = 0` (0 = disabled); `app.worker_main._serve_metrics(port) -> asyncio.Server` serving `GET /metrics` with `app.services.metrics.get_metrics_text()`.

- [ ] **Step 1: Failing test:**

```python
import asyncio

import httpx

from app.worker_main import _serve_metrics


async def test_metrics_server_serves_openmetrics():
    server = await _serve_metrics(0)  # port 0 = ephemeral
    port = server.sockets[0].getsockname()[1]
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://127.0.0.1:{port}/metrics")
        assert resp.status_code == 200
        assert "noodle" in resp.text or "#" in resp.text  # exposition text
    finally:
        server.close()
        await server.wait_closed()
```

- [ ] **Step 2:** FAIL (no `_serve_metrics`).

- [ ] **Step 3: Config:** add after `queue_drain` in `config.py`:

```python
    # Worker-only: serve GET /metrics on this port (OpenMetrics text) so
    # Prometheus can scrape the execution plane directly. 0 = disabled.
    worker_metrics_port: int = 0
```

- [ ] **Step 4: Implement** in `worker_main.py` (stdlib asyncio — no new deps):

```python
async def _serve_metrics(port: int) -> asyncio.Server:
    """Minimal HTTP responder for GET /metrics. Anything else gets 404.

    Deliberately not a web framework: the worker's only HTTP surface is this
    one read-only endpoint, bound by the operator's network policy.
    """
    from app.services.metrics import get_metrics_text

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            while (await asyncio.wait_for(reader.readline(), timeout=5.0)).strip():
                pass  # drain headers
            if request_line.startswith(b"GET /metrics"):
                body = get_metrics_text().encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; version=0.0.4\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode() + body
                )
            else:
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
        except Exception:  # noqa: BLE001 — a bad scrape must never hurt the worker
            pass
        finally:
            writer.close()

    return await asyncio.start_server(_handle, "0.0.0.0", port)
```

In `_amain()`, after `await init_sandbox()` add:

```python
    metrics_server: asyncio.Server | None = None
    if settings.worker_metrics_port > 0:
        metrics_server = await _serve_metrics(settings.worker_metrics_port)
        logger.info("worker metrics on :%d/metrics", settings.worker_metrics_port)
```

and in the `finally:` teardown block add (before `engine.dispose`):

```python
        if metrics_server is not None:
            metrics_server.close()
            with contextlib.suppress(Exception):
                await metrics_server.wait_closed()
```

Verify the exact render-function name first: `grep -n "def get_metrics_text" apps/api/app/services/metrics.py` (it exists; if the signature takes args, mirror `app/routers/metrics.py`'s call).

- [ ] **Step 5:** test PASS; ruff clean. Document in `.env.example`: `#WORKER_METRICS_PORT=9401`. **Commit:** `feat(workers): optional /metrics listener on the execution plane`.

---

# Area F — Sandbox (8 → 10)

### Task 17: Document sandbox configuration in `.env.example`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1:** Append this block (keys verified against `app/config.py` lines 232–269):

```bash
# ── Sandboxed execution (container-per-run isolation) ─────────────────────
# off      = warm subprocess pool (default; trusted single-tenant)
# auto     = hardened containers when a Docker daemon is reachable, else
#            fall back to the subprocess pool with a startup warning
# required = refuse to start without a working daemon + runtime
#            (mandatory when MULTI_TENANCY_ENABLED=true)
#EXECUTION_SANDBOX=auto
# Isolation runtime: auto picks the strongest available (kata > runsc > runc).
#SANDBOX_RUNTIME=auto
# Docker daemon for sandbox containers; empty = DOCKER_HOST / default socket.
#SANDBOX_DOCKER_HOST=
# Dedicated bridge network for run containers (keeps tenant code away from
# postgres/redis/minio).
#SANDBOX_NETWORK=noodle-sandbox
# Per-container ceilings.
#SANDBOX_MEM_LIMIT=1g
#SANDBOX_CPU_LIMIT=1.0
#SANDBOX_PIDS_LIMIT=256
#SANDBOX_TMPFS_SIZE=256m
# Warm pool: idle containers kept per (org, env) / globally, TTL, recycle cap.
#SANDBOX_WARM_PER_KEY=1
#SANDBOX_WARM_TOTAL=8
#SANDBOX_WARM_TTL_SECONDS=300
#SANDBOX_MAX_RUNS_PER_CONTAINER=50
# Ceilings for per-workflow sandbox resource requests (workflow settings can
# raise limits up to these, never past them).
#SANDBOX_MAX_MEMORY_MB=8192
#SANDBOX_MAX_CPU=4.0
#SANDBOX_MAX_TMPFS_MB=2048
```

- [ ] **Step 2:** No test applies. `git add .env.example`; **commit:** `docs(sandbox): document all sandbox env vars in .env.example`.

---

### Task 18: One-command sandboxed compose

**Files:**
- Create: `deploy/docker-compose.sandbox.yml`
- Modify: `docs/deployment.md` (add usage; if the "Sandboxed execution" section exists, extend it — `grep -n "Sandboxed execution" docs/deployment.md`)

- [ ] **Step 1: Write `deploy/docker-compose.sandbox.yml`:**

```yaml
# Overlay enabling sandboxed (container-per-run) execution on the worker.
#
#   docker compose -f docker-compose.yml -f docker-compose.sandbox.yml up -d
#
# The worker gains the host Docker socket so it can spawn hardened sibling
# run containers (Docker-out-of-Docker). The worker itself never executes
# untrusted code — runs execute in the disposable containers it creates
# (cap_drop ALL, read-only rootfs, dedicated egress-isolated network).
services:
  worker:
    environment:
      EXECUTION_SANDBOX: ${EXECUTION_SANDBOX:-auto}
      SANDBOX_RUNTIME: ${SANDBOX_RUNTIME:-auto}
    volumes:
      - envdata:/app/envs
      - artifactdata:/app/artifacts
      - /var/run/docker.sock:/var/run/docker.sock
```

Note: compose merges `environment` maps but REPLACES `volumes` lists — that is why the two named volumes are repeated here. Verify with step 2.

- [ ] **Step 2: Validate merge:**

```bash
cd deploy && docker compose -f docker-compose.yml -f docker-compose.sandbox.yml config 2>/dev/null | grep -A4 "docker.sock"
```

Expected: the worker service shows all three volumes and `EXECUTION_SANDBOX: auto`. If `docker` is unavailable in the execution environment, run `python -c "import yaml,sys; yaml.safe_load(open('docker-compose.sandbox.yml'))"` as a syntax floor and note the limitation in the commit body.

- [ ] **Step 3: Docs.** In `docs/deployment.md`, in/near the sandbox section, add the exact two-file `up` command and one sentence per mode (`auto`/`required`).

- [ ] **Step 4: Commit:** `feat(deploy): sandboxed compose overlay — one command to enable container isolation`.

---

### Task 18b: Daemonless sandbox via rootless Podman (docs + probe test)

Hosts without a Docker daemon can still get container sandboxing: rootless Podman exposes a Docker-compatible API socket, and `SANDBOX_DOCKER_HOST` already points the SDK at an arbitrary socket (`sandbox_pool._make_docker_client`, config.py ~line 242). This task documents and pins that path — no Python-level "jail" libraries (see the security note below).

**Files:**
- Modify: `docs/deployment.md`, `.env.example`
- Test: `apps/api/tests/test_sandbox_docker_host.py` (new)

- [ ] **Step 1: Failing test** — prove `_make_docker_client` honours the socket override:

```python
from app.config import settings
from app.services import sandbox_pool


def test_make_docker_client_uses_sandbox_docker_host(monkeypatch):
    captured = {}

    class _FakeDocker:
        @staticmethod
        def DockerClient(base_url):  # noqa: N802 — mirrors the SDK surface
            captured["base_url"] = base_url
            return object()

        @staticmethod
        def from_env():
            captured["base_url"] = "from_env"
            return object()

    monkeypatch.setitem(__import__("sys").modules, "docker", _FakeDocker)
    monkeypatch.setattr(
        settings, "sandbox_docker_host", "unix:///run/user/1000/podman/podman.sock"
    )
    sandbox_pool._make_docker_client()
    assert captured["base_url"] == "unix:///run/user/1000/podman/podman.sock"
```

- [ ] **Step 2:** Run it. If it already passes (the code path exists), that is the expected outcome — keep the test as a pin and skip to Step 3. If it fails, STOP and report (the config seam has drifted).

- [ ] **Step 3: Docs.** In `docs/deployment.md`'s sandbox section add a "Without Docker: rootless Podman" subsection:

```markdown
### Without Docker: rootless Podman

The sandbox talks to any Docker-API-compatible daemon. On a host without
Docker, rootless Podman works daemonlessly and without root:

    systemctl --user enable --now podman.socket
    export SANDBOX_DOCKER_HOST=unix:///run/user/$(id -u)/podman/podman.sock
    export EXECUTION_SANDBOX=auto

Notes: rootless Podman ignores the `runtime` selection for gVisor/Kata
(runs runc-equivalent crun); resource limits (memory/cpu/pids) require
cgroups v2 delegation — on most modern distros this is already on. Verify
with Settings → the sandbox status card, or `GET /ops/sandbox`.

Python-level sandboxing libraries (pysandbox, RestrictedPython, PyPy's
sandbox) are NOT supported as isolation modes: in-process Python jails are
escapable by design and would be a false security boundary. The supported
tiers are: subprocess pool (trusted), containers via Docker/Podman
(untrusted code), and remote runner pools.
```

- [ ] **Step 4:** Add to `.env.example` under the sandbox block: `# Rootless Podman: unix:///run/user/1000/podman/podman.sock` above the `#SANDBOX_DOCKER_HOST=` line. Run the test file + ruff. **Commit:** `docs(sandbox): daemonless container sandboxing via rootless Podman`.

---

### Task 19: Sandbox status endpoint

**Files:**
- Modify: `apps/api/app/routers/ops.py`, `apps/api/app/services/sandbox_pool.py`
- Test: `apps/api/tests/test_ops_sandbox.py` (new)

**Interfaces:**
- Produces: `GET /ops/sandbox` (permission `ops:pool:read`) → `{"mode": str, "active": bool, "runtime": str | None, "network": str | None, "idle": int, "active_runs": int}`. Task 20 (UI) consumes this exact shape.

- [ ] **Step 1: Failing test** (copy the client/permission fixture style from an existing `/ops` test — `grep -rn "ops/pool" apps/api/tests | head -3` and open that file):

```python
async def test_ops_sandbox_reports_mode(client_with_ops_permission):
    resp = await client_with_ops_permission.get("/ops/sandbox")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] in ("off", "auto", "required")
    assert body["active"] is False  # no docker in the test environment
    assert isinstance(body["idle"], int)
```

(Use whatever the real fixture name is — mirror the neighboring ops test exactly.)

- [ ] **Step 2:** FAIL (404).

- [ ] **Step 3: Pool introspection.** In `sandbox_pool.py`, class `SandboxPool`, add:

```python
    def status(self) -> dict:
        """Structured status for the ops endpoint / settings card."""
        return {
            "active": self.enabled,
            "runtime": self._runtime if self.enabled else None,
            "network": self._network if self.enabled else None,
            "idle": sum(len(v) for v in self._idle.values()),
            "active_runs": len(self._active),
        }
```

- [ ] **Step 4: Route** in `ops.py`, after `pool_status`:

```python
@router.get(
    "/ops/sandbox",
    dependencies=[Depends(require_permission("ops:pool:read"))],
)
async def sandbox_status() -> dict:
    """Sandbox mode, probe result, and warm-pool occupancy."""
    from app.services.sandbox_pool import pool as _sbx_pool
    return {"mode": settings.execution_sandbox, **_sbx_pool.status()}
```

- [ ] **Step 5:** test PASS; ruff clean. **Commit:** `feat(sandbox): GET /ops/sandbox status endpoint`.

---

### Task 20: Sandbox card in Settings

**Files:**
- Modify: `apps/web/src/SettingsPage.tsx`, `apps/web/src/api.ts`
- Test: extend `apps/web/src/SettingsPage.test.tsx`

**Interfaces:**
- Consumes: `GET /ops/sandbox` (Task 19 shape).

- [ ] **Step 1: api method** (in the `api` object):

```ts
  sandboxStatus(): Promise<{
    mode: string; active: boolean; runtime: string | null;
    network: string | null; idle: number; active_runs: number;
  }> {
    return request(`/ops/sandbox`);
  },
```

- [ ] **Step 2: Card.** In `SettingsPage.tsx` add a section (same wrapper markup as neighbors) using `useQuery({queryKey:["sandbox-status"], queryFn: api.sandboxStatus, retry: false})`. Render rules:
  - query errored (403/404 — viewer without ops permission): render nothing.
  - `active === true`: green-tinted card "Sandboxed execution: ON — runtime {runtime}, {idle} warm / {active_runs} running, network {network}".
  - `active === false && mode !== "off"`: amber card "Sandbox mode '{mode}' configured but no Docker daemon is reachable — runs use the subprocess pool."
  - `mode === "off"`: neutral card "Sandboxed execution is off. Workflow code runs in warm subprocess pools with host privileges. Enable container isolation with EXECUTION_SANDBOX=auto — see docs/deployment.md."

- [ ] **Step 3: Test:** mock `api.sandboxStatus` → `{mode:"off",active:false,runtime:null,network:null,idle:0,active_runs:0}`; assert `screen.getByText(/Sandboxed execution is off/)` renders.

- [ ] **Step 4:** `npm run typecheck && npm test -- --run` green. **Commit:** `feat(web): sandbox status card in Settings`.

---

### Task 21: Workflow-level execution mode — model, resolver, and routing

Workflow-level sandboxing: each workflow chooses `inherit | sandboxed | standard`; a per-run override can force one run into the sandbox. Fail-closed rules — overrides may only ESCALATE to sandboxed; multi-tenant strict and `execution_sandbox=required` sandbox everything regardless; a `sandboxed` workflow with no reachable sandbox REFUSES to run instead of silently using the host pool.

**Files:**
- Modify: `apps/api/app/config.py`, `apps/api/app/models.py` (`Workflow` ~line 503, `Run` ~line 750), `apps/api/app/services/sandbox_policy.py`, `apps/api/app/services/runner.py`, `apps/api/app/exceptions.py`
- Create: `apps/api/alembic/versions/0082_workflow_execution_mode.py`
- Test: `apps/api/tests/test_execution_mode.py` (new)

**Interfaces:**
- Produces: `Workflow.execution_mode: str` (`inherit|sandboxed|standard`, default `inherit`), `Run.execution_mode: str | None` (per-run escalation), setting `sandbox_workflow_default: Literal["sandboxed","standard"] = "sandboxed"`, `sandbox_policy.resolve_execution_mode(*, run_override, workflow_mode) -> str`, exception `SandboxRequired` (HTTP 409). Tasks 21b/21c consume all of these; `start_run` gains kwarg `execution_mode: str | None = None`.

- [ ] **Step 1: Failing resolver tests** — `apps/api/tests/test_execution_mode.py`:

```python
"""resolve_execution_mode precedence: fail-closed, escalate-only."""

from app.config import settings
from app.services.sandbox_policy import resolve_execution_mode


def test_multi_tenant_strict_always_sandboxed(monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    monkeypatch.setattr(settings, "sandbox_policy_strict", True)
    assert resolve_execution_mode(run_override=None, workflow_mode="standard") == "sandboxed"


def test_required_mode_sandboxes_everything(monkeypatch):
    monkeypatch.setattr(settings, "execution_sandbox", "required")
    assert resolve_execution_mode(run_override=None, workflow_mode="standard") == "sandboxed"


def test_inherit_follows_deployment_default(monkeypatch):
    monkeypatch.setattr(settings, "execution_sandbox", "auto")
    monkeypatch.setattr(settings, "sandbox_workflow_default", "standard")
    assert resolve_execution_mode(run_override=None, workflow_mode="inherit") == "standard"
    monkeypatch.setattr(settings, "sandbox_workflow_default", "sandboxed")
    assert resolve_execution_mode(run_override=None, workflow_mode="inherit") == "sandboxed"


def test_inherit_with_sandbox_off_is_standard(monkeypatch):
    monkeypatch.setattr(settings, "execution_sandbox", "off")
    assert resolve_execution_mode(run_override=None, workflow_mode="inherit") == "standard"


def test_run_override_escalates_but_never_downgrades(monkeypatch):
    monkeypatch.setattr(settings, "execution_sandbox", "auto")
    assert (
        resolve_execution_mode(run_override="sandboxed", workflow_mode="standard")
        == "sandboxed"
    )
    # a "standard" override must NOT downgrade a sandboxed workflow
    assert (
        resolve_execution_mode(run_override="standard", workflow_mode="sandboxed")
        == "sandboxed"
    )


def test_workflow_sandboxed_wins_even_when_sandbox_off(monkeypatch):
    # resolver still says sandboxed; the admission/execution gates then refuse
    # the run rather than silently downgrading (fail-closed).
    monkeypatch.setattr(settings, "execution_sandbox", "off")
    assert resolve_execution_mode(run_override=None, workflow_mode="sandboxed") == "sandboxed"
```

- [ ] **Step 2:** `uv run pytest apps/api/tests/test_execution_mode.py -q` → FAIL (import error).

- [ ] **Step 3: Config.** In `config.py`, after `sandbox_policy_strict` add:

```python
    # What execution_mode="inherit" workflows get when the sandbox pool is
    # available. "sandboxed" (default) preserves the historical behaviour:
    # EXECUTION_SANDBOX=auto|required routes every local run into containers.
    # "standard" makes the sandbox opt-in per workflow: only workflows marked
    # execution_mode="sandboxed" (or runs with the sandbox override) use
    # containers; the rest keep the warm subprocess pool.
    sandbox_workflow_default: Literal["sandboxed", "standard"] = "sandboxed"
```

- [ ] **Step 4: Resolver.** In `sandbox_policy.py` add:

```python
VALID_EXECUTION_MODES = ("inherit", "sandboxed", "standard")


def resolve_execution_mode(
    *, run_override: str | None, workflow_mode: str | None
) -> str:
    """Effective execution mode for a run. Fail-closed precedence:

    1. multi-tenant strict → "sandboxed" (per-workflow opt-outs never honoured);
    2. execution_sandbox=required → "sandboxed" (operator mandate);
    3. workflow execution_mode; "inherit" resolves to the deployment default
       (``sandbox_workflow_default`` when the sandbox can be active,
       "standard" when execution_sandbox=off);
    4. a per-run override may only ESCALATE to "sandboxed" — any other
       override value is ignored so a run flag can never bypass a workflow's
       sandbox requirement.
    """
    if settings.multi_tenancy_enabled and settings.sandbox_policy_strict:
        return "sandboxed"
    if settings.execution_sandbox == "required":
        return "sandboxed"
    mode = workflow_mode if workflow_mode in ("sandboxed", "standard") else "inherit"
    if mode == "inherit":
        mode = (
            settings.sandbox_workflow_default
            if settings.execution_sandbox != "off"
            else "standard"
        )
    if run_override == "sandboxed":
        return "sandboxed"
    return mode
```

- [ ] **Step 5:** resolver tests PASS.

- [ ] **Step 6: Model + migration.** In `models.py` class `Workflow`, next to `allow_concurrent` (grep it):

```python
    # inherit | sandboxed | standard — see sandbox_policy.resolve_execution_mode.
    execution_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="inherit", server_default="inherit"
    )
```

In class `Run` (next to `runner_pool_id` — grep it):

```python
    # Per-run escalation stamped at start_run; only "sandboxed" is honoured.
    execution_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
```

Migration `0082_workflow_execution_mode.py` (`down_revision = "0081_mcp_connection_policy"`):

```python
"""Workflow-level execution mode + per-run sandbox escalation.

Revision ID: 0082_workflow_execution_mode
Revises: 0081_mcp_connection_policy
"""

import sqlalchemy as sa
from alembic import op

revision = "0082_workflow_execution_mode"
down_revision = "0081_mcp_connection_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("execution_mode", sa.String(length=20), nullable=False,
                  server_default="inherit"),
    )
    op.add_column("runs", sa.Column("execution_mode", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "execution_mode")
    op.drop_column("workflows", "execution_mode")
```

- [ ] **Step 7: Exception.** In `exceptions.py`, mirror `DedicatedPoolRequired` (read it first — same base class, same http-status wiring, 409):

```python
class SandboxRequired(ServiceError):
    """Run resolved to sandboxed execution but no sandbox is available."""
```

(Copy `DedicatedPoolRequired`'s constructor/status pattern exactly; use 409.)

- [ ] **Step 8: Stamp + thread the override.** In `runner.py`:
  - `start_run` and `_start_run_impl`: add kwarg `execution_mode: str | None = None`; forward it. Where `_start_run_impl` constructs the `Run(` row (grep `Run(` inside it), stamp `execution_mode=("sandboxed" if execution_mode == "sandboxed" else None),`.
  - `_execute_queued_entry`: it already loads `run = await session.get(Run, run_id)`; capture `run_execution_mode = run.execution_mode` next to `runner_pool_id = run.runner_pool_id` and pass `run_execution_mode=run_execution_mode` through `_execute_run` → `_execute_run_impl` (add the kwarg, default `None`, to both).

- [ ] **Step 9: Admission gate.** In `_start_run_impl`, directly AFTER the `dedicated_pool` gate block (~line 601):

```python
        # Workflow-level sandbox routing: refuse dispatch outright when the
        # run resolves to sandboxed but this deployment cannot honour it —
        # config-level check so API (control) and worker replicas agree.
        from app.services.sandbox_policy import resolve_execution_mode

        _wf_mode = getattr(wf_obj, "execution_mode", "inherit") if wf_obj else "inherit"
        if resolve_execution_mode(
            run_override=execution_mode, workflow_mode=_wf_mode
        ) == "sandboxed" and settings.execution_sandbox == "off":
            pool_qualifies = False
            if runner_pool_id:
                from app.models import RunnerPool as _RP

                _pool = await session.get(_RP, runner_pool_id)
                pool_qualifies = _pool is not None and _pool.provider in (
                    "docker", "kubernetes"
                )
            if not pool_qualifies:
                raise SandboxRequired(
                    "This run requires sandboxed execution. Enable "
                    "EXECUTION_SANDBOX=auto|required on the worker (see "
                    "deploy/docker-compose.sandbox.yml), or assign a "
                    "docker/kubernetes runner pool."
                )
```

Add `SandboxRequired` to the runner's exception imports; confirm the router layer converts `ServiceError` subtypes to their http status (grep how `DedicatedPoolRequired` reaches the client — identical path).

- [ ] **Step 10: Execution-time routing.** `_prepare_run_context` loads the Workflow row (verify with `grep -n "Workflow" apps/api/app/services/runner.py | head -20`); add `execution_mode: str` to `_PreparedRunContext` populated from it (default `"inherit"`). Then in `_execute_run_impl`'s `use_subprocess_runner` branch (~line 1295), replace the executor selection:

```python
            effective_mode = resolve_execution_mode(
                run_override=run_execution_mode,
                workflow_mode=prep.execution_mode,
            )
            if runner_pool_id:
                ...existing remote branch unchanged...
            elif effective_mode == "sandboxed":
                if not sandbox_executor.active:
                    raise SandboxRequired(
                        "run requires sandboxed execution but this worker has "
                        "no active sandbox (EXECUTION_SANDBOX=off or Docker "
                        "unreachable)"
                    )
                ...existing sandbox branch unchanged...
            else:
                ...existing local branch unchanged...
```

(`from app.services.sandbox_policy import resolve_execution_mode` joins the module imports. Note the old condition `elif sandbox_executor.active:` is REPLACED by `elif effective_mode == "sandboxed":` — with the default `sandbox_workflow_default="sandboxed"` this is behaviour-identical for existing deployments: pool active ⇒ inherit ⇒ sandboxed; pool inactive + sandbox off ⇒ inherit ⇒ standard ⇒ local.)

- [ ] **Step 11: Behaviour tests** — append to `test_execution_mode.py` an admission-gate test (mirror the client fixture style of an existing run-start test — read one first; use the real run-start route from Task 13 step 1):

```python
async def test_sandboxed_workflow_409s_when_sandbox_off(client, ...):
    # create a workflow via the API, set execution_mode="sandboxed" directly
    # on the row (session fixture), then:
    resp = await client.post(f"/workflows/{wf_id}/runs", json={})
    assert resp.status_code == 409
    assert "sandbox" in resp.json()["detail"].lower()
```

And a routing unit test: monkeypatch `runner_mod.sandbox_executor` with a stub where `active` is False, call `_execute_run_impl` with `run_execution_mode="sandboxed"`... if that requires too much harness, assert instead that the full-suite regression holds: `uv run pytest apps/api/tests -q -k "sandbox or runner"` green with the default config (proves no behaviour change for inherit).

- [ ] **Step 12:** all green; `cd apps/api && uv run alembic heads` → `0082_workflow_execution_mode (head)`; ruff clean. **Commit:** `feat(sandbox): workflow-level execution mode with escalate-only per-run override`.

---

### Task 21b: Execution mode over REST + MCP (LLM-driven sandboxing)

**Files:**
- Modify: `apps/api/app/routers/workflows.py` (settings update + run-start payload), `apps/api/app/routers/runs.py` (if the run-start route lives there — Task 13 step 1 established which), `apps/api/app/mcp/tools.py` (`_create_workflow` ~line 1134, `_run_workflow` ~line 719, `run_workflow_by_id` ~line 679, `_update_workflow_settings` ~line 2607, plus each tool's registered parameter schema)
- Test: `apps/api/tests/test_execution_mode_surfaces.py` (new)

**Interfaces:**
- Consumes: Task 21's `resolve_execution_mode`, `Workflow.execution_mode`, `start_run(execution_mode=...)`.
- Produces: REST — workflow-settings payload accepts `execution_mode`; run-start body accepts `sandbox: bool`. MCP — `update_workflow_settings` + `create_workflow` accept `execution_mode`; `run_workflow` accepts `sandbox: bool`.

- [ ] **Step 1: Failing tests** — `test_execution_mode_surfaces.py` (reuse the MCP dispatch helper from `apps/api/tests/test_mcp_tools.py` — read it first and copy its call pattern):

```python
async def test_workflow_settings_accepts_execution_mode(client, ...):
    ...create workflow via API...
    resp = await client.patch(f"/workflows/{wf_id}", json={"execution_mode": "sandboxed"})
    assert resp.status_code == 200
    ...reload row; assert wf.execution_mode == "sandboxed"...


async def test_workflow_settings_rejects_bad_mode(client, ...):
    resp = await client.patch(f"/workflows/{wf_id}", json={"execution_mode": "yolo"})
    assert resp.status_code == 422


async def test_mcp_update_workflow_settings_sets_mode(mcp_call, ...):
    result = await mcp_call("update_workflow_settings",
                            {"workflow_id": wf_id, "execution_mode": "sandboxed"})
    ...assert row updated...


async def test_mcp_run_workflow_sandbox_flag_stamps_run(mcp_call, monkeypatch, ...):
    # stub start_run to capture kwargs (mirror how test_mcp_tools stubs it)
    ...call run_workflow with {"workflow_id": wf_id, "sandbox": True}...
    assert captured["execution_mode"] == "sandboxed"
```

(Adapt route method/paths to what `routers/workflows.py` actually exposes for settings updates — grep `allow_concurrent` there and use the same route the UI uses. Keep the four test intents exactly.)

- [ ] **Step 2:** FAIL.

- [ ] **Step 3: REST.** In the workflow-settings update path, mirror the `allow_concurrent` field handling for `execution_mode`, validating against `sandbox_policy.VALID_EXECUTION_MODES` (422 otherwise). In the run-start endpoint's body model add `sandbox: bool = False` and pass `execution_mode=("sandboxed" if body.sandbox else None)` into `start_run`.

- [ ] **Step 4: MCP.** In `mcp/tools.py`:
  - `_update_workflow_settings`: accept optional `execution_mode`, validate against `VALID_EXECUTION_MODES`, apply to the row (mirror the function's existing optional-field pattern).
  - `_create_workflow`: accept optional `execution_mode` (same validation) so an agent can create a workflow born sandboxed.
  - `_run_workflow` and `run_workflow_by_id`: accept optional `sandbox: bool`; pass `execution_mode=("sandboxed" if sandbox else None)` to `start_run`.
  - Update each tool's registered parameter schema (find the `McpTool(` entries for `update_workflow_settings`, `create_workflow`, `run_workflow` and add the new properties with descriptions: e.g. `"sandbox": {"type": "boolean", "description": "Run in a disposable hardened container regardless of the workflow's execution mode"}`).

- [ ] **Step 5:** tests PASS; `uv run pytest apps/api/tests -q -k "mcp_tools or execution_mode"` green; ruff clean. **Commit:** `feat(mcp): agents can create, mark, and run workflows sandboxed (execution_mode over REST+MCP)`.

---

### Task 21c: Execution mode in the UI

**Files:**
- Modify: `apps/web/src/editor/WorkflowSettingsModal.tsx`, `apps/web/src/editor/MetaBar.tsx`, `apps/web/src/types.ts`, `apps/web/src/api.ts` (only if the settings-update call needs the new field typed)
- Test: extend `apps/web/src/editor/WorkflowSettingsModal.test.tsx` and `apps/web/src/editor/MetaBar.test.tsx`

**Interfaces:**
- Consumes: Task 21b's REST field `execution_mode` on the workflow settings payload; `GET /ops/sandbox` (Task 19) for availability hints.

- [ ] **Step 1: Types.** In `types.ts`, find the workflow type carrying `allow_concurrent` (grep) and add `execution_mode?: "inherit" | "sandboxed" | "standard";`.

- [ ] **Step 2: Settings modal.** In `WorkflowSettingsModal.tsx`, next to the existing concurrency control (grep `allow_concurrent`), add a labelled select following the modal's existing field markup:

```tsx
<label>
  Execution isolation
  <select
    value={executionMode}
    onChange={(e) => setExecutionMode(e.target.value as typeof executionMode)}
  >
    <option value="inherit">Deployment default</option>
    <option value="sandboxed">Always sandboxed (hardened container)</option>
    <option value="standard">Standard pool (trusted, fastest)</option>
  </select>
</label>
{executionMode === "sandboxed" && (
  <p className="muted">
    Runs execute in a disposable container with dropped capabilities and a
    read-only root filesystem. If no sandbox is available on the worker, runs
    are refused rather than downgraded.
  </p>
)}
```

Wire `executionMode` state from the workflow prop and include it in the save payload exactly like `allow_concurrent`.

- [ ] **Step 3: Badge.** In `MetaBar.tsx`, where existing status pills render (read the file; reuse the pill/badge component used by `PublishPill`), render a "Sandboxed" pill when `workflow.execution_mode === "sandboxed"`, with `title="Runs in a disposable hardened container"`.

- [ ] **Step 4: Tests.** Settings modal test: render with `execution_mode: "inherit"`, change the select to `sandboxed`, save, assert the mutation payload contains `execution_mode: "sandboxed"` (mirror the file's existing save-assertion pattern). MetaBar test: renders the "Sandboxed" pill when the prop is set, absent otherwise.

- [ ] **Step 5:** `cd apps/web && npm run typecheck && npm test -- --run` green. **Commit:** `feat(web): per-workflow execution isolation toggle + sandboxed badge`.

---

### Task 21d: Per-workflow sandbox resource limits

Users tune memory/CPU/tmpfs per workflow (a pandas job needs 4 GB; a scraper needs 512 MB). Rules: only **resource** keys are workflow-tunable — the security floor (cap_drop, no-new-privileges, read-only rootfs, network) never is; requests are validated against deployment ceilings at write time (422) AND clamped again at spawn time (defense in depth); resources join the warm-pool key so a high-memory workflow never reuses a low-memory container.

**Files:**
- Modify: `apps/api/app/config.py`, `apps/api/app/models.py` (class `Workflow`), `apps/api/app/services/sandbox_policy.py`, `apps/api/app/services/sandbox_pool.py`, `apps/api/app/services/executors/base.py`, `apps/api/app/services/executors/sandbox.py`, `apps/api/app/services/runner.py`, `apps/api/app/routers/workflows.py`, `apps/api/app/mcp/tools.py`, `apps/web/src/editor/WorkflowSettingsModal.tsx`, `apps/web/src/types.ts`
- Create: `apps/api/alembic/versions/0083_workflow_sandbox_resources.py`
- Test: `apps/api/tests/test_sandbox_resources.py` (new), extend `apps/web/src/editor/WorkflowSettingsModal.test.tsx`

**Interfaces:**
- Consumes: Task 21 (`execution_mode`, `_PreparedRunContext`), `container_runtime.hardening_kwargs(overrides=...)` + `_OVERRIDABLE` (existing).
- Produces: `Workflow.sandbox_resources: dict | None` (`{"memory_mb": int, "cpu": float, "tmpfs_mb": int}` — all keys optional), settings `sandbox_max_memory_mb=8192` / `sandbox_max_cpu=4.0` / `sandbox_max_tmpfs_mb=2048`, `sandbox_policy.resolve_sandbox_overrides(requested) -> dict` (docker spawn kwargs), `sandbox_policy.validate_sandbox_resources(payload) -> dict` (raises `ValueError` for the routers to 422), `RunExecutionContext["sandbox_spawn_overrides"]`.

- [ ] **Step 1: Failing tests** — `apps/api/tests/test_sandbox_resources.py`:

```python
"""Per-workflow sandbox resources: clamped translation + pool-key isolation."""

import pytest

from app.config import settings
from app.services.sandbox_policy import (
    resolve_sandbox_overrides,
    validate_sandbox_resources,
)


def test_empty_request_means_no_overrides():
    assert resolve_sandbox_overrides(None) == {}
    assert resolve_sandbox_overrides({}) == {}


def test_translation_to_docker_kwargs():
    out = resolve_sandbox_overrides({"memory_mb": 2048, "cpu": 2.0, "tmpfs_mb": 512})
    assert out["mem_limit"] == "2048m"
    assert out["nano_cpus"] == 2_000_000_000
    assert out["tmpfs"] == {"/tmp": "size=512m"}


def test_spawn_time_clamp_to_deployment_ceiling(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_max_memory_mb", 1024)
    monkeypatch.setattr(settings, "sandbox_max_cpu", 1.0)
    out = resolve_sandbox_overrides({"memory_mb": 999999, "cpu": 64})
    assert out["mem_limit"] == "1024m"
    assert out["nano_cpus"] == 1_000_000_000


def test_security_keys_are_never_translated():
    out = resolve_sandbox_overrides(
        {"memory_mb": 512, "network": "host", "cap_drop": [], "read_only": False}
    )
    assert set(out) == {"mem_limit"}  # unknown/forbidden keys silently ignored


def test_validate_rejects_bad_payloads(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_max_memory_mb", 1024)
    with pytest.raises(ValueError, match="memory_mb"):
        validate_sandbox_resources({"memory_mb": 4096})  # over ceiling → explicit error
    with pytest.raises(ValueError, match="unknown"):
        validate_sandbox_resources({"network": "host"})
    with pytest.raises(ValueError, match="cpu"):
        validate_sandbox_resources({"cpu": -1})
    assert validate_sandbox_resources({"memory_mb": 512}) == {"memory_mb": 512}


def test_pool_key_includes_resource_fingerprint():
    from app.services.sandbox_pool import overrides_key

    a = overrides_key({"mem_limit": "512m"})
    b = overrides_key({"mem_limit": "2048m"})
    assert a != b
    assert overrides_key({}) == overrides_key(None)
```

- [ ] **Step 2:** `uv run pytest apps/api/tests/test_sandbox_resources.py -q` → FAIL (imports).

- [ ] **Step 3: Config.** In `config.py`, after `sandbox_workflow_default` (Task 21) add:

```python
    # Ceilings for per-workflow sandbox resource requests
    # (Workflow.sandbox_resources). Requests above these are rejected at write
    # time and clamped again at container spawn. The security floor (cap_drop,
    # no-new-privileges, read-only rootfs, network) is never workflow-tunable.
    sandbox_max_memory_mb: int = 8192
    sandbox_max_cpu: float = 4.0
    sandbox_max_tmpfs_mb: int = 2048
```

- [ ] **Step 4: Policy helpers.** In `sandbox_policy.py` add:

```python
_RESOURCE_KEYS = ("memory_mb", "cpu", "tmpfs_mb")


def validate_sandbox_resources(payload: dict) -> dict:
    """Validate a user-supplied sandbox_resources payload (write-time, 422 path).

    Raises ValueError naming the offending key. Only resource keys are
    accepted — security-floor keys (network, capabilities, rootfs) are
    'unknown' here by design.
    """
    if not isinstance(payload, dict):
        raise ValueError("sandbox_resources must be an object")
    unknown = sorted(set(payload) - set(_RESOURCE_KEYS))
    if unknown:
        raise ValueError(f"sandbox_resources: unknown key(s) {unknown}")
    ceilings = {
        "memory_mb": settings.sandbox_max_memory_mb,
        "cpu": settings.sandbox_max_cpu,
        "tmpfs_mb": settings.sandbox_max_tmpfs_mb,
    }
    clean: dict = {}
    for key, value in payload.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"sandbox_resources: {key} must be a positive number")
        if value > ceilings[key]:
            raise ValueError(
                f"sandbox_resources: {key}={value} exceeds this deployment's "
                f"ceiling of {ceilings[key]}"
            )
        clean[key] = value
    return clean


def resolve_sandbox_overrides(requested: dict | None) -> dict:
    """Translate a workflow's sandbox_resources into docker spawn overrides.

    Spawn-time twin of validate_sandbox_resources: unknown keys are ignored
    and values are CLAMPED (not rejected) so a row written under an older,
    higher ceiling still runs. Returns {} when nothing is requested, so
    hardening_kwargs falls back to the global SANDBOX_* defaults.
    """
    if not isinstance(requested, dict) or not requested:
        return {}
    overrides: dict = {}
    mem = requested.get("memory_mb")
    if isinstance(mem, (int, float)) and not isinstance(mem, bool) and mem > 0:
        overrides["mem_limit"] = f"{int(min(mem, settings.sandbox_max_memory_mb))}m"
    cpu = requested.get("cpu")
    if isinstance(cpu, (int, float)) and not isinstance(cpu, bool) and cpu > 0:
        overrides["nano_cpus"] = int(
            min(float(cpu), settings.sandbox_max_cpu) * 1_000_000_000
        )
    tmpfs = requested.get("tmpfs_mb")
    if isinstance(tmpfs, (int, float)) and not isinstance(tmpfs, bool) and tmpfs > 0:
        overrides["tmpfs"] = {
            "/tmp": f"size={int(min(tmpfs, settings.sandbox_max_tmpfs_mb))}m"
        }
    return overrides
```

- [ ] **Step 5: Pool key + spawn.** In `sandbox_pool.py`:
  - Module-level helper:

```python
def overrides_key(overrides: dict | None) -> str:
    """Stable fingerprint of spawn overrides for the warm-pool key, so a
    high-memory workflow can never be handed a low-memory warm container."""
    if not overrides:
        return ""
    return json.dumps(overrides, sort_keys=True)
```

  - `SandboxWorker.spawn`: add kwarg `overrides: dict | None = None`; change the hardening line to `spawn_kwargs = hardening_kwargs(runtime=runtime, network=network, overrides=overrides)`.
  - `SandboxPool.dispatch`: add kwarg `spawn_overrides: dict | None = None`; compute `key = (org_id, env_id, overrides_key(spawn_overrides))` and pass `overrides=spawn_overrides` through `_acquire` → `SandboxWorker.spawn` (add the parameter to `_acquire` too).
  - The key tuple grows from 2 to 3 elements. Update the two annotations `tuple[str | None, str | None]` to `tuple[str | None, str | None, str]`, and check every `worker.key` consumer: `_handle_call_workflow` uses `self.key[1]` for the env id — index 1 is still the env id, unchanged. Run `grep -n "\.key\[" apps/api/app/services/sandbox_pool.py` and verify no other index use.

- [ ] **Step 6: Thread through executor.** `executors/base.py` `RunExecutionContext`: add `sandbox_spawn_overrides: dict | None`. `executors/sandbox.py` `execute`: pass `spawn_overrides=ctx.get("sandbox_spawn_overrides")` into `self._pool.dispatch(...)`. In `runner.py`: `_build_ctx` gains `sandbox_spawn_overrides: dict | None = None` param → key in the returned dict; `_PreparedRunContext` gains `sandbox_resources: dict | None` populated from the Workflow row in `_prepare_run_context` (next to Task 21's `execution_mode`); the sandbox branch of `_execute_run_impl` passes `sandbox_spawn_overrides=resolve_sandbox_overrides(prep.sandbox_resources)` into `_build_ctx` (other branches pass nothing — the TypedDict key defaults to None via `_build_ctx`'s default).

- [ ] **Step 7: Model + migration.** `models.py` class `Workflow`, next to `execution_mode`:

```python
    # Optional per-workflow sandbox resource requests: {"memory_mb", "cpu",
    # "tmpfs_mb"}. Validated by sandbox_policy.validate_sandbox_resources;
    # clamped to the sandbox_max_* ceilings at spawn.
    sandbox_resources: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

Migration `0083_workflow_sandbox_resources.py` (`down_revision = "0082_workflow_execution_mode"`): `op.add_column("workflows", sa.Column("sandbox_resources", sa.JSON(), nullable=True))` + drop in downgrade, full file mirroring 0082's structure.

- [ ] **Step 8: Surfaces.** REST (`routers/workflows.py` settings update, same spot as Task 21b's `execution_mode`): accept optional `sandbox_resources`; call `validate_sandbox_resources`, converting `ValueError` to HTTP 422 with the error text. MCP (`mcp/tools.py` `_update_workflow_settings` and `_create_workflow`): same field + validation (McpToolError on ValueError), and add to the two tools' parameter schemas: `"sandbox_resources": {"type": "object", "description": "Sandbox resource requests: memory_mb, cpu, tmpfs_mb — clamped to deployment ceilings"}`.

- [ ] **Step 9: UI.** In `WorkflowSettingsModal.tsx`, below Task 21c's isolation select, render when `executionMode === "sandboxed"` (reuse the modal's numeric-input field markup):

```tsx
<div className="settings-row-3col">
  <label>
    Memory (MB)
    <input type="number" min={128} value={resMemory ?? ""} placeholder="1024"
      onChange={(e) => setResMemory(e.target.value ? Number(e.target.value) : null)} />
  </label>
  <label>
    CPU cores
    <input type="number" min={0.25} step={0.25} value={resCpu ?? ""} placeholder="1"
      onChange={(e) => setResCpu(e.target.value ? Number(e.target.value) : null)} />
  </label>
  <label>
    Scratch /tmp (MB)
    <input type="number" min={64} value={resTmpfs ?? ""} placeholder="256"
      onChange={(e) => setResTmpfs(e.target.value ? Number(e.target.value) : null)} />
  </label>
</div>
<p className="muted">Blank = deployment default. Requests above the deployment ceiling are rejected.</p>
```

Save payload includes `sandbox_resources` only when at least one field is set (`{memory_mb: resMemory ?? undefined, ...}` with undefineds stripped); surface a 422 response through the modal's existing error display. Add `sandbox_resources?: { memory_mb?: number; cpu?: number; tmpfs_mb?: number } | null;` to the workflow type in `types.ts`. Test: set memory to 2048, save, assert payload contains `sandbox_resources: {memory_mb: 2048}`.

- [ ] **Step 10:** `uv run pytest apps/api/tests/test_sandbox_resources.py apps/api/tests -q -k "sandbox"` green; `cd apps/api && uv run alembic heads` → `0083_workflow_sandbox_resources (head)`; ruff clean; `cd apps/web && npm run typecheck && npm test -- --run` green. **Commit:** `feat(sandbox): per-workflow resource limits, ceiling-validated and pool-key isolated`.

---

# Area G — Security (8.5 → 10)

### Task 22: Truthful security model in README + SECURITY.md

**Files:**
- Modify: `README.md` (the `## ⚠️ Security model` section), `SECURITY.md`

- [ ] **Step 1:** Replace the entire README "Security model" section body (currently claims sandbox/multi-tenant execution is out of scope) with:

```markdown
## Security model

Noodle supports three execution postures — pick per deployment:

1. **Trusted single-tenant (default):** workflow authors are trusted; code
   nodes run in warm, per-environment worker subprocesses on the host at
   native speed. Correct for a team running its own instance.
2. **Sandboxed:** set `EXECUTION_SANDBOX=auto|required` and each run executes
   in a disposable hardened container — capabilities dropped, read-only root
   filesystem, CPU/memory/pids limits, a dedicated egress-isolated network,
   and gVisor/Kata when installed. Recommended whenever workflows execute
   AI-generated or otherwise untrusted code. One-command enable:
   `docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.sandbox.yml up`.
3. **Multi-tenant:** `MULTI_TENANCY_ENABLED=true` **requires** the sandbox
   (`EXECUTION_SANDBOX=required`) — the server refuses to boot otherwise.
   Tenant isolation combines per-org row-level security, org-scoped
   encryption keys, per-org quotas/fairness, and per-(org, environment)
   container pools.

Startup is fail-closed: unsafe combinations (default `SECRET_KEY` with auth
enabled, missing internal token in a split topology, a requested-but-bypassed
sandbox) abort boot rather than degrade silently. See [SECURITY.md](SECURITY.md).
```

- [ ] **Step 2:** In `SECURITY.md`, read the file; update any sentence repeating the "out of scope" claim to reference the three postures above (keep the vulnerability-reporting section untouched).

- [ ] **Step 3:** `grep -n "out of scope" README.md SECURITY.md` → no execution-isolation hits remain. **Commit:** `docs(security): security model matches shipped sandbox + multi-tenancy`.

---

### Task 23: Make bandit + mypy blocking for security-critical modules

**Files:**
- Modify: `.github/workflows/ci.yml` (~lines 155–170)

- [ ] **Step 1:** In the security job: delete the `continue-on-error: true` line under **mypy — security-critical modules** and rename it `(blocking)`. For **bandit**, split it: keep the broad advisory run as-is, and add above it a blocking run scoped to the security core:

```yaml
      - name: bandit — security-critical modules (blocking)
        run: >-
          uvx bandit -ll
          apps/api/app/security.py
          apps/api/app/tenancy.py
          apps/api/app/services/crypto.py
          apps/api/app/services/mcp_client.py
          apps/api/app/services/redaction.py
```

- [ ] **Step 2: Verify locally before committing** (a red CI gate is a plan failure):

```bash
uvx bandit -ll apps/api/app/security.py apps/api/app/tenancy.py apps/api/app/services/crypto.py apps/api/app/services/mcp_client.py apps/api/app/services/redaction.py
uvx mypy --ignore-missing-imports --follow-imports=skip apps/api/app/services/crypto.py apps/api/app/services/rate_limit.py apps/api/app/logging.py apps/api/app/security.py
```

If either reports findings, fix them (or add a targeted `# nosec BXXX — <reason>` / `# type: ignore[<code>] — <reason>` with a one-line justification) in the same commit. Do NOT widen scopes or exclusions to pass.

- [ ] **Step 3: Commit:** `ci(security): bandit + mypy blocking on security-critical modules`.

---

### Task 24: Webhook HMAC replay window

**Files:**
- Modify: `apps/api/app/services/triggers.py` (`_webhook_hmac_passes`, line ~328)
- Test: extend the existing webhook-auth test file (`grep -rln "_webhook_hmac_passes\|hmac_verification" apps/api/tests` — extend that file)

**Interfaces:**
- Produces: optional node params `hmac_timestamp_header` (default `""` = feature off — fully backward compatible) and `hmac_max_age_seconds` (default 300). When the header param is set, requests lacking the header or older/newer than the window are rejected.

- [ ] **Step 1: Failing tests** (mirror how the existing tests build `node_params`/headers for `_webhook_hmac_passes` — read them first, then add):

```python
def test_hmac_rejects_stale_timestamp(...):  # use the file's existing fixture args
    import time
    node_params = {
        "hmac_verification": "on", "hmac_header": "X-Signature",
        "hmac_timestamp_header": "X-Timestamp", "hmac_max_age_seconds": 300,
    }
    headers = _signed_headers(body, secret)  # reuse the file's signing helper
    headers["x-timestamp"] = str(int(time.time()) - 3600)
    assert _webhook_hmac_passes(node_params, resolved, headers, body) is False


def test_hmac_accepts_fresh_timestamp(...):
    ...same but timestamp = now → True...


def test_hmac_missing_timestamp_header_rejected_when_configured(...):
    ...no x-timestamp header → False...
```

- [ ] **Step 2:** FAIL.

- [ ] **Step 3: Implement.** In `_webhook_hmac_passes`, after the existing "feature on?" early return (`hmac_verification != "on"` check at ~line 339), add:

```python
    ts_header = str(node_params.get("hmac_timestamp_header") or "").lower()
    if ts_header:
        import time as _time

        max_age = float(node_params.get("hmac_max_age_seconds") or 300)
        raw_ts = (headers or {}).get(ts_header) or ""
        try:
            ts = float(raw_ts)
        except (TypeError, ValueError):
            return False
        if abs(_time.time() - ts) > max_age:
            return False
```

(Confirm the function's headers dict is lowercase-keyed like the rest of the module — it is (`lower_headers` convention); mirror whatever name the function actually uses.)

- [ ] **Step 4:** tests PASS; the pre-existing hmac tests still pass unchanged (default off). Ruff clean. **Commit:** `feat(security): optional timestamp replay window for webhook HMAC auth`.

---

# Area H — Deployment (8 → 10)

### Task 25: Backup, restore, and upgrade runbook

**Files:**
- Create: `docs/backup-restore.md`
- Modify: `README.md` (one link line in the deployment/docs list)

- [ ] **Step 1: Write `docs/backup-restore.md`** covering, with exact commands for the shipped compose stack: **What to back up** (postgres `pgdata`, `artifactdata`, `envdata` volumes; `.env` incl. `SECRET_KEY` — call out in bold that losing `SECRET_KEY` makes every stored credential permanently undecryptable); **Backup** (`docker compose exec postgres pg_dump -U noodle -Fc noodle > noodle-$(date +%F).dump` + `docker run --rm -v noodle_artifactdata:/data -v "$PWD:/backup" alpine tar czf /backup/artifacts-$(date +%F).tgz -C /data .`); **Restore** (fresh volumes, `pg_restore -U noodle -d noodle --clean`, tar extract, start API last); **Upgrade order** (back up → pull → `docker compose up -d api` — API runs `alembic upgrade head` on start → then worker/web; downgrade = restore backup, do not run `alembic downgrade` against production data); **S3 backends** (bucket versioning + lifecycle instead of the artifact volume tar).

- [ ] **Step 2:** Add to README's docs links: `- [Backup, restore & upgrades](docs/backup-restore.md)`.

- [ ] **Step 3: Commit:** `docs(deploy): backup/restore/upgrade runbook`.

---

### Task 26: CI validates compose + helm

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1:** Add a job (top-level, alongside the existing jobs — copy the indentation of the `web` job):

```yaml
  deploy-config:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: compose config — base
        run: docker compose -f deploy/docker-compose.yml config -q
        env:
          MINIO_ROOT_PASSWORD: ci-placeholder
          INTERNAL_API_TOKEN: ci-placeholder
          NOODLE_SECRET_KEY: ci-placeholder
      - name: compose config — sandbox overlay
        run: >-
          docker compose -f deploy/docker-compose.yml
          -f deploy/docker-compose.sandbox.yml config -q
        env:
          MINIO_ROOT_PASSWORD: ci-placeholder
          INTERNAL_API_TOKEN: ci-placeholder
          NOODLE_SECRET_KEY: ci-placeholder
      - name: helm lint
        run: helm lint deploy/helm/noodle
```

(GitHub ubuntu runners ship docker compose v2 and helm preinstalled — no setup steps needed.)

- [ ] **Step 2:** Validate YAML locally: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`. If helm/docker are available locally, run the three commands once and fix any lint findings in the chart (values defaults, missing icon warnings are acceptable; errors are not).

- [ ] **Step 3: Commit:** `ci(deploy): validate compose (base + sandbox overlay) and helm chart`.

---

# Area I — Architecture (9 → 10)

### Task 27: Extract checkpointing from runner.py

**Files:**
- Create: `apps/api/app/services/run_checkpoints.py`
- Modify: `apps/api/app/services/runner.py`
- Test: existing suites (`apps/api/tests -k checkpoint`) prove the move

- [ ] **Step 1: Mechanical move.** Create `run_checkpoints.py` with module docstring `"""Durable-execution checkpointing (extracted from runner.py — A2 follow-up)."""` and MOVE (cut, do not copy) from `runner.py`: `_MAX_CHECKPOINT_BYTES`, `_CheckpointDebouncer`, `_serialize_checkpoint_outputs`, `_save_checkpoint` — byte-identical bodies, including Task 11's `-> bool` change. Bring exactly the imports those need (`time`, `datetime/UTC`, `logging`, `update`, `Run`, `SessionLocal`, `serialize_value`, `_approx_json_length`, `truncate_serialized_value`). Module-level logger: `logger = logging.getLogger(__name__)`.

- [ ] **Step 2: Back-compat aliases** in `runner.py` (replacing the moved code, mirroring the existing `run_persistence` alias block at lines ~140–152):

```python
# A2 follow-up: checkpointing moved to run_checkpoints. Aliases keep this
# module's established surface (tests monkeypatch these names here).
from app.services import run_checkpoints
from app.services.run_checkpoints import (  # noqa: F401
    _MAX_CHECKPOINT_BYTES,
    _CheckpointDebouncer,
    _save_checkpoint,
    _serialize_checkpoint_outputs,
)
```

⚠️ Monkeypatch hazard: any test patching `runner._save_checkpoint` / `runner._MAX_CHECKPOINT_BYTES` now patches the alias, while internal callers would still resolve via… make internal call sites use the module-level name (`_save_checkpoint(...)` — resolves through this module's globals, so monkeypatching `runner._save_checkpoint` keeps working, exactly like the `SessionLocal` comment at runner.py:1498 relies on). Confirm Task 11's test (patches `runner_mod._MAX_CHECKPOINT_BYTES`) still passes — if it fails, repoint that test at `run_checkpoints._MAX_CHECKPOINT_BYTES`.

- [ ] **Step 3:** `uv run pytest apps/api/tests -q -k "checkpoint or durable or resume"` → green; `uv run pytest apps/api/tests -q` (full API suite once) → green; ruff clean on both files.

- [ ] **Step 4: Commit:** `refactor(arch): extract checkpointing from runner.py into run_checkpoints.py`.

---

### Task 28: ADR — run execution architecture

**Files:**
- Create: `docs/adr/0001-run-execution-architecture.md`

- [ ] **Step 1: Write the ADR** (status: Accepted; date: today). Sections and required content:
  - **Context:** one visual editor + API control plane; runs must survive process death; three isolation postures (trusted subprocess / hardened container / remote pool).
  - **Decision:** (1) durable DB-backed queue (`RunQueueEntry`, SKIP LOCKED leasing) as the only dispatch path — no in-memory fast path; (2) `RunExecutor` protocol seam (`executors/base.py`) with local/sandbox/remote implementations owning zero persistence; (3) `runner._execute_run` as the single bookkeeping chokepoint (context prep, events, terminal writes); (4) engine hosts state via explicit kwargs/`RuntimeContext`, ContextVars only for per-node mutable state; (5) checkpoint-column resume rather than event sourcing, with NodeRun rows as the fallback reconstruction source.
  - **Consequences:** executors are swappable without touching bookkeeping; every trigger path funnels through one admission gate (quotas, single-flight, isolation policy); the checkpoint cap (1 MiB) trades resume completeness for bounded row size (truncation now surfaced as a run event); split topology requires Redis (events) + Postgres (leasing), enforced by `dispatch_topology_errors()`.
  - **Alternatives rejected:** full event sourcing (storage/replay cost, no operational win today); Redis-primary queue (DB is the correctness baseline; Redis reserved as optimisation); per-executor persistence (would fragment the terminal-state invariant).
- [ ] **Step 2: Commit:** `docs(arch): ADR-0001 run execution architecture`.

---

## Final Gate (after Task 28)

- [ ] `uv run pytest -q --timeout=120` → full backend suite green (baseline: 2,903 passed / 89 skipped — expect ~40 more).
- [ ] `cd apps/web && npm run typecheck && npm test -- --run && npm run build` → green.
- [ ] `uv run ruff check .` → "All checks passed!".
- [ ] `cd apps/api && uv run alembic heads` → exactly one head: `0083_workflow_sandbox_resources`.
- [ ] If Docker is available: run the soak baseline (`scripts/soak_test.py --runs 50`) against the compose stack and paste the PASS output into the PR description.
- [ ] `/code-review` (medium) on the branch diff before opening the PR.

## Self-Review Notes

- **Spec coverage:** Artifacts → Tasks 1–4; MCP → 5–9; Engine → 10–12; Queue → 13–14; Workers → 15–16; Sandbox → 17–21; Security → 22–24; Deployment → 18, 25–26; Architecture → 15, 27–28. Pre-rename constraint enforced globally.
- **Deliberately excluded (YAGNI, per audit):** content-addressed artifacts, GCS/Azure backends, event sourcing, Redis queue backend, MCP marketplace, kubernetes_job provider, EditorPage decomposition (rename-adjacent; schedule post-rename).
- **Migration chain:** 0080 (Task 1) → 0081 (Task 7) → 0082 (Task 21) → 0083 (Task 21d) — execute those tasks in that order; all other tasks are order-independent within their area except Task 27 after Task 11, Task 20 after 19, Task 18 before 26, Task 9 after 7, and 21 → 21b → 21c → 21d (21c also wants 19 for the availability hint; 21d builds on 21's `_PreparedRunContext` and 21c's modal section).
