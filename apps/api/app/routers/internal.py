"""Internal endpoints called by the Celery worker (Slice 3b scale-out).

Lives under ``/internal/*`` and is exempt from the JWT auth gate so the
worker can reach it without a user token. Authentication is a shared
secret (``settings.internal_api_token``); when the secret is blank, no
check is performed (convenient for local dev where only your machine
reaches the API).
"""

from fastapi import APIRouter, HTTPException, Request, status

from app.config import settings
from app.services.triggers import _tick

router = APIRouter(prefix="/internal", tags=["internal"])

_HEADER = "x-noodle-internal-token"


def _check_token(request: Request) -> None:
    expected = settings.internal_api_token
    if not expected:
        return
    if request.headers.get(_HEADER) != expected:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "invalid internal token"
        )


@router.post("/scheduler/tick")
async def scheduler_tick(request: Request) -> dict:
    """One scheduler pass — what the in-process loop does each 30s, but
    driven by Celery Beat from the worker. Idempotent and quick if nothing
    is due."""
    _check_token(request)
    await _tick()
    return {"ok": True}
