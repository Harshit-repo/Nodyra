"""Durable run-queue tests.

Task 4 covers the ``run_queue`` table/model. Later tasks (5/5b/6) extend this
file with queue-service and dead-letter behaviour.

These tests exercise the ORM model directly, so they spin up their own engine
and sessionmaker rather than going through the app ``client`` fixture. Local
runs use temporary SQLite files under the repo's ignored ``.tmp`` directory;
the Postgres CI lane sets ``NODYRA_TEST_DATABASE_URL`` so the same file also
exercises the real row-locking path.
"""

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import models  # noqa: F401 - registers ORM models on Base.metadata
from app.db import Base
from app.models import RunQueueEntry

TEST_DATABASE_URL = os.environ.get("NODYRA_TEST_DATABASE_URL")
SQLITE_TMP_DIR = Path(__file__).resolve().parents[3] / ".tmp" / "pytest-sqlite"


@pytest_asyncio.fixture
async def session_maker() -> AsyncIterator[async_sessionmaker]:
    if TEST_DATABASE_URL:
        db_path = None
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)

        # This module is a queue-unit/locking suite and deliberately uses
        # synthetic run/workflow IDs. Disable only FK triggers on its isolated
        # Postgres connections so tests reach the queue behavior under test;
        # unique indexes, row locks, and SKIP LOCKED remain active. Migration
        # drift and the separate full-API Postgres lane validate real FKs.
        @event.listens_for(engine.sync_engine, "connect")
        def _isolate_queue_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("SET session_replication_role = replica")
            finally:
                cursor.close()
    else:
        SQLITE_TMP_DIR.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            suffix=".db",
            dir=SQLITE_TMP_DIR,
            delete=False,
        )
        handle.close()
        db_path = Path(handle.name)
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        yield maker
    finally:
        await engine.dispose()
        if db_path:
            try:
                db_path.unlink()
            except OSError:
                pass


@pytest_asyncio.fixture
async def session(session_maker: async_sessionmaker) -> AsyncIterator:
    async with session_maker() as s:
        yield s


@pytest.mark.asyncio
async def test_run_queue_entry_defaults(session) -> None:
    entry = RunQueueEntry(run_id="run-1", workflow_id="wf-1")
    session.add(entry)
    await session.commit()
    await session.refresh(entry)

    assert entry.id  # auto-assigned uuid hex
    assert entry.status == "queued"
    assert entry.priority == 0
    assert entry.attempts == 0
    assert entry.max_attempts == 3
    assert entry.leased_by is None
    assert entry.lease_expires_at is None
    assert entry.available_at is not None
    assert entry.created_at is not None
    assert entry.updated_at is not None


@pytest.mark.asyncio
async def test_run_queue_entry_full_fieldset(session) -> None:
    now = datetime.now(UTC)
    entry = RunQueueEntry(
        run_id="run-2",
        workflow_id="wf-2",
        environment_id="env-2",
        runner_pool_id="pool-2",
        status="leased",
        priority=5,
        queue_reason="global_concurrency_limit",
        attempts=1,
        max_attempts=4,
        available_at=now,
        leased_by="worker-a",
        lease_expires_at=now + timedelta(seconds=30),
        last_error="boom",
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)

    assert entry.environment_id == "env-2"
    assert entry.runner_pool_id == "pool-2"
    assert entry.status == "leased"
    assert entry.priority == 5
    assert entry.queue_reason == "global_concurrency_limit"
    assert entry.attempts == 1
    assert entry.max_attempts == 4
    assert entry.leased_by == "worker-a"
    assert entry.last_error == "boom"


@pytest.mark.asyncio
async def test_run_queue_entry_queryable_by_status(session) -> None:
    session.add_all(
        [
            RunQueueEntry(run_id="r-a", workflow_id="wf", status="queued"),
            RunQueueEntry(run_id="r-b", workflow_id="wf", status="queued"),
            RunQueueEntry(run_id="r-c", workflow_id="wf", status="dead_lettered"),
        ]
    )
    await session.commit()

    queued = (
        await session.scalars(select(RunQueueEntry).where(RunQueueEntry.status == "queued"))
    ).all()
    assert len(queued) == 2


@pytest.mark.asyncio
async def test_run_queue_entry_one_active_per_run(session) -> None:
    """A run should not be enqueued twice while still active. The schema
    enforces uniqueness of run_id so the queue service can rely on it."""
    session.add(RunQueueEntry(run_id="dup", workflow_id="wf"))
    await session.commit()

    session.add(RunQueueEntry(run_id="dup", workflow_id="wf"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    rows = (await session.scalars(select(RunQueueEntry))).all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Task 5: DB-backed queue service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_creates_queued_entry(session) -> None:
    from app.services import queue

    entry = await queue.enqueue(
        session, run_id="r1", workflow_id="wf1", reason="global_concurrency_limit"
    )
    await session.commit()

    assert entry.status == "queued"
    assert entry.queue_reason == "global_concurrency_limit"
    assert entry.attempts == 0


@pytest.mark.asyncio
async def test_enqueue_persists_required_labels(session) -> None:
    from app.services import queue

    entry = await queue.enqueue(
        session,
        run_id="r-label",
        workflow_id="wf-label",
        required_labels={" gpu ": " a100 ", " bare ": True, " off ": False, " ": "drop"},
    )
    await session.commit()

    assert entry.required_labels == {"gpu": "a100", "bare": "true", "off": "false"}


@pytest.mark.asyncio
async def test_notify_queue_workers_sets_local_wakeup_when_redis_publish_fails(
    monkeypatch,
) -> None:
    from app import redis_client as redis_module
    from app.services import queue

    class BrokenRedis:
        async def publish(self, _channel: str, _message: str) -> None:
            raise RuntimeError("redis down")

    queue._wakeup = None
    monkeypatch.setattr(redis_module, "redis_client", BrokenRedis())

    await queue.notify_queue_workers()

    wakeup = queue._get_wakeup()
    assert wakeup.is_set()
    wakeup.clear()


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_per_run(session) -> None:
    from app.services import queue

    first = await queue.enqueue(session, run_id="r1", workflow_id="wf1")
    await session.commit()
    second = await queue.enqueue(session, run_id="r1", workflow_id="wf1")
    await session.commit()

    assert first.id == second.id
    rows = (await session.scalars(select(RunQueueEntry))).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_enqueue_terminal_entry_accepts_replay_seed_override(session) -> None:
    from app.services import queue

    existing = RunQueueEntry(
        run_id="r-terminal",
        workflow_id="wf-old",
        status="completed",
        replay_seed={"cache": {"old": {"out": 1}}},
    )
    session.add(existing)
    await session.commit()

    revived = await queue.enqueue(
        session,
        run_id="r-terminal",
        workflow_id="wf-new",
        priority=4,
        replay_seed={"cache": {"new": {"out": 2}}},
    )
    await session.commit()

    assert revived.id == existing.id
    assert revived.status == "queued"
    assert revived.priority == 4
    assert revived.replay_seed == {"cache": {"new": {"out": 2}}}


@pytest.mark.asyncio
async def test_lease_picks_oldest_available_and_marks_leased(session) -> None:
    from app.services import queue

    older = datetime.now(UTC) - timedelta(seconds=10)
    newer = datetime.now(UTC)
    session.add_all(
        [
            RunQueueEntry(run_id="old", workflow_id="wf", available_at=older),
            RunQueueEntry(run_id="new", workflow_id="wf", available_at=newer),
        ]
    )
    await session.commit()

    leased = await queue.lease(session, worker_id="w1", lease_seconds=30)
    await session.commit()

    assert leased is not None
    assert leased.run_id == "old"
    assert leased.status == "leased"
    assert leased.leased_by == "w1"
    assert leased.lease_expires_at is not None
    assert leased.attempts == 1


@pytest.mark.asyncio
async def test_lease_respects_priority(session) -> None:
    from app.services import queue

    now = datetime.now(UTC)
    session.add_all(
        [
            RunQueueEntry(run_id="lo", workflow_id="wf", priority=0, available_at=now),
            RunQueueEntry(run_id="hi", workflow_id="wf", priority=9, available_at=now),
        ]
    )
    await session.commit()

    leased = await queue.lease(session, worker_id="w1")
    await session.commit()
    assert leased is not None and leased.run_id == "hi"


@pytest.mark.asyncio
async def test_lease_skips_not_yet_available(session) -> None:
    from app.services import queue

    future = datetime.now(UTC) + timedelta(seconds=120)
    session.add(RunQueueEntry(run_id="later", workflow_id="wf", available_at=future))
    await session.commit()

    leased = await queue.lease(session, worker_id="w1")
    await session.commit()
    assert leased is None


@pytest.mark.asyncio
async def test_heartbeat_extends_lease(session) -> None:
    from app.services import queue

    session.add(RunQueueEntry(run_id="r1", workflow_id="wf"))
    await session.commit()
    leased = await queue.lease(session, worker_id="w1", lease_seconds=30)
    await session.commit()
    first_expiry = leased.lease_expires_at
    if first_expiry.tzinfo is None:
        first_expiry = first_expiry.replace(tzinfo=UTC)

    ok = await queue.heartbeat(session, run_id="r1", lease_seconds=60)
    await session.commit()
    await session.refresh(leased)
    new_expiry = leased.lease_expires_at
    if new_expiry.tzinfo is None:
        new_expiry = new_expiry.replace(tzinfo=UTC)

    assert ok is True
    assert new_expiry > first_expiry


@pytest.mark.asyncio
async def test_released_attempt_token_cannot_mutate_replacement_lease(session) -> None:
    """A worker from an expired lease is fenced after another worker leases it."""
    from app.services import queue

    start = datetime.now(UTC)
    # available_at defaults to the *database's* now(), which lands after the
    # start captured above, so the available_at <= now predicate matches
    # nothing and lease() returns None. SQLite hides this because
    # CURRENT_TIMESTAMP truncates to whole seconds; PostgreSQL does not. Pin it,
    # as every other time-sensitive test in this file does.
    session.add(
        RunQueueEntry(
            run_id="fenced", workflow_id="wf", max_attempts=3, available_at=start
        )
    )
    await session.commit()

    first = await queue.lease(
        session, worker_id="worker-a", lease_seconds=3, now=start
    )
    await session.commit()
    assert first is not None and first.lease_token
    stale_token = first.lease_token

    await queue.requeue_expired_leases(
        session, now=start + timedelta(seconds=4)
    )
    await session.commit()
    second = await queue.lease(
        session,
        worker_id="worker-b",
        lease_seconds=30,
        now=start + timedelta(seconds=5),
    )
    await session.commit()
    assert second is not None and second.lease_token
    assert second.lease_token != stale_token
    current_token = second.lease_token

    assert (
        await queue.heartbeat(
            session, run_id="fenced", lease_token=stale_token
        )
        is False
    )
    assert (
        await queue.complete(session, run_id="fenced", lease_token=stale_token)
        is False
    )
    assert (
        await queue.fail(
            session,
            run_id="fenced",
            lease_token=stale_token,
            retryable=False,
            error="stale",
        )
        is None
    )
    await session.refresh(second)
    assert second.status == "leased"
    assert second.lease_token == current_token

    assert (
        await queue.mark_running(
            session, run_id="fenced", lease_token=current_token
        )
        is True
    )
    assert (
        await queue.complete(
            session, run_id="fenced", lease_token=current_token
        )
        is True
    )
    await session.commit()
    await session.refresh(second)
    assert second.status == "completed"
    assert second.lease_token is None


@pytest.mark.asyncio
async def test_lease_provider_filter_separates_agent_and_local(session) -> None:
    """A1: the dispatch-loop provider filter keeps the WS-terminating control
    replica ({agent, kubernetes}) and the worker ({local, docker}) from
    stealing each other's entries — so agent runs never starve behind a worker
    that can't host their WebSocket, and local runs never block on control."""
    from app.models import RunnerPool
    from app.services import queue

    agent_pool = RunnerPool(name="agents", provider="agent")
    docker_pool = RunnerPool(name="dock", provider="docker")
    session.add_all([agent_pool, docker_pool])
    await session.flush()

    now = datetime.now(UTC)
    session.add_all(
        [
            RunQueueEntry(run_id="local", workflow_id="wf", runner_pool_id=None, available_at=now),
            RunQueueEntry(
                run_id="agent", workflow_id="wf", runner_pool_id=agent_pool.id, available_at=now
            ),
            RunQueueEntry(
                run_id="docker", workflow_id="wf", runner_pool_id=docker_pool.id, available_at=now
            ),
        ]
    )
    await session.commit()

    control_providers = frozenset({"agent", "kubernetes"})
    worker_providers = frozenset({"local", "docker"})

    # control leases only the agent entry...
    leased = await queue.lease(session, worker_id="control", providers=control_providers)
    await session.commit()
    assert leased is not None and leased.run_id == "agent"

    # ...and cannot touch the remaining local/docker entries.
    assert await queue.lease(session, worker_id="control", providers=control_providers) is None

    # the worker drains local + docker, never the agent entry.
    drained = set()
    for _ in range(2):
        got = await queue.lease(session, worker_id="worker", providers=worker_providers)
        await session.commit()
        assert got is not None
        drained.add(got.run_id)
    assert drained == {"local", "docker"}


@pytest.mark.asyncio
async def test_lease_filters_by_worker_labels(session) -> None:
    from app.services import queue

    # Seed both entries firmly in the past so eligibility never races the
    # wall clock: ``lease()`` computes its own ``now``, and a sub-millisecond
    # commit could otherwise leave the "cpu" entry (previously ``now + 1ms``)
    # not-yet-available — the labeled "gpu" entry is filtered, lease returns
    # None, and the test flakes (observed in full-suite runs). The 1ms
    # stagger keeps the intended FIFO order: "gpu" is older.
    now = datetime.now(UTC) - timedelta(seconds=5)
    session.add_all(
        [
            RunQueueEntry(
                run_id="gpu",
                workflow_id="wf",
                required_labels={"gpu": "a100"},
                available_at=now,
            ),
            RunQueueEntry(
                run_id="cpu",
                workflow_id="wf",
                available_at=now + timedelta(milliseconds=1),
            ),
        ]
    )
    await session.commit()

    leased = await queue.lease(session, worker_id="cpu-worker")
    await session.commit()
    assert leased is not None and leased.run_id == "cpu"

    leased = await queue.lease(
        session,
        worker_id="gpu-worker",
        worker_labels={"gpu": "a100"},
    )
    await session.commit()
    assert leased is not None and leased.run_id == "gpu"


@pytest.mark.asyncio
async def test_lease_returns_none_when_required_labels_unmatched(session) -> None:
    from app.services import queue

    session.add(
        RunQueueEntry(
            run_id="gpu",
            workflow_id="wf",
            required_labels={"gpu": "a100"},
        )
    )
    await session.commit()

    assert await queue.lease(session, worker_id="cpu-worker") is None
    assert (
        await queue.lease(
            session,
            worker_id="wrong-gpu-worker",
            worker_labels={"gpu": "l4"},
        )
        is None
    )


@pytest.mark.asyncio
async def test_lease_uses_multi_tenant_fair_order_and_marks_capped_orgs(
    session,
    monkeypatch,
) -> None:
    from app.config import settings
    from app.services import live_settings, org_limits, queue

    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    live_settings.invalidate_live_settings_cache()
    org_limits.invalidate_limits_cache()
    monkeypatch.setattr(
        live_settings,
        "SessionLocal",
        lambda: pytest.fail("queue leasing opened a second database session"),
    )
    old = datetime.now(UTC) - timedelta(seconds=5)
    session.add(models.OrgSettings(org_id="org-c", max_concurrent_runs=1))
    session.add_all(
        [
            RunQueueEntry(run_id="a-running", workflow_id="wf", org_id="org-a", status="running"),
            RunQueueEntry(run_id="a-queued", workflow_id="wf", org_id="org-a", available_at=old),
            RunQueueEntry(
                run_id="b-queued",
                workflow_id="wf",
                org_id="org-b",
                available_at=old + timedelta(milliseconds=1),
            ),
            RunQueueEntry(run_id="c-running", workflow_id="wf", org_id="org-c", status="running"),
            RunQueueEntry(run_id="c-queued", workflow_id="wf", org_id="org-c", available_at=old),
        ]
    )
    await session.commit()

    leased = await queue.lease(session, worker_id="w1")
    capped = await session.scalar(
        select(RunQueueEntry)
        .where(RunQueueEntry.run_id == "c-queued")
        .execution_options(skip_org_filter=True)
    )

    assert leased is not None
    assert leased.run_id == "b-queued"
    assert capped.queue_reason == "org_quota_exceeded"
    live_settings.invalidate_live_settings_cache()
    org_limits.invalidate_limits_cache()


@pytest.mark.asyncio
async def test_complete_marks_completed(session) -> None:
    from app.services import queue

    session.add(RunQueueEntry(run_id="r1", workflow_id="wf"))
    await session.commit()
    await queue.lease(session, worker_id="w1")
    await session.commit()

    await queue.complete(session, run_id="r1")
    await session.commit()

    entry = await session.scalar(select(RunQueueEntry).where(RunQueueEntry.run_id == "r1"))
    assert entry.status == "completed"


@pytest.mark.asyncio
async def test_missing_entry_transitions_return_false(session) -> None:
    from app.services import queue

    assert await queue.mark_running(session, run_id="missing") is False
    assert await queue.heartbeat(session, run_id="missing") is False
    assert await queue.complete(session, run_id="missing") is False
    assert await queue.wait_for_approval(session, run_id="missing") is False
    assert await queue.resume_waiting(session, run_id="missing", replay_seed={}) is None
    assert await queue.cancel(session, run_id="missing") is False


@pytest.mark.asyncio
async def test_wait_for_approval_and_resume_waiting(session) -> None:
    from app.services import queue

    expires = datetime.now(UTC) + timedelta(seconds=30)
    session.add(
        RunQueueEntry(
            run_id="approval-run",
            workflow_id="wf",
            status="leased",
            leased_by="w1",
            lease_expires_at=expires,
        )
    )
    await session.commit()

    assert await queue.wait_for_approval(session, run_id="approval-run") is True
    waiting = await session.scalar(
        select(RunQueueEntry).where(RunQueueEntry.run_id == "approval-run")
    )
    assert waiting.status == "waiting"
    assert waiting.queue_reason == "agent_approval"
    assert waiting.leased_by is None
    assert waiting.lease_expires_at is None

    moment = datetime.now(UTC)
    resumed = await queue.resume_waiting(
        session,
        run_id="approval-run",
        replay_seed={"targets": ["node-1"]},
        now=moment,
    )

    assert resumed is not None
    assert resumed.status == "queued"
    assert resumed.queue_reason == "approval_resume"
    assert resumed.available_at == moment
    assert resumed.replay_seed == {"targets": ["node-1"]}


@pytest.mark.asyncio
async def test_fail_retryable_requeues_with_backoff(session) -> None:
    from app.services import queue

    session.add(RunQueueEntry(run_id="r1", workflow_id="wf", max_attempts=3))
    await session.commit()
    await queue.lease(session, worker_id="w1")
    await session.commit()

    await queue.fail(session, run_id="r1", retryable=True, error="transient")
    await session.commit()

    entry = await session.scalar(select(RunQueueEntry).where(RunQueueEntry.run_id == "r1"))
    assert entry.status == "queued"
    assert entry.last_error == "transient"
    assert entry.leased_by is None
    # backoff pushes availability into the future
    available = entry.available_at
    if available.tzinfo is None:
        available = available.replace(tzinfo=UTC)
    assert available > datetime.now(UTC)


@pytest.mark.asyncio
async def test_fail_non_retryable_marks_failed(session) -> None:
    from app.services import queue

    session.add(RunQueueEntry(run_id="r1", workflow_id="wf"))
    await session.commit()
    await queue.lease(session, worker_id="w1")
    await session.commit()

    await queue.fail(session, run_id="r1", retryable=False, error="fatal")
    await session.commit()

    entry = await session.scalar(select(RunQueueEntry).where(RunQueueEntry.run_id == "r1"))
    assert entry.status == "failed"
    assert entry.last_error == "fatal"


@pytest.mark.asyncio
async def test_cancel_marks_cancelled(session) -> None:
    from app.services import queue

    session.add(RunQueueEntry(run_id="r1", workflow_id="wf"))
    await session.commit()

    await queue.cancel(session, run_id="r1")
    await session.commit()

    entry = await session.scalar(select(RunQueueEntry).where(RunQueueEntry.run_id == "r1"))
    assert entry.status == "cancelled"


@pytest.mark.asyncio
async def test_requeue_expired_leases(session) -> None:
    from app.services import queue

    past = datetime.now(UTC) - timedelta(seconds=5)
    session.add(
        RunQueueEntry(
            run_id="r1",
            workflow_id="wf",
            status="leased",
            leased_by="dead-worker",
            lease_expires_at=past,
            attempts=1,
            max_attempts=3,
        )
    )
    await session.commit()

    count = await queue.requeue_expired_leases(session)
    await session.commit()

    assert count == 1
    entry = await session.scalar(select(RunQueueEntry).where(RunQueueEntry.run_id == "r1"))
    assert entry.status == "queued"
    assert entry.leased_by is None


def test_requeue_expired_leases_locks_rows_on_postgres() -> None:
    from app.services.queue import _expired_lease_stmt

    stmt = _expired_lease_stmt(datetime.now(UTC))
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql


@pytest.mark.asyncio
async def test_stats_reports_counts_and_oldest(session) -> None:
    from app.services import queue

    old = datetime.now(UTC) - timedelta(seconds=30)
    session.add_all(
        [
            RunQueueEntry(run_id="q1", workflow_id="wf", status="queued", available_at=old),
            RunQueueEntry(run_id="q2", workflow_id="wf", status="queued"),
            RunQueueEntry(run_id="l1", workflow_id="wf", status="leased"),
            RunQueueEntry(run_id="f1", workflow_id="wf", status="failed"),
        ]
    )
    await session.commit()

    stats = await queue.stats(session)

    assert stats["queued"] == 2
    assert stats["leased"] == 1
    assert stats["failed"] == 1
    assert stats["oldest_queued_age_seconds"] is not None
    assert stats["oldest_queued_age_seconds"] >= 29


@pytest.mark.asyncio
async def test_stats_breaks_down_by_org_when_multi_tenancy_enabled(
    session,
    monkeypatch,
) -> None:
    from app.config import settings
    from app.services import queue

    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    session.add_all(
        [
            RunQueueEntry(run_id="org-a-leased", workflow_id="wf", org_id="org-a", status="leased"),
            RunQueueEntry(
                run_id="org-a-parked",
                workflow_id="wf",
                org_id="org-a",
                status="queued",
                queue_reason="org_quota_exceeded",
            ),
            RunQueueEntry(run_id="org-b-running", workflow_id="wf", org_id="org-b", status="running"),
        ]
    )
    await session.commit()

    stats = await queue.stats(session)

    assert stats["by_org"]["org-a"]["leased"] == 1
    assert stats["by_org"]["org-a"]["queued"] == 1
    assert stats["by_org"]["org-a"]["quota_parked"] == 1
    assert stats["by_org"]["org-b"]["running"] == 1


@pytest.mark.asyncio
async def test_fail_retryable_dead_letters_when_attempts_exhausted(session) -> None:
    """Retryable failure with no attempts remaining is dead-lettered, not
    requeued — distinguishes "retry budget exhausted" from "non-retryable"."""
    from app.services import queue

    session.add(RunQueueEntry(run_id="r1", workflow_id="wf", max_attempts=1))
    await session.commit()
    # Single allowed attempt, then a retryable failure → dead_lettered.
    await queue.lease(session, worker_id="w1")
    await session.commit()
    await queue.fail(session, run_id="r1", retryable=True, error="boom")
    await session.commit()

    entry = await session.scalar(select(RunQueueEntry).where(RunQueueEntry.run_id == "r1"))
    assert entry.status == "dead_lettered"
    assert entry.last_error == "boom"


@pytest.mark.asyncio
async def test_fail_records_attempts_log(session) -> None:
    from app.services import queue

    session.add(RunQueueEntry(run_id="r1", workflow_id="wf", max_attempts=2))
    await session.commit()
    await queue.lease(session, worker_id="w1")
    await session.commit()
    await queue.fail(session, run_id="r1", retryable=True, error="boom")
    await session.commit()

    entry = await session.scalar(select(RunQueueEntry).where(RunQueueEntry.run_id == "r1"))
    assert len(entry.attempts_log) == 1
    record = entry.attempts_log[0]
    assert record["event"] == "retry_scheduled"
    assert record["error"] == "boom"
    assert record["attempt"] == 1
    assert "ts" in record


@pytest.mark.asyncio
async def test_replay_resets_terminal_entry(session) -> None:
    from app.services import queue

    session.add(
        RunQueueEntry(
            run_id="r1",
            workflow_id="wf",
            status="dead_lettered",
            attempts=3,
            max_attempts=3,
            last_error="exhausted",
        )
    )
    await session.commit()

    replayed = await queue.replay(session, run_id="r1")
    await session.commit()

    assert replayed is not None
    assert replayed.status == "queued"
    assert replayed.attempts == 0
    assert replayed.last_error is None
    # Replay marker is preserved so operators can see the chain.
    events = [r["event"] for r in replayed.attempts_log]
    assert "replay" in events


@pytest.mark.asyncio
async def test_replay_replaces_seed_with_cache_targets(session) -> None:
    from app.services import queue

    session.add(
        RunQueueEntry(
            run_id="r1",
            workflow_id="wf",
            status="failed",
            attempts=2,
            last_error="boom",
            replay_seed={"cache": {"old": {"out": 1}}},
        )
    )
    await session.commit()

    replayed = await queue.replay(
        session,
        run_id="r1",
        cache={"node-1": {"out": 2}},
        targets=["node-2"],
    )
    await session.commit()

    assert replayed is not None
    assert replayed.replay_seed == {
        "cache": {"node-1": {"out": 2}},
        "targets": ["node-2"],
    }


@pytest.mark.asyncio
async def test_replay_rejects_active_entry(session) -> None:
    from app.services import queue

    session.add(RunQueueEntry(run_id="r1", workflow_id="wf", status="running"))
    await session.commit()

    result = await queue.replay(session, run_id="r1")
    assert result is None


async def test_lease_uses_config_lease_seconds(session) -> None:
    from app.config import settings as app_settings
    from app.services import queue

    original = app_settings.queue_lease_seconds
    app_settings.queue_lease_seconds = 7
    try:
        await queue.enqueue(session, run_id="r-cfg", workflow_id="wf")
        before = datetime.now(UTC)
        entry = await queue.lease(session, worker_id="w1")
        assert entry is not None
        assert entry.lease_expires_at is not None
        expires = entry.lease_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        delta = expires - before
        assert 5 <= delta.total_seconds() <= 9
    finally:
        app_settings.queue_lease_seconds = original


@pytest.mark.asyncio
async def test_lease_filters_by_provider(session) -> None:
    """A worker may only lease local + docker entries; agent/k8s entries need
    the WS-holding API process (program A1)."""
    from app.models import RunnerPool
    from app.services import queue as q

    agent_pool = RunnerPool(name="agents", provider="agent")
    docker_pool = RunnerPool(name="dockers", provider="docker")
    session.add_all([agent_pool, docker_pool])
    await session.flush()

    await q.enqueue(session, run_id="r-local", workflow_id="w1")
    await q.enqueue(session, run_id="r-agent", workflow_id="w1", runner_pool_id=agent_pool.id)
    await q.enqueue(session, run_id="r-docker", workflow_id="w1", runner_pool_id=docker_pool.id)
    await session.commit()

    worker_caps = frozenset({"local", "docker"})
    leased = set()
    while True:
        entry = await q.lease(session, worker_id="w", providers=worker_caps)
        if entry is None:
            break
        leased.add(entry.run_id)
    assert leased == {"r-local", "r-docker"}

    # unrestricted lease (inline role) still gets the agent entry
    entry = await q.lease(session, worker_id="w")
    assert entry is not None and entry.run_id == "r-agent"


@pytest.mark.asyncio
async def test_requeue_expired_lease_resets_running_run(session) -> None:
    """A lost worker leaves Run.status='running'; requeue must flip it back to
    'queued' or _execute_queued_entry will refuse to re-dispatch (A1)."""
    from app.models import Run
    from app.services import queue as q

    run = Run(
        workflow_id="wf-lost",
        workflow_version=1,
        mode="production",
        trigger_type="schedule",
        status="running",
    )
    session.add(run)
    await session.flush()

    entry = await q.enqueue(session, run_id=run.id, workflow_id="wf-lost")
    moment = datetime.now(UTC)
    leased = await q.lease(session, worker_id="lost-worker", now=moment)
    assert leased is not None and leased.run_id == run.id
    leased.status = "running"
    await session.commit()

    acted = await q.requeue_expired_leases(session, now=moment + timedelta(seconds=9999))
    await session.commit()
    assert acted == 1
    await session.refresh(entry)
    await session.refresh(run)
    assert entry.status == "queued"
    assert run.status == "queued"
    assert run.finished_at is None


@pytest.mark.asyncio
async def test_cancel_reconcile_cancels_local_task_for_cancelled_entry(session) -> None:
    """An API replica can only flip the queue entry to 'cancelled'; the worker
    holding the executing task must observe that and cancel locally (A1)."""
    import asyncio

    from app.services import queue as q

    await q.enqueue(session, run_id="r-cancel", workflow_id="w1")
    await q.cancel(session, run_id="r-cancel")
    await session.commit()

    async def _hang():
        await asyncio.sleep(60)

    task = asyncio.ensure_future(_hang())
    active = {"r-cancel": task, "r-other": asyncio.ensure_future(_hang())}
    try:
        cancelled = await q._cancel_reconcile(session, active)
        assert cancelled == ["r-cancel"]
        await asyncio.sleep(0)
        assert task.cancelled()
        assert not active["r-other"].done()
    finally:
        for t in active.values():
            t.cancel()


@pytest.mark.asyncio
async def test_enqueue_persists_trace_context(session) -> None:
    from app.services import queue as run_queue

    entry = await run_queue.enqueue(
        session,
        run_id="run-tc",
        workflow_id="wf-1",
        trace_context={"traceparent": "00-aa-bb-01"},
    )
    await session.commit()
    await session.refresh(entry)
    assert entry.trace_context == {"traceparent": "00-aa-bb-01"}


@pytest.mark.asyncio
async def test_reenqueue_replaces_trace_context(session) -> None:
    from app.services import queue as run_queue

    entry = await run_queue.enqueue(
        session,
        run_id="run-tc2",
        workflow_id="wf-1",
        trace_context={"traceparent": "00-old-old-01"},
    )
    entry.status = "failed"  # terminal -> revival path
    await session.commit()
    revived = await run_queue.enqueue(
        session,
        run_id="run-tc2",
        workflow_id="wf-1",
        trace_context={"traceparent": "00-new-new-01"},
    )
    await session.commit()
    assert revived.trace_context == {"traceparent": "00-new-new-01"}


@pytest.mark.asyncio
async def test_lease_exclude_local_skips_local_head_of_queue(session) -> None:
    """Regression: a saturated local pool must not head-of-line block remote
    dispatches. With ``exclude_local=True`` the lease skips local entries —
    even older, otherwise-first ones — and returns the remote-pool entry."""
    from app.models import RunnerPool
    from app.services import queue as q

    docker_pool = RunnerPool(name="dockers", provider="docker")
    session.add(docker_pool)
    await session.flush()

    older = datetime.now(UTC) - timedelta(seconds=60)
    await q.enqueue(session, run_id="r-local-first", workflow_id="w1", available_at=older)
    await q.enqueue(
        session,
        run_id="r-docker-second",
        workflow_id="w1",
        runner_pool_id=docker_pool.id,
    )
    await session.commit()

    entry = await q.lease(session, worker_id="w", exclude_local=True)
    assert entry is not None and entry.run_id == "r-docker-second"

    # The local entry is untouched and still leaseable normally.
    entry = await q.lease(session, worker_id="w")
    assert entry is not None and entry.run_id == "r-local-first"


@pytest.mark.asyncio
async def test_lease_exclude_local_composes_with_provider_filter(session) -> None:
    """exclude_local + a provider capability set (worker role: local+docker)
    still leases the docker entry while dropping local rows."""
    from app.models import RunnerPool
    from app.services import queue as q

    docker_pool = RunnerPool(name="dockers", provider="docker")
    session.add(docker_pool)
    await session.flush()

    older = datetime.now(UTC) - timedelta(seconds=60)
    await q.enqueue(session, run_id="r-local", workflow_id="w1", available_at=older)
    await q.enqueue(session, run_id="r-docker", workflow_id="w1", runner_pool_id=docker_pool.id)
    await session.commit()

    entry = await q.lease(
        session,
        worker_id="w",
        providers=frozenset({"local", "docker"}),
        exclude_local=True,
    )
    assert entry is not None and entry.run_id == "r-docker"


async def _require_postgres(session_maker: async_sessionmaker) -> None:
    async with session_maker() as session:
        if session.bind is None or session.bind.dialect.name != "postgresql":
            pytest.skip("Postgres-only queue chaos coverage")


@pytest.mark.asyncio
async def test_postgres_two_worker_burst_has_no_duplicate_or_stuck_entries(
    session_maker: async_sessionmaker,
) -> None:
    """RQ-1: 50 queued entries drained by two workers without duplicates or
    leftover leased/running rows. This must run on Postgres so the real
    ``FOR UPDATE SKIP LOCKED`` lease path is exercised."""
    from app.services import queue as q

    await _require_postgres(session_maker)
    moment = datetime.now(UTC) - timedelta(seconds=1)
    async with session_maker() as session:
        for i in range(50):
            await q.enqueue(
                session,
                run_id=f"burst-{i:02d}",
                workflow_id="wf-burst",
                available_at=moment,
            )
        await session.commit()

    start = asyncio.Event()

    async def worker(worker_id: str) -> list[str]:
        leased: list[str] = []
        empty_polls = 0
        await start.wait()
        while empty_polls < 5:
            async with session_maker() as session:
                entry = await q.lease(session, worker_id=worker_id, lease_seconds=30)
                if entry is None:
                    await session.rollback()
                    empty_polls += 1
                    run_id = None
                else:
                    run_id = entry.run_id
                    leased.append(run_id)
                    empty_polls = 0
                    await session.commit()

            if run_id is None:
                await asyncio.sleep(0.005)
                continue

            # Leave a small interleaving window so the peer worker is also
            # exercising the lease path rather than this task draining serially.
            await asyncio.sleep(0.002)
            async with session_maker() as session:
                assert await q.complete(session, run_id=run_id) is True
                await session.commit()

        return leased

    tasks = [
        asyncio.create_task(worker("worker-a")),
        asyncio.create_task(worker("worker-b")),
    ]
    start.set()
    by_worker = await asyncio.gather(*tasks)
    all_run_ids = [run_id for part in by_worker for run_id in part]

    assert len(all_run_ids) == 50
    assert len(set(all_run_ids)) == 50
    assert all(by_worker), "both workers should participate in the burst"

    async with session_maker() as session:
        rows = (await session.scalars(select(RunQueueEntry))).all()
        stats = await q.stats(session)

    assert len(rows) == 50
    assert {row.status for row in rows} == {"completed"}
    assert {row.attempts for row in rows} == {1}
    assert stats["queued"] == 0
    assert stats["leased"] == 0
    assert stats["running"] == 0
    assert stats["completed"] == 50


@pytest.mark.asyncio
async def test_postgres_expired_running_lease_is_reclaimed_once(
    session_maker: async_sessionmaker,
) -> None:
    """RQ-1: a worker-lost running lease requeues exactly once and can be
    reclaimed by another worker without duplicating the queue row."""
    from app.models import Run
    from app.services import queue as q

    await _require_postgres(session_maker)
    moment = datetime.now(UTC)
    async with session_maker() as session:
        run = Run(
            workflow_id="wf-reclaim",
            workflow_version=1,
            mode="production",
            trigger_type="manual",
            status="queued",
        )
        session.add(run)
        await session.flush()
        run_id = run.id
        await q.enqueue(
            session,
            run_id=run_id,
            workflow_id="wf-reclaim",
            available_at=moment,
        )
        await session.commit()

    async with session_maker() as session:
        leased = await q.lease(
            session,
            worker_id="worker-a",
            lease_seconds=1,
            now=moment,
        )
        assert leased is not None and leased.run_id == run_id
        leased.status = "running"
        run = await session.get(Run, run_id)
        assert run is not None
        run.status = "running"
        await session.commit()

    async with session_maker() as session:
        acted = await q.requeue_expired_leases(
            session,
            now=moment + timedelta(seconds=2),
        )
        await session.commit()
    assert acted == 1

    async with session_maker() as session:
        entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
        )
        run = await session.get(Run, run_id)
        assert entry is not None
        assert run is not None
        assert entry.status == "queued"
        assert entry.leased_by is None
        assert entry.lease_expires_at is None
        assert entry.attempts == 1
        assert [record["event"] for record in entry.attempts_log] == ["lease_expired"]
        assert run.status == "queued"

    async with session_maker() as session:
        reclaimed = await q.lease(
            session,
            worker_id="worker-b",
            now=moment + timedelta(seconds=3),
        )
        assert reclaimed is not None and reclaimed.run_id == run_id
        assert reclaimed.attempts == 2
        await q.complete(session, run_id=run_id)
        await session.commit()

    async with session_maker() as session:
        rows = (
            await session.scalars(select(RunQueueEntry).where(RunQueueEntry.run_id == run_id))
        ).all()
    assert len(rows) == 1
    assert rows[0].status == "completed"


@pytest.mark.asyncio
async def test_postgres_dead_letter_replay_returns_to_queue(
    session_maker: async_sessionmaker,
) -> None:
    """RQ-1: retry exhaustion dead-letters the entry; replay resets attempts
    and preserves replay seed so the next lease is a fresh dispatch."""
    from app.services import queue as q

    await _require_postgres(session_maker)
    moment = datetime.now(UTC)
    async with session_maker() as session:
        await q.enqueue(
            session,
            run_id="dead-replay",
            workflow_id="wf-dead",
            max_attempts=2,
            available_at=moment,
        )
        await session.commit()

    for attempt in range(2):
        async with session_maker() as session:
            leased = await q.lease(
                session,
                worker_id=f"worker-{attempt}",
                now=moment + timedelta(seconds=attempt),
            )
            assert leased is not None and leased.run_id == "dead-replay"
            await session.commit()

        async with session_maker() as session:
            failed = await q.fail(
                session,
                run_id="dead-replay",
                retryable=True,
                error=f"boom-{attempt}",
                now=moment + timedelta(seconds=attempt),
            )
            assert failed is not None
            if attempt == 0:
                assert failed.status == "queued"
                # Avoid sleeping through retry backoff in a state-machine test.
                failed.available_at = moment + timedelta(milliseconds=1)
            else:
                assert failed.status == "dead_lettered"
            await session.commit()

    async with session_maker() as session:
        entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == "dead-replay")
        )
        assert entry is not None
        assert entry.status == "dead_lettered"
        assert entry.attempts == 2
        assert [record["event"] for record in entry.attempts_log] == [
            "retry_scheduled",
            "dead_lettered",
        ]

        replayed = await q.replay(
            session,
            run_id="dead-replay",
            cache={"upstream": {"ok": True}},
            targets=["node-a"],
            now=moment + timedelta(seconds=10),
        )
        assert replayed is not None
        assert replayed.status == "queued"
        assert replayed.attempts == 0
        assert replayed.replay_seed == {
            "cache": {"upstream": {"ok": True}},
            "targets": ["node-a"],
        }
        await session.commit()

    async with session_maker() as session:
        leased = await q.lease(
            session,
            worker_id="worker-replay",
            now=moment + timedelta(seconds=11),
        )
        assert leased is not None and leased.run_id == "dead-replay"
        assert leased.attempts == 1
        assert leased.replay_seed == {
            "cache": {"upstream": {"ok": True}},
            "targets": ["node-a"],
        }
        await q.complete(session, run_id="dead-replay")
        await session.commit()

    async with session_maker() as session:
        entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == "dead-replay")
        )
    assert entry is not None
    assert entry.status == "completed"
