"""
Production fix tests — RED phase.

Each test targets a specific bug found in the production audit.
Tests are written before the fix; they must FAIL before and PASS after.
"""

import asyncio
import time
from collections import deque
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fix 1 — runner.py:1347
# run.webhook_response written outside the `if run is not None:` guard
# ---------------------------------------------------------------------------

def test_runner_webhook_response_inside_null_guard():
    """run.webhook_response assignment must be inside the run is not None guard.

    Before the fix, runner.py assigned run.webhook_response OUTSIDE the guard,
    causing AttributeError when the run was deleted mid-execution. The terminal
    persistence block now lives in run_persistence.persist_run_outcome (A2
    split); the invariant is unchanged.
    """
    import inspect

    from app.services import run_persistence

    source = inspect.getsource(run_persistence.persist_run_outcome)
    lines = source.splitlines()

    in_run_none_guard = False
    indent_of_guard = None
    webhook_assignment_guarded = None

    for line in lines:
        stripped = line.strip()
        # Detect entry into `if run is not None:` block
        if stripped == "if run is not None:":
            in_run_none_guard = True
            indent_of_guard = len(line) - len(line.lstrip())
        elif in_run_none_guard and stripped:
            current_indent = len(line) - len(line.lstrip())
            # If we're back at or before the guard's indent level, we left the block
            if not stripped.startswith("#") and current_indent <= indent_of_guard:
                in_run_none_guard = False

        if "run.webhook_response" in stripped:
            webhook_assignment_guarded = in_run_none_guard

    assert webhook_assignment_guarded is not None, \
        "run.webhook_response assignment not found in _execute_run"
    assert webhook_assignment_guarded, \
        "run.webhook_response must be inside 'if run is not None:' guard " \
        "(AttributeError crash when run is deleted mid-execution)"


# ---------------------------------------------------------------------------
# Fix 4 — triggers.py:1123
# workflow.versions[-1] without guard raises IndexError for zero-version workflows
# ---------------------------------------------------------------------------

def test_scheduler_tick_guards_empty_versions():
    """_tick must not raise IndexError for workflows with no versions.

    Before the fix, the fallback schedule loop accesses workflow.versions[-1]
    unconditionally. On a workflow with an empty versions list this raises
    IndexError, which propagates through scheduler_loop and kills the tick.
    """
    import inspect
    from app.services import triggers as triggers_module

    source = inspect.getsource(triggers_module._tick)

    # The fix should add an explicit empty-versions guard before the [-1] access.
    # Check that the guard is present.
    lines = source.splitlines()
    found_guard = False
    for line in lines:
        stripped = line.strip()
        if (
            "not workflow.versions" in stripped
            or "len(workflow.versions)" in stripped
            or "workflow.versions:" in stripped and stripped.startswith("if")
        ):
            found_guard = True
            break

    assert found_guard, (
        "triggers._tick accesses workflow.versions[-1] without a length guard. "
        "IndexError is raised for newly-created workflows with no versions yet, "
        "killing the entire scheduler tick. "
        "Fix: add 'if not workflow.versions: continue' before workflow.versions[-1]"
    )


# ---------------------------------------------------------------------------
# Fix 7 — retention.py:79
# old_ids loaded without LIMIT — can load millions of UUIDs into memory
# ---------------------------------------------------------------------------

def test_retention_prune_uses_batched_deletes():
    """prune_old_runs should process large result sets in chunks, not load all IDs at once.

    Before the fix, the window-function query loads every exceeding-cap ID
    into a Python list. With millions of runs this spikes RAM and risks timeout.
    The fix fetches and deletes in batches of at most BATCH_SIZE.
    """
    import inspect
    from app.services import retention

    source = inspect.getsource(retention.prune_old_runs)

    # The fix should introduce a LIMIT or batch size constant
    has_batch = (
        "BATCH" in source
        or "limit(" in source.lower()
        or "_PRUNE_BATCH" in source
        or "batch_size" in source.lower()
        or "chunk" in source.lower()
    )
    assert has_batch, (
        "retention.prune_old_runs should use batched deletes (LIMIT/BATCH_SIZE) "
        "to avoid loading all run IDs into memory at once"
    )


# ---------------------------------------------------------------------------
# Fix 8 — remote_dispatch.py:394
# asyncio.get_event_loop().create_future() deprecated; may use wrong loop on Py 3.12
# ---------------------------------------------------------------------------

def test_remote_dispatch_uses_get_running_loop():
    """RemoteDispatch must use asyncio.get_running_loop(), not get_event_loop().

    On Python 3.12+, get_event_loop() in a thread-pool context may return a
    different loop, causing futures to resolve on the wrong loop and hang.
    """
    import inspect
    from app.services import remote_dispatch

    source = inspect.getsource(remote_dispatch)

    assert "get_event_loop().create_future" not in source, (
        "remote_dispatch uses asyncio.get_event_loop().create_future() which is "
        "deprecated and may return the wrong loop on Python 3.12+. "
        "Use asyncio.get_running_loop().create_future() instead."
    )


# ---------------------------------------------------------------------------
# Fix 9 — auth.py:45
# _AUTH_RATE_BUCKETS never evicts empty deque keys — unbounded memory leak
# ---------------------------------------------------------------------------

def test_rate_limiter_sweeps_stale_buckets():
    """_sweep_rate_buckets must remove keys whose full 60s window has expired.

    Before the fix, every unique IP creates an immortal dict entry. After the
    1-minute sliding window, the deque drains to zero but the key stays forever,
    leaking memory under IP-spoofing or many unique clients.
    The fix adds _sweep_rate_buckets which removes fully-expired keys.
    """
    from app.routers import auth as auth_module

    auth_module._AUTH_RATE_BUCKETS.clear()

    stale_key = "test:192.0.2.1"
    auth_module._AUTH_RATE_BUCKETS[stale_key].append(time.monotonic() - 120)

    # The sweep function (added by the fix) should remove the stale key
    assert hasattr(auth_module, "_sweep_rate_buckets"), (
        "_sweep_rate_buckets function not found in auth module. "
        "Fix: add a _sweep_rate_buckets(now) function that removes keys "
        "with fully-expired sliding windows."
    )
    auth_module._sweep_rate_buckets(time.monotonic())

    assert stale_key not in auth_module._AUTH_RATE_BUCKETS, (
        "_sweep_rate_buckets did not remove a key with a fully-expired window. "
        "Fix: evict keys where all timestamps are older than 60s."
    )


# ---------------------------------------------------------------------------
# Fix 5 — runner.py:1013
# run_events list grows unbounded for long agentic runs (OOM)
# ---------------------------------------------------------------------------

def test_runner_run_events_has_cap():
    """run_events list must be bounded to prevent OOM on long agentic runs.

    Before the fix, every agent step appends to run_events without limit.
    With max_steps=500 and 256KB per capped event, peak RAM is ~128MB per run.
    """
    import inspect
    from app.services import runner as runner_module

    # _execute_run is a thin tracing wrapper (A5); the execution body —
    # where run_events accumulates — is _execute_run_impl.
    source = inspect.getsource(runner_module._execute_run_impl)

    # The fix should introduce a cap on the run_events list
    has_cap = (
        "MAX_RUN_EVENTS" in source
        or "_MAX_RUN_EVENTS" in source
        or "max_run_events" in source.lower()
        or "run_events_cap" in source.lower()
        or ("run_events" in source and ("[-" in source or "maxlen" in source or "deque" in source))
    )
    assert has_cap, (
        "run_events list in _execute_run has no length cap. "
        "Add a MAX_RUN_EVENTS constant and trim/use collections.deque(maxlen=...) "
        "to prevent OOM on long agentic runs."
    )
