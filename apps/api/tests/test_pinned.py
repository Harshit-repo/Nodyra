from httpx import AsyncClient

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
