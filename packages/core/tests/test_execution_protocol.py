import pytest

from nodyra.execution_protocol import (
    LEGACY_PROTOCOL_VERSION,
    MAX_DISPATCH_BYTES,
    PROTOCOL_VERSION,
    ProtocolError,
    build_dispatch_envelope,
    negotiate_protocol,
    protocol_manifest,
    validate_dispatch_envelope,
    validate_runner_event,
)


def test_dispatch_envelope_is_versioned_bounded_and_idempotent() -> None:
    envelope = build_dispatch_envelope(
        {"type": "run_assigned", "run_id": "run-1", "graph": {"nodes": [], "edges": []}}
    )

    assert envelope["protocol_version"] == PROTOCOL_VERSION
    assert envelope["idempotency_key"] == "run:run-1:dispatch:v1"
    assert envelope["message_id"]
    assert envelope["limits"]["max_dispatch_bytes"] == MAX_DISPATCH_BYTES
    assert validate_dispatch_envelope(envelope) == PROTOCOL_VERSION


def test_dispatch_rejects_missing_identity_and_oversized_payloads() -> None:
    with pytest.raises(ProtocolError, match="requires run_id"):
        build_dispatch_envelope({"type": "run_assigned"})

    with pytest.raises(ProtocolError, match="maximum"):
        build_dispatch_envelope(
            {
                "type": "run_assigned",
                "run_id": "run-large",
                "graph": {"source": "x" * MAX_DISPATCH_BYTES},
            }
        )


def test_protocol_negotiation_prefers_current_and_retains_legacy_reads() -> None:
    assert negotiate_protocol([LEGACY_PROTOCOL_VERSION, PROTOCOL_VERSION]) == PROTOCOL_VERSION
    assert negotiate_protocol(None) == LEGACY_PROTOCOL_VERSION
    with pytest.raises(ProtocolError, match="no compatible runner protocol"):
        negotiate_protocol(["99.0"])


def test_waiting_completes_runner_attempt_but_is_not_terminal_run_state() -> None:
    validate_runner_event(
        {"type": "run_finished", "run_id": "run-1", "status": "waiting"}
    )
    manifest = protocol_manifest()
    assert "waiting" in manifest["runner_completion_statuses"]
    assert "waiting" not in manifest["terminal_run_statuses"]


def test_runner_event_rejects_invalid_status_and_missing_run() -> None:
    with pytest.raises(ProtocolError, match="requires run_id"):
        validate_runner_event({"type": "run_finished", "status": "success"})
    with pytest.raises(ProtocolError, match="completion status"):
        validate_runner_event(
            {"type": "run_finished", "run_id": "run-1", "status": "unknown"}
        )
