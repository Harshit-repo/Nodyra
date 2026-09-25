"""Service-level exceptions with HTTP status mapping.

Routes catch these directly instead of translating generic ``ValueError`` /
``RuntimeError`` strings into status codes, so a new integration point can't
accidentally surface a 500 for a 400-class error.

Each exception carries an ``http_status`` (integer) and a ``detail`` (dict)
suitable for FastAPI's JSON error responses.
"""

from __future__ import annotations

from fastapi import status as _http_status


class ServiceError(Exception):
    """Base for all Nodyra service-level exceptions."""
    http_status: int = _http_status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.detail = detail or {}


class WorkflowNeedsTrigger(ServiceError):
    """Workflow has no trigger node that can start a run."""
    http_status = _http_status.HTTP_400_BAD_REQUEST


class StepNeedsUpstreamTrigger(ServiceError):
    """A step-run target has no upstream trigger connected."""
    http_status = _http_status.HTTP_400_BAD_REQUEST


class PackageNotInstalled(ServiceError):
    """A node requires Python packages not in the workflow's environment."""
    http_status = _http_status.HTTP_400_BAD_REQUEST


class SingleFlightConflict(ServiceError):
    """A single-flight workflow already has another run in progress."""
    http_status = _http_status.HTTP_409_CONFLICT


class ArtifactRefInvalid(ServiceError):
    """A caller-supplied artifact reference failed validation.

    The refs in a run's ``cache``/pinned data come from the request body, so a
    stale or tampered one is a client error. Raised as a bare ValueError it
    escaped as "500 Internal server error" with no detail — telling the caller
    nothing and reporting their bad input as our bug.
    """
    http_status = _http_status.HTTP_400_BAD_REQUEST


class DuplicateRun(ServiceError):
    """A run with this deduplication key already exists.

    Raised when concurrent deliveries of the same event race past the
    "have I seen this key?" read and the unique index arbitrates. Callers
    treat it as a successful de-duplication, not an error.
    """
    http_status = _http_status.HTTP_409_CONFLICT


class QuotaExceeded(ServiceError):
    """An org-level quota (executions/day, storage, …) has been reached."""
    http_status = _http_status.HTTP_429_TOO_MANY_REQUESTS


class DedicatedPoolRequired(ServiceError):
    """An org requires isolated execution but no docker/kubernetes pool is assigned."""
    http_status = _http_status.HTTP_400_BAD_REQUEST


class SandboxRequired(ServiceError):
    """Workflow demands sandboxed execution but none is available."""
    http_status = _http_status.HTTP_409_CONFLICT


class AuthError(ServiceError):
    """Authentication failure (invalid credentials, expired token, etc.)."""
    http_status = _http_status.HTTP_401_UNAUTHORIZED
