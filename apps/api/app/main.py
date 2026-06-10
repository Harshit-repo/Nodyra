import asyncio
import contextlib
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import SessionLocal, engine
from app.models import Environment, Run, RunQueueEntry
from app.redis_client import redis_client
from app.routers import (
    artifacts,
    audit,
    auth,
    chat,
    chat_public,
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
    provider_webhooks,
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
    logger = logging.getLogger("noodle")
    try:
        async with SessionLocal() as session:
            existing = (
                await session.scalars(
                    select(Environment).where(Environment.is_global.is_(True))
                )
            ).all()
            if len(existing) > 1:
                logger.warning(
                    "_ensure_global_environment: found %d global environments; "
                    "expected exactly one. Multi-replica startup race likely.",
                    len(existing),
                )
            if not existing:
                session.add(
                    Environment(
                        name="Global",
                        is_global=True,
                        packages=["requests"],
                        status="pending",
                    )
                )
                try:
                    await session.commit()
                except IntegrityError:
                    pass  # Concurrent replica inserted first — benign
    except Exception:
        logger.debug("_ensure_global_environment failed; DB may not be migrated yet", exc_info=True)


async def _mark_interrupted_runs() -> None:
    """Clear run records that were left running by a previous API process.

    Also cancels the corresponding RunQueueEntry rows so the dispatch loop
    does not attempt to re-lease entries whose runs are already gone.
    Covers both ``running`` (actively executing) and ``waiting`` (paused for
    operator approval) — neither can be resumed after a process restart.
    """
    logger = logging.getLogger("noodle")
    try:
        async with SessionLocal() as session:
            result = await session.scalars(
                select(Run).where(Run.status.in_(("running", "waiting")))
            )
            runs = result.all()
            if not runs:
                return
            run_ids = [r.id for r in runs]
            now = datetime.now(UTC)
            for run in runs:
                run.status = "cancelled"
                run.finished_at = now

            # Cancel matching queue entries so they are not re-dispatched.
            queue_entries = (
                await session.scalars(
                    select(RunQueueEntry).where(RunQueueEntry.run_id.in_(run_ids))
                )
            ).all()
            for entry in queue_entries:
                entry.status = "cancelled"
                entry.leased_by = None
                entry.lease_expires_at = None

            await session.commit()
            logger.warning(
                "startup: cancelled %d interrupted run(s): %s",
                len(runs),
                run_ids,
            )

        # Also clean up any run_queue rows stuck as "running" whose
        # corresponding run is already in a terminal state.  This can
        # happen when a previous restart cancelled the Run record but
        # crashed before updating the queue entry.
        async with SessionLocal() as session:
            orphaned = (
                await session.scalars(
                    select(RunQueueEntry)
                    .join(Run, RunQueueEntry.run_id == Run.id)
                    .where(
                        RunQueueEntry.status == "running",
                        Run.status.in_(("cancelled", "error", "success")),
                    )
                )
            ).all()
            if orphaned:
                for entry in orphaned:
                    entry.status = "cancelled"
                    entry.leased_by = None
                    entry.lease_expires_at = None
                await session.commit()
                logger.warning(
                    "startup: cancelled %d orphaned queue entry(ies) with terminal runs",
                    len(orphaned),
                )
    except Exception:  # noqa: BLE001 - DB may not be migrated yet; not fatal
        logging.getLogger("noodle").exception(
            "startup: _mark_interrupted_runs failed — stale 'running' rows may persist"
        )


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
    # Pin the run-event broker transport once: Redis (fans out across
    # replicas) when reachable, else the in-process buffer. Doing this at
    # startup — rather than probing Redis on every publish/subscribe — is what
    # stops publish() and subscribe() from ever choosing different transports.
    from app.services.events import broker as event_broker

    await event_broker.connect()
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


# resolve_org is a global dependency so every request — including
# unauthenticated reads with no require_permission dependency — sets the
# request-scoped org context that the ORM filter and Postgres RLS read.
# No-op (returns None, sets nothing) while multi_tenancy_enabled is off.
from app.security import resolve_org  # noqa: E402

app = FastAPI(
    title="Noodle API",
    version="0.0.1",
    lifespan=lifespan,
    dependencies=[Depends(resolve_org)],
)

# NOTE: CORSMiddleware is added LAST (see bottom of this block) so it is the
# OUTERMOST middleware. Starlette's add_middleware prepends, so the last call
# wins the outer position. CORS must be outermost so that short-circuit error
# responses from auth_gate (401) and _body_size_limit (413) still carry the
# Access-Control-Allow-Origin header — otherwise the browser reports an opaque
# CORS failure instead of the real status and the SPA can't react (e.g. redirect
# to login on session expiry).


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a structured JSON error for any unhandled exception instead of a
    plain-text 500, so clients can always parse the response body."""
    logger = logging.getLogger("noodle")
    req_id = request.headers.get("x-request-id", "")
    logger.exception("Unhandled exception on %s %s (req=%s)", request.method, request.url.path, req_id)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": req_id},
    )


_MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MiB — generous for graph payloads


@app.middleware("http")
async def _body_size_limit(request: Request, call_next):
    """Reject requests whose Content-Length header exceeds the cap.

    Large unchecked bodies (e.g. a deeply-nested graph with huge embedded
    blobs) could exhaust memory before FastAPI parses the JSON. This guard
    uses the declared Content-Length; a chunked request with no
    Content-Length header gets through but is still bounded by the OS
    TCP receive buffer and the client's connection, so it's an acceptable
    trade-off without adding streaming body inspection overhead.
    """
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large"},
                )
        except ValueError:
            pass
    return await call_next(request)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Attach X-Request-ID and Content-Security-Policy to every response."""
    req_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    # Tight CSP for the API (no HTML rendered here, only JSON).  Relaxed for
    # the docs UI so Swagger/ReDoc can load their CDN assets.
    if request.url.path.startswith(("/docs", "/redoc")):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' https://cdn.jsdelivr.net https://unpkg.com; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com"
        )
    else:
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'"
        )
    return response

_AUTH_EXEMPT_PREFIXES = (
    "/auth",
    "/health",
    "/webhook",
    "/webhook-test",
    "/provider-webhook",
    "/internal",
    "/runner-pools/ws",  # agent runner WS — uses its own token query param
    # OAuth provider redirect lands here via top-level browser navigation with
    # no bearer token. The callback is authenticated by the HMAC-signed `state`
    # param (see app.services.oauth.decode_oauth_state), so it is safe to exempt
    # — without this, OAuth credential connect is broken whenever auth_required.
    "/credentials/oauth/callback",
)
# Public surface: landing page + OpenAPI schema/docs (so unauthenticated users
# can discover the API), and a favicon for browsers. /metrics and
# /system/status used to be public but were tightened in the 2026-05-30 QA
# sweep so deployment topology isn't leaked to anonymous callers; operators
# scraping Prometheus should configure a bearer token in their scrape config.
_AUTH_EXEMPT_PATHS = {
    "/",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/favicon.ico",
}


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
        # Allow ``?token=`` on routes that are typically opened via plain
        # browser navigation (artifact downloads, ws upgrade is handled
        # elsewhere). The query token is the same bearer token.
        query_token = request.query_params.get("token")
        if query_token and verify_token(query_token) is not None:
            return await call_next(request)
        return JSONResponse(
            {"detail": "Authentication required"}, status_code=401
        )
    if verify_token(header.removeprefix("Bearer ")) is None:
        return JSONResponse(
            {"detail": "Invalid or expired token"}, status_code=401
        )
    return await call_next(request)


# Registered last → outermost middleware (Starlette prepends each add_middleware).
# This guarantees CORS headers are present even on error responses produced by
# the middlewares above. See the note next to FastAPI(...) construction.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(nodes.router)
app.include_router(environments.router)
# Webhook ingress role:
#   ingress   -> serve public /webhook/{path} (default; this process is the
#                ingress tier — validate fast, enqueue on the durable queue)
#   inline    -> serve public /webhook/{path} (simple single-user setup)
#   disabled  -> do NOT mount public /webhook/{path} (control-plane only)
# The editor capture paths (/webhook-test/*) are always mounted so the builder
# UX works on any replica, including a disabled (control-plane-only) one.
app.include_router(webhooks.router)
if settings.webhook_role != "disabled":
    app.include_router(webhooks.production_router)
    app.include_router(provider_webhooks.router)
app.include_router(workflows.router)
app.include_router(runs.router)
app.include_router(chat.router)
app.include_router(chat_public.router)
app.include_router(deployments.router)
app.include_router(code_modules.router)
app.include_router(internal.router)
app.include_router(export.router)
app.include_router(auth.router)
app.include_router(credentials.router)
app.include_router(audit.router)
app.include_router(auth.users_router)
app.include_router(artifacts.router)
app.include_router(ops.router)
app.include_router(pinned.router)
app.include_router(system_settings.router)
app.include_router(runner_pools.router)
app.include_router(expressions.router)


@app.get("/")
async def root() -> dict:
    return {"name": "Noodle API", "version": "0.0.1"}
