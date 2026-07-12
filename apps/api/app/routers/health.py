import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.redis_client import redis_client
from app.services.sandbox_pool import pool as sandbox_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict:
    """Liveness probe — process is up. No external dependencies checked."""
    return {"status": "ok"}


async def readiness_checks() -> tuple[bool, dict[str, str]]:
    """Shared readiness probe: PostgreSQL + Redis + sandbox state.

    Used by the API's ``GET /health/ready`` route and by the standalone
    worker's HTTP listener (``worker_health_port`` / helm ``worker.healthPort``)
    so both planes report readiness with identical semantics.

    Failure detail is logged, never returned: these endpoints are auth-exempt
    (K8s/compose probes can't authenticate), and raw driver errors can embed
    DSNs, hostnames, or credentials that must not leak to anonymous callers.
    """
    checks: dict[str, str] = {}
    healthy = True

    try:
        async with asyncio.timeout(5):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001
        healthy = False
        logger.exception("readiness: database check failed")
        checks["database"] = "error: unreachable"

    # H7: Redis is only a hard dependency when it actually backs the run queue
    # or a split dispatch topology. A single-process deployment
    # (queue_backend=none, dispatch_role=inline) has no Redis, so pinging it
    # would wrongly flap readiness to 503 on every probe.
    redis_required = (
        settings.queue_backend == "redis" or settings.dispatch_role != "inline"
    )
    if redis_required:
        try:
            async with asyncio.timeout(5):
                await redis_client.ping()
            checks["redis"] = "ok"
        except Exception:  # noqa: BLE001
            healthy = False
            logger.exception("readiness: redis check failed")
            checks["redis"] = "error: unreachable"
    else:
        checks["redis"] = "not required"

    # Sandbox state is process-local. Enforce it only where this process owns
    # local execution; split API/control replicas rely on worker readiness.
    if settings.execution_sandbox != "off":
        if settings.dispatch_role not in {"inline", "worker"}:
            checks["sandbox"] = "delegated to workers"
        elif sandbox_pool.enabled:
            checks["sandbox"] = sandbox_pool.describe()
        elif settings.execution_sandbox == "required":
            healthy = False
            checks["sandbox"] = "error: required but inactive"
        else:
            checks["sandbox"] = "inactive (subprocess fallback)"

    return healthy, checks


@router.get("/ready")
async def ready() -> JSONResponse:
    """Readiness probe — checks PostgreSQL and Redis connectivity."""
    healthy, checks = await readiness_checks()
    body = {"status": "ok" if healthy else "degraded", "checks": checks}
    return JSONResponse(body, status_code=200 if healthy else 503)
