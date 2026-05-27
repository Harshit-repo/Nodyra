"""Trigger-gated workflow execution (Slice 23).

Confirms that a workflow with multiple triggers only fires the chosen branch
on each dispatch path, that a workflow with no trigger is rejected, and that
the existing explicit-targets path (retry / rerun / "Run this step") is
unaffected.
"""
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select

from app.models import ScheduleState
from app.services import triggers

# Graph with TWO disjoint trigger branches sharing no edges:
#   manual_trigger("m") ── e_m ──> code("m_out")
#   webhook_trigger("hook") ── e_h ──> code("h_out")
TWO_TRIGGER_GRAPH = {
    "nodes": [
        {
            "id": "m",
            "type": "manual_trigger",
            "params": {"data": {"ok": True}},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "m_out",
            "type": "code",
            "params": {"code": "output = 'manual'"},
            "position": {"x": 250, "y": 0},
        },
        {
            "id": "hook",
            "type": "webhook_trigger",
            "params": {"path": "two-trigger", "http_method": "POST"},
            "position": {"x": 0, "y": 200},
        },
        {
            "id": "h_out",
            "type": "code",
            "params": {"code": "output = 'webhook'"},
            "position": {"x": 250, "y": 200},
        },
    ],
    "edges": [
        {
            "id": "e_m",
            "source": "m",
            "source_output": "main",
            "target": "m_out",
            "target_input": "input",
        },
        {
            "id": "e_h",
            "source": "hook",
            "source_output": "main",
            "target": "h_out",
            "target_input": "input",
        },
    ],
}


async def _workflow_with(client: AsyncClient, name: str, graph: dict) -> str:
    workflow_id = (await client.post("/workflows", json={"name": name})).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})
    return workflow_id


async def test_editor_run_with_manual_trigger_id_only_runs_manual_branch(
    client: AsyncClient,
) -> None:
    workflow_id = await _workflow_with(client, "Gated", TWO_TRIGGER_GRAPH)

    run_id = (
        await client.post(
            f"/workflows/{workflow_id}/run", json={"trigger_node_id": "m"}
        )
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] == "success"

    node_ids = {n["node_id"] for n in run["node_runs"]}
    assert node_ids == {"m", "m_out"}


async def test_editor_run_default_prefers_manual_trigger(
    client: AsyncClient,
) -> None:
    """No ``trigger_node_id`` + a manual_trigger present → manual fires."""
    workflow_id = await _workflow_with(client, "DefaultPick", TWO_TRIGGER_GRAPH)

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()

    node_ids = {n["node_id"] for n in run["node_runs"]}
    assert node_ids == {"m", "m_out"}


async def test_webhook_dispatch_only_runs_webhook_branch(
    client: AsyncClient,
) -> None:
    workflow_id = await _workflow_with(client, "Hooked", TWO_TRIGGER_GRAPH)
    await client.put(f"/workflows/{workflow_id}", json={"active": True})
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    response = (
        await client.post("/webhook/two-trigger", json={"order": 42})
    ).json()
    assert len(response["runs"]) == 1
    run = (await client.get(f"/runs/{response['runs'][0]}")).json()
    assert run["status"] == "success"

    node_ids = {n["node_id"] for n in run["node_runs"]}
    assert node_ids == {"hook", "h_out"}


async def test_run_without_any_trigger_returns_400(client: AsyncClient) -> None:
    no_trigger = {
        "nodes": [
            {
                "id": "lonely",
                "type": "code",
                "params": {"code": "output = 1"},
                "position": {"x": 0, "y": 0},
            }
        ],
        "edges": [],
    }
    workflow_id = await _workflow_with(client, "Triggerless", no_trigger)

    response = await client.post(f"/workflows/{workflow_id}/run", json={})
    assert response.status_code == 400
    assert "trigger" in response.json()["detail"].lower()


async def test_explicit_targets_path_still_runs_subset(
    client: AsyncClient,
) -> None:
    """The retry / "Run this step" path must keep working unchanged."""
    workflow_id = await _workflow_with(client, "Subset", TWO_TRIGGER_GRAPH)

    # targets=["m_out"] + cache for "m" emulates the editor's "Run this step"
    # flow: the gating helper should pass `targets` through and skip trigger
    # resolution.
    run_id = (
        await client.post(
            f"/workflows/{workflow_id}/run",
            json={
                "targets": ["m_out"],
                "cache": {"m": {"main": {"seeded": True}}},
            },
        )
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] == "success"

    node_ids = {n["node_id"] for n in run["node_runs"]}
    assert node_ids == {"m", "m_out"}


async def test_scheduler_only_fires_due_trigger_in_two_schedule_graph(
    client: AsyncClient,
) -> None:
    """A graph with two unrelated schedule triggers must only fire one
    branch per tick (the first trigger in graph order, by convention)."""
    graph = {
        "nodes": [
            {
                "id": "s1",
                "type": "schedule_trigger",
                "params": {"interval": "minutes", "every": 1},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "s1_out",
                "type": "code",
                "params": {"code": "output = 'first'"},
                "position": {"x": 250, "y": 0},
            },
            {
                "id": "s2",
                "type": "schedule_trigger",
                "params": {"interval": "minutes", "every": 1},
                "position": {"x": 0, "y": 200},
            },
            {
                "id": "s2_out",
                "type": "code",
                "params": {"code": "output = 'second'"},
                "position": {"x": 250, "y": 200},
            },
        ],
        "edges": [
            {
                "id": "ea",
                "source": "s1",
                "source_output": "main",
                "target": "s1_out",
                "target_input": "input",
            },
            {
                "id": "eb",
                "source": "s2",
                "source_output": "main",
                "target": "s2_out",
                "target_input": "input",
            },
        ],
    }
    workflow_id = await _workflow_with(client, "TwoSched", graph)
    await client.put(f"/workflows/{workflow_id}", json={"active": True})
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    await triggers._tick()  # first sighting establishes ScheduleState
    assert (await client.get(f"/workflows/{workflow_id}/runs")).json() == []

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

    await triggers._tick()
    runs = (await client.get(f"/workflows/{workflow_id}/runs")).json()
    assert len(runs) == 1
    run = (await client.get(f"/runs/{runs[0]['id']}")).json()
    node_ids = {n["node_id"] for n in run["node_runs"]}
    # First schedule in graph order is the one that fires; sibling stays inert.
    assert node_ids == {"s1", "s1_out"}


async def test_subworkflow_does_not_fire_sibling_trigger(
    client: AsyncClient,
) -> None:
    """A sub-workflow with two unrelated triggers must execute only the
    branch that received the seeded input."""
    # Sub has two disjoint branches; the first trigger receives the seed.
    sub_graph = {
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
                "params": {"code": "output = ('main', input)"},
                "position": {"x": 250, "y": 0},
            },
            {
                "id": "stray",
                "type": "schedule_trigger",
                "params": {"interval": "minutes", "every": 1},
                "position": {"x": 0, "y": 200},
            },
            {
                "id": "stray_out",
                "type": "code",
                "params": {"code": "output = 'stray ran!'"},
                "position": {"x": 250, "y": 200},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "t",
                "source_output": "main",
                "target": "c",
                "target_input": "input",
            },
            {
                "id": "e2",
                "source": "stray",
                "source_output": "main",
                "target": "stray_out",
                "target_input": "input",
            },
        ],
    }
    sub = (await client.post("/workflows", json={"name": "Sub"})).json()
    await client.put(f"/workflows/{sub['id']}", json={"graph": sub_graph})

    parent_graph = {
        "nodes": [
            {
                "id": "pt",
                "type": "manual_trigger",
                "params": {"data": 99},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "call",
                "type": "execute_workflow",
                "params": {"workflow_id": sub["id"]},
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e",
                "source": "pt",
                "source_output": "main",
                "target": "call",
                "target_input": "input",
            }
        ],
    }
    parent = (await client.post("/workflows", json={"name": "Parent"})).json()
    await client.put(f"/workflows/{parent['id']}", json={"graph": parent_graph})

    run_id = (
        await client.post(f"/workflows/{parent['id']}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] == "success"

    # The sub-workflow's stray branch must not have produced 'stray ran!'.
    sub_call = next(
        nr for nr in run["node_runs"] if nr["node_id"] == "call"
    )
    output = sub_call["output"]["main"]
    # output of `c` in the sub is the tuple ('main', 99); serialization may
    # turn it into a list, so just confirm it's not the stray branch.
    flat = repr(output)
    assert "stray ran!" not in flat


async def test_invalid_trigger_node_id_returns_400(
    client: AsyncClient,
) -> None:
    workflow_id = await _workflow_with(client, "Bad", TWO_TRIGGER_GRAPH)
    response = await client.post(
        f"/workflows/{workflow_id}/run",
        json={"trigger_node_id": "does_not_exist"},
    )
    assert response.status_code == 400


async def test_trigger_node_id_referencing_non_trigger_returns_400(
    client: AsyncClient,
) -> None:
    workflow_id = await _workflow_with(client, "NotTrigger", TWO_TRIGGER_GRAPH)
    response = await client.post(
        f"/workflows/{workflow_id}/run",
        json={"trigger_node_id": "m_out"},  # a code node
    )
    assert response.status_code == 400
