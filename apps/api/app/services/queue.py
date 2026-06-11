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

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import Run, RunnerPool, RunQueueEntry

logger = logging.getLogger(__name__)

# Defaults; the *active* values are read from ``settings.queue_*`` at call
# time (see "Production-readiness gaps" in
# docs/architecture-improvement-plan.md, item 4). Kept as module constants
# for back-compat with tests that import them directly.
DEFAULT_LEASE_SECONDS = 30
RETRY_BACKOFF_BASE_SECONDS = 5
RETRY_BACKOFF_MAX_SECONDS = 300


def _lease_seconds(override: int | None = None) -> int:
    return override if override is not None else settings.queue_lease_seconds


def _retry_backoff_base() -> int:
    return settings.queue_retry_backoff_base_seconds


def _retry_backoff_max() -> int:
    return settings.queue_retry_backoff_max_seconds


def _default_max_attempts() -> int:
    return settings.queue_default_max_attempts

# Orchestration states that are still "live" (occupy the run's single queue slot).
ACTIVE_STATUSES = ("queued", "leased", "running", "waiting")

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
    # Queue bookkeeping is internal infrastructure keyed by a unique run_id;
    # callers arrive in mixed org contexts (run task pinned to its org, the
    # dispatch loop as system, request handlers in the caller's org). Org
    # scoping here adds silent-miss failure modes without an isolation win,
    # so the lookup deliberately bypasses the tenancy filter — like lease().
    return await session.scalar(
        select(RunQueueEntry)
        .where(RunQueueEntry.run_id == run_id)
        .execution_options(skip_org_filter=True)
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
    max_attempts: int | None = None,
    available_at: datetime | None = None,
    trace_context: dict | None = None,
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
        existing.trace_context = trace_context
        return existing

    entry = RunQueueEntry(
        run_id=run_id,
        workflow_id=workflow_id,
        environment_id=environment_id,
        runner_pool_id=runner_pool_id,
        status="queued",
        queue_reason=reason,
        priority=priority,
        max_attempts=max_attempts if max_attempts is not None else _default_max_attempts(),
        available_at=available_at or _now(None),
        trace_context=trace_context,
    )
    session.add(entry)
    await session.flush()
    return entry


async def _org_fair_order(
    session: AsyncSession, moment: datetime
) -> list[str]:
    """Orgs with eligible queued work, fairest-first (Phase C2).

    Two cheap grouped queries instead of correlated subqueries in the lease
    statement, so the SKIP LOCKED fast path stays a plain indexed select:

    * orgs are ordered by their current in-flight count ascending (an org
      with nothing running leases before an org with a deep backlog — a
      1000-entry flood from one tenant interleaves instead of starving the
      rest), tie-broken by oldest eligible entry;
    * orgs at their ``max_concurrent_runs`` cap are excluded entirely and
      their queued entries get ``queue_reason="org_quota_exceeded"`` for the
      backpressure UI.
    """
    from app.services.org_limits import effective_limits

    eligible = (
        await session.execute(
            select(
                RunQueueEntry.org_id,
                func.min(RunQueueEntry.available_at),
            )
            .where(
                RunQueueEntry.status == "queued",
                RunQueueEntry.available_at <= moment,
            )
            .group_by(RunQueueEntry.org_id)
            .execution_options(skip_org_filter=True)
        )
    ).all()
    if not eligible:
        return []
    inflight = dict(
        (
            await session.execute(
                select(RunQueueEntry.org_id, func.count())
                .where(RunQueueEntry.status.in_(("leased", "running")))
                .group_by(RunQueueEntry.org_id)
                .execution_options(skip_org_filter=True)
            )
        ).all()
    )
    allowed: list[tuple[int, datetime, str]] = []
    capped: list[str] = []
    for org_id, oldest in eligible:
        limits = await effective_limits(session, org_id)
        cap = limits.max_concurrent_runs
        if cap and inflight.get(org_id, 0) >= cap:
            capped.append(org_id)
            continue
        allowed.append((inflight.get(org_id, 0), oldest, org_id))
    if capped:
        await session.execute(
            update(RunQueueEntry)
            .where(
                RunQueueEntry.org_id.in_(capped),
                RunQueueEntry.status == "queued",
                RunQueueEntry.queue_reason != "org_quota_exceeded",
            )
            .values(queue_reason="org_quota_exceeded")
            .execution_options(synchronize_session=False)
        )
    allowed.sort()
    return [org_id for _, _, org_id in allowed]


async def lease(
    session: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int | None = None,
    now: datetime | None = None,
    providers: frozenset[str] | None = None,
) -> RunQueueEntry | None:
    """Claim the next eligible queued entry for ``worker_id``.

    Eligible = ``status == "queued"`` and ``available_at <= now``. Ordered by
    priority (high first) then oldest-available (FIFO within a priority). With
    multi-tenancy on, an org-fair pre-pass picks WHICH org to lease from
    (fewest in-flight first, per-org caps enforced) before this ordering
    applies within that org. The claimed entry is marked ``leased`` with a
    fresh lease expiry and its attempt count incremented. Returns the leased
    entry, or ``None`` when nothing is eligible.
    """
    moment = _now(now)

    def _base_stmt():
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
            .execution_options(skip_org_filter=True)
        )
        # Provider capability filter (program A1): a standalone worker can run
        # entries whose execution it can actually host — "local" (no pool) and
        # pools whose provider doesn't need a WS terminating in another
        # process. None = no filter (inline single-process role).
        if providers is not None:
            clauses = []
            if "local" in providers:
                clauses.append(RunQueueEntry.runner_pool_id.is_(None))
            remote = providers - {"local"}
            if remote:
                pool_ids = (
                    select(RunnerPool.id)
                    .where(RunnerPool.provider.in_(sorted(remote)))
                    .scalar_subquery()
                )
                clauses.append(RunQueueEntry.runner_pool_id.in_(pool_ids))
            stmt = stmt.where(or_(*clauses))
        # Postgres: lock the candidate row and skip ones already locked by a
        # peer worker so concurrent leases don't hand the same entry out
        # twice. SQLite has no row locking, so only request it where
        # supported.
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        return stmt

    entry: RunQueueEntry | None = None
    if settings.multi_tenancy_enabled:
        # Try the fairest few orgs in order; a miss means a peer worker
        # drained that org between the pre-pass and the lock attempt.
        for org_id in (await _org_fair_order(session, moment))[:5]:
            entry = await session.scalar(
                _base_stmt().where(RunQueueEntry.org_id == org_id)
            )
            if entry is not None:
                break
    else:
        entry = await session.scalar(_base_stmt())
    if entry is None:
        return None

    entry.status = "leased"
    entry.leased_by = worker_id
    entry.lease_expires_at = moment + timedelta(seconds=_lease_seconds(lease_seconds))
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
    lease_seconds: int | None = None,
    now: datetime | None = None,
) -> bool:
    """Extend the lease on an active entry. Returns ``False`` if there is no
    leasable entry (already completed/cancelled or missing)."""
    entry = await _get(session, run_id)
    if entry is None or entry.status not in ("leased", "running"):
        return False
    entry.lease_expires_at = _now(now) + timedelta(seconds=_lease_seconds(lease_seconds))
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


async def wait_for_approval(session: AsyncSession, *, run_id: str) -> bool:
    """Park a run until an operator approval explicitly resumes it."""
    entry = await _get(session, run_id)
    if entry is None:
        return False
    entry.status = "waiting"
    entry.queue_reason = "agent_approval"
    entry.leased_by = None
    entry.lease_expires_at = None
    return True


async def resume_waiting(
    session: AsyncSession,
    *,
    run_id: str,
    replay_seed: dict,
    now: datetime | None = None,
) -> RunQueueEntry | None:
    """Move an approval-waiting run back to the queue with resume state."""
    entry = await _get(session, run_id)
    if entry is None or entry.status != "waiting":
        return None
    moment = _now(now)
    _append_attempt(entry, event="approval_resume", error=None, ts=moment)
    entry.status = "queued"
    entry.queue_reason = "approval_resume"
    entry.leased_by = None
    entry.lease_expires_at = None
    entry.available_at = moment
    entry.replay_seed = replay_seed
    return entry


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
            _retry_backoff_base() * (2 ** max(entry.attempts - 1, 0)),
            _retry_backoff_max(),
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
            select(RunQueueEntry).where(
                RunQueueEntry.status == "leased",
                RunQueueEntry.lease_expires_at.is_not(None),
                RunQueueEntry.lease_expires_at <= moment,
            )
        )
    ).all()

    acted = 0
    for entry in leased:
        # DB-side filter already narrows to expired leases; re-check in Python
        # to stay correct under naive/aware timestamp mismatches (SQLite).
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
            # The worker that held this lease is presumed dead mid-execution.
            # Reset the user-facing Run row too: _execute_queued_entry only
            # dispatches runs in status "queued".
            run = await session.scalar(
                select(Run)
                .where(Run.id == entry.run_id, Run.status == "running")
                .execution_options(skip_org_filter=True)
            )
            if run is not None:
                run.status = "queued"
                run.finished_at = None
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
            select(RunQueueEntry.status, func.count())
            .group_by(RunQueueEntry.status)
            .execution_options(skip_org_filter=True)
        )
    ).all()
    counts = {status: int(count) for status, count in rows}

    oldest = await session.scalar(
        select(func.min(RunQueueEntry.available_at))
        .where(RunQueueEntry.status == "queued")
        .execution_options(skip_org_filter=True)
    )
    oldest_age = None
    if oldest is not None:
        oldest_age = max(0.0, (moment - _as_aware(oldest)).total_seconds())

    result = {
        "queued": counts.get("queued", 0),
        "leased": counts.get("leased", 0),
        "running": counts.get("running", 0),
        "waiting": counts.get("waiting", 0),
        "completed": counts.get("completed", 0),
        "failed": counts.get("failed", 0),
        "dead_lettered": counts.get("dead_lettered", 0),
        "cancelled": counts.get("cancelled", 0),
        "oldest_queued_age_seconds": oldest_age,
    }
    if settings.multi_tenancy_enabled:
        # C6: per-org backpressure breakdown, including entries parked by the
        # org concurrency cap (queue_reason="org_quota_exceeded").
        org_rows = (
            await session.execute(
                select(
                    RunQueueEntry.org_id,
                    RunQueueEntry.status,
                    func.count(),
                )
                .where(
                    RunQueueEntry.status.in_(
                        ("queued", "leased", "running", "waiting")
                    )
                )
                .group_by(RunQueueEntry.org_id, RunQueueEntry.status)
                .execution_options(skip_org_filter=True)
            )
        ).all()
        by_org: dict[str, dict[str, int]] = {}
        for org_id, status, count in org_rows:
            by_org.setdefault(str(org_id), {})[str(status)] = int(count)
        parked_rows = (
            await session.execute(
                select(RunQueueEntry.org_id, func.count())
                .where(
                    RunQueueEntry.status == "queued",
                    RunQueueEntry.queue_reason == "org_quota_exceeded",
                )
                .group_by(RunQueueEntry.org_id)
                .execution_options(skip_org_filter=True)
            )
        ).all()
        for org_id, count in parked_rows:
            by_org.setdefault(str(org_id), {})["quota_parked"] = int(count)
        result["by_org"] = by_org
    return result


# ---------------------------------------------------------------------------
# Background dispatch loop
# ---------------------------------------------------------------------------

# Defaults used when ``settings`` hasn't been loaded yet (e.g. tooling
# scripts). The dispatch loop reads from ``settings.queue_*`` at runtime so
# operators can tune backpressure without code changes.
DISPATCH_POLL_SECONDS = 1.0
MAX_DISPATCHES_PER_TICK = 25
DISPATCH_SHUTDOWN_TIMEOUT = 5.0


def _worker_id() -> str:
    """A stable-ish identifier for the current process so leases can be
    attributed to a specific replica when diagnosing lost workers."""
    return f"{socket.gethostname()}:{id(asyncio.get_running_loop())}"


async def _cancel_reconcile(
    session: AsyncSession, active_runs: dict[str, "asyncio.Task[None]"]
) -> list[str]:
    """Cancel local tasks whose queue entry was cancelled by another process.

    In split topologies the API replica handling DELETE /runs/{id} has no
    task handle — it marks the RunQueueEntry cancelled and this worker-side
    sweep turns that into a real asyncio cancellation.
    """
    if not active_runs:
        return []
    rows = (
        await session.scalars(
            select(RunQueueEntry.run_id)
            .where(
                RunQueueEntry.run_id.in_(list(active_runs)),
                RunQueueEntry.status == "cancelled",
            )
            .execution_options(skip_org_filter=True)
        )
    ).all()
    cancelled: list[str] = []
    for run_id in rows:
        task = active_runs.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            cancelled.append(run_id)
    return cancelled


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
    from app.services.runner import _active_runs, _execute_queued_entry  # noqa: PLC0415
    from app.services.runtime_pool import pool as runtime_pool  # noqa: PLC0415

    worker = _worker_id()
    in_flight: set[asyncio.Task[None]] = set()

    # Provider capability by role (program A1): a standalone worker can host
    # local subprocess runs and docker-pool runs; agent/kubernetes pools need
    # the WebSocket-terminating API process, so their entries are left for a
    # dispatch_role=inline replica. Inline leases everything (today's mode).
    role = settings.dispatch_role
    providers = frozenset({"local", "docker"}) if role == "worker" else None

    try:
        while True:
            await asyncio.sleep(settings.queue_dispatch_poll_seconds)
            try:
                async with SessionLocal() as session:
                    requeued = await requeue_expired_leases(session)
                    await session.commit()
                if requeued:
                    logger.info("queue: requeued %d expired lease(s)", requeued)

                if role == "worker":
                    # Cross-process cancellation: see _cancel_reconcile.
                    async with SessionLocal() as session:
                        cancelled = await _cancel_reconcile(session, _active_runs)
                    if cancelled:
                        logger.info(
                            "queue: cancelled %d run(s) flagged by control plane",
                            len(cancelled),
                        )

                # Drain mode: keep requeueing expired leases and let in-flight
                # tasks finish, but stop pulling new work so the process can
                # exit cleanly without producing avoidable cancelled runs.
                if settings.queue_drain:
                    continue

                # Bound how many LOCAL (in-process pool) runs we lease this
                # tick to the free concurrency slots measured now — leasing
                # more would just pile up coroutines blocked on the pool
                # semaphore. Remote runs aren't subject to this (their
                # capacity is enforced by the remote pool / _QueuedError).
                local_budget = runtime_pool.available_global_slots()
                for _ in range(settings.queue_max_dispatches_per_tick):
                    async with SessionLocal() as session:
                        entry = await lease(
                            session, worker_id=worker, providers=providers
                        )
                        if entry is None:
                            await session.rollback()
                            break
                        is_local = entry.runner_pool_id is None
                        if is_local and local_budget <= 0:
                            # No local capacity — drop the lease (rollback
                            # leaves it ``queued`` with attempts unchanged) and
                            # try again on a later tick when a slot frees.
                            await session.rollback()
                            break
                        run_id = entry.run_id
                        await session.commit()
                    if is_local:
                        local_budget -= 1
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
                in_flight, timeout=settings.queue_dispatch_shutdown_timeout_seconds
            )
