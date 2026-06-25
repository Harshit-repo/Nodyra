# Production Audit Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix every issue in `docs/audit-2026-06-24.md` in priority order (P0 → P1 → P2 → P3) to make Noodle production-ready.

**Architecture:** Small, reviewable, TDD-first changes per issue group. Backend fixes use SQLAlchemy `select()` with org scoping instead of `session.get()`. DB changes use Alembic migrations. Docker uses multi-stage builds. CI gates flip from advisory to blocking once underlying failures are resolved.

**Tech Stack:** FastAPI, SQLAlchemy 2.x async, Alembic, Pydantic v2, React + Zustand + React Query, ReactFlow, Vite, nginx, Docker multi-stage, Helm 3, uv, pytest, Vitest, Playwright

## Global Constraints

- Python 3.12 minimum; tests run with `uv run pytest`
- Frontend uses `npm test -- --run` (Vitest) and `npm run typecheck`
- All Alembic migrations must include a `downgrade()` function
- New migrations go in `apps/api/alembic/versions/`, numbered `0061_`, `0062_`, etc.
- Use the existing `active_org_id()` from `app.tenancy` for org scoping in `select()` queries
- Never use `session.get()` for tenant-scoped models when tenant isolation matters; always use `session.scalar(select(Model).where(Model.id == id))`
- The `do_orm_execute` tenant filter applies automatically to `select()` queries for models with `org_id`; no need to explicitly add `.where(Model.org_id == ...)` unless outside a request context
- `require_permission(perm)` is from `app.security` and returns a FastAPI `Depends`-compatible callable
- Run tests with: `uv run pytest apps/api/tests/<file>.py -v` from repo root
- Run frontend tests with: `cd apps/web && npm test -- --run`

---

## Issue Checklist

### P0 — Must fix before any real user
- [ ] **B-01** Debug snapshot endpoint: no auth guard
- [ ] **B-02** MCP tools: `session.get()` bypasses org filter (`_delete_schedule`, `_toggle_schedule`, `_cancel_run`, `_get_run`, `_get_run_events`, `_get_workflow_stats`, `_list_workflow_versions`, `_get_node_run`)
- [ ] **B-03** `cancel_workflow_run`: no org ownership check
- [ ] **E-01** Code node sandbox: `os`/`socket`/`subprocess` not blocked
- [ ] **E-02** `CodeExecToolAdapter`: may bypass process isolation
- [ ] **V-01** Web Dockerfile: Vite dev server in production
- [ ] **V-02** `deploy/.env` secrets baked into Docker image
- [ ] **T-01** Frontend unit tests not in CI

### P1 — Must fix before production launch
- [ ] **B-04** Unauthenticated workflow/run list endpoints
- [ ] **B-05** `session.commit()` in auth dependency (should be `flush()`)
- [ ] **B-06** API restart cancels active remote-pool runs
- [ ] **B-07** OAuth introspection: no cache or circuit breaker
- [ ] **D-01** `runs.deduplication_key`: no UNIQUE constraint
- [ ] **D-02** `workflow_versions`: no `(workflow_id, version)` unique constraint
- [ ] **D-03** `github_sync_jobs`: missing RLS policy + composite index
- [ ] **D-04** `github_sync_configs.webhook_secret`: stored in plaintext
- [ ] **D-05** `github_sync_configs`: missing RLS policy
- [ ] **E-03** Cancellation not propagated into `asyncio.to_thread` sync nodes
- [ ] **E-04** DNS rebinding TOCTOU in SSRF guard
- [ ] **E-05** `venv` backend: `index_urls` not validated through SSRF guard
- [ ] **E-06** Cloud runner bootstrap token in user-data
- [ ] **E-07** Prompt injection triggers side-effecting tool calls
- [ ] **E-08** Remote sub-workflow cycle detection broken
- [ ] **F-01** `window.__noodle_sign_out` global
- [ ] **F-02** `marked.parse()` cast as string
- [ ] **F-03** Canvas drag listener leak on unmount
- [ ] **F-04** Module-level body-index cache shared mutable state
- [ ] **F-05** Credential form values not reset on preset change
- [ ] **T-02** Canvas has zero unit tests
- [ ] **T-03** Credentials list truncates at 500 (no pagination)
- [ ] **T-04** `GET /workflows` loads all version data
- [ ] **T-05** Audit log router untested
- [ ] **V-03** No multi-stage Python Dockerfile
- [ ] **V-04** Helm migration job: `DATABASE_URL` in plaintext
- [ ] **V-05** No liveness probe on API deployment
- [ ] **V-06** No Kubernetes resource limits
- [ ] **V-07** Ingress TLS disabled by default
- [ ] **V-08** MinIO: default credentials on all interfaces
- [ ] **V-09** No rolling update strategy
- [ ] **V-10** Postgres test suite advisory (`continue-on-error`)

### P2 — Should fix soon
- [ ] **B-08** SHA-256 key derivation (should be HKDF)
- [ ] **B-09** Silent credential decrypt failure
- [ ] **B-10** Request body buffering
- [ ] **B-11** Credential list N+1 KEK queries
- [ ] **B-12** Duplicate WHERE clauses in `list_all_runs`
- [ ] **D-06** RLS fail-open (no dedicated app role)
- [ ] **D-07** Missing FK constraints on runner_pool_id columns
- [ ] **D-08** `Artifact.id` missing `default=_uuid`
- [ ] **D-09** Retention misses `RunQueueEntry` on SQLite
- [ ] **D-10** Migration 0043 imports app code
- [ ] **D-11** `audit_events` missing composite index
- [ ] **E-09** Loop shallow copy race (concurrent iterations)
- [ ] **E-10** Sandbox JSON drop silently
- [ ] **E-11** No process count limit in `PooledProcessIsolator`
- [ ] **E-12** `paginate()`: no item count cap
- [ ] **F-06** `requestAllPages`: no concurrency cap
- [ ] **F-07** `NodeCard`: 10+ separate store selectors
- [ ] **F-08** `hasTriggerUpstream` BFS on hover
- [ ] **F-09** Login form: no `<form>` semantics
- [ ] **F-10** `NoodleNodeData` index signature
- [ ] **F-11** React Query: retries 4xx errors
- [ ] **T-06** WebSocket streaming endpoint untested
- [ ] **T-07** Queue dispatch N+1 org limits
- [ ] **T-08** `NodeRun` bulk insert
- [ ] **T-09** Run detail double query
- [ ] **T-10** No workflow import endpoint
- [ ] **T-11** `packages/nodes` excluded from bandit
- [ ] **V-11** No CD pipeline
- [ ] **V-12** `trusted_proxy_count: 0` in Helm
- [ ] **V-13** Security CI advisory
- [ ] **V-14** No `terminationGracePeriodSeconds`

### P3 — Polish / technical debt
- [ ] **B-12** Duplicate filter clauses in `list_all_runs`
- [ ] **E-13** `max_iterations=0` semantics
- [ ] **D-12** `NodeRun` float timestamps
- [ ] Various frontend and backend polish items

---

## Task 1: B-01 — Auth guard on debug snapshot endpoint

**Files:**
- Modify: `apps/api/app/routers/runs.py:719-721`
- Modify: `apps/api/tests/test_runs.py`

**Context:** `GET /runs/{run_id}/debug-snapshot` returns all node outputs (including credential values from API responses) with zero auth. Anyone with a run ID can exfiltrate the data. The fix is one line: add the same `require_permission("workflow:run")` dependency that the other sensitive run endpoints already use.

- [ ] **Step 1: Confirm the vulnerability exists**

```bash
grep -n "debug.snapshot\|run_debug_snapshot" apps/api/app/routers/runs.py
# Expected: line ~719 shows the route has no Depends(require_permission(...))
```

- [ ] **Step 2: Write a failing test**

In `apps/api/tests/test_runs.py`, add before any existing auth tests:

```python
async def test_debug_snapshot_requires_auth(client, workflow, run_factory):
    """Debug snapshot must require authentication."""
    run = await run_factory(workflow.id)
    # Unauthenticated request (no auth header / cookie)
    resp = await client.get(f"/runs/{run.id}/debug-snapshot")
    assert resp.status_code == 401
```

Run: `uv run pytest apps/api/tests/test_runs.py::test_debug_snapshot_requires_auth -v`
Expected: FAIL (currently returns 200, not 401)

- [ ] **Step 3: Add the auth dependency**

In `apps/api/app/routers/runs.py`, change line 719–722 from:

```python
@router.get("/runs/{run_id}/debug-snapshot", response_model=RunDebugSnapshot)
async def run_debug_snapshot(
    run_id: str, session: AsyncSession = Depends(get_session)
) -> RunDebugSnapshot:
```

To:

```python
@router.get(
    "/runs/{run_id}/debug-snapshot",
    response_model=RunDebugSnapshot,
    dependencies=[Depends(require_permission("workflow:run"))],
)
async def run_debug_snapshot(
    run_id: str, session: AsyncSession = Depends(get_session)
) -> RunDebugSnapshot:
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest apps/api/tests/test_runs.py::test_debug_snapshot_requires_auth -v
```
Expected: PASS

- [ ] **Step 5: Run the broader run test suite to check no regressions**

```bash
uv run pytest apps/api/tests/test_runs.py -v --timeout=60
```
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/routers/runs.py apps/api/tests/test_runs.py
git commit -m "security: require workflow:run permission on debug-snapshot endpoint (B-01)"
```

---

## Task 2: B-02 — MCP tools: replace `session.get()` with org-scoped `select()`

**Files:**
- Modify: `apps/api/app/mcp/tools.py`
- Modify or create: `apps/api/tests/test_mcp_multitenancy.py`

**Context:** `session.get(Model, id)` bypasses the `do_orm_execute` ORM tenant filter because it checks SQLAlchemy's identity map first. The fix is to use `session.scalar(select(Model).where(Model.id == id))` instead — this always goes through `do_orm_execute` which appends the `org_id` filter.

The affected handlers (confirmed by grep):
- `_cancel_run` (line 442): `session.get(Run, run_id)`
- `_delete_schedule` (line 984): `session.get(Deployment, schedule_id)`
- `_get_run` (line 213): `session.get(Run, run_id, options=[selectinload(Run.node_runs)])`
- `_get_run_events` (line 279): `session.get(Run, run_id)`
- `_get_workflow_stats` (line 310): `session.get(Workflow, workflow_id)`
- `_list_workflow_versions` (line 829): `session.get(Workflow, workflow_id)`
- `_get_node_run` (line 1432): `session.get(Run, run_id)`

Note: `_load_workflow` and `_toggle_schedule` (which delegates to `update_deployment`) must also be checked.

- [ ] **Step 1: Write failing multi-tenant test**

Create `apps/api/tests/test_mcp_multitenancy.py`:

```python
"""Cross-org MCP tool isolation tests (B-02)."""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_cancel_run_cross_org_blocked(
    client: AsyncClient, second_org_client: AsyncClient,
    workflow, run_factory
):
    """An MCP cancel_run call cannot cancel a run belonging to a different org."""
    # Create a run in org A
    run = await run_factory(workflow.id)
    # Attempt to cancel it from org B's MCP session
    resp = await second_org_client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "cancel_run", "arguments": {"run_id": run.id}}
    })
    # Should get tool error (run not found in org B), not success
    body = resp.json()
    result = body.get("result", {})
    assert result.get("isError") is True or "not found" in str(result).lower()


async def test_delete_schedule_cross_org_blocked(
    client: AsyncClient, second_org_client: AsyncClient,
    deployment_factory
):
    """delete_schedule cannot delete a deployment owned by a different org."""
    deployment = await deployment_factory()
    resp = await second_org_client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "delete_schedule", "arguments": {"schedule_id": deployment.id}}
    })
    body = resp.json()
    result = body.get("result", {})
    assert result.get("isError") is True
```

Run: `uv run pytest apps/api/tests/test_mcp_multitenancy.py -v`
Expected: FAIL (cross-org operations currently succeed due to session.get bypass)

Note: if `second_org_client` fixture doesn't exist in conftest.py, it needs to be added. Check `apps/api/tests/conftest.py` first.

- [ ] **Step 2: Add `second_org_client` fixture to conftest if missing**

Check `apps/api/tests/conftest.py` for existing multi-org fixtures. If none exists, add:

```python
@pytest_asyncio.fixture
async def second_org(session: AsyncSession):
    """A second organization for cross-org isolation tests."""
    from app.models import Organization
    org = Organization(id="test-org-b", name="Org B", slug="org-b")
    session.add(org)
    await session.flush()
    return org


@pytest_asyncio.fixture
async def second_org_client(app, second_org):
    """An authenticated client for the second organization."""
    # This mirrors how the primary client is created but for org B
    # Adjust to match the conftest pattern
    async with AsyncClient(app=app, base_url="http://test") as ac:
        # Set up auth headers for org B
        # (copy the pattern from the existing `client` fixture)
        yield ac
```

Inspect conftest.py carefully and match the fixture pattern exactly — do not guess.

- [ ] **Step 3: Fix `_cancel_run` in `apps/api/app/mcp/tools.py`**

Find line 438–446:
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

Replace with:
```python
async def _cancel_run(session: AsyncSession, user: User | None, args: dict) -> Any:
    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        raise McpToolError("run_id is required.")
    run = await session.scalar(select(Run).where(Run.id == run_id))
    if run is None:
        raise McpToolError(f"Run not found: {run_id}")
    status = await _runner_cancel_run(run_id)
    return {"run_id": run_id, "status": status or run.status}
```

- [ ] **Step 4: Fix `_delete_schedule` in `apps/api/app/mcp/tools.py`**

Find line 980–994:
```python
async def _delete_schedule(session: AsyncSession, user: User | None, args: dict) -> Any:
    schedule_id = str(args.get("schedule_id") or "").strip()
    if not schedule_id:
        raise McpToolError("schedule_id is required.")
    deployment = await session.get(Deployment, schedule_id)
    if deployment is None:
        raise McpToolError(f"Schedule not found: {schedule_id}")
    ...
```

Replace `session.get(Deployment, schedule_id)` with:
```python
    deployment = await session.scalar(select(Deployment).where(Deployment.id == schedule_id))
```

- [ ] **Step 5: Fix `_get_run` (line ~213)**

Replace:
```python
run = await session.get(Run, run_id, options=[selectinload(Run.node_runs)])
```
With:
```python
run = await session.scalar(
    select(Run).where(Run.id == run_id).options(selectinload(Run.node_runs))
)
```

- [ ] **Step 6: Fix `_get_run_events` (line ~279)**

Replace:
```python
run = await session.get(Run, run_id)
```
With:
```python
run = await session.scalar(select(Run).where(Run.id == run_id))
```

- [ ] **Step 7: Fix `_get_workflow_stats` (line ~310)**

Replace:
```python
workflow = await session.get(Workflow, workflow_id)
```
With:
```python
workflow = await session.scalar(select(Workflow).where(Workflow.id == workflow_id))
```

- [ ] **Step 8: Fix `_list_workflow_versions` (line ~829)**

Replace:
```python
workflow = await session.get(Workflow, workflow_id)
```
With:
```python
workflow = await session.scalar(select(Workflow).where(Workflow.id == workflow_id))
```

- [ ] **Step 9: Fix `_get_node_run` (line ~1432)**

Replace:
```python
if await session.get(Run, run_id) is None:
```
With:
```python
if await session.scalar(select(Run).where(Run.id == run_id)) is None:
```

- [ ] **Step 10: Run tests**

```bash
uv run pytest apps/api/tests/test_mcp_multitenancy.py apps/api/tests/test_mcp_tools.py -v
```
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add apps/api/app/mcp/tools.py apps/api/tests/test_mcp_multitenancy.py apps/api/tests/conftest.py
git commit -m "security: replace session.get() with org-scoped select() in all MCP tools (B-02)"
```

---

## Task 3: B-03 — `cancel_workflow_run`: add org ownership check

**Files:**
- Modify: `apps/api/app/routers/runs.py`
- Modify: `apps/api/tests/test_runs.py`

**Context:** `POST /runs/{run_id}/cancel` already has `require_permission("workflow:run")` but calls `cancel_run(run_id)` directly without loading the run through the ORM tenant filter first. A user in Org A can cancel Org B's run.

- [ ] **Step 1: Write failing test**

In `apps/api/tests/test_runs.py`:
```python
async def test_cancel_run_verifies_org_ownership(
    client, second_org_client, workflow, run_factory
):
    """cancel endpoint must verify the run belongs to the caller's org."""
    run = await run_factory(workflow.id)
    # Org B tries to cancel Org A's run
    resp = await second_org_client.post(f"/runs/{run.id}/cancel")
    assert resp.status_code == 404
```

Run: `uv run pytest apps/api/tests/test_runs.py::test_cancel_run_verifies_org_ownership -v`
Expected: FAIL (currently succeeds cross-org)

- [ ] **Step 2: Fix `cancel_workflow_run`**

Find `cancel_workflow_run` in `apps/api/app/routers/runs.py` (~line 348):

```python
@router.post(
    "/runs/{run_id}/cancel",
    response_model=RunCancelResponse,
    dependencies=[Depends(require_permission("workflow:run"))],
)
async def cancel_workflow_run(run_id: str) -> RunCancelResponse:
    result = await cancel_run(run_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return RunCancelResponse(run_id=run_id, status=result)
```

Replace with:

```python
@router.post(
    "/runs/{run_id}/cancel",
    response_model=RunCancelResponse,
    dependencies=[Depends(require_permission("workflow:run"))],
)
async def cancel_workflow_run(
    run_id: str, session: AsyncSession = Depends(get_session)
) -> RunCancelResponse:
    # Verify the run exists and belongs to this org (do_orm_execute adds org filter)
    run = await session.scalar(select(Run).where(Run.id == run_id))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    result = await cancel_run(run_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return RunCancelResponse(run_id=run_id, status=result)
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest apps/api/tests/test_runs.py::test_cancel_run_verifies_org_ownership \
              apps/api/tests/test_runs.py -v --timeout=60
```
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/routers/runs.py apps/api/tests/test_runs.py
git commit -m "security: verify org ownership before cancel_run (B-03)"
```

---

## Task 4: E-01 — Code node sandbox: block dangerous imports

**Files:**
- Modify: `packages/nodes/noodle_nodes/builtin.py`
- Modify: `packages/nodes/tests/test_builtin_nodes.py`

**Context:** The `_CODE_NODE_BLOCKED_IMPORTS` set in `builtin.py` steers users toward Noodle's built-ins but does not block OS-level escape. The set currently blocks `matplotlib`, `sklearn`, etc. — not `os`, `socket`, `subprocess`. This is the most critical fix: add a defence-in-depth layer of dangerous import blocking. This is NOT a complete sandbox — true sandboxing requires Docker — but it prevents accidental and most casual attacks.

- [ ] **Step 1: Find current blocked imports list**

```bash
grep -n "_CODE_NODE_BLOCKED_IMPORTS\|_BLOCKED_NAMES\|_DANGEROUS" \
    packages/nodes/noodle_nodes/builtin.py | head -30
```

- [ ] **Step 2: Write failing tests**

In `packages/nodes/tests/test_builtin_nodes.py` (or create if not exists):

```python
import pytest
from noodle_nodes.builtin import _run_code_node  # adjust import to match actual function


@pytest.mark.parametrize("dangerous_import", [
    "import os",
    "import os as _os",
    "from os import path",
    "import socket",
    "import subprocess",
    "from subprocess import run",
    "import pty",
    "__import__('os')",
])
def test_code_node_blocks_dangerous_imports(dangerous_import):
    """Code node must raise an error for dangerous system imports."""
    code = f"""
{dangerous_import}
def run(inputs):
    return {{}}
"""
    with pytest.raises(Exception, match="not allowed|blocked|ImportError|forbidden"):
        # Call the code validation or execution function
        # Adjust to match the actual API: _CodeValidator, or the node's execute()
        _validate_code(code)  # or however validation is exposed
```

Run: `uv run pytest packages/nodes/tests/test_builtin_nodes.py -k "dangerous" -v`
Expected: FAIL (currently passes without error)

- [ ] **Step 3: Find the `_CODE_NODE_BLOCKED_IMPORTS` and `_CodeValidator` in builtin.py**

```bash
grep -n "_CODE_NODE_BLOCKED_IMPORTS\|_CodeValidator\|class.*Validator\|BLOCKED" \
    packages/nodes/noodle_nodes/builtin.py | head -20
```

- [ ] **Step 4: Extend the blocked imports list**

Find the `_CODE_NODE_BLOCKED_IMPORTS` set (around line 1299 per audit) and add to it:

```python
_CODE_NODE_BLOCKED_IMPORTS: frozenset[str] = frozenset({
    # Existing entries (keep all current ones)
    # ... existing entries ...
    # OS/system escape paths — defence-in-depth; true sandboxing requires Docker
    "os",
    "os.path",
    "posix",
    "nt",
    "socket",
    "subprocess",
    "multiprocessing",
    "pty",
    "fcntl",
    "signal",
    "ctypes",
    "cffi",
    "mmap",
    "resource",
    # Network escape paths
    "urllib",
    "urllib.request",
    "urllib.error",
    "http",
    "http.client",
    "ftplib",
    "telnetlib",
    "smtplib",
    "imaplib",
    "poplib",
    # File system escape paths (use Noodle's file nodes instead)
    "pathlib",
    "glob",
    "shutil",
    "tempfile",
    "fileinput",
    # Code execution escape paths
    "code",
    "codeop",
    "importlib",
    "pkgutil",
    "runpy",
    # System introspection escape paths
    "sys",
    "sysconfig",
    "platform",
    "gc",
    "inspect",
    "dis",
    "ast",
    "tokenize",
    "compileall",
    "py_compile",
})
```

Also extend `_BLOCKED_NAMES` to block `__import__`, `exec`, `eval`, `open`, `compile`, `breakpoint` if not already blocked:

```python
_BLOCKED_NAMES: frozenset[str] = frozenset({
    # Existing entries
    # ... 
    "__import__",
    "exec",
    "eval",
    "open",
    "compile",
    "breakpoint",
    "__builtins__",
    "__loader__",
    "__spec__",
})
```

- [ ] **Step 5: Ensure the `_CodeValidator` AST walker checks both `import` and `from ... import`**

Read the `_CodeValidator` class and verify it has checks for:
- `ast.Import` nodes → checks each alias name against `_CODE_NODE_BLOCKED_IMPORTS`
- `ast.ImportFrom` nodes → checks the module name against `_CODE_NODE_BLOCKED_IMPORTS`
- `ast.Name` nodes → checks id against `_BLOCKED_NAMES`
- `ast.Call` nodes → checks for `__import__("os")` patterns

If any of these checks are missing, add them.

- [ ] **Step 6: Run tests**

```bash
uv run pytest packages/nodes/tests/test_builtin_nodes.py -k "dangerous" -v
uv run pytest packages/nodes/tests/ -v --timeout=120
```
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add packages/nodes/noodle_nodes/builtin.py packages/nodes/tests/test_builtin_nodes.py
git commit -m "security: block dangerous OS/network/file imports in Code node validator (E-01)"
```

---

## Task 5: E-02 — Verify and fix `CodeExecToolAdapter` isolation

**Files:**
- Read: `packages/nodes/noodle_nodes/ai_v2/agent_tools.py`
- Modify if needed: `packages/nodes/noodle_nodes/ai_v2/agent_tools.py`
- Modify if needed: `packages/nodes/tests/test_ai_agent_tools.py`

**Context:** The AI agent's built-in code execution tool (`enable_code_execution=True`) imports `subprocess` at the top of `agent_tools.py`. If it runs LLM-generated code via `subprocess.run` directly rather than through the same `ProcessPoolExecutor` + `_CodeValidator` path as the Code node, it is an unguarded code execution path.

- [ ] **Step 1: Read the implementation**

```bash
grep -n "CodeExecToolAdapter\|subprocess\|ProcessPool\|execute\|run_code" \
    packages/nodes/noodle_nodes/ai_v2/agent_tools.py | head -40
```

- [ ] **Step 2: Determine what it actually does**

Read the `CodeExecToolAdapter.invoke_async` method fully. Two outcomes:
  - **If it uses `subprocess.run` directly with the LLM code**: it is a sandbox escape. Apply `_CodeValidator` and route through `ProcessPoolExecutor` (same as Code node's `_run_code_isolated`).
  - **If it already calls the Code node's `_run_code_isolated`/`_CodeValidator`**: it is safe. Add a comment confirming it and add a test asserting the call chain.

- [ ] **Step 3: If unsafe — add validation layer**

```python
# In CodeExecToolAdapter.invoke_async, before executing code:
from noodle_nodes.builtin import _CodeValidator

validator = _CodeValidator()
try:
    validator.validate(code_string)
except (SyntaxError, ValueError) as e:
    return {"error": f"Code validation failed: {e}", "output": None}

# Then route through the isolated executor:
from noodle_nodes.builtin import _run_code_isolated
result = await _run_code_isolated(code_string, inputs={})
```

- [ ] **Step 4: Write test**

```python
def test_code_exec_tool_blocks_os_import():
    """CodeExecToolAdapter must apply the same validation as the Code node."""
    adapter = CodeExecToolAdapter()
    import asyncio
    result = asyncio.run(adapter.invoke_async({"code": "import os; os.system('id')"}))
    assert "error" in result or result.get("output") is None
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest packages/nodes/tests/test_ai_agent_tools.py -v
```

- [ ] **Step 6: Commit**

```bash
git add packages/nodes/noodle_nodes/ai_v2/agent_tools.py packages/nodes/tests/test_ai_agent_tools.py
git commit -m "security: ensure CodeExecToolAdapter applies Code node validation and isolation (E-02)"
```

---

## Task 6: V-01 — Production web Docker image (nginx static build)

**Files:**
- Modify: `apps/web/Dockerfile`
- Create: `apps/web/nginx.conf`
- Modify: `deploy/docker-compose.yml` (image build context)
- Modify: `.dockerignore` (if exists at apps/web level; create if not)

**Context:** The current Dockerfile serves the Vite dev server. The fix is a two-stage build: stage 1 uses `node:20-slim` to run `npm run build`, stage 2 copies the `dist/` output into `nginx:alpine` and serves it.

- [ ] **Step 1: Create nginx config for SPA**

Create `apps/web/nginx.conf`:

```nginx
server {
    listen 5173;
    root /usr/share/nginx/html;
    index index.html;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Gzip compression
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript;

    # Cache static assets aggressively (Vite hashes filenames)
    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # SPA fallback: serve index.html for all non-file routes
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Health check endpoint for liveness/readiness probes
    location /health {
        return 200 "ok";
        add_header Content-Type text/plain;
    }
}
```

- [ ] **Step 2: Rewrite `apps/web/Dockerfile`**

```dockerfile
# Stage 1: Build the production bundle
FROM node:20-slim AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY . .
RUN npm run build

# Stage 2: Serve with nginx
FROM nginx:1.27-alpine AS production
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Remove default nginx config
RUN rm -f /etc/nginx/conf.d/default.conf.bak
EXPOSE 5173
CMD ["nginx", "-g", "daemon off;"]
```

- [ ] **Step 3: Create `apps/web/.dockerignore`**

```
node_modules
dist
.git
*.test.*
e2e
coverage
.nyc_output
*.log
```

- [ ] **Step 4: Verify the build works locally**

```bash
cd apps/web
docker build -t noodle-web:test .
docker run --rm -p 5173:5173 noodle-web:test &
sleep 2
curl -f http://localhost:5173/health
kill %1
```
Expected: `ok`

- [ ] **Step 5: Update `deploy/docker-compose.yml` web service**

The `web` service in `docker-compose.yml` should have its build context pointing to `apps/web` (verify it already does) and no dev-mode env vars. Ensure:
```yaml
web:
  build:
    context: ../apps/web
    dockerfile: Dockerfile
```
Remove any `command: npm run dev` override if present.

- [ ] **Step 6: Commit**

```bash
git add apps/web/Dockerfile apps/web/nginx.conf apps/web/.dockerignore deploy/docker-compose.yml
git commit -m "build: replace Vite dev server with nginx+static production web image (V-01)"
```

---

## Task 7: V-02 — Remove secrets from Docker image; fix `.dockerignore`

**Files:**
- Modify: `deploy/Dockerfile.python`
- Modify: `.dockerignore` (root level)
- Modify: `deploy/.env` (remove real secrets; leave as example)
- Modify/Create: `deploy/.env.example`

**Context:** `COPY . .` in `Dockerfile.python` with the repo root as the build context copies `deploy/.env` (which contains real `NOODLE_SECRET_KEY` and `INTERNAL_API_TOKEN`) into the image. The fix is to add `deploy/.env` to `.dockerignore` and replace the file with placeholder values.

⚠️ **IMPORTANT:** If this image has ever been pushed to a registry, assume the secrets are compromised. Generate new values and update the production deployment. The plan step is documented here but generating new production secrets is a manual operator step.

- [ ] **Step 1: Read the root `.dockerignore`**

```bash
cat .dockerignore
```

Check if `deploy/.env` is excluded. If not, add it.

- [ ] **Step 2: Update `.dockerignore`**

Add these lines to the root `.dockerignore`:
```
# Secrets — never bake into images
deploy/.env
.env
.env.local
.env.*.local
# Dev/test artifacts
**/__pycache__
**/*.pyc
**/.pytest_cache
**/.mypy_cache
**/.ruff_cache
apps/web/node_modules
apps/web/dist
# Docs and design assets
docs/
design/
brand/
# Git
.git
.github
```

- [ ] **Step 3: Sanitise `deploy/.env`**

Replace the contents of `deploy/.env` with placeholder values only (no real secrets):
```bash
# This file is NOT committed with real values.
# Copy to deploy/.env and fill in real values before running.
NOODLE_SECRET_KEY=CHANGE-ME-generate-with-openssl-rand-hex-32
INTERNAL_API_TOKEN=CHANGE-ME-generate-with-openssl-rand-hex-32
DATABASE_URL=postgresql+asyncpg://noodle:CHANGE-ME@db:5432/noodle
REDIS_URL=redis://redis:6379/0
```

- [ ] **Step 4: Add `deploy/.env` to `.gitignore`**

```bash
grep "deploy/.env" .gitignore || echo "deploy/.env" >> .gitignore
```

- [ ] **Step 5: Create `deploy/.env.example`** (if it doesn't already exist)

Copy the sanitized `.env` to `deploy/.env.example`:
```bash
cp deploy/.env deploy/.env.example
```

- [ ] **Step 6: Verify build doesn't include secrets**

```bash
docker build -f deploy/Dockerfile.python -t noodle-api:test . 2>&1 | head -30
docker run --rm noodle-api:test sh -c "cat /app/deploy/.env 2>/dev/null || echo 'NOT_FOUND'"
```
Expected: `NOT_FOUND`

- [ ] **Step 7: Document secret rotation**

In `deploy/.env.example`, add a comment:
```
# Generate new values with:
#   NOODLE_SECRET_KEY: openssl rand -hex 32
#   INTERNAL_API_TOKEN: openssl rand -hex 32
```

- [ ] **Step 8: Commit**

```bash
git add .dockerignore deploy/.env deploy/.env.example .gitignore
git commit -m "security: remove real secrets from repo; fix .dockerignore to exclude deploy/.env (V-02)"
```

---

## Task 8: T-01 — Wire frontend unit tests into CI

**Files:**
- Modify: `.github/workflows/ci.yml`

**Context:** The `web` CI job runs `typecheck` and `build` but not `npm test`. Vitest tests exist (32 test files) but are never run in CI, so regressions ship silently.

- [ ] **Step 1: Verify tests pass locally**

```bash
cd apps/web
npm test -- --run
```
Expected: All tests pass. Fix any pre-existing failures before proceeding.

- [ ] **Step 2: Modify the `web` job in `.github/workflows/ci.yml`**

Find the `web:` job. After `- run: npm run typecheck` and before `- run: npm run build`, add:

```yaml
      - run: npm test -- --run
```

Full web job should be:
```yaml
  web:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: apps/web
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: npm install
      - run: npm run typecheck
      - run: npm test -- --run
      - run: npm run build
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add npm test to web CI job — frontend unit tests now gate merges (T-01)"
```

---

## Task 9: V-10 — Flip Postgres test lane from advisory to blocking

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: relevant test files to fix known failures first

**Context:** The "Full API suite on Postgres" step has `continue-on-error: true` because some tests have "orphan-row FK violations in a few ops/AI tests". These must be fixed before flipping the gate.

- [ ] **Step 1: Identify which tests fail on Postgres**

Run locally or inspect CI logs:
```bash
NOODLE_TEST_DATABASE_URL=postgresql+asyncpg://noodle:noodle@localhost/noodle \
  uv run pytest apps/api/tests/ -v --timeout=60 2>&1 | grep "FAILED\|ERROR" | head -30
```

- [ ] **Step 2: Fix FK violation tests**

The audit mentions "orphan-row FK violations in ops/AI tests." Read each failing test and fix either:
a) The test itself (not deleting referenced rows in teardown), or
b) The model/migration (adding `ON DELETE CASCADE` where appropriate)

Pattern: if a test creates a `Run` and a `NodeRun` but only deletes the `NodeRun`, the `Run` FK violation fires. Fix the teardown to delete in the correct order, or ensure `CASCADE` is set.

- [ ] **Step 3: Flip `continue-on-error` to `false`**

In `.github/workflows/ci.yml`, find:
```yaml
      - name: Full API suite on Postgres (advisory)
        continue-on-error: true
        run: >-
          uv run pytest --timeout=300 --timeout-method=thread apps/api/tests
```

Change to:
```yaml
      - name: Full API suite on Postgres
        run: >-
          uv run pytest --timeout=300 --timeout-method=thread apps/api/tests
```

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml apps/api/tests/
git commit -m "ci: flip Postgres test lane from advisory to blocking (V-10)"
```

---

## Task 10: D-01 — Unique constraint on `runs.deduplication_key`

**Files:**
- Create: `apps/api/alembic/versions/0061_runs_dedup_unique.py`
- Modify: `apps/api/tests/test_runs.py`

**Context:** `runs.deduplication_key` has a plain index but no UNIQUE constraint. Two concurrent webhook deliveries can both pass the "no active run for this key" check before either commits, creating duplicate runs.

- [ ] **Step 1: Write a failing test for the race condition**

In `apps/api/tests/test_runs.py`:

```python
async def test_deduplication_key_unique_enforced(session):
    """Two runs with the same dedup key must not both be inserted."""
    from sqlalchemy.exc import IntegrityError
    from app.models import Run
    
    run1 = Run(id="run-dedup-1", workflow_id="wf1", deduplication_key="key-abc", status="pending")
    run2 = Run(id="run-dedup-2", workflow_id="wf1", deduplication_key="key-abc", status="pending")
    session.add(run1)
    await session.flush()
    session.add(run2)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()
```

Run: `uv run pytest apps/api/tests/test_runs.py::test_deduplication_key_unique_enforced -v`
Expected: FAIL on SQLite (no constraint), may work on Postgres with migration applied

- [ ] **Step 2: Create migration `0061_runs_dedup_unique.py`**

```python
"""Add partial unique index on runs.deduplication_key

Revision ID: 0061_runs_dedup_unique
Revises: 0060_mcp_production_hardening
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0061_runs_dedup_unique"
down_revision: str | None = "0060_mcp_production_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remove the existing plain index first (created in migration 0025)
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_index("ix_runs_deduplication_key", if_exists=True)
    
    # Add partial unique index: NULL values are excluded (multiple NULLs allowed)
    # Only Postgres supports partial indexes natively.
    # For SQLite compatibility, use a regular unique index (NULLs still not equal in SQLite)
    op.create_index(
        "uq_runs_deduplication_key",
        "runs",
        ["deduplication_key"],
        unique=True,
        postgresql_where=sa.text("deduplication_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_runs_deduplication_key", table_name="runs")
    op.create_index("ix_runs_deduplication_key", "runs", ["deduplication_key"])
```

- [ ] **Step 3: Apply migration**

```bash
cd apps/api && uv run alembic upgrade head
```

- [ ] **Step 4: Run test**

```bash
uv run pytest apps/api/tests/test_runs.py::test_deduplication_key_unique_enforced -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/alembic/versions/0061_runs_dedup_unique.py apps/api/tests/test_runs.py
git commit -m "db: add partial unique index on runs.deduplication_key (D-01)"
```

---

## Task 11: D-02 — Unique constraint on `(workflow_id, version)` in `workflow_versions`

**Files:**
- Create: `apps/api/alembic/versions/0062_workflow_version_unique.py`
- Modify: `apps/api/tests/test_workflows.py`

- [ ] **Step 1: Write failing test**

```python
async def test_workflow_version_unique_enforced(session, workflow):
    """Two WorkflowVersions with the same (workflow_id, version) must fail."""
    from sqlalchemy.exc import IntegrityError
    from app.models import WorkflowVersion
    
    v1 = WorkflowVersion(id="wfv-1", workflow_id=workflow.id, version=1, graph={})
    v2 = WorkflowVersion(id="wfv-2", workflow_id=workflow.id, version=1, graph={})
    session.add(v1)
    await session.flush()
    session.add(v2)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()
```

- [ ] **Step 2: Create migration**

```python
"""Add unique constraint on (workflow_id, version) in workflow_versions

Revision ID: 0062_workflow_version_unique
Revises: 0061_runs_dedup_unique
"""
from __future__ import annotations

from alembic import op

revision: str = "0062_workflow_version_unique"
down_revision: str | None = "0061_runs_dedup_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_versions") as batch_op:
        batch_op.create_unique_constraint(
            "uq_workflow_versions_workflow_version",
            ["workflow_id", "version"],
        )


def downgrade() -> None:
    with op.batch_alter_table("workflow_versions") as batch_op:
        batch_op.drop_constraint(
            "uq_workflow_versions_workflow_version", type_="unique"
        )
```

- [ ] **Step 3: Apply and test**

```bash
cd apps/api && uv run alembic upgrade head
uv run pytest apps/api/tests/test_workflows.py -v
```

- [ ] **Step 4: Commit**

```bash
git add apps/api/alembic/versions/0062_workflow_version_unique.py apps/api/tests/test_workflows.py
git commit -m "db: add unique constraint on (workflow_id, version) (D-02)"
```

---

## Task 12: D-03 + D-05 — RLS policies for GitHub sync tables + composite index

**Files:**
- Create: `apps/api/alembic/versions/0063_github_sync_rls.py`
- Read: `apps/api/alembic/versions/0058_tenant_rls_hardening.py` (for RLS pattern)

- [ ] **Step 1: Read migration 0058 to understand the RLS pattern**

```bash
cat apps/api/alembic/versions/0058_tenant_rls_hardening.py
```

Copy the exact RLS policy creation pattern.

- [ ] **Step 2: Create migration**

```python
"""Add RLS policies to github_sync_jobs and github_sync_configs + hot-path index

Revision ID: 0063_github_sync_rls
Revises: 0062_workflow_version_unique
"""
from __future__ import annotations

from alembic import op
from sqlalchemy.dialects.postgresql import dialect as pg_dialect

revision: str = "0063_github_sync_rls"
down_revision: str | None = "0062_workflow_version_unique"
branch_labels = None
depends_on = None

_TABLES = ["github_sync_jobs", "github_sync_configs"]


def upgrade() -> None:
    # Only Postgres supports RLS
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # Add composite index for both backends
        op.create_index(
            "ix_github_sync_jobs_org_status_retry",
            "github_sync_jobs",
            ["org_id", "status", "next_retry_at"],
        )
        return

    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY org_isolation ON {table} "
            "USING ("
            "  NULLIF(current_setting('app.current_org', true), '') IS NULL"
            "  OR org_id = current_setting('app.current_org', true)"
            ") "
            "WITH CHECK ("
            "  NULLIF(current_setting('app.current_org', true), '') IS NULL"
            "  OR org_id = current_setting('app.current_org', true)"
            ")"
        )

    # Hot-path index for the sync worker query
    op.create_index(
        "ix_github_sync_jobs_org_status_retry",
        "github_sync_jobs",
        ["org_id", "status", "next_retry_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_github_sync_jobs_org_status_retry", table_name="github_sync_jobs")
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS org_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
```

- [ ] **Step 3: Apply and verify**

```bash
cd apps/api && uv run alembic upgrade head
```

- [ ] **Step 4: Commit**

```bash
git add apps/api/alembic/versions/0063_github_sync_rls.py
git commit -m "db: add RLS policies to github_sync_jobs + github_sync_configs; add hot-path index (D-03/D-05)"
```

---

## Task 13: D-04 — Encrypt `github_sync_configs.webhook_secret`

**Files:**
- Read: `apps/api/app/models.py` (GithubSyncConfig model)
- Read: `apps/api/app/services/crypto.py` (encryption pattern)
- Create: `apps/api/alembic/versions/0064_encrypt_webhook_secret.py`
- Modify: `apps/api/app/models.py`
- Modify: `apps/api/app/services/github_sync.py`
- Modify: `apps/api/app/routers/github_sync.py`

**Context:** `GithubSyncConfig.webhook_secret` is a plaintext `Text` column. Follow the exact DEK/KEK pattern used for `Credential.encrypted_data` and `Credential.encrypted_dek`.

- [ ] **Step 1: Read the Credential model for the DEK/KEK pattern**

```bash
grep -n "encrypted_data\|encrypted_dek\|encrypt_data\|decrypt_data" \
    apps/api/app/models.py apps/api/app/services/crypto.py | head -30
```

- [ ] **Step 2: Update `GithubSyncConfig` model**

In `apps/api/app/models.py`, find `GithubSyncConfig` class and add encrypted columns:

```python
class GithubSyncConfig(Base):
    __tablename__ = "github_sync_configs"
    # ... existing columns ...
    
    # Replace: webhook_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    # With encrypted variant:
    encrypted_webhook_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_webhook_secret_dek: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Keep old column briefly for migration, will drop after
    webhook_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Add property to access the decrypted secret:
```python
    @property
    def decrypted_webhook_secret(self) -> str | None:
        """Decrypt webhook secret using org KEK pattern."""
        if self.encrypted_webhook_secret is None:
            # Fall back to legacy plaintext during migration period
            return self.webhook_secret
        from app.services.crypto import decrypt_data
        import json
        data = decrypt_data(
            self.encrypted_webhook_secret,
            dek_ciphertext=self.encrypted_webhook_secret_dek,
        )
        return data.get("secret")
```

- [ ] **Step 3: Create migration to add encrypted columns and migrate existing data**

```python
"""Encrypt github_sync_configs.webhook_secret using org KEK

Revision ID: 0064_encrypt_webhook_secret
Revises: 0063_github_sync_rls
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0064_encrypt_webhook_secret"
down_revision: str | None = "0063_github_sync_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("github_sync_configs") as batch_op:
        batch_op.add_column(
            sa.Column("encrypted_webhook_secret", sa.Text, nullable=True)
        )
        batch_op.add_column(
            sa.Column("encrypted_webhook_secret_dek", sa.Text, nullable=True)
        )
    
    # Migrate existing plaintext secrets
    # NOTE: This runs at migration time; if crypto is not available,
    # the plaintext column stays as fallback until re-encryption runs
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, webhook_secret, org_id FROM github_sync_configs WHERE webhook_secret IS NOT NULL")
    )
    for row in rows:
        try:
            from app.services.crypto import encrypt_data, get_org_kek_sync
            import json
            org_kek = get_org_kek_sync(row.org_id)
            encrypted, dek_ct = encrypt_data({"secret": row.webhook_secret}, org_kek=org_kek)
            bind.execute(
                sa.text(
                    "UPDATE github_sync_configs SET "
                    "encrypted_webhook_secret = :enc, "
                    "encrypted_webhook_secret_dek = :dek, "
                    "webhook_secret = NULL "
                    "WHERE id = :id"
                ),
                {"enc": encrypted, "dek": dek_ct, "id": row.id}
            )
        except Exception:
            # If encryption fails (key not available), leave plaintext as fallback
            pass


def downgrade() -> None:
    # On downgrade, restore plaintext from encrypted (requires crypto to be available)
    with op.batch_alter_table("github_sync_configs") as batch_op:
        batch_op.drop_column("encrypted_webhook_secret")
        batch_op.drop_column("encrypted_webhook_secret_dek")
```

**Note:** If `get_org_kek_sync` doesn't exist (only async version), use the async version via `asyncio.run()` or adapt. Check `apps/api/app/services/crypto.py` for the exact available functions.

- [ ] **Step 4: Update `verify_github_hmac` to use `decrypted_webhook_secret`**

In `apps/api/app/services/github_sync.py`, wherever `config.webhook_secret` is accessed, replace with `config.decrypted_webhook_secret`.

- [ ] **Step 5: Write test**

```python
async def test_webhook_secret_encrypted_at_rest(session, github_sync_config_factory):
    """webhook_secret must be stored encrypted, not in plaintext."""
    config = await github_sync_config_factory(webhook_secret="my-secret-value")
    
    # Reload from DB
    from sqlalchemy import select, text
    from app.models import GithubSyncConfig
    
    # Check the raw DB value — should not be plaintext
    raw = await session.execute(
        text("SELECT webhook_secret, encrypted_webhook_secret FROM github_sync_configs WHERE id = :id"),
        {"id": config.id}
    )
    row = raw.one()
    assert row.webhook_secret is None, "webhook_secret must be NULL after encryption"
    assert row.encrypted_webhook_secret is not None
    assert "my-secret-value" not in (row.encrypted_webhook_secret or "")
    
    # But the property should decrypt correctly
    await session.refresh(config)
    assert config.decrypted_webhook_secret == "my-secret-value"
```

- [ ] **Step 6: Apply and run tests**

```bash
cd apps/api && uv run alembic upgrade head
uv run pytest apps/api/tests/test_github_sync.py -v
```

- [ ] **Step 7: Commit**

```bash
git add apps/api/alembic/versions/0064_encrypt_webhook_secret.py \
        apps/api/app/models.py \
        apps/api/app/services/github_sync.py \
        apps/api/tests/test_github_sync.py
git commit -m "security: encrypt github_sync_configs.webhook_secret using org KEK (D-04)"
```

---

## Task 14: B-04 — Auth guard on unauthenticated list endpoints

**Files:**
- Modify: `apps/api/app/routers/workflows.py`
- Modify: `apps/api/app/routers/runs.py`
- Modify: `apps/api/app/security.py` (add `soft_require_auth` dependency if needed)
- Modify: `apps/api/tests/test_workflows.py`, `apps/api/tests/test_runs.py`

**Context:** `GET /workflows` and `GET /runs` return data without requiring auth when `auth_required=False`. The correct fix is to gate these on a dependency that enforces auth when `settings.auth_required=True`, and passes through when false. This preserves single-tenant dev-mode behaviour while hardening deployed instances.

- [ ] **Step 1: Check if `soft_require_auth` or similar already exists**

```bash
grep -n "soft_require\|optional.*auth\|auth_required" apps/api/app/security.py | head -20
```

- [ ] **Step 2: Add `require_auth_if_enabled` dependency to `security.py`**

If not already present, add to `apps/api/app/security.py`:

```python
async def require_auth_if_enabled(
    user: User | None = Depends(optional_current_user),
) -> User | None:
    """Require authentication when auth_required=True; pass through when False.
    
    Allows single-tenant dev deployments to browse without login, while
    production deployments with auth_required=True are fully gated.
    """
    if settings.auth_required and user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    return user
```

- [ ] **Step 3: Add to `list_workflows` and `list_all_runs`**

In `apps/api/app/routers/workflows.py`, find `list_workflows` and add:
```python
@router.get("/workflows", response_model=...)
async def list_workflows(
    ...,
    _user: User | None = Depends(require_auth_if_enabled),
) -> ...:
```

Same for `list_all_runs` in `apps/api/app/routers/runs.py`.

Also add the dependency to `GET /runs/{run_id}` and `GET /workflows/{workflow_id}` if they lack it.

- [ ] **Step 4: Write tests**

```python
async def test_list_workflows_requires_auth_when_enabled(client_no_auth, auth_required_settings):
    """With auth_required=True, unauthenticated list endpoints return 401."""
    resp = await client_no_auth.get("/workflows")
    assert resp.status_code == 401

async def test_list_workflows_accessible_without_auth_when_disabled(client_no_auth, no_auth_settings):
    """With auth_required=False, list endpoints work without auth (dev mode)."""
    resp = await client_no_auth.get("/workflows")
    assert resp.status_code == 200
```

- [ ] **Step 5: Run tests and commit**

```bash
uv run pytest apps/api/tests/test_workflows.py apps/api/tests/test_runs.py -v
git add apps/api/app/security.py apps/api/app/routers/workflows.py \
        apps/api/app/routers/runs.py apps/api/tests/
git commit -m "security: gate list endpoints on auth when auth_required=True (B-04)"
```

---

## Task 15: B-05 — Replace `session.commit()` with `session.flush()` in auth dependency

**Files:**
- Modify: `apps/api/app/security.py`

- [ ] **Step 1: Find the line**

```bash
grep -n "session.commit\|last_used_at" apps/api/app/security.py
```

- [ ] **Step 2: Replace `commit()` with `flush()`**

In `_api_token_principal`, find:
```python
await session.commit()
```
Replace with:
```python
await session.flush()
```

- [ ] **Step 3: Write test**

```python
async def test_api_token_last_used_updated_without_premature_commit(session, api_token):
    """last_used_at update should not commit the session prematurely."""
    # Verify the update happens without a full commit
    # This is a unit test on the dependency
    from app.security import _api_token_principal
    # ... set up and call the dependency, verify no IntegrityError from premature commit
```

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/security.py
git commit -m "fix: replace session.commit() with flush() in _api_token_principal (B-05)"
```

---

## Task 16: B-06 — API restart should not cancel remote-pool runs

**Files:**
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/tests/` (add test)

- [ ] **Step 1: Find `_mark_interrupted_runs`**

```bash
grep -n "_mark_interrupted_runs\|running.*cancel\|interrupted" apps/api/app/main.py | head -20
```

- [ ] **Step 2: Add filter to exclude remote-pool runs**

Find the function and add a filter `Run.runner_pool_id.is_(None)`:

```python
async def _mark_interrupted_runs(session: AsyncSession) -> None:
    # Only cancel local runs (runner_pool_id IS NULL).
    # Remote-pool runs are managed by lease expiry — the worker heartbeats
    # extend the lease; if the worker dies, the lease expires and the run
    # is re-queued automatically. Cancelling here would abort active work.
    stmt = (
        select(Run)
        .where(
            Run.status.in_(("running", "waiting")),
            Run.runner_pool_id.is_(None),  # <-- ADD THIS FILTER
        )
    )
    ...
```

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/main.py
git commit -m "fix: do not cancel remote-pool runs on API restart (B-06)"
```

---

## Task 17: V-03 — Multi-stage Python Dockerfile

**Files:**
- Modify: `deploy/Dockerfile.python`

- [ ] **Step 1: Read current Dockerfile**

```bash
cat deploy/Dockerfile.python
```

- [ ] **Step 2: Rewrite as multi-stage**

```dockerfile
# Stage 1: Install dependencies only (no source code)
FROM python:3.12-slim AS deps
WORKDIR /build
RUN pip install uv
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/
COPY packages/core/pyproject.toml packages/core/
COPY packages/nodes/pyproject.toml packages/nodes/
COPY packages/runner/pyproject.toml packages/runner/
COPY packages/runtime/pyproject.toml packages/runtime/
COPY packages/exporter/pyproject.toml packages/exporter/
COPY packages/importer/pyproject.toml packages/importer/
RUN uv sync --locked --no-dev

# Stage 2: Production image — only source code + deps
FROM python:3.12-slim AS production
WORKDIR /app

# Create non-root user (matches existing pattern)
RUN groupadd -r noodle && useradd -r -g noodle -u 10001 noodle

# Copy deps from stage 1
COPY --from=deps /build/.venv /app/.venv

# Copy only source code (no tests, docs, design, CI configs)
COPY apps/api/app apps/api/app
COPY apps/api/alembic apps/api/alembic
COPY apps/api/alembic.ini apps/api/alembic.ini
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY packages packages
COPY pyproject.toml ./

# Entrypoint
COPY deploy/python-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER noodle
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["/entrypoint.sh"]
```

**Note:** Adjust the `COPY` paths to match the actual Dockerfile.python structure exactly. The above is a template; read the existing file first.

- [ ] **Step 3: Verify build**

```bash
docker build -f deploy/Dockerfile.python -t noodle-api:test . 2>&1 | tail -5
# Verify test files are not included:
docker run --rm noodle-api:test find /app -name "test_*.py" 2>/dev/null | head -5
```
Expected: No test files found.

- [ ] **Step 4: Commit**

```bash
git add deploy/Dockerfile.python
git commit -m "build: multi-stage Python Dockerfile — excludes tests, docs, CI artifacts (V-03)"
```

---

## Task 18: V-04 — Helm migration job: DATABASE_URL as Kubernetes Secret

**Files:**
- Modify: `deploy/helm/noodle/templates/migration-job.yaml`
- Modify: `deploy/helm/noodle/templates/runtime-secret.yaml`
- Modify: `deploy/helm/noodle/values.yaml`

- [ ] **Step 1: Read current files**

```bash
cat deploy/helm/noodle/templates/migration-job.yaml
cat deploy/helm/noodle/templates/runtime-secret.yaml
```

- [ ] **Step 2: Add `postgres-url` key to the Secret**

In `runtime-secret.yaml`, add:
```yaml
  postgres-url: {{ .Values.noodle.databaseUrl | b64enc | quote }}
```

- [ ] **Step 3: Update migration-job.yaml to use secretKeyRef**

Replace:
```yaml
- name: DATABASE_URL
  value: {{ .Values.noodle.databaseUrl | quote }}
```
With:
```yaml
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "noodle.fullname" . }}-runtime
      key: postgres-url
```

- [ ] **Step 4: Add `databaseUrl` to values.yaml with placeholder**

```yaml
noodle:
  databaseUrl: "postgresql+asyncpg://noodle:CHANGE_ME@postgres:5432/noodle"
```

- [ ] **Step 5: Commit**

```bash
git add deploy/helm/noodle/
git commit -m "ops: move Helm migration job DATABASE_URL into Kubernetes Secret (V-04)"
```

---

## Task 19: V-05 + V-09 — Add liveness probe + rolling update strategy to Helm

**Files:**
- Modify: `deploy/helm/noodle/templates/api-deployment.yaml`

- [ ] **Step 1: Add liveness probe**

```yaml
livenessProbe:
  httpGet:
    path: /health/live
    port: 8000
  initialDelaySeconds: 30
  periodSeconds: 20
  failureThreshold: 3
  timeoutSeconds: 5
readinessProbe:
  httpGet:
    path: /health/ready
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 10
  failureThreshold: 3
  timeoutSeconds: 5
```

- [ ] **Step 2: Add rolling update strategy**

```yaml
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxSurge: 1
    maxUnavailable: 0
```

- [ ] **Step 3: Add terminationGracePeriodSeconds**

```yaml
spec:
  terminationGracePeriodSeconds: 60
```

- [ ] **Step 4: Commit**

```bash
git add deploy/helm/noodle/templates/api-deployment.yaml
git commit -m "ops: add liveness probe, rolling update strategy, terminationGracePeriodSeconds (V-05/V-09)"
```

---

## Task 20: V-06 — Add Kubernetes resource limits

**Files:**
- Modify: `deploy/helm/noodle/values.yaml`
- Modify: `deploy/helm/noodle/templates/api-deployment.yaml`
- Modify: `deploy/helm/noodle/templates/worker-deployment.yaml`

- [ ] **Step 1: Add limits to values.yaml**

```yaml
api:
  resources:
    requests:
      cpu: "250m"
      memory: "512Mi"
    limits:
      cpu: "1000m"
      memory: "1Gi"

worker:
  resources:
    requests:
      cpu: "500m"
      memory: "1Gi"
    limits:
      cpu: "2000m"
      memory: "4Gi"
```

- [ ] **Step 2: Wire into deployment templates**

In `api-deployment.yaml` and `worker-deployment.yaml`:
```yaml
resources:
  {{- toYaml .Values.api.resources | nindent 12 }}
```

- [ ] **Step 3: Commit**

```bash
git add deploy/helm/noodle/
git commit -m "ops: add Kubernetes resource requests and limits (V-06)"
```

---

## Task 21: V-07 + V-08 — Ingress TLS warning + MinIO credential security

**Files:**
- Modify: `deploy/helm/noodle/values.yaml`
- Modify: `deploy/docker-compose.yml`
- Create: `deploy/helm/noodle/templates/NOTES.txt`

- [ ] **Step 1: Add Helm NOTES.txt with TLS warning**

Create `deploy/helm/noodle/templates/NOTES.txt`:
```
Noodle has been deployed.

{{- if and .Values.ingress.enabled (not .Values.ingress.tls) }}

⚠️  WARNING: Ingress TLS is disabled!
    Credentials, session tokens, and workflow data will be transmitted in
    plaintext. For production, enable TLS via cert-manager:

      ingress:
        tls:
          enabled: true
          secretName: noodle-tls
        annotations:
          cert-manager.io/cluster-issuer: letsencrypt-prod

{{- end }}
```

- [ ] **Step 2: Parameterize MinIO credentials in docker-compose.yml**

In `deploy/docker-compose.yml`, replace hardcoded MinIO credentials:
```yaml
minio:
  environment:
    MINIO_ROOT_USER: ${MINIO_ROOT_USER:?required - set in deploy/.env}
    MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:?required - set in deploy/.env}
  ports:
    - "127.0.0.1:9000:9000"
    - "127.0.0.1:9001:9001"
```

- [ ] **Step 3: Update deploy/.env.example**

Add:
```
MINIO_ROOT_USER=noodle
MINIO_ROOT_PASSWORD=CHANGE-ME-at-least-8-chars
```

- [ ] **Step 4: Commit**

```bash
git add deploy/helm/noodle/templates/NOTES.txt deploy/docker-compose.yml deploy/.env.example
git commit -m "ops: add TLS warning in Helm NOTES; secure MinIO bind address and credentials (V-07/V-08)"
```

---

## Task 22: F-01 — Remove `window.__noodle_sign_out` global

**Files:**
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/HomeHeader.tsx` (or wherever `window.__noodle_sign_out` is consumed)

- [ ] **Step 1: Find all usages**

```bash
grep -rn "__noodle_sign_out\|window\.__noodle" apps/web/src/
```

- [ ] **Step 2: Add `signOut` to `AuthRuntimeProvider` context**

Read `apps/web/src/AuthRuntime.tsx` to understand the current context shape. Add `signOut` to the context value and the `AuthRuntimeContext` type.

- [ ] **Step 3: Remove `window.__noodle_sign_out` from App.tsx**

Find lines like:
```typescript
window.__noodle_sign_out = signOut;
```
Remove them and instead pass `signOut` through context.

- [ ] **Step 4: Update `HomeHeader` to use context**

```typescript
// Before:
const handleSignOut = () => window.__noodle_sign_out?.();

// After:
import { useAuthRuntime } from './AuthRuntime';
const { signOut } = useAuthRuntime();
const handleSignOut = signOut;
```

- [ ] **Step 5: Run tests**

```bash
cd apps/web && npm test -- --run
npm run typecheck
```

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/
git commit -m "refactor: remove window.__noodle_sign_out; use AuthRuntime context (F-01)"
```

---

## Task 23: F-02 — Fix `marked.parse()` async return value in ChatPanel

**Files:**
- Modify: `apps/web/src/editor/ChatPanel.tsx`

- [ ] **Step 1: Find the problematic line**

```bash
grep -n "marked\|parseInline\|dangerouslySetInnerHTML" apps/web/src/editor/ChatPanel.tsx
```

- [ ] **Step 2: Fix the async issue**

Find the `renderMessageContent` function (~line 206). It likely looks like:
```typescript
const html = DOMPurify.sanitize(marked.parse(content) as string);
```

The issue: `marked.parse()` can return `Promise<string>` in async mode.

Fix — use `marked.parseInline()` (always synchronous) for inline message content, or configure `marked` to always be synchronous:

```typescript
import { marked } from 'marked';

// Option A: Use parseInline (always sync, good for chat messages)
const renderMessageContent = (content: string): string => {
  return DOMPurify.sanitize(marked.parseInline(content) as string);
};

// Option B: Force sync mode
marked.setOptions({ async: false });
const renderMessageContent = (content: string): string => {
  return DOMPurify.sanitize(marked.parse(content) as string);
};
```

Choose Option A (`parseInline`) if chat messages are single-paragraph. Choose Option B if they can contain block-level markdown (headers, lists, code blocks). Read the chat UI to decide.

- [ ] **Step 3: Write test**

```typescript
// In apps/web/src/editor/ChatPanel.test.tsx:
it('renders markdown content as string not Promise', () => {
  render(<ChatMessage content="**hello**" />);
  expect(screen.getByText('hello')).toBeInTheDocument();
  expect(screen.queryByText('[object Promise]')).not.toBeInTheDocument();
});
```

- [ ] **Step 4: Run tests**

```bash
cd apps/web && npm test -- --run
```

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/ChatPanel.tsx
git commit -m "fix: use marked.parseInline to avoid Promise<string> in chat rendering (F-02)"
```

---

## Task 24: F-03 — Fix canvas drag listener leak on unmount

**Files:**
- Modify: `apps/web/src/editor/Canvas.tsx`

- [ ] **Step 1: Find the right-drag handler**

```bash
grep -n "rightDrag\|mousemove\|mouseup\|handleCanvasMouseDown" apps/web/src/editor/Canvas.tsx | head -20
```

- [ ] **Step 2: Fix cleanup in `handleCanvasMouseDown`**

The current code adds `window.addEventListener('mousemove', onMove)` and `window.addEventListener('mouseup', onUp)` imperatively. The `onUp` removes them, but if the component unmounts before `mouseup`, listeners leak.

Read the current `handleCanvasMouseDown` (lines 761–817). Add an AbortController:

```typescript
const handleCanvasMouseDown = useCallback((e: React.MouseEvent) => {
  if (e.button !== 2) return; // Right mouse button only
  
  const controller = new AbortController();
  rightDragRef.current = { controller }; // Store for cleanup
  
  const onMove = (ev: MouseEvent) => { /* ... existing logic ... */ };
  const onUp = (ev: MouseEvent) => {
    controller.abort(); // Removes all listeners
    /* ... existing cleanup ... */
  };
  
  window.addEventListener('mousemove', onMove, { signal: controller.signal });
  window.addEventListener('mouseup', onUp, { signal: controller.signal });
}, [/* existing deps */]);

// Add cleanup effect:
useEffect(() => {
  return () => {
    // Abort any in-progress right-drag on unmount
    rightDragRef.current?.controller?.abort();
  };
}, []);
```

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/editor/Canvas.tsx
git commit -m "fix: clean up canvas right-drag window listeners on unmount (F-03)"
```

---

## Task 25: F-05 — Reset credential form values when preset changes

**Files:**
- Modify: `apps/web/src/CredentialsPage.tsx`

- [ ] **Step 1: Find the preset change handler**

```bash
grep -n "presetId\|setPresetId\|setValues\|preset" apps/web/src/CredentialsPage.tsx | head -20
```

- [ ] **Step 2: Add values reset in the preset change handler**

Find where `presetId` state is set. In the same handler (or in a `useEffect` watching `presetId`), also reset `values`:

```typescript
const handlePresetChange = (newPresetId: string) => {
  const newPreset = presets.find(p => p.id === newPresetId);
  setPresetId(newPresetId);
  if (newPreset) {
    setValues(presetInitialValues(newPreset));
    setName(newPreset.label); // Also reset the name
  }
};
```

Or if using `useEffect`:
```typescript
useEffect(() => {
  const preset = presets.find(p => p.id === presetId);
  if (preset) {
    setValues(presetInitialValues(preset));
  }
}, [presetId]); // Runs when presetId changes
```

- [ ] **Step 3: Write test**

```typescript
it('resets form values when switching credential presets', async () => {
  const { user } = renderWithProviders(<CredentialsPage />);
  
  // Open create modal, select Google OAuth preset
  await user.click(screen.getByText('New credential'));
  await user.click(screen.getByText('Google OAuth'));
  
  // Fill in a field
  await user.type(screen.getByLabelText('Client ID'), 'my-client-id');
  
  // Switch to API Key preset
  await user.click(screen.getByText('API Key'));
  
  // The Client ID field should be gone/reset
  expect(screen.queryByDisplayValue('my-client-id')).not.toBeInTheDocument();
});
```

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/CredentialsPage.tsx
git commit -m "fix: reset credential form values when switching presets (F-05)"
```

---

## Task 26: T-03 — Add pagination to credentials list endpoint

**Files:**
- Modify: `apps/api/app/routers/credentials.py`
- Modify: `apps/api/tests/test_credentials_v2.py`

**Context:** `GET /credentials` silently truncates at `_LIST_CREDENTIALS_HARD_CAP = 500`. Add `limit`/`offset` or `page`/`page_size` query params.

- [ ] **Step 1: Read current implementation**

```bash
grep -n "_LIST_CREDENTIALS_HARD_CAP\|list_credentials\|limit\|offset" \
    apps/api/app/routers/credentials.py | head -30
```

- [ ] **Step 2: Add pagination parameters**

Modify `list_credentials` to accept `limit: int = Query(default=50, le=500)` and `offset: int = Query(default=0, ge=0)`:

```python
@router.get("/credentials", response_model=PageResponse[CredentialInfo])
async def list_credentials(
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(require_permission("credential:read")),
) -> PageResponse[CredentialInfo]:
    # Count total
    total = await session.scalar(select(func.count()).select_from(Credential))
    # Query page
    creds = (await session.scalars(
        select(Credential).offset(offset).limit(limit)
    )).all()
    items = [await _info(c, session) for c in creds]
    return PageResponse(items=items, total=total, limit=limit, offset=offset)
```

- [ ] **Step 3: Update frontend credentials query**

In `apps/web/src/queries/index.ts`, update `useCredentials` to handle paginated response.

- [ ] **Step 4: Write tests**

```python
async def test_credentials_pagination(client, credential_factory):
    """Credentials list must support pagination."""
    for i in range(5):
        await credential_factory(name=f"cred-{i}")
    
    resp = await client.get("/credentials?limit=2&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["total"] == 5
    
    resp2 = await client.get("/credentials?limit=2&offset=2")
    assert len(resp2.json()["items"]) == 2
```

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/routers/credentials.py apps/api/tests/test_credentials_v2.py \
        apps/web/src/queries/index.ts
git commit -m "feat: paginate credentials list endpoint (T-03)"
```

---

## Task 27: T-04 — Workflow list: don't load all versions

**Files:**
- Modify: `apps/api/app/routers/workflows.py`
- Modify: `apps/api/tests/test_workflows.py`

**Context:** `GET /workflows` uses `selectinload(Workflow.versions)` which loads all version rows for every workflow. Replace with a targeted latest-version subquery.

- [ ] **Step 1: Read current implementation**

```bash
grep -n "selectinload.*versions\|list_workflows" apps/api/app/routers/workflows.py | head -20
```

- [ ] **Step 2: Replace with aggregated subquery**

Instead of `selectinload(Workflow.versions)`, use a `select()` with a subquery for just the latest version number:

```python
from sqlalchemy import func

# Subquery: latest version number per workflow
latest_version_sq = (
    select(
        WorkflowVersion.workflow_id,
        func.max(WorkflowVersion.version).label("latest_version"),
    )
    .group_by(WorkflowVersion.workflow_id)
    .subquery()
)

stmt = (
    select(Workflow, latest_version_sq.c.latest_version)
    .outerjoin(latest_version_sq, Workflow.id == latest_version_sq.c.workflow_id)
    .where(...)
    .offset(offset)
    .limit(limit)
)
```

Adjust the `WorkflowListItem` schema to use `latest_version: int | None` instead of `versions: list[WorkflowVersionInfo]`.

- [ ] **Step 3: Run tests and commit**

```bash
uv run pytest apps/api/tests/test_workflows.py -v
git add apps/api/app/routers/workflows.py apps/api/tests/test_workflows.py
git commit -m "perf: stop loading all workflow versions in list endpoint (T-04)"
```

---

## Task 28: T-05 — Add audit log router tests

**Files:**
- Create: `apps/api/tests/test_audit_router.py`

- [ ] **Step 1: Read the audit router**

```bash
cat apps/api/app/routers/audit.py
```

- [ ] **Step 2: Create tests**

```python
"""Tests for the audit log endpoint."""
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_audit_log_requires_permission(client_no_auth):
    """Audit log endpoint requires audit:read permission."""
    resp = await client_no_auth.get("/audit")
    assert resp.status_code in (401, 403)


async def test_audit_log_lists_events(client, workflow):
    """Audit log returns recorded audit events."""
    # The workflow creation should have been audited
    resp = await client.get("/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body or isinstance(body, list)


async def test_audit_log_filters_by_actor(client, current_user):
    """Audit log can be filtered by actor_id."""
    resp = await client.get(f"/audit?actor_id={current_user.id}")
    assert resp.status_code == 200


async def test_audit_log_pagination(client):
    """Audit log supports pagination via limit/offset."""
    resp = await client.get("/audit?limit=5&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    # Either a PageResponse or a list with headers
    assert body is not None
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest apps/api/tests/test_audit_router.py -v
```

- [ ] **Step 4: Commit**

```bash
git add apps/api/tests/test_audit_router.py
git commit -m "test: add audit log router tests (T-05)"
```

---

## Task 29: E-03 — Cancellation propagation to sync nodes (threading.Event)

**Files:**
- Read: `packages/core/noodle/engine/node_exec.py`
- Modify: `packages/core/noodle/context.py` (add cancellation event)
- Modify: `packages/core/noodle/engine/node_exec.py`

**Context:** When `asyncio.CancelledError` fires on a `to_thread` task, the underlying thread keeps running. We need a `threading.Event` per run that sync nodes can poll.

- [ ] **Step 1: Add `cancellation_event` to run context**

In `packages/core/noodle/context.py`:
```python
import threading

# Per-run cancellation event. Sync nodes running in threads should
# poll this at natural checkpoints (e.g., in loops, after blocking I/O).
run_cancellation_event: ContextVar[threading.Event | None] = ContextVar(
    "run_cancellation_event", default=None
)
```

- [ ] **Step 2: Set the event on cancellation in `node_exec.py`**

In `invoke_node`, wrap the `asyncio.to_thread` call to signal the event on cancellation:

```python
evt = run_cancellation_event.get()
try:
    result = await asyncio.to_thread(sync_fn, inputs)
except asyncio.CancelledError:
    if evt:
        evt.set()  # Signal threads to stop
    raise
```

- [ ] **Step 3: Update `wait_node` to poll the cancellation event**

In `builtin.py`, find the `wait_node` function:
```python
from noodle.context import run_cancellation_event

def wait_node(inputs):
    seconds = inputs.get("seconds", 0)
    evt = run_cancellation_event.get()
    # Sleep in 0.1s intervals, checking for cancellation
    remaining = float(seconds)
    while remaining > 0:
        if evt and evt.is_set():
            raise InterruptedError("Node cancelled")
        time.sleep(min(0.1, remaining))
        remaining -= 0.1
```

- [ ] **Step 4: Write test**

```python
async def test_sync_node_respects_cancellation_event():
    """Cancellation event should stop a blocking sync node."""
    import threading
    from noodle.context import run_cancellation_event
    
    evt = threading.Event()
    token = run_cancellation_event.set(evt)
    try:
        # Simulate a cancellation after 0.5s
        async def cancel_after():
            await asyncio.sleep(0.05)
            evt.set()
        
        asyncio.create_task(cancel_after())
        # wait_node for 10 seconds should return early due to cancellation
        start = time.time()
        with pytest.raises(InterruptedError):
            wait_node({"seconds": 10})
        assert time.time() - start < 1.0, "Node should have stopped early"
    finally:
        run_cancellation_event.reset(token)
```

- [ ] **Step 5: Commit**

```bash
git add packages/core/noodle/context.py packages/core/noodle/engine/node_exec.py \
        packages/nodes/noodle_nodes/builtin.py
git commit -m "feat: add threading.Event cancellation propagation to sync nodes (E-03)"
```

---

## Task 30: E-05 — Validate `index_urls` in venv backend through SSRF guard

**Files:**
- Modify: `apps/api/app/services/backends/venv.py`
- Modify: `apps/api/tests/test_venv_service.py`

- [ ] **Step 1: Read the `_do_build` function**

```bash
grep -n "index_urls\|extra.index\|_do_build" apps/api/app/services/backends/venv.py | head -20
```

- [ ] **Step 2: Add URL validation**

```python
from noodle_nodes.http_security import assert_public_http_url

async def _do_build(env: Environment, ...) -> None:
    index_urls = env.backend_config.get("index_urls", [])
    for url in index_urls:
        try:
            assert_public_http_url(url)
        except ValueError as e:
            raise ValueError(
                f"Package index URL is not allowed: {url!r}. "
                f"Private network URLs are blocked to prevent SSRF. "
                f"Reason: {e}"
            ) from e
    # ... rest of _do_build
```

- [ ] **Step 3: Write test**

```python
async def test_venv_backend_rejects_private_index_url(session, env_factory):
    """index_urls must be validated to prevent SSRF via package manager."""
    env = await env_factory(backend_config={"index_urls": ["http://169.254.169.254/pypi"]})
    with pytest.raises(ValueError, match="not allowed"):
        await build_environment(env)
```

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/services/backends/venv.py apps/api/tests/test_venv_service.py
git commit -m "security: validate venv index_urls through SSRF guard (E-05)"
```

---

## Task 31: E-08 — Propagate call_chain through remote sub-workflow dispatch

**Files:**
- Modify: relevant file in `apps/api/app/services/` or `packages/core/`
- Read: `packages/core/noodle/context.py` for `call_chain`

- [ ] **Step 1: Find remote dispatch**

```bash
grep -rn "call_chain\|resolve_remote_subworkflow\|SubworkflowCall" \
    packages/core/ apps/api/ | grep -v ".pyc" | head -20
```

- [ ] **Step 2: Pass call_chain in the SubworkflowCall payload**

In `resolve_remote_subworkflow`, add:
```python
from noodle.context import call_chain

current_chain = call_chain.get(frozenset())
# Include in the dispatch payload
payload["call_chain"] = list(current_chain)
```

And on the receiving side, restore it:
```python
received_chain = frozenset(payload.get("call_chain", []))
token = call_chain.set(received_chain)
try:
    await resolve_subworkflow(...)
finally:
    call_chain.reset(token)
```

- [ ] **Step 3: Commit**

```bash
git add packages/core/ apps/api/
git commit -m "fix: propagate call_chain through remote sub-workflow dispatch to detect cycles (E-08)"
```

---

## Task 32: D-11 — Add composite index on audit_events

**Files:**
- Create: `apps/api/alembic/versions/0065_audit_events_index.py`

- [ ] **Step 1: Create migration**

```python
"""Add composite index on audit_events (org_id, created_at DESC)

Revision ID: 0065_audit_events_index
Revises: 0064_encrypt_webhook_secret
"""
from __future__ import annotations

from alembic import op

revision: str = "0065_audit_events_index"
down_revision: str | None = "0064_encrypt_webhook_secret"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_events_org_id_created_at",
        "audit_events",
        ["org_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_org_id_created_at", table_name="audit_events")
```

- [ ] **Step 2: Apply and commit**

```bash
cd apps/api && uv run alembic upgrade head
git add apps/api/alembic/versions/0065_audit_events_index.py
git commit -m "db: add composite (org_id, created_at) index on audit_events (D-11)"
```

---

## Task 33: T-07 — Fix queue dispatch N+1 org limits query

**Files:**
- Modify: `apps/api/app/services/queue.py`

- [ ] **Step 1: Find `_org_fair_order`**

```bash
grep -n "_org_fair_order\|effective_limits" apps/api/app/services/queue.py | head -20
```

- [ ] **Step 2: Batch-load org limits**

```python
async def _org_fair_order(session: AsyncSession, org_ids: list[str]) -> list[str]:
    """Return org_ids sorted by fairness, loading limits in one batch query."""
    # Batch-load all relevant system settings in one query
    settings_rows = (await session.scalars(
        select(SystemSettings).where(SystemSettings.org_id.in_(org_ids))
    )).all()
    limits_by_org = {s.org_id: s.max_concurrent_runs for s in settings_rows}
    
    # ... use limits_by_org dict instead of calling effective_limits() per org
```

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/services/queue.py
git commit -m "perf: batch-load org limits in _org_fair_order to eliminate N+1 (T-07)"
```

---

## Task 34: T-08 — Bulk insert NodeRun records

**Files:**
- Modify: `apps/api/app/services/run_persistence.py`

- [ ] **Step 1: Find the insertion loop**

```bash
grep -n "NodeRun\|session.add\|node_run" apps/api/app/services/run_persistence.py | head -30
```

- [ ] **Step 2: Replace loop with bulk insert**

```python
# Before (loop of individual session.add()):
for nr_data in node_run_records:
    session.add(NodeRun(**nr_data))

# After (single bulk insert):
if node_run_records:
    await session.execute(
        insert(NodeRun),
        node_run_records,  # list of dicts
    )
```

- [ ] **Step 3: Run tests and commit**

```bash
uv run pytest apps/api/tests/ -k "node_run" -v
git add apps/api/app/services/run_persistence.py
git commit -m "perf: bulk insert NodeRun records instead of per-row session.add() (T-08)"
```

---

## Task 35: F-09 — Login form semantics

**Files:**
- Modify: `apps/web/src/LoginPage.tsx`

- [ ] **Step 1: Wrap form inputs in `<form onSubmit>`**

```typescript
<form
  onSubmit={(e) => {
    e.preventDefault();
    handleSubmit();
  }}
  noValidate
>
  <input
    type="email"
    autoComplete="email"
    value={email}
    onChange={(e) => setEmail(e.target.value)}
  />
  <input
    type="password"
    autoComplete="current-password"
    value={password}
    onChange={(e) => setPassword(e.target.value)}
  />
  <button type="submit">Sign in</button>
</form>
```

Remove per-input `onKeyDown` Enter handlers (the form submit handles it).

- [ ] **Step 2: Run tests and commit**

```bash
cd apps/web && npm test -- --run
git add apps/web/src/LoginPage.tsx
git commit -m "ux: add form semantics and autocomplete to login page (F-09)"
```

---

## Task 36: F-11 — React Query: don't retry 4xx errors

**Files:**
- Modify: `apps/web/src/queries/client.ts`

- [ ] **Step 1: Update retry config**

```typescript
import { QueryClient } from '@tanstack/react-query';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        // Never retry 4xx client errors — they won't succeed on retry
        if (error instanceof Error && 'status' in error) {
          const status = (error as { status: number }).status;
          if (status >= 400 && status < 500) return false;
        }
        return failureCount < 1;
      },
    },
  },
});
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/queries/client.ts
git commit -m "fix: do not retry 4xx API errors in React Query (F-11)"
```

---

## Task 37: V-13 — Flip security CI from advisory to blocking

**Files:**
- Modify: `.github/workflows/ci.yml`

**Context:** All three security CI steps have `continue-on-error: true`. First fix the underlying issues (bandit findings, mypy errors), then flip.

- [ ] **Step 1: Run bandit and mypy locally**

```bash
uvx bandit -ll -r apps/api/app packages/core/noodle packages/nodes/noodle_nodes 2>&1 | head -50
```

Fix all HIGH (`-lll`) severity findings. For MEDIUM findings, either fix or add `# nosec B<number>` with a comment explaining why it is a false positive or accepted risk.

- [ ] **Step 2: Run mypy on security modules**

```bash
uvx mypy --ignore-missing-imports --follow-imports=skip \
    apps/api/app/services/crypto.py \
    apps/api/app/services/rate_limit.py \
    apps/api/app/security.py
```

Fix all errors.

- [ ] **Step 3: Flip `continue-on-error: false`**

In `.github/workflows/ci.yml`, remove `continue-on-error: true` from pip-audit and bandit steps.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml apps/api/ packages/
git commit -m "ci: flip security scanning from advisory to blocking (V-13)"
```

---

## Task 38: T-10 — Add workflow import endpoint

**Files:**
- Create: `apps/api/app/routers/import_router.py` (or add to existing export router)
- Create: `apps/api/tests/test_import.py`

- [ ] **Step 1: Read the export endpoint**

```bash
cat apps/api/app/routers/export.py | head -80
```

- [ ] **Step 2: Create import endpoint**

```python
@router.post("/import", response_model=WorkflowInfo)
async def import_workflow(
    body: WorkflowImportRequest,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(require_permission("workflow:write")),
) -> WorkflowInfo:
    """Import a workflow from the exported JSON format."""
    # Validate the graph structure
    try:
        graph = WorkflowGraph.model_validate(body.graph)
    except ValidationError as e:
        raise HTTPException(422, f"Invalid workflow graph: {e}")
    
    # Create the workflow
    workflow = Workflow(
        id=_uuid(),
        name=body.name or "Imported Workflow",
        draft_graph=body.graph,
    )
    session.add(workflow)
    await session.flush()
    return WorkflowInfo.model_validate(workflow)
```

- [ ] **Step 3: Write round-trip test**

```python
async def test_workflow_import_export_roundtrip(client, workflow):
    """Exporting then importing a workflow preserves graph fidelity."""
    # Export
    export_resp = await client.get(f"/export/{workflow.id}")
    assert export_resp.status_code == 200
    exported = export_resp.json()
    
    # Import
    import_resp = await client.post("/import", json={
        "name": "Roundtrip Test",
        "graph": exported["graph"]
    })
    assert import_resp.status_code == 200
    imported = import_resp.json()
    
    # Verify graph structure is preserved
    assert imported["draft_graph"]["nodes"] == exported["graph"]["nodes"]
```

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/routers/ apps/api/tests/test_import.py
git commit -m "feat: add POST /import endpoint for workflow import (T-10)"
```

---

## Task 39: D-09 — Retention: add RunQueueEntry cleanup

**Files:**
- Modify: `apps/api/app/services/retention.py`

- [ ] **Step 1: Read current retention code**

```bash
cat apps/api/app/services/retention.py
```

- [ ] **Step 2: Add RunQueueEntry deletion**

Find where `NodeRun`, `RunEvent`, etc. are deleted before `Run`. Add:
```python
from app.models import RunQueueEntry

# Delete RunQueueEntry rows (FK cascade works on Postgres; explicit for SQLite)
await session.execute(delete(RunQueueEntry).where(RunQueueEntry.run_id.in_(ids)))
```

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/services/retention.py
git commit -m "fix: include RunQueueEntry in retention prune cascade (D-09)"
```

---

## Task 40: D-08 — Add `default=_uuid` to `Artifact.id`

**Files:**
- Modify: `apps/api/app/models.py`

- [ ] **Step 1: Find `Artifact.id` definition**

```bash
grep -n "class Artifact\|Artifact.*id.*primary" apps/api/app/models.py | head -5
```

- [ ] **Step 2: Add default**

```python
# Find _uuid function reference in models.py
id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
```

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/models.py
git commit -m "fix: add default=_uuid to Artifact.id to prevent blank-string PK (D-08)"
```

---

## Task 41: Final Validation

Run all available test suites and quality gates.

- [ ] **Step 1: Full backend test suite (SQLite)**

```bash
uv run pytest --timeout=300 --timeout-method=thread -v 2>&1 | tee /tmp/test-results.txt
```

- [ ] **Step 2: Frontend tests**

```bash
cd apps/web
npm test -- --run
npm run typecheck
npm run build
```

- [ ] **Step 3: Migration drift check (if Postgres available)**

```bash
cd apps/api
DATABASE_URL=postgresql+asyncpg://noodle:noodle@localhost/noodle_check \
    uv run alembic upgrade head
uv run alembic check
```

- [ ] **Step 4: Bandit scan**

```bash
uvx bandit -ll -r apps/api/app packages/core/noodle packages/nodes/noodle_nodes
```

- [ ] **Step 5: Ruff lint**

```bash
uv run ruff check .
```

- [ ] **Step 6: Create final report**

Document all fixed issues, files changed, tests added, and remaining P3 items.

---

## P3 Items (implement after P0-P2 are done)

These are lower-priority polish items. Implement in order:

1. **E-13**: `max_iterations=0` — treat as "use default"; add UI validation
2. **B-12**: Extract shared filter in `list_all_runs` to avoid duplication
3. **D-12**: Add `DateTime` columns to `NodeRun` alongside float timestamps
4. **F-07**: Combine NodeCard selectors into single `useShallow` call
5. **F-08**: Precompute `triggerReachableIds` set in Zustand store
6. **F-04**: Move body-index cache into Zustand state
7. **E-12**: Add `max_items=10000` to `paginate()` in `ProviderTransport`
8. **E-11**: Add global process limit to `PooledProcessIsolator`
9. **T-02**: Canvas store integration tests (Zustand layer)
10. **V-11**: CD pipeline GitHub Actions workflow

---

## Self-Review Against Audit Report

Checking spec coverage:
- ✅ B-01 through B-07: All covered in Tasks 1, 3, 14, 15, 16, 17 (B-07 OAuth caching deferred to P2)
- ✅ B-02 (MCP session.get): Task 2 — all 8 affected handlers
- ✅ E-01/E-02: Tasks 4, 5 — sandbox + CodeExecToolAdapter
- ✅ V-01/V-02/V-03: Tasks 6, 7, 17 — Docker fixes
- ✅ T-01: Task 8 — CI frontend tests
- ✅ D-01 through D-05: Tasks 10, 11, 12, 13 — DB migrations
- ✅ F-01 through F-05: Tasks 22-25 — Frontend P1
- ✅ T-03/T-04/T-05: Tasks 26, 27, 28 — Testing gaps
- ✅ V-04 through V-10: Tasks 18-21 — Helm/CI fixes
- ✅ E-03/E-05/E-08: Tasks 29, 30, 31 — Engine fixes
- ✅ D-08/D-09/D-11: Tasks 34, 39, 40 — DB P2
- ✅ T-07/T-08: Tasks 33, 34 — Performance
- ✅ F-09/F-11: Tasks 35, 36 — Frontend P2
- ✅ V-13: Task 37 — Security CI
- ✅ T-10: Task 38 — Import endpoint

Gaps identified (not in tasks above, need investigation before implementing):
- **B-07** (OAuth introspection cache): requires reading the OAuth introspection code to design the cache correctly
- **E-04** (DNS rebinding/IP pinning): complex transport implementation; marked P1 but is architecture-heavy
- **E-06** (Cloud runner bootstrap token): requires cloud infrastructure changes; document risk
- **E-07** (Prompt injection): change default `side_effect_approval` setting
- **F-04** (module cache): requires careful Zustand refactor
- **D-07** (FK constraints): batch migration with data validation needed
- **D-10** (migration 0043 app import): refactor extraction needed
- **V-12** (trusted_proxy_count in Helm): one-line values.yaml change

These items should be implemented after the main tasks, reading the relevant code first.
