"""Versioned control-plane/execution-plane message contract.

The module intentionally uses only the Python standard library so the API,
runner agent, and disposable runtime images can share the exact same contract.
Protocol v1 is additive over the legacy unversioned messages; readers accept
legacy payloads for the 0.x compatibility window while all new writers emit v1.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Final

PROTOCOL_VERSION: Final = "1.0"
LEGACY_PROTOCOL_VERSION: Final = "0.0"
SUPPORTED_PROTOCOL_VERSIONS: Final = (PROTOCOL_VERSION, LEGACY_PROTOCOL_VERSION)
MAX_DISPATCH_BYTES: Final = 16 * 1024 * 1024
MAX_EVENT_BYTES: Final = 1024 * 1024
HEARTBEAT_INTERVAL_SECONDS: Final = 30
LEASE_SECONDS: Final = 90

TERMINAL_RUN_STATUSES: Final = frozenset(
    {"success", "error", "timed_out", "cancelled"}
)
# A runner completes one execution attempt when it pauses for approval, even
# though the durable run remains resumable. Keep transport completion separate
# from lifecycle terminality so clients do not close a run stream on waiting.
RUNNER_COMPLETION_STATUSES: Final = TERMINAL_RUN_STATUSES | frozenset({"waiting"})


class ProtocolError(ValueError):
    """Raised when a runner message violates the negotiated contract."""


def _encoded_size(payload: dict[str, Any]) -> int:
    try:
        return len(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"message is not JSON serializable: {exc}") from exc


def enforce_message_size(
    payload: dict[str, Any], *, maximum: int, message_type: str
) -> None:
    size = _encoded_size(payload)
    if size > maximum:
        raise ProtocolError(
            f"{message_type} message is {size} bytes; maximum is {maximum} bytes"
        )


def negotiate_protocol(offered: list[str] | tuple[str, ...] | None) -> str:
    """Select the newest mutually supported protocol.

    Missing offers identify a pre-v1 runner and are accepted as legacy during
    the documented 0.x compatibility window.
    """

    versions = [str(version) for version in (offered or [LEGACY_PROTOCOL_VERSION])]
    for supported in SUPPORTED_PROTOCOL_VERSIONS:
        if supported in versions:
            return supported
    raise ProtocolError(
        "no compatible runner protocol; "
        f"server supports {', '.join(SUPPORTED_PROTOCOL_VERSIONS)}"
    )


def build_dispatch_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Add required v1 metadata and enforce the dispatch payload ceiling."""

    if payload.get("type") != "run_assigned":
        raise ProtocolError("dispatch payload type must be 'run_assigned'")
    run_id = str(payload.get("run_id") or "")
    if not run_id:
        raise ProtocolError("dispatch payload requires run_id")
    envelope = {
        **payload,
        "protocol_version": PROTOCOL_VERSION,
        "message_id": uuid.uuid4().hex,
        "idempotency_key": f"run:{run_id}:dispatch:v1",
        "dispatched_at": datetime.now(UTC).isoformat(),
        "limits": {
            "max_dispatch_bytes": MAX_DISPATCH_BYTES,
            "max_event_bytes": MAX_EVENT_BYTES,
        },
    }
    enforce_message_size(
        envelope, maximum=MAX_DISPATCH_BYTES, message_type="run_assigned"
    )
    return envelope


def validate_dispatch_envelope(payload: dict[str, Any]) -> str:
    """Validate a dispatch and return its protocol version.

    Legacy unversioned payloads remain readable, but versioned payloads must
    include idempotency and message identifiers so retries can be deduplicated.
    """

    if payload.get("type") != "run_assigned":
        raise ProtocolError("expected run_assigned message")
    run_id = str(payload.get("run_id") or "")
    if not run_id:
        raise ProtocolError("run_assigned message requires run_id")
    version = str(payload.get("protocol_version") or LEGACY_PROTOCOL_VERSION)
    if version not in SUPPORTED_PROTOCOL_VERSIONS:
        raise ProtocolError(f"unsupported runner protocol version {version!r}")
    if version != LEGACY_PROTOCOL_VERSION:
        if not payload.get("message_id"):
            raise ProtocolError("versioned dispatch requires message_id")
        if payload.get("idempotency_key") != f"run:{run_id}:dispatch:v1":
            raise ProtocolError("invalid dispatch idempotency_key")
    enforce_message_size(
        payload, maximum=MAX_DISPATCH_BYTES, message_type="run_assigned"
    )
    return version


def validate_runner_event(payload: dict[str, Any]) -> None:
    """Validate size and required fields for runner-to-control-plane events."""

    message_type = str(payload.get("type") or "")
    if not message_type:
        raise ProtocolError("runner message requires type")
    if message_type in {"run_event", "run_finished", "env_building", "env_error"}:
        if not payload.get("run_id"):
            raise ProtocolError(f"{message_type} message requires run_id")
    if message_type == "run_finished":
        status = str(payload.get("status") or "")
        if status not in RUNNER_COMPLETION_STATUSES:
            raise ProtocolError(f"invalid runner completion status {status!r}")
    enforce_message_size(payload, maximum=MAX_EVENT_BYTES, message_type=message_type)


def protocol_manifest() -> dict[str, Any]:
    """Machine-readable compatibility, heartbeat, lease, and size contract."""

    return {
        "current": PROTOCOL_VERSION,
        "supported": list(SUPPORTED_PROTOCOL_VERSIONS),
        "legacy_read_support": LEGACY_PROTOCOL_VERSION,
        "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
        "lease_seconds": LEASE_SECONDS,
        "max_dispatch_bytes": MAX_DISPATCH_BYTES,
        "max_event_bytes": MAX_EVENT_BYTES,
        "runner_completion_statuses": sorted(RUNNER_COMPLETION_STATUSES),
        "terminal_run_statuses": sorted(TERMINAL_RUN_STATUSES),
    }
