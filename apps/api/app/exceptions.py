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
    """Base for all Noodle service-level exceptions."""
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


class QuotaExceeded(ServiceError):
    """An org-level quota (executions/day, storage, …) has been reached."""
    http_status = _http_status.HTTP_429_TOO_MANY_REQUESTS


class DedicatedPoolRequired(ServiceError):
    """An org requires isolated execution but no docker/kubernetes pool is assigned."""
    http_status = _http_status.HTTP_400_BAD_REQUEST
