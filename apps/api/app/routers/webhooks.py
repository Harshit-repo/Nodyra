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
from app.security import require_permission
from app.services.triggers import dispatch_webhook, wait_for_webhook_result

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
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]

WEBHOOK_CAPTURE_TTL_SECONDS = 5 * 60  # 5 min — typical Listen-then-test loop
WEBHOOK_CAPTURE_MAX_ENTRIES = 256

# Listen sessions. Registered by the editor's "Listen for test event" button;
# the test URL handler rejects requests when no session is active so Postman
# can't hit the URL outside of an editor session. Sessions live in Redis when
# available so the gate works when the editor and the incoming test request
# land on different replicas; the in-process dict is the single-process
# fallback (same degradation mode as the capture buffer above).
WEBHOOK_LISTEN_TTL_SECONDS = 10 * 60  # 10 min — generous for Postman setup time
_LISTEN_KEY_PREFIX = "noodle:webhook_listen:"
_listening: dict[str, float] = {}  # path → monotonic expire_at (fallback store)


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


async def _is_listening(path: str) -> bool:
    """Return True when there is a non-expired listen session for ``path``."""
    redis = _listen_redis()
    if redis is not None:
        try:
            return bool(await redis.exists(_LISTEN_KEY_PREFIX + path))
        except Exception:  # noqa: BLE001 - Redis down → in-process fallback
            pass
    expire_at = _listening.get(path)
    if expire_at is None:
        return False
    if time.monotonic() > expire_at:
        _listening.pop(path, None)
        return False
    return True


async def _start_listening(path: str) -> None:
    redis = _listen_redis()
    if redis is not None:
        try:
            await redis.set(
                _LISTEN_KEY_PREFIX + path, "1", ex=WEBHOOK_LISTEN_TTL_SECONDS
            )
            return
        except Exception:  # noqa: BLE001
            pass
    _listening[path] = time.monotonic() + WEBHOOK_LISTEN_TTL_SECONDS


async def _stop_listening(path: str) -> None:
    redis = _listen_redis()
    if redis is not None:
        try:
            await redis.delete(_LISTEN_KEY_PREFIX + path)
        except Exception:  # noqa: BLE001
            pass
    _listening.pop(path, None)


# Caller-facing detail per rejection status from ``dispatch_webhook``.
_REJECT_DETAIL = {
    401: "Webhook authentication failed.",
    403: "Caller IP is not allowed.",
}


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
    return JSONResponse(
        content=body, status_code=status_code, headers=headers
    )


def _evict_stale(now: float) -> None:
    """Drop entries older than the TTL. Cheap O(N) scan; N is tiny."""
    cutoff = now - WEBHOOK_CAPTURE_TTL_SECONDS
    for path, (seen_at, _payload) in list(_captured.items()):
        if seen_at < cutoff:
            _captured.pop(path, None)


def _record_capture(path: str, payload: dict) -> None:
    now = time.monotonic()
    _evict_stale(now)
    # If still over cap (every entry fresh), drop the oldest.
    while len(_captured) >= WEBHOOK_CAPTURE_MAX_ENTRIES:
        oldest_path = min(_captured, key=lambda p: _captured[p][0])
        _captured.pop(oldest_path, None)
    _captured[path] = (now, payload)


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
_REDACTED_HEADER_NAMES = frozenset({
    "authorization",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-api-key",
    "x-auth-token",
    "x-csrf-token",
})
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


@router.api_route("/webhook-test/{path}", methods=_METHODS)
async def capture_webhook(path: str, request: Request) -> dict:
    """Editor test URL — only active while a listen session is registered.

    Call ``POST /webhook-test/{path}/listen`` first (the editor's Listen button
    does this automatically). Without an active session the URL returns 404 so
    external tools cannot hit the test endpoint at arbitrary times.

    When listening, the request is always captured (so the editor shows it),
    then dispatched against the draft graph. Auth IS checked when a workflow
    draft has auth configured.
    """
    if not await _is_listening(path):
        raise HTTPException(
            status_code=404,
            detail=(
                "No active listen session for this webhook path. "
                "Click 'Listen for test event' in the editor first."
            ),
        )
    req_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    payload, raw_body = await _payload(request)
    _record_capture(path, _redacted_payload(payload))
    result = await dispatch_webhook(
        path, payload, prefer_draft=True, raw_body=raw_body,
        client_ip=request.client.host if request.client else None,
    )
    logger.info(
        "webhook test path=%s matched=%s runs=%d req_id=%s",
        path, result.any_match, len(result.run_ids), req_id,
    )
    if result.reject_status is not None:
        raise HTTPException(
            result.reject_status, _REJECT_DETAIL[result.reject_status]
        )
    return {
        "message": "Noodle test webhook received",
        "path": path,
        "matched": result.any_match,
        "runs": result.run_ids,
        "x_request_id": req_id,
    }


# The editor-facing endpoints below carry their own auth dependency: the
# "/webhook-test" prefix is exempt from the global auth/CSRF middleware (the
# capture URL itself must accept unauthenticated external requests), so
# without a route-level check anyone could open the listen gate or read
# captured payloads — defeating the gate's purpose.
_EDITOR_SESSION = [Depends(require_permission("workflow:run"))]


@router.get("/webhook-test/{path}/last", dependencies=_EDITOR_SESSION)
async def last_webhook(path: str) -> dict | None:
    """Return the most recent request captured for this webhook path.

    Polled by the editor while listening — renew the listen session on each
    poll so it stays open exactly as long as the editor tab does, instead of
    hard-expiring mid-session after the initial TTL.
    """
    if await _is_listening(path):
        await _start_listening(path)
    _evict_stale(time.monotonic())
    entry = _captured.get(path)
    return entry[1] if entry is not None else None


@router.delete("/webhook-test/{path}/last", status_code=204, dependencies=_EDITOR_SESSION)
async def clear_webhook(path: str) -> None:
    """Drop the last captured request so a fresh ``Listen`` can wait for new ones."""
    _captured.pop(path, None)


@router.post("/webhook-test/{path}/listen", status_code=200, dependencies=_EDITOR_SESSION)
async def start_listen_session(path: str) -> dict:
    """Register an active listen session so the test URL accepts incoming requests.

    Called by the editor when the user clicks 'Listen for test event'. The
    session expires automatically after ``WEBHOOK_LISTEN_TTL_SECONDS`` (10 min)
    without editor polls (see ``last_webhook``) so a closed browser tab never
    leaves the test URL permanently open.
    """
    await _start_listening(path)
    return {"listening": True, "ttl_seconds": WEBHOOK_LISTEN_TTL_SECONDS}


@router.delete("/webhook-test/{path}/listen", status_code=204, dependencies=_EDITOR_SESSION)
async def stop_listen_session(path: str) -> None:
    """Clear the listen session; the test URL returns 404 until re-opened."""
    await _stop_listening(path)


@production_router.api_route("/webhook/{path:path}", methods=_METHODS)
async def trigger_webhook(path: str, request: Request) -> dict:
    """Production webhook — dispatch a run of matching active workflows.

    ``{path:path}`` captures the full sub-path (slashes included) so resource
    routes like ``/webhook/customers/42/orders`` reach a webhook node whose
    ``path`` template is ``customers/{id}/orders``.
    """
    req_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    payload, raw_body = await _payload(request)
    _record_capture(path, _redacted_payload(payload))
    # Webhook ingress is inherently cross-org: the path decides which org's
    # workflow fires, not the caller's X-Org-Id (callers are external systems
    # with no Noodle identity). Matching runs unscoped; start_run then pins
    # each run to its workflow's org.
    from app.tenancy import run_as_system

    with run_as_system():
        result = await dispatch_webhook(
            path, payload, raw_body=raw_body,
            client_ip=request.client.host if request.client else None,
        )
        logger.info(
            "webhook prod path=%s matched=%s runs=%d req_id=%s",
            path, result.any_match, len(result.run_ids), req_id,
        )
        if result.reject_status is not None:
            # Path matched at least one workflow, but every candidate was
            # rejected: 403 when an IP allowlist blocked the caller, else 401
            # (auth/HMAC). Distinct from an unknown-path 404.
            raise HTTPException(
                result.reject_status, _REJECT_DETAIL[result.reject_status]
            )
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
