from __future__ import annotations

import asyncio

import pytest

import nodyra_nodes  # noqa: F401 - registers loop, metanode, and code nodes
from nodyra.engine import execute
from nodyra.models import WorkflowGraph
from nodyra.sdk import NodeRegistry, node, registry


def _scheduler_tasks() -> list[asyncio.Task]:
    current = asyncio.current_task()
    return [
        task
        for task in asyncio.all_tasks()
        if task is not current
        and not task.done()
        and task.get_name().startswith("nodyra-scheduler-")
    ]


async def _assert_fails_cleanly(awaitable, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        await asyncio.wait_for(awaitable, timeout=2.0)
    await asyncio.sleep(0)
    assert _scheduler_tasks() == []


async def test_event_callback_failure_cancels_scheduler_without_join_deadlock() -> None:
    local_registry = NodeRegistry()

    @node(name="Value", id="supervision_value", inputs=[], registry=local_registry)
    def value() -> int:
        return 1

    graph = WorkflowGraph.model_validate(
        {
            "nodes": [
                {"id": f"node-{index}", "type": "supervision_value", "params": {}}
                for index in range(100)
            ],
            "edges": [],
        }
    )

    async def broken_callback(_event: dict) -> None:
        raise RuntimeError("callback fault")

    await _assert_fails_cleanly(
        execute(graph, local_registry, on_event=broken_callback), "callback fault"
    )


async def test_metanode_adapter_failure_cancels_scheduler_group(monkeypatch) -> None:
    import nodyra.engine.metanodes as metanodes

    async def broken_metanode(**_kwargs):
        raise RuntimeError("metanode fault")

    monkeypatch.setattr(metanodes, "_run_metanode", broken_metanode)
    graph = WorkflowGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "meta",
                    "type": "meta_node",
                    "params": {
                        "execution": "isolated",
                        "subgraph": {
                            "nodes": [
                                {
                                    "id": "child",
                                    "type": "code",
                                    "params": {"code": "output = 1"},
                                }
                            ],
                            "edges": [],
                        },
                        "ports": {"inputs": [], "outputs": []},
                    },
                }
            ],
            "edges": [],
        }
    )

    await _assert_fails_cleanly(execute(graph, registry), "metanode fault")


async def test_loop_driver_failure_cancels_scheduler_group(monkeypatch) -> None:
    import nodyra.engine.loops as loops

    async def broken_loop(**_kwargs):
        raise RuntimeError("loop fault")

    monkeypatch.setattr(loops, "_run_loop", broken_loop)
    graph = WorkflowGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "start",
                    "type": "loop_start",
                    "params": {"mode": "range", "count": 1},
                },
                {
                    "id": "body",
                    "type": "code",
                    "params": {"code": "output = input"},
                },
                {
                    "id": "end",
                    "type": "loop_end",
                    "params": {"loop_start_id": "start"},
                },
            ],
            "edges": [
                {
                    "source": "start",
                    "source_output": "item",
                    "target": "body",
                    "target_input": "input",
                },
                {"source": "body", "target": "end"},
            ],
        }
    )

    await _assert_fails_cleanly(execute(graph, registry), "loop fault")
