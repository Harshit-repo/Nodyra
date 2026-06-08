import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.config import settings
from app.services.events import broker
from noodle.models import RunResult, RunStatus

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
            "position": {"x": 250, "y": 0},
        },
    ],
    "edges": [
        {
            "id": "e1",
            "source": "t",
            "source_output": "main",
            "target": "c",
            "target_input": "input",
        }
    ],
}


async def _workflow_with_graph(client: AsyncClient) -> str:
    workflow_id = (await client.post("/workflows", json={"name": "Run"})).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": GRAPH})
    return workflow_id


async def test_run_executes_the_graph(client: AsyncClient) -> None:
    workflow_id = await _workflow_with_graph(client)

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]

    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] == "success"

    results = {n["node_id"]: n for n in run["node_runs"]}
    assert results["c"]["output"]["main"] == 6
    assert results["t"]["status"] == "success"


LOOP_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual_trigger", "params": {"data": [1, 2, 3]},
         "position": {"x": 0, "y": 0}},
        {"id": "s", "type": "loop_start", "params": {},
         "position": {"x": 1, "y": 0}},
        {"id": "b", "type": "code", "params": {"code": "output = input * 2"},
         "position": {"x": 2, "y": 0}},
        {"id": "e", "type": "loop_end", "params": {"loop_start_id": "s"},
         "position": {"x": 3, "y": 0}},
    ],
    "edges": [
        {"id": "t->s", "source": "t", "source_output": "main",
         "target": "s", "target_input": "input"},
        {"id": "s->b", "source": "s", "source_output": "item",
         "target": "b", "target_input": "input"},
        {"id": "b->e", "source": "b", "source_output": "main",
         "target": "e", "target_input": "input"},
    ],
}


async def test_loop_persists_one_node_run_per_iteration(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Loop"})).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": LOOP_GRAPH})

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] == "success"

    body_runs = [nr for nr in run["node_runs"] if nr["node_id"] == "b"]
    assert sorted(nr["iteration_path"] for nr in body_runs) == [[0], [1], [2]]

    end_runs = [nr for nr in run["node_runs"] if nr["node_id"] == "e"]
    assert len(end_runs) == 1
    assert end_runs[0]["iteration_path"] is None
    assert end_runs[0]["output"]["results"] == [2, 4, 6]

    # Non-loop node keeps exactly one run with a null iteration_path.
    trig_runs = [nr for nr in run["node_runs"] if nr["node_id"] == "t"]
    assert len(trig_runs) == 1
    assert trig_runs[0]["iteration_path"] is None


async def test_run_records_node_errors(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Bad"})).json()["id"]
    bad_graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "boom",
                "type": "code",
                "params": {"code": "raise ValueError('nope')"},
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e",
                "source": "t",
                "source_output": "main",
                "target": "boom",
                "target_input": "input",
            }
        ],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": bad_graph})

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] == "error"
    boom = next(nr for nr in run["node_runs"] if nr["node_id"] == "boom")
    assert boom["status"] == "error"
    assert "nope" in boom["error"]


async def test_typed_outputs_persist_and_stream_as_envelopes(
    client: AsyncClient,
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Typed"})).json()[
        "id"
    ]
    graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "typed",
                "type": "code",
                "params": {
                    "code": "\n".join(
                        [
                            "from datetime import datetime",
                            "from decimal import Decimal",
                            "output = {",
                            "    'price': Decimal('19.99'),",
                            "    'created': datetime(2026, 5, 25, 1, 2, 3),",
                            "    'coords': (1, 2),",
                            "    'tags': {'vip', 'beta'},",
                            "    'raw': b'hello',",
                            "}",
                        ]
                    )
                },
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e",
                "source": "t",
                "source_output": "main",
                "target": "typed",
                "target_input": "input",
            }
        ],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    typed_run = next(nr for nr in run["node_runs"] if nr["node_id"] == "typed")
    output = typed_run["output"]["main"]

    assert output["price"]["__noodle_typed__"] is True
    assert output["price"]["type"] == "decimal"
    assert output["price"]["value"] == "19.99"
    assert output["created"]["type"] == "datetime"
    assert output["coords"]["type"] == "tuple"
    assert output["tags"]["type"] == "set"
    assert output["raw"]["type"] == "bytes"

    streamed: list[dict] = []
    async for event in broker.subscribe(run_id):
        streamed.append(event)
    node_event = next(
        event
        for event in streamed
        if event.get("type") == "node_finished" and event.get("node_id") == "typed"
    )
    assert node_event["outputs"]["main"]["price"]["type"] == "decimal"


async def test_run_accepts_editor_cache_for_webhook_payload(
    client: AsyncClient,
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Webhook"})).json()[
        "id"
    ]
    graph = {
        "nodes": [
            {
                "id": "hook",
                "type": "webhook_trigger",
                "params": {"path": "orders"},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "code",
                "type": "code",
                "params": {"code": "output = input['body']['order']"},
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "hook",
                "source_output": "main",
                "target": "code",
                "target_input": "input",
            }
        ],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    run_id = (
        await client.post(
            f"/workflows/{workflow_id}/run",
            json={"cache": {"hook": {"main": {"body": {"order": 42}}}}},
        )
    ).json()["run_id"]

    run = (await client.get(f"/runs/{run_id}")).json()
    results = {n["node_id"]: n for n in run["node_runs"]}
    assert run["status"] == "success"
    assert results["hook"]["output"]["main"]["body"]["order"] == 42
    assert results["code"]["output"]["main"] == 42


async def test_runs_are_listed_for_a_workflow(client: AsyncClient) -> None:
    workflow_id = await _workflow_with_graph(client)
    await client.post(f"/workflows/{workflow_id}/run", json={})
    await client.post(f"/workflows/{workflow_id}/run", json={})

    runs = (await client.get(f"/workflows/{workflow_id}/runs")).json()["items"]
    assert len(runs) == 2
    assert all(r["status"] == "success" for r in runs)


async def test_rerun_replays_the_trigger_parameters(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Replay"})).json()["id"]
    graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "e",
                "type": "code",
                "params": {"code": "output = input"},
                "position": {"x": 1, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "t",
                "source_output": "main",
                "target": "e",
                "target_input": "input",
            }
        ],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    original = (
        await client.post(
            f"/workflows/{workflow_id}/run",
            json={"parameters": {"x": 7}},
        )
    ).json()["run_id"]
    assert (await client.get(f"/runs/{original}")).json()["status"] == "success"

    re_id = (await client.post(f"/runs/{original}/rerun")).json()["run_id"]
    re_run = (await client.get(f"/runs/{re_id}")).json()
    results = {n["node_id"]: n for n in re_run["node_runs"]}
    assert results["e"]["output"]["main"] == {"x": 7}


async def test_retry_runs_only_the_failed_node_and_downstream(
    client: AsyncClient,
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Boom"})).json()["id"]
    graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "ok",
                "type": "code",
                "params": {"code": "output = 1"},
                "position": {"x": 1, "y": 0},
            },
            {
                "id": "boom",
                "type": "code",
                "params": {"code": "raise RuntimeError('nope')"},
                "position": {"x": 2, "y": 0},
            },
            {
                "id": "tail",
                "type": "code",
                "params": {"code": "output = input"},
                "position": {"x": 3, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e0",
                "source": "t",
                "source_output": "main",
                "target": "ok",
                "target_input": "input",
            },
            {
                "id": "e1",
                "source": "ok",
                "source_output": "main",
                "target": "boom",
                "target_input": "input",
            },
            {
                "id": "e2",
                "source": "boom",
                "source_output": "main",
                "target": "tail",
                "target_input": "input",
            },
        ],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    original = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    first = (await client.get(f"/runs/{original}")).json()
    statuses = {n["node_id"]: n["status"] for n in first["node_runs"]}
    assert statuses["ok"] == "success"
    assert statuses["boom"] == "error"

    # Fix the boom node so retry has a chance to succeed.
    fixed = {**graph}
    fixed["nodes"] = [
        n if n["id"] != "boom"
        else {**n, "params": {"code": "output = input * 10"}}
        for n in graph["nodes"]
    ]
    await client.put(f"/workflows/{workflow_id}", json={"graph": fixed})

    retry_id = (await client.post(f"/runs/{original}/retry")).json()["run_id"]
    retry = (await client.get(f"/runs/{retry_id}")).json()
    statuses = {n["node_id"]: n["status"] for n in retry["node_runs"]}
    # 'ok' was supplied via cache; 'boom' and 'tail' re-ran.
    assert statuses["ok"] == "success"
    assert statuses["boom"] == "success"
    assert statuses["tail"] == "success"
    results = {n["node_id"]: n for n in retry["node_runs"]}
    assert results["boom"]["output"]["main"] == 10
    assert results["tail"]["output"]["main"] == 10


async def test_retry_deserializes_typed_cached_outputs(
    client: AsyncClient,
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Typed Retry"})).json()[
        "id"
    ]
    graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "producer",
                "type": "code",
                "params": {
                    "code": (
                        "from decimal import Decimal\n"
                        "output = {'amount': Decimal('3.50')}"
                    )
                },
                "position": {"x": 200, "y": 0},
            },
            {
                "id": "consumer",
                "type": "code",
                "params": {
                    "code": "\n".join(
                        [
                            "if type(input['amount']).__name__ != 'Decimal':",
                            "    raise RuntimeError('not decimal')",
                            "raise RuntimeError('boom')",
                        ]
                    )
                },
                "position": {"x": 400, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e0",
                "source": "t",
                "source_output": "main",
                "target": "producer",
                "target_input": "input",
            },
            {
                "id": "e1",
                "source": "producer",
                "source_output": "main",
                "target": "consumer",
                "target_input": "input",
            },
        ],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    original = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    first = (await client.get(f"/runs/{original}")).json()
    results = {n["node_id"]: n for n in first["node_runs"]}
    assert results["producer"]["output"]["main"]["amount"]["type"] == "decimal"
    assert results["consumer"]["status"] == "error"

    fixed = {**graph}
    fixed["nodes"] = [
        n
        if n["id"] != "consumer"
        else {**n, "params": {"code": "output = type(input['amount']).__name__"}}
        for n in graph["nodes"]
    ]
    await client.put(f"/workflows/{workflow_id}", json={"graph": fixed})

    retry_id = (await client.post(f"/runs/{original}/retry")).json()["run_id"]
    retry = (await client.get(f"/runs/{retry_id}")).json()
    results = {n["node_id"]: n for n in retry["node_runs"]}
    assert results["consumer"]["output"]["main"] == "Decimal"


async def test_retry_rejects_runs_with_no_failures(client: AsyncClient) -> None:
    workflow_id = await _workflow_with_graph(client)
    original = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    assert (await client.get(f"/runs/{original}")).json()["status"] == "success"
    resp = await client.post(f"/runs/{original}/retry")
    assert resp.status_code == 400


async def test_all_runs_endpoint_lists_across_workflows(client: AsyncClient) -> None:
    wf_a = await _workflow_with_graph(client)
    wf_b = (await client.post("/workflows", json={"name": "Other"})).json()["id"]
    await client.put(f"/workflows/{wf_b}", json={"graph": GRAPH})
    await client.post(f"/workflows/{wf_a}/run", json={})
    await client.post(f"/workflows/{wf_b}/run", json={})
    await client.post(f"/workflows/{wf_a}/run", json={})

    all_runs = (await client.get("/runs")).json()["items"]
    assert len(all_runs) == 3
    assert {r["workflow_id"] for r in all_runs} == {wf_a, wf_b}
    # Joined workflow_name is populated.
    assert all(r["workflow_name"] for r in all_runs)

    only_a = (await client.get(f"/runs?workflow_id={wf_a}")).json()["items"]
    assert len(only_a) == 2
    assert all(r["workflow_id"] == wf_a for r in only_a)

    paged = (await client.get("/runs?limit=1")).json()["items"]
    assert len(paged) == 1


async def test_targeted_run_executes_a_subset(client: AsyncClient) -> None:
    workflow_id = await _workflow_with_graph(client)

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={"targets": ["t"]})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()

    node_ids = {n["node_id"] for n in run["node_runs"]}
    assert node_ids == {"t"}


async def test_running_run_can_be_cancelled(client: AsyncClient) -> None:
    previous = settings.run_synchronously
    settings.run_synchronously = False
    try:
        workflow_id = (await client.post("/workflows", json={"name": "Slow"})).json()[
            "id"
        ]
        slow_graph = {
            "nodes": [
                {
                    "id": "t",
                    "type": "manual_trigger",
                    "params": {},
                    "position": {"x": 0, "y": 0},
                },
                {
                    "id": "slow",
                    "type": "code",
                    "params": {"code": "import time\ntime.sleep(0.5)\noutput = 1"},
                    "position": {"x": 250, "y": 0},
                },
            ],
            "edges": [
                {
                    "id": "e",
                    "source": "t",
                    "source_output": "main",
                    "target": "slow",
                    "target_input": "input",
                }
            ],
        }
        await client.put(f"/workflows/{workflow_id}", json={"graph": slow_graph})

        run_id = (
            await client.post(f"/workflows/{workflow_id}/run", json={})
        ).json()["run_id"]
        cancel = await client.post(f"/runs/{run_id}/cancel")
        assert cancel.status_code == 200
        assert cancel.json()["status"] in {"cancelling", "cancelled"}

        for _ in range(20):
            run = (await client.get(f"/runs/{run_id}")).json()
            if run["status"] == "cancelled":
                break
            await asyncio.sleep(0.05)

        assert run["status"] == "cancelled"
    finally:
        settings.run_synchronously = previous


async def test_run_timeline_returns_ordered_events(client: AsyncClient) -> None:
    workflow_id = await _workflow_with_graph(client)
    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]

    resp = await client.get(f"/runs/{run_id}/timeline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["status"] == "success"

    types = [e["type"] for e in body["events"]]
    # start_run now enqueues a durable queue entry; "enqueued" precedes "started".
    assert "enqueued" in types
    assert "started" in types
    assert "completed" in types
    assert types.index("enqueued") <= types.index("started")
    # Each node produced a started/finished pair.
    assert types.count("node_started") == 2
    assert types.count("node_finished") == 2
    # started precedes completed.
    assert types.index("started") < types.index("completed")
    # Every node_started precedes its matching node_finished by node_id.
    finished = {
        e["data"]["node_id"]: i
        for i, e in enumerate(body["events"])
        if e["type"] == "node_finished"
    }
    started = {
        e["data"]["node_id"]: i
        for i, e in enumerate(body["events"])
        if e["type"] == "node_started"
    }
    for node_id, idx in finished.items():
        assert started[node_id] < idx


async def test_run_timeline_includes_persisted_agent_events(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.runner as runner_module

    workflow_id = await _workflow_with_graph(client)

    async def fake_execute(graph, registry, **kwargs) -> RunResult:  # noqa: ANN001, ARG001
        on_event = kwargs["on_event"]
        await on_event({"type": "node_started", "node_id": "agent"})
        await on_event(
            {
                "type": "agent_action_requested",
                "agent_node_id": "agent",
                "step": 0,
                "max_steps": 3,
                "tool_calls": [
                    {"id": "call_1", "name": "lookup", "arguments": {"q": "Ada"}}
                ],
            }
        )
        await on_event(
            {
                "type": "agent_tool_started",
                "agent_node_id": "agent",
                "step": 0,
                "max_steps": 3,
                "tool_call_id": "call_1",
                "tool_name": "lookup",
                "arguments": {"q": "Ada"},
            }
        )
        await on_event(
            {
                "type": "agent_tool_approval_required",
                "agent_node_id": "agent",
                "step": 0,
                "max_steps": 3,
                "tool_call_id": "call_1",
                "tool_name": "lookup",
                "status": "blocked",
                "message": "Tool 'lookup' requires approval before running.",
                "arguments": {"q": "Ada"},
            }
        )
        await on_event(
            {
                "type": "agent_tool_finished",
                "agent_node_id": "agent",
                "step": 0,
                "max_steps": 3,
                "tool_call_id": "call_1",
                "tool_name": "lookup",
                "status": "success",
                "tool_result": {
                    "tool_call_id": "call_1",
                    "name": "lookup",
                    "content": "Ada Lovelace",
                    "is_error": False,
                },
            }
        )
        await on_event(
            {
                "type": "agent_action_completed",
                "agent_node_id": "agent",
                "step": 1,
                "max_steps": 3,
                "status": "success",
            }
        )
        await on_event(
            {
                "type": "node_finished",
                "node_id": "agent",
                "status": "success",
                "outputs": {"main": {"answer": "done"}},
                "started_at": 1.0,
                "finished_at": 2.0,
            }
        )
        return RunResult(status=RunStatus.success)

    monkeypatch.setattr(runner_module, "execute", fake_execute)

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    body = (await client.get(f"/runs/{run_id}/timeline")).json()
    types = [event["type"] for event in body["events"]]

    assert "agent_action_requested" in types
    assert "agent_tool_started" in types
    assert "agent_tool_approval_required" in types
    assert "agent_tool_finished" in types
    assert "agent_action_completed" in types
    finished = next(e for e in body["events"] if e["type"] == "agent_tool_finished")
    assert finished["data"]["agent_node_id"] == "agent"
    assert finished["data"]["tool_result"]["content"] == "Ada Lovelace"


async def test_run_timeline_includes_persisted_guardrail_events(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.runner as runner_module

    workflow_id = await _workflow_with_graph(client)

    async def fake_execute(graph, registry, **kwargs) -> RunResult:  # noqa: ANN001, ARG001
        on_event = kwargs["on_event"]
        await on_event({"type": "node_started", "node_id": "agent"})
        await on_event(
            {
                "type": "node_finished",
                "node_id": "agent",
                "status": "success",
                "outputs": {"main": {"answer": "done"}},
                "debug": {
                    "guardrail_events": [
                        {
                            "type": "guardrail_redacted",
                            "adapter": "keyword_guardrail",
                            "replacement_count": 2,
                        }
                    ]
                },
                "started_at": 1.0,
                "finished_at": 2.0,
            }
        )
        return RunResult(status=RunStatus.success)

    monkeypatch.setattr(runner_module, "execute", fake_execute)

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    body = (await client.get(f"/runs/{run_id}/timeline")).json()
    event = next(e for e in body["events"] if e["type"] == "guardrail_redacted")
    assert event["data"]["node_id"] == "agent"
    assert event["data"]["adapter"] == "keyword_guardrail"
    assert event["data"]["replacement_count"] == 2


async def test_run_approvals_are_recorded_and_decidable(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.runner as runner_module

    workflow_id = await _workflow_with_graph(client)

    async def fake_execute(graph, registry, **kwargs) -> RunResult:  # noqa: ANN001, ARG001
        on_event = kwargs["on_event"]
        await on_event(
            {
                "type": "agent_tool_approval_required",
                "agent_node_id": "agent",
                "step": 0,
                "max_steps": 3,
                "tool_call_id": "call_pending",
                "tool_name": "send_email",
                "status": "blocked",
                "message": "Tool 'send_email' requires approval before running.",
                "arguments": {"to": "ada@example.com"},
            }
        )
        await on_event(
            {
                "type": "agent_tool_auto_approved",
                "agent_node_id": "agent",
                "step": 0,
                "max_steps": 3,
                "tool_call_id": "call_auto",
                "tool_name": "update_sheet",
                "status": "approved",
                "message": "Side-effecting tool auto-approved by agent setting.",
                "arguments": {"row": 3},
            }
        )
        return RunResult(status=RunStatus.success)

    monkeypatch.setattr(runner_module, "execute", fake_execute)

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    approvals = (await client.get(f"/runs/{run_id}/approvals")).json()

    by_tool = {approval["tool_name"]: approval for approval in approvals}
    assert by_tool["send_email"]["status"] == "pending"
    assert by_tool["send_email"]["arguments"] == {"to": "ada@example.com"}
    assert by_tool["update_sheet"]["status"] == "approved"
    assert by_tool["update_sheet"]["resolved_by"] == "auto"

    pending_id = by_tool["send_email"]["id"]
    decision = (
        await client.post(
            f"/runs/{run_id}/approvals/{pending_id}/decision",
            json={"decision": "approve", "reason": "Looks correct"},
        )
    ).json()
    assert decision["status"] == "approved"
    assert decision["reason"] == "Looks correct"

    conflict = await client.post(
        f"/runs/{run_id}/approvals/{pending_id}/decision",
        json={"decision": "reject"},
    )
    assert conflict.status_code == 409

    timeline = (await client.get(f"/runs/{run_id}/timeline")).json()
    assert "agent_tool_auto_approved" in [
        event["type"] for event in timeline["events"]
    ]
    decided = [
        event
        for event in timeline["events"]
        if event["type"] == "agent_tool_approval_decided"
    ]
    assert decided[0]["data"]["approval_id"] == pending_id
    assert decided[0]["data"]["status"] == "approved"


async def test_approval_decision_requeues_waiting_run(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.runner as runner_module
    from noodle.ai_runtime import AgentActionRequest, AIMessage, ToolCall

    workflow_id = await _workflow_with_graph(client)
    approval_key = "agent|0|call_pending|send_email"
    request = AgentActionRequest(
        tool_calls=[
            ToolCall(
                id="call_pending",
                name="send_email",
                arguments={"to": "ada@example.com"},
            )
        ],
        messages_so_far=[AIMessage.user("Send the update")],
        step=0,
        max_steps=3,
    )
    calls: list[object] = []

    async def fake_execute(graph, registry, **kwargs) -> RunResult:  # noqa: ANN001, ARG001
        calls.append(kwargs.get("agent_action_resume"))
        on_event = kwargs["on_event"]
        if kwargs.get("agent_action_resume"):
            await on_event(
                {
                    "type": "node_finished",
                    "node_id": "agent",
                    "status": "success",
                    "outputs": {"main": {"answer": "sent"}},
                    "started_at": 3.0,
                    "finished_at": 4.0,
                }
            )
            return RunResult(status=RunStatus.success)

        await on_event(
            {
                "type": "node_finished",
                "node_id": "supplier",
                "status": "success",
                "outputs": {"main": object()},
                "started_at": 0.1,
                "finished_at": 0.2,
            }
        )
        await on_event(
            {
                "type": "agent_tool_approval_required",
                "agent_node_id": "agent",
                "step": 0,
                "max_steps": 3,
                "tool_call_id": "call_pending",
                "tool_name": "send_email",
                "approval_key": approval_key,
                "status": "blocked",
                "message": "Tool 'send_email' requires approval before running.",
                "arguments": {"to": "ada@example.com"},
            }
        )
        await on_event(
            {
                "type": "node_finished",
                "node_id": "agent",
                "status": "waiting",
                "outputs": {},
                "error": "Tool 'send_email' requires approval before running.",
                "debug": {
                    "agent_approval_state": {
                        "agent_node_id": "agent",
                        "approval_key": approval_key,
                        "tool_call_id": "call_pending",
                        "tool_name": "send_email",
                        "request": request.model_dump(mode="json"),
                    }
                },
                "started_at": 1.0,
                "finished_at": 2.0,
            }
        )
        return RunResult(status=RunStatus.waiting)

    monkeypatch.setattr(runner_module, "execute", fake_execute)

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    waiting_run = (await client.get(f"/runs/{run_id}")).json()
    assert waiting_run["status"] == "waiting"

    approval = (await client.get(f"/runs/{run_id}/approvals")).json()[0]
    assert approval["status"] == "pending"

    response = await client.post(
        f"/runs/{run_id}/approvals/{approval['id']}/decision",
        json={"decision": "approve"},
    )
    assert response.status_code == 200, response.text

    resumed_run = (await client.get(f"/runs/{run_id}")).json()
    assert resumed_run["status"] == "success"
    assert any(call is not None for call in calls)

    timeline = (await client.get(f"/runs/{run_id}/timeline")).json()
    prepared = [
        event for event in timeline["events"] if event["type"] == "agent_resume_prepared"
    ]
    assert prepared
    assert prepared[0]["data"]["cached_node_ids"] == []
    assert prepared[0]["data"]["skipped_cache_nodes"][0]["node_id"] == "supplier"
    assert prepared[0]["data"]["skipped_cache_nodes"][0]["reason"] == "unrestorable_output"


async def test_approval_reject_resumes_waiting_run(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejecting an approval also resumes the run (denied tool flows back to the
    agent) instead of leaving it stuck in ``waiting``."""
    import app.services.runner as runner_module
    from noodle.ai_runtime import AgentActionRequest, AIMessage, ToolCall

    workflow_id = await _workflow_with_graph(client)
    approval_key = "agent|0|call_pending|send_email"
    request = AgentActionRequest(
        tool_calls=[
            ToolCall(
                id="call_pending",
                name="send_email",
                arguments={"to": "ada@example.com"},
            )
        ],
        messages_so_far=[AIMessage.user("Send the update")],
        step=0,
        max_steps=3,
    )
    resume_payloads: list[object] = []

    async def fake_execute(graph, registry, **kwargs) -> RunResult:  # noqa: ANN001, ARG001
        resume = kwargs.get("agent_action_resume")
        resume_payloads.append(resume)
        on_event = kwargs["on_event"]
        if resume:
            await on_event(
                {
                    "type": "node_finished",
                    "node_id": "agent",
                    "status": "success",
                    "outputs": {"main": {"answer": "understood, skipping"}},
                    "started_at": 3.0,
                    "finished_at": 4.0,
                }
            )
            return RunResult(status=RunStatus.success)

        await on_event(
            {
                "type": "agent_tool_approval_required",
                "agent_node_id": "agent",
                "step": 0,
                "max_steps": 3,
                "tool_call_id": "call_pending",
                "tool_name": "send_email",
                "approval_key": approval_key,
                "status": "blocked",
                "message": "Tool 'send_email' requires approval before running.",
                "arguments": {"to": "ada@example.com"},
            }
        )
        await on_event(
            {
                "type": "node_finished",
                "node_id": "agent",
                "status": "waiting",
                "outputs": {},
                "error": "Tool 'send_email' requires approval before running.",
                "debug": {
                    "agent_approval_state": {
                        "agent_node_id": "agent",
                        "approval_key": approval_key,
                        "tool_call_id": "call_pending",
                        "tool_name": "send_email",
                        "request": request.model_dump(mode="json"),
                    }
                },
                "started_at": 1.0,
                "finished_at": 2.0,
            }
        )
        return RunResult(status=RunStatus.waiting)

    monkeypatch.setattr(runner_module, "execute", fake_execute)

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    waiting_run = (await client.get(f"/runs/{run_id}")).json()
    assert waiting_run["status"] == "waiting"

    approval = (await client.get(f"/runs/{run_id}/approvals")).json()[0]
    assert approval["status"] == "pending"

    response = await client.post(
        f"/runs/{run_id}/approvals/{approval['id']}/decision",
        json={"decision": "reject", "reason": "Not allowed"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "rejected"

    resumed_run = (await client.get(f"/runs/{run_id}")).json()
    assert resumed_run["status"] == "success"

    # The resume must carry the denied tool call so the engine returns a
    # denial result to the agent rather than re-prompting for approval.
    resume = next(payload for payload in resume_payloads if payload)
    assert resume["agent"].rejected_tool_call_ids == ["call_pending"]


async def test_waiting_run_emits_non_terminal_run_waiting(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run paused for approval must publish a non-terminal ``run_waiting``
    signal — NOT ``run_finished``. ``run_finished`` is the broker's only stream
    terminator, so emitting it here would close the client's run WebSocket and
    strand the run as "waiting" with no way to resume on the same socket."""
    import app.services.runner as runner_module
    from noodle.ai_runtime import AgentActionRequest, AIMessage, ToolCall

    workflow_id = await _workflow_with_graph(client)
    approval_key = "agent|0|call_pending|send_email"
    request = AgentActionRequest(
        tool_calls=[
            ToolCall(
                id="call_pending",
                name="send_email",
                arguments={"to": "ada@example.com"},
            )
        ],
        messages_so_far=[AIMessage.user("Send the update")],
        step=0,
        max_steps=3,
    )

    async def fake_execute(graph, registry, **kwargs) -> RunResult:  # noqa: ANN001, ARG001
        on_event = kwargs["on_event"]
        await on_event(
            {
                "type": "agent_tool_approval_required",
                "agent_node_id": "agent",
                "step": 0,
                "max_steps": 3,
                "tool_call_id": "call_pending",
                "tool_name": "send_email",
                "approval_key": approval_key,
                "status": "blocked",
                "message": "Tool 'send_email' requires approval before running.",
                "arguments": {"to": "ada@example.com"},
            }
        )
        await on_event(
            {
                "type": "node_finished",
                "node_id": "agent",
                "status": "waiting",
                "outputs": {},
                "error": "Tool 'send_email' requires approval before running.",
                "debug": {
                    "agent_approval_state": {
                        "agent_node_id": "agent",
                        "approval_key": approval_key,
                        "tool_call_id": "call_pending",
                        "tool_name": "send_email",
                        "request": request.model_dump(mode="json"),
                    }
                },
                "started_at": 1.0,
                "finished_at": 2.0,
            }
        )
        return RunResult(status=RunStatus.waiting)

    monkeypatch.setattr(runner_module, "execute", fake_execute)

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    assert (await client.get(f"/runs/{run_id}")).json()["status"] == "waiting"

    # Drain the buffered broker events without blocking. A waiting run never
    # publishes ``run_finished``, so ``subscribe`` would otherwise block after
    # replaying history — iterate with a short per-event timeout and stop when
    # the stream goes quiet.
    streamed: list[dict] = []
    gen = broker.subscribe(run_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(gen.__anext__(), timeout=0.5)
            except (asyncio.TimeoutError, StopAsyncIteration):
                break
            streamed.append(event)
            if event.get("type") == "run_finished":
                break
    finally:
        await gen.aclose()

    types = [event.get("type") for event in streamed]
    assert "run_waiting" in types
    assert "run_finished" not in types


async def test_run_timeline_includes_queue_enqueue_event(client: AsyncClient) -> None:
    workflow_id = await _workflow_with_graph(client)
    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]

    body = (await client.get(f"/runs/{run_id}/timeline")).json()
    enqueued = next(e for e in body["events"] if e["type"] == "enqueued")
    # start_run is the canonical enqueue reason for the durable queue.
    assert enqueued["data"]["reason"] == "start_run"
    assert enqueued["data"]["priority"] == 0


async def test_run_timeline_missing_run_returns_404(client: AsyncClient) -> None:
    resp = await client.get("/runs/does-not-exist/timeline")
    assert resp.status_code == 404


async def test_replay_run_requeues_dead_lettered_entry(client: AsyncClient) -> None:
    """A dead-lettered run can be replayed: queue entry → queued, Run → queued."""
    from app.db import get_session
    from app.main import app as fastapi_app
    from app.models import Run, RunQueueEntry

    workflow_id = await _workflow_with_graph(client)
    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]

    override = fastapi_app.dependency_overrides[get_session]
    async for session in override():
        entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
        )
        entry.status = "dead_lettered"
        entry.attempts = entry.max_attempts
        entry.last_error = "exhausted"
        run = await session.get(Run, run_id)
        run.status = "error"
        await session.commit()
        break

    resp = await client.post(f"/runs/{run_id}/replay")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["previous_status"] == "dead_lettered"
    assert body["status"] == "queued"

    async for session in override():
        entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
        )
        assert entry.status == "queued"
        assert entry.attempts == 0
        assert entry.last_error is None
        assert any(rec["event"] == "replay" for rec in entry.attempts_log)
        run = await session.get(Run, run_id)
        assert run.status == "queued"
        break


async def test_replay_rejects_active_run(client: AsyncClient) -> None:
    from app.db import get_session
    from app.main import app as fastapi_app
    from app.models import RunQueueEntry

    workflow_id = await _workflow_with_graph(client)
    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]

    override = fastapi_app.dependency_overrides[get_session]
    async for session in override():
        entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
        )
        entry.status = "running"
        await session.commit()
        break

    resp = await client.post(f"/runs/{run_id}/replay")
    assert resp.status_code == 409


async def test_replay_missing_run_returns_404(client: AsyncClient) -> None:
    resp = await client.post("/runs/does-not-exist/replay")
    assert resp.status_code == 404


async def test_replay_from_node_seeds_cache_with_upstream_outputs(
    client: AsyncClient,
) -> None:
    """Replay-from-failure persists upstream success outputs as the engine cache.

    Failure point ``c`` is replayed; trigger ``t``'s prior output is supplied
    via ``RunQueueEntry.replay_seed.cache`` and ``targets`` is restricted to
    ``c`` and its descendants.
    """
    from app.db import get_session
    from app.main import app as fastapi_app
    from app.models import Run, RunQueueEntry

    workflow_id = await _workflow_with_graph(client)
    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]

    override = fastapi_app.dependency_overrides[get_session]
    async for session in override():
        # Force terminal state and pretend ``c`` failed.
        entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
        )
        entry.status = "failed"
        entry.attempts = entry.max_attempts
        run = await session.get(Run, run_id)
        run.status = "error"
        await session.commit()
        break

    resp = await client.post(
        f"/runs/{run_id}/replay", json={"from_node_id": "c"}
    )
    assert resp.status_code == 200, resp.text

    async for session in override():
        entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
        )
        assert entry.status == "queued"
        assert entry.replay_seed is not None
        # Upstream trigger ``t`` is cached; failed node ``c`` is in targets.
        assert "t" in (entry.replay_seed.get("cache") or {})
        assert "c" not in (entry.replay_seed.get("cache") or {})
        assert entry.replay_seed.get("targets") == ["c"]
        # Prior NodeRun for ``t`` had output {"main": {"n": 3}} — that flows in.
        assert entry.replay_seed["cache"]["t"] == {"main": {"n": 3}}
        break


async def test_replay_from_unknown_node_returns_400(client: AsyncClient) -> None:
    from app.db import get_session
    from app.main import app as fastapi_app
    from app.models import Run, RunQueueEntry

    workflow_id = await _workflow_with_graph(client)
    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]

    override = fastapi_app.dependency_overrides[get_session]
    async for session in override():
        entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
        )
        entry.status = "failed"
        run = await session.get(Run, run_id)
        run.status = "error"
        await session.commit()
        break

    resp = await client.post(
        f"/runs/{run_id}/replay", json={"from_node_id": "nonexistent"}
    )
    assert resp.status_code == 400


async def test_queued_entry_consumes_replay_seed(client: AsyncClient) -> None:
    """``_execute_queued_entry`` reads ``replay_seed`` and clears it after use."""
    from app.db import get_session
    from app.main import app as fastapi_app
    from app.models import Run, RunQueueEntry
    from app.services.runner import _execute_queued_entry

    workflow_id = await _workflow_with_graph(client)
    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]

    override = fastapi_app.dependency_overrides[get_session]
    async for session in override():
        entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
        )
        entry.status = "queued"
        entry.replay_seed = {
            "cache": {"t": {"main": {"n": 5}}},
            "targets": ["c"],
        }
        run = await session.get(Run, run_id)
        run.status = "queued"
        await session.commit()
        break

    await _execute_queued_entry(run_id)

    async for session in override():
        entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
        )
        assert entry.replay_seed is None  # consumed
        run = await session.get(Run, run_id)
        assert run.status == "success"
        break

    # The seeded n=5 (not the original n=3) flowed through ``c``.
    run = (await client.get(f"/runs/{run_id}")).json()
    by_node = {n["node_id"]: n for n in run["node_runs"]}
    assert by_node["c"]["output"]["main"] == 10


async def test_local_run_parks_in_queue_when_at_capacity(
    client: AsyncClient, monkeypatch
) -> None:
    """A local run with no immediate admission slot is parked as a durable
    ``queued`` entry (reason ``local_capacity``) instead of executing."""
    from app.db import get_session
    from app.main import app as fastapi_app
    from app.models import Run, RunQueueEntry
    from app.services.runtime_pool import pool as runtime_pool

    workflow_id = await _workflow_with_graph(client)

    # Async dispatch + local queue on, but pretend the pool is saturated so
    # neither start_run nor the dispatch loop will execute the run.
    monkeypatch.setattr(settings, "run_synchronously", False)
    monkeypatch.setattr(settings, "local_queue_enabled", True)
    monkeypatch.setattr(runtime_pool, "has_immediate_capacity", lambda: False)
    monkeypatch.setattr(runtime_pool, "available_global_slots", lambda: 0)

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]

    override = fastapi_app.dependency_overrides[get_session]
    async for session in override():
        run = await session.get(Run, run_id)
        assert run.status == "queued"
        entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
        )
        assert entry is not None
        assert entry.status == "queued"
        assert entry.queue_reason == "local_capacity"
        break


async def test_local_run_executes_when_capacity_available(
    client: AsyncClient, monkeypatch
) -> None:
    """With capacity, a local async run dispatches immediately (not parked)."""
    from app.db import get_session
    from app.main import app as fastapi_app
    from app.models import RunQueueEntry

    workflow_id = await _workflow_with_graph(client)

    monkeypatch.setattr(settings, "run_synchronously", False)
    monkeypatch.setattr(settings, "local_queue_enabled", True)
    # Default pool reports capacity, so the run should NOT be parked.

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]

    # Let the background execution task settle.
    for _ in range(50):
        run = (await client.get(f"/runs/{run_id}")).json()
        if run["status"] in ("success", "error"):
            break
        await asyncio.sleep(0.05)
    assert run["status"] == "success"

    override = fastapi_app.dependency_overrides[get_session]
    async for session in override():
        entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
        )
        # Was dispatched immediately, never parked with the local_capacity reason.
        assert entry.queue_reason != "local_capacity"
        break



# --- Task 20: Debug in editor ------------------------------------------------

async def test_debug_snapshot_returns_graph_failed_node_and_upstream_outputs(
    client: AsyncClient,
) -> None:
    """The debug snapshot bundles everything the editor needs to load a failed
    run: the exact graph that ran, the first failed node id, and the success
    outputs the editor can pin as upstream."""
    workflow_id = (await client.post("/workflows", json={"name": "Snap"})).json()["id"]
    graph = {
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {}, "position": {"x": 0, "y": 0}},
            {"id": "ok", "type": "code", "params": {"code": "output = 7"},
             "position": {"x": 1, "y": 0}},
            {"id": "boom", "type": "code", "params": {"code": "raise RuntimeError('x')"},
             "position": {"x": 2, "y": 0}},
        ],
        "edges": [
            {"id": "e0", "source": "t", "source_output": "main",
             "target": "ok", "target_input": "input"},
            {"id": "e1", "source": "ok", "source_output": "main",
             "target": "boom", "target_input": "input"},
        ],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    run_id = (await client.post(f"/workflows/{workflow_id}/run", json={})).json()["run_id"]
    snap = (await client.get(f"/runs/{run_id}/debug-snapshot")).json()

    assert snap["run_id"] == run_id
    assert snap["workflow_id"] == workflow_id
    assert snap["failed_node_id"] == "boom"
    assert "ok" in snap["upstream_cache"]
    # 'boom' failed so its output is not in the cache.
    assert "boom" not in snap["upstream_cache"]
    assert "boom" in snap["node_errors"]
    # Graph round-trips so the editor can render exactly what ran.
    assert {n["id"] for n in snap["graph"]["nodes"]} == {"t", "ok", "boom"}


async def test_debug_snapshot_404_for_unknown_run(client: AsyncClient) -> None:
    resp = await client.get("/runs/does-not-exist/debug-snapshot")
    assert resp.status_code == 404




async def test_run_blocked_when_node_package_missing(client: AsyncClient) -> None:
    env = (await client.post(
        "/environments", json={"name": "bare", "packages": []}
    )).json()
    wf = (await client.post("/workflows", json={"name": "needs-duckdb"})).json()
    graph = {
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {},
             "position": {"x": 0, "y": 0}},
            {"id": "q", "type": "duckdb_sql", "params": {},
             "position": {"x": 1, "y": 0}},
        ],
        "edges": [{"id": "e", "source": "t", "source_output": "main",
                   "target": "q", "target_input": "input"}],
    }
    await client.put(
        f"/workflows/{wf['id']}",
        json={"environment_id": env["id"], "graph": graph},
    )
    resp = await client.post(f"/workflows/{wf['id']}/run")
    assert resp.status_code == 400
    assert "duckdb" in resp.json()["detail"].lower()


# --- #14: list_runs must not eagerly load node_run details --------------------


async def test_list_runs_returns_empty_node_runs(client: AsyncClient) -> None:
    """GET /workflows/{id}/runs must skip loading per-node output/logs/debug.

    The list view only shows status and time; full node data comes from
    GET /runs/{id}. Loading heavy blobs for every run in the page is wasteful.
    """
    workflow_id = (await client.post("/workflows", json={"name": "ListRunsTest"})).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": GRAPH})
    run_resp = await client.post(f"/workflows/{workflow_id}/run", json={})
    assert run_resp.status_code in (200, 202), run_resp.text
    run_id = run_resp.json()["run_id"]

    list_resp = await client.get(f"/workflows/{workflow_id}/runs")
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert len(items) >= 1
    assert items[0]["node_runs"] == [], (
        "List endpoint must return empty node_runs — full data belongs to GET /runs/{id}"
    )

    # Single-run endpoint must still return the full per-node breakdown.
    run_detail = (await client.get(f"/runs/{run_id}")).json()
    assert len(run_detail["node_runs"]) > 0, "GET /runs/{id} must still return full node_runs"
