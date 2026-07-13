"""Operational endpoints: Prometheus metrics and system status."""

import socket
import time
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Credential, Environment, Run, RunnerPool, RunQueueEntry, Workflow
from app.schemas import (
    DeadLetterEntry,
    DeadLetterListResponse,
    DeadLetterReplayResponse,
    DispatcherCapacity,
    DrainRequest,
    QueueCapacity,
    QueueStats,
    RuntimeModeStatus,
)
from app.security import require_permission, require_role
from app.services import queue as run_queue
from app.services.drain_state import is_draining, set_draining

# require_role (not bare current_user): 401s without a token when
# auth_required is on, but keeps anonymous access on open (auth-off)
# instances — same convention as every other read endpoint. A strict
# current_user dependency here broke the SPA's ops surface (which only
# attaches a Bearer token after login) on no-auth self-hosted installs.
_viewer_dep = Depends(require_role("viewer"))

router = APIRouter(tags=["ops"])

_started_at = time.time()
_VERSION = "0.0.1"
_REPLICA_ID = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"

# P1-11: Module-level cache so Prometheus scrapes don't hammer the DB with
# COUNT queries every poll interval.  30 s TTL — the cache is discarded when
# stale and rebuilt on the next hit.
_METRICS_CACHE_TTL = 30.0
_metrics_cache: dict = {"text": "", "last_fetched": 0.0}


async def _counts(session: AsyncSession) -> dict[str, int]:
    async def count(query) -> int:  # noqa: ANN001 - inner helper
        return int(await session.scalar(query) or 0)

    return {
        "workflows": await count(select(func.count()).select_from(Workflow)),
        "workflows_active": await count(
            select(func.count()).select_from(Workflow).where(Workflow.active.is_(True))
        ),
        "environments": await count(select(func.count()).select_from(Environment)),
        "credentials": await count(select(func.count()).select_from(Credential)),
        "runs": await count(select(func.count()).select_from(Run)),
        "runs_success": await count(
            select(func.count()).select_from(Run).where(Run.status == "success")
        ),
        "runs_error": await count(
            select(func.count()).select_from(Run).where(Run.status.in_(("error", "timed_out")))
        ),
    }


@router.get("/ops/health")
async def ops_health(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Comprehensive component-level health check.

    Returns status for every subsystem: DB, Redis, queue, active runs,
    stuck runs, pool capacity.  Designed for load-balancer probes and
    operator dashboards — a 200 means the API can serve traffic; non-200
    means the load balancer should drain this replica.
    """
    from app.redis_client import redis_client as _redis

    health: dict[str, Any] = {
        "status": "ok",
        "version": _VERSION,
        "replica_id": _REPLICA_ID,
        "uptime_seconds": int(time.time() - _started_at),
        "server_time": datetime.now().astimezone().isoformat(),
    }

    # --- DB ---------------------------------------------------------------
    try:
        await session.execute(select(1))
        health["database"] = "ok"
    except Exception:
        health["database"] = {"status": "error", "error": "database probe failed"}
        health["status"] = "degraded"

    # --- Redis ------------------------------------------------------------
    try:
        await _redis.ping()
        health["redis"] = "ok"
    except Exception:
        health["redis"] = "unreachable"
        if settings.queue_backend == "redis":
            health["status"] = "degraded"

    # --- Queue ------------------------------------------------------------
    try:
        qs = await run_queue.stats(session)
        health["queue"] = {
            "queued": qs.get("queued", 0),
            "leased": qs.get("leased", 0),
            "running": qs.get("running", 0),
            "dead_lettered": qs.get("dead_lettered", 0),
            "oldest_queued_age_seconds": qs.get("oldest_queued_age_seconds"),
        }
    except Exception:
        health["queue"] = {"status": "error", "error": "queue stats query failed"}

    # --- Active / stuck runs ----------------------------------------------
    try:
        active_count = int(
            await session.scalar(
                select(func.count())
                .select_from(Run)
                .where(Run.status.in_(("running", "queued", "waiting")))
            )
            or 0
        )
        health["active_runs"] = active_count

        # Stuck: running runs that started >30 min ago with no recent node
        from datetime import UTC, timedelta

        stuck_cutoff = datetime.now(UTC) - timedelta(seconds=1800)
        stuck_count = int(
            await session.scalar(
                select(func.count())
                .select_from(Run)
                .where(
                    Run.status == "running",
                    Run.runner_pool_id.is_(None),
                    Run.started_at < stuck_cutoff,
                )
            )
            or 0
        )
        health["stuck_runs"] = stuck_count
    except Exception:
        health["active_runs"] = {"error": "active run count query failed"}

    # --- Pools ------------------------------------------------------------
    try:
        from app.services.runtime_pool import pool as _rt_pool

        if _rt_pool:
            health["runtime_pool"] = {
                "available": _rt_pool.available_global_slots(),
                "max_slots": _rt_pool.current_max_slots(),
                "configured_max": settings.max_concurrent_runs,
                "autoscale_enabled": settings.pool_autoscale_enabled,
            }
        else:
            health["runtime_pool"] = "unavailable"
    except Exception:
        health["runtime_pool"] = "unavailable"

    return health


@router.get("/system/status", dependencies=[_viewer_dep])
async def system_status(
    session: AsyncSession = Depends(get_session),
) -> dict:
    counts = await _counts(session)
    return {
        "version": _VERSION,
        "uptime_seconds": int(time.time() - _started_at),
        "healthy": True,
        "app_timezone": settings.app_timezone or "UTC",
        "server_time": datetime.now().astimezone().isoformat(),
        **counts,
    }


@router.get("/ops/runtime-mode", response_model=RuntimeModeStatus, dependencies=[_viewer_dep])
async def runtime_mode(
    session: AsyncSession = Depends(get_session),
) -> RuntimeModeStatus:
    """Report the active runtime topology and any production misconfigurations."""
    try:
        dialect = make_url(settings.database_url).get_backend_name()
    except Exception:  # noqa: BLE001 - never let a malformed URL 500 this probe
        dialect = "unknown"

    providers = await session.scalars(select(RunnerPool.provider).distinct())
    runner_providers = sorted({p for p in providers if p})
    replica_unsafe_reasons = settings.replica_unsafe_reasons()

    return RuntimeModeStatus(
        mode=settings.runtime_mode,
        database_dialect=dialect,
        queue_backend=settings.queue_backend,
        scheduler_role=settings.scheduler_role,
        webhook_role=settings.webhook_role,
        artifact_backend=settings.artifact_storage_backend,
        runner_providers=runner_providers,
        replica_safe=not replica_unsafe_reasons,
        replica_unsafe_reasons=replica_unsafe_reasons,
        allow_insecure=settings.runtime_allow_insecure,
        otel_enabled=settings.otel_enabled,
        warnings=settings.runtime_warnings(),
    )


@router.get("/ops/queue", response_model=QueueStats, dependencies=[_viewer_dep])
async def queue_stats(
    session: AsyncSession = Depends(get_session),
) -> QueueStats:
    """Aggregate run-queue health for the ops/backpressure surface.

    Pulls straight from ``services.queue.stats`` so the endpoint stays in sync
    with the durable queue state without re-deriving counts here.
    """
    data = await run_queue.stats(session)
    return QueueStats(**data)


@router.get(
    "/ops/capacity",
    response_model=QueueCapacity,
    dependencies=[_viewer_dep],
)
async def queue_capacity(
    session: AsyncSession = Depends(get_session),
) -> QueueCapacity:
    """Worker capacity planner data for the ops dashboard."""
    from app.services.dispatcher_health import live_dispatchers

    queue = await run_queue.stats(session)
    dispatchers_raw = await live_dispatchers()
    dispatchers = [DispatcherCapacity(**row) for row in dispatchers_raw]

    local_available = sum(
        int(d.available_slots or 0)
        for d in dispatchers
        if "local" in d.providers or "docker" in d.providers
    )
    local_max = sum(
        int(d.max_slots or 0)
        for d in dispatchers
        if "local" in d.providers or "docker" in d.providers
    )

    label_rows = (
        await session.execute(
            select(
                RunQueueEntry.required_labels,
                RunQueueEntry.runner_pool_id,
                RunnerPool.provider,
            )
            .outerjoin(RunnerPool, RunnerPool.id == RunQueueEntry.runner_pool_id)
            .where(
                RunQueueEntry.status == "queued",
                RunQueueEntry.required_labels.is_not(None),
            )
            .execution_options(skip_org_filter=True)
        )
    ).all()
    blocked = 0
    for required_labels, _runner_pool_id, provider in label_rows:
        target_provider = str(provider or "local")
        if target_provider not in {"local", "docker"}:
            continue
        satisfiable = any(
            target_provider in dispatcher.providers
            and run_queue.labels_satisfied(required_labels, dispatcher.labels)
            for dispatcher in dispatchers
        )
        if not satisfiable:
            blocked += 1

    return QueueCapacity(
        queued=int(queue.get("queued", 0)),
        leased=int(queue.get("leased", 0)),
        running=int(queue.get("running", 0)),
        local_available_slots=local_available,
        local_max_slots=local_max,
        dispatchers=dispatchers,
        label_blocked_queued=blocked,
    )


@router.get("/ops/drain", dependencies=[_viewer_dep])
async def drain_status() -> dict:
    """Whether the dispatch loop is currently draining (no new leases)."""
    return {"draining": await is_draining()}


@router.post(
    "/ops/drain",
    dependencies=[Depends(require_permission("ops:drain"))],
)
async def set_drain(payload: DrainRequest) -> dict:
    """Toggle drain mode. While true the dispatch loop stops leasing new
    queue entries; leased/running entries continue to completion. Used by
    deploy scripts to drain a replica before sending SIGTERM, avoiding
    avoidable ``cancelled`` runs (see "Production-readiness gaps" #2 in
    docs/architecture-improvement-plan.md).
    """
    try:
        draining = await set_draining(payload.draining)
    except Exception as exc:  # noqa: BLE001 - do not claim an unpropagated drain
        raise HTTPException(
            status_code=503,
            detail="Could not propagate drain state to execution workers; retry after Redis recovers",
        ) from exc
    return {"draining": draining}


@router.get(
    "/ops/pool",
    dependencies=[Depends(require_permission("ops:pool:read"))],
)
async def pool_status() -> dict:
    """Current pool capacity and autoscaling state."""
    from app.services.runtime_pool import pool as _rt_pool

    return {
        "max_slots": _rt_pool.current_max_slots(),
        "available": _rt_pool.available_global_slots(),
        "configured_max": settings.max_concurrent_runs,
        "autoscale_enabled": settings.pool_autoscale_enabled,
        "autoscale_max": settings.pool_autoscale_max,
        "autoscale_threshold": settings.pool_autoscale_threshold,
    }


@router.get(
    "/ops/sandbox",
    dependencies=[Depends(require_permission("ops:pool:read"))],
)
async def sandbox_status() -> dict:
    """Sandbox mode, probe result, and warm-pool occupancy."""
    from app.services.sandbox_pool import pool as _sbx_pool

    return {"mode": settings.execution_sandbox, **_sbx_pool.status()}


@router.post(
    "/ops/pool/resize",
    dependencies=[Depends(require_permission("ops:pool:resize"))],
)
async def pool_resize(payload: dict) -> dict:
    """Manually resize the global pool concurrency ceiling.

    Body: ``{"max_slots": 16}``.  The autoscaler still runs and may
    override this on its next tick if ``pool_autoscale_enabled`` is on.
    """
    target = int(payload.get("max_slots", settings.max_concurrent_runs))
    target = max(1, min(target, 128))  # hard cap at 128
    from app.services.runtime_pool import pool as _rt_pool

    new_max = await _rt_pool.resize(target)
    return {"max_slots": new_max}


@router.get(
    "/ops/replicas",
    dependencies=[Depends(require_permission("ops:replicas:read"))],
)
async def list_replicas() -> list[dict]:
    """List all replicas currently registered via Redis heartbeats.

    Each replica refreshes its heartbeat every 15 s.  Stale entries
    (older than 45 s) are shown with ``status: stale`` — those replicas
    have likely crashed or been scaled down.
    """
    from app.services.replica_health import list_replicas as _list

    return await _list()


@router.get(
    "/ops/dead-letter",
    response_model=DeadLetterListResponse,
    dependencies=[Depends(require_permission("ops:dead-letter:read"))],
)
async def list_dead_letter(
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
) -> DeadLetterListResponse:
    """List run-queue entries currently in the dead-letter state.

    Powers the ops UI's dead-letter triage view. Operators can decide whether
    to replay (via the replay endpoints below) or leave for manual investigation.
    """
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    total = int(
        await session.scalar(
            select(func.count())
            .select_from(RunQueueEntry)
            .where(RunQueueEntry.status == "dead_lettered")
        )
        or 0
    )
    rows = (
        await session.scalars(
            select(RunQueueEntry)
            .where(RunQueueEntry.status == "dead_lettered")
            .order_by(RunQueueEntry.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    entries = [
        DeadLetterEntry(
            run_id=r.run_id,
            workflow_id=r.workflow_id,
            status=r.status,
            attempts=r.attempts,
            max_attempts=r.max_attempts,
            queue_reason=r.queue_reason or "",
            last_error=r.last_error,
            available_at=r.available_at,
        )
        for r in rows
    ]
    return DeadLetterListResponse(entries=entries, total=total)


@router.post(
    "/ops/dead-letter/replay",
    response_model=DeadLetterReplayResponse,
    dependencies=[Depends(require_permission("ops:dead-letter:replay"))],
)
async def replay_dead_letter(
    session: AsyncSession = Depends(get_session),
) -> DeadLetterReplayResponse:
    """Bulk-replay every dead-letter entry currently on the queue.

    Useful after fixing a transient upstream outage (e.g. a runner pool came
    back) where the dead-letter queue is full of recoverable failures. Each
    entry is reset to ``queued`` via the same ``run_queue.replay`` path used
    by single-run replay, so attempt history is preserved.
    """
    rows = (
        await session.scalars(select(RunQueueEntry).where(RunQueueEntry.status == "dead_lettered"))
    ).all()
    replayed: list[str] = []
    skipped: list[str] = []
    for entry in rows:
        result = await run_queue.replay(session, run_id=entry.run_id)
        if result is None:
            skipped.append(entry.run_id)
        else:
            replayed.append(entry.run_id)
    await session.commit()
    return DeadLetterReplayResponse(replayed=replayed, skipped=skipped)


@router.get("/metrics")
async def metrics(session: AsyncSession = Depends(get_session)) -> Response:
    now = time.time()
    if now - _metrics_cache["last_fetched"] < _METRICS_CACHE_TTL:
        text = _metrics_cache["text"]
        try:
            from app.services.metrics import get_metrics_text

            text += get_metrics_text()
        except Exception:  # noqa: BLE001 - telemetry must stay best-effort
            pass
        return Response(
            content=text,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
    counts = await _counts(session)
    queue = await run_queue.stats(session)
    queue_oldest = queue.get("oldest_queued_age_seconds")

    lines = [
        "# HELP nodyra_workflows Total workflows.",
        "# TYPE nodyra_workflows gauge",
        f"nodyra_workflows {counts['workflows']}",
        "# HELP nodyra_workflows_active Active (production-triggered) workflows.",
        "# TYPE nodyra_workflows_active gauge",
        f"nodyra_workflows_active {counts['workflows_active']}",
        "# HELP nodyra_environments Number of Python environments.",
        "# TYPE nodyra_environments gauge",
        f"nodyra_environments {counts['environments']}",
        "# HELP nodyra_credentials Number of stored credentials.",
        "# TYPE nodyra_credentials gauge",
        f"nodyra_credentials {counts['credentials']}",
        "# HELP nodyra_runs_total Total recorded workflow runs.",
        "# TYPE nodyra_runs_total counter",
        f"nodyra_runs_total {counts['runs']}",
        "# HELP nodyra_runs_success_total Runs that finished successfully.",
        "# TYPE nodyra_runs_success_total counter",
        f"nodyra_runs_success_total {counts['runs_success']}",
        "# HELP nodyra_runs_error_total Runs that failed.",
        "# TYPE nodyra_runs_error_total counter",
        f"nodyra_runs_error_total {counts['runs_error']}",
        "# HELP nodyra_uptime_seconds API process uptime.",
        "# TYPE nodyra_uptime_seconds gauge",
        f"nodyra_uptime_seconds {int(time.time() - _started_at)}",
        # Run queue gauges — one series per orchestration state so operators
        # can alert on "queue backed up" (queued > N), "stuck leases"
        # (leased > N), or "dead-letter growth" (dead_lettered > 0) without
        # polling /ops/queue JSON.
        "# HELP nodyra_queue_depth Run queue entries grouped by orchestration status.",
        "# TYPE nodyra_queue_depth gauge",
        f'nodyra_queue_depth{{status="queued"}} {queue["queued"]}',
        f'nodyra_queue_depth{{status="leased"}} {queue["leased"]}',
        f'nodyra_queue_depth{{status="running"}} {queue["running"]}',
        f'nodyra_queue_depth{{status="waiting"}} {queue["waiting"]}',
        f'nodyra_queue_depth{{status="completed"}} {queue["completed"]}',
        f'nodyra_queue_depth{{status="failed"}} {queue["failed"]}',
        f'nodyra_queue_depth{{status="dead_lettered"}} {queue["dead_lettered"]}',
        f'nodyra_queue_depth{{status="cancelled"}} {queue["cancelled"]}',
        "# HELP nodyra_queue_oldest_queued_age_seconds Wait of the oldest "
        "queued entry; -1 when empty.",
        "# TYPE nodyra_queue_oldest_queued_age_seconds gauge",
        "nodyra_queue_oldest_queued_age_seconds "
        f"{queue_oldest if queue_oldest is not None else -1}",
        "# HELP nodyra_queue_draining 1 if the dispatch loop is currently draining, else 0.",
        "# TYPE nodyra_queue_draining gauge",
        f"nodyra_queue_draining {1 if settings.queue_drain else 0}",
    ]
    # Cache only database-backed gauges. In-process counters must be rendered
    # on every scrape; caching the full exposition hid new security and runtime
    # events for up to 30 seconds and made alerting observably stale.
    base_text = "\n".join(lines) + "\n"
    _metrics_cache["text"] = base_text
    _metrics_cache["last_fetched"] = now
    text = base_text
    try:
        from app.services.metrics import get_metrics_text

        text += get_metrics_text()
    except Exception:  # noqa: BLE001 - telemetry must stay best-effort
        pass
    return Response(
        content=text,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
