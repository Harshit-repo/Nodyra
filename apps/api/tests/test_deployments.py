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


async def test_active_deployment_rejected_for_triggerless_workflow(
    client: AsyncClient,
) -> None:
    """A workflow whose published graph has no trigger can't be activated —
    the error surfaces at create/activate, not silently at every fire."""
    workflow_id = (await client.post("/workflows", json={"name": "empty"})).json()[
        "id"
    ]
    await client.put(f"/workflows/{workflow_id}", json={"graph": {"nodes": [], "edges": []}})
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    # Active create is refused...
    resp = await client.post(
        "/deployments",
        json={"workflow_id": workflow_id, "name": "nope", "active": True},
    )
    assert resp.status_code == 400
    assert "trigger" in resp.json()["detail"].lower()

    # ...a paused one is allowed, but activating it later is refused.
    paused = await client.post(
        "/deployments",
        json={"workflow_id": workflow_id, "name": "paused", "active": False},
    )
    assert paused.status_code == 201
    activate = await client.put(
        f"/deployments/{paused.json()['id']}", json={"active": True}
    )
    assert activate.status_code == 400
    # ...and run-now gives the same clear error rather than a generic failure.
    run = await client.post(f"/deployments/{paused.json()['id']}/run")
    assert run.status_code == 400
    assert "trigger" in run.json()["detail"].lower()


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
    assert len(listed["items"]) == 1
    assert listed["items"][0]["id"] == deployment["id"]

    updated = await client.put(
        f"/deployments/{deployment['id']}", json={"active": False}
    )
    assert updated.json()["active"] is False

    deleted = await client.delete(f"/deployments/{deployment['id']}")
    assert deleted.status_code == 204


async def test_deployment_interval_update_clears_stale_cron(
    client: AsyncClient,
) -> None:
    workflow_id = await _create_workflow(client)
    deployment = (
        await client.post(
            "/deployments",
            json={
                "workflow_id": workflow_id,
                "name": "Hourly",
                "schedule_cron": "0 * * * *",
                "active": False,
            },
        )
    ).json()

    updated = (
        await client.put(
            f"/deployments/{deployment['id']}",
            json={"schedule_interval": "minutes", "schedule_every": 1},
        )
    ).json()

    assert updated["schedule_cron"] == ""
    assert updated["schedule_interval"] == "minutes"
    assert updated["schedule_every"] == 1


async def test_publish_update_deployments_syncs_schedule_cadence(
    client: AsyncClient,
) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Scheduled"})
    ).json()["id"]
    hourly_graph = {
        "nodes": [
            {
                "id": "sched",
                "type": "schedule_trigger",
                "params": {
                    "interval": "hours",
                    "every": 1,
                    "cron": "0 * * * *",
                    "tz": "Australia/Sydney",
                },
                "position": {"x": 0, "y": 0},
            }
        ],
        "edges": [],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": hourly_graph})
    await client.post(f"/workflows/{workflow_id}/publish", json={})
    deployment = (
        await client.post(
            "/deployments",
            json={
                "workflow_id": workflow_id,
                "name": "Production schedule",
                "schedule_cron": "0 * * * *",
                "schedule_interval": "hours",
                "schedule_every": 1,
                "schedule_tz": "Australia/Sydney",
                "active": True,
            },
        )
    ).json()

    minute_graph = {
        **hourly_graph,
        "nodes": [
            {
                **hourly_graph["nodes"][0],
                "params": {
                    "interval": "minutes",
                    "every": 1,
                    "cron": "",
                    "tz": "Australia/Sydney",
                },
            }
        ],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": minute_graph})
    published = (
        await client.post(
            f"/workflows/{workflow_id}/publish",
            json={"update_deployments": True},
        )
    ).json()

    updated = (await client.get(f"/deployments/{deployment['id']}")).json()
    assert published["updated_deployments"] == 1
    assert updated["workflow_version_id"] == published["workflow_version_id"]
    assert updated["schedule_cron"] == ""
    assert updated["schedule_interval"] == "minutes"
    assert updated["schedule_every"] == 1
    assert updated["schedule_tz"] == "Australia/Sydney"


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
    assert (await client.get(f"/workflows/{workflow_id}/runs")).json()["items"] == []

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
    runs = (await client.get(f"/workflows/{workflow_id}/runs")).json()["items"]
    # Exactly one run, fired with trigger_type=deployment, not "schedule".
    assert len(runs) == 1
    assert runs[0]["trigger_type"] == "deployment"


# --- Task 16: unsafe-node policy gate ----------------------------------------

import pytest  # noqa: E402

from app.config import settings  # noqa: E402
from app.services.unsafe_nodes import classify as classify_unsafe_nodes  # noqa: E402


def _http_graph(url: str) -> dict:
    return {
        "nodes": [
            {"id": "trig", "type": "manual_trigger", "params": {}, "position": {"x": 0, "y": 0}},
            {
                "id": "fetch",
                "type": "http_request",
                "params": {"url": url, "method": "GET"},
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "trig",
                "source_output": "main",
                "target": "fetch",
                "target_input": "input",
            }
        ],
    }


async def _publish(client: AsyncClient, graph: dict) -> str:
    wf_id = (await client.post("/workflows", json={"name": "WF"})).json()["id"]
    await client.put(f"/workflows/{wf_id}", json={"graph": graph})
    await client.post(f"/workflows/{wf_id}/publish", json={})
    return wf_id


def test_classify_flags_code_http_private_and_sql_expressions() -> None:
    graph = {
        "nodes": [
            {"id": "c1", "type": "code", "params": {"code": "output = 1"}},
            {"id": "h1", "type": "http_request", "params": {"url": "http://10.0.0.1/api"}},
            {"id": "h2", "type": "http_request", "params": {"url": "https://example.com/x"}},
            {"id": "q1", "type": "postgres_query", "params": {"query": "select {{ input.id }}"}},
            {"id": "q2", "type": "postgres_query", "params": {"query": "select 1"}},
            {"id": "x1", "type": "execute_command", "params": {"command": "ls"}},
            {"id": "s1", "type": "ssh_execute", "params": {}},
            {"id": "n1", "type": "manual_trigger", "params": {}},
        ],
        "edges": [],
    }
    findings = classify_unsafe_nodes(graph)
    kinds = {(f["node_id"], f["kind"]) for f in findings}
    assert kinds == {
        ("c1", "code"),
        ("h1", "http_private_ip"),
        ("q1", "sql_with_expressions"),
        # postgres_query opens a credentialed DB connection to an arbitrary
        # host, so it is also flagged as network_egress (SSRF surface) — both
        # postgres nodes get this, plus q1's expression-injection finding.
        ("q1", "network_egress"),
        ("q2", "network_egress"),
        ("x1", "execute_command"),
        ("s1", "ssh"),
    }


async def test_warn_policy_allows_activation_with_risky_node(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "unsafe_node_policy", "warn")
    workflow_id = await _create_workflow(client)  # contains a Code node
    resp = await client.post(
        "/deployments",
        json={"workflow_id": workflow_id, "name": "warn-ok", "active": True},
    )
    assert resp.status_code == 201
    assert resp.json()["active"] is True


async def test_require_approval_blocks_without_flag_then_passes_with_flag(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "unsafe_node_policy", "require_approval")
    workflow_id = await _create_workflow(client)
    blocked = await client.post(
        "/deployments",
        json={"workflow_id": workflow_id, "name": "needs-approval", "active": True},
    )
    assert blocked.status_code == 409
    detail = blocked.json()["detail"]
    assert detail["policy"] == "require_approval"
    assert any(f["kind"] == "code" for f in detail["findings"])

    approved = await client.post(
        "/deployments",
        json={
            "workflow_id": workflow_id,
            "name": "needs-approval-2",
            "active": True,
            "approve_unsafe_nodes": True,
        },
    )
    assert approved.status_code == 201


async def test_block_policy_rejects_even_with_approval(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "unsafe_node_policy", "block")
    workflow_id = await _create_workflow(client)
    resp = await client.post(
        "/deployments",
        json={
            "workflow_id": workflow_id,
            "name": "never",
            "active": True,
            "approve_unsafe_nodes": True,
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["policy"] == "block"


async def test_policy_only_checked_when_activating(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "unsafe_node_policy", "block")
    workflow_id = await _create_workflow(client)
    # Inactive deployment with risky nodes is fine — it never runs on a schedule.
    resp = await client.post(
        "/deployments",
        json={"workflow_id": workflow_id, "name": "draft", "active": False},
    )
    assert resp.status_code == 201
    # And flipping it active later must trigger the gate.
    deployment_id = resp.json()["id"]
    flip = await client.put(f"/deployments/{deployment_id}", json={"active": True})
    assert flip.status_code == 409


async def test_safe_workflow_passes_block_policy(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "unsafe_node_policy", "block")
    safe_graph = {
        "nodes": [
            {"id": "trig", "type": "manual_trigger", "params": {}, "position": {"x": 0, "y": 0}},
        ],
        "edges": [],
    }
    workflow_id = await _publish(client, safe_graph)
    resp = await client.post(
        "/deployments",
        json={"workflow_id": workflow_id, "name": "safe", "active": True},
    )
    assert resp.status_code == 201


