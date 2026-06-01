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
    await client.post(f"/workflows/{workflow_id}/publish", json={})

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
    await client.post(f"/workflows/{workflow_id}/publish", json={})

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
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    await triggers._tick()  # first sighting starts the clock, no run
    assert (await client.get(f"/workflows/{workflow_id}/runs")).json()["items"] == []

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

    runs = (await client.get(f"/workflows/{workflow_id}/runs")).json()["items"]
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
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    await triggers._tick()  # start the clock
    assert (await client.get(f"/workflows/{workflow_id}/runs")).json()["items"] == []

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
    runs = (await client.get(f"/workflows/{workflow_id}/runs")).json()["items"]
    assert len(runs) == 1
    assert runs[0]["trigger_type"] == "schedule"


def test_is_due_unknown_timezone_does_not_fire() -> None:
    """An invalid IANA name must not silently fall back to UTC."""
    triggers._logged_bad_tz.clear()
    last = datetime.now(UTC) - timedelta(hours=5)
    now = datetime.now(UTC)
    params = {"cron": "* * * * *", "tz": "Mars/Olympus_Mons"}
    assert triggers._is_due(params, last, now) is False
    # And the warning is flood-controlled — only the first invalid hit logs.
    assert "Mars/Olympus_Mons" in triggers._logged_bad_tz


# --- Webhook auth (Slice 21) -------------------------------------------------


def _webhook_graph_with_auth(path: str, auth_params: dict) -> dict:
    graph = _webhook_graph(path)
    graph["nodes"][0]["params"].update(auth_params)
    return graph


async def test_webhook_basic_auth_rejects_missing_credentials(
    client: AsyncClient,
) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Basic"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "secured",
        {
            "auth_type": "basic",
            "auth_username": "alice",
            "auth_password": "wonderland",
        },
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    response = await client.post("/webhook/secured", json={})
    assert response.status_code == 401


async def test_webhook_basic_auth_accepts_valid_credentials(
    client: AsyncClient,
) -> None:
    import base64

    workflow_id = (
        await client.post("/workflows", json={"name": "Basic2"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "secured2",
        {
            "auth_type": "basic",
            "auth_username": "alice",
            "auth_password": "wonderland",
        },
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    token = base64.b64encode(b"alice:wonderland").decode("ascii")
    response = await client.post(
        "/webhook/secured2",
        headers={"Authorization": f"Basic {token}"},
        json={"order": 1},
    )
    body = response.json()
    assert response.status_code == 200
    assert len(body["runs"]) == 1


async def test_webhook_header_auth_checks_value(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Header"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "header-auth",
        {
            "auth_type": "header",
            "auth_header_name": "X-API-Key",
            "auth_header_value": "supersecret",
        },
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    wrong = await client.post(
        "/webhook/header-auth",
        headers={"X-API-Key": "nope"},
        json={},
    )
    assert wrong.status_code == 401
    ok = await client.post(
        "/webhook/header-auth",
        headers={"X-API-Key": "supersecret"},
        json={},
    )
    assert ok.status_code == 200
    assert len(ok.json()["runs"]) == 1


async def test_webhook_query_auth_checks_value(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Query"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "query-auth",
        {
            "auth_type": "query",
            "auth_query_name": "token",
            "auth_query_value": "tokentokentoken",
        },
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    wrong = await client.post("/webhook/query-auth?token=wrong", json={})
    assert wrong.status_code == 401
    ok = await client.post(
        "/webhook/query-auth?token=tokentokentoken", json={}
    )
    assert ok.status_code == 200
    assert len(ok.json()["runs"]) == 1


async def test_webhook_unknown_path_still_returns_200(client: AsyncClient) -> None:
    """Unknown paths return 200 with empty runs (existing behaviour).

    Only path matches that *exist but fail auth* return 401.
    """
    response = await client.post("/webhook/nobody-listens", json={})
    assert response.status_code == 200
    assert response.json()["runs"] == []


def test_is_due_different_timezones_fire_at_different_utc() -> None:
    """Same cron expression resolves to different UTC fire times per tz.

    Cron '0 9 * * *' fires daily at 09:00 local. NY (UTC-4 in May DST) fires
    at 13:00 UTC; Sydney (UTC+10) fires at 23:00 UTC. Anchor ``last`` and
    ``now`` so NY has crossed today's 09:00 but Sydney hasn't yet.
    """
    last = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)  # 08:00 NY / 22:00 Sydney
    now = datetime(2026, 5, 26, 14, 0, tzinfo=UTC)  # past 13:00 NY only

    sydney_due = triggers._is_due(
        {"cron": "0 9 * * *", "tz": "Australia/Sydney"}, last, now
    )
    ny_due = triggers._is_due(
        {"cron": "0 9 * * *", "tz": "America/New_York"}, last, now
    )
    assert ny_due is True
    assert sydney_due is False


# --- Task 8: webhook ingress role split --------------------------------------

async def test_webhook_role_inline_mounts_router(client: AsyncClient) -> None:
    """Default webhook_role=='inline' — /webhook/* is reachable."""
    resp = await client.post("/webhook/no-such-path", json={})
    # 200 is the "accepted, no matching trigger" reply; we just need NOT 404.
    assert resp.status_code != 404


async def test_webhook_role_disabled_unmounts_router(monkeypatch) -> None:
    """webhook_role=='disabled' — /webhook/* is not registered on the app."""
    import importlib
    from app import config as _config
    from app.config import Settings

    # Build a settings instance with webhook_role=disabled, then reload main.
    new_settings = Settings(webhook_role="disabled")
    monkeypatch.setattr(_config, "settings", new_settings)

    import app.main as _main
    reloaded = importlib.reload(_main)
    paths = {getattr(r, "path", "") for r in reloaded.app.routes}
    assert not any(p.startswith("/webhook") for p in paths), (
        "/webhook routes should not be mounted when webhook_role=disabled"
    )

    # Restore normal inline behaviour for subsequent tests in the session.
    monkeypatch.setattr(_config, "settings", Settings())
    importlib.reload(_main)


