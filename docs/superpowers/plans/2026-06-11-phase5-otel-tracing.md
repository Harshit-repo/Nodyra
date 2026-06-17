# Phase 5: OpenTelemetry Tracing (A5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One connected OpenTelemetry trace per run — `run.enqueue` (API) → `run.lease` (worker) → `run.execute` → `node.execute` per node — off by default, zero overhead when off.

**Architecture:** All tracing lives in a new `apps/api/app/tracing.py` module guarded by a single boolean; the engine and runtime packages are untouched. Trace context crosses the API→worker process boundary as a W3C `traceparent` carrier persisted on `run_queue.trace_context` (additive migration 0049). **Deviation from the master-plan sketch:** `node.execute` spans are synthesized HOST-side from the engine's `node_finished` events (which already carry `started_at`/`finished_at` epoch-second floats through the existing event channel) instead of creating spans inside the runtime subprocess — the subprocess runs in user-built venvs, and making `opentelemetry-sdk` a dependency of every user environment is invasive for zero fidelity gain. The acceptance (single connected trace across API → worker → subprocess node work) is still met: node spans carry the subprocess's real timings.

**Tech Stack:** opentelemetry-sdk, opentelemetry-exporter-otlp-proto-http (no grpc dep), opentelemetry-instrumentation-fastapi, opentelemetry-instrumentation-sqlalchemy. Provider is module-local (never `trace.set_tracer_provider`) so tests can tear down and re-setup freely.

**Validation commands (Windows dev box — `uv run` is broken, use the built venv; NEVER run two package suites concurrently):**

```powershell
cd apps\api;        ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd packages\core;   ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd apps\web;        npm run test:e2e
```

Known pre-existing failure (not ours): `test_architecture_fixes.py::test_dispatch_webhook_passes_shared_session_to_resolve_node_auth`.

---

## Span tree

```
HTTP request (FastAPIInstrumentor, when enabled)
 └── run.enqueue              start_run; injects carrier → run_queue.trace_context
      └── run.lease           _execute_queued_entry (worker / dispatch loop path)
           └── run.execute    _execute_run wrapper; attrs run_id, workflow_id, org_id, status
                └── node.execute   one per node_finished event; attrs node_id, node_type,
                                   status, org_id, iteration_path; explicit start/end times
```

Immediate-dispatch path (no queue wait): `run.execute` parents directly on the `run.enqueue` carrier — still one connected trace, just no `run.lease` hop.

---

### Task 1: Dependencies + config settings

**Files:**
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/app/config.py` (after `multi_tenancy_enabled`, ~line 177)
- Modify: `uv.lock` (regenerated)

- [ ] **Step 1: Add the four OTel packages to `apps/api/pyproject.toml` dependencies**

```toml
    "httpx>=0.27",
    # A5: OpenTelemetry tracing (API/worker processes only — never user envs)
    "opentelemetry-sdk>=1.30",
    "opentelemetry-exporter-otlp-proto-http>=1.30",
    "opentelemetry-instrumentation-fastapi>=0.51b0",
    "opentelemetry-instrumentation-sqlalchemy>=0.51b0",
```

- [ ] **Step 2: Lock and install into the built venv**

Run from repo root:

```powershell
uv lock
uv pip install --python .venv\Scripts\python.exe opentelemetry-sdk opentelemetry-exporter-otlp-proto-http opentelemetry-instrumentation-fastapi opentelemetry-instrumentation-sqlalchemy
.venv\Scripts\python.exe -c "import opentelemetry.sdk.trace, opentelemetry.exporter.otlp.proto.http.trace_exporter, opentelemetry.instrumentation.fastapi, opentelemetry.instrumentation.sqlalchemy; print('otel ok')"
```

Expected: `otel ok`. (If `uv pip install` misbehaves on this box, fall back to `.venv\Scripts\python.exe -m pip install <same packages>`.)

- [ ] **Step 3: Add the two settings to `app/config.py`**

Insert directly after the `multi_tenancy_enabled: bool = False` line:

```python
    # A5: OpenTelemetry tracing. Off by default — when disabled no SDK objects
    # are created and every tracing hook is a single boolean check (zero
    # overhead). Endpoint is the OTLP/HTTP collector traces URL, e.g.
    # http://localhost:4318/v1/traces; blank uses the SDK default
    # (http://localhost:4318/v1/traces). Standard OTEL_* env vars are also
    # honoured by the SDK for anything not surfaced here.
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = ""
```

- [ ] **Step 4: Commit**

```bash
git add apps/api/pyproject.toml apps/api/app/config.py uv.lock
git commit -m "feat(api): OTel deps + otel_enabled/otel_exporter_otlp_endpoint settings (A5)"
```

### Task 2: `app/tracing.py` module

**Files:**
- Create: `apps/api/app/tracing.py`
- Test: `apps/api/tests/test_tracing.py`

- [ ] **Step 1: Write the failing unit tests**

```python
# apps/api/tests/test_tracing.py
"""A5: tracing module unit tests. The provider is module-local, so each test
sets up against an InMemorySpanExporter and tears down via shutdown_tracing."""

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from app import tracing
from app.config import settings


@pytest.fixture
def exporter(monkeypatch):
    monkeypatch.setattr(settings, "otel_enabled", True)
    exp = InMemorySpanExporter()
    tracing.shutdown_tracing()
    tracing.setup_tracing("noodle-test", exporter=exp)
    yield exp
    tracing.shutdown_tracing()


def test_disabled_by_default_everything_no_ops():
    tracing.shutdown_tracing()  # known-clean module state
    assert tracing.enabled() is False
    assert tracing.inject_context() is None
    with tracing.span("run.execute") as sp:
        assert sp is None
    # must not raise
    tracing.record_node_span({"node_id": "n1"}, node_types={}, org_id=None)


def test_setup_is_idempotent(exporter):
    assert tracing.enabled() is True
    tracing.setup_tracing("noodle-test-again")  # second call: no-op, no raise
    assert tracing.enabled() is True


def test_node_span_synthesis_under_run_execute(exporter):
    with tracing.span("run.execute", attributes={"noodle.run_id": "r1"}):
        tracing.record_node_span(
            {
                "node_id": "n1",
                "status": "success",
                "started_at": 1000.0,
                "finished_at": 1002.5,
                "iteration_path": [0, 2],
            },
            node_types={"n1": "code"},
            org_id="org-1",
        )
    spans = {s.name: s for s in exporter.get_finished_spans()}
    run, node = spans["run.execute"], spans["node.execute"]
    assert node.parent is not None
    assert node.parent.span_id == run.context.span_id
    assert node.context.trace_id == run.context.trace_id
    assert node.attributes["noodle.node_id"] == "n1"
    assert node.attributes["noodle.node_type"] == "code"
    assert node.attributes["noodle.status"] == "success"
    assert node.attributes["noodle.org_id"] == "org-1"
    assert node.attributes["noodle.iteration_path"] == "0/2"
    assert node.start_time == 1_000_000_000_000  # 1000.0 s → ns
    assert node.end_time == 1_002_500_000_000


def test_node_span_skips_events_without_timestamps(exporter):
    tracing.record_node_span(
        {"node_id": "n1", "status": "success"}, node_types={}, org_id=None
    )
    assert exporter.get_finished_spans() == ()


def test_carrier_round_trip_connects_spans(exporter):
    with tracing.span("run.enqueue"):
        carrier = tracing.inject_context()
    assert carrier is not None and "traceparent" in carrier
    with tracing.span("run.lease", carrier=carrier):
        pass
    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert (
        spans["run.lease"].context.trace_id
        == spans["run.enqueue"].context.trace_id
    )
    assert spans["run.lease"].parent.span_id == spans["run.enqueue"].context.span_id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_tracing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.tracing'`

- [ ] **Step 3: Implement the module**

```python
# apps/api/app/tracing.py
"""OpenTelemetry tracing for the API/worker processes (program A5).

Span tree per run (see the Phase 5 plan):

    HTTP request (FastAPIInstrumentor)
      └── run.enqueue            start_run; carrier persisted on run_queue
           └── run.lease         _execute_queued_entry (worker)
                └── run.execute  _execute_run
                     └── node.execute   one per node_finished event

``node.execute`` spans are synthesized HOST-side from the engine's
``node_finished`` events (they carry ``started_at``/``finished_at`` epoch
floats), so runtime subprocesses in user-built venvs need no OTel deps.

Everything is a no-op unless ``settings.otel_enabled`` is true, guarded by
one module boolean — the run/event hot paths pay a single ``if`` when off.
The provider is module-local (never ``trace.set_tracer_provider``) so tests
can tear down and re-create it; instrumentors get it passed explicitly.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_enabled = False
_provider: Any = None  # opentelemetry.sdk.trace.TracerProvider when enabled
_tracer: Any = None


def enabled() -> bool:
    return _enabled


def setup_tracing(service_name: str, *, exporter: Any | None = None) -> None:
    """Initialise the module-local tracer provider.

    Idempotent; a complete no-op when ``settings.otel_enabled`` is false.
    ``exporter`` overrides the OTLP/HTTP exporter (tests pass an
    ``InMemorySpanExporter``, wired through a SimpleSpanProcessor so spans
    are visible synchronously)."""
    global _enabled, _provider, _tracer
    if not settings.otel_enabled or _provider is not None:
        return
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        SimpleSpanProcessor,
    )

    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name})
    )
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        kwargs: dict[str, Any] = {}
        if settings.otel_exporter_otlp_endpoint:
            kwargs["endpoint"] = settings.otel_exporter_otlp_endpoint
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(**kwargs)))
    _provider = provider
    _tracer = provider.get_tracer("noodle")
    _enabled = True
    logger.info("tracing enabled service=%s", service_name)


def shutdown_tracing() -> None:
    """Flush + drop the provider. Safe to call when never set up."""
    global _enabled, _provider, _tracer
    if _provider is not None:
        try:
            _provider.shutdown()
        except Exception:  # noqa: BLE001 - teardown must never raise
            pass
    _provider = None
    _tracer = None
    _enabled = False


def flush(timeout_millis: int = 5_000) -> None:
    """Force-flush buffered spans (lifespan teardown / worker drain)."""
    if _provider is not None:
        try:
            _provider.force_flush(timeout_millis)
        except Exception:  # noqa: BLE001
            pass


def instrument_app(app: Any) -> None:
    if not _enabled:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, tracer_provider=_provider)


def instrument_sqlalchemy(async_engine: Any) -> None:
    if not _enabled:
        return
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(
        engine=async_engine.sync_engine, tracer_provider=_provider
    )


def inject_context() -> dict | None:
    """The current span context as a W3C carrier dict, or None when disabled
    (callers store the None directly — absent carrier means no tracing)."""
    if not _enabled:
        return None
    from opentelemetry.propagate import inject

    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier or None


def _context_from(carrier: dict | None) -> Any:
    if not carrier:
        return None  # None → ambient current context
    from opentelemetry.propagate import extract

    return extract(carrier)


@contextmanager
def span(
    name: str,
    *,
    carrier: dict | None = None,
    attributes: dict | None = None,
) -> Iterator[Any]:
    """Run ``name`` as the current span. Parent comes from ``carrier`` when
    given, else the ambient context. Yields the span, or None when disabled."""
    if not _enabled:
        yield None
        return
    with _tracer.start_as_current_span(
        name, context=_context_from(carrier), attributes=attributes or {}
    ) as sp:
        yield sp


def record_node_span(
    event: dict, *, node_types: dict[str, str], org_id: str | None
) -> None:
    """Synthesize a ``node.execute`` span from a ``node_finished`` event using
    the event's own timestamps (epoch seconds → ns). Parent is the ambient
    current span — the run.execute span in ``_execute_run``."""
    if not _enabled:
        return
    started = event.get("started_at")
    finished = event.get("finished_at")
    if not isinstance(started, (int, float)) or not isinstance(finished, (int, float)):
        return
    node_id = str(event.get("node_id") or "")
    attrs: dict[str, Any] = {
        "noodle.node_id": node_id,
        "noodle.node_type": node_types.get(node_id, ""),
        "noodle.status": str(event.get("status") or ""),
    }
    if org_id:
        attrs["noodle.org_id"] = org_id
    path = event.get("iteration_path")
    if isinstance(path, list) and path:
        attrs["noodle.iteration_path"] = "/".join(str(p) for p in path)
    sp = _tracer.start_span(
        "node.execute", start_time=int(started * 1e9), attributes=attrs
    )
    sp.end(end_time=int(finished * 1e9))
```

- [ ] **Step 4: Run the tests**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_tracing.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/tracing.py apps/api/tests/test_tracing.py
git commit -m "feat(api): tracing module — module-local OTel provider, carrier helpers, node-span synthesis (A5)"
```

### Task 3: `run_queue.trace_context` column + enqueue parameter

**Files:**
- Modify: `apps/api/app/models.py` (RunQueueEntry, after `replay_seed`, ~line 953)
- Create: `apps/api/alembic/versions/0049_queue_trace_context.py`
- Modify: `apps/api/app/services/queue.py` (`enqueue`, ~line 94)
- Test: `apps/api/tests/test_run_queue.py` (append)

- [ ] **Step 1: Write the failing tests (append to `tests/test_run_queue.py`)**

```python
@pytest.mark.asyncio
async def test_enqueue_persists_trace_context(session) -> None:
    from app.services import queue as run_queue

    entry = await run_queue.enqueue(
        session,
        run_id="run-tc",
        workflow_id="wf-1",
        trace_context={"traceparent": "00-aa-bb-01"},
    )
    await session.commit()
    await session.refresh(entry)
    assert entry.trace_context == {"traceparent": "00-aa-bb-01"}


@pytest.mark.asyncio
async def test_reenqueue_replaces_trace_context(session) -> None:
    from app.services import queue as run_queue

    entry = await run_queue.enqueue(
        session,
        run_id="run-tc2",
        workflow_id="wf-1",
        trace_context={"traceparent": "00-old-old-01"},
    )
    entry.status = "failed"  # terminal → revival path
    await session.commit()
    revived = await run_queue.enqueue(
        session,
        run_id="run-tc2",
        workflow_id="wf-1",
        trace_context={"traceparent": "00-new-new-01"},
    )
    await session.commit()
    assert revived.trace_context == {"traceparent": "00-new-new-01"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_run_queue.py -v -k trace_context`
Expected: FAIL with `TypeError: enqueue() got an unexpected keyword argument 'trace_context'`

- [ ] **Step 3: Add the model column**

In `app/models.py`, directly after the `replay_seed` column on `RunQueueEntry`:

```python
    # W3C trace-context carrier injected at enqueue (program A5) so the worker
    # that leases this entry can parent its spans on the enqueueing request's
    # trace. None whenever tracing is disabled.
    trace_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

- [ ] **Step 4: Write the migration**

```python
# apps/api/alembic/versions/0049_queue_trace_context.py
"""A5 (architecture program Phase 5): run_queue.trace_context.

W3C trace-context carrier (``{"traceparent": ...}``) injected at enqueue so a
worker leasing the entry in another process can join the same trace. Additive
and nullable — NULL whenever tracing is disabled.

Revision ID: 0049_queue_trace_context
Revises: 0048_run_parent
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_queue_trace_context"
down_revision: str | None = "0048_run_parent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run_queue", sa.Column("trace_context", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("run_queue", "trace_context")
```

- [ ] **Step 5: Thread the parameter through `queue.enqueue`**

In `app/services/queue.py`, change the `enqueue` signature (add after `available_at`):

```python
    available_at: datetime | None = None,
    trace_context: dict | None = None,
) -> RunQueueEntry:
```

In the revival branch (`existing.status` reset block), after `existing.available_at = ...`:

```python
        existing.available_at = available_at or _now(None)
        existing.trace_context = trace_context
        return existing
```

In the fresh-entry constructor, after `available_at=...`:

```python
        available_at=available_at or _now(None),
        trace_context=trace_context,
    )
```

- [ ] **Step 6: Run the queue tests**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_run_queue.py -v`
Expected: all PASS (new + existing).

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/models.py apps/api/alembic/versions/0049_queue_trace_context.py apps/api/app/services/queue.py apps/api/tests/test_run_queue.py
git commit -m "feat(api): run_queue.trace_context carrier column + enqueue param (A5, migration 0049)"
```

### Task 4: Wire the run spans through the runner

**Files:**
- Modify: `apps/api/app/services/runner.py` (`start_run`, `_execute_run`, `_execute_queued_entry`)
- Test: `apps/api/tests/test_tracing.py` (append integration test)

- [ ] **Step 1: Write the failing integration test (append to `tests/test_tracing.py`)**

Boilerplate mirrors `tests/test_runs.py::test_run_executes_the_graph` (same `client` fixture and graph shape). Executor note: if the `client` fixture needs extra imports in this file, copy the exact import block from `test_runs.py`.

```python
from httpx import AsyncClient

GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual_trigger", "params": {"data": {"n": 3}},
         "position": {"x": 0, "y": 0}},
        {"id": "c", "type": "code", "params": {"code": "output = input['n'] * 2"},
         "position": {"x": 250, "y": 0}},
    ],
    "edges": [
        {"id": "e1", "source": "t", "source_output": "main",
         "target": "c", "target_input": "input"},
    ],
}


async def test_run_produces_connected_trace(client: AsyncClient, exporter) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Traced"})).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": GRAPH})
    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] == "success"

    spans = exporter.get_finished_spans()
    by_name: dict[str, list] = {}
    for s in spans:
        by_name.setdefault(s.name, []).append(s)

    assert len(by_name.get("run.enqueue", [])) == 1
    assert len(by_name.get("run.execute", [])) == 1
    node_spans = by_name.get("node.execute", [])
    assert {s.attributes["noodle.node_id"] for s in node_spans} == {"t", "c"}

    run_span = by_name["run.execute"][0]
    assert run_span.attributes["noodle.run_id"] == run_id
    assert run_span.attributes["noodle.workflow_id"] == workflow_id
    assert run_span.attributes["noodle.status"] == "success"

    # single connected trace: every span shares the enqueue span's trace id
    trace_id = by_name["run.enqueue"][0].context.trace_id
    assert all(s.context.trace_id == trace_id for s in spans)
    # node spans hang off run.execute
    assert all(
        s.parent is not None and s.parent.span_id == run_span.context.span_id
        for s in node_spans
    )
    # node types resolved from the graph
    types = {s.attributes["noodle.node_id"]: s.attributes["noodle.node_type"]
             for s in node_spans}
    assert types == {"t": "manual_trigger", "c": "code"}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_tracing.py::test_run_produces_connected_trace -v`
Expected: FAIL — `KeyError`/assert on missing `run.enqueue` spans (no spans recorded yet).

- [ ] **Step 3: Wire `start_run` — run.enqueue span + carrier**

In `app/services/runner.py`, add to the import block:

```python
from app import tracing
```

In `start_run`, replace the `await run_queue.enqueue(...)` call with a span-wrapped version that injects the carrier (keep the surrounding code identical):

```python
        # A5: the enqueue span is the trace root for this run (child of the
        # HTTP request span when FastAPI instrumentation is on). Its carrier
        # rides the queue entry so a worker in another process joins the trace.
        with tracing.span(
            "run.enqueue",
            attributes={"noodle.run_id": run_id, "noodle.workflow_id": workflow_id},
        ):
            trace_carrier = tracing.inject_context()
            await run_queue.enqueue(
                session,
                run_id=run_id,
                workflow_id=workflow_id,
                runner_pool_id=runner_pool_id,
                reason=(
                    ("dispatch_disabled" if settings.dispatch_role == "disabled" else "local_capacity")
                    if queue_locally
                    else "start_run"
                ),
                trace_context=trace_carrier,
            )
        await session.commit()
```

Then pass the carrier to both immediate-dispatch `_execute_run` calls (synchronous and `create_task` variants) as `trace_carrier=trace_carrier`:

```python
            await _execute_run(
                run_id, workflow_id, graph, targets, cache,
                prefer_draft=prefer_draft, runner_pool_id=runner_pool_id,
                trace_carrier=trace_carrier,
            )
```

(and the same keyword in the `asyncio.create_task(_execute_run(...))` call.)

- [ ] **Step 4: Wrap `_execute_run` — run.execute span + node synthesis**

Rename the existing `async def _execute_run(` to `async def _execute_run_impl(` and:

1. Add two keyword params to the END of `_execute_run_impl`'s signature:

```python
    agent_action_resume: dict[str, AgentActionRequest] | None = None,
    trace_org: str | None = None,
) -> str:
```

2. Make the impl return its terminal status: the `_QueuedError` early-return becomes `return "queued"` (replacing the bare `return` after `_log_run_id.reset(run_id_token)`), and the function's last line gains `return status` (after the final `_log_run_id.reset(run_id_token)`).

3. Inside the impl, just before the `async def on_event(event: dict)` definition, build the node-type map (cheap; only consulted when tracing is on):

```python
    trace_node_types: dict[str, str] = (
        {str(n.get("id")): str(n.get("type") or "")
         for n in graph_dict.get("nodes", []) if isinstance(n, dict)}
        if tracing.enabled()
        else {}
    )
```

4. In `on_event`, right after `broker.publish(run_id, clean)`, synthesize the node span:

```python
        broker.publish(run_id, clean)
        if clean.get("type") == "node_finished":
            tracing.record_node_span(
                clean, node_types=trace_node_types, org_id=trace_org
            )
        if clean.get("type") == "node_finished":
```

(Executor note: merge into the existing `if clean.get("type") == "node_finished":` block as its first statement instead of duplicating the condition — shown split here only for diff clarity.)

5. Add the new thin wrapper named `_execute_run` (same public name, so all call sites, tests, and monkeypatches keep working) directly above `_execute_run_impl`:

```python
async def _execute_run(
    run_id: str,
    workflow_id: str,
    graph_dict: dict,
    targets: list[str] | None,
    cache: dict[str, dict] | None = None,
    *,
    prefer_draft: bool = False,
    runner_pool_id: str | None = None,
    agent_action_resume: dict[str, AgentActionRequest] | None = None,
    trace_carrier: dict | None = None,
) -> None:
    """Tracing wrapper: opens the run.execute span (parented on ``trace_carrier``
    when given, else ambient context) around the real executor. A plain
    pass-through when tracing is off."""
    if not tracing.enabled():
        await _execute_run_impl(
            run_id, workflow_id, graph_dict, targets, cache,
            prefer_draft=prefer_draft, runner_pool_id=runner_pool_id,
            agent_action_resume=agent_action_resume,
        )
        return
    org_id = await _resolve_run_org(run_id)
    attrs = {"noodle.run_id": run_id, "noodle.workflow_id": workflow_id}
    if org_id:
        attrs["noodle.org_id"] = org_id
    with tracing.span("run.execute", carrier=trace_carrier, attributes=attrs) as sp:
        status = await _execute_run_impl(
            run_id, workflow_id, graph_dict, targets, cache,
            prefer_draft=prefer_draft, runner_pool_id=runner_pool_id,
            agent_action_resume=agent_action_resume, trace_org=org_id,
        )
        if sp is not None:
            sp.set_attribute("noodle.status", status)
```

(`_resolve_run_org` is already imported from `app.services.runtime_pool` at the top of runner.py.)

- [ ] **Step 5: Wire `_execute_queued_entry` — run.lease span**

In `_execute_queued_entry`, capture the stored carrier while the queue entry is loaded (right after the `replay_seed` extraction):

```python
        entry_trace_carrier: dict | None = (
            dict(queue_entry.trace_context)
            if queue_entry is not None and queue_entry.trace_context
            else None
        )
```

Then, just before the final `await _execute_run(...)` call, mint the lease span and a fresh carrier (the lease span is brief by design — it marks the worker-side pickup, not the execution):

```python
    # A5: run.lease marks the worker-side pickup; run.execute parents on it.
    with tracing.span(
        "run.lease",
        carrier=entry_trace_carrier,
        attributes={"noodle.run_id": run_id, "noodle.workflow_id": workflow_id},
    ):
        lease_carrier = tracing.inject_context() or entry_trace_carrier

    await _execute_run(
        run_id, workflow_id, graph_dict, targets, cache,
        prefer_draft=(mode in ("manual", "test")),
        runner_pool_id=runner_pool_id,
        agent_action_resume=agent_action_resume,
        trace_carrier=lease_carrier,
    )
```

- [ ] **Step 6: Run the integration test, then the full API suite**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_tracing.py -v`
Expected: all PASS.
Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests -q`
Expected: all PASS except the known pre-existing failure.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/services/runner.py apps/api/tests/test_tracing.py
git commit -m "feat(api): run.enqueue/run.lease/run.execute spans + host-side node.execute synthesis (A5)"
```

### Task 5: Process lifecycle + instrumentation + ops surface

**Files:**
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/worker_main.py`
- Modify: `apps/api/app/schemas.py` (`RuntimeModeStatus`, ~line 885)
- Modify: `apps/api/app/routers/ops.py` (`runtime_mode`, ~line 65)
- Test: `apps/api/tests/test_tracing.py` (append)

- [ ] **Step 1: Write the failing ops test (append to `tests/test_tracing.py`)**

```python
async def test_runtime_mode_reports_otel_flag(client: AsyncClient) -> None:
    body = (await client.get("/ops/runtime-mode")).json()
    assert body["otel_enabled"] is False
```

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_tracing.py::test_runtime_mode_reports_otel_flag -v`
Expected: FAIL with `KeyError: 'otel_enabled'`

- [ ] **Step 2: Surface the flag on `/ops/runtime-mode`**

`app/schemas.py` — add to `RuntimeModeStatus` after `allow_insecure: bool`:

```python
    allow_insecure: bool
    otel_enabled: bool
```

`app/routers/ops.py` — add to the `RuntimeModeStatus(...)` constructor after `allow_insecure=...`:

```python
        allow_insecure=settings.runtime_allow_insecure,
        otel_enabled=settings.otel_enabled,
```

- [ ] **Step 3: Initialise tracing in the API process (`app/main.py`)**

Add to the import block:

```python
from app import tracing
```

Directly after the `app = FastAPI(...)` construction (and before middleware registration):

```python
# A5: tracing is initialised at import so FastAPI/SQLAlchemy instrumentation
# wraps everything from the first request. All three calls no-op when
# settings.otel_enabled is false.
tracing.setup_tracing("noodle-api")
tracing.instrument_app(app)
tracing.instrument_sqlalchemy(engine)
```

In the lifespan teardown, after `await _bounded(expr_preview.shutdown())`:

```python
    tracing.flush()  # push buffered spans before the process winds down
```

- [ ] **Step 4: Initialise tracing in the worker (`app/worker_main.py`)**

Add to the import block:

```python
from app import tracing
```

In `_amain()`, directly after `_validate()`:

```python
    tracing.setup_tracing("noodle-worker")
    tracing.instrument_sqlalchemy(engine)
```

In the `finally` block, before `await engine.dispose()`:

```python
        with contextlib.suppress(Exception):
            tracing.flush()
```

- [ ] **Step 5: Run the test + full API suite**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_tracing.py tests/test_ops.py -q` (if `tests/test_ops.py` doesn't exist, run just test_tracing.py)
Expected: PASS.
Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests -q`
Expected: all PASS except the known pre-existing failure.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/main.py apps/api/app/worker_main.py apps/api/app/schemas.py apps/api/app/routers/ops.py apps/api/tests/test_tracing.py
git commit -m "feat(api): tracing lifecycle in api+worker, FastAPI/SQLAlchemy instrumentation, ops otel flag (A5)"
```

### Task 6: Docs + full verification

**Files:**
- Modify: `docs/deployment.md`
- Modify: `docs/architecture.md` (only if it has an observability section — check first)

- [ ] **Step 1: Add a tracing section to `docs/deployment.md`**

Find the operations/observability area of the doc (near the runtime-mode / ops material) and add:

```markdown
## Distributed tracing (OpenTelemetry)

Off by default. To enable, set on every API replica **and** worker:

    OTEL_ENABLED=true
    OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318/v1/traces   # OTLP/HTTP

Each run produces one trace: `run.enqueue` (API, child of the HTTP request
span) → `run.lease` (the worker that picked the entry up) → `run.execute` →
one `node.execute` span per node with `noodle.node_id`, `noodle.node_type`,
`noodle.status`, `noodle.org_id`, and `noodle.iteration_path` attributes.
Node spans carry the engine's real start/finish timestamps, including for
nodes executed inside runtime subprocesses — the subprocesses themselves
need no OTel dependencies. Trace context crosses the API→worker boundary on
the durable queue row (`run_queue.trace_context`), so split topologies get
the same single connected trace. FastAPI requests and SQLAlchemy queries are
auto-instrumented in the API process. When disabled, no SDK objects exist
and every hook is a single boolean check. `/ops/runtime-mode` reports
`otel_enabled` so you can confirm what a replica is actually running.
```

- [ ] **Step 2: Run all affected suites (SEQUENTIALLY — never two at once on this box)**

```powershell
cd apps\api;       ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd packages\core;  ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd apps\web;       npm run test:e2e
```

Expected: API green (+1 known pre-existing fail), core 343 green (engine untouched — confirms no accidental coupling), e2e 3/3.

- [ ] **Step 3: Manual acceptance (best-effort on this box)**

If Docker Desktop is running: start a local collector (`docker run --rm -p 4318:4318 otel/opentelemetry-collector`), boot the API with `OTEL_ENABLED=true`, run a workflow from the editor, and confirm the collector logs one trace containing run.enqueue/run.execute/node.execute. If Docker isn't available, note it as deferred (the InMemory-exporter integration test covers the span tree; only the OTLP wire hop is unverified).

- [ ] **Step 4: Commit**

```bash
git add docs/deployment.md
git commit -m "docs: OpenTelemetry tracing setup + span-tree reference (A5)"
```

---

## Self-review notes

- **Spec coverage vs master plan Phase 5:** deps ✔ (Task 1), config off-by-default ✔ (Task 1), span tree enqueue→lease→execute→node with required attributes ✔ (Tasks 2+4), trace context through `RunQueueEntry.trace_context` additive migration ✔ (Task 3), subprocess propagation **replaced** by host-side synthesis from the event channel (deviation documented in the header — acceptance still met), single-connected-trace acceptance ✔ (Task 4 integration test + Task 6 manual check), ops/docs mention ✔ (Tasks 5+6).
- **Zero-overhead-when-off:** every hook (`span`, `inject_context`, `record_node_span`, instrumentors, `_execute_run` wrapper) returns after one module-boolean check; no OTel imports execute at module import time (all SDK imports are inside `setup_tracing`/helpers).
- **Type consistency:** carrier is `dict | None` end-to-end (`inject_context` → `enqueue(trace_context=)` → `RunQueueEntry.trace_context` → `_execute_queued_entry` → `span(carrier=)`); `_execute_run_impl` returns `str` status consumed only by the wrapper.
- **Known executor adjustments:** integration-test imports/fixtures must match `tests/test_runs.py`'s conventions; the `on_event` node-span hook merges into the existing `node_finished` branch; `docs/deployment.md` section placement is judgement-based.
