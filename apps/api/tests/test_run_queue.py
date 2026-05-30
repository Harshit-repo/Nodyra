"""Durable run-queue tests.

Task 4 covers the ``run_queue`` table/model. Later tasks (5/5b/6) extend this
file with queue-service and dead-letter behaviour.

These tests exercise the ORM model directly, so they spin up their own
temporary SQLite engine/session rather than going through the app ``client``
fixture.
"""

import os
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import models  # noqa: F401 - registers ORM models on Base.metadata
from app.db import Base
from app.models import RunQueueEntry


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    db_path = handle.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()
    try:
        os.unlink(db_path)
    except OSError:
        pass


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
        await session.scalars(
            select(RunQueueEntry).where(RunQueueEntry.status == "queued")
        )
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
async def test_complete_marks_completed(session) -> None:
    from app.services import queue

    session.add(RunQueueEntry(run_id="r1", workflow_id="wf"))
    await session.commit()
    await queue.lease(session, worker_id="w1")
    await session.commit()

    await queue.complete(session, run_id="r1")
    await session.commit()

    entry = await session.scalar(
        select(RunQueueEntry).where(RunQueueEntry.run_id == "r1")
    )
    assert entry.status == "completed"


@pytest.mark.asyncio
async def test_fail_retryable_requeues_with_backoff(session) -> None:
    from app.services import queue

    session.add(RunQueueEntry(run_id="r1", workflow_id="wf", max_attempts=3))
    await session.commit()
    await queue.lease(session, worker_id="w1")
    await session.commit()

    await queue.fail(session, run_id="r1", retryable=True, error="transient")
    await session.commit()

    entry = await session.scalar(
        select(RunQueueEntry).where(RunQueueEntry.run_id == "r1")
    )
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

    entry = await session.scalar(
        select(RunQueueEntry).where(RunQueueEntry.run_id == "r1")
    )
    assert entry.status == "failed"
    assert entry.last_error == "fatal"


@pytest.mark.asyncio
async def test_cancel_marks_cancelled(session) -> None:
    from app.services import queue

    session.add(RunQueueEntry(run_id="r1", workflow_id="wf"))
    await session.commit()

    await queue.cancel(session, run_id="r1")
    await session.commit()

    entry = await session.scalar(
        select(RunQueueEntry).where(RunQueueEntry.run_id == "r1")
    )
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
    entry = await session.scalar(
        select(RunQueueEntry).where(RunQueueEntry.run_id == "r1")
    )
    assert entry.status == "queued"
    assert entry.leased_by is None


@pytest.mark.asyncio
async def test_stats_reports_counts_and_oldest(session) -> None:
    from app.services import queue

    old = datetime.now(UTC) - timedelta(seconds=30)
    session.add_all(
        [
            RunQueueEntry(
                run_id="q1", workflow_id="wf", status="queued", available_at=old
            ),
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

    entry = await session.scalar(
        select(RunQueueEntry).where(RunQueueEntry.run_id == "r1")
    )
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

    entry = await session.scalar(
        select(RunQueueEntry).where(RunQueueEntry.run_id == "r1")
    )
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
        await queue.enqueue(session, run_id='r-cfg', workflow_id='wf')
        before = datetime.now(UTC)
        entry = await queue.lease(session, worker_id='w1')
        assert entry is not None
        assert entry.lease_expires_at is not None
        delta = (entry.lease_expires_at.replace(tzinfo=UTC) if entry.lease_expires_at.tzinfo is None else entry.lease_expires_at) - before
        assert 5 <= delta.total_seconds() <= 9
    finally:
        app_settings.queue_lease_seconds = original
