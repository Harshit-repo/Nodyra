import asyncio

from httpx import AsyncClient
from sqlalchemy import select

from app.config import settings
from app.services.events import broker

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
    variables = {
        variable["name"]: variable for variable in results["c"]["debug"]["variables"]
    }
    assert variables["output"]["preview"] == 6


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

    runs = (await client.get(f"/workflows/{workflow_id}/runs")).json()
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

    all_runs = (await client.get("/runs")).json()
    assert len(all_runs) == 3
    assert {r["workflow_id"] for r in all_runs} == {wf_a, wf_b}
    # Joined workflow_name is populated.
    assert all(r["workflow_name"] for r in all_runs)

    only_a = (await client.get(f"/runs?workflow_id={wf_a}")).json()
    assert len(only_a) == 2
    assert all(r["workflow_id"] == wf_a for r in only_a)

    paged = (await client.get("/runs?limit=1")).json()
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
    from app.models import NodeRun, Run, RunQueueEntry

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


