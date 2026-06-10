# Noodle Production Architecture Program — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Program note:** Phases 0–1 are keystroke-detailed and executable as-is. Phases 2–7 are larger subsystems: their scope, interfaces, file maps, ordering, and acceptance criteria are locked here, but each MUST get its own detailed plan (via superpowers:writing-plans) before execution. Do not start a Phase 2+ task from this document alone.

**Goal:** Harden Noodle from a strong v1 into a production-grade platform by closing the gaps found in the 2026-06-10 architecture review: control-plane sandbox isolation, worker extraction, engine scheduler parallelism, frontend data-layer reliability, and an E2E safety net.

**Architecture:** Keep the existing FastAPI + DB-queue + warm-subprocess + React Flow foundation. Changes are extractions and seam-hardening, not redesigns: move user-expression evaluation out of the API process, split execution into a standalone worker role behind a `RunExecutor` interface, replace the engine's level-barrier scheduler with dependency-counting, and migrate the frontend to TanStack Query page-by-page.

**Tech Stack:** FastAPI, SQLAlchemy async, Postgres/SQLite, Redis pub/sub, Python subprocess runtime, React 18 + Zustand + TanStack Query, Playwright, OpenTelemetry.

**Review source:** Architecture review delivered 2026-06-10 in-session (findings A1–A5, B1–B5, C1–C3, D1–D3, E). Cross-references: `docs/architecture-improvement-plan.md`, `docs/multi-tenancy-plan.md`, `docs/frontend-audit.md`.

**Validation commands (Windows dev machine — `uv run` is broken here, use the built venv):**

```powershell
# Backend / core / nodes — run from each package dir so `app`/`noodle` import
cd apps\api;        ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd packages\core;   ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd packages\nodes;  ..\..\.venv\Scripts\python.exe -m pytest tests -q
# Frontend — from apps\web
npm run typecheck; npm run test; npm run build
```

---

## Phase map and ordering

| Phase | Review items | Outcome | Depends on |
|---|---|---|---|
| 0 | B3, D3 | Engine status-ranking fix + Playwright smoke suite (safety net for everything after) | — |
| 1 | C1 | Expression preview evaluated in an isolated, secret-free subprocess | — |
| 2 | B2, B1, B4, B5 | Engine split into modules; dependency-counting scheduler; pool ownership moved to host; output-measure fast path | 0 |
| 3 | A2, A1, A4 | `RunExecutor` interface; standalone worker process role; Celery retired | 0, 2 |
| 4 | A3 | Sub-workflow resolution via engine callback (same semantics everywhere) | 2, 3 |
| 5 | A5 | OpenTelemetry tracing across enqueue → lease → execute → node | 3 |
| 6 | D2, D1 | TanStack Query data layer; store/NodeDetails decomposition | 0 |
| 7 | C2, C3, E | Multi-tenancy launch gate: cross-org loop tests, cookie+CSRF auth, org-KEK (Phase E of MT plan), license decision | 1, 3 |

Phases 1, 2→3→4→5, and 6 are independent tracks and can proceed in parallel. Phase 7 is the MT launch gate and lands last.

---

## Phase 0: Safety net (detailed — execute from this document)

### Task 0.1: Engine run-status precedence ranking (B3)

`_execute_nodes` overwrites `run_status` with *any* non-success status, so a gathered level containing both an `error` node and a `waiting` node ends as whichever finished last. Make "worst status" an explicit ranking: `error > waiting > success`.

**Files:**
- Modify: `packages/core/noodle/engine.py` (`_execute_nodes`, ~lines 1976–2022; also the same overwrite pattern at ~1990)
- Test: `packages/core/tests/test_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# append to packages/core/tests/test_engine.py
from noodle.engine import _worse_status
from noodle.models import RunStatus


def test_worse_status_ranking():
    # error outranks waiting outranks success, regardless of argument order
    assert _worse_status(RunStatus.success, RunStatus.waiting) is RunStatus.waiting
    assert _worse_status(RunStatus.waiting, RunStatus.success) is RunStatus.waiting
    assert _worse_status(RunStatus.waiting, RunStatus.error) is RunStatus.error
    assert _worse_status(RunStatus.error, RunStatus.waiting) is RunStatus.error
    assert _worse_status(RunStatus.error, RunStatus.success) is RunStatus.error
    assert _worse_status(RunStatus.success, RunStatus.success) is RunStatus.success
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages\core; ..\..\.venv\Scripts\python.exe -m pytest tests/test_engine.py::test_worse_status_ranking -v`
Expected: FAIL with `ImportError: cannot import name '_worse_status'`

- [ ] **Step 3: Implement the ranking and use it at every aggregation site**

```python
# packages/core/noodle/engine.py — add near the RunStatus imports
_STATUS_RANK: dict[RunStatus, int] = {
    RunStatus.success: 0,
    RunStatus.waiting: 1,
    RunStatus.error: 2,
}


def _worse_status(a: RunStatus, b: RunStatus) -> RunStatus:
    """The more severe of two run statuses: error > waiting > success."""
    return a if _STATUS_RANK[a] >= _STATUS_RANK[b] else b
```

In `_execute_nodes._one` replace **both** occurrences of:

```python
if st is not RunStatus.success:
    run_status = st
```

with:

```python
run_status = _worse_status(run_status, st)
```

Then grep the whole file for the same overwrite pattern inside the loop drivers (`_run_loop`, `_run_conditional_loop`, `_run_metanode` call sites) and apply `_worse_status` anywhere a status accumulator is overwritten unconditionally by a non-success value.

- [ ] **Step 4: Run the full engine suite**

Run: `cd packages\core; ..\..\.venv\Scripts\python.exe -m pytest tests -q`
Expected: all PASS (including the new test and `test_branch_order_depends_on_insertion_not_position`)

- [ ] **Step 5: Commit**

```bash
git add packages/core/noodle/engine.py packages/core/tests/test_engine.py
git commit -m "fix(engine): rank run-status aggregation error > waiting > success"
```

### Task 0.2: Playwright E2E smoke suite (D3)

A 3-test critical-path suite: auth setup, workflow authoring, run-and-observe. This is the regression net for Phases 2, 3, and 6.

**Files:**
- Create: `apps/web/e2e/playwright.config.ts`
- Create: `apps/web/e2e/smoke.spec.ts`
- Modify: `apps/web/package.json` (devDependency `@playwright/test`, script `test:e2e`)
- Modify: `.github/workflows/ci.yml` (new `e2e` job)

- [ ] **Step 1: Install Playwright**

Run from `apps/web`: `npm install -D @playwright/test && npx playwright install chromium`

- [ ] **Step 2: Write the config — boots API (fresh SQLite DB) + Vite dev server**

```typescript
// apps/web/e2e/playwright.config.ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  use: { baseURL: "http://localhost:5173", trace: "retain-on-failure" },
  webServer: [
    {
      // Fresh DB per e2e session so the first-user setup flow is deterministic.
      command:
        "python -m uvicorn app.main:app --port 8000",
      cwd: "../../apps/api",
      env: { NOODLE_DATABASE_URL: "sqlite+aiosqlite:///./e2e.db" },
      url: "http://localhost:8000/health",
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: "npm run dev",
      url: "http://localhost:5173",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
```

> Executor note: delete `apps/api/e2e.db` in a `globalSetup` (or pre-test script) so reruns start clean; confirm the health endpoint path in `app/routers/health.py` and the exact `NOODLE_*` env names in `app/config.py` before wiring.

- [ ] **Step 3: Write the three smoke tests**

```typescript
// apps/web/e2e/smoke.spec.ts
import { test, expect } from "@playwright/test";

const EMAIL = "owner@e2e.local";
const PASSWORD = "e2e-password-123";

test.describe.configure({ mode: "serial" });

test("first-user setup creates the owner account and lands on workflows", async ({ page }) => {
  await page.goto("/");
  // Fresh DB → setup/register form. Selectors must be confirmed against
  // the rendered auth page (apps/web/src — login/setup component).
  await page.getByLabel(/email/i).fill(EMAIL);
  await page.getByLabel(/password/i).first().fill(PASSWORD);
  await page.getByRole("button", { name: /create|sign up|get started/i }).click();
  await expect(page.getByRole("link", { name: /workflows/i })).toBeVisible();
});

test("create a workflow and add a node from the palette", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /new workflow/i }).click();
  await expect(page).toHaveURL(/editor/);
  await page.getByPlaceholder(/search/i).fill("Manual Trigger");
  await page.getByText("Manual Trigger", { exact: false }).first().click();
  // node lands on canvas
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
  await page.getByRole("button", { name: /save/i }).click();
});

test("run the workflow and see a successful node result", async ({ page }) => {
  await page.goto("/");
  await page.getByText(/untitled|new workflow/i).first().click();
  await page.getByRole("button", { name: /^run/i }).click();
  // success badge / status driven by the run-event WebSocket
  await expect(page.getByText(/success/i).first()).toBeVisible({ timeout: 30_000 });
});
```

> Executor note: the selectors above are best-effort from the component names; adjust against the live DOM (use `npx playwright codegen http://localhost:5173` once). Keep the three behaviors fixed: setup→authenticated shell, palette→node-on-canvas→save, run→success-visible.

- [ ] **Step 4: Add script and run**

Add to `apps/web/package.json` scripts: `"test:e2e": "playwright test --config e2e/playwright.config.ts"`.
Run: `npm run test:e2e`
Expected: 3 passed.

- [ ] **Step 5: Add CI job and commit**

Add an `e2e` job to `.github/workflows/ci.yml` mirroring the existing node-setup steps + `npx playwright install --with-deps chromium` + `npm run test:e2e`, uploading `playwright-report` on failure.

```bash
git add apps/web/e2e apps/web/package.json apps/web/package-lock.json .github/workflows/ci.yml
git commit -m "test(e2e): Playwright smoke suite — setup, authoring, run-observe"
```

---

## Phase 1: Expression preview isolation (C1) (detailed — execute from this document)

**Problem:** `POST /expression-preview` (`apps/api/app/routers/expressions.py:50`) runs AST-allowlisted `eval` of user input **inside the API process** — the process holding the master KEK, DB credentials, and (post-MT) every org's data. EXPR-1 proved this sandbox can be escaped. The engine-side eval already runs in env subprocesses; the preview endpoint is the remaining control-plane hole.

**Design:** a warm, dedicated preview subprocess with a JSON-lines stdin/stdout protocol (mirroring the `packages/runtime` pattern), launched with an **explicitly minimal environment** (no `NOODLE_*`, no secrets — built from scratch, not scrubbed), a per-request timeout that kills and restarts the worker, and zero `app.*` imports inside the worker. An escape now lands in a process that knows nothing.

**Files:**
- Create: `packages/core/noodle/expr_preview_worker.py` (worker entrypoint; imports only `noodle.expr` + `noodle.serialization`)
- Create: `apps/api/app/services/expr_preview.py` (subprocess manager)
- Modify: `apps/api/app/routers/expressions.py` (delegate to the service)
- Test: `apps/api/tests/test_expr_preview_isolation.py`

### Task 1.1: Worker entrypoint

- [ ] **Step 1: Write the worker (pure, no app imports)**

```python
# packages/core/noodle/expr_preview_worker.py
"""Expression-preview worker: JSON-lines over stdin/stdout.

Runs user ``{{ }}`` expressions OUTSIDE the API process so a sandbox escape
in ``noodle.expr`` lands in a process with no secrets, no DB access, and a
minimal environment. Must never import ``app.*`` or read config.

Protocol (one JSON object per line):
  request:  {"op": "eval", "value": str, "json": any, "inputs": {}, "nodes": {}}
            {"op": "probe_env", "key": str}        # tests only
  response: {"result": any, "error": str|null, "parts": [...]}
            {"value": str|null}                     # probe_env
"""

import json
import os
import sys
from typing import Any

from noodle.expr import build_context, evaluate, evaluate_parts
from noodle.serialization import serialize_value


def _unwrap(value: Any) -> Any:
    inner = getattr(value, "_data", None)
    return inner if inner is not None else value


def _serialize_part(part: dict[str, Any]) -> dict[str, Any]:
    out = dict(part)
    if "value" in out:
        out["value"] = serialize_value(_unwrap(out["value"]))
    return out


def _handle_eval(req: dict[str, Any]) -> dict[str, Any]:
    context = build_context(
        first_input=req.get("json"),
        inputs=req.get("inputs") or {},
        node_outputs=req.get("nodes") or {},
    )
    parts = [_serialize_part(p) for p in evaluate_parts(req["value"], context)]
    try:
        result = evaluate(req["value"], context)
    except Exception as exc:  # noqa: BLE001 - any eval failure becomes a payload
        return {"result": None, "error": f"{type(exc).__name__}: {exc}", "parts": parts}
    if isinstance(result, str) and result.startswith("[expr error:"):
        return {"result": None, "error": result.strip("[]"), "parts": parts}
    return {"result": serialize_value(_unwrap(result)), "error": None, "parts": parts}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if req.get("op") == "probe_env":
                resp: dict[str, Any] = {"value": os.environ.get(req["key"])}
            else:
                resp = _handle_eval(req)
        except Exception as exc:  # noqa: BLE001 - protocol must never die silently
            resp = {"result": None, "error": f"worker: {type(exc).__name__}: {exc}", "parts": []}
        sys.stdout.write(json.dumps(resp, default=str) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add packages/core/noodle/expr_preview_worker.py
git commit -m "feat(core): expression-preview worker entrypoint (JSON-lines, no app imports)"
```

### Task 1.2: Subprocess manager service

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/test_expr_preview_isolation.py
import os

import pytest

from app.services import expr_preview


@pytest.fixture(autouse=True)
async def _fresh_worker():
    yield
    await expr_preview.shutdown()


async def test_preview_evaluates_in_subprocess():
    out = await expr_preview.preview(
        value="{{ $json.a + 1 }}", json_value={"a": 41}, inputs={}, nodes={}
    )
    assert out["error"] is None
    assert out["result"] == 42


async def test_worker_env_has_no_secrets(monkeypatch):
    monkeypatch.setenv("NOODLE_SECRET_KEY", "super-secret")
    await expr_preview.shutdown()  # force respawn under the patched env
    assert await expr_preview.probe_env("NOODLE_SECRET_KEY") is None
    assert await expr_preview.probe_env("PATH") is not None


async def test_timeout_kills_and_restarts_worker():
    out = await expr_preview.preview(
        value="{{ sum(1 for _ in range(10**10)) }}",
        json_value=None, inputs={}, nodes={}, timeout=0.5,
    )
    assert out["error"] is not None and "timeout" in out["error"].lower()
    # worker restarted: next request still works
    again = await expr_preview.preview(
        value="{{ 1 + 1 }}", json_value=None, inputs={}, nodes={}
    )
    assert again["result"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_expr_preview_isolation.py -v`
Expected: FAIL with `ModuleNotFoundError: app.services.expr_preview` (or ImportError)

- [ ] **Step 3: Implement the manager**

```python
# apps/api/app/services/expr_preview.py
"""Manages the isolated expression-preview subprocess.

The worker (``noodle.expr_preview_worker``) is spawned with a from-scratch
minimal environment — secrets are absent by construction, not scrubbed.
One warm worker, requests serialized by a lock (preview traffic is light and
sub-millisecond); a timeout kills and respawns the worker.
"""

import asyncio
import json
import os
import sys
from typing import Any

_DEFAULT_TIMEOUT = 5.0

_proc: asyncio.subprocess.Process | None = None
_lock = asyncio.Lock()


def _minimal_env() -> dict[str, str]:
    env: dict[str, str] = {}
    # Only what Python needs to start; nothing app-specific crosses over.
    for key in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "PYTHONIOENCODING"):
        if key in os.environ:
            env[key] = os.environ[key]
    env["PYTHONIOENCODING"] = "utf-8"
    return env


async def _ensure_worker() -> asyncio.subprocess.Process:
    global _proc
    if _proc is not None and _proc.returncode is None:
        return _proc
    _proc = await asyncio.create_subprocess_exec(
        sys.executable, "-u", "-m", "noodle.expr_preview_worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=_minimal_env(),
    )
    return _proc


async def _kill_worker() -> None:
    global _proc
    if _proc is not None and _proc.returncode is None:
        _proc.kill()
        await _proc.wait()
    _proc = None


async def shutdown() -> None:
    async with _lock:
        await _kill_worker()


async def _request(payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    async with _lock:
        proc = await _ensure_worker()
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps(payload, default=str).encode() + b"\n")
        await proc.stdin.drain()
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
        except TimeoutError:
            await _kill_worker()
            return {"result": None, "error": "Expression timeout — evaluation killed", "parts": []}
        if not line:  # worker died
            await _kill_worker()
            return {"result": None, "error": "Preview worker exited unexpectedly", "parts": []}
        return json.loads(line)


async def preview(
    *, value: str, json_value: Any, inputs: dict, nodes: dict,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    return await _request(
        {"op": "eval", "value": value, "json": json_value, "inputs": inputs, "nodes": nodes},
        timeout,
    )


async def probe_env(key: str) -> str | None:
    """Test hook: read an env var from inside the worker process."""
    resp = await _request({"op": "probe_env", "key": key}, _DEFAULT_TIMEOUT)
    return resp.get("value")
```

- [ ] **Step 4: Run the tests**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_expr_preview_isolation.py -v`
Expected: 3 passed. (If the timeout test is slow to die on Windows, that's `kill()` racing the generator — assert only on the response payload, which is already what the test does.)

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/expr_preview.py apps/api/tests/test_expr_preview_isolation.py
git commit -m "feat(api): isolated expression-preview subprocess with minimal env + kill-on-timeout"
```

### Task 1.3: Rewire the router

- [ ] **Step 1: Replace in-process eval with the service**

```python
# apps/api/app/routers/expressions.py — replace the handler body; keep the schemas
from app.services import expr_preview


@router.post("/expression-preview", response_model=ExpressionPreviewResponse)
async def preview_expression(
    body: ExpressionPreviewRequest,
) -> ExpressionPreviewResponse:
    out = await expr_preview.preview(
        value=body.value,
        json_value=body.json_value,
        inputs=body.inputs,
        nodes=body.nodes,
    )
    return ExpressionPreviewResponse(
        result=out.get("result"), error=out.get("error"), parts=out.get("parts", [])
    )
```

Remove the now-unused `noodle.expr` / `serialize_value` imports and the local `_unwrap`/`_serialize_part` helpers (they moved into the worker). Register `expr_preview.shutdown()` in the lifespan teardown in `app/main.py`.

- [ ] **Step 2: Run the existing expression router tests + full API suite**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests -q`
Expected: all PASS — the response shape is unchanged, so any existing `/expression-preview` tests must stay green.

- [ ] **Step 3: Manual editor check + commit**

With the dev stack up, type `{{ $json.x + 1 }}` in an inspector expression field and confirm the live preview still renders.

```bash
git add apps/api/app/routers/expressions.py apps/api/app/main.py
git commit -m "feat(api): expression preview now evaluates in the isolated worker (C1)"
```

---

## Phase 2: Engine modularization + dependency scheduler (B2, B1, B4, B5)

> **Requires its own detailed plan before execution.** Scope locked below.

**File map (B2 — split `engine.py` ~2,100 lines into a package, no behavior change):**

| New file | Moves in |
|---|---|
| `packages/core/noodle/engine/__init__.py` | `execute`, `run`, `GraphError` — public API re-exported so `from noodle.engine import execute` keeps working everywhere (API, runtime, exporter) |
| `packages/core/noodle/engine/scheduler.py` | `_execute_nodes`, `_topo_order`, `_topo_levels`, `_needed_nodes`, `_predecessors`, `_descendants`, `_ancestors`, `_worse_status` |
| `packages/core/noodle/engine/node_exec.py` | `_run_one_node`, retry/timeout/log-capture machinery, `_CaptureProxy`, `_install_capture` |
| `packages/core/noodle/engine/loops.py` | `LoopRegion`, `_loop_regions`, `_validate_loop_regions`, `_run_loop`, `_run_conditional_loop`, `_loop_items`, `_as_loop_rows`, `_restricted_levels` |
| `packages/core/noodle/engine/agent.py` | `_dispatch_agent_action_request`, tool adapters/index helpers, `_agent_approval_key` |
| `packages/core/noodle/engine/datasets.py` | `_auto_expand_dataset_inputs`, `_auto_promote_outputs`, the dataset cap constants |
| `packages/core/noodle/engine/validation.py` | `_validate_connection_kinds`, `_validate_input_kinds`, `_validate_output_kinds`, port-kind helpers |
| `packages/core/noodle/engine/metanodes.py` | `_expand_metanodes`, `_expand_graph_dict`, `_run_metanode` |
| `packages/core/noodle/engine/pools.py` | process-pool dict + eviction (then deleted in B4, below) |

**Scheduler rewrite (B1):** replace level-gather in `_execute_nodes` with dependency counting: compute remaining-indegree per needed node; maintain a ready list ordered by `graph.nodes` insertion index; run ready nodes concurrently under an `asyncio.Semaphore(max_node_concurrency)` (default unbounded to match today); on completion, decrement dependents and enqueue newly-ready ones. **Invariants that must hold (existing tests + new):** (1) `test_branch_order_depends_on_insertion_not_position` stays green — simultaneous-ready nodes start in insertion order; (2) loop regions: `owned` body/end nodes are never scheduled by the outer scheduler; (3) `loop_start`/`meta_node` interception identical to today; (4) skipped-branch propagation unchanged. Acceptance: a test graph `slow(2s) → C` ∥ `fast(0.1s) → D` where D completes before C starts (impossible under level barriers).

**Pool ownership (B4):** move `_process_pools`/`_get_process_pool`/`_evict_pool` out of the engine; the host (`packages/runtime`, `apps/api/app/services/runner.py` in-process path) constructs and injects a `ProcessIsolator` (callable: `submit(fn, *args) -> Future`) via a new `execute(..., process_isolator=...)` parameter. Eviction policy lives with the host, keyed off actual task completion times, not `get` calls — fixing the long-running-code-node cold-pool issue.

**Output-measure fast path (B5):** in the output-cap check, skip `_approx_encoded_length` when the output is `None`/bool/int/float, or a `str`/`bytes` shorter than the cap, or a `list`/`dict` whose `len()` is small and all values are scalars (cheap one-level scan). Acceptance: a benchmark test showing a 10k-iteration loop of small outputs spends <10% of prior time in measurement.

**Verification per task:** `cd packages\core; ..\..\.venv\Scripts\python.exe -m pytest tests -q` plus the full API suite (the runner imports the engine) plus the Phase 0 smoke suite.

---

## Phase 3: RunExecutor interface + worker extraction (A2, A1, A4)

> **Requires its own detailed plan before execution.** Scope locked below.

**Step 1 — executor interface (A2), pure refactor:**

```python
# apps/api/app/services/executors/base.py (new)
class RunExecutionContext(TypedDict):
    run_id: str
    workflow_id: str
    org_id: str
    graph: dict
    parameters: dict | None
    cache: dict | None
    targets: list[str] | None
    environment_id: str | None
    runner_pool_id: str | None
    credentials: dict          # decrypted at dispatch, never persisted
    default_timeouts: dict[str, float]

class RunExecutor(Protocol):
    async def execute(self, ctx: RunExecutionContext, on_event: EventCallback) -> RunOutcome: ...
    async def cancel(self, run_id: str) -> bool: ...
```

- `executors/local.py` wraps the current `runtime_pool` path; `executors/remote.py` wraps `remote_dispatch`. `runner._execute_run` shrinks to: load graph → seed params → pick executor → persist results. Provider split: `services/providers/{agent,docker,k8s}.py` extracted from `remote_dispatch.py`.
- Acceptance: full API test suite green with zero test edits (behavior-preserving), `runner.py` < 800 lines, `remote_dispatch.py` replaced by an orchestrator < 400 lines + three provider modules.

**Step 2 — worker role (A1):**

- New config: `dispatch_role: inline | worker | disabled` (default `inline` — single-process local mode unchanged, mirroring the existing `scheduler_role` pattern in `config.py`).
- New entrypoint `apps/api/app/worker_main.py`: no FastAPI routes; runs `run_queue_dispatch_loop` + `runtime_pool` + `idle_reaper_loop` + graceful drain on SIGTERM (reusing `drain_active_runs`). Started as `python -m app.worker_main`.
- API with `dispatch_role=disabled` does not start the dispatch loop or runtime pool.
- **Startup validation:** `dispatch_role=worker|disabled` requires Redis (the event broker must be cross-process — in-process fallback would strand WebSocket clients on the API replica) and Postgres (SKIP LOCKED leasing). Fail fast with a clear error, matching the existing `RUNTIME_MODE=production` warning pattern.
- `deploy/` compose + Helm gain a `worker` service; docs/deployment.md documents the topology: N api replicas (dispatch disabled) + M workers + 1 scheduler leader.
- Acceptance: e2e smoke suite passes against a split topology (api `dispatch_role=disabled` + one worker); a run started via the UI executes on the worker and streams events to the browser through Redis.

**Step 3 — retire Celery (A4):** delete `apps/worker` (Celery), fold any remaining Beat-only schedule into the leader-elected scheduler; remove celery deps from lockfiles; update `docs/architecture.md`. Acceptance: `rg -i celery` returns only changelog/docs history.

---

## Phase 4: Sub-workflow resolution via engine callback (A3)

> **Requires its own detailed plan before execution.** Scope locked below.

- Engine gains `execute(..., subworkflow_runner: SubworkflowRunner | None)` where `SubworkflowRunner = Callable[[SubworkflowCall], Awaitable[dict[str, Any]]]` and `SubworkflowCall` carries `workflow_id`, `use_published: bool`, `parameters`, `parent_run_id`, `depth`.
- The `workflow_call` node type is executed by the engine through this callback instead of being spliced by `runner.InlineSubWorkflow` / `_call_sub_workflow`. Each host injects its resolver: API (DB lookup + child Run rows + org caps via `max_inflight_subworkflows`), runtime subprocess (RPC back to host), exporter (bundled graphs resolved locally).
- **Must preserve:** the global-concurrency-cap bypass for child runs (`HANDOFF.md` §7 deadlock), org sub-workflow caps from `OrgSettings`, and depth limiting.
- Acceptance: existing sub-workflow API tests green; new core test exercising a sub-workflow through a stub resolver; exporter round-trip test for a workflow containing `workflow_call`.

---

## Phase 5: OpenTelemetry tracing (A5)

> **Requires its own detailed plan before execution.** Scope locked below.

- Deps: `opentelemetry-sdk`, `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-sqlalchemy`, OTLP exporter. Config: `otel_enabled: bool = False`, `otel_exporter_otlp_endpoint: str = ""` — off by default, zero overhead when off.
- Span tree per run: `run.enqueue` → `run.lease` (worker) → `run.execute` → `node.execute` (one span per node, attributes: `node_id`, `node_type`, `status`, `org_id`, `iteration_path`). Trace context propagated through `RunQueueEntry` (a `trace_context` JSON column, additive migration) and into the runtime subprocess via the existing event/env payload channel.
- Acceptance: with a local OTLP collector, one manual run produces a single connected trace across API → worker → subprocess; `/ops` docs mention the setting.

---

## Phase 6: Frontend data layer + decomposition (D2, D1)

> **Requires its own detailed plan before execution.** Scope locked below.

**TanStack Query migration (D2) — page-by-page, never big-bang:**

- Add `@tanstack/react-query`; mount one `QueryClientProvider` in `App.tsx` (defaults: `staleTime: 30_000`, `retry: 1`, `refetchOnWindowFocus: false` — match current UX).
- Create `apps/web/src/queries/` with typed hooks wrapping the existing `api.ts` functions (keep `api.ts` as the transport; queries are the lifecycle layer): `useWorkflows`, `useRuns(workflowId)`, `useEnvironments`, `useCredentials`, `useRunnerPools`, `useOrgUsage`, plus matching mutations with invalidation.
- Migration order (lowest risk → highest): `WorkflowsPage` → `ExecutionsPage` (replaces its polling effects) → `EnvironmentsPage` (install poller from FE-12 becomes `refetchInterval`) → `CredentialsPage` → `DeploymentsPage` / `RunnerPoolsPage` / `OrganizationPage` → editor-adjacent reads (palette manifests, pinned data). **WebSocket run-event streaming stays in the Zustand store** — it is push state, not request state.
- Per-page acceptance: page renders identically, no leaked pollers on unmount (FE-12 class), typecheck + unit tests + e2e smoke green.

**Editor decomposition (D1):**

- `editor/store.ts` (2,099 lines) → Zustand slice files composed into the same store hook (public API unchanged so components don't churn): `store/graphSlice.ts` (nodes/edges/params), `store/runSlice.ts` (run events/status/meta), `store/childWorkflowSlice.ts`, `store/clipboardSlice.ts`, `store/index.ts`. The 28 existing store tests must pass unmodified — they are the refactor contract.
- `editor/NodeDetails.tsx` (3,543 lines) → `editor/node-details/` directory split along its existing panel boundaries (params form, settings tab, modals — exact boundaries mapped during that phase's planning); parent keeps the public component export.
- CSS: no rewrite; add `editor/tokens.css` (extract the repeated color/spacing/z-index values) and require new components to use module-scoped CSS. Dead-style cleanup is opportunistic, not a goal.

---

## Phase 7: Multi-tenancy launch gate (C2, C3, MT Phase E, license)

> **Requires its own detailed plan before execution; coordinates with `docs/multi-tenancy-plan.md`.** This phase is the release gate: MT must not be enabled for untrusted orgs until every box below is checked.

- **C2 — cross-org background-loop tests:** API test module that enables `multi_tenancy_enabled`, creates two orgs with queued runs/retention-expired runs/due schedules, and asserts each loop (queue dispatch, retention, scheduler, heartbeats) processes both orgs *and* writes rows stamped with the correct `org_id`. Plus a CI guard script asserting every `SessionLocal()` call site in `services/{queue,triggers,retention,remote_dispatch,runner}.py` background loops sits inside `run_as_system()`.
- **C3 — session hardening:** httpOnly secure cookie sessions + CSRF (double-submit token) + strict CSP, replacing localStorage bearer tokens (closes FE-1). WebSocket auth moves to cookie or one-time ticket. Keep bearer support for the runner agent API only.
- **MT Phase E — org-KEK envelope:** per the existing `multi-tenancy-plan.md`: populate `Organization.wrapped_org_kek`, re-wrap credential DEKs under org KEKs, migration + rotation runbook. (Already designed there — this program just sequences it as a launch blocker.)
- **Expression preview isolation (Phase 1) confirmed deployed** — it is a hard MT precondition.
- **License decision (AGPL vs BSL)** — tracked in `status-matrix.md` as the last "Planned" governance item; production release blocks on it.

---

## Self-review notes

- Spec coverage: A1→3, A2→3, A3→4, A4→3, A5→5, B1/B2/B4/B5→2, B3→0.1, C1→1, C2/C3→7, D1/D2→6, D3→0.2, E→7. All review findings have a phase.
- Phase 0–1 steps carry complete code; Phases 2–7 are deliberately specification-grade and flagged as requiring their own writing-plans pass — do not execute them from this document.
- Known executor adjustments: e2e selectors (Task 0.2 Step 3) and the API health-endpoint/env-var names (Task 0.2 Step 2) must be confirmed against the live app; flagged inline.
