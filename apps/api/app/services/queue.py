"""Durable, DB-backed run queue.

This is the canonical source of truth for *run scheduling and backpressure* in
production. It operates on :class:`~app.models.RunQueueEntry` rows and exposes a
small backend-agnostic interface so the run orchestrator never talks to a queue
implementation directly:

    enqueue / lease / heartbeat / complete / fail / cancel /
    requeue_expired_leases / stats

The DB backend is the correctness baseline. A Redis backend (config
``queue_backend=redis``) can later implement this same interface as an
optimisation; it is not a parallel dispatch path.

Every function takes an :class:`AsyncSession` and does **not** commit — the
caller owns the transaction boundary so queue mutations can compose atomically
with run-state changes. ``RunQueueEntry.status`` is the orchestration state and
is intentionally distinct from the user-facing ``Run.status`` (see the model
docstring); callers map between the two.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import RunQueueEntry

logger = logging.getLogger(__name__)

# Default lease duration. A worker must ``heartbeat`` within this window or the
# lease is considered lost and the entry is requeued by
# ``requeue_expired_leases``.
DEFAULT_LEASE_SECONDS = 30

# Retry backoff for retryable failures: ``base * 2 ** (attempts - 1)`` capped.
RETRY_BACKOFF_BASE_SECONDS = 5
RETRY_BACKOFF_MAX_SECONDS = 300

# Orchestration states that are still "live" (occupy the run's single queue slot).
ACTIVE_STATUSES = ("queued", "leased", "running")

# Terminal orchestration states. Task 6 adds ``dead_lettered`` handling on top.
TERMINAL_STATUSES = ("completed", "failed", "cancelled", "dead_lettered")


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _as_aware(value: datetime) -> datetime:
    """SQLite round-trips ``DateTime`` as naive; treat stored times as UTC so
    comparisons against timezone-aware ``now`` don't raise."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def _get(session: AsyncSession, run_id: str) -> RunQueueEntry | None:
    return await session.scalar(
        select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
    )


async def enqueue(
    session: AsyncSession,
    *,
    run_id: str,
    workflow_id: str,
    reason: str = "",
    priority: int = 0,
    environment_id: str | None = None,
    runner_pool_id: str | None = None,
    max_attempts: int = 3,
    available_at: datetime | None = None,
) -> RunQueueEntry:
    """Add a run to the queue, or reset an existing entry for the same run.

    Idempotent per run: a run has at most one queue entry (enforced by the
    unique ``run_id``). Re-enqueuing a run that still has a live entry returns
    that entry unchanged; re-enqueuing one with a terminal entry (replay/retry)
    revives the existing row rather than inserting a duplicate.
    """
    existing = await _get(session, run_id)
    if existing is not None:
        if existing.status in ACTIVE_STATUSES:
            return existing
        existing.status = "queued"
        existing.queue_reason = reason
        existing.priority = priority
        existing.attempts = 0
        existing.leased_by = None
        existing.lease_expires_at = None
        existing.last_error = None
        existing.available_at = available_at or _now(None)
        return existing

    entry = RunQueueEntry(
        run_id=run_id,
        workflow_id=workflow_id,
        environment_id=environment_id,
        runner_pool_id=runner_pool_id,
        status="queued",
        queue_reason=reason,
        priority=priority,
        max_attempts=max_attempts,
        available_at=available_at or _now(None),
    )
    session.add(entry)
    await session.flush()
    return entry


async def lease(
    session: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> RunQueueEntry | None:
    """Claim the next eligible queued entry for ``worker_id``.

    Eligible = ``status == "queued"`` and ``available_at <= now``. Ordered by
    priority (high first) then oldest-available (FIFO within a priority). The
    claimed entry is marked ``leased`` with a fresh lease expiry and its attempt
    count incremented. Returns the leased entry, or ``None`` when nothing is
    eligible.
    """
    moment = _now(now)
    stmt = (
        select(RunQueueEntry)
        .where(
            RunQueueEntry.status == "queued",
            RunQueueEntry.available_at <= moment,
        )
        .order_by(
            RunQueueEntry.priority.desc(),
            RunQueueEntry.available_at.asc(),
        )
        .limit(1)
    )
    # Postgres: lock the candidate row and skip ones already locked by a peer
    # worker so concurrent leases don't hand the same entry out twice. SQLite
    # has no row locking, so only request it where supported.
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)

    entry = await session.scalar(stmt)
    if entry is None:
        return None

    entry.status = "leased"
    entry.leased_by = worker_id
    entry.lease_expires_at = moment + timedelta(seconds=lease_seconds)
    entry.attempts += 1
    await session.flush()
    return entry


async def mark_running(session: AsyncSession, *, run_id: str) -> bool:
    """Transition a leased entry to ``running`` once execution actually starts."""
    entry = await _get(session, run_id)
    if entry is None or entry.status not in ("leased", "queued"):
        return False
    entry.status = "running"
    return True


async def heartbeat(
    session: AsyncSession,
    *,
    run_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> bool:
    """Extend the lease on an active entry. Returns ``False`` if there is no
    leasable entry (already completed/cancelled or missing)."""
    entry = await _get(session, run_id)
    if entry is None or entry.status not in ("leased", "running"):
        return False
    entry.lease_expires_at = _now(now) + timedelta(seconds=lease_seconds)
    return True


async def complete(session: AsyncSession, *, run_id: str) -> bool:
    """Mark a run's queue entry completed."""
    entry = await _get(session, run_id)
    if entry is None:
        return False
    entry.status = "completed"
    entry.leased_by = None
    entry.lease_expires_at = None
    return True


async def fail(
    session: AsyncSession,
    *,
    run_id: str,
    retryable: bool,
    error: str,
    now: datetime | None = None,
) -> RunQueueEntry | None:
    """Record a failed attempt.

    State machine:

    - ``retryable=True`` and attempts remain → requeue with exponential backoff.
    - ``retryable=True`` and attempts exhausted → ``dead_lettered`` (terminal).
    - ``retryable=False`` → ``failed`` (terminal; explicit non-retryable fault).

    Both terminal states are replayable via :func:`replay`. Every call appends
    to ``attempts_log`` so the ops UI can render retry history.
    """
    entry = await _get(session, run_id)
    if entry is None:
        return None

    moment = _now(now)
    entry.last_error = error
    entry.leased_by = None
    entry.lease_expires_at = None

    if retryable and entry.attempts < entry.max_attempts:
        backoff = min(
            RETRY_BACKOFF_BASE_SECONDS * (2 ** max(entry.attempts - 1, 0)),
            RETRY_BACKOFF_MAX_SECONDS,
        )
        entry.status = "queued"
        entry.available_at = moment + timedelta(seconds=backoff)
        event = "retry_scheduled"
    elif retryable:
        entry.status = "dead_lettered"
        event = "dead_lettered"
    else:
        entry.status = "failed"
        event = "failed"

    _append_attempt(entry, event=event, error=error, ts=moment)
    return entry


def _append_attempt(
    entry: RunQueueEntry,
    *,
    event: str,
    error: str | None,
    ts: datetime,
) -> None:
    """Append a record to ``attempts_log`` without mutating the existing list
    in place. JSON columns on SQLAlchemy don't detect in-place mutation, so we
    rebind the attribute to trigger the dirty-flag."""
    history = list(entry.attempts_log or [])
    history.append(
        {
            "attempt": entry.attempts,
            "event": event,
            "error": error,
            "ts": ts.isoformat(),
        }
    )
    entry.attempts_log = history


async def replay(
    session: AsyncSession,
    *,
    run_id: str,
    now: datetime | None = None,
    cache: dict | None = None,
    targets: list[str] | None = None,
) -> RunQueueEntry | None:
    """Reset a terminal queue entry to ``queued`` for another dispatch attempt.

    Only entries in ``failed``/``dead_lettered``/``cancelled`` are replayable.
    Attempts are reset to 0 (the new try is treated as fresh) and the history
    is preserved with a ``replay`` marker so operators can see the chain.

    When ``cache`` or ``targets`` is supplied (replay-from-failure path), they
    are persisted on ``entry.replay_seed`` so ``_execute_queued_entry`` can
    seed the engine. Passing neither clears any prior seed (whole-run replay).
    Returns ``None`` if there's no entry or it isn't in a terminal state.
    """
    entry = await _get(session, run_id)
    if entry is None or entry.status not in ("failed", "dead_lettered", "cancelled"):
        return None
    moment = _now(now)
    _append_attempt(entry, event="replay", error=None, ts=moment)
    entry.status = "queued"
    entry.attempts = 0
    entry.last_error = None
    entry.leased_by = None
    entry.lease_expires_at = None
    entry.available_at = moment
    if cache is not None or targets is not None:
        seed: dict = {}
        if cache is not None:
            seed["cache"] = cache
        if targets is not None:
            seed["targets"] = list(targets)
        entry.replay_seed = seed
    else:
        entry.replay_seed = None
    return entry


async def cancel(session: AsyncSession, *, run_id: str) -> bool:
    """Cancel a run's queue entry regardless of current state."""
    entry = await _get(session, run_id)
    if entry is None:
        return False
    entry.status = "cancelled"
    entry.leased_by = None
    entry.lease_expires_at = None
    return True


async def requeue_expired_leases(
    session: AsyncSession, *, now: datetime | None = None
) -> int:
    """Find leased entries whose lease has expired (worker presumed lost) and
    requeue them if attempts remain, otherwise fail them. Returns the number of
    entries acted on."""
    moment = _now(now)
    leased = (
        await session.scalars(
            select(RunQueueEntry).where(RunQueueEntry.status == "leased")
        )
    ).all()

    acted = 0
    for entry in leased:
        if entry.lease_expires_at is None:
            continue
        if _as_aware(entry.lease_expires_at) > moment:
            continue
        acted += 1
        entry.leased_by = None
        entry.lease_expires_at = None
        if entry.attempts < entry.max_attempts:
            entry.status = "queued"
            entry.available_at = moment
            _append_attempt(
                entry,
                event="lease_expired",
                error="lease expired (worker lost)",
                ts=moment,
            )
        else:
            entry.status = "dead_lettered"
            entry.last_error = entry.last_error or "lease expired (worker lost)"
            _append_attempt(
                entry,
                event="dead_lettered",
                error=entry.last_error,
                ts=moment,
            )
    return acted


async def stats(session: AsyncSession, *, now: datetime | None = None) -> dict:
    """Aggregate queue health for the ops/backpressure surface."""
    moment = _now(now)
    rows = (
        await session.execute(
            select(RunQueueEntry.status, func.count()).group_by(RunQueueEntry.status)
        )
    ).all()
    counts = {status: int(count) for status, count in rows}

    oldest = await session.scalar(
        select(func.min(RunQueueEntry.available_at)).where(
            RunQueueEntry.status == "queued"
        )
    )
    oldest_age = None
    if oldest is not None:
        oldest_age = max(0.0, (moment - _as_aware(oldest)).total_seconds())

    return {
        "queued": counts.get("queued", 0),
        "leased": counts.get("leased", 0),
        "running": counts.get("running", 0),
        "completed": counts.get("completed", 0),
        "failed": counts.get("failed", 0),
        "dead_lettered": counts.get("dead_lettered", 0),
        "cancelled": counts.get("cancelled", 0),
        "oldest_queued_age_seconds": oldest_age,
    }


# ---------------------------------------------------------------------------
# Background dispatch loop
# ---------------------------------------------------------------------------

# Poll interval for the dispatch loop. Kept short so the local-mode promise
# ("queued runs become live as soon as capacity frees") holds without operators
# tuning anything. Each tick is cheap: one indexed SELECT per
# requeue_expired_leases + lease pair.
DISPATCH_POLL_SECONDS = 1.0

# Cap how many entries one tick will dispatch so a backlog doesn't monopolise
# the loop and starve heartbeat/requeue work.
MAX_DISPATCHES_PER_TICK = 25

# How long to wait for an in-flight dispatch task to settle on shutdown.
DISPATCH_SHUTDOWN_TIMEOUT = 5.0


def _worker_id() -> str:
    """A stable-ish identifier for the current process so leases can be
    attributed to a specific replica when diagnosing lost workers."""
    return f"{socket.gethostname()}:{id(asyncio.get_event_loop())}"


async def run_queue_dispatch_loop() -> None:
    """Lease and dispatch queued runs on a tight poll.

    Replaces the legacy in-memory ``remote_dispatch.queue_dispatch_loop`` with
    a durable-queue-aware loop. Each tick:

    1. Requeues entries whose lease has expired (worker presumed lost) so
       another worker can pick them up.
    2. Leases up to ``MAX_DISPATCHES_PER_TICK`` eligible entries and dispatches
       each by calling back into ``runner._execute_queued_entry``.

    The actual dispatch runs as a background task so a slow run doesn't block
    the loop. The dispatch callback owns lifecycle transitions (running →
    completed/failed/cancelled) via the queue interface.
    """
    # Import here to avoid the runner ↔ queue circular import.
    from app.services.runner import _execute_queued_entry  # noqa: PLC0415

    worker = _worker_id()
    in_flight: set[asyncio.Task[None]] = set()

    try:
        while True:
            await asyncio.sleep(DISPATCH_POLL_SECONDS)
            try:
                async with SessionLocal() as session:
                    requeued = await requeue_expired_leases(session)
                    await session.commit()
                if requeued:
                    logger.info("queue: requeued %d expired lease(s)", requeued)

                for _ in range(MAX_DISPATCHES_PER_TICK):
                    async with SessionLocal() as session:
                        entry = await lease(session, worker_id=worker)
                        if entry is None:
                            await session.rollback()
                            break
                        run_id = entry.run_id
                        await session.commit()
                    task = asyncio.create_task(_execute_queued_entry(run_id))
                    in_flight.add(task)
                    task.add_done_callback(in_flight.discard)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never let one tick kill the loop
                logger.exception("queue dispatch loop tick failed")
    finally:
        # Best-effort drain on shutdown so in-flight runs persist their state.
        if in_flight:
            await asyncio.wait(
                in_flight, timeout=DISPATCH_SHUTDOWN_TIMEOUT
            )
