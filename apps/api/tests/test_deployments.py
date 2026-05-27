from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select

from app.models import Deployment
from app.services import triggers


def _graph_with_trigger() -> dict:
    """Manual-trigger workflow that echoes its input through a Code node."""
    return {
        "nodes": [
            {
                "id": "trig",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "echo",
                "type": "code",
                "params": {"code": "output = input"},
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "trig",
                "source_output": "main",
                "target": "echo",
                "target_input": "input",
            }
        ],
    }


async def _create_workflow(client: AsyncClient) -> str:
    workflow_id = (await client.post("/workflows", json={"name": "WF"})).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": _graph_with_trigger()}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})
    return workflow_id


async def test_deployment_crud(client: AsyncClient) -> None:
    workflow_id = await _create_workflow(client)
    resp = await client.post(
        "/deployments",
        json={
            "workflow_id": workflow_id,
            "name": "Daily 9am Sydney",
            "schedule_cron": "0 9 * * *",
            "schedule_tz": "Australia/Sydney",
            "default_parameters": {"region": "ap-southeast-2"},
            "active": True,
        },
    )
    assert resp.status_code == 201
    deployment = resp.json()
    assert deployment["schedule_tz"] == "Australia/Sydney"
    assert deployment["default_parameters"] == {"region": "ap-southeast-2"}

    listed = (await client.get(f"/deployments?workflow_id={workflow_id}")).json()
    assert len(listed) == 1
    assert listed[0]["id"] == deployment["id"]

    updated = await client.put(
        f"/deployments/{deployment['id']}", json={"active": False}
    )
    assert updated.json()["active"] is False

    deleted = await client.delete(f"/deployments/{deployment['id']}")
    assert deleted.status_code == 204


async def test_run_now_seeds_default_parameters(client: AsyncClient) -> None:
    workflow_id = await _create_workflow(client)
    deployment = (
        await client.post(
            "/deployments",
            json={
                "workflow_id": workflow_id,
                "name": "with params",
                "default_parameters": {"hello": "world"},
                "active": True,
            },
        )
    ).json()

    run_id = (
        await client.post(f"/deployments/{deployment['id']}/run")
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] == "success"
    assert run["trigger_type"] == "deployment"
    results = {n["node_id"]: n for n in run["node_runs"]}
    # The trigger's seeded input flowed through the echo node unchanged.
    assert results["echo"]["output"]["main"] == {"hello": "world"}


async def test_workflow_run_accepts_parameters(client: AsyncClient) -> None:
    workflow_id = await _create_workflow(client)
    run_id = (
        await client.post(
            f"/workflows/{workflow_id}/run",
            json={"parameters": {"answer": 42}},
        )
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] == "success"
    results = {n["node_id"]: n for n in run["node_runs"]}
    assert results["echo"]["output"]["main"] == {"answer": 42}


async def test_active_deployment_overrides_in_graph_schedule(
    client: AsyncClient,
) -> None:
    """A workflow with an active deployment should be fired by the deployment,
    not its in-graph schedule_trigger (single source of truth)."""
    workflow_id = (
        await client.post("/workflows", json={"name": "Both"})
    ).json()["id"]
    graph = {
        "nodes": [
            {
                "id": "sched",
                "type": "schedule_trigger",
                "params": {"interval": "minutes", "every": 1},
                "position": {"x": 0, "y": 0},
            }
        ],
        "edges": [],
    }
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})
    deployment = (
        await client.post(
            "/deployments",
            json={
                "workflow_id": workflow_id,
                "name": "Owns the schedule",
                "schedule_interval": "minutes",
                "schedule_every": 1,
                "active": True,
            },
        )
    ).json()

    await triggers._tick()  # start the clock for the deployment only
    assert (await client.get(f"/workflows/{workflow_id}/runs")).json() == []

    # Rewind the deployment's last_fired by an hour so it's overdue.
    async with triggers.SessionLocal() as session:
        d = (
            await session.scalars(
                select(Deployment).where(Deployment.id == deployment["id"])
            )
        ).one()
        d.last_fired = datetime.now(UTC) - timedelta(hours=1)
        await session.commit()

    await triggers._tick()
    runs = (await client.get(f"/workflows/{workflow_id}/runs")).json()
    # Exactly one run, fired with trigger_type=deployment, not "schedule".
    assert len(runs) == 1
    assert runs[0]["trigger_type"] == "deployment"
