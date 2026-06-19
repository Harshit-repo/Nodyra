"""Twilio voice call trigger spec and lifecycle hooks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.parse
from typing import Any

from noodle_nodes.integrations_v2.registry import register_provider_trigger
from noodle_nodes.integrations_v2.specs import (
    OperationParamSpec,
    ProviderTriggerActivationContext,
    ProviderTriggerDeactivationContext,
    ProviderTriggerEvent,
    ProviderTriggerRequest,
    ProviderTriggerSpec,
    ProviderTriggerSubscription,
)

from noodle_nodes.integrations_v2.providers.twilio.operations import (
    _account_sid,
    _credentials_dict,
    _credentials_param,
    _transport,
)

TWILIO_API_BASE = "https://api.twilio.com"


TWILIO_VOICE_CALL_TRIGGER_SPEC = ProviderTriggerSpec(
    node_id="twilio_voice_call_trigger",
    name="Twilio Voice Call Trigger",
    provider="twilio",
    resource="voice",
    event="inbound_call",
    category="Communication",
    description="Start a workflow when an inbound voice call arrives on a configured Twilio phone number.",
    icon="brand:twilio",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="twilio_phone_number",
            required=True,
            placeholder="+1234567890",
            description="E.164 phone number to listen on.",
        ),
        OperationParamSpec(
            name="initial_greeting",
            default="",
            description="Text/TwiML spoken when call is answered.",
        ),
        OperationParamSpec(
            name="gather_speech",
            type="boolean",
            default=True,
            description="Enable speech/DTMF input collection.",
        ),
        OperationParamSpec(
            name="speech_timeout",
            type="integer",
            default=5,
            description="Seconds of silence before speech is done.",
        ),
        OperationParamSpec(
            name="max_speech_duration",
            type="integer",
            default=60,
            description="Max call duration in seconds.",
        ),
        OperationParamSpec(
            name="speech_language",
            default="en-US",
            description="Language for speech recognition.",
        ),
        OperationParamSpec(
            name="recording_enabled",
            type="boolean",
            default=False,
            description="Record the call.",
        ),
        OperationParamSpec(
            name="recording_channels",
            default="mono",
            choices=["mono", "dual"],
            description='Recording channel mode: "mono" or "dual".',
        ),
        OperationParamSpec(
            name="transcribe_callback",
            type="boolean",
            default=False,
            description="Request Twilio transcription after call.",
        ),
        OperationParamSpec(
            name="status_callback",
            type="boolean",
            default=False,
            description="Send call status events to noodle.",
        ),
        OperationParamSpec(
            name="response_mode",
            default="sync",
            choices=["sync", "async"],
            description='Response mode: "sync" or "async".',
        ),
    ),
    activate=lambda context: activate_voice_webhook(context),
    deactivate=lambda context: deactivate_voice_webhook(context),
    handle_event=lambda request, params: handle_voice_event(request, params),
)


def activate_voice_webhook(
    context: ProviderTriggerActivationContext,
) -> ProviderTriggerSubscription:
    params = context.params
    credentials = params.get("credentials")
    phone_number = str(params.get("twilio_phone_number") or "").strip()
    if not phone_number:
        raise ValueError("twilio_voice_call_trigger: twilio_phone_number is required")

    account_sid = _account_sid(credentials)
    transport = _transport(credentials)

    # Look up the phone number SID via Twilio IncomingPhoneNumbers API
    response = transport.request(
        "GET",
        f"/2010-04-01/Accounts/{account_sid}/IncomingPhoneNumbers.json",
        operation="list_incoming_phone_numbers",
        params={"PhoneNumber": phone_number},
    )

    phone_numbers = response.get("incoming_phone_numbers", []) if isinstance(response, dict) else []
    if not phone_numbers:
        raise ValueError(
            f"twilio_voice_call_trigger: phone number {phone_number!r} not found in this Twilio account"
        )

    phone_number_sid = str(phone_numbers[0].get("sid") or "")
    if not phone_number_sid:
        raise ValueError(
            f"twilio_voice_call_trigger: could not retrieve SID for phone number {phone_number!r}"
        )

    # Update the phone number's VoiceUrl and VoiceMethod
    transport.request(
        "POST",
        f"/2010-04-01/Accounts/{account_sid}/IncomingPhoneNumbers/{phone_number_sid}.json",
        operation="update_voice_url",
        data={"VoiceUrl": context.callback_url, "VoiceMethod": "POST"},
    )

    return ProviderTriggerSubscription(
        external_id=phone_number_sid,
        config={
            "twilio_phone_number": phone_number,
            "callback_url": context.callback_url,
        },
    )


def deactivate_voice_webhook(context: ProviderTriggerDeactivationContext) -> None:
    if not context.external_id:
        return

    params = context.params
    credentials = params.get("credentials")
    account_sid = _account_sid(credentials)
    transport = _transport(credentials)

    # Clear the phone number's VoiceUrl
    transport.request(
        "POST",
        f"/2010-04-01/Accounts/{account_sid}/IncomingPhoneNumbers/{context.external_id}.json",
        operation="clear_voice_url",
        data={"VoiceUrl": ""},
    )


def _validate_twilio_signature(auth_token: str, url: str, params: dict[str, Any], signature: str) -> bool:
    """Validate a Twilio webhook signature using HMAC-SHA1."""
    s = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    mac = hmac.new(auth_token.encode("utf-8"), s.encode("utf-8"), hashlib.sha1).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def _parse_form_body(raw_body: bytes) -> dict[str, str]:
    """Parse application/x-www-form-urlencoded body into a dict."""
    if not raw_body:
        return {}
    try:
        text = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        return {}
    parsed = urllib.parse.parse_qs(text, keep_blank_values=True)
    # parse_qs returns lists; take the first value for each key
    return {k: v[0] if v else "" for k, v in parsed.items()}


def _lower_headers(headers: dict[str, str]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in (headers or {}).items()}


def handle_voice_event(
    request: ProviderTriggerRequest,
    params: dict[str, Any],
) -> ProviderTriggerEvent:
    headers = _lower_headers(request.headers)
    signature = headers.get("x-twilio-signature", "")

    # Parse the form-encoded body
    if isinstance(request.body, dict):
        form_params: dict[str, Any] = request.body
    else:
        form_params = _parse_form_body(request.raw_body)

    # Validate Twilio signature
    credentials = params.get("credentials")
    creds = _credentials_dict(credentials)
    auth_token = str(creds.get("auth_token") or "")

    # _callback_url is injected by the trigger runtime from the subscription
    # config stored during activate (config["callback_url"]). Fail closed:
    # if it is missing, the runtime is misconfigured and we must not accept
    # the request — an unverifiable webhook is an unauthenticated request.
    callback_url = str(params.get("_callback_url") or "")

    if not auth_token or not callback_url:
        return ProviderTriggerEvent(
            payload=None,
            response_body={"message": "Twilio webhook misconfigured: cannot validate signature"},
            response_status=500,
        )

    if not signature or not _validate_twilio_signature(auth_token, callback_url, form_params, signature):
        return ProviderTriggerEvent(
            payload=None,
            response_body={"message": "Twilio webhook signature verification failed"},
            response_status=403,
        )

    # Skip completed calls — only fire on new inbound calls
    call_status = str(form_params.get("CallStatus") or "")
    if call_status == "completed":
        return ProviderTriggerEvent(
            payload=None,
            response_body={"message": "call completed, skipping"},
            response_status=200,
        )

    call_sid = str(form_params.get("CallSid") or "")

    return ProviderTriggerEvent(
        payload=dict(form_params),
        dedupe_key=f"twilio_call:{call_sid}" if call_sid else None,
        response_body={"message": "Twilio voice call event accepted"},
        response_status=202,
    )


register_provider_trigger(TWILIO_VOICE_CALL_TRIGGER_SPEC)
