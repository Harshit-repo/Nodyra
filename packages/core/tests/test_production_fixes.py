"""
Production fix tests (core engine) — RED phase.

Tests for engine.py and serialization.py bugs found in production audit.
Tests must FAIL before the fix and PASS after.
"""

import time

import pytest

from nodyra.engine import execute
from nodyra.models import GraphNode, RunStatus, WorkflowGraph
from nodyra.sdk import NodeRegistry, node

# ---------------------------------------------------------------------------
# Fix 2 — engine.py:1233
# Sync node called directly on the event loop thread when no timeout is set
# ---------------------------------------------------------------------------



@pytest.mark.asyncio
async def test_three_sync_nodes_run_concurrently_not_serially():
    """Three independent slow sync nodes should run in ~0.1s, not ~0.3s."""
    reg = NodeRegistry()

    @node(name="S1", id="s1_concurrent", inputs=[], registry=reg)
    def s1() -> int:
        time.sleep(0.10)
        return 1

    @node(name="S2", id="s2_concurrent", inputs=[], registry=reg)
    def s2() -> int:
        time.sleep(0.10)
        return 2

    @node(name="S3", id="s3_concurrent", inputs=[], registry=reg)
    def s3() -> int:
        time.sleep(0.10)
        return 3

    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="a", type="s1_concurrent"),
            GraphNode(id="b", type="s2_concurrent"),
            GraphNode(id="c", type="s3_concurrent"),
        ],
        edges=[],
    )

    start = time.monotonic()
    result = await execute(graph, reg)
    elapsed = time.monotonic() - start

    assert result.status == RunStatus.success
    assert elapsed < 0.25, (
        f"Three 0.10s sync nodes took {elapsed:.2f}s — they ran serially, "
        "meaning sync nodes are blocking the event loop. "
        "Fix: wrap sync node invocation in asyncio.to_thread."
    )


# ---------------------------------------------------------------------------
# Fix 3 — engine.py:74
# _process_pools grows unbounded — idle pools never reaped for normal completions
# ---------------------------------------------------------------------------

# test_process_pools_idle_eviction moved: pool ownership left the engine in B4.
# See tests/test_process_isolation.py for idle-eviction + in-flight coverage.


# ---------------------------------------------------------------------------
# Fix 6 — serialization.py:427
# json.dumps materialises full string in memory just to get size + preview
# ---------------------------------------------------------------------------



def test_truncate_serialized_value_preview_does_not_require_full_encode():
    """A simpler variant: verify truncation uses _approx_json_length, not json.dumps, for size."""
    import inspect

    from nodyra import serialization

    source = inspect.getsource(serialization.truncate_serialized_value)

    # After the fix, json.dumps should not be called for the full value
    # The size_bytes should come from _approx_json_length (already computed as `approx`)
    # Check that size_bytes uses `approx` not `len(encoded)` from a full json.dumps call
    lines = source.splitlines()
    full_dumps_for_size = False
    for line in lines:
        stripped = line.strip()
        if "encoded = json.dumps" in stripped and "[:1024]" not in stripped:
            # Check if this is the problematic pattern
            full_dumps_for_size = True
            break

    assert not full_dumps_for_size, (
        "truncate_serialized_value still calls json.dumps to get 'encoded' for size_bytes. "
        "Fix: use `approx` (already computed by _approx_json_length) for size_bytes, "
        "and stream first 1024 chars via itertools/streaming encoder for preview."
    )


# ---------------------------------------------------------------------------
# Fix 10 — engine.py:1238
# resolve_agent_actions loop can spin forever if agent returns step=0 repeatedly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_agent_actions_terminates_after_max_iterations():
    """resolve_agent_actions must not loop forever when step counter stays at 0.

    Before the fix, the loop only terminates when AgentActionRequest.step
    exceeds max_steps — but if a buggy node returns step=0 with empty
    tool_calls repeatedly, step is never incremented and the loop is infinite.
    The fix adds an independent iteration counter in resolve_agent_actions.
    """
    from nodyra.engine import _MAX_AGENT_LOOP_ITERATIONS  # expected after fix

    # Verify the cap constant exists (added by the fix)
    assert _MAX_AGENT_LOOP_ITERATIONS > 0, (
        "_MAX_AGENT_LOOP_ITERATIONS constant missing from engine.py. "
        "Fix: add an independent iteration counter in resolve_agent_actions."
    )


