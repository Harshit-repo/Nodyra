"""Internal endpoints for out-of-process operational callers.

Lives under ``/internal/*`` and is exempt from the JWT auth gate so an
external driver (e.g. a cron job) can reach it without a user token.
Authentication is a shared secret (``settings.internal_api_token``).

Production deployments **must** set ``INTERNAL_API_TOKEN`` to a long
random value. When the token is blank in production mode, the endpoint
refuses all requests rather than silently accepting unauthenticated callers.
In local dev mode (``runtime_mode=local``) a blank token is permitted so
the developer doesn't need to configure extra secrets for a single-process
loopback call.
"""

import hmac
import logging

from fastapi import APIRouter, HTTPException, Request, status

from app.config import settings
from app.services.triggers import _tick

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])

_HEADER = "x-noodle-internal-token"


def _check_token(request: Request) -> None:
    expected = settings.internal_api_token
    if not expected:
        # Production mode: refuse all requests.  The operator must set a token.
        if settings.runtime_mode != "local":
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "INTERNAL_API_TOKEN is not configured — set it to a long random "
                "value to secure the /internal endpoints.  "
                "Generate one with:  python -c \"import secrets; print(secrets.token_urlsafe(32))\"",
            )
        # Local dev: a blank token is permitted (single-machine loopback).
        return
    presented = request.headers.get(_HEADER) or ""
    # Constant-time comparison so the shared secret can't be recovered by
    # timing the response to header guesses.
    if not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "invalid internal token"
        )


@router.post("/scheduler/tick")
async def scheduler_tick(request: Request) -> dict:
    """One scheduler pass — what the in-process loop does each 30s, but
    drivable by an external scheduler (e.g. cron). Multi-replica deployments
    normally use ``scheduler_role=leader`` instead. Idempotent and quick if
    nothing is due."""
    _check_token(request)
    await _tick()
    return {"ok": True}
