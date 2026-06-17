"""Structured JSON logging with request correlation (H4).

A single line of JSON per log record so a log aggregator (Loki, CloudWatch,
Datadog, …) can index ``request_id`` / ``org_id`` / ``user_id`` / ``trace_id``
and stitch one request's lines together across the API and worker processes.

Correlation IDs travel via ContextVars set by the request middleware
(``bind_request_context``) so any logger anywhere in the call stack picks them
up without threading the IDs through every function signature.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from logging.config import dictConfig

# --- correlation context ----------------------------------------------------

_request_id: ContextVar[str | None] = ContextVar("log_request_id", default=None)
_org_id: ContextVar[str | None] = ContextVar("log_org_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("log_user_id", default=None)
_trace_id: ContextVar[str | None] = ContextVar("log_trace_id", default=None)

_VARS = {
    "request_id": _request_id,
    "org_id": _org_id,
    "user_id": _user_id,
    "trace_id": _trace_id,
}


def bind_request_context(
    *,
    request_id: str | None = None,
    org_id: str | None = None,
    user_id: str | None = None,
    trace_id: str | None = None,
) -> dict[str, Token]:
    """Set correlation IDs for the current context; return reset tokens."""
    values = {
        "request_id": request_id,
        "org_id": org_id,
        "user_id": user_id,
        "trace_id": trace_id,
    }
    return {name: _VARS[name].set(value) for name, value in values.items()}


def reset_request_context(tokens: dict[str, Token]) -> None:
    """Restore the correlation context captured by ``bind_request_context``."""
    for name, token in tokens.items():
        _VARS[name].reset(token)


# --- filter + formatter -----------------------------------------------------

class RequestContextFilter(logging.Filter):
    """Copy the correlation ContextVars onto each record as attributes."""

    def filter(self, record: logging.LogRecord) -> bool:
        for name, var in _VARS.items():
            setattr(record, name, var.get())
        return True


# Attributes always present on a LogRecord — anything else passed via ``extra``
# is appended to the JSON payload.
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in _VARS:
            value = getattr(record, name, None)
            if value:
                payload[name] = value
        # Surface any structured ``extra=`` fields the caller attached. The
        # correlation keys are handled above (and may be a falsy None on the
        # record), so exclude them here.
        for key, value in record.__dict__.items():
            if key in _RESERVED or key in _VARS or key in payload:
                continue
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str | int | None = None) -> None:
    """Install the JSON formatter + context filter on the root logger.

    Idempotent: ``dictConfig`` replaces existing handlers, so repeated calls
    (app import, lifespan, worker entry) converge on one configuration.
    """
    from app.config import settings

    resolved = level or getattr(settings, "log_level", "INFO")
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "request_context": {
                    "()": "app.logging.RequestContextFilter",
                }
            },
            "formatters": {
                "json": {
                    "()": "app.logging.JsonFormatter",
                }
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "filters": ["request_context"],
                }
            },
            "root": {
                "level": resolved,
                "handlers": ["default"],
            },
            # uvicorn brings its own handlers; route them through ours so access
            # and error logs are JSON too instead of double-formatted text.
            "loggers": {
                "uvicorn": {"handlers": ["default"], "level": resolved, "propagate": False},
                "uvicorn.error": {"handlers": ["default"], "level": resolved, "propagate": False},
                "uvicorn.access": {"handlers": ["default"], "level": resolved, "propagate": False},
            },
        }
    )
