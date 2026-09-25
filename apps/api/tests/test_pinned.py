import asyncio
from decimal import Decimal

from httpx import AsyncClient

from nodyra.serialization import serialize_value

GRAPH = {
    "nodes": [
        {
            "id": "t",
            "type": "manual_trigger",
            "params": {"data": {"n": 0}},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "c",
            "type": "code",
            "params": {"code": "output = input['n'] + 1"},
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


async def test_pin_unpin_roundtrip(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Pinned"})).json()["id"]

    payload = {"main": {"n": 99}}
    pinned = (
        await client.put(
            f"/workflows/{workflow_id}/pinned/t",
            json={"payload": payload},
        )
    ).json()
    assert pinned["node_id"] == "t"
    assert pinned["payload"] == payload

    listed = (await client.get(f"/workflows/{workflow_id}/pinned")).json()
    assert len(listed) == 1
    assert listed[0]["payload"] == payload

    assert (
        await client.delete(f"/workflows/{workflow_id}/pinned/t")
    ).status_code == 204
    listed = (await client.get(f"/workflows/{workflow_id}/pinned")).json()
    assert listed == []


async def test_run_uses_pinned_output(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Pinned Run"})).json()[
        "id"
    ]
    await client.put(f"/workflows/{workflow_id}", json={"graph": GRAPH})

    # Pin trigger output so the code node sees a custom value.
    await client.put(
        f"/workflows/{workflow_id}/pinned/t",
        json={"payload": {"main": {"n": 99}}},
    )

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    results = {n["node_id"]: n for n in run["node_runs"]}
    assert results["c"]["output"]["main"] == 100


async def test_run_uses_pinned_output_on_immediate_dispatch(
    client: AsyncClient, monkeypatch
) -> None:
    """Pins must apply on the immediate-dispatch path, not only when the run
    parks on the durable queue (the queued worker reloads pins separately)."""
    from app.config import settings

    monkeypatch.setattr(settings, "run_synchronously", False)
    monkeypatch.setattr(settings, "dispatch_role", "enabled")
    monkeypatch.setattr(settings, "local_queue_enabled", False)

    workflow_id = (
        await client.post("/workflows", json={"name": "Immediate Pin"})
    ).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": GRAPH})
    await client.put(
        f"/workflows/{workflow_id}/pinned/t",
        json={"payload": {"main": {"n": 99}}},
    )

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    run = None
    for _ in range(100):
        await asyncio.sleep(0.05)
        run = (await client.get(f"/runs/{run_id}")).json()
        if run["status"] in ("success", "error"):
            break
    assert run is not None and run["status"] == "success"
    results = {n["node_id"]: n for n in run["node_runs"]}
    assert results["c"]["output"]["main"] == 100
    # Cache-hit (pinned) nodes must still carry finish timestamps so the
    # "last node output" reads don't misorder them (LB-2).
    assert results["t"]["finished_at"] is not None


async def test_run_deserializes_typed_pinned_output(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Typed Pin"})).json()[
        "id"
    ]
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
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    await client.put(
        f"/workflows/{workflow_id}/pinned/t",
        json={"payload": {"main": serialize_value(Decimal("4.20"))}},
    )

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    results = {n["node_id"]: n for n in run["node_runs"]}
    assert results["c"]["output"]["main"] == "Decimal"
