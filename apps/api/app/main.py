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

from app import tracing
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
    mcp,
    nodes,
    ops,
    orgs,
    pinned,
    provider_webhooks,
    runner_pools,
    runs,
    system_settings,
    webhooks,
    workflows,
)
from app.services import expr_preview
from app.services.crypto import verify_token
from app.services.events import broker_reaper_loop
from app.services.queue import run_queue_dispatch_loop
from app.services.remote_dispatch import (
    cloud_idle_terminate_loop,
    dispatcher,
    runner_heartbeat_loop,
)
from app.services.retention import retention_loop
from app.services.runner import (
    drain_active_runs,
    process_isolator,
    shutdown_active_runs,
)
from app.services.runtime_pool import idle_reaper_loop
from app.services.runtime_pool import pool as runtime_pool
from app.services.triggers import scheduler_loop


async def _ensure_global_environment() -> None:
    """Make sure exactly one global environment exists."""
    logger = logging.getLogger("noodle")
    try:
        async with SessionLocal() as session:
            existing = (
                await session.scalars(select(Environment).where(Environment.is_global.is_(True)))
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
        from app.tenancy import run_as_system

        with run_as_system():
            async with SessionLocal() as session:
                result = await session.scalars(
                    select(Run).where(Run.status.in_(("running", "waiting")))
                )
                runs = result.all()
                if runs:
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
    # Split-topology misconfigurations abort startup (program A1) — a control
    # plane that silently executed runs, or used in-process events, would
    # corrupt the worker topology rather than degrade it.
    topology_errors = settings.dispatch_topology_errors()
    if settings.dispatch_role == "worker":
        topology_errors.append(
            "dispatch_role=worker is the standalone worker entrypoint "
            "(python -m app.worker_main); API replicas use inline, control, "
            "or disabled."
        )
    if topology_errors:
        raise RuntimeError("startup aborted:\n  - " + "\n  - ".join(topology_errors))
    # ``inline`` owns local execution (warm subprocess pool, sandbox, idle
    # reaper, interrupted-run recovery). ``control`` runs a dispatch loop but
    # only for agent/kubernetes (no local pool), so local-execution startup is
    # gated on ``dispatch_inline`` while the loop itself runs for both.
    dispatch_inline = settings.dispatch_role == "inline"
    run_dispatch_loop = settings.dispatch_role in ("inline", "control")

    # Pin the app's default timezone — explicit .env override wins, otherwise
    # ask the OS. This becomes the fallback for any schedule_trigger that
    # doesn't set its own ``tz``.
    if not settings.app_timezone:
        settings.app_timezone = _detect_local_timezone()
    logging.getLogger("noodle").info("noodle app timezone: %s", settings.app_timezone)

    await _ensure_global_environment()

    # Licensing: reconcile config-requested capabilities against the active
    # license BEFORE the sandbox/MT policy runs. A flag set without the
    # entitlement (e.g. multi_tenancy_enabled on Community) is forced off here
    # with a logged warning rather than failing to boot.
    from app.services.licensing import reconcile_capabilities

    for _warning in await reconcile_capabilities():
        logging.getLogger("noodle").warning("licensing: %s", _warning)

    # Phase D: refuse unsafe MT configurations outright; probe the container
    # sandbox only where this process can dispatch runs.
    from app.services.sandbox_policy import enforce_sandbox_policy

    enforce_sandbox_policy()
    if dispatch_inline:
        from app.services.sandbox_pool import init_sandbox

        await init_sandbox()

    if dispatch_inline:
        # Only the process that owns execution may declare runs interrupted;
        # in split topologies workers own runs and lease-expiry recovers them.
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
    from app.tenancy import run_as_system

    def _as_system(loop_fn):
        """Background loops operate across ALL orgs: with multi-tenancy on,
        an unscoped task would otherwise be pinned to the default org by the
        fail-closed context fallback and silently skip every other tenant's
        schedules/queue entries/retention. No-op while the flag is off."""

        async def system_loop():
            with run_as_system():
                await loop_fn()

        return system_loop

    def _make_loop_task(loop_fn, lock_name: str):
        if not settings.enable_inprocess_scheduler:
            return None
        if settings.scheduler_role == "disabled":
            return None
        if settings.scheduler_role == "leader":
            return asyncio.create_task(
                run_with_leader_election(_as_system(loop_fn), name=lock_name)
            )
        return asyncio.create_task(_as_system(loop_fn)())

    scheduler = _make_loop_task(scheduler_loop, "noodle.scheduler")
    # Retention prune is gated on the same flag — it's another in-process
    # loop and we want at most one owner across replicas.
    retention = _make_loop_task(retention_loop, "noodle.retention")
    # Idle reaper closes warm runner subprocesses that have been sitting
    # unused past ``runner_idle_seconds``. Independent of the scheduler flag
    # because every replica should reap its own pool.
    reaper = (
        asyncio.create_task(_as_system(idle_reaper_loop)())
        if dispatch_inline and settings.use_subprocess_runner and settings.runner_idle_seconds > 0
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
    # The dispatch loop runs where execution is owned: ``inline`` (everything)
    # and ``control`` (agent/kubernetes only — those WebSockets terminate on
    # this replica). ``disabled`` enqueues only; workers run their own loop via
    # app.worker_main. The agent-WS heartbeat and cloud idle-terminate loops
    # stay on the API regardless — agent connections terminate here.
    queue_loop = (
        asyncio.create_task(_as_system(run_queue_dispatch_loop)()) if run_dispatch_loop else None
    )
    cloud_idle = asyncio.create_task(_as_system(cloud_idle_terminate_loop)())
    heartbeat = asyncio.create_task(_as_system(runner_heartbeat_loop)())
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
    from app.services.sandbox_pool import pool as _sandbox_pool

    await _bounded(_sandbox_pool.flush())
    await _bounded(expr_preview.shutdown())
    tracing.flush()  # push buffered spans before the process winds down
    process_isolator.shutdown()  # sync + fast: wait=False pool teardown
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

# A5: tracing is initialised at import so FastAPI/SQLAlchemy instrumentation
# wraps everything from the first request. All three calls no-op when
# settings.otel_enabled is false.
tracing.setup_tracing("noodle-api")
tracing.instrument_app(app)
tracing.instrument_sqlalchemy(engine)

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
    logger.exception(
        "Unhandled exception on %s %s (req=%s)", request.method, request.url.path, req_id
    )
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
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response


_AUTH_EXEMPT_PREFIXES = (
    "/auth",
    "/health",
    "/webhook",
    "/webhook-test",
    "/provider-webhook",
    "/internal",
    "/runner-pools/ws",  # agent runner WS — uses its own token query param
    "/runner-pools/wheels",  # public OSS wheel index (uv sends no auth header)
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


_CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Paths that use their own request-level auth and must never be CSRF-blocked.
_CSRF_EXEMPT_PREFIXES = (
    "/health",
    "/webhook",
    "/webhook-test",
    "/provider-webhook",
    "/internal",
    "/runner-pools/ws",
    "/credentials/oauth/callback",
)
# Specific /auth endpoints that are safe without CSRF: they have no session
# cookie yet (bootstrapping login/register) or are read-only probes. Matching
# is method-blind, so never list a path whose unsafe methods must stay
# protected (GETs are already exempt via _CSRF_SAFE_METHODS — e.g. listing
# "/auth/users" here would have exempted POST /auth/users, the admin
# create-user endpoint, from CSRF).
_CSRF_EXEMPT_PATHS_EXACT = {
    "/auth/login",
    "/auth/register",
    "/auth/required",
}


@app.middleware("http")
async def _csrf_gate(request: Request, call_next):
    """Enforce CSRF double-submit for cookie-authenticated state-changing requests.

    Algorithm:
      1. Read-only methods (GET/HEAD/OPTIONS) are always safe — skip.
      2. Auth-exempt prefixes handle their own security — skip.
      3. If the request carries ``Authorization: Bearer`` → Bearer is not
         forgeable via cookie injection → skip CSRF check.
      4. If the ``noodle_session`` httpOnly cookie is absent → anonymous or
         Bearer-only client → skip (the route's own auth will 401 if needed).
      5. Otherwise: verify that the ``X-CSRF-Token`` header matches the
         ``noodle_csrf`` non-httpOnly cookie set by the login endpoint.
         Mismatch → 403. This pattern prevents CSRF without a server-side
         token store; the attacker can't read the ``noodle_csrf`` cookie
         (same-site + domain restrictions) so they can't forge the header.
    """
    if request.method in _CSRF_SAFE_METHODS:
        return await call_next(request)

    path = request.url.path
    if (
        path in _AUTH_EXEMPT_PATHS
        or path in _CSRF_EXEMPT_PATHS_EXACT
        or any(path == p or path.startswith(f"{p}/") for p in _CSRF_EXEMPT_PREFIXES)
    ):
        return await call_next(request)

    # Bearer auth is CSRF-safe — skip enforcement.
    if request.headers.get("authorization", "").startswith("Bearer "):
        return await call_next(request)

    # No session cookie → anonymous / Bearer-only path → not a cookie session.
    session_cookie = request.cookies.get(settings.session_cookie_name)
    if not session_cookie:
        return await call_next(request)

    # Cookie-auth: enforce CSRF double-submit.
    csrf_cookie = request.cookies.get(settings.csrf_cookie_name, "")
    csrf_header = request.headers.get(settings.csrf_header_name, "")
    if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
        return JSONResponse(
            {"detail": "CSRF token missing or invalid"},
            status_code=403,
        )
    return await call_next(request)


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
    # Accept cookie-based auth (httpOnly session cookie) as an alternative to
    # Bearer — verify the token value is valid before passing the request on.
    if not header.startswith("Bearer "):
        cookie_token = request.cookies.get(settings.session_cookie_name, "")
        if cookie_token and verify_token(cookie_token) is not None:
            return await call_next(request)
        # Allow ``?token=`` on routes that are typically opened via plain
        # browser navigation (artifact downloads, ws upgrade is handled
        # elsewhere). The query token is the same bearer token.
        query_token = request.query_params.get("token")
        if query_token and verify_token(query_token) is not None:
            return await call_next(request)
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    if verify_token(header.removeprefix("Bearer ")) is None:
        return JSONResponse({"detail": "Invalid or expired token"}, status_code=401)
    return await call_next(request)


@app.middleware("http")
async def _tenant_context_scope(request: Request, call_next):
    """Clear request tenant context even when auth/org resolution fails."""
    from app.tenancy import current_org_id

    token = current_org_id.set(None)
    try:
        return await call_next(request)
    finally:
        current_org_id.reset(token)


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
if settings.mcp_server_enabled:
    app.include_router(mcp.router)
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
app.include_router(orgs.router)
app.include_router(expressions.router)


@app.get("/")
async def root() -> dict:
    return {"name": "Noodle API", "version": "0.0.1"}
