"""Smoke test: spawn the runtime subprocess and run a real workflow through it."""

import asyncio
import json
import sys
from decimal import Decimal

import pytest

from nodyra.engine.subworkflows import SubworkflowCall
from nodyra.serialization import serialize_value
from nodyra_runtime import server
from nodyra_runtime.server import _needs_host_callbacks

GRAPH = {
    "nodes": [
        {
            "id": "t",
            "type": "manual_trigger",
            "params": {"data": {"n": 3}},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "c",
            "type": "code",
            "params": {"code": "output = input['n'] * 2"},
            "position": {"x": 200, "y": 0},
        },
    ],
    "edges": [
        {
            "id": "e",
            "source": "t",
            "source_output": "main",
            "target": "c",
            "target_input": "input",
        }
    ],
}


async def test_runtime_subprocess_executes_a_graph() -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        "-m",
        "nodyra_runtime",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None

    try:
        ready_line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
        ready = json.loads(ready_line)
        assert ready["type"] == "ready"
        assert isinstance(ready["startup_ms"], int)
        assert ready["startup_ms"] >= 0

        request = {
            "type": "run",
            "request_id": "abc",
            "graph": GRAPH,
            "cache": None,
            "targets": None,
        }
        process.stdin.write((json.dumps(request) + "\n").encode())
        await process.stdin.drain()

        finished: dict[str, dict] = {}
        status = ""
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
            event = json.loads(line)
            kind = event.get("type")
            if kind == "node_finished":
                finished[event["node_id"]] = event
            elif kind == "result":
                status = event["status"]
                break
            elif kind == "error":
                raise AssertionError(f"runtime error: {event.get('error')}")

        assert status == "success"
        assert finished["c"]["outputs"]["main"] == 6
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()


LOOP_GRAPH = {
    "nodes": [
        {
            "id": "trig",
            "type": "manual_trigger",
            "params": {"data": [1, 2, 3]},
            "position": {"x": 0, "y": 0},
        },
        {"id": "s", "type": "loop_start", "params": {}, "position": {"x": 1, "y": 0}},
        {
            "id": "b",
            "type": "code",
            "params": {"code": "output = input * 2"},
            "position": {"x": 2, "y": 0},
        },
        {
            "id": "e",
            "type": "loop_end",
            "params": {"loop_start_id": "s"},
            "position": {"x": 3, "y": 0},
        },
    ],
    "edges": [
        {
            "id": "trig->s",
            "source": "trig",
            "source_output": "main",
            "target": "s",
            "target_input": "input",
        },
        {
            "id": "s->b",
            "source": "s",
            "source_output": "item",
            "target": "b",
            "target_input": "input",
        },
        {
            "id": "b->e",
            "source": "b",
            "source_output": "main",
            "target": "e",
            "target_input": "input",
        },
    ],
}


def test_loop_graph_does_not_need_host_callbacks():
    msg = {"type": "run", "graph": LOOP_GRAPH}
    assert _needs_host_callbacks(msg) is False


async def test_runtime_emits_heartbeats_while_run_is_active(monkeypatch) -> None:
    events: list[dict] = []

    class _Result:
        status = "success"

    async def _execute(*_args, **_kwargs):
        await asyncio.sleep(0.03)
        return _Result()

    monkeypatch.setattr(server, "execute", _execute)
    monkeypatch.setattr(server, "_RUNTIME_HEARTBEAT_SECONDS", 0.005)
    monkeypatch.setattr(server, "_emit", events.append)

    await server._handle_run(
        {
            "type": "run",
            "request_id": "heartbeat",
            "run_id": "run-heartbeat",
            "graph": GRAPH,
            "cache": None,
            "targets": None,
        }
    )

    assert any(event.get("type") == "heartbeat" for event in events)
    assert events[-1]["type"] == "result"


async def test_referenced_module_failure_reports_the_original_error(monkeypatch) -> None:
    events: list[dict] = []

    def reject_module(*_args, **_kwargs):
        raise ValueError("module import rejected by policy")

    monkeypatch.setattr(server, "register_module_functions", reject_module)
    monkeypatch.setattr(server, "_emit", events.append)
    await server._handle_run(
        {
            "type": "run",
            "request_id": "broken-module",
            "graph": {
                "nodes": [{"id": "custom", "type": "user:broken:transform", "params": {}}],
                "edges": [],
            },
            "workflow_modules": [{"id": "broken", "contents": "invalid module"}],
        }
    )
    assert events[-1]["type"] == "error"
    assert events[-1]["error"] == (
        "module registration failed: ValueError: module import rejected by policy"
    )
    assert not server._pending_callbacks


async def test_runtime_subprocess_runs_a_loop() -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        "-m",
        "nodyra_runtime",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None

    try:
        ready_line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
        assert json.loads(ready_line)["type"] == "ready"

        request = {
            "type": "run",
            "request_id": "loop",
            "graph": LOOP_GRAPH,
            "cache": None,
            "targets": None,
        }
        process.stdin.write((json.dumps(request) + "\n").encode())
        await process.stdin.drain()

        finished: dict[str, dict] = {}
        status = ""
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
            event = json.loads(line)
            kind = event.get("type")
            if kind == "node_finished":
                finished[event["node_id"]] = event
            elif kind == "result":
                status = event["status"]
                break
            elif kind == "error":
                raise AssertionError(f"runtime error: {event.get('error')}")

        assert status == "success"
        assert finished["e"]["outputs"]["results"] == [2, 4, 6]
        assert finished["e"]["outputs"]["errors"] == []
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()


BATCH_GRAPH = {
    "nodes": [
        {
            "id": "trig",
            "type": "manual_trigger",
            "params": {"data": [1, 2, 3, 4, 5]},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "s",
            "type": "loop_start",
            "params": {"mode": "batch", "batch_size": 2},
            "position": {"x": 1, "y": 0},
        },
        {
            "id": "b",
            "type": "code",
            "params": {"code": "output = sum(input)"},
            "position": {"x": 2, "y": 0},
        },
        {
            "id": "e",
            "type": "loop_end",
            "params": {"loop_start_id": "s"},
            "position": {"x": 3, "y": 0},
        },
    ],
    "edges": [
        {
            "id": "trig->s",
            "source": "trig",
            "source_output": "main",
            "target": "s",
            "target_input": "input",
        },
        {
            "id": "s->b",
            "source": "s",
            "source_output": "item",
            "target": "b",
            "target_input": "input",
        },
        {
            "id": "b->e",
            "source": "b",
            "source_output": "main",
            "target": "e",
            "target_input": "input",
        },
    ],
}

WHILE_GRAPH = {
    "nodes": [
        {
            "id": "s",
            "type": "loop_start",
            "params": {
                "mode": "while",
                "initial": {"count": 0},
                "condition": "{{ state.count < 3 }}",
            },
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "b",
            "type": "code",
            "params": {"code": "output = {'count': input['count'] + 1}"},
            "position": {"x": 1, "y": 0},
        },
        {
            "id": "e",
            "type": "loop_end",
            "params": {"loop_start_id": "s"},
            "position": {"x": 2, "y": 0},
        },
    ],
    "edges": [
        {
            "id": "s->b",
            "source": "s",
            "source_output": "state",
            "target": "b",
            "target_input": "input",
        },
        {
            "id": "b->e",
            "source": "b",
            "source_output": "main",
            "target": "e",
            "target_input": "input",
        },
    ],
}


async def _run_via_subprocess(graph: dict, request_id: str) -> tuple[str, dict[str, dict]]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        "-m",
        "nodyra_runtime",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        ready = await asyncio.wait_for(process.stdout.readline(), timeout=10)
        assert json.loads(ready)["type"] == "ready"
        request = {
            "type": "run",
            "request_id": request_id,
            "graph": graph,
            "cache": None,
            "targets": None,
        }
        process.stdin.write((json.dumps(request) + "\n").encode())
        await process.stdin.drain()
        finished: dict[str, dict] = {}
        status = ""
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
            event = json.loads(line)
            kind = event.get("type")
            if kind == "node_finished":
                finished[event["node_id"]] = event
            elif kind == "result":
                status = event["status"]
                break
            elif kind == "error":
                raise AssertionError(f"runtime error: {event.get('error')}")
        return status, finished
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()


def test_batch_and_while_loops_do_not_need_host_callbacks():
    assert _needs_host_callbacks({"type": "run", "graph": BATCH_GRAPH}) is False
    assert _needs_host_callbacks({"type": "run", "graph": WHILE_GRAPH}) is False


async def test_runtime_subprocess_runs_a_batch_loop() -> None:
    status, finished = await _run_via_subprocess(BATCH_GRAPH, "batch")
    assert status == "success"
    assert finished["e"]["outputs"]["results"] == [3, 7, 5]  # [1+2, 3+4, 5]


async def test_runtime_subprocess_runs_a_while_loop() -> None:
    status, finished = await _run_via_subprocess(WHILE_GRAPH, "while")
    assert status == "success"
    assert finished["e"]["outputs"]["results"] == {"count": 3}


async def test_runtime_serializes_events_and_deserializes_cache() -> None:
    graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {"data": None},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "c",
                "type": "code",
                "params": {"code": "output = type(input).__name__"},
                "position": {"x": 200, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e",
                "source": "t",
                "source_output": "main",
                "target": "c",
                "target_input": "input",
            }
        ],
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        "-m",
        "nodyra_runtime",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None

    try:
        ready_line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
        ready = json.loads(ready_line)
        assert ready["type"] == "ready"

        request = {
            "type": "run",
            "request_id": "typed",
            "graph": graph,
            "cache": {"t": {"main": serialize_value(Decimal("1.25"))}},
            "targets": None,
        }
        process.stdin.write((json.dumps(request) + "\n").encode())
        await process.stdin.drain()

        finished: dict[str, dict] = {}
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
            event = json.loads(line)
            kind = event.get("type")
            if kind == "node_finished":
                finished[event["node_id"]] = event
            elif kind == "result":
                break
            elif kind == "error":
                raise AssertionError(f"runtime error: {event.get('error')}")

        assert finished["t"]["outputs"]["main"]["type"] == "decimal"
        assert finished["c"]["outputs"]["main"] == "Decimal"
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()


async def test_runtime_subprocess_does_not_inject_artifacts_into_code_nodes() -> None:
    """Isolated code nodes can't write artifacts; `artifacts` is not in scope.

    Artifacts now flow as refs from dedicated nodes, not from sandboxed user
    code. A code node referencing `artifacts` should fail cleanly rather than
    silently produce one.
    """
    graph = {
        "nodes": [
            {
                "id": "writer",
                "type": "code",
                "params": {"code": "output = artifacts.write_text('hello', name='hello.txt')"},
                "position": {"x": 0, "y": 0},
            }
        ],
        "edges": [],
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        "-m",
        "nodyra_runtime",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None

    try:
        ready_line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
        assert json.loads(ready_line)["type"] == "ready"

        request = {
            "type": "run",
            "request_id": "artifact",
            "run_id": "run-artifact",
            "graph": graph,
            "cache": None,
            "targets": None,
        }
        process.stdin.write((json.dumps(request) + "\n").encode())
        await process.stdin.drain()

        status = None
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
            event = json.loads(line)
            kind = event.get("type")
            if kind == "result":
                status = event.get("status")
                break
            if kind == "error":
                status = "error"
                break

        # The run fails because `artifacts` is not defined in the sandbox.
        assert status == "error"
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()


# ---------------------------------------------------------------------------
# _needs_host_callbacks unit tests
# ---------------------------------------------------------------------------


def _msg(node_type: str) -> dict:
    return {
        "type": "run",
        "graph": {
            "nodes": [{"id": "n1", "type": node_type, "params": {}, "position": {"x": 0, "y": 0}}],
            "edges": [],
        },
    }


@pytest.mark.parametrize(
    "node_type",
    ["execute_workflow", "map_items", "map_group", "map_dataset", "mcp_tool"],
)
def test_needs_host_callbacks_true_for_callback_nodes(node_type: str) -> None:
    assert _needs_host_callbacks(_msg(node_type)) is True


@pytest.mark.parametrize("node_type", ["code", "manual_trigger", "http_request", "csv_parse"])
def test_needs_host_callbacks_false_for_plain_nodes(node_type: str) -> None:
    assert _needs_host_callbacks(_msg(node_type)) is False


def test_needs_host_callbacks_mixed_graph() -> None:
    msg = {
        "type": "run",
        "graph": {
            "nodes": [
                {"id": "a", "type": "manual_trigger", "params": {}, "position": {"x": 0, "y": 0}},
                {"id": "b", "type": "map_items", "params": {}, "position": {"x": 100, "y": 0}},
            ],
            "edges": [],
        },
    }
    assert _needs_host_callbacks(msg) is True


def test_needs_host_callbacks_empty_graph() -> None:
    assert _needs_host_callbacks({"type": "run", "graph": {"nodes": [], "edges": []}}) is False


def test_needs_host_callbacks_true_for_call_workflow_hooks() -> None:
    # HK-1: a call_workflow hook round-trips through the host even though no
    # callback-typed node is present; the worker must stay on the concurrent
    # path or the main loop deadlocks awaiting a response it never reads.
    msg = {
        "type": "run",
        "graph": {
            "nodes": [
                {
                    "id": "n1",
                    "type": "code",
                    "params": {"code": "output = 1"},
                    "hooks": [
                        {
                            "trigger": "on_success",
                            "type": "call_workflow",
                            "config": {"workflow_id": "other"},
                        }
                    ],
                    "position": {"x": 0, "y": 0},
                }
            ],
            "edges": [],
        },
    }
    assert _needs_host_callbacks(msg) is True


def test_needs_host_callbacks_ignores_non_callback_hooks() -> None:
    msg = {
        "type": "run",
        "graph": {
            "nodes": [
                {
                    "id": "n1",
                    "type": "code",
                    "params": {"code": "output = 1"},
                    "hooks": [{"trigger": "on_success", "type": "log", "config": {}}],
                    "position": {"x": 0, "y": 0},
                }
            ],
            "edges": [],
        },
    }
    assert _needs_host_callbacks(msg) is False


def test_needs_host_callbacks_missing_graph() -> None:
    assert _needs_host_callbacks({"type": "run"}) is False


@pytest.mark.asyncio
async def test_subworkflow_callback_carries_active_request_id(monkeypatch) -> None:
    emitted: list[dict] = []
    monkeypatch.setattr(server, "_emit", emitted.append)
    server._active_request_id = "request-123"
    call = SubworkflowCall(
        workflow_id="child",
        parameters={"value": 1},
        use_published=True,
        parent_run_id="parent-run",
        depth=1,
    )
    task = asyncio.create_task(server._run_subworkflow_via_host(call))
    try:
        for _ in range(20):
            if emitted:
                break
            await asyncio.sleep(0)
        assert emitted[0]["request_id"] == "request-123"
        server._resolve_callback(
            {
                "type": "call_workflow_response",
                "callback_id": emitted[0]["callback_id"],
                "result": {"ok": True},
            }
        )
        assert await task == {"ok": True}
    finally:
        server._active_request_id = ""
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_mcp_tool_callback_round_trip(monkeypatch) -> None:
    """mcp_tool nodes dispatch through RuntimeContext.call_mcp_tool; the
    runtime must round-trip the call to the host and resolve the answer."""
    emitted: list[dict] = []
    monkeypatch.setattr(server, "_emit", emitted.append)
    server._active_request_id = "request-mcp"
    task = asyncio.create_task(server._call_mcp_tool_via_host("conn-1", "echo", {"message": "hi"}))
    try:
        for _ in range(20):
            if emitted:
                break
            await asyncio.sleep(0)
        assert emitted[0]["type"] == "call_mcp_tool"
        assert emitted[0]["request_id"] == "request-mcp"
        assert emitted[0]["connection_id"] == "conn-1"
        assert emitted[0]["tool_name"] == "echo"
        assert emitted[0]["arguments"] == {"message": "hi"}
        server._resolve_callback(
            {
                "type": "call_mcp_tool_response",
                "callback_id": emitted[0]["callback_id"],
                "result": {"ok": True},
            }
        )
        assert await task == {"ok": True}
    finally:
        server._active_request_id = ""
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_mcp_tool_callback_error_path(monkeypatch) -> None:
    emitted: list[dict] = []
    monkeypatch.setattr(server, "_emit", emitted.append)
    task = asyncio.create_task(server._call_mcp_tool_via_host("conn-1", "nope", {}))
    try:
        for _ in range(20):
            if emitted:
                break
            await asyncio.sleep(0)
        server._resolve_callback(
            {
                "type": "call_mcp_tool_error",
                "callback_id": emitted[0]["callback_id"],
                "error": "Unknown tool: nope",
            }
        )
        with pytest.raises(RuntimeError, match="Unknown tool"):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
