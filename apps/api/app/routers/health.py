import asyncio
import functools
import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.redis_client import redis_client
from app.services.sandbox_pool import pool as sandbox_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


@functools.cache
def _expected_schema_head() -> str:
    """The migration revision this code expects the database to be at.

    Read from the shipped Alembic scripts rather than a hardcoded constant, so
    the value can never drift from the migrations in the same image. Alembic's
    ``ScriptDirectory`` is imported lazily: it is a dev/deploy-time dependency
    and readiness must not hard-fail if it is trimmed from a slim image.
    """
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        api_root = Path(__file__).resolve().parents[2]  # …/apps/api
        config = Config(str(api_root / "alembic.ini"))
        config.set_main_option("script_location", str(api_root / "alembic"))
        heads = ScriptDirectory.from_config(config).get_heads()
    except Exception:  # noqa: BLE001 — never let probe wiring crash the probe
        logger.exception("readiness: could not resolve expected schema head")
        return "unknown"
    # A branched history has no single head; skip the check rather than guess.
    return heads[0] if len(heads) == 1 else "unknown"


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

    # Connectivity AND schema. A reachable database whose migrations have not
    # run is NOT ready: the Helm chart migrates in a separate job, so a lagging
    # or failed job would otherwise let this pod go Ready and serve 500s on
    # every write. ``SELECT 1`` alone cannot see that (F-04).
    expected_head = _expected_schema_head()
    try:
        async with asyncio.timeout(5):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                actual_head = (
                    await conn.scalar(text("SELECT version_num FROM alembic_version"))
                    if expected_head != "unknown"
                    else None
                )
    except Exception:  # noqa: BLE001
        healthy = False
        logger.exception("readiness: database check failed")
        checks["database"] = "error: unreachable"
    else:
        if expected_head == "unknown" or str(actual_head or "") == expected_head:
            checks["database"] = "ok"
        else:
            healthy = False
            logger.error(
                "readiness: schema at %r, expected %r — migrations have not run",
                actual_head,
                expected_head,
            )
            # Revision ids are non-sensitive and make the failure actionable;
            # no DSN, host, or driver text is included.
            checks["database"] = (
                f"error: schema at {actual_head or 'none'}, expected {expected_head}"
            )

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
