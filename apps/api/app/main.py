import asyncio
import contextlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.requests import ClientDisconnect

from app import tracing
from app.config import settings
from app.exceptions import ServiceError
from app.logging import bind_request_context, configure_logging, reset_request_context
from nodyra import __version__ as NODYRA_VERSION

# Install structured JSON logging before anything logs (H4). Operators can opt
# back into plain text with ``log_json=False``.
if settings.log_json:
    configure_logging()
from app.db import SessionLocal, engine
from app.models import Environment, Run, RunQueueEntry
from app.redis_client import redis_client
from app.routers import (
    admin,
    agentic_build,
    artifacts,
    audit,
    auth,
    billing,
    chat,
    chat_public,
    code_modules,
    credentials,
    deployments,
    environments,
    export,
    expressions,
    folders,
    health,
    internal,
    mcp,
    mcp_approvals,
    mcp_connections,
    mcp_gateway,
    node_registry,
    nodes,
    ops,
    orgs,
    pinned,
    provider_webhooks,
    runner_pools,
    runs,
    system_settings,
    templates,
    webhooks,
    workflows,
)
from app.routers import (
    github_sync as github_sync_router,
)
from app.security import get_client_ip
from app.services import expr_preview
from app.services.audit import audit_webhook_loop
from app.services.docker_workers import docker_workers_autoscale_loop
from app.services.environment_builds import run_environment_build_dispatch_loop
from app.services.events import broker_reaper_loop
from app.services.execution_actor import ExecutionActorMiddleware
from app.services.ghost_cleanup import ghost_cleanup_loop
from app.services.github_sync_jobs import github_sync_dispatch_loop
from app.services.json_responses import NodyraJSONResponse
from app.services.license_refresh import license_refresh_loop, refresh_enabled
from app.services.queue import run_queue_dispatch_loop
from app.services.remote_dispatch import (
    cloud_idle_terminate_loop,
    dispatcher,
    runner_heartbeat_loop,
)
from app.services.replica_health import replica_heartbeat_loop
from app.services.retention import retention_loop
from app.services.runner import (
    drain_active_runs,
    process_isolator,
    shutdown_active_runs,
)
from app.services.runtime_pool import idle_reaper_loop, pool_autoscaler_loop
from app.services.runtime_pool import pool as runtime_pool
from app.services.stuck_run_detector import stuck_run_detector_loop
from app.services.triggers import scheduler_loop


async def _ensure_global_environment() -> None:
    """Make sure exactly one global environment exists.

    Runs unscoped (``run_as_system``) so the org filter in multi-tenant mode
    doesn't limit the SELECT to the default org, which would cause every
    other org to appear to have no global environment.
    """
    logger = logging.getLogger("nodyra")
    try:
        from app.tenancy import run_as_system

        with run_as_system():
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
    logger = logging.getLogger("nodyra")
    try:
        from app.tenancy import run_as_system

        with run_as_system():
            async with SessionLocal() as session:
                result = await session.scalars(
                    select(Run).where(
                        Run.status.in_(("running", "waiting")),
                        # Skip remote-pool runs: they are managed by lease expiry.
                        # The worker heartbeats extend the lease; if the worker dies
                        # the lease expires and the run is re-queued automatically.
                        Run.runner_pool_id.is_(None),
                    )
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
                        entry.lease_token = None
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
                        entry.lease_token = None
                        entry.lease_expires_at = None
                    await session.commit()
                    logger.warning(
                        "startup: cancelled %d orphaned queue entry(ies) with terminal runs",
                        len(orphaned),
                    )
    except Exception:  # noqa: BLE001 - DB may not be migrated yet; not fatal
        logging.getLogger("nodyra").exception(
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
    # The core validator hook is replaceable by embedders. Reassert the API's
    # metrics observer at every process startup so validation blocks cannot
    # silently disappear after a temporary hook or module reload.
    from app.services.metrics import install_code_validation_hook

    install_code_validation_hook()
    # Split-topology misconfigurations abort startup (program A1) — a control
    # plane that silently executed runs, or used in-process events, would
    # corrupt the worker topology rather than degrade it.
    # Fail-closed security guard (AUTH-1/AUTH-3): default secret / missing
    # internal token under an enforced boundary aborts startup outright.
    security_errors = settings.security_startup_errors()
    if security_errors:
        raise RuntimeError("startup aborted:\n  - " + "\n  - ".join(security_errors))
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
    logging.getLogger("nodyra").info("nodyra app timezone: %s", settings.app_timezone)

    await _ensure_global_environment()

    # Licensing: reconcile config-requested capabilities against the active
    # license BEFORE the sandbox/MT policy runs. A flag set without the
    # entitlement (e.g. multi_tenancy_enabled on Community) is forced off here
    # with a logged warning rather than failing to boot.
    from app.services.licensing import reconcile_capabilities

    for _warning in await reconcile_capabilities():
        logging.getLogger("nodyra").warning("licensing: %s", _warning)

    from app.tenancy import assert_safe_postgres_role

    await assert_safe_postgres_role(engine)

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
            logging.getLogger("nodyra").exception(
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

        async def system_loop(*args, **kwargs):
            with run_as_system():
                await loop_fn(*args, **kwargs)

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

    scheduler = _make_loop_task(scheduler_loop, "nodyra.scheduler")
    # Retention prune is gated on the same flag — it's another in-process
    # loop and we want at most one owner across replicas.
    retention = _make_loop_task(retention_loop, "nodyra.retention")
    # Idle reaper closes warm runner subprocesses that have been sitting
    # unused past ``runner_idle_seconds``. Independent of the scheduler flag
    # because every replica should reap its own pool.
    reaper = (
        asyncio.create_task(_as_system(idle_reaper_loop)())
        if dispatch_inline and settings.use_subprocess_runner and settings.runner_idle_seconds > 0
        else None
    )
    autoscaler = (
        asyncio.create_task(_as_system(pool_autoscaler_loop)())
        if dispatch_inline and settings.use_subprocess_runner and settings.pool_autoscale_enabled
        else None
    )
    docker_autoscale = (
        asyncio.create_task(_as_system(docker_workers_autoscale_loop)())
        if dispatch_inline
        else None
    )
    # Pin the run-event broker transport once: Redis (fans out across
    # replicas) when reachable, else the in-process buffer. Doing this at
    # startup — rather than probing Redis on every publish/subscribe — is what
    # stops publish() and subscribe() from ever choosing different transports.
    from app.services.events import broker as event_broker
    from app.services.events import workflow_broker

    await event_broker.connect()
    await workflow_broker.connect()
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
    environment_builds = (
        asyncio.create_task(_as_system(run_environment_build_dispatch_loop)())
        if settings.dispatch_role in ("inline", "control")
        else None
    )
    cloud_idle = asyncio.create_task(_as_system(cloud_idle_terminate_loop)())
    heartbeat = asyncio.create_task(_as_system(runner_heartbeat_loop)())
    github_sync = asyncio.create_task(_as_system(github_sync_dispatch_loop)())
    ghost_cleanup = asyncio.create_task(_as_system(ghost_cleanup_loop)())
    audit_webhook = (
        asyncio.create_task(_as_system(audit_webhook_loop)())
        if settings.audit_webhook_url and settings.audit_webhook_secret
        else None
    )
    # Renews this instance's licence before it lapses. Started only when a
    # licence server is configured, so an air-gapped deployment never reaches
    # out and keeps verifying offline exactly as before.
    license_refresh = (
        asyncio.create_task(_as_system(license_refresh_loop)())
        if refresh_enabled()
        else None
    )
    replica_heartbeat = asyncio.create_task(_as_system(replica_heartbeat_loop)(role="api"))
    stuck_detector = (
        asyncio.create_task(_as_system(stuck_run_detector_loop)()) if dispatch_inline else None
    )
    yield
    # Graceful drain on shutdown: stop the dispatch loop from leasing new
    # entries, give in-flight runs a bounded window to finish, then
    # cancel any laggards. ``queue_drain`` may already be true if an
    # operator pre-drained via /ops/drain; restore the prior value after
    # teardown so the setting isn't sticky for the next lifespan
    # (matters for tests that reuse the process).
    _prior_drain = settings.queue_drain
    settings.queue_drain = True
    for task in (
        scheduler,
        retention,
        reaper,
        autoscaler,
        docker_autoscale,
        broker_reaper,
        queue_loop,
        environment_builds,
        cloud_idle,
        heartbeat,
        github_sync,
        ghost_cleanup,
        audit_webhook,
        license_refresh,
        replica_heartbeat,
        stuck_detector,
    ):
        if task is None:
            continue
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    drain_timeout = max(settings.queue_dispatch_shutdown_timeout_seconds, 0.0)
    if drain_timeout > 0:
        leftover = await drain_active_runs(drain_timeout)
        if leftover:
            logging.getLogger("nodyra").warning(
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
from app.security import _user_from_session_token, resolve_org  # noqa: E402


async def _gate_token_valid(token: str, *, client_ip: str = "") -> bool:
    """C1: a token passes the gate only when it is well-formed, unexpired, AND
    not revoked by the user's ``sessions_valid_after`` cutoff.

    ``verify_token`` alone checks only signature/exp, so routes gated solely by
    this middleware (e.g. ``?token=`` artifact downloads, which carry no
    ``current_user`` dependency) would otherwise keep honouring a token after a
    "log out everywhere". Resolving the user here enforces revocation centrally.
    """
    if not token:
        return False
    async with SessionLocal() as session:
        return await _user_from_session_token(token, session, client_ip=client_ip) is not None


app = FastAPI(
    title="Nodyra API",
    version=NODYRA_VERSION,
    lifespan=lifespan,
    dependencies=[Depends(resolve_org)],
    # Non-finite floats (NaN/Inf) are not valid JSON; sanitize every REST
    # response so legacy run outputs containing them degrade to null instead
    # of crashing json.dumps and returning a bare 500.
    default_response_class=NodyraJSONResponse,
)

# A5: tracing is initialised at import so FastAPI/SQLAlchemy instrumentation
# wraps everything from the first request. All three calls no-op when
# settings.otel_enabled is false.
tracing.setup_tracing("nodyra-api")
tracing.instrument_app(app)
tracing.instrument_sqlalchemy(engine)

# NOTE: CORSMiddleware is added LAST (see bottom of this block) so it is the
# OUTERMOST middleware. Starlette's add_middleware prepends, so the last call
# wins the outer position. CORS must be outermost so that short-circuit error
# responses from auth_gate (401) and _body_size_limit (413) still carry the
# Access-Control-Allow-Origin header — otherwise the browser reports an opaque
# CORS failure instead of the real status and the SPA can't react (e.g. redirect
# to login on session expiry).


@app.exception_handler(ServiceError)
async def _service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    """Convert ServiceError subclasses to typed HTTP responses via ExceptionMiddleware.

    Registered on ServiceError (not Exception) so Starlette routes it through
    ExceptionMiddleware (MRO walk) rather than ServerErrorMiddleware, which
    would call the handler AND still re-raise the exception in test mode.
    """
    return JSONResponse(
        status_code=exc.http_status,
        content={"detail": str(exc), **exc.detail},
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a structured JSON error for any unhandled exception instead of a
    plain-text 500, so clients can always parse the response body."""
    logger = logging.getLogger("nodyra")
    req_id = request.headers.get("x-request-id", "")
    logger.exception(
        "Unhandled exception on %s %s (req=%s)", request.method, request.url.path, req_id
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": req_id},
    )


_MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MiB — generous for graph payloads
_MULTIPART_OVERHEAD_BYTES = 1024 * 1024

# B-10: cap concurrent chunked-body reads to bound total in-flight memory.
# 20 concurrent readers * 10 MiB cap = 200 MiB ceiling (less in practice; most
# requests send Content-Length and take the fast path, never touching this).
_MAX_CHUNKED_BODY_READERS = 20
# Counter is safe without a lock: asyncio is single-threaded and we only
# increment/decrement at yield points (no concurrent mutation between checks).
_chunked_body_readers: int = 0


def _json_contains_nul(value: object) -> bool:
    """Return whether a decoded JSON document contains PostgreSQL-invalid NUL text."""
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            if "\x00" in current:
                return True
        elif isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return False


def _request_body_limit(path: str) -> int:
    if path in {"/artifacts/upload", "/runner-pools/artifact-upload"}:
        artifact_limit = settings.max_artifact_bytes
        if artifact_limit > 0:
            return artifact_limit + _MULTIPART_OVERHEAD_BYTES
    return _MAX_BODY_BYTES


@app.middleware("http")
async def _body_size_limit(request: Request, call_next):
    """Reject requests whose declared or streamed body exceeds the cap.

    Large unchecked bodies (e.g. a deeply-nested graph with huge embedded
    blobs) could exhaust memory before FastAPI parses the JSON. Declared bodies
    are rejected immediately; chunked bodies are consumed only up to the same
    route-specific limit and then cached for FastAPI's downstream parser.
    """
    global _chunked_body_readers
    # PostgreSQL rejects NUL and other C0 controls in text predicates. Reject
    # them at the HTTP boundary so malformed filters consistently produce a
    # client error instead of leaking dialect-specific 500 responses.
    query_params = getattr(request, "query_params", None)
    if query_params is not None:
        for key, value in query_params.multi_items():
            if any(ord(char) < 0x20 or ord(char) == 0x7F for char in f"{key}{value}"):
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Query parameters contain invalid control characters"},
                )
    limit = _request_body_limit(request.url.path)
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > limit:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large"},
                )
        except ValueError:
            content_length = None
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    is_json = content_type == "application/json" or content_type.endswith("+json")
    if is_json:
        # Read JSON through the same hard cap even when Content-Length is
        # declared; a client must not be able to lie about that header and make
        # the downstream parser allocate an unbounded body. Cache the bytes so
        # FastAPI can parse them normally after this boundary check.
        chunks: list[bytes] = []
        total = 0
        try:
            async for chunk in request.stream():
                total += len(chunk)
                if total > limit:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Request body too large"},
                    )
                chunks.append(chunk)
        except ClientDisconnect:
            return Response(status_code=499)
        request._body = b"".join(chunks)  # noqa: SLF001 - Starlette body cache
        try:
            decoded_json = json.loads(request._body) if request._body else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Preserve FastAPI's standard malformed-JSON response.
            decoded_json = None
        if _json_contains_nul(decoded_json):
            return JSONResponse(
                status_code=400,
                content={"detail": "JSON strings contain an invalid NUL character"},
            )
    elif content_length is None:
        # Transfer-Encoding: chunked has no declared size. Read only up to the
        # route-specific cap, cache the bounded body for downstream parsers, and
        # reject before request.body()/multipart parsing can grow without limit.
        if _chunked_body_readers >= _MAX_CHUNKED_BODY_READERS:
            return JSONResponse(
                status_code=503,
                content={"detail": "Server busy: too many concurrent large uploads"},
                headers={"Retry-After": "1"},
            )
        _chunked_body_readers += 1
        try:
            chunks: list[bytes] = []
            total = 0
            try:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > limit:
                        return JSONResponse(
                            status_code=413,
                            content={"detail": "Request body too large"},
                        )
                    chunks.append(chunk)
            except ClientDisconnect:
                return Response(status_code=499)
            request._body = b"".join(chunks)  # noqa: SLF001 - Starlette body cache
        finally:
            _chunked_body_readers -= 1
    return await call_next(request)


@app.middleware("http")
async def _metrics_middleware(request: Request, call_next):
    """Record HTTP request count and duration for Prometheus metrics."""
    import time as _time

    from app.services.metrics import (
        _normalize_path,
        http_request_duration_seconds,
        http_requests_total,
    )

    start = _time.monotonic()
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        elapsed = _time.monotonic() - start
        norm_path = _normalize_path(request.url.path)
        http_requests_total.inc(
            method=request.method,
            path=norm_path,
            status=str(getattr(response, "status_code", 500)),
        )
        http_request_duration_seconds.observe(
            elapsed,
            method=request.method,
            path=norm_path,
        )


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Attach request correlation and defensive browser headers to every response.

    Also binds the request id into the logging context (H4) so every log line
    emitted while handling this request carries ``request_id`` for correlation.
    The org id is bound lazily by ``resolve_org`` once tenancy is resolved.
    """
    req_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    tokens = bind_request_context(request_id=req_id)
    try:
        response = await call_next(request)
    finally:
        reset_request_context(tokens)
    response.headers["X-Request-ID"] = req_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    )
    # Tight CSP for the API (no HTML rendered here, only JSON).  Relaxed for
    # the docs UI so Swagger/ReDoc can load their CDN assets.
    if "content-security-policy" in response.headers:
        pass
    elif request.url.path.startswith(("/docs", "/redoc")):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' https://cdn.jsdelivr.net https://unpkg.com; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com"
        )
    else:
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        )
    return response


_AUTH_EXEMPT_PREFIXES = (
    "/mcp-gateway",  # Every gateway route requires authenticated scoped access.
    "/auth",
    "/.well-known",
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
    # Public chat performs its own open/secret-link/login policy from the
    # published Chat Trigger. Keeping it behind the global gate would make
    # open and secret-link chat pages unusable whenever AUTH_REQUIRED=true.
    "/chat/p",
)
# Public surface: landing page + OpenAPI schema/docs (so unauthenticated users
# can discover the API), and a favicon for browsers. /metrics and
# /system/status used to be public but were tightened in the 2026-05-30 QA
# sweep so deployment topology isn't leaked to anonymous callers; operators
# scraping Prometheus should configure a bearer token in their scrape config.
_AUTH_EXEMPT_PATHS = {
    "/",
    # Signature-authenticated (Stripe) and token-authenticated (a customer
    # instance renewing its own licence). Neither caller is a Nodyra user.
    "/billing/webhook",
    "/billing/license",
    "/mcp",  # MCP performs its own bearer auth and standards-compliant challenge.
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
    # Stripe holds no Nodyra credential and cannot send a CSRF token; the
    # endpoint authenticates by HMAC signature over the raw request body.
    "/billing/webhook",
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
      4. If the ``nodyra_session`` httpOnly cookie is absent → anonymous or
         Bearer-only client → skip (the route's own auth will 401 if needed).
      5. Otherwise: verify that the ``X-CSRF-Token`` header matches the
         ``nodyra_csrf`` non-httpOnly cookie set by the login endpoint.
         Mismatch → 403. This pattern prevents CSRF without a server-side
         token store; the attacker can't read the ``nodyra_csrf`` cookie
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
    client_ip = get_client_ip(request) if settings.auth_bind_token_to_ip else ""
    # Accept cookie-based auth (httpOnly session cookie) as an alternative to
    # Bearer — verify the token value is valid before passing the request on.
    if not header.startswith("Bearer "):
        cookie_token = request.cookies.get(settings.session_cookie_name, "")
        if cookie_token and await _gate_token_valid(cookie_token, client_ip=client_ip):
            return await call_next(request)
        # Allow ``?token=`` on routes that are typically opened via plain
        # browser navigation (artifact downloads, ws upgrade is handled
        # elsewhere). The query token is the same bearer token.
        query_token = request.query_params.get("token")
        if query_token and await _gate_token_valid(query_token, client_ip=client_ip):
            return await call_next(request)
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    if not await _gate_token_valid(header.removeprefix("Bearer "), client_ip=client_ip):
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
app.add_middleware(ExecutionActorMiddleware)
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
app.include_router(folders.router)
app.include_router(workflows.router)
app.include_router(workflows.ws_router)
app.include_router(agentic_build.router)
app.include_router(runs.router)
app.include_router(chat.router)
app.include_router(chat_public.router)
app.include_router(deployments.router)
app.include_router(code_modules.router)
app.include_router(internal.router)
if settings.mcp_server_enabled:
    app.include_router(mcp.router)
app.include_router(export.router)
app.include_router(templates.router)
app.include_router(auth.router)
app.include_router(credentials.router)
app.include_router(admin.router)
app.include_router(audit.router)
app.include_router(auth.users_router)
app.include_router(artifacts.router)
app.include_router(ops.router)
app.include_router(pinned.router)
app.include_router(system_settings.router)
app.include_router(runner_pools.router)
app.include_router(orgs.router)
app.include_router(expressions.router)
app.include_router(mcp_connections.router)
app.include_router(mcp_gateway.router)
app.include_router(mcp_approvals.router)
app.include_router(github_sync_router.router, prefix="/api")
app.include_router(node_registry.router)
app.include_router(billing.router)
if settings.license_issuer_enabled:
    # Issuance, checkout and the Stripe webhook exist ONLY on the instance the
    # vendor runs as its licence server. Not mounting them elsewhere means a
    # customer deployment cannot expose them even if it is handed a signing key
    # by mistake.
    app.include_router(billing.issuer_router)


@app.get("/")
async def root() -> dict:
    return {"name": "Nodyra API", "version": NODYRA_VERSION}
