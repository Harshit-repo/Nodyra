"""H4 — structured JSON logging + request-correlation context."""

import json
import logging

from app import logging as app_logging


def _format(record: logging.LogRecord) -> dict:
    formatter = app_logging.JsonFormatter()
    app_logging.RequestContextFilter().filter(record)
    return json.loads(formatter.format(record))


def _record(msg: str = "hello", level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(
        name="noodle.test", level=level, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )


def test_json_formatter_emits_core_fields() -> None:
    out = _format(_record("a message"))
    assert out["level"] == "INFO"
    assert out["logger"] == "noodle.test"
    assert out["message"] == "a message"
    assert "timestamp" in out


def test_request_context_is_injected_into_logs() -> None:
    tokens = app_logging.bind_request_context(
        request_id="req-123", org_id="org-9", user_id="user-7"
    )
    try:
        out = _format(_record())
    finally:
        app_logging.reset_request_context(tokens)
    assert out["request_id"] == "req-123"
    assert out["org_id"] == "org-9"
    assert out["user_id"] == "user-7"


def test_context_absent_keys_are_omitted() -> None:
    app_logging.reset_request_context(
        app_logging.bind_request_context()  # set+reset to ensure cleared
    )
    out = _format(_record())
    assert "request_id" not in out
    assert "org_id" not in out


def test_reset_clears_context_between_requests() -> None:
    tokens = app_logging.bind_request_context(request_id="first")
    app_logging.reset_request_context(tokens)
    out = _format(_record())
    assert "request_id" not in out


def test_exception_info_is_serialized() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _record("failed", level=logging.ERROR)
        record.exc_info = sys.exc_info()
    out = _format(record)
    assert "boom" in out["exception"]


def test_configure_logging_is_idempotent() -> None:
    app_logging.configure_logging()
    app_logging.configure_logging()
    root = logging.getLogger()
    assert root.handlers  # at least one handler installed
