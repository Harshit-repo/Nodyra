from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select

from app.models import ScheduleState
from app.services import triggers


def _webhook_graph(path: str) -> dict:
    return {
        "nodes": [
            {
                "id": "hook",
                "type": "webhook_trigger",
                "params": {"path": path, "http_method": "POST"},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "proc",
                "type": "code",
                "params": {"code": "output = input['body']"},
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "hook",
                "source_output": "main",
                "target": "proc",
                "target_input": "input",
            }
        ],
    }


async def test_webhook_triggers_active_workflow(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Hooked"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}",
        json={"graph": _webhook_graph("orders"), "active": True},
    )

    response = (await client.post("/webhook/orders", json={"order": 42})).json()
    assert len(response["runs"]) == 1

    run = (await client.get(f"/runs/{response['runs'][0]}")).json()
    assert run["status"] == "success"
    assert run["trigger_type"] == "webhook"
    results = {n["node_id"]: n for n in run["node_runs"]}
    assert results["proc"]["output"]["main"] == {"order": 42}


async def test_webhook_with_no_active_workflow(client: AsyncClient) -> None:
    response = (await client.post("/webhook/unknown", json={})).json()
    assert response["runs"] == []


async def test_inactive_workflow_is_not_triggered(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Off"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}",
        json={"graph": _webhook_graph("idle"), "active": False},
    )

    response = (await client.post("/webhook/idle", json={})).json()
    assert response["runs"] == []


async def test_schedule_tick_fires_when_due(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Scheduled"})
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

    await triggers._tick()  # first sighting starts the clock, no run
    assert (await client.get(f"/workflows/{workflow_id}/runs")).json() == []

    # Rewind the persisted last_fired so the schedule is overdue.
    async with triggers.SessionLocal() as session:
        state = (
            await session.scalars(
                select(ScheduleState).where(
                    ScheduleState.workflow_id == workflow_id
                )
            )
        ).one()
        state.last_fired = datetime.now(UTC) - timedelta(hours=1)
        await session.commit()

    await triggers._tick()  # now overdue — should fire

    runs = (await client.get(f"/workflows/{workflow_id}/runs")).json()
    assert len(runs) == 1
    assert runs[0]["trigger_type"] == "schedule"


async def test_schedule_tick_honours_cron(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Cron"})
    ).json()["id"]
    graph = {
        "nodes": [
            {
                "id": "sched",
                "type": "schedule_trigger",
                "params": {"cron": "* * * * *"},  # every minute
                "position": {"x": 0, "y": 0},
            }
        ],
        "edges": [],
    }
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )

    await triggers._tick()  # start the clock
    assert (await client.get(f"/workflows/{workflow_id}/runs")).json() == []

    async with triggers.SessionLocal() as session:
        state = (
            await session.scalars(
                select(ScheduleState).where(
                    ScheduleState.workflow_id == workflow_id
                )
            )
        ).one()
        state.last_fired = datetime.now(UTC) - timedelta(minutes=5)
        await session.commit()

    await triggers._tick()  # a cron minute has elapsed — should fire
    runs = (await client.get(f"/workflows/{workflow_id}/runs")).json()
    assert len(runs) == 1
    assert runs[0]["trigger_type"] == "schedule"
