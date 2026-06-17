"""dispatch_role topology (program A1): config validation + queued-only starts."""

from app.config import Settings


def test_inline_role_has_no_topology_errors():
    s = Settings(dispatch_role="inline", database_url="sqlite+aiosqlite:///x.db")
    assert s.dispatch_topology_errors() == []


def test_worker_role_requires_redis_and_postgres():
    s = Settings(
        dispatch_role="worker",
        queue_backend="none",
        database_url="sqlite+aiosqlite:///x.db",
    )
    errors = s.dispatch_topology_errors()
    assert any("queue_backend" in e for e in errors)
    assert any("postgres" in e.lower() for e in errors)


def test_disabled_role_valid_with_redis_and_postgres():
    s = Settings(
        dispatch_role="disabled",
        queue_backend="redis",
        database_url="postgresql+asyncpg://u:p@h:5432/db",
    )
    assert s.dispatch_topology_errors() == []


def test_control_role_valid_with_redis_and_postgres():
    s = Settings(
        dispatch_role="control",
        queue_backend="redis",
        database_url="postgresql+asyncpg://u:p@h:5432/db",
    )
    assert s.dispatch_topology_errors() == []


def test_control_role_requires_redis_and_postgres():
    s = Settings(
        dispatch_role="control",
        queue_backend="none",
        database_url="sqlite+aiosqlite:///x.db",
    )
    errors = s.dispatch_topology_errors()
    assert any("queue_backend" in e for e in errors)
    assert any("postgres" in e.lower() for e in errors)


async def test_start_run_parks_on_queue_when_dispatch_disabled(
    client, monkeypatch
) -> None:
    """dispatch_role=disabled: the API is a pure control plane — every run is
    parked on the durable queue for a worker to lease, never executed inline."""
    from sqlalchemy import select

    from app.config import settings
    from app.db import get_session
    from app.main import app as fastapi_app
    from app.models import Run, RunQueueEntry

    graph = {
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {},
             "position": {"x": 0, "y": 0}},
        ],
        "edges": [],
    }
    workflow_id = (
        await client.post("/workflows", json={"name": "disabled-role"})
    ).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    monkeypatch.setattr(settings, "run_synchronously", False)
    monkeypatch.setattr(settings, "dispatch_role", "disabled")

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]

    override = fastapi_app.dependency_overrides[get_session]
    async for session in override():
        run = await session.get(Run, run_id)
        assert run.status == "queued"
        entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
        )
        assert entry is not None
        assert entry.status == "queued"
        assert entry.queue_reason == "dispatch_disabled"
        break


async def test_start_run_parks_on_queue_when_dispatch_control(
    client, monkeypatch
) -> None:
    """dispatch_role=control parks every run like disabled (a worker runs
    local/docker; this replica's dispatch loop leases only agent/kubernetes)."""
    from sqlalchemy import select

    from app.config import settings
    from app.db import get_session
    from app.main import app as fastapi_app
    from app.models import Run, RunQueueEntry

    graph = {
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {},
             "position": {"x": 0, "y": 0}},
        ],
        "edges": [],
    }
    workflow_id = (
        await client.post("/workflows", json={"name": "control-role"})
    ).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    monkeypatch.setattr(settings, "run_synchronously", False)
    monkeypatch.setattr(settings, "dispatch_role", "control")

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]

    override = fastapi_app.dependency_overrides[get_session]
    async for session in override():
        run = await session.get(Run, run_id)
        assert run.status == "queued"
        entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
        )
        assert entry is not None
        assert entry.status == "queued"
        assert entry.queue_reason == "dispatch_disabled"
        break


async def test_inline_start_run_queue_ledger_is_not_leaseable(
    client, monkeypatch
) -> None:
    """Inline-owned runs keep a queue ledger row, but workers must not lease it."""
    from sqlalchemy import select

    from app.config import settings
    from app.db import get_session
    from app.main import app as fastapi_app
    from app.models import RunQueueEntry
    from app.services import queue as queue_module
    from app.services import runner as runner_module

    graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 0, "y": 0},
            },
        ],
        "edges": [],
    }
    workflow_id = (
        await client.post("/workflows", json={"name": "inline-ledger"})
    ).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    async def fake_execute_run(*args, **kwargs):
        return None

    notified = False

    async def fake_notify():
        nonlocal notified
        notified = True

    monkeypatch.setattr(settings, "run_synchronously", False)
    monkeypatch.setattr(settings, "dispatch_role", "inline")
    monkeypatch.setattr(settings, "local_queue_enabled", False)
    monkeypatch.setattr(runner_module, "_execute_run", fake_execute_run)
    monkeypatch.setattr(queue_module, "notify_queue_workers", fake_notify)

    run_id = await runner_module.start_run(workflow_id, graph, 1)

    override = fastapi_app.dependency_overrides[get_session]
    async for session in override():
        entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
        )
        assert entry is not None
        assert entry.status == "running"
        assert await queue_module.lease(session, worker_id="worker") is None
        break
    assert notified is False


def test_worker_main_validation_rejects_bad_config(monkeypatch):
    import pytest

    from app import worker_main
    from app.config import settings

    monkeypatch.setattr(settings, "dispatch_role", "inline")
    monkeypatch.setattr(settings, "queue_backend", "none")
    with pytest.raises(SystemExit) as exc:
        worker_main._validate()
    assert "DISPATCH_ROLE=worker" in str(exc.value)


def test_worker_main_validation_accepts_worker_config(monkeypatch):
    from app import worker_main
    from app.config import settings

    monkeypatch.setattr(settings, "dispatch_role", "worker")
    monkeypatch.setattr(settings, "queue_backend", "redis")
    monkeypatch.setattr(
        settings, "database_url", "postgresql+asyncpg://u:p@h:5432/db"
    )
    # A split topology must carry the shared internal token (AUTH-3); the suite
    # secret is already non-default via conftest, so this is the last gate.
    monkeypatch.setattr(settings, "internal_api_token", "shared-worker-secret")
    worker_main._validate()  # must not raise


def test_worker_main_validation_rejects_blank_internal_token(monkeypatch):
    """AUTH-3: a worker in a split topology refuses to boot without the shared
    internal token — otherwise /internal/* would accept unauthenticated calls."""
    import pytest

    from app import worker_main
    from app.config import settings

    monkeypatch.setattr(settings, "dispatch_role", "worker")
    monkeypatch.setattr(settings, "queue_backend", "redis")
    monkeypatch.setattr(
        settings, "database_url", "postgresql+asyncpg://u:p@h:5432/db"
    )
    monkeypatch.setattr(settings, "internal_api_token", "")
    with pytest.raises(SystemExit) as exc:
        worker_main._validate()
    assert "INTERNAL_API_TOKEN" in str(exc.value)
