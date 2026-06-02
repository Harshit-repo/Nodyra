"""Webhook ingress.

* ``/webhook-test/{path}`` captures requests so the editor can show what a
  webhook node receives while you build a workflow.
* ``/webhook/{path}`` is the production URL — it dispatches a run of every
  active workflow that starts with a matching webhook node.

Capture buffer: every editor "Listen" session pushes a new path through here,
so the in-memory ``_captured`` dict used to grow unbounded for the life of
the API process. It's now bounded two ways: each entry carries a timestamp
and is evicted after ``WEBHOOK_CAPTURE_TTL_SECONDS``, and the dict is capped
at ``WEBHOOK_CAPTURE_MAX_ENTRIES`` with oldest-first eviction.
"""

import json
import logging
import time
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status

from app.services.triggers import dispatch_webhook

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


async def _payload(request: Request) -> dict:
    raw = await request.body()
    body: object
    try:
        body = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        body = raw.decode("utf-8", "replace")
    return {
        "method": request.method,
        "headers": dict(request.headers),
        "query": dict(request.query_params),
        "body": body,
        "received_at": datetime.now(UTC).isoformat(),
    }


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
    """Editor test URL — capture the request and dispatch matching workflows
    using their draft graph (so unpublished credential refs and auth changes
    apply). Workflow ``active`` is ignored on this path; auth IS still
    checked, so the user can validate their Basic/Header/Query setup."""
    payload = await _payload(request)
    _record_capture(path, _redacted_payload(payload))
    run_ids, any_path_matched = await dispatch_webhook(
        path, payload, prefer_draft=True
    )
    logger.info(
        "webhook test path=%s matched=%s runs=%d",
        path, any_path_matched, len(run_ids),
    )
    if not run_ids and any_path_matched:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Webhook authentication failed.",
        )
    return {
        "message": "Noodle test webhook received",
        "path": path,
        "runs": run_ids,
    }


@router.get("/webhook-test/{path}/last")
async def last_webhook(path: str) -> dict | None:
    """Return the most recent request captured for this webhook path."""
    _evict_stale(time.monotonic())
    entry = _captured.get(path)
    return entry[1] if entry is not None else None


@router.delete("/webhook-test/{path}/last", status_code=204)
async def clear_webhook(path: str) -> None:
    """Drop the last captured request so a fresh ``Listen`` can wait for new ones."""
    _captured.pop(path, None)


@production_router.api_route("/webhook/{path}", methods=_METHODS)
async def trigger_webhook(path: str, request: Request) -> dict:
    """Production webhook — dispatch a run of matching active workflows."""
    payload = await _payload(request)
    _record_capture(path, _redacted_payload(payload))
    run_ids, any_path_matched = await dispatch_webhook(path, payload)
    logger.info(
        "webhook prod path=%s matched=%s runs=%d",
        path, any_path_matched, len(run_ids),
    )
    if not run_ids and any_path_matched:
        # Path matched at least one workflow, but every candidate's auth check
        # failed. Surface a clear 401 so the caller knows it wasn't an
        # unknown-path 404.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Webhook authentication failed.",
        )
    return {
        "message": "Workflow triggered" if run_ids else "No active workflow for this path",
        "path": path,
        "runs": run_ids,
    }
