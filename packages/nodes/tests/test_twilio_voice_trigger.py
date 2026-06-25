"""Tests for Twilio Voice Call Trigger (twilio_voice_call_trigger)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.parse
from unittest.mock import MagicMock, patch

import pytest

from noodle_nodes.integrations_v2.providers.twilio.voice_trigger import (
    activate_voice_webhook,
    deactivate_voice_webhook,
    handle_voice_event,
)
from noodle_nodes.integrations_v2.specs import (
    ProviderTriggerActivationContext,
    ProviderTriggerDeactivationContext,
    ProviderTriggerRequest,
    ProviderTriggerSubscription,
)

_CREDS = {"account_sid": "ACtest", "auth_token": "secret"}
_PHONE = "+15005550006"
_PN_SID = "PNabc123"
_CALLBACK = "https://noodle.example.com/api/webhooks/triggers/wh1"


def _make_activation_context(**override) -> ProviderTriggerActivationContext:
    return ProviderTriggerActivationContext(
        workflow_id="wf1",
        workflow_version_id="wv1",
        node_id="n1",
        callback_url=_CALLBACK,
        params={
            "credentials": _CREDS,
            "twilio_phone_number": _PHONE,
            **override,
        },
    )


def _make_signed_request(
    auth_token: str, url: str, params: dict[str, str]
) -> ProviderTriggerRequest:
    """Create a mock request with a valid Twilio signature."""
    s = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    mac = hmac.new(auth_token.encode(), s.encode(), hashlib.sha1).digest()
    sig = base64.b64encode(mac).decode()
    body_str = urllib.parse.urlencode(params)
    return ProviderTriggerRequest(
        headers={"x-twilio-signature": sig},
        query={},
        body=params,
        raw_body=body_str.encode(),
    )


# ---------------------------------------------------------------------------
# 1. activate sets voice URL
# ---------------------------------------------------------------------------


def test_activate_sets_voice_url():
    ctx = _make_activation_context()
    list_response = {"incoming_phone_numbers": [{"sid": _PN_SID, "voice_url": ""}]}
    with patch(
        "noodle_nodes.integrations_v2.providers.twilio.voice_trigger._transport"
    ) as mock_transport_fn:
        transport = MagicMock()
        mock_transport_fn.return_value = transport
        transport.request.side_effect = [list_response, {}]

        result = activate_voice_webhook(ctx)

    assert isinstance(result, ProviderTriggerSubscription)
    assert result.external_id == _PN_SID
    assert result.config["twilio_phone_number"] == _PHONE
    assert result.config["callback_url"] == _CALLBACK
    # Verify the update call set VoiceUrl and VoiceMethod
    update_call = transport.request.call_args_list[1]
    assert update_call.kwargs["data"]["VoiceUrl"] == _CALLBACK
    assert update_call.kwargs["data"]["VoiceMethod"] == "POST"


# ---------------------------------------------------------------------------
# 2. activate raises if phone number not found
# ---------------------------------------------------------------------------


def test_activate_raises_if_phone_not_found():
    ctx = _make_activation_context()
    with patch(
        "noodle_nodes.integrations_v2.providers.twilio.voice_trigger._transport"
    ) as mock_transport_fn:
        transport = MagicMock()
        mock_transport_fn.return_value = transport
        transport.request.return_value = {"incoming_phone_numbers": []}

        with pytest.raises(ValueError, match="not found"):
            activate_voice_webhook(ctx)


# ---------------------------------------------------------------------------
# 3. deactivate clears voice URL
# ---------------------------------------------------------------------------


def test_deactivate_clears_voice_url():
    ctx = ProviderTriggerDeactivationContext(
        workflow_id="wf1",
        workflow_version_id="wv1",
        node_id="n1",
        external_id=_PN_SID,
        params={"credentials": _CREDS},
    )
    with patch(
        "noodle_nodes.integrations_v2.providers.twilio.voice_trigger._transport"
    ) as mock_transport_fn:
        transport = MagicMock()
        mock_transport_fn.return_value = transport
        transport.request.return_value = {}

        deactivate_voice_webhook(ctx)

    update_call = transport.request.call_args
    assert update_call.kwargs["data"]["VoiceUrl"] == ""
    assert _PN_SID in update_call.args[1]


# ---------------------------------------------------------------------------
# 4. handle_event returns payload on valid signature
# ---------------------------------------------------------------------------


def test_handle_event_returns_payload_on_valid_signature():
    call_params = {
        "CallSid": "CA123",
        "From": "+1555000",
        "To": _PHONE,
        "CallStatus": "ringing",
        "Direction": "inbound",
    }
    request = _make_signed_request(_CREDS["auth_token"], _CALLBACK, call_params)
    node_params = {"credentials": _CREDS, "_callback_url": _CALLBACK}

    event = handle_voice_event(request, node_params)

    assert event.payload is not None
    assert event.payload["CallSid"] == "CA123"
    assert event.dedupe_key == "twilio_call:CA123"
    assert event.response_status == 202


# ---------------------------------------------------------------------------
# 5. handle_event rejects invalid signature
# ---------------------------------------------------------------------------


def test_handle_event_rejects_invalid_signature():
    call_params = {"CallSid": "CA123", "CallStatus": "ringing"}
    body_str = urllib.parse.urlencode(call_params)
    request = ProviderTriggerRequest(
        headers={"x-twilio-signature": "BADSIG"},
        query={},
        body=call_params,
        raw_body=body_str.encode(),
    )
    node_params = {"credentials": _CREDS, "_callback_url": _CALLBACK}

    event = handle_voice_event(request, node_params)

    assert event.payload is None
    assert event.response_status == 403


# ---------------------------------------------------------------------------
# 6. handle_event skips completed calls
# ---------------------------------------------------------------------------


def test_handle_event_skips_completed_calls():
    """Completed calls are skipped after a valid signature check."""
    call_params = {"CallSid": "CA123", "CallStatus": "completed"}
    request = _make_signed_request(_CREDS["auth_token"], _CALLBACK, call_params)
    node_params = {"credentials": _CREDS, "_callback_url": _CALLBACK}

    event = handle_voice_event(request, node_params)

    assert event.payload is None
    assert event.response_status == 200


def test_handle_event_fails_closed_when_callback_url_missing():
    """Without _callback_url the runtime cannot validate — must fail with 500."""
    call_params = {"CallSid": "CA123", "CallStatus": "ringing"}
    body_str = urllib.parse.urlencode(call_params)
    request = ProviderTriggerRequest(
        headers={"x-twilio-signature": "anything"},
        query={},
        body=call_params,
        raw_body=body_str.encode(),
    )
    node_params = {"credentials": _CREDS}  # no _callback_url

    event = handle_voice_event(request, node_params)

    assert event.payload is None
    assert event.response_status == 500


# ---------------------------------------------------------------------------
# Bonus: registration sanity check
# ---------------------------------------------------------------------------


def test_trigger_is_registered():
    from noodle_nodes.integrations_v2.registry import is_registered_provider_trigger

    assert is_registered_provider_trigger("twilio_voice_call_trigger")
