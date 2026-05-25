import asyncio

from httpx import AsyncClient

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
                "id": "boom",
                "type": "code",
                "params": {"code": "raise ValueError('nope')"},
                "position": {"x": 0, "y": 0},
            }
        ],
        "edges": [],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": bad_graph})

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] == "error"
    assert run["node_runs"][0]["status"] == "error"
    assert "nope" in run["node_runs"][0]["error"]


async def test_typed_outputs_persist_and_stream_as_envelopes(
    client: AsyncClient,
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Typed"})).json()[
        "id"
    ]
    graph = {
        "nodes": [
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
                "position": {"x": 0, "y": 0},
            }
        ],
        "edges": [],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    output = run["node_runs"][0]["output"]["main"]

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
        event for event in streamed if event.get("type") == "node_finished"
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
                "id": "ok",
                "type": "code",
                "params": {"code": "output = 1"},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "boom",
                "type": "code",
                "params": {"code": "raise RuntimeError('nope')"},
                "position": {"x": 1, "y": 0},
            },
            {
                "id": "tail",
                "type": "code",
                "params": {"code": "output = input"},
                "position": {"x": 2, "y": 0},
            },
        ],
        "edges": [
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
                "id": "producer",
                "type": "code",
                "params": {
                    "code": (
                        "from decimal import Decimal\n"
                        "output = {'amount': Decimal('3.50')}"
                    )
                },
                "position": {"x": 0, "y": 0},
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
                "position": {"x": 200, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "producer",
                "source_output": "main",
                "target": "consumer",
                "target_input": "input",
            }
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
                    "id": "slow",
                    "type": "code",
                    "params": {"code": "import time\ntime.sleep(0.5)\noutput = 1"},
                    "position": {"x": 0, "y": 0},
                }
            ],
            "edges": [],
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
