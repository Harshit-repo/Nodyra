import asyncio

from httpx import AsyncClient

from app.config import settings

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


async def test_runs_are_listed_for_a_workflow(client: AsyncClient) -> None:
    workflow_id = await _workflow_with_graph(client)
    await client.post(f"/workflows/{workflow_id}/run", json={})
    await client.post(f"/workflows/{workflow_id}/run", json={})

    runs = (await client.get(f"/workflows/{workflow_id}/runs")).json()
    assert len(runs) == 2
    assert all(r["status"] == "success" for r in runs)


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
