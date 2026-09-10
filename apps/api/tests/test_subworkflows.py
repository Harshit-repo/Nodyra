from unittest.mock import AsyncMock

import pytest
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


@pytest.mark.parametrize("same_environment", [False, True])
async def test_sandbox_child_never_falls_back_to_host_subprocess(
    client, monkeypatch, same_environment
):
    from app.config import settings
    from app.services import runtime_pool, sandbox_pool
    from app.services.subworkflows import resolve_subworkflow
    from nodyra.engine.subworkflows import InlineSubworkflow, SubworkflowCall

    workflow = (await client.post("/workflows", json={"name": "Sandbox child"})).json()
    await client.put(
        f"/workflows/{workflow['id']}", json={"graph": _trigger_to_code("output = input")}
    )
    monkeypatch.setattr(settings, "use_subprocess_runner", True)
    host_dispatch = AsyncMock(side_effect=AssertionError("Sandbox child escaped to host execution"))
    sandbox_dispatch = AsyncMock(return_value="success")
    monkeypatch.setattr(runtime_pool.pool, "dispatch_subworkflow", host_dispatch)
    monkeypatch.setattr(sandbox_pool.pool, "dispatch", sandbox_dispatch)
    result = await resolve_subworkflow(
        SubworkflowCall(
            workflow_id=workflow["id"],
            parameters=7,
            use_published=False,
            parent_run_id=None,
            depth=1,
            org_id="default",
        ),
        parent_env_id=None if same_environment else "parent-env",
        parent_sandboxed=True,
    )
    host_dispatch.assert_not_awaited()
    if same_environment:
        assert isinstance(result, InlineSubworkflow)
        sandbox_dispatch.assert_not_awaited()
    else:
        sandbox_dispatch.assert_awaited_once()
        assert sandbox_dispatch.await_args.kwargs["org_id"] == "default"


async def test_failed_subworkflow_marks_parent_failed(client):
    child = (await client.post("/workflows", json={"name": "Failing child"})).json()
    await client.put(
        f"/workflows/{child['id']}",
        json={"graph": _trigger_to_code("raise ValueError('invoice rejected')")},
    )
    parent = (await client.post("/workflows", json={"name": "Parent failure propagation"})).json()
    graph = _trigger_to_code("output = input")
    graph["nodes"][1].update(type="execute_workflow", params={"workflow_id": child["id"]})
    await client.put(f"/workflows/{parent['id']}", json={"graph": graph})
    run_id = (await client.post(f"/workflows/{parent['id']}/run", json={})).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] == "error", run
    assert any("invoice rejected" in (node.get("error") or "") for node in run["node_runs"])


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

    run_id = (await client.post(f"/workflows/{parent['id']}/run", json={})).json()["run_id"]
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

    run_id = (await client.post(f"/workflows/{workflow['id']}/run", json={})).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    results = {n["node_id"]: n for n in run["node_runs"]}
    assert run["status"] == "error"
    assert results["sub"]["status"] == "error"
    assert "cycle" in (results["sub"].get("error") or "")


async def test_subworkflow_uses_published_version_in_production(
    client: AsyncClient,
) -> None:
    """Production runs (webhook/schedule/deployment) must invoke the
    sub-workflow's published version, never its editable draft. Editor
    manual runs propagate "use draft" so iteration doesn't require
    publishing every dependency.
    """
    # Sub-workflow: publish "PUBLISHED", then edit draft to "DRAFT" without
    # publishing. Returns a constant so we don't have to wrangle webhook
    # payload shapes.
    sub = (await client.post("/workflows", json={"name": "Sub"})).json()
    await client.put(
        f"/workflows/{sub['id']}",
        json={"graph": _trigger_to_code("output = 'PUBLISHED'")},
    )
    await client.post(f"/workflows/{sub['id']}/publish", json={})
    await client.put(
        f"/workflows/{sub['id']}",
        json={"graph": _trigger_to_code("output = 'DRAFT'")},
    )

    # Parent: webhook → execute_workflow(sub). Publish + activate so it
    # fires on the production webhook path (and runs in production mode).
    parent = (await client.post("/workflows", json={"name": "Parent"})).json()
    parent_graph = {
        "nodes": [
            {
                "id": "t",
                "type": "webhook_trigger",
                "params": {"path": "prod-sub"},
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
    await client.post(f"/workflows/{parent['id']}/publish", json={})
    await client.put(f"/workflows/{parent['id']}", json={"active": True})

    response = await client.post("/webhook/prod-sub", json={})
    run_id = response.json()["runs"][0]
    run = (await client.get(f"/runs/{run_id}")).json()
    results = {n["node_id"]: n for n in run["node_runs"]}
    assert run["status"] == "success"
    # Slice 11 regression guard: production sub-workflow calls must use the
    # published version, even though the sub has a newer unpublished draft.
    assert results["sub"]["output"]["main"] == "PUBLISHED"


async def test_execute_workflow_requires_workflow_id(client: AsyncClient) -> None:
    workflow = (await client.post("/workflows", json={"name": "Empty Sub"})).json()
    graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "sub",
                "type": "execute_workflow",
                "params": {"workflow_id": ""},
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

    run_id = (await client.post(f"/workflows/{workflow['id']}/run", json={})).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    results = {n["node_id"]: n for n in run["node_runs"]}
    assert results["sub"]["status"] == "error"
    assert "workflow_id" in (results["sub"].get("error") or "")
