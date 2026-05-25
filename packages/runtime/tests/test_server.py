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
