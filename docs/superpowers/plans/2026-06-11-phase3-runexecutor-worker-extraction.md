# Phase 3: RunExecutor Interface + Worker Extraction + Celery Retirement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract run execution behind a `RunExecutor` interface (A2), add a standalone worker process role via `dispatch_role` (A1), and retire the Celery worker (A4) — Phase 3 of `2026-06-10-production-architecture-program.md`.

**Architecture:** Three stages, each independently shippable. Stage A is a behavior-preserving refactor: `runner._execute_run` delegates dispatch to `LocalExecutor`/`RemoteExecutor` adapters, persistence/alert/resume helpers move to sibling modules with explicit dependency injection (so conftest's `SessionLocal` monkeypatching keeps working with **zero test edits**), and `RemoteDispatcher`'s three provider paths move to `services/providers/{agent,docker,k8s}.py` as stateless function modules (connection state stays on the dispatcher facade — tests poke `dispatcher._agents` directly). Stage B adds `dispatch_role: inline|worker|disabled`, provider-aware queue leasing, lost-worker run recovery, cross-process cancel reconciliation, and the `python -m app.worker_main` entrypoint. Stage C deletes `apps/worker` (Celery), rewires compose/Helm to the new worker, and sweeps docs.

**Tech Stack:** FastAPI, SQLAlchemy async, Postgres (SKIP LOCKED leasing), Redis pub/sub (cross-process run events), asyncio.

**Validation commands (Windows dev machine — `uv run` is broken here, use the built venv):**

```powershell
cd D:\noodle\apps\api;       D:\noodle\.venv\Scripts\python.exe -m pytest tests -q
cd D:\noodle\packages\core;  D:\noodle\.venv\Scripts\python.exe -m pytest tests -q
```

Known pre-existing failure (NOT caused by this work, fails at base too): `tests/test_architecture_fixes.py::test_dispatch_webhook_passes_shared_session_to_resolve_node_auth`. "Suite green" below means *544+ passed and only this one failure*.

---

## Locked constraints (from research — violating any of these breaks tests)

1. **conftest swaps `SessionLocal` per module** (`apps/api/tests/conftest.py:174-197`). Any moved code that opens DB sessions must receive its session factory **as a call-time argument from `runner.py` / `remote_dispatch.py` module globals** (name lookup at call time picks up the patch). New modules must NOT do `from app.db import SessionLocal` and use it for runtime work.
2. **Tests patch `runner_module.execute`** (`tests/test_runs.py:703` etc. — the *engine* `execute`). The in-process (`use_subprocess_runner=False`) engine invocation **stays in `runner.py`**. Only the subprocess `runtime_pool` path and remote path move into executors.
3. **Tests patch `runner_module.start_run`** (`test_architecture_fixes.py:986`) — error-handler dispatch must resolve `start_run` from `runner` module globals at call time.
4. **Tests poke dispatcher internals**: `dispatcher._agents`, `dispatcher._lock`, `dispatcher._run_callbacks`, `dispatcher._pick_agent`, `dispatcher._handle_agent_message`, `dispatcher.mark_stale_runners_offline`, `dispatcher.ping_connected_agents`, and construct `RemoteDispatcher()` fresh (`test_audit_wave2.py`, `test_runner_pools.py`). All of these names must survive on the class.
5. **Import surface to preserve** (re-export if moved): `app.services.runner`: `start_run`, `cancel_run`, `resume_waiting_run_from_approval`, `_execute_queued_entry`, `_active_runs`, `SessionLocal`, `_RunIdFilter`, `_log_run_id`, `InlineSubWorkflow` (lazy import in `runtime_pool.py:338`), `process_isolator`, `drain_active_runs`, `shutdown_active_runs`. `app.services.remote_dispatch`: `dispatcher`, `RemoteDispatcher`, `_AgentConnection`, `build_env_payload`, `_validate_packages`, `_validate_python_version`, `_QueuedError`, `cloud_idle_terminate_loop`, `runner_heartbeat_loop`, `SessionLocal`, `EventCallback`.
6. **`_call_sub_workflow` and its cluster stay in `runner.py`** — Phase 4 (A3) moves sub-workflow resolution into an engine callback; moving it twice is churn, and its `workflow_caller` ContextVar signature admits no DI.
7. Agent and Kubernetes providers depend on WebSocket connections terminating in the API process (`_agents` / `_k8s_futures` resolved by WS routes). A standalone worker can execute **local** and **docker** runs only; this is enforced by provider-aware leasing and documented.

---

## Stage A — RunExecutor interface + module split (A2)

### Task A1: Executor types and protocol

**Files:**
- Create: `apps/api/app/services/executors/__init__.py`
- Create: `apps/api/app/services/executors/base.py`
- Test: `apps/api/tests/test_executors.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_executors.py
"""RunExecutor seam (program A2): the executor protocol + adapters."""


def test_executor_protocol_shape():
    from app.services.executors.base import (
        RunExecutionContext,
        RunExecutor,
        RunOutcome,
    )

    ctx: RunExecutionContext = {
        "run_id": "r1",
        "workflow_id": "w1",
        "graph": {"nodes": [], "edges": []},
        "cache": None,
        "targets": None,
        "environment_id": None,
        "runner_pool_id": None,
        "env_payload": None,
        "workflow_modules": [],
        "run_timeout": None,
        "default_timeouts": {},
        "pause_on_approval": True,
        "agent_action_resume": None,
    }
    assert ctx["run_id"] == "r1"
    assert RunOutcome(status="success").status == "success"
    assert hasattr(RunExecutor, "execute") and hasattr(RunExecutor, "cancel")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd D:\noodle\apps\api; D:\noodle\.venv\Scripts\python.exe -m pytest tests/test_executors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.executors'`

- [ ] **Step 3: Implement**

```python
# apps/api/app/services/executors/__init__.py
```
(empty file)

```python
# apps/api/app/services/executors/base.py
"""RunExecutor seam (program review A2).

An executor takes a fully-prepared run (credentials already resolved into the
graph, env payload built, modules gathered) and produces a terminal status.
It owns NO persistence and NO DB sessions — `runner._execute_run` remains the
single place that loads context and persists outcomes, so the executor can be
swapped (local pool today, remote pool, future RPC worker) without touching
run bookkeeping.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict, runtime_checkable

EventCallback = Callable[[dict], Awaitable[None]]


class RunExecutionContext(TypedDict):
    run_id: str
    workflow_id: str
    graph: dict                      # credential refs already resolved
    cache: dict | None
    targets: list[str] | None
    environment_id: str | None
    runner_pool_id: str | None
    env_payload: dict | None         # remote runs only; built by the host
    workflow_modules: list[dict]
    run_timeout: float | None
    default_timeouts: dict[str, float]
    pause_on_approval: bool
    agent_action_resume: dict[str, Any] | None  # node_id -> serialized request


@dataclass(frozen=True)
class RunOutcome:
    status: str  # success | error | waiting | cancelled


@runtime_checkable
class RunExecutor(Protocol):
    async def execute(
        self, ctx: RunExecutionContext, on_event: EventCallback
    ) -> RunOutcome: ...

    async def cancel(self, run_id: str) -> bool: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd D:\noodle\apps\api; D:\noodle\.venv\Scripts\python.exe -m pytest tests/test_executors.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/executors apps/api/tests/test_executors.py
git commit -m "feat(api): RunExecutor protocol + execution context types (A2)"
```

### Task A2: LocalExecutor — wrap the runtime_pool path

**Files:**
- Create: `apps/api/app/services/executors/local.py`
- Modify: `apps/api/app/services/runner.py` (the `runner_pool_id is None` subprocess branch of `_execute_run`, ~lines 1294-1314)
- Test: append to `apps/api/tests/test_executors.py`

- [ ] **Step 1: Write the failing test**

```python
# append to apps/api/tests/test_executors.py
import asyncio


async def test_local_executor_delegates_to_pool_and_cancels_task():
    from app.services.executors.base import RunOutcome
    from app.services.executors.local import LocalExecutor

    calls: dict = {}

    class FakePool:
        async def dispatch(self, run_id, env_id, graph, cache, targets, on_event,
                           **kwargs):
            calls["dispatch"] = (run_id, env_id, kwargs.get("run_timeout"))
            return "success"

    active: dict[str, asyncio.Task] = {}
    ex = LocalExecutor(
        pool=FakePool(),
        sub_workflow_caller=lambda *a, **k: None,
        active_runs=active,
    )

    async def on_event(_event: dict) -> None: ...

    out = await ex.execute(
        {
            "run_id": "r1", "workflow_id": "w1",
            "graph": {"nodes": [], "edges": []},
            "cache": None, "targets": None,
            "environment_id": "env-9", "runner_pool_id": None,
            "env_payload": None, "workflow_modules": [],
            "run_timeout": 12.5, "default_timeouts": {},
            "pause_on_approval": True, "agent_action_resume": None,
        },
        on_event,
    )
    assert out == RunOutcome(status="success")
    assert calls["dispatch"] == ("r1", "env-9", 12.5)

    # cancel() cancels the registered task for the run id
    async def _hang():
        await asyncio.sleep(60)

    task = asyncio.ensure_future(_hang())
    active["r2"] = task
    assert await ex.cancel("r2") is True
    await asyncio.sleep(0)
    assert task.cancelled() or task.cancelling()
    assert await ex.cancel("missing") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd D:\noodle\apps\api; D:\noodle\.venv\Scripts\python.exe -m pytest tests/test_executors.py -v`
Expected: FAIL with `ModuleNotFoundError` for `executors.local`

- [ ] **Step 3: Implement LocalExecutor**

```python
# apps/api/app/services/executors/local.py
"""Local execution: warm per-env subprocess via the host's RuntimePool."""

from __future__ import annotations

import asyncio
from typing import Any

from app.services.executors.base import (
    EventCallback,
    RunExecutionContext,
    RunOutcome,
)


class LocalExecutor:
    """Adapter over ``runtime_pool.pool`` (the warm subprocess pool).

    No DB access. ``active_runs`` is runner.py's run-id → asyncio.Task
    registry; cancel() works by cancelling the awaiting task, exactly like
    ``cancel_run`` does today.
    """

    def __init__(self, *, pool: Any, sub_workflow_caller: Any,
                 active_runs: dict[str, asyncio.Task]) -> None:
        self._pool = pool
        self._sub_workflow_caller = sub_workflow_caller
        self._active_runs = active_runs

    async def execute(
        self, ctx: RunExecutionContext, on_event: EventCallback
    ) -> RunOutcome:
        status = await self._pool.dispatch(
            ctx["run_id"],
            ctx["environment_id"],
            ctx["graph"],
            ctx["cache"],
            ctx["targets"],
            on_event,
            sub_workflow_caller=self._sub_workflow_caller,
            workflow_modules=ctx["workflow_modules"],
            run_timeout=ctx["run_timeout"],
            pause_on_approval=ctx["pause_on_approval"],
            agent_action_resume=ctx["agent_action_resume"],
        )
        return RunOutcome(status=str(status))

    async def cancel(self, run_id: str) -> bool:
        task = self._active_runs.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            return True
        return False
```

- [ ] **Step 4: Wire it into `runner.py`**

In `runner.py`, near `process_isolator = PooledProcessIsolator()`, add:

```python
from app.services.executors.base import RunExecutionContext, RunOutcome
from app.services.executors.local import LocalExecutor

local_executor = LocalExecutor(
    pool=runtime_pool,
    sub_workflow_caller=_call_sub_workflow,
    active_runs=_active_runs,
)
```

(Place the construction **after** `_call_sub_workflow` is defined — module order matters. `_active_runs` is shared by reference, so conftest's `.clear()` resets both views.)

In `_execute_run`, replace the `else:` local-dispatch arm of the subprocess branch (currently `status = await runtime_pool.dispatch(run_id, env_id, graph_dict, ...)`) with:

```python
                else:
                    outcome = await local_executor.execute(
                        _build_ctx(
                            run_id=run_id, workflow_id=workflow_id,
                            graph=graph_dict, cache=cache, targets=targets,
                            environment_id=env_id, runner_pool_id=None,
                            env_payload=None, workflow_modules=workflow_modules,
                            run_timeout=run_timeout,
                            agent_action_resume=agent_action_resume,
                        ),
                        on_event,
                    )
                    status = outcome.status
```

And add this helper near `_engine_default_timeouts`:

```python
def _build_ctx(
    *, run_id: str, workflow_id: str, graph: dict, cache: dict | None,
    targets: list[str] | None, environment_id: str | None,
    runner_pool_id: str | None, env_payload: dict | None,
    workflow_modules: list[dict], run_timeout: float | None,
    agent_action_resume: dict[str, AgentActionRequest] | None,
) -> RunExecutionContext:
    return {
        "run_id": run_id, "workflow_id": workflow_id, "graph": graph,
        "cache": cache, "targets": targets, "environment_id": environment_id,
        "runner_pool_id": runner_pool_id, "env_payload": env_payload,
        "workflow_modules": workflow_modules, "run_timeout": run_timeout,
        "default_timeouts": _engine_default_timeouts(),
        "pause_on_approval": True,
        "agent_action_resume": (
            {nid: req.model_dump(mode="json")
             for nid, req in agent_action_resume.items()}
            if agent_action_resume else None
        ),
    }
```

- [ ] **Step 5: Run executor tests + full API suite**

Run: `cd D:\noodle\apps\api; D:\noodle\.venv\Scripts\python.exe -m pytest tests -q`
Expected: suite green (544+ passed, only the known pre-existing failure).

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/executors/local.py apps/api/app/services/runner.py apps/api/tests/test_executors.py
git commit -m "feat(api): LocalExecutor wraps the runtime_pool dispatch path (A2)"
```

### Task A3: RemoteExecutor — wrap remote dispatch

**Files:**
- Create: `apps/api/app/services/executors/remote.py`
- Modify: `apps/api/app/services/runner.py` (`if runner_pool_id:` branch of `_execute_run`, ~lines 1245-1293, and `cancel_run`)
- Test: append to `apps/api/tests/test_executors.py`

- [ ] **Step 1: Write the failing test**

```python
# append to apps/api/tests/test_executors.py
async def test_remote_executor_delegates_to_dispatcher():
    from app.services.executors.base import RunOutcome
    from app.services.executors.remote import RemoteExecutor

    calls: dict = {}

    class FakeDispatcher:
        async def assign_run(self, run_id, pool_id, env_payload, graph, cache,
                             targets, workflow_modules, on_event,
                             pause_on_approval=False, agent_action_resume=None):
            calls["assign"] = (run_id, pool_id, env_payload)
            return "success"

        async def cancel_remote_run(self, run_id, runner_id):
            calls["cancel"] = (run_id, runner_id)

    async def fake_runner_id(run_id):
        return "runner-7"

    ex = RemoteExecutor(dispatcher=FakeDispatcher(), runner_id_for=fake_runner_id)

    async def on_event(_event: dict) -> None: ...

    out = await ex.execute(
        {
            "run_id": "r1", "workflow_id": "w1",
            "graph": {"nodes": [], "edges": []},
            "cache": None, "targets": None,
            "environment_id": None, "runner_pool_id": "pool-1",
            "env_payload": {"id": "default"}, "workflow_modules": [],
            "run_timeout": None, "default_timeouts": {},
            "pause_on_approval": True, "agent_action_resume": None,
        },
        on_event,
    )
    assert out == RunOutcome(status="success")
    assert calls["assign"] == ("r1", "pool-1", {"id": "default"})

    assert await ex.cancel("r1") is True
    assert calls["cancel"] == ("r1", "runner-7")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd D:\noodle\apps\api; D:\noodle\.venv\Scripts\python.exe -m pytest tests/test_executors.py -v`
Expected: FAIL (`executors.remote` missing)

- [ ] **Step 3: Implement RemoteExecutor**

```python
# apps/api/app/services/executors/remote.py
"""Remote execution: agent / docker / kubernetes runner pools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.services.executors.base import (
    EventCallback,
    RunExecutionContext,
    RunOutcome,
)


class RemoteExecutor:
    """Adapter over ``remote_dispatch.dispatcher``.

    ``_QueuedError`` (no runner capacity) propagates to the caller —
    ``runner._execute_run`` owns the requeue-with-backoff transition exactly
    as it does today. ``runner_id_for`` resolves the agent a run was pinned
    to; injected so this module needs no DB session of its own (the runner
    module's patched ``SessionLocal`` does the lookup).
    """

    def __init__(self, *, dispatcher: Any,
                 runner_id_for: Callable[[str], Awaitable[str | None]]) -> None:
        self._dispatcher = dispatcher
        self._runner_id_for = runner_id_for

    async def execute(
        self, ctx: RunExecutionContext, on_event: EventCallback
    ) -> RunOutcome:
        status = await self._dispatcher.assign_run(
            ctx["run_id"],
            ctx["runner_pool_id"],
            ctx["env_payload"],
            ctx["graph"],
            ctx["cache"],
            ctx["targets"],
            ctx["workflow_modules"],
            on_event,
            pause_on_approval=ctx["pause_on_approval"],
            agent_action_resume=ctx["agent_action_resume"],
        )
        return RunOutcome(status=str(status))

    async def cancel(self, run_id: str) -> bool:
        runner_id = await self._runner_id_for(run_id)
        if not runner_id:
            return False
        await self._dispatcher.cancel_remote_run(run_id, runner_id)
        return True
```

- [ ] **Step 4: Wire it into `runner.py`**

Next to `local_executor`, add:

```python
async def _runner_id_for(run_id: str) -> str | None:
    async with SessionLocal() as session:
        run = await session.get(Run, run_id)
        return run.runner_id if run else None


remote_executor = RemoteExecutor(dispatcher=dispatcher, runner_id_for=_runner_id_for)
```

In `_execute_run`, the `if runner_pool_id:` branch becomes (the `_QueuedError` requeue block stays **verbatim** where it is):

```python
                if runner_pool_id:
                    env_payload = await _build_env_payload_for_run(env_id)
                    try:
                        outcome = await remote_executor.execute(
                            _build_ctx(
                                run_id=run_id, workflow_id=workflow_id,
                                graph=graph_dict, cache=cache, targets=targets,
                                environment_id=env_id,
                                runner_pool_id=runner_pool_id,
                                env_payload=env_payload,
                                workflow_modules=workflow_modules,
                                run_timeout=run_timeout,
                                agent_action_resume=agent_action_resume,
                            ),
                            on_event,
                        )
                        status = outcome.status
                    except _QueuedError as queued_exc:
                        ...  # existing requeue/dead-letter block, unchanged
```

In `cancel_run`, replace the `dispatcher.cancel_remote_run(run_id, runner_id)` call with `await remote_executor.cancel(run_id)` inside the same best-effort `try/except` (the `runner_id` pre-check can drop — `cancel` no-ops when the run has no runner).

- [ ] **Step 5: Run the remote-pool tests + full suite**

Run: `cd D:\noodle\apps\api; D:\noodle\.venv\Scripts\python.exe -m pytest tests/test_runner_pools.py tests/test_executors.py -q` then the full suite.
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/executors/remote.py apps/api/app/services/runner.py apps/api/tests/test_executors.py
git commit -m "feat(api): RemoteExecutor wraps remote_dispatch.assign_run (A2)"
```

### Task A4: Move outcome persistence + alerts out of runner.py

**Files:**
- Create: `apps/api/app/services/run_persistence.py`
- Create: `apps/api/app/services/run_alerts.py`
- Modify: `apps/api/app/services/runner.py`

All moved functions receive their collaborators (session factory, start_run, broker) **as parameters**; `runner.py` passes its module globals at call time so conftest/monkeypatch swaps keep applying (locked constraints 1 & 3). No test edits.

- [ ] **Step 1: Create `run_persistence.py`** — move these symbols **verbatim** from `runner.py` (only signature changes shown):

Moves in unchanged: `_MAX_RUN_EVENTS`, `AGENT_EVENT_TYPES`, `GUARDRAIL_EVENT_TYPES`, `_approval_key`, `_upsert_run_approval`, `_maybe_truncate`, `_cap_output`, `_cap_logs`, `_contains_unrestorable_object`, `_graph_node_types`, `_extract_webhook_response`.

Module docstring + imports:

```python
# apps/api/app/services/run_persistence.py
"""Terminal persistence for a finished run (split out of runner.py, A2).

Everything here is invoked by ``runner._execute_run`` with an explicit
``session_factory`` — the runner module's (test-swappable) ``SessionLocal``
— so this module holds no DB state of its own.
"""
```

Then add the terminal-write function, extracted from `_execute_run`'s `try: async with SessionLocal() as session:` block (runner.py ~lines 1433-1534, the Run/NodeRun/RunEvent/approval/queue-mirror/metering writes **and** the minimal-status-fallback `except`), reshaped as:

```python
async def persist_run_outcome(
    session_factory,
    *,
    run_id: str,
    status: str,
    graph_dict: dict,
    node_events: dict[str, dict],
    node_run_records: dict[tuple[str, tuple], dict],
    run_events: "deque[dict[str, Any]]",
    output_cap: int,
) -> None:
    ...  # body moved verbatim; every `SessionLocal()` becomes `session_factory()`
```

The `run_queue.*` mirror calls and the `metering` import move with it. The artifact-refs persist call (`persist_artifact_refs`) stays in `runner.py` (already a one-liner).

- [ ] **Step 2: Create `run_alerts.py`** — move **verbatim**: `_first_failed_event`, `_webhook_urls`, `_post_error_webhooks`, `_dispatch_error_handlers`. Reshape the entrypoint:

```python
# apps/api/app/services/run_alerts.py
"""Error-workflow + alert-webhook dispatch for failed runs (split from runner.py)."""

async def dispatch_error_handlers(
    session_factory,
    start_run_fn,
    *,
    run_id: str,
    node_events: dict[str, dict],
    secret_values: list[str],
) -> None:
    ...  # body verbatim; SessionLocal() -> session_factory(); start_run -> start_run_fn
```

- [ ] **Step 3: Rewire `runner.py`**

- Delete the moved bodies; import the modules: `from app.services import run_alerts, run_persistence`.
- Keep back-compat aliases so any external/lazy importer still resolves:

```python
_cap_output = run_persistence._cap_output
_cap_logs = run_persistence._cap_logs
AGENT_EVENT_TYPES = run_persistence.AGENT_EVENT_TYPES
GUARDRAIL_EVENT_TYPES = run_persistence.GUARDRAIL_EVENT_TYPES
_contains_unrestorable_object = run_persistence._contains_unrestorable_object
_graph_node_types = run_persistence._graph_node_types
```

- In `_execute_run`, the terminal block becomes:

```python
    await run_persistence.persist_run_outcome(
        SessionLocal,            # module global — resolves the test swap at call time
        run_id=run_id, status=status, graph_dict=graph_dict,
        node_events=node_events, node_run_records=node_run_records,
        run_events=run_events, output_cap=output_cap,
    )
```

and the error-handler tail becomes:

```python
    if status == "error":
        await run_alerts.dispatch_error_handlers(
            SessionLocal, start_run,
            run_id=run_id, node_events=node_events, secret_values=secret_values,
        )
```

(`SessionLocal` and `start_run` are looked up in `runner` module globals when this line runs — patched versions apply.)

- [ ] **Step 4: Run the full API suite**

Run: `cd D:\noodle\apps\api; D:\noodle\.venv\Scripts\python.exe -m pytest tests -q`
Expected: green with zero test edits. Pay attention to `test_runs.py`, `test_architecture_fixes.py` (error-workflow + persistence tests).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/run_persistence.py apps/api/app/services/run_alerts.py apps/api/app/services/runner.py
git commit -m "refactor(api): extract run persistence + error alerts from runner.py (A2)"
```

### Task A5: Move approval-resume out of runner.py

**Files:**
- Create: `apps/api/app/services/run_resume.py`
- Modify: `apps/api/app/services/runner.py`

- [ ] **Step 1:** Move the body of `resume_waiting_run_from_approval` (runner.py ~lines 867-1005) verbatim into:

```python
# apps/api/app/services/run_resume.py
"""Approval-driven resume of a waiting run (split from runner.py, A2)."""

async def resume_waiting_run_from_approval(
    session_factory, broker, *, run_id: str, approval_id: str
) -> dict | None:
    """Returns the resume event dict on success (caller publishes + may
    execute synchronously), or None when the run can't be resumed."""
```

Change vs. the original: instead of publishing + synchronously executing at the end, **return** `resume_event` after the commit; return `None` on every early-out.

- [ ] **Step 2:** `runner.py` keeps a thin wrapper with the original name/signature (routers import it):

```python
async def resume_waiting_run_from_approval(run_id: str, approval_id: str) -> bool:
    resume_event = await run_resume.resume_waiting_run_from_approval(
        SessionLocal, broker, run_id=run_id, approval_id=approval_id
    )
    if resume_event is None:
        return False
    broker.publish(run_id, resume_event)
    if settings.run_synchronously:
        await _execute_queued_entry(run_id)
    return True
```

- [ ] **Step 3:** Full suite green (approval-resume tests live in `test_runs.py` / agent-approval test modules). Then check the line count:

Run: `(Get-Content D:\noodle\apps\api\app\services\runner.py | Measure-Object -Line).Lines`
Expected: **≤ ~900** (master-plan target is <800; the remaining overage is the `_call_sub_workflow` cluster, which Phase 4/A3 removes by design — note the actual number in the commit message).

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/services/run_resume.py apps/api/app/services/runner.py
git commit -m "refactor(api): extract approval-resume from runner.py (A2)"
```

### Task A6: Provider split of remote_dispatch.py

**Files:**
- Create: `apps/api/app/services/providers/__init__.py` (empty)
- Create: `apps/api/app/services/providers/agent.py`
- Create: `apps/api/app/services/providers/docker.py`
- Create: `apps/api/app/services/providers/k8s.py`
- Modify: `apps/api/app/services/remote_dispatch.py`

**Design:** providers are **stateless function modules**; every function takes the dispatcher instance `d` (for `_agents`/`_run_callbacks`/`_k8s_futures` state and `_lock`) and `session_factory` as leading parameters. `RemoteDispatcher` keeps all state and its full public/test surface (locked constraint 4) but its methods become one-line delegations that pass `SessionLocal` from `remote_dispatch` module globals at call time.

- [ ] **Step 1: `providers/agent.py`** — move from `remote_dispatch.py` verbatim (reshaped to functions): `_assign_agent_run` → `assign_agent_run(d, session_factory, run_id, pool_id, ...)`, `_pick_agent` → `pick_agent(d, session_factory, pool_id)`, `_handle_agent_message` → `handle_agent_message(d, session_factory, conn, msg)`, `_resolve_remote_subworkflow` → `resolve_remote_subworkflow(conn, msg)` (keeps its lazy `from app.services.runner import _call_sub_workflow`), plus the cloud-provision cluster: `_maybe_provision`, `_bootstrap_user_data`, `_provision_aws_instance`, `_provision_gcp_instance`, `_provision_azure_instance`, `_create_runner_and_token`, `_update_runner_instance_id`, `idle_terminate_cloud_runners`, and the module-level `_terminate_cloud_instance`/`_terminate_ec2`/`_terminate_gce`/`_terminate_azure`, `_uuid_hex`. Also move `_AgentConnection` here; `remote_dispatch.py` re-imports it (`from app.services.providers.agent import _AgentConnection`) so test imports keep working.
- [ ] **Step 2: `providers/docker.py`** — move `_assign_docker_run` → `assign_docker_run(d, session_factory, run_id, pool_id, ...)` and `_ensure_docker_image` → `ensure_docker_image(client, image_tag, env_payload)`. Import `_validate_packages`/`_validate_python_version` from `app.services.remote_dispatch`? **No** — move those two plus `_PKG_SPEC_RE`/`_PY_VERSION_RE` into `providers/docker.py` and re-export from `remote_dispatch.py` (tests import them from there).
- [ ] **Step 3: `providers/k8s.py`** — move `_assign_k8s_run` → `assign_k8s_run(d, session_factory, run_id, pool_id, ...)` and `handle_k8s_runner_connect` → `handle_k8s_runner_connect(d, run_id, ws)`.
- [ ] **Step 4: Slim `remote_dispatch.py` to the orchestrator.** Keep: module docstring, `EventCallback`, `_QUEUE_TTL_SECONDS` (move next to its users if only providers use it — it's used by agent + k8s waits, so move to `providers/agent.py` and import it in `k8s.py` from there; keep a re-export), `_AgentConnection` re-import, `RemoteDispatcher` (state + delegations below), `dispatcher = RemoteDispatcher()`, `queue_run`, `signal_capacity`, `shutdown`, `ping_connected_agents`, `mark_stale_runners_offline`, `handle_runner_connect`/`handle_runner_disconnect`, `cancel_remote_run`, `build_env_payload`, `_QueuedError`, `cloud_idle_terminate_loop`, `runner_heartbeat_loop`, re-exports (`_validate_packages`, `_validate_python_version`). Delegation pattern:

```python
    async def _assign_agent_run(self, *args, **kwargs):
        return await agent_provider.assign_agent_run(self, SessionLocal, *args, **kwargs)

    async def _pick_agent(self, pool_id: str):
        return await agent_provider.pick_agent(self, SessionLocal, pool_id)

    async def _handle_agent_message(self, conn, msg: dict) -> None:
        await agent_provider.handle_agent_message(self, SessionLocal, conn, msg)

    async def _assign_docker_run(self, *args, **kwargs):
        return await docker_provider.assign_docker_run(self, SessionLocal, *args, **kwargs)

    async def _assign_k8s_run(self, *args, **kwargs):
        return await k8s_provider.assign_k8s_run(self, SessionLocal, *args, **kwargs)

    async def handle_k8s_runner_connect(self, run_id: str, ws) -> None:
        await k8s_provider.handle_k8s_runner_connect(self, run_id, ws)

    async def _maybe_provision(self, pool_id: str) -> None:
        await agent_provider.maybe_provision(self, SessionLocal, pool_id)

    async def idle_terminate_cloud_runners(self) -> None:
        await agent_provider.idle_terminate_cloud_runners(SessionLocal)
```

(`SessionLocal` resolved from `remote_dispatch` globals at call time → conftest swap applies to all providers. Inside provider bodies, replace every `SessionLocal()` with `session_factory()` and every `self.` state access with `d.`.)

- [ ] **Step 5: Check line counts + run the suite**

Run: `(Get-Content D:\noodle\apps\api\app\services\remote_dispatch.py | Measure-Object -Line).Lines`
Expected: **< 400**.
Run: `cd D:\noodle\apps\api; D:\noodle\.venv\Scripts\python.exe -m pytest tests -q`
Expected: green — especially `test_runner_pools.py`, `test_audit_wave2.py`, `test_audit_wave3.py` (all poke dispatcher internals), with zero test edits.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/providers apps/api/app/services/remote_dispatch.py
git commit -m "refactor(api): split remote_dispatch into agent/docker/k8s provider modules (A2)"
```

---

## Stage B — dispatch_role + standalone worker (A1)

### Task B1: `dispatch_role` config + fail-fast topology validation

**Files:**
- Modify: `apps/api/app/config.py`
- Test: `apps/api/tests/test_dispatch_role.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/test_dispatch_role.py
"""dispatch_role topology (program A1): config validation + queued-only starts."""

from app.config import Settings


def test_inline_role_has_no_topology_errors():
    s = Settings(dispatch_role="inline", database_url="sqlite+aiosqlite:///x.db")
    assert s.dispatch_topology_errors() == []


def test_worker_role_requires_redis_and_postgres():
    s = Settings(
        dispatch_role="worker",
        queue_backend="none",
        database_url="sqlite+aiosqlite:///x.db",
    )
    errors = s.dispatch_topology_errors()
    assert any("queue_backend" in e for e in errors)
    assert any("postgres" in e.lower() for e in errors)


def test_disabled_role_valid_with_redis_and_postgres():
    s = Settings(
        dispatch_role="disabled",
        queue_backend="redis",
        database_url="postgresql+asyncpg://u:p@h:5432/db",
    )
    assert s.dispatch_topology_errors() == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd D:\noodle\apps\api; D:\noodle\.venv\Scripts\python.exe -m pytest tests/test_dispatch_role.py -v`
Expected: FAIL (`dispatch_role` unknown / method missing — note `extra="ignore"` means the field must exist for the kwarg to stick).

- [ ] **Step 3: Implement in `config.py`** — add the field next to `scheduler_role`:

```python
    # Execution-plane topology (program A1), mirroring scheduler_role:
    #   inline   -> this process leases its own durable-queue entries and
    #               executes them (single-process default; today's behaviour)
    #   worker   -> standalone execution role, started via
    #               ``python -m app.worker_main`` (no HTTP surface)
    #   disabled -> pure control plane: no dispatch loop, no runtime pool;
    #               every run is parked on the durable queue for workers.
    # worker/disabled require Redis (run events must cross processes — the
    # in-process broker would strand WebSocket clients on the API replica)
    # and Postgres (SKIP LOCKED queue leasing). See dispatch_topology_errors().
    dispatch_role: Literal["inline", "worker", "disabled"] = "inline"
```

and the method next to `runtime_warnings()`:

```python
    def dispatch_topology_errors(self) -> list[str]:
        """Hard misconfigurations for split dispatch topologies. Unlike
        ``runtime_warnings()`` these abort startup: a worker/disabled process
        that silently fell back to in-process events or SQLite leasing would
        lose runs, not just degrade."""
        if self.dispatch_role == "inline":
            return []
        errors: list[str] = []
        if self.queue_backend != "redis":
            errors.append(
                f"dispatch_role={self.dispatch_role} requires queue_backend=redis "
                "so run events reach API replicas across processes."
            )
        if not self.database_url.startswith("postgresql"):
            errors.append(
                f"dispatch_role={self.dispatch_role} requires a PostgreSQL "
                "database_url (SKIP LOCKED queue leasing)."
            )
        return errors
```

Also append to `runtime_warnings()` (inside the production-mode block):

```python
        if self.dispatch_role == "disabled":
            warnings.append(
                "dispatch_role=disabled: agent/kubernetes runner-pool runs "
                "need their WebSocket-terminating API replica to dispatch "
                "them; in an api+worker split those pools stay queued. Keep "
                "one replica with dispatch_role=inline if you use them."
            )
```

- [ ] **Step 4: Run tests** — `pytest tests/test_dispatch_role.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/config.py apps/api/tests/test_dispatch_role.py
git commit -m "feat(api): dispatch_role config with fail-fast topology validation (A1)"
```

### Task B2: Provider-aware queue leasing

**Files:**
- Modify: `apps/api/app/services/queue.py` (`lease`)
- Test: append to `apps/api/tests/test_run_queue.py` (uses that file's standalone `session` fixture)

- [ ] **Step 1: Write the failing test**

```python
# append to apps/api/tests/test_run_queue.py
async def test_lease_filters_by_provider(session) -> None:
    """A worker may only lease local + docker entries; agent/k8s entries need
    the WS-holding API process (program A1)."""
    from app.models import RunnerPool
    from app.services import queue as q

    agent_pool = RunnerPool(name="agents", provider="agent")
    docker_pool = RunnerPool(name="dockers", provider="docker")
    session.add_all([agent_pool, docker_pool])
    await session.flush()

    await q.enqueue(session, run_id="r-local", workflow_id="w1")
    await q.enqueue(session, run_id="r-agent", workflow_id="w1",
                    runner_pool_id=agent_pool.id)
    await q.enqueue(session, run_id="r-docker", workflow_id="w1",
                    runner_pool_id=docker_pool.id)
    await session.commit()

    worker_caps = frozenset({"local", "docker"})
    leased = set()
    while True:
        entry = await q.lease(session, worker_id="w", providers=worker_caps)
        if entry is None:
            break
        leased.add(entry.run_id)
    assert leased == {"r-local", "r-docker"}

    # unrestricted lease (inline role) still gets the agent entry
    entry = await q.lease(session, worker_id="w")
    assert entry is not None and entry.run_id == "r-agent"
```

(Adjust `RunnerPool(...)` constructor kwargs to the model's actual required columns — check `app/models.py` `RunnerPool` definition before running; add minimal required fields only.)

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_run_queue.py::test_lease_filters_by_provider -v` → FAIL (`lease() got an unexpected keyword argument 'providers'`).

- [ ] **Step 3: Implement.** In `queue.py`, import `or_` from sqlalchemy and `RunnerPool` from `app.models`. Extend `lease`:

```python
async def lease(
    session: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int | None = None,
    now: datetime | None = None,
    providers: frozenset[str] | None = None,
) -> RunQueueEntry | None:
```

and inside `_base_stmt()`, after the existing `.where(...)`:

```python
        # Provider capability filter (program A1): a standalone worker can run
        # entries whose execution it can actually host — "local" (no pool) and
        # pools whose provider doesn't need a WS terminating in another
        # process. None = no filter (inline single-process role).
        if providers is not None:
            clauses = []
            if "local" in providers:
                clauses.append(RunQueueEntry.runner_pool_id.is_(None))
            remote = providers - {"local"}
            if remote:
                pool_ids = (
                    select(RunnerPool.id)
                    .where(RunnerPool.provider.in_(sorted(remote)))
                    .scalar_subquery()
                )
                clauses.append(RunQueueEntry.runner_pool_id.in_(pool_ids))
            stmt = stmt.where(or_(*clauses))
```

> Executor note: the statement-level `skip_org_filter=True` execution option already on the lease select governs the whole statement including the subquery — verify by running the MT queue tests (`test_queue_fairness.py`) after the change.

- [ ] **Step 4: Run** — `pytest tests/test_run_queue.py tests/test_queue_fairness.py -q` → green.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/queue.py apps/api/tests/test_run_queue.py
git commit -m "feat(api): provider-capability filter on queue leasing (A1)"
```

### Task B3: Lost-worker run recovery

A crashed worker leaves `Run.status="running"` while `requeue_expired_leases` requeues the entry — but `_execute_queued_entry` only dispatches runs whose status is `queued`, so the run would never re-execute. Reset the Run row alongside the entry.

**Files:**
- Modify: `apps/api/app/services/queue.py` (`requeue_expired_leases`)
- Test: append to `apps/api/tests/test_run_queue.py`

- [ ] **Step 1: Write the failing test**

```python
# append to apps/api/tests/test_run_queue.py
async def test_requeue_expired_lease_resets_running_run(session) -> None:
    """A lost worker leaves Run.status='running'; requeue must flip it back to
    'queued' or _execute_queued_entry will refuse to re-dispatch (A1)."""
    from datetime import UTC, datetime, timedelta

    from app.models import Run
    from app.services import queue as q

    run = Run(workflow_id="w1", workflow_version=1, mode="production",
              trigger_type="schedule", status="running")
    session.add(run)
    await session.flush()

    entry = await q.enqueue(session, run_id=run.id, workflow_id="w1")
    moment = datetime.now(UTC)
    leased = await q.lease(session, worker_id="lost-worker", now=moment)
    assert leased is not None and leased.run_id == run.id
    await session.commit()

    acted = await q.requeue_expired_leases(
        session, now=moment + timedelta(seconds=9999)
    )
    await session.commit()
    assert acted == 1
    await session.refresh(entry)
    await session.refresh(run)
    assert entry.status == "queued"
    assert run.status == "queued"
    assert run.finished_at is None
```

(Adjust `Run(...)` kwargs to the model's required columns; mirror how other tests in this file construct rows.)

- [ ] **Step 2: Run to verify failure** — run status stays `"running"` → assertion fails.

- [ ] **Step 3: Implement.** In `requeue_expired_leases`, import `Run` from `app.models`; in the requeue branch (after `entry.status = "queued"` / `entry.available_at = moment`):

```python
            # The worker that held this lease is presumed dead mid-execution.
            # Reset the user-facing Run row too: _execute_queued_entry only
            # dispatches runs in status "queued".
            run = await session.scalar(
                select(Run)
                .where(Run.id == entry.run_id, Run.status == "running")
                .execution_options(skip_org_filter=True)
            )
            if run is not None:
                run.status = "queued"
                run.finished_at = None
```

- [ ] **Step 4: Run** — `pytest tests/test_run_queue.py -q` → green; full API suite green.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/queue.py apps/api/tests/test_run_queue.py
git commit -m "fix(queue): reset Run.status on expired-lease requeue so lost-worker runs re-execute (A1)"
```

### Task B4: Role-aware dispatch loop + cross-process cancel reconciliation

**Files:**
- Modify: `apps/api/app/services/queue.py` (`run_queue_dispatch_loop` + new `_cancel_reconcile`)
- Test: append to `apps/api/tests/test_run_queue.py`

- [ ] **Step 1: Write the failing test**

```python
# append to apps/api/tests/test_run_queue.py
async def test_cancel_reconcile_cancels_local_task_for_cancelled_entry(session) -> None:
    """An API replica can only flip the queue entry to 'cancelled'; the worker
    holding the executing task must observe that and cancel locally (A1)."""
    import asyncio

    from app.services import queue as q

    await q.enqueue(session, run_id="r-cancel", workflow_id="w1")
    await q.cancel(session, run_id="r-cancel")
    await session.commit()

    async def _hang():
        await asyncio.sleep(60)

    task = asyncio.ensure_future(_hang())
    active = {"r-cancel": task, "r-other": asyncio.ensure_future(_hang())}
    try:
        cancelled = await q._cancel_reconcile(session, active)
        assert cancelled == ["r-cancel"]
        await asyncio.sleep(0)
        assert task.cancelled() or task.cancelling()
        assert not active["r-other"].done()
    finally:
        for t in active.values():
            t.cancel()
```

- [ ] **Step 2: Run to verify failure** — `_cancel_reconcile` doesn't exist → AttributeError.

- [ ] **Step 3: Implement.** Add to `queue.py`:

```python
async def _cancel_reconcile(
    session: AsyncSession, active_runs: dict[str, "asyncio.Task[None]"]
) -> list[str]:
    """Cancel local tasks whose queue entry was cancelled by another process.

    In split topologies the API replica handling DELETE /runs/{id} has no
    task handle — it marks the RunQueueEntry cancelled and this worker-side
    sweep turns that into a real asyncio cancellation.
    """
    if not active_runs:
        return []
    rows = (
        await session.scalars(
            select(RunQueueEntry.run_id)
            .where(
                RunQueueEntry.run_id.in_(list(active_runs)),
                RunQueueEntry.status == "cancelled",
            )
            .execution_options(skip_org_filter=True)
        )
    ).all()
    cancelled: list[str] = []
    for run_id in rows:
        task = active_runs.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            cancelled.append(run_id)
    return cancelled
```

Then extend `run_queue_dispatch_loop`. After the deferred imports, add:

```python
    from app.services.runner import _active_runs  # noqa: PLC0415

    role = settings.dispatch_role
    providers = (
        frozenset({"local", "docker"}) if role == "worker" else None
    )
```

Per tick, right after the expired-lease requeue commit:

```python
                if role == "worker":
                    async with SessionLocal() as session:
                        cancelled = await _cancel_reconcile(session, _active_runs)
                    if cancelled:
                        logger.info("queue: cancelled %d run(s) flagged by control plane", len(cancelled))
```

And change the lease call to `entry = await lease(session, worker_id=worker, providers=providers)`.

- [ ] **Step 4: Run** — `pytest tests/test_run_queue.py -q` then the full suite → green (inline role passes `providers=None`, so existing behavior is untouched).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/queue.py apps/api/tests/test_run_queue.py
git commit -m "feat(queue): role-aware leasing + worker-side cancel reconciliation (A1)"
```

### Task B5: `start_run` parks everything when dispatch is disabled

**Files:**
- Modify: `apps/api/app/services/runner.py` (`start_run`, the `queue_locally` computation ~line 811)
- Test: append to `apps/api/tests/test_dispatch_role.py`

- [ ] **Step 1: Write the failing test** (mirrors the local-queue test at `tests/test_runs.py:1380` — same client-fixture pattern):

```python
# append to apps/api/tests/test_dispatch_role.py
import pytest
from httpx import AsyncClient

from app.config import settings


@pytest.fixture
def _disabled_dispatch():
    prior_role = settings.dispatch_role
    prior_sync = settings.run_synchronously
    settings.dispatch_role = "disabled"
    settings.run_synchronously = False
    yield
    settings.dispatch_role = prior_role
    settings.run_synchronously = prior_sync


async def test_start_run_parks_on_queue_when_dispatch_disabled(
    client: AsyncClient, _disabled_dispatch
) -> None:
    resp = await client.post("/workflows", json={"name": "wf-disabled-role"})
    wf = resp.json()
    graph = {
        "nodes": [{"id": "t", "type": "manual_trigger", "params": {}}],
        "edges": [],
    }
    await client.put(f"/workflows/{wf['id']}", json={"graph": graph})

    resp = await client.post(f"/workflows/{wf['id']}/run", json={})
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    resp = await client.get(f"/runs/{run_id}")
    assert resp.json()["status"] == "queued"  # parked, not executed
```

(Adjust endpoint paths/payloads to match how `tests/test_runs.py` creates+runs workflows — copy its helper pattern verbatim.)

- [ ] **Step 2: Run to verify failure** — run executes (status `running`/`success`) instead of staying `queued`.

- [ ] **Step 3: Implement.** In `start_run`, replace the `queue_locally` computation with:

```python
        # Park the run on the durable queue instead of dispatching inline when
        # (a) this replica is a pure control plane (dispatch_role=disabled —
        # applies to remote-pool runs too; a worker or WS-holding replica will
        # lease it), or (b) it's a local run with no immediate pool capacity.
        # Synchronous runs (tests) always execute inline.
        queue_locally = not settings.run_synchronously and (
            settings.dispatch_role == "disabled"
            or (
                settings.local_queue_enabled
                and runner_pool_id is None
                and not runtime_pool.has_immediate_capacity()
            )
        )
```

The existing `reason=` on the enqueue call: change to `reason="dispatch_disabled" if settings.dispatch_role == "disabled" else "local_capacity"` inside the `queue_locally` arm.

- [ ] **Step 4: Run** — `pytest tests/test_dispatch_role.py tests/test_runs.py -q` then full suite → green.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/runner.py apps/api/tests/test_dispatch_role.py
git commit -m "feat(api): dispatch_role=disabled parks all runs on the durable queue (A1)"
```

### Task B6: Lifespan gating in main.py

**Files:**
- Modify: `apps/api/app/main.py`

- [ ] **Step 1: Implement** (no new unit test — lifespan doesn't run under ASGITransport; verified by the split-topology check in Task C2):

At the top of `lifespan`, before any loop starts:

```python
    # Split-topology misconfigurations abort startup (program A1) — a control
    # plane that silently executed runs, or used in-process events, would
    # corrupt the worker topology rather than degrade it.
    topology_errors = settings.dispatch_topology_errors()
    if settings.dispatch_role == "worker":
        topology_errors.append(
            "dispatch_role=worker is the standalone worker entrypoint "
            "(python -m app.worker_main); API replicas use inline or disabled."
        )
    if topology_errors:
        raise RuntimeError("startup aborted:\n  - " + "\n  - ".join(topology_errors))
```

Gate run-ownership startup/loops on the role:

```python
    dispatch_inline = settings.dispatch_role == "inline"
    await _ensure_global_environment()
    if dispatch_inline:
        # Only the process that owns execution may declare runs interrupted;
        # in split topologies workers own runs and lease-expiry recovers them.
        await _mark_interrupted_runs()
```

- `reaper = ...` gains `and dispatch_inline` in its condition.
- `queue_loop = asyncio.create_task(...) if dispatch_inline else None`.
- `cloud_idle` and `heartbeat` stay unconditional (agent WS terminates on API replicas regardless of role).
- In the shutdown sequence, the `for task in (...)` already tolerates `None` — just ensure `queue_loop` stays in the tuple.

- [ ] **Step 2: Run the full API suite** (tests don't run the lifespan, so this guards imports/syntax) plus a manual boot check:

Run: `cd D:\noodle\apps\api; D:\noodle\.venv\Scripts\python.exe -c "from app.main import app; print('ok')"`
Expected: `ok`. Suite green.

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/main.py
git commit -m "feat(api): lifespan honours dispatch_role — control-plane replicas stop executing (A1)"
```

### Task B7: Worker entrypoint

**Files:**
- Create: `apps/api/app/worker_main.py`
- Test: append to `apps/api/tests/test_dispatch_role.py`

- [ ] **Step 1: Write the failing test**

```python
# append to apps/api/tests/test_dispatch_role.py
def test_worker_main_validation_rejects_bad_config(monkeypatch):
    from app import worker_main

    monkeypatch.setattr(settings, "dispatch_role", "inline")
    monkeypatch.setattr(settings, "queue_backend", "none")
    with pytest.raises(SystemExit) as exc:
        worker_main._validate()
    msg = str(exc.value)
    assert "DISPATCH_ROLE=worker" in msg


def test_worker_main_validation_accepts_worker_config(monkeypatch):
    from app import worker_main

    monkeypatch.setattr(settings, "dispatch_role", "worker")
    monkeypatch.setattr(settings, "queue_backend", "redis")
    monkeypatch.setattr(
        settings, "database_url", "postgresql+asyncpg://u:p@h:5432/db"
    )
    worker_main._validate()  # must not raise
```

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError: app.worker_main`.

- [ ] **Step 3: Implement**

```python
# apps/api/app/worker_main.py
"""Standalone execution-worker entrypoint (program A1).

``python -m app.worker_main`` runs the execution plane with no HTTP surface:
the durable-queue dispatch loop (leasing local + docker entries), the warm
runtime pool, and the idle reaper. API replicas run dispatch_role=disabled
and only enqueue; run events reach browsers via the Redis-backed broker.

Graceful drain: SIGTERM/SIGINT set queue_drain (stop leasing), wait up to
``queue_dispatch_shutdown_timeout_seconds`` for in-flight runs, then cancel
laggards. On Windows consoles Ctrl+C may skip the drain (asyncio.run tears
down directly) — production workers are Linux.
"""

import asyncio
import contextlib
import logging
import signal

from app.config import settings
from app.db import engine
from app.redis_client import redis_client
from app.services.events import broker, broker_reaper_loop
from app.services.queue import run_queue_dispatch_loop
from app.services.runner import (
    drain_active_runs,
    process_isolator,
    shutdown_active_runs,
)
from app.services.runtime_pool import idle_reaper_loop
from app.services.runtime_pool import pool as runtime_pool
from app.tenancy import run_as_system

logger = logging.getLogger("noodle.worker")


def _validate() -> None:
    errors = settings.dispatch_topology_errors()
    if settings.dispatch_role != "worker":
        errors.append(
            "the worker entrypoint requires DISPATCH_ROLE=worker "
            f"(got {settings.dispatch_role!r})"
        )
    if errors:
        raise SystemExit("worker startup aborted:\n  - " + "\n  - ".join(errors))


def _as_system(loop_fn):
    """Workers operate across ALL orgs — same rationale as main.py's loops."""

    async def system_loop():
        with run_as_system():
            await loop_fn()

    return system_loop


async def _amain() -> None:
    _validate()
    if settings.artifact_storage_backend == "s3":
        from app.services.s3_artifact_backend import register_s3_backend  # noqa: PLC0415

        register_s3_backend()
    mode = await broker.connect()
    if mode != "redis":
        raise SystemExit(
            "worker requires a reachable Redis event broker "
            f"(REDIS_URL={settings.redis_url}); broker mode was {mode!r}"
        )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(sig, stop.set)

    tasks = [
        asyncio.create_task(_as_system(run_queue_dispatch_loop)()),
        asyncio.create_task(broker_reaper_loop()),
    ]
    if settings.use_subprocess_runner and settings.runner_idle_seconds > 0:
        tasks.append(asyncio.create_task(_as_system(idle_reaper_loop)()))

    logger.info("noodle worker up (pid drain timeout %.1fs)",
                settings.queue_dispatch_shutdown_timeout_seconds)
    try:
        await stop.wait()
    finally:
        settings.queue_drain = True
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        drain_timeout = max(settings.queue_dispatch_shutdown_timeout_seconds, 0.0)
        if drain_timeout > 0:
            leftover = await drain_active_runs(drain_timeout)
            if leftover:
                logger.warning(
                    "drain expired with %d active run(s); forcing cancel", leftover
                )
        with contextlib.suppress(Exception):
            await shutdown_active_runs()
        with contextlib.suppress(Exception):
            await runtime_pool.shutdown()
        process_isolator.shutdown()
        with contextlib.suppress(Exception):
            await engine.dispose()
        with contextlib.suppress(Exception):
            await redis_client.aclose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run** — `pytest tests/test_dispatch_role.py -v` → PASS; plus an import smoke: `D:\noodle\.venv\Scripts\python.exe -c "from app import worker_main; print('ok')"` (from `apps/api`).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/worker_main.py apps/api/tests/test_dispatch_role.py
git commit -m "feat(api): standalone worker entrypoint — python -m app.worker_main (A1)"
```

---

## Stage C — deploy topology + Celery retirement (A4)

### Task C1: Compose + Helm switch to the new worker; Celery services removed

**Files:**
- Modify: `deploy/docker-compose.yml`
- Modify: `deploy/helm/noodle/templates/worker-deployment.yaml`
- Modify: `deploy/helm/noodle/values.yaml`, `deploy/helm/noodle/templates/_helpers.tpl` (check for CELERY_* env entries; remove)
- Modify: `deploy/Dockerfile.python` (remove `apps/worker` install lines if present)

- [ ] **Step 1: docker-compose.yml.** Delete the `worker:` (celery) and `beat:` services. Replace with:

```yaml
  worker:
    build:
      context: ..
      dockerfile: deploy/Dockerfile.python
    command: python -m app.worker_main
    environment:
      DATABASE_URL: postgresql+asyncpg://noodle:noodle@postgres:5432/noodle
      REDIS_URL: redis://redis:6379/0
      QUEUE_BACKEND: redis
      DISPATCH_ROLE: worker
      USE_SUBPROCESS_RUNNER: "true"
      ENVS_DIR: /app/envs
      ARTIFACTS_DIR: /app/artifacts
      SECRET_KEY: ${NOODLE_SECRET_KEY:-noodle-dev-secret-change-me-in-production}
    volumes:
      - envdata:/app/envs
      - artifactdata:/app/artifacts
      - "D:/output_grainbrokers_parquet:/data/grainbrokers:ro"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
```

On the `api:` service: remove `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND`; change `ENABLE_INPROCESS_SCHEDULER: "false"` to `"true"`; add `QUEUE_BACKEND: redis`, `DISPATCH_ROLE: disabled`, `SCHEDULER_ROLE: leader`. (The api keeps the Alembic migrate step and serves agent/k8s WS; the worker executes local+docker runs.)

> Executor note: confirm the command's working directory matches `deploy/Dockerfile.python` (the api service runs `uvicorn app.main:app` from `/app` — if imports resolve via an `apps/api` path layout, mirror whatever the api command does, e.g. `sh -c "cd apps/api && python -m app.worker_main"`).

- [ ] **Step 2: Helm.** `worker-deployment.yaml`: replace the celery command with `["python", "-m", "app.worker_main"]`, delete the entire `beat` Deployment block. Check `values.yaml` (worker resources/replicas keys stay) and `_helpers.tpl` `noodle.env` for `CELERY_*` vars — remove them, add `DISPATCH_ROLE` (worker deployment) / `QUEUE_BACKEND: redis` if env is templated there.

- [ ] **Step 3: Dockerfile.python** — remove any `apps/worker` COPY/install lines (verify with `Select-String -Path deploy/Dockerfile.python -Pattern worker`).

- [ ] **Step 4: Commit**

```bash
git add deploy
git commit -m "feat(deploy): compose + Helm run the dispatch_role worker; Celery services removed (A1, A4)"
```

### Task C2: Split-topology verification (acceptance for A1)

Requires Docker Desktop running. If unavailable on this machine, mark the checkbox `[skipped — no Docker]`, record it in the final report, and rely on Task B5's API-level test for the queued-start behavior.

- [ ] **Step 1:** `cd D:\noodle\deploy; docker compose down -v; docker compose up -d --build postgres redis api worker web`
- [ ] **Step 2:** Wait for `docker compose logs worker` to show `noodle worker up`, and `docker compose logs api` to show clean startup (no topology error).
- [ ] **Step 3:** Run the e2e smoke against the compose stack. The Playwright config (`apps/web/e2e/playwright.config.ts`) boots its own servers; for an external stack add an env-gated bypass — in the config, when `process.env.E2E_EXTERNAL_BASE_URL` is set, set `use.baseURL` to it and `webServer: undefined`. Then:

```powershell
cd D:\noodle\apps\web
$env:E2E_EXTERNAL_BASE_URL = "http://localhost:5173"; npm run test:e2e
```

Expected: 3 passed — the run executes on the worker (confirm with `docker compose logs worker | Select-String "dispatch"`) and node events stream to the browser through Redis.

- [ ] **Step 4:** Tear down (`docker compose down -v`) and commit the e2e config tweak:

```bash
git add apps/web/e2e/playwright.config.ts
git commit -m "test(e2e): allow running the smoke suite against an external stack"
```

### Task C3: Delete apps/worker and sweep Celery references

**Files:**
- Delete: `apps/worker/` (entire directory)
- Modify: `pyproject.toml` (workspace members: drop `"apps/worker"`)
- Modify: `.env.example` (CELERY section → DISPATCH_ROLE/worker docs)
- Modify: `apps/api/app/routers/internal.py` (docstrings only)
- Modify: `apps/api/app/services/triggers.py:8`, `apps/api/app/config.py:85` (comments)
- Modify: `README.md`, `docs/architecture.md`, `docs/deployment.md`, `HANDOFF.md` (stack descriptions)

- [ ] **Step 1:** `git rm -r apps/worker`; remove `"apps/worker"` from `[tool.uv.workspace] members` and `"worker"` from `[tool.ruff.lint.isort] known-first-party` in root `pyproject.toml`. Regenerate the lockfile if `uv lock` works on this machine (`uv lock` — note: `uv run` is broken here but `lock` may not be; if it fails, record a follow-up in the final report instead of blocking).
- [ ] **Step 2:** `.env.example`: delete `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` and the "Slice 3b — optional Celery Beat scheduler" section; replace with:

```bash
# ---- Split execution topology (optional) -----------------------------------
# Run the API as a pure control plane and execution on standalone workers:
#   API replicas:  DISPATCH_ROLE=disabled  SCHEDULER_ROLE=leader  QUEUE_BACKEND=redis
#   Workers:       DISPATCH_ROLE=worker    QUEUE_BACKEND=redis    (python -m app.worker_main)
# Both roles need PostgreSQL + Redis. Default (inline) is single-process.
# DISPATCH_ROLE=inline
```

- [ ] **Step 3:** `internal.py` module + endpoint docstrings: replace "Celery worker / Celery Beat" wording with "an external scheduler (e.g. cron) may drive `/internal/scheduler/tick`; multi-replica deployments normally use `scheduler_role=leader` instead". `triggers.py:8` and `config.py:85` comments: same substitution (leader-elected scheduler, not Celery Beat).
- [ ] **Step 4:** Docs sweep — `README.md`, `docs/architecture.md`, `docs/deployment.md`, `HANDOFF.md`: replace Celery worker/beat topology descriptions with the new one: *N api replicas (`dispatch_role=disabled`) + M workers (`python -m app.worker_main`) + leader-elected scheduler; single-process default unchanged.* `docs/deployment.md` gets the topology matrix (roles × required infra). Leave historical planning docs (`docs/architecture-improvement-plan.md`, `docs/superpowers/plans/*`, comparison docs describing *other* products) untouched.
- [ ] **Step 5: Acceptance check**

Run: `cd D:\noodle; git grep -il celery -- ':!docs/superpowers' ':!docs/architecture-improvement-plan.md' ':!plan.md' ':!docs/*comparison*'`
Expected: no hits outside historical/comparison docs.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: retire the Celery worker — dispatch_role worker + leader scheduler replace it (A4)"
```

### Task C4: Full verification + docs + memory

- [ ] **Step 1:** Full test sweep:

```powershell
cd D:\noodle\packages\core;  D:\noodle\.venv\Scripts\python.exe -m pytest tests -q   # expect 332+
cd D:\noodle\packages\nodes; D:\noodle\.venv\Scripts\python.exe -m pytest tests -q   # expect 720+
cd D:\noodle\apps\api;       D:\noodle\.venv\Scripts\python.exe -m pytest tests -q   # expect 544+ + 1 known fail
cd D:\noodle\apps\web;       npm run typecheck; npm run test                          # vitest 147
cd D:\noodle\apps\web;       npm run test:e2e                                         # 3 passed (inline mode)
```

- [ ] **Step 2:** Update `docs/architecture.md` execution-plane section (RunExecutor seam, provider modules, dispatch_role topology) if not already covered in C3.
- [ ] **Step 3:** Tick Phase 3 in `docs/superpowers/plans/2026-06-10-production-architecture-program.md` is NOT needed (that doc has no Phase-3 checkboxes) — instead update the memory file `architecture-program.md`: Phase 3 done, branch, key file map, verification numbers, deviations (runner.py line count, agent/k8s worker limitation).
- [ ] **Step 4:** Commit any remaining docs, then finish the branch (merge to `feat/multi-tenancy` per program convention) via superpowers:finishing-a-development-branch.

---

## Self-review notes

- **Spec coverage:** A2 → Tasks A1-A6 (protocol, local, remote, runner shrink, provider split). A1 → Tasks B1-B7 + C1-C2 (config, leasing, recovery, cancel, start_run, lifespan, entrypoint, deploy, e2e). A4 → Tasks C1, C3 (delete celery services + package + docs). All three review items covered.
- **Deviations from the master plan, with reasons:**
  - `RunExecutionContext` drops `org_id`/`parameters`/`credentials` and adds `env_payload`/`workflow_modules`/`run_timeout`/`pause_on_approval`/`agent_action_resume`: credentials are resolved *into* the graph before dispatch (there is no separate credentials dict in reality), parameters are seeded into the cache by `start_run`, and org context travels via ContextVars. The added fields are what `runtime_pool.dispatch`/`dispatcher.assign_run` actually consume.
  - `runner.py` lands ≤ ~900 lines, not < 800: the `_call_sub_workflow` cluster (~240 lines) must stay until Phase 4 (A3) replaces it with the engine callback (locked constraint 6).
  - Workers lease only `local` + `docker` entries: agent/k8s providers require the WS-terminating API process (pre-existing single-process assumption, now made explicit + warned about). Documented in `runtime_warnings()` and deployment docs.
  - Cross-process cancel (Task B4) is additive beyond the locked scope but required for `cancel_run` to keep working at all in split topologies.
- **Placeholder scan:** "verbatim move" instructions name exact symbols and the new signatures; new seams carry complete code. Executor-verify notes flag the four spots where live-code confirmation is needed (RunnerPool/Run constructor kwargs in tests, tenancy subquery option, Dockerfile layout, helm env helper).
- **Type consistency:** `RunOutcome(status=...)`, `RunExecutionContext` keys, and `lease(providers=frozenset[str])` are used identically across Tasks A1-A3 and B2/B4. `session_factory` is positional-first in all moved-module entrypoints.
