"""Smoke test: spawn the runtime subprocess and run a real workflow through it."""

import asyncio
import json
import sys
from decimal import Decimal

from noodle.serialization import serialize_value

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
        "noodle_runtime",
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
        "noodle_runtime",
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


async def test_runtime_writes_artifact_refs(tmp_path) -> None:
    graph = {
        "nodes": [
            {
                "id": "writer",
                "type": "code",
                "params": {
                    "code": "output = artifacts.write_text('hello', name='hello.txt')"
                },
                "position": {"x": 0, "y": 0},
            }
        ],
        "edges": [],
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        "-m",
        "noodle_runtime",
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
            "request_id": "artifact",
            "run_id": "run-artifact",
            "graph": graph,
            "cache": None,
            "targets": None,
            "artifacts_dir": str(tmp_path),
        }
        process.stdin.write((json.dumps(request) + "\n").encode())
        await process.stdin.drain()

        ref = None
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
            event = json.loads(line)
            kind = event.get("type")
            if kind == "node_finished":
                ref = event["outputs"]["main"]
            elif kind == "result":
                break
            elif kind == "error":
                raise AssertionError(f"runtime error: {event.get('error')}")

        assert ref["__noodle_artifact__"] is True
        assert ref["run_id"] == "run-artifact"
        assert ref["node_id"] == "writer"
        assert (tmp_path / ref["storage_key"]).read_text() == "hello"
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()


async def test_runtime_executes_inline_subworkflow_via_callback() -> None:
    """End-to-end: parent graph contains execute_workflow; the test
    plays the role of the host, replies to ``call_workflow`` with an
    ``inline_graph`` directive, and verifies the runtime runs the sub
    in the same process and returns its leaf as the parent node's
    output — no fresh subprocess required."""
    parent = {
        "nodes": [
            {"id": "p", "type": "manual_trigger", "params": {"data": {"x": 4}},
             "position": {"x": 0, "y": 0}},
            {"id": "call", "type": "execute_workflow",
             "params": {"workflow_id": "wf-inline"},
             "position": {"x": 200, "y": 0}},
        ],
        "edges": [{"id": "e", "source": "p", "source_output": "main",
                   "target": "call", "target_input": "input"}],
    }
    inline_sub = {
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {},
             "position": {"x": 0, "y": 0}},
            {"id": "c", "type": "code",
             "params": {"code": "output = input['x'] * 7"},
             "position": {"x": 200, "y": 0}},
        ],
        "edges": [{"id": "e", "source": "t", "source_output": "main",
                   "target": "c", "target_input": "input"}],
    }

    process = await asyncio.create_subprocess_exec(
        sys.executable, "-u", "-m", "noodle_runtime",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None

    try:
        ready = json.loads(
            await asyncio.wait_for(process.stdout.readline(), timeout=10)
        )
        assert ready["type"] == "ready"

        process.stdin.write((json.dumps({
            "type": "run", "request_id": "rid", "run_id": "run-inline",
            "graph": parent, "cache": None, "targets": None,
        }) + "\n").encode())
        await process.stdin.drain()

        finished: dict[str, dict] = {}
        saw_callback = False
        status = ""
        while True:
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=5)
            except TimeoutError:
                # Drain stderr so the failure message in pytest is useful.
                err = b""
                try:
                    err = await asyncio.wait_for(process.stderr.read(2048), timeout=0.5)
                except TimeoutError:
                    pass
                raise AssertionError(
                    f"runtime timed out; stderr={err.decode('utf-8', 'replace')!r}"
                ) from None
            event = json.loads(line)
            kind = event.get("type")
            if kind == "call_workflow":
                # Host plays inline-response: tells the runtime to run the
                # sub itself and use its leaf as the result.
                saw_callback = True
                # The sub seeds its trigger via cache; without a cache entry
                # the trigger node has no input, but execute_workflow forwards
                # the parent's input via the host. Emulate that here.
                cache_seed = {"t": {"main": event["input"]}}
                process.stdin.write((json.dumps({
                    "type": "call_workflow_response",
                    "callback_id": event["callback_id"],
                    "inline_graph": inline_sub,
                    "inline_cache": cache_seed,
                    "inline_targets": ["t", "c"],
                    "inline_sources": ["t"],
                }) + "\n").encode())
                await process.stdin.drain()
            elif kind == "node_finished":
                finished[event["node_id"]] = event
            elif kind == "result":
                status = event["status"]
                break
            elif kind == "error":
                raise AssertionError(f"runtime error: {event.get('error')}")

        assert saw_callback, "runtime did not emit call_workflow"
        assert status == "success"
        # Parent's execute_workflow node got the inline sub's leaf back.
        assert finished["call"]["outputs"]["main"] == 28
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()
