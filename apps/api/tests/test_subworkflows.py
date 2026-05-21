from httpx import AsyncClient


def _trigger_to_code(code: str) -> dict:
    return {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "c",
                "type": "code",
                "params": {"code": code},
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


async def test_execute_workflow_runs_sub_workflow(client: AsyncClient) -> None:
    # Sub-workflow doubles the input.
    sub = (await client.post("/workflows", json={"name": "Sub"})).json()
    await client.put(
        f"/workflows/{sub['id']}",
        json={"graph": _trigger_to_code("output = input * 2")},
    )

    # Parent workflow calls the sub.
    parent = (await client.post("/workflows", json={"name": "Parent"})).json()
    parent_graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {"data": 7},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "sub",
                "type": "execute_workflow",
                "params": {"workflow_id": sub["id"]},
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e",
                "source": "t",
                "source_output": "main",
                "target": "sub",
                "target_input": "input",
            }
        ],
    }
    await client.put(f"/workflows/{parent['id']}", json={"graph": parent_graph})

    run_id = (
        await client.post(f"/workflows/{parent['id']}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    results = {n["node_id"]: n for n in run["node_runs"]}
    assert run["status"] == "success"
    assert results["sub"]["output"]["main"] == 14


async def test_execute_workflow_self_call_is_a_cycle(client: AsyncClient) -> None:
    workflow = (await client.post("/workflows", json={"name": "Loop"})).json()
    graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {"data": 0},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "sub",
                "type": "execute_workflow",
                "params": {"workflow_id": workflow["id"]},
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e",
                "source": "t",
                "source_output": "main",
                "target": "sub",
                "target_input": "input",
            }
        ],
    }
    await client.put(f"/workflows/{workflow['id']}", json={"graph": graph})

    run_id = (
        await client.post(f"/workflows/{workflow['id']}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    results = {n["node_id"]: n for n in run["node_runs"]}
    assert run["status"] == "error"
    assert results["sub"]["status"] == "error"
    assert "cycle" in (results["sub"].get("error") or "")


async def test_execute_workflow_requires_workflow_id(client: AsyncClient) -> None:
    workflow = (await client.post("/workflows", json={"name": "Empty Sub"})).json()
    graph = {
        "nodes": [
            {
                "id": "sub",
                "type": "execute_workflow",
                "params": {"workflow_id": ""},
                "position": {"x": 0, "y": 0},
            },
        ],
        "edges": [],
    }
    await client.put(f"/workflows/{workflow['id']}", json={"graph": graph})

    run_id = (
        await client.post(f"/workflows/{workflow['id']}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    results = {n["node_id"]: n for n in run["node_runs"]}
    assert results["sub"]["status"] == "error"
    assert "workflow_id" in (results["sub"].get("error") or "")
