import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal, engine
from app.models import Environment, Run
from app.redis_client import redis_client
from app.routers import (
    artifacts,
    audit,
    auth,
    code_modules,
    credentials,
    deployments,
    environments,
    export,
    expressions,
    health,
    internal,
    nodes,
    ops,
    pinned,
    runner_pools,
    runs,
    system_settings,
    webhooks,
    workflows,
)
from app.services.crypto import verify_token
from app.services.events import broker_reaper_loop
from app.services.queue import run_queue_dispatch_loop
from app.services.remote_dispatch import (
    cloud_idle_terminate_loop,
    dispatcher,
    runner_heartbeat_loop,
)
from app.services.retention import retention_loop
from app.services.runner import drain_active_runs, shutdown_active_runs
from app.services.runtime_pool import idle_reaper_loop
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


async def _mark_interrupted_runs() -> None:
    """Clear run records that were left running by a previous API process."""
    try:
        async with SessionLocal() as session:
            result = await session.scalars(select(Run).where(Run.status == "running"))
            runs = result.all()
            if not runs:
                return
            now = datetime.now(UTC)
            for run in runs:
                run.status = "cancelled"
                run.finished_at = now
            await session.commit()
    except Exception:  # noqa: BLE001 - DB may not be migrated yet; not fatal
        pass


def _detect_local_timezone() -> str:
    """Best-effort detection of the OS's IANA timezone (e.g. Australia/Sydney).

    Tries ``tzlocal`` first (correctly translates Windows zone names to IANA);
    falls back to ``datetime.now().astimezone().tzinfo`` and finally "UTC".
    """
    try:
        from tzlocal import get_localzone_name  # imported lazily; small dep

        name = get_localzone_name()
        if name:
            return str(name)
    except Exception:  # noqa: BLE001 - any failure → fall through
        pass
    tz = datetime.now().astimezone().tzinfo
    name = getattr(tz, "key", None) or (str(tz) if tz else "")
    return name or "UTC"


async def _bounded(coro, timeout: float = 5.0) -> None:
    """Run a shutdown step but never let it block teardown forever.

    The aiosqlite driver can hang on ``engine.dispose()`` when the event loop
    is already tearing down (its connection lives on a worker thread), which
    would otherwise wedge both the real server's shutdown and TestClient.
    """
    with contextlib.suppress(Exception):
        await asyncio.wait_for(coro, timeout=timeout)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pin the app's default timezone — explicit .env override wins, otherwise
    # ask the OS. This becomes the fallback for any schedule_trigger that
    # doesn't set its own ``tz``.
    if not settings.app_timezone:
        settings.app_timezone = _detect_local_timezone()
    logging.getLogger("noodle").info(
        "noodle app timezone: %s", settings.app_timezone
    )

    await _ensure_global_environment()
    await _mark_interrupted_runs()
    # Register the configured artifact backend. ``local`` self-registers on
    # first use; ``s3`` needs an explicit hook so a missing boto3 install or
    # bad bucket name surfaces at startup rather than on the first download.
    if settings.artifact_storage_backend == "s3":
        from app.services.s3_artifact_backend import register_s3_backend

        try:
            register_s3_backend()
        except Exception:  # noqa: BLE001
            logging.getLogger("noodle").exception(
                "Failed to register S3 artifact backend; falling back to local."
            )
    # Scheduler / retention loops respect both ``enable_inprocess_scheduler``
    # (legacy on/off knob) and ``scheduler_role``:
    #   inline    -> run unconditionally (single-process dev)
    #   leader    -> run only while we hold the DB advisory lock
    #   disabled  -> never run
    from app.services.leader_election import run_with_leader_election

    def _make_loop_task(loop_fn, lock_name: str):
        if not settings.enable_inprocess_scheduler:
            return None
        if settings.scheduler_role == "disabled":
            return None
        if settings.scheduler_role == "leader":
            return asyncio.create_task(
                run_with_leader_election(loop_fn, name=lock_name)
            )
        return asyncio.create_task(loop_fn())

    scheduler = _make_loop_task(scheduler_loop, "noodle.scheduler")
    # Retention prune is gated on the same flag — it's another in-process
    # loop and we want at most one owner across replicas.
    retention = _make_loop_task(retention_loop, "noodle.retention")
    # Idle reaper closes warm runner subprocesses that have been sitting
    # unused past ``runner_idle_seconds``. Independent of the scheduler flag
    # because every replica should reap its own pool.
    reaper = (
        asyncio.create_task(idle_reaper_loop())
        if settings.use_subprocess_runner and settings.runner_idle_seconds > 0
        else None
    )
    # Broker reaper: every replica owns its own pub/sub buffer, so it
    # always runs (independent of the scheduler flag).
    broker_reaper = asyncio.create_task(broker_reaper_loop())
    queue_loop = asyncio.create_task(run_queue_dispatch_loop())
    cloud_idle = asyncio.create_task(cloud_idle_terminate_loop())
    heartbeat = asyncio.create_task(runner_heartbeat_loop())
    yield
    # Graceful drain on shutdown: stop the dispatch loop from leasing new
    # entries, give in-flight runs a bounded window to finish, then
    # cancel any laggards. ``queue_drain`` may already be true if an
    # operator pre-drained via /ops/drain; restore the prior value after
    # teardown so the setting isn't sticky for the next lifespan
    # (matters for tests that reuse the process).
    _prior_drain = settings.queue_drain
    settings.queue_drain = True
    for task in (scheduler, retention, reaper, broker_reaper, queue_loop, cloud_idle, heartbeat):
        if task is None:
            continue
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    drain_timeout = max(settings.queue_dispatch_shutdown_timeout_seconds, 0.0)
    if drain_timeout > 0:
        leftover = await drain_active_runs(drain_timeout)
        if leftover:
            logging.getLogger("noodle").warning(
                "shutdown drain expired with %d active run(s); forcing cancel",
                leftover,
            )
    await _bounded(shutdown_active_runs())
    await _bounded(dispatcher.shutdown())
    await _bounded(runtime_pool.shutdown())
    await _bounded(engine.dispose())
    await _bounded(redis_client.aclose())
    settings.queue_drain = _prior_drain


app = FastAPI(title="Noodle API", version="0.0.1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_AUTH_EXEMPT_PREFIXES = (
    "/auth",
    "/health",
    "/webhook",
    "/webhook-test",
    "/internal",
    "/runner-pools/ws",  # agent runner WS — uses its own token query param
)
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
# Webhook ingress role:
#   inline    -> serve /webhooks/* from this process (default; dev)
#   ingress   -> serve /webhooks/* (this process IS the ingress tier)
#   disabled  -> do not mount /webhooks/* (control-plane only)
if settings.webhook_role != "disabled":
    app.include_router(webhooks.router)
app.include_router(workflows.router)
app.include_router(runs.router)
app.include_router(deployments.router)
app.include_router(code_modules.router)
app.include_router(internal.router)
app.include_router(export.router)
app.include_router(auth.router)
app.include_router(credentials.router)
app.include_router(audit.router)
app.include_router(artifacts.router)
app.include_router(ops.router)
app.include_router(pinned.router)
app.include_router(system_settings.router)
app.include_router(runner_pools.router)
app.include_router(expressions.router)


@app.get("/")
async def root() -> dict:
    return {"name": "Noodle API", "version": "0.0.1"}
