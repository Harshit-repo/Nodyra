from httpx import AsyncClient


async def test_system_status(client: AsyncClient) -> None:
    resp = await client.get("/system/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["healthy"] is True
    assert body["version"]
    assert "uptime_seconds" in body
    assert body["workflows"] == 0


async def test_metrics_exposes_prometheus_text(client: AsyncClient) -> None:
    await client.post("/workflows", json={"name": "Tracked"})

    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    text = resp.text
    assert "noodle_workflows" in text
    assert "noodle_runs_total" in text
    assert "noodle_uptime_seconds" in text


async def test_runtime_mode_endpoint_reports_topology(client: AsyncClient) -> None:
    resp = await client.get("/ops/runtime-mode")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] in ("local", "production")
    for key in (
        "database_dialect",
        "queue_backend",
        "scheduler_role",
        "webhook_role",
        "artifact_backend",
        "runner_providers",
        "allow_insecure",
        "warnings",
    ):
        assert key in body
    assert isinstance(body["runner_providers"], list)
    assert isinstance(body["warnings"], list)
    # The database dialect is derived from DATABASE_URL, not echoed verbatim.
    assert "://" not in body["database_dialect"]


async def test_queue_stats_empty(client: AsyncClient) -> None:
    resp = await client.get("/ops/queue")
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "queued",
        "leased",
        "running",
        "completed",
        "failed",
        "dead_lettered",
        "cancelled",
    ):
        assert body[key] == 0
    assert body["oldest_queued_age_seconds"] is None


async def test_queue_stats_reflects_queue_entries(client: AsyncClient) -> None:
    # Insert queue entries via the same session factory the endpoint uses, so
    # the queue stats route reads what this test wrote.
    from app.db import get_session
    from app.main import app as fastapi_app
    from app.models import RunQueueEntry

    override = fastapi_app.dependency_overrides[get_session]
    async for session in override():
        session.add(RunQueueEntry(run_id="r1", workflow_id="wf", status="queued"))
        session.add(
            RunQueueEntry(run_id="r2", workflow_id="wf", status="leased", attempts=1)
        )
        session.add(
            RunQueueEntry(
                run_id="r3", workflow_id="wf", status="dead_lettered", attempts=3
            )
        )
        await session.commit()
        break

    resp = await client.get("/ops/queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["queued"] == 1
    assert body["leased"] == 1
    assert body["dead_lettered"] == 1
    assert body["oldest_queued_age_seconds"] is not None
    assert body["oldest_queued_age_seconds"] >= 0


async def test_drain_status_default_false(client: AsyncClient) -> None:
    resp = await client.get('/ops/drain')
    assert resp.status_code == 200
    assert resp.json() == {'draining': False}


async def test_drain_toggle_round_trips(client: AsyncClient) -> None:
    from app.config import settings as app_settings
    try:
        resp = await client.post('/ops/drain', json={'draining': True})
        assert resp.status_code == 200
        assert resp.json() == {'draining': True}
        assert app_settings.queue_drain is True

        resp = await client.get('/ops/drain')
        assert resp.json() == {'draining': True}

        resp = await client.post('/ops/drain', json={'draining': False})
        assert resp.json() == {'draining': False}
        assert app_settings.queue_drain is False
    finally:
        app_settings.queue_drain = False
