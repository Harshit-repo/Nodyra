"""Tests for twilio_outbound_call and twilio_call_status_handler nodes."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nodyra_nodes.http_security import UnsafeHttpTargetError
from nodyra_nodes.integrations_v2.providers.twilio.voice_outbound import (
    twilio_call_status_handler,
    twilio_outbound_call,
)

CREDS = {"account_sid": "ACtest", "auth_token": "token123"}

_TRANSPORT_PATH = "nodyra_nodes.integrations_v2.providers.twilio.voice_outbound._transport"

_FAKE_RESPONSE = {
    "sid": "CA1",
    "status": "queued",
    "direction": "outbound-api",
    "to": "+15005551234",
    "from": "+15005550006",
}


# ---------------------------------------------------------------------------
# twilio_outbound_call tests
# ---------------------------------------------------------------------------


def test_outbound_call_with_twiml_url():
    """Providing twiml_url makes an outbound call and returns CallSid."""
    with patch(_TRANSPORT_PATH) as mock_t:
        transport = MagicMock()
        mock_t.return_value = transport
        transport.request.return_value = _FAKE_RESPONSE

        result = twilio_outbound_call(
            credentials=CREDS,
            to="+15005551234",
            from_phone="+15005550006",
            twiml_url="https://demo.twilio.com/welcome/voice/",
        )

    assert result["CallSid"] == "CA1"
    assert result["Status"] == "queued"
    assert result["Direction"] == "outbound-api"
    assert result["To"] == "+15005551234"
    assert result["From"] == "+15005550006"

    # Verify transport was called with Url key in data
    call_kwargs = transport.request.call_args
    # More robust: check keyword argument named 'data'
    _, kw = call_kwargs
    assert "Url" in kw["data"]
    assert kw["data"]["Url"] == "https://demo.twilio.com/welcome/voice/"


def test_outbound_call_uses_twiml_param_when_no_url():
    """No twiml_url: the request uses Twiml key (inline TwiML), not Url."""
    with patch(_TRANSPORT_PATH) as mock_t:
        transport = MagicMock()
        mock_t.return_value = transport
        transport.request.return_value = _FAKE_RESPONSE

        twilio_outbound_call(
            credentials=CREDS,
            to="+15005551234",
            from_phone="+15005550006",
            twiml_url="",
        )

    _, kw = transport.request.call_args
    data = kw["data"]
    assert "Twiml" in data
    assert "Url" not in data
    assert "<Say>" in data["Twiml"]


def test_outbound_call_falls_back_to_input_for_to():
    """When to='', the destination number is taken from wired input."""
    with patch(_TRANSPORT_PATH) as mock_t:
        transport = MagicMock()
        mock_t.return_value = transport
        transport.request.return_value = _FAKE_RESPONSE

        twilio_outbound_call(
            input="+15005550006",
            credentials=CREDS,
            to="",
            from_phone="+15005550006",
            twiml_url="https://demo.twilio.com/welcome/voice/",
        )

    _, kw = transport.request.call_args
    assert kw["data"]["To"] == "+15005550006"


def test_outbound_call_raises_ssrf_on_bad_status_callback():
    """A private IP in status_callback raises UnsafeHttpTargetError (SSRF guard)."""
    with pytest.raises(UnsafeHttpTargetError):
        twilio_outbound_call(
            credentials=CREDS,
            to="+15005551234",
            from_phone="+15005550006",
            twiml_url="https://demo.twilio.com/welcome/voice/",
            status_callback="http://169.254.169.254/",
        )


# ---------------------------------------------------------------------------
# twilio_call_status_handler tests
# ---------------------------------------------------------------------------


def test_status_completed():
    """completed → Category=completed, IsTerminal=True, CallDuration parsed."""
    result = twilio_call_status_handler(
        input={"CallSid": "CA1", "CallStatus": "completed", "CallDuration": "15"}
    )
    assert result["CallSid"] == "CA1"
    assert result["CallStatus"] == "completed"
    assert result["CallDuration"] == 15
    assert result["Category"] == "completed"
    assert result["IsTerminal"] is True


def test_status_failed():
    """failed → Category=failed, IsTerminal=True."""
    result = twilio_call_status_handler(
        input={"CallSid": "CA2", "CallStatus": "failed", "CallDuration": "0"}
    )
    assert result["Category"] == "failed"
    assert result["IsTerminal"] is True


def test_status_in_progress():
    """in-progress → Category=in_progress, IsTerminal=False."""
    result = twilio_call_status_handler(
        input={"CallSid": "CA3", "CallStatus": "in-progress"}
    )
    assert result["Category"] == "in_progress"
    assert result["IsTerminal"] is False


def test_status_no_answer():
    """no-answer → Category=no_answer, IsTerminal=True."""
    result = twilio_call_status_handler(
        input={"CallSid": "CA4", "CallStatus": "no-answer"}
    )
    assert result["Category"] == "no_answer"
    assert result["IsTerminal"] is True


def test_status_busy():
    """busy → Category=busy, IsTerminal=True."""
    result = twilio_call_status_handler(
        input={"CallSid": "CA5", "CallStatus": "busy"}
    )
    assert result["Category"] == "busy"
    assert result["IsTerminal"] is True


def test_status_unknown():
    """ringing (unknown status) → Category=other, IsTerminal=False."""
    result = twilio_call_status_handler(
        input={"CallSid": "CA6", "CallStatus": "ringing"}
    )
    assert result["Category"] == "other"
    assert result["IsTerminal"] is False

@pytest.fixture(autouse=True)
def _blocked_egress_posture(monkeypatch):
    """These tests verify the BLOCKED posture of nodyra_nodes.http_security;
    single-tenant API processes default to allowing private egress (mirroring
    workers), so pin the env explicitly."""
    monkeypatch.setenv("NODYRA_ALLOW_PRIVATE_EGRESS", "0")

