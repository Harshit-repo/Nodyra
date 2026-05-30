"""Operational endpoints: Prometheus metrics and system status."""

import time
from datetime import datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Credential, Environment, Run, RunnerPool, Workflow
from app.schemas import DrainRequest, QueueStats, RuntimeModeStatus
from app.security import require_permission
from app.services import queue as run_queue

router = APIRouter(tags=["ops"])

_started_at = time.time()
_VERSION = "0.0.1"


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
            select(func.count()).select_from(Run).where(Run.status == "error")
        ),
    }


@router.get("/system/status")
async def system_status(session: AsyncSession = Depends(get_session)) -> dict:
    counts = await _counts(session)
    return {
        "version": _VERSION,
        "uptime_seconds": int(time.time() - _started_at),
        "healthy": True,
        "app_timezone": settings.app_timezone or "UTC",
        "server_time": datetime.now().astimezone().isoformat(),
        **counts,
    }


@router.get("/ops/runtime-mode", response_model=RuntimeModeStatus)
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

    return RuntimeModeStatus(
        mode=settings.runtime_mode,
        database_dialect=dialect,
        queue_backend=settings.queue_backend,
        scheduler_role=settings.scheduler_role,
        webhook_role=settings.webhook_role,
        artifact_backend=settings.artifact_storage_backend,
        runner_providers=runner_providers,
        allow_insecure=settings.runtime_allow_insecure,
        warnings=settings.runtime_warnings(),
    )


@router.get("/ops/queue", response_model=QueueStats)
async def queue_stats(session: AsyncSession = Depends(get_session)) -> QueueStats:
    """Aggregate run-queue health for the ops/backpressure surface.

    Pulls straight from ``services.queue.stats`` so the endpoint stays in sync
    with the durable queue state without re-deriving counts here.
    """
    data = await run_queue.stats(session)
    return QueueStats(**data)


@router.get("/ops/drain")
async def drain_status() -> dict:
    """Whether the dispatch loop is currently draining (no new leases)."""
    return {"draining": settings.queue_drain}


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
    settings.queue_drain = bool(payload.draining)
    return {"draining": settings.queue_drain}


@router.get("/metrics")
async def metrics(session: AsyncSession = Depends(get_session)) -> Response:
    counts = await _counts(session)
    lines = [
        "# HELP noodle_workflows Total workflows.",
        "# TYPE noodle_workflows gauge",
        f"noodle_workflows {counts['workflows']}",
        "# HELP noodle_workflows_active Active (production-triggered) workflows.",
        "# TYPE noodle_workflows_active gauge",
        f"noodle_workflows_active {counts['workflows_active']}",
        "# HELP noodle_environments Number of Python environments.",
        "# TYPE noodle_environments gauge",
        f"noodle_environments {counts['environments']}",
        "# HELP noodle_credentials Number of stored credentials.",
        "# TYPE noodle_credentials gauge",
        f"noodle_credentials {counts['credentials']}",
        "# HELP noodle_runs_total Total recorded workflow runs.",
        "# TYPE noodle_runs_total counter",
        f"noodle_runs_total {counts['runs']}",
        "# HELP noodle_runs_success_total Runs that finished successfully.",
        "# TYPE noodle_runs_success_total counter",
        f"noodle_runs_success_total {counts['runs_success']}",
        "# HELP noodle_runs_error_total Runs that failed.",
        "# TYPE noodle_runs_error_total counter",
        f"noodle_runs_error_total {counts['runs_error']}",
        "# HELP noodle_uptime_seconds API process uptime.",
        "# TYPE noodle_uptime_seconds gauge",
        f"noodle_uptime_seconds {int(time.time() - _started_at)}",
    ]
    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
