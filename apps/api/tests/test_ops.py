from httpx import AsyncClient


async def _seed_parents(session, workflow_id: str, run_ids: list[str]) -> None:
    """Create the parent ``Workflow`` and ``Run`` rows a ``RunQueueEntry``
    references, so its FK (``run_queue.run_id`` -> ``runs.id``) is satisfied.

    SQLite ignores FKs by default, so orphan queue rows slipped through there;
    Postgres enforces them and rejects the insert. Seeding the parents keeps the
    tests honest on both backends.
    """
    from app.models import Run, Workflow

    session.add(Workflow(id=workflow_id, name=f"wf-{workflow_id}"))
    await session.flush()
    for rid in run_ids:
        session.add(Run(id=rid, workflow_id=workflow_id, status="queued"))
    await session.flush()


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
    assert "nodyra_workflows" in text
    assert "nodyra_runs_total" in text
    assert "nodyra_uptime_seconds" in text


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
        await _seed_parents(session, "wf", ["r1", "r2", "r3"])
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


async def test_ops_monitoring_requires_auth_when_auth_enabled(
    client: AsyncClient, monkeypatch
) -> None:
    """Regression: the ops monitoring surface must stay guarded on
    auth-enabled instances (81c0e690's intent) while remaining reachable
    anonymously when auth is off (covered by the tests above)."""
    from app.config import settings as _settings

    monkeypatch.setattr(_settings, "auth_required", True)
    for path in ("/system/status", "/ops/runtime-mode", "/ops/queue", "/ops/drain"):
        response = await client.get(path)
        assert response.status_code == 401, path


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


async def _seed_dead_letter(run_ids: list[str]) -> None:
    """Helper: insert dead-lettered queue entries via the test session factory."""
    from app.db import get_session
    from app.main import app as fastapi_app
    from app.models import RunQueueEntry

    override = fastapi_app.dependency_overrides[get_session]
    async for session in override():
        await _seed_parents(session, "wf-dlq", run_ids)
        for rid in run_ids:
            session.add(
                RunQueueEntry(
                    run_id=rid,
                    workflow_id="wf-dlq",
                    status="dead_lettered",
                    attempts=3,
                    max_attempts=3,
                    last_error="boom",
                )
            )
        await session.commit()
        break


async def test_dead_letter_list_empty(client: AsyncClient) -> None:
    resp = await client.get("/ops/dead-letter")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"entries": [], "total": 0}


async def test_dead_letter_list_returns_dead_lettered_entries(
    client: AsyncClient,
) -> None:
    await _seed_dead_letter(["dl-1", "dl-2"])

    resp = await client.get("/ops/dead-letter")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    rids = sorted(e["run_id"] for e in body["entries"])
    assert rids == ["dl-1", "dl-2"]
    entry = body["entries"][0]
    assert entry["status"] == "dead_lettered"
    assert entry["workflow_id"] == "wf-dlq"
    assert entry["attempts"] == 3
    assert entry["last_error"] == "boom"


async def test_dead_letter_replay_bulk_resets_entries(client: AsyncClient) -> None:
    from sqlalchemy import select

    from app.db import get_session
    from app.main import app as fastapi_app
    from app.models import RunQueueEntry

    await _seed_dead_letter(["dl-a", "dl-b"])

    resp = await client.post("/ops/dead-letter/replay")
    assert resp.status_code == 200
    body = resp.json()
    assert sorted(body["replayed"]) == ["dl-a", "dl-b"]
    assert body["skipped"] == []

    override = fastapi_app.dependency_overrides[get_session]
    async for session in override():
        rows = (await session.scalars(select(RunQueueEntry))).all()
        statuses = {r.run_id: r.status for r in rows}
        assert statuses == {"dl-a": "queued", "dl-b": "queued"}
        # Attempts reset; replay event appended to history.
        for r in rows:
            assert r.attempts == 0
            events = [e.get("event") for e in (r.attempts_log or [])]
            assert "replay" in events
        break

    # Subsequent bulk replay is a no-op now that nothing is dead-lettered.
    resp = await client.post("/ops/dead-letter/replay")
    assert resp.json() == {"replayed": [], "skipped": []}
