from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db import engine
from app.redis_client import redis_client

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
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        healthy = False
        checks["database"] = f"error: {exc}"

    try:
        await redis_client.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        healthy = False
        checks["redis"] = f"error: {exc}"

    body = {"status": "ok" if healthy else "degraded", "checks": checks}
    return JSONResponse(body, status_code=200 if healthy else 503)
