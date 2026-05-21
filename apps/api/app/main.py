import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal, engine
from app.models import Environment
from app.redis_client import redis_client
from app.routers import (
    audit,
    auth,
    credentials,
    environments,
    export,
    health,
    nodes,
    ops,
    pinned,
    runs,
    webhooks,
    workflows,
)
from app.services.crypto import verify_token
from app.services.runtime_pool import pool as runtime_pool
from app.services.triggers import scheduler_loop


async def _ensure_global_environment() -> None:
    """Make sure exactly one global environment exists."""
    try:
        async with SessionLocal() as session:
            result = await session.scalars(
                select(Environment).where(Environment.is_global.is_(True))
            )
            if result.first() is None:
                session.add(
                    Environment(
                        name="Global",
                        is_global=True,
                        packages=["requests"],
                        status="pending",
                    )
                )
                await session.commit()
    except Exception:  # noqa: BLE001 - DB may not be migrated yet; not fatal
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _ensure_global_environment()
    scheduler = asyncio.create_task(scheduler_loop())
    yield
    scheduler.cancel()
    await runtime_pool.shutdown()
    await engine.dispose()
    await redis_client.aclose()


app = FastAPI(title="Noodle API", version="0.0.1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_AUTH_EXEMPT_PREFIXES = ("/auth", "/health", "/webhook")
_AUTH_EXEMPT_PATHS = {"/", "/metrics", "/system/status"}


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """Reject unauthenticated requests when ``settings.auth_required`` is on.

    ``/auth/*``, ``/health/*`` and the webhook ingress are always reachable so
    users can sign in and external systems can trigger workflows with their
    own auth (e.g. webhook URL secret).
    """
    if not settings.auth_required or request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    if path in _AUTH_EXEMPT_PATHS or any(
        path == p or path.startswith(f"{p}/") for p in _AUTH_EXEMPT_PREFIXES
    ):
        return await call_next(request)

    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        return JSONResponse(
            {"detail": "Authentication required"}, status_code=401
        )
    if verify_token(header.removeprefix("Bearer ")) is None:
        return JSONResponse(
            {"detail": "Invalid or expired token"}, status_code=401
        )
    return await call_next(request)

app.include_router(health.router)
app.include_router(nodes.router)
app.include_router(environments.router)
app.include_router(webhooks.router)
app.include_router(workflows.router)
app.include_router(runs.router)
app.include_router(export.router)
app.include_router(auth.router)
app.include_router(credentials.router)
app.include_router(audit.router)
app.include_router(ops.router)
app.include_router(pinned.router)


@app.get("/")
async def root() -> dict:
    return {"name": "Noodle API", "version": "0.0.1"}
