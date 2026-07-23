"""Webhook ingress.

* ``/webhook-test/{path}`` captures requests so the editor can show what a
  webhook node receives while you build a workflow. Only active while a
  listen session is registered via ``POST /webhook-test/{path}/listen``;
  requests before that return 404.
* ``/webhook/{path}`` is the production URL — it dispatches a run of every
  active workflow that starts with a matching webhook node.

Capture buffer: every editor "Listen" session pushes a new path through here,
so the in-memory ``_captured`` dict used to grow unbounded for the life of
the API process. It's now bounded two ways: each entry carries a timestamp
and is evicted after ``WEBHOOK_CAPTURE_TTL_SECONDS``, and the dict is capped
at ``WEBHOOK_CAPTURE_MAX_ENTRIES`` with oldest-first eviction.

Listen sessions: the editor's Listen button registers a session via the
(authenticated) start endpoint; the test URL handler checks the session store
before processing any request so external tools (Postman, curl) cannot hit
the test URL without the editor open. Sessions are kept in Redis when
configured (so the gate holds across replicas) with an in-process fallback.
"""

import json
import logging
import time
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.db import get_session
from app.security import get_client_ip, require_permission
from app.services.triggers import dispatch_webhook, wait_for_webhook_result
from app.tenancy import DEFAULT_ORG_ID, active_org_id, run_as_org

logger = logging.getLogger(__name__)

# Editor/test capture paths (`/webhook-test/*`). Always mounted so the builder
# UX works even on a control-plane-only replica (``webhook_role=disabled``).
router = APIRouter(tags=["webhooks"])

# Public production path (`/webhook/{path}`). Mounted only when
# ``webhook_role != "disabled"`` (see ``app/main.py``), so an editor-only
# replica rejects production webhooks with 404 instead of silently matching.
production_router = APIRouter(tags=["webhooks"])

# Stored as ``(monotonic_seen_at, payload)`` so we can age entries out
# without paying for a separate timestamp dict.
_captured: dict[str, tuple[float, dict]] = {}
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
# OPTIONS is always allowed so CORS preflight on webhook URLs works regardless
# of which HTTP methods the node configures. The node's ``http_method``
# parameter still gates actual dispatch — OPTIONS merely returns 204.

WEBHOOK_CAPTURE_TTL_SECONDS = 5 * 60  # 5 min — typical Listen-then-test loop
WEBHOOK_CAPTURE_MAX_ENTRIES = 256

# Body size cap for webhook ingress. Individual endpoint handlers further
# validate against this; it mirrors the global ``_MAX_BODY_BYTES`` in main.py.
_WEBHOOK_MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MiB


def _normalize_webhook_path(path: str) -> str:
    """Normalize a webhook path to prevent trivial bypasses.

    - Strips trailing slashes so ``/webhook/orders/`` and ``/webhook/orders`` match the same route.
    - Collapses duplicate slashes (``//`` → ``/``).
    - Rejects path traversal patterns (``..``, ``.`` segments).
    - Strips leading slashes (the route prefix already provides one).

    Returns the normalized path, or raises ``HTTPException(400)`` for invalid input.
    """

    # Reject path traversal — ``..`` and ``.`` as whole segments are never legitimate
    # in webhook paths.
    segments = [s for s in path.split("/") if s]
    for seg in segments:
        if seg in ("..", "."):
            raise HTTPException(
                400,
                f"Invalid webhook path segment: {seg!r}",
            )
    # Join cleaned segments with single slashes; FastAPI's {path:path} already
    # strips the leading slash, so we receive e.g. ``orders/42`` not ``/orders/42``.
    normalized = "/".join(segments)
    if not normalized and path.strip("/"):
        # Path was all slashes or dots — reject.
        raise HTTPException(400, "Invalid webhook path")
    return normalized


async def _check_webhook_body_size(request: Request) -> None:
    """Reject webhook requests whose body size exceeds ``_WEBHOOK_MAX_BODY_BYTES``.

    Checks ``Content-Length`` first (fast path).  For chunked or missing
    Content-Length, streams the body up to the limit so oversized payloads
    are rejected before any handler runs.
    """
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _WEBHOOK_MAX_BODY_BYTES:
                raise HTTPException(
                    413,
                    f"Request body too large (max {_WEBHOOK_MAX_BODY_BYTES // 1024 // 1024} MiB)",
                )
            return  # declared size is ok
        except (ValueError, TypeError):
            pass  # Malformed Content-Length — stream and check below
    # Chunked or missing Content-Length: stream up to the cap.
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _WEBHOOK_MAX_BODY_BYTES:
            raise HTTPException(
                413,
                f"Request body too large (max {_WEBHOOK_MAX_BODY_BYTES // 1024 // 1024} MiB)",
            )
        chunks.append(chunk)
    request._body = b"".join(chunks)  # noqa: SLF001 - cache for downstream


# Listen sessions. Registered by the editor's "Listen for test event" button;
# the test URL handler rejects requests when no session is active so Postman
# can't hit the URL outside of an editor session. Sessions live in Redis when
# available so the gate works when the editor and the incoming test request
# land on different replicas; the in-process dict is the single-process
# fallback (same degradation mode as the capture buffer above).
WEBHOOK_LISTEN_TTL_SECONDS = 10 * 60  # 10 min — generous for Postman setup time
_LISTEN_KEY_PREFIX = "nodyra:webhook_listen:"
_listening: dict[str, tuple[float, str]] = {}  # path → (expire_at, org_id)


def _capture_key(org_id: str, path: str) -> str:
    return f"{org_id}:{path}"


def _listen_redis():
    """The events broker's connected Redis client, or None.

    The broker is the reliable "is Redis actually deployed" signal — when it
    isn't holding a client (in-memory event bus, Redis unreachable) every
    replica is on its own anyway and the in-process store is the best we can
    do, without paying a doomed TCP connect per webhook-test request.
    """
    try:
        from app.services.events import broker

        return broker._redis
    except Exception:  # noqa: BLE001 - fall back to the in-process store
        return None


async def _listening_org(path: str) -> str | None:
    """Return the org owning the active listen session for ``path``."""
    redis = _listen_redis()
    if redis is not None:
        try:
            value = await redis.get(_LISTEN_KEY_PREFIX + path)
            if value:
                return value.decode() if isinstance(value, bytes) else str(value)
        except Exception:  # noqa: BLE001 - Redis down → in-process fallback
            pass
    entry = _listening.get(path)
    if entry is None:
        return None
    expire_at, org_id = entry
    if time.monotonic() > expire_at:
        _listening.pop(path, None)
        return None
    return org_id


async def _start_listening(path: str, org_id: str) -> None:
    redis = _listen_redis()
    if redis is not None:
        try:
            await redis.set(_LISTEN_KEY_PREFIX + path, org_id, ex=WEBHOOK_LISTEN_TTL_SECONDS)
            return
        except Exception:  # noqa: BLE001
            pass
    _listening[path] = (
        time.monotonic() + WEBHOOK_LISTEN_TTL_SECONDS,
        org_id,
    )


async def _stop_listening(path: str, org_id: str) -> None:
    if await _listening_org(path) != org_id:
        return
    redis = _listen_redis()
    if redis is not None:
        try:
            await redis.delete(_LISTEN_KEY_PREFIX + path)
        except Exception:  # noqa: BLE001
            pass
    _listening.pop(path, None)


# Caller-facing detail per rejection status from ``dispatch_webhook``.
# 401 includes the ``WWW-Authenticate`` header per RFC 7235 so clients know
# which schemes the webhook accepts. Only Basic is listed here because the
# node-level scheme is not known at the route layer; a generic challenge is
# still correct — the client can retry with any scheme the server supports.
_REJECT_DETAIL = {
    401: "Webhook authentication failed.",
    403: "Caller IP is not allowed.",
}
_REJECT_HEADERS: dict[int, dict[str, str]] = {
    401: {"WWW-Authenticate": 'Basic realm="Nodyra webhook"'},
}


def _reject_response(status: int) -> HTTPException:
    """Raise a rejection with the right status, detail, and headers."""
    return HTTPException(
        status,
        _REJECT_DETAIL[status],
        _REJECT_HEADERS.get(status),
    )


def _shaped_response(shape: dict) -> Response:
    """Build the immediate response from a webhook node's ``response_data``.

    ``shape`` is ``{"status", "headers", "body", "no_body"}`` as produced by
    ``triggers._webhook_on_received_response``. ``No Body`` returns an empty
    body with the chosen status; everything else is JSON-encoded.
    """
    status_code = int(shape.get("status") or 200)
    headers = {str(k): str(v) for k, v in (shape.get("headers") or {}).items()}
    if shape.get("no_body"):
        return Response(status_code=status_code, headers=headers)
    content_type = shape.get("content_type")
    body = shape.get("body")
    if content_type and content_type.split(";")[0].strip().lower() != "application/json":
        # Non-JSON content type: emit the body as text (verbatim if already a
        # string), tagged with the requested media type.
        text = body if isinstance(body, str) else json.dumps(body, default=str)
        return Response(
            content=text,
            status_code=status_code,
            headers=headers,
            media_type=content_type,
        )
    return JSONResponse(content=body, status_code=status_code, headers=headers)


def _evict_stale(now: float) -> None:
    """Drop entries older than the TTL. Cheap O(N) scan; N is tiny."""
    cutoff = now - WEBHOOK_CAPTURE_TTL_SECONDS
    for path, (seen_at, _payload) in list(_captured.items()):
        if seen_at < cutoff:
            _captured.pop(path, None)


def _record_capture(path: str, payload: dict, *, org_id: str) -> None:
    now = time.monotonic()
    _evict_stale(now)
    # If still over cap (every entry fresh), drop the oldest.
    while len(_captured) >= WEBHOOK_CAPTURE_MAX_ENTRIES:
        oldest_path = min(_captured, key=lambda p: _captured[p][0])
        _captured.pop(oldest_path, None)
    _captured[_capture_key(org_id, path)] = (now, payload)


async def _payload(request: Request) -> tuple[dict, bytes]:
    """Return the node-facing payload dict and the raw request bytes.

    The raw bytes are returned separately (not embedded in the payload, which
    becomes the trigger node's JSON output) so HMAC verification can sign the
    exact bytes received.
    """
    raw = await request.body()
    body: object
    try:
        body = json.loads(raw) if raw else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Not JSON: keep a best-effort text view (binary bytes become
        # replacement chars). The exact bytes are preserved separately via
        # raw_body capture when the node opts in.
        body = raw.decode("utf-8", "replace")
    payload = {
        "method": request.method,
        "headers": dict(request.headers),
        "query": dict(request.query_params),
        "body": body,
        "received_at": datetime.now(UTC).isoformat(),
    }
    return payload, raw


# Headers that carry credentials — never store them in the capture buffer.
# Matching is case-insensitive; values are replaced with the literal string
# below so the editor preview still shows that *something* was sent.
_REDACTED_HEADER_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "proxy-authorization",
        "x-api-key",
        "x-auth-token",
        "x-csrf-token",
    }
)
_REDACTED_VALUE = "[redacted]"


def _redacted_payload(payload: dict) -> dict:
    """Strip credential-bearing headers before persisting to the capture
    buffer. Dispatch still uses the original payload so auth checks pass."""
    safe = dict(payload)
    headers = payload.get("headers") or {}
    safe["headers"] = {
        name: (_REDACTED_VALUE if name.lower() in _REDACTED_HEADER_NAMES else value)
        for name, value in headers.items()
    }
    return safe


# The editor-facing endpoints below carry their own auth dependency: the
# "/webhook-test" prefix is exempt from the global auth/CSRF middleware (the
# capture URL itself must accept unauthenticated external requests), so
# without a route-level check anyone could open the listen gate or read
# captured payloads — defeating the gate's purpose.
_EDITOR_SESSION = [Depends(require_permission("workflow:run"))]


@router.get("/webhook-test/{path:path}/last", dependencies=_EDITOR_SESSION)
async def last_webhook(path: str) -> dict | None:
    """Return the most recent request captured for this webhook path.

    Polled by the editor while listening — renew the listen session on each
    poll so it stays open exactly as long as the editor tab does, instead of
    hard-expiring mid-session after the initial TTL.
    """
    path = _normalize_webhook_path(path)
    org_id = active_org_id() or DEFAULT_ORG_ID
    if await _listening_org(path) == org_id:
        await _start_listening(path, org_id)
    _evict_stale(time.monotonic())
    entry = _captured.get(_capture_key(org_id, path))
    return entry[1] if entry is not None else None


@router.delete("/webhook-test/{path:path}/last", status_code=204, dependencies=_EDITOR_SESSION)
async def clear_webhook(path: str) -> None:
    """Drop the last captured request so a fresh ``Listen`` can wait for new ones."""
    path = _normalize_webhook_path(path)
    org_id = active_org_id() or DEFAULT_ORG_ID
    _captured.pop(_capture_key(org_id, path), None)


@router.post("/webhook-test/{path:path}/listen", status_code=200, dependencies=_EDITOR_SESSION)
async def start_listen_session(path: str) -> dict:
    """Register an active listen session so the test URL accepts incoming requests.

    Called by the editor when the user clicks 'Listen for test event'. The
    session expires automatically after ``WEBHOOK_LISTEN_TTL_SECONDS`` (10 min)
    without editor polls (see ``last_webhook``) so a closed browser tab never
    leaves the test URL permanently open.
    """
    path = _normalize_webhook_path(path)
    org_id = active_org_id() or DEFAULT_ORG_ID
    owner = await _listening_org(path)
    if owner is not None and owner != org_id:
        raise HTTPException(
            status_code=409,
            detail="This webhook test path is currently being used by another workspace.",
        )
    await _start_listening(path, org_id)
    return {"listening": True, "ttl_seconds": WEBHOOK_LISTEN_TTL_SECONDS}


@router.delete("/webhook-test/{path:path}/listen", status_code=204, dependencies=_EDITOR_SESSION)
async def stop_listen_session(path: str) -> None:
    """Clear the listen session; the test URL returns 404 until re-opened."""
    path = _normalize_webhook_path(path)
    await _stop_listening(path, active_org_id() or DEFAULT_ORG_ID)


async def capture_webhook(path: str, request: Request) -> dict:
    """Editor test URL — only active while a listen session is registered.

    Call ``POST /webhook-test/{path}/listen`` first (the editor's Listen button
    does this automatically). Without an active session the URL returns 404 so
    external tools cannot hit the test endpoint at arbitrary times.

    When listening, the request is always captured (so the editor shows it),
    then dispatched against the draft graph. Auth IS checked when a workflow
    draft has auth configured.

    OPTIONS returns 204 immediately (CORS preflight) — no listen session or
    dispatch is needed.
    """
    if request.method == "OPTIONS":
        return Response(status_code=204)
    path = _normalize_webhook_path(path)
    await _check_webhook_body_size(request)
    listen_org_id = await _listening_org(path)
    if listen_org_id is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No active listen session for this webhook path. "
                "Click 'Listen for test event' in the editor first."
            ),
        )
    # Apply rate limiting to the test URL as well — the listen gate already
    # prevents abuse by unauthenticated callers, but a rate limit adds a second
    # layer against flooding within an active listen window.  Uses a lower
    # default than the production URL since test workloads are lighter.
    await _enforce_webhook_rate_limit(
        path, request, limit=settings.webhook_test_rate_limit_per_minute
    )
    client_ip = request.client.host if request.client else None
    req_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    payload, raw_body = await _payload(request)
    _record_capture(path, _redacted_payload(payload), org_id=listen_org_id)
    with run_as_org(listen_org_id):
        result = await dispatch_webhook(
            path,
            payload,
            prefer_draft=True,
            raw_body=raw_body,
            client_ip=client_ip,
        )
    logger.info(
        "webhook test path=%s matched=%s runs=%d req_id=%s client_ip=%s",
        path,
        result.any_match,
        len(result.run_ids),
        req_id,
        client_ip or "-",
    )
    if result.reject_status is not None:
        logger.warning(
            "webhook auth rejected test path=%s status=%d client_ip=%s req_id=%s",
            path,
            result.reject_status,
            client_ip or "-",
            req_id,
        )
        raise _reject_response(result.reject_status)
    return {
        "message": "Nodyra test webhook received",
        "path": path,
        "matched": result.any_match,
        "runs": result.run_ids,
        "x_request_id": req_id,
    }


for _method in _METHODS:
    router.add_api_route(
        "/webhook-test/{path:path}",
        capture_webhook,
        methods=[_method],
        operation_id=f"capture_webhook_{_method.lower()}",
    )


async def _enforce_webhook_rate_limit(
    path: str, request: Request, *, limit: int | None = None
) -> None:
    """H5: throttle public webhook ingress per (path, caller IP).

    Unauthenticated ``/webhook/{path}`` can otherwise be hammered into a
    run-queue flood. Keyed by path *and* IP so one noisy sender can't starve a
    different webhook or a different caller of the same one.

    When ``limit`` is None the production default is used; pass an explicit
    value (e.g. the lower test‑URL threshold) to override.
    """
    if not settings.webhook_rate_limit_enabled:
        return
    from app.services import rate_limit

    ip = get_client_ip(request)
    allowed = await rate_limit.allow(
        "webhook",
        f"{path}:{ip}",
        limit=limit if limit is not None else settings.webhook_rate_limit_per_minute,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many webhook requests; slow down.",
        )


@production_router.post("/webhooks/github-sync/{org_id}", status_code=200)
async def github_sync_webhook(
    org_id: str,
    request: Request,
    session=Depends(get_session),
) -> dict:
    """GitHub push webhook for the GitHub sync feature.

    No bearer auth — authenticated by HMAC-SHA256 signature in
    ``X-Hub-Signature-256``. Bypasses org filter via run_as_system so the
    config lookup is unscoped (the request carries no Nodyra auth context).
    """
    import json as _json

    from sqlalchemy import select as _select
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import GithubSyncConfig, GithubSyncJob, Workflow
    from app.services.github_sync import verify_github_hmac, workflow_file_path
    from app.services.github_sync_jobs import notify_sync_workers
    from app.tenancy import run_as_system

    typed_session: AsyncSession = session

    with run_as_system():
        cfg = await typed_session.scalar(
            _select(GithubSyncConfig).where(GithubSyncConfig.org_id == org_id)
        )
        if cfg is None:
            raise HTTPException(404, "No sync config for this org")

        payload_bytes = await request.body()
        sig = request.headers.get("X-Hub-Signature-256", "")
        if not verify_github_hmac(cfg.decrypted_webhook_secret(), payload_bytes, sig):
            raise HTTPException(401, "Invalid webhook signature")

        event = request.headers.get("X-GitHub-Event", "")
        if event != "push":
            return {"status": "ignored", "event": event}

        data = _json.loads(payload_bytes)
        ref = data.get("ref", "")
        if ref != f"refs/heads/{cfg.main_branch}":
            return {"status": "ignored", "ref": ref}

        # Collect changed .py files under the configured base path
        changed_paths: set[str] = set()
        for commit in data.get("commits", []):
            for key in ("added", "modified", "removed"):
                for p in commit.get(key, []):
                    if p.startswith(cfg.base_path) and p.endswith(".py"):
                        changed_paths.add(p)

        if not changed_paths:
            return {"status": "ok", "enqueued": 0}

        # Match changed files to workflows via file path
        workflows = (
            await typed_session.scalars(_select(Workflow).where(Workflow.org_id == org_id))
        ).all()
        slug_to_workflow = {workflow_file_path(cfg.base_path, wf): wf for wf in workflows}

        enqueued = 0
        for path in changed_paths:
            wf = slug_to_workflow.get(path)
            if wf is None:
                continue
            typed_session.add(
                GithubSyncJob(
                    org_id=org_id,
                    workflow_id=wf.id,
                    job_type="pull",
                    origin="webhook",
                    status="pending",
                )
            )
            enqueued += 1

        if enqueued:
            await typed_session.commit()
            notify_sync_workers()

    return {"status": "ok", "enqueued": enqueued}


async def trigger_webhook(path: str, request: Request) -> dict:
    """Production webhook — dispatch a run of matching active workflows.

    ``{path:path}`` captures the full sub-path (slashes included) so resource
    routes like ``/webhook/customers/42/orders`` reach a webhook node whose
    ``path`` template is ``customers/{id}/orders``.

    OPTIONS returns 204 immediately (CORS preflight) — no dispatch is needed.
    """
    if request.method == "OPTIONS":
        return Response(status_code=204)
    path = _normalize_webhook_path(path)
    await _check_webhook_body_size(request)
    await _enforce_webhook_rate_limit(path, request)
    client_ip = request.client.host if request.client else None
    req_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    payload, raw_body = await _payload(request)
    _record_capture(
        path,
        _redacted_payload(payload),
        org_id=active_org_id() or DEFAULT_ORG_ID,
    )
    # Webhook ingress is inherently cross-org: the path decides which org's
    # workflow fires, not the caller's X-Org-Id (callers are external systems
    # with no Nodyra identity). Matching runs unscoped; start_run then pins
    # each run to its workflow's org.
    from app.tenancy import run_as_system

    with run_as_system():
        result = await dispatch_webhook(
            path,
            payload,
            raw_body=raw_body,
            client_ip=client_ip,
        )
        logger.info(
            "webhook prod path=%s matched=%s runs=%d req_id=%s client_ip=%s",
            path,
            result.any_match,
            len(result.run_ids),
            req_id,
            client_ip or "-",
        )
        if result.reject_status is not None:
            # Path matched at least one workflow, but every candidate was
            # rejected: 403 when an IP allowlist blocked the caller, else 401
            # (auth/HMAC). Distinct from an unknown-path 404.
            logger.warning(
                "webhook auth rejected prod path=%s status=%d client_ip=%s req_id=%s",
                path,
                result.reject_status,
                client_ip or "-",
                req_id,
            )
            raise _reject_response(result.reject_status)
        if not result.any_match:
            # No active workflow registered for this path. Return 404 so
            # external senders (Stripe, GitHub, etc.) know the endpoint
            # doesn't exist — a 200 would falsely signal delivery success.
            raise HTTPException(404, "No active workflow for this webhook path.")
        if result.sync is not None:
            shape = await wait_for_webhook_result(
                **result.sync,
                timeout=settings.webhook_response_timeout_seconds,
            )
            if shape.get("status") == 504:
                logger.warning(
                    "webhook sync timeout run_id=%s path=%s timeout=%d req_id=%s",
                    result.sync["run_id"],
                    path,
                    settings.webhook_response_timeout_seconds,
                    req_id,
                )
            return _shaped_response(shape)
    if result.response is not None:
        return _shaped_response(result.response)
    if result.run_ids:
        message = "Workflow triggered"
    elif result.deduped:
        message = "Duplicate delivery acknowledged"
    else:
        message = "No active workflow for this path"
    return {
        "message": message,
        "path": path,
        "runs": result.run_ids,
        "x_request_id": req_id,
    }


for _method in _METHODS:
    production_router.add_api_route(
        "/webhook/{path:path}",
        trigger_webhook,
        methods=[_method],
        operation_id=f"trigger_webhook_{_method.lower()}",
    )
