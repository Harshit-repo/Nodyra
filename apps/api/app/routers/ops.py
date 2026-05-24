"""Operational endpoints: Prometheus metrics and system status."""

import time
from datetime import datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Credential, Environment, Run, Workflow

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
