import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.redis_client import redis_client
from app.services.sandbox_pool import pool as sandbox_pool

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict:
    """Liveness probe — process is up. No external dependencies checked."""
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> JSONResponse:
    """Readiness probe — checks PostgreSQL and Redis connectivity."""
    checks: dict[str, str] = {}
    healthy = True

    try:
        async with asyncio.timeout(5):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        healthy = False
        checks["database"] = f"error: {exc}"

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
        except Exception as exc:  # noqa: BLE001
            healthy = False
            checks["redis"] = f"error: {exc}"
    else:
        checks["redis"] = "not required"

    # Sandbox state is informational in "auto" (subprocess fallback is fine)
    # but a hard readiness failure in "required" — runs would error at
    # dispatch time, so refuse traffic instead.
    if settings.execution_sandbox != "off":
        if sandbox_pool.enabled:
            checks["sandbox"] = sandbox_pool.describe()
        elif settings.execution_sandbox == "required":
            healthy = False
            checks["sandbox"] = "error: required but inactive"
        else:
            checks["sandbox"] = "inactive (subprocess fallback)"

    body = {"status": "ok" if healthy else "degraded", "checks": checks}
    return JSONResponse(body, status_code=200 if healthy else 503)
