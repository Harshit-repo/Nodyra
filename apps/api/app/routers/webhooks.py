"""Webhook ingress.

* ``/webhook-test/{path}`` captures requests so the editor can show what a
  webhook node receives while you build a workflow.
* ``/webhook/{path}`` is the production URL — it dispatches a run of every
  active workflow that starts with a matching webhook node.
"""

import json
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status

from app.services.triggers import dispatch_webhook

router = APIRouter(tags=["webhooks"])

_captured: dict[str, dict] = {}
_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


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


@router.api_route("/webhook-test/{path}", methods=_METHODS)
async def capture_webhook(path: str, request: Request) -> dict:
    """Editor test URL — capture the request and dispatch matching workflows
    using their draft graph (so unpublished credential refs and auth changes
    apply). Workflow ``active`` is ignored on this path; auth IS still
    checked, so the user can validate their Basic/Header/Query setup."""
    payload = await _payload(request)
    _captured[path] = payload
    run_ids, any_path_matched = await dispatch_webhook(
        path, payload, prefer_draft=True
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
    return _captured.get(path)


@router.delete("/webhook-test/{path}/last", status_code=204)
async def clear_webhook(path: str) -> None:
    """Drop the last captured request so a fresh ``Listen`` can wait for new ones."""
    _captured.pop(path, None)


@router.api_route("/webhook/{path}", methods=_METHODS)
async def trigger_webhook(path: str, request: Request) -> dict:
    """Production webhook — dispatch a run of matching active workflows."""
    payload = await _payload(request)
    _captured[path] = payload
    run_ids, any_path_matched = await dispatch_webhook(path, payload)
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
