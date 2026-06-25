"""Twilio Outbound Call and Call Status Handler nodes."""

from __future__ import annotations

from noodle.sdk import node
from noodle_nodes.http_security import assert_public_http_url
from noodle_nodes.integrations_v2.providers.twilio.operations import (
    _account_sid,
    _transport,
)

# ---------------------------------------------------------------------------
# Status mapping for twilio_call_status_handler
# ---------------------------------------------------------------------------

_STATUS_MAP: dict[str, tuple[str, bool]] = {
    "completed": ("completed", True),
    "failed": ("failed", True),
    "no-answer": ("no_answer", True),
    "busy": ("busy", True),
    "canceled": ("other", True),
    "in-progress": ("in_progress", False),
}


@node(
    name="Twilio Outbound Call",
    id="twilio_outbound_call",
    category="Communication",
    icon="brand:twilio",
    role="executable",
    tool_side_effecting=True,
    params={
        "credentials": {
            "type": "credential",
            "description": "Twilio Account SID and Auth Token.",
        },
        "to": {
            "description": "Destination E.164 number. Falls back to wired input if empty.",
        },
        "from_phone": {
            "description": "Twilio phone number to call from.",
        },
        "twiml_url": {
            "description": "URL that returns TwiML. If empty, a simple <Say>Hello</Say> TwiML is used.",
        },
        "status_callback": {
            "description": "URL for call status updates.",
        },
        "timeout": {
            "description": "Ring timeout in seconds.",
        },
        "caller_id": {
            "description": "Override caller ID.",
        },
        "machine_detection": {
            "choices": ["Enable", "DetectMessageEnd"],
            "description": "AMD mode.",
        },
        "record": {
            "description": "Record the call.",
        },
    },
)
def twilio_outbound_call(
    input=None,
    credentials=None,
    to: str = "",
    from_phone: str = "",
    twiml_url: str = "",
    status_callback: str = "",
    timeout: int = 30,
    caller_id: str = "",
    machine_detection: str = "Enable",
    record: bool = False,
) -> dict:
    """Make an outbound voice call via Twilio."""

    # Resolve destination number — fall back to wired input
    to_number = to.strip() if to else str(input or "").strip()
    if not to_number:
        raise ValueError("twilio_outbound_call: 'to' is required (or pass it as wired input)")
    if not from_phone:
        raise ValueError("twilio_outbound_call: 'from_phone' is required")

    # SSRF guards on user-supplied URLs
    if twiml_url:
        assert_public_http_url(twiml_url)
    if status_callback:
        assert_public_http_url(status_callback)

    transport = _transport(credentials)
    account_sid = _account_sid(credentials)

    # Build form-encoded body
    data: dict[str, str] = {
        "To": to_number,
        "From": from_phone,
        "Timeout": str(int(timeout)),
        "MachineDetection": machine_detection,
        "Record": "true" if record else "false",
    }

    if twiml_url:
        data["Url"] = twiml_url
    else:
        data["Twiml"] = "<Response><Say>Hello</Say></Response>"

    if status_callback:
        data["StatusCallback"] = status_callback
    if caller_id:
        data["CallerId"] = caller_id

    result = transport.request(
        "POST",
        f"/2010-04-01/Accounts/{account_sid}/Calls.json",
        operation="create_call",
        data=data,
    )

    return {
        "CallSid": result.get("sid", ""),
        "Status": result.get("status", ""),
        "Direction": result.get("direction", ""),
        "To": result.get("to", ""),
        "From": result.get("from", ""),
    }


@node(
    name="Twilio Call Status Handler",
    id="twilio_call_status_handler",
    category="Communication",
    icon="brand:twilio",
    role="executable",
    tool_side_effecting=False,
    params={},
)
def twilio_call_status_handler(
    input=None,
) -> dict:
    """Parse a Twilio call status callback and classify the call status."""

    data = input if isinstance(input, dict) else {}

    call_sid = str(data.get("CallSid", "") or "")
    call_status = str(data.get("CallStatus", "") or "")
    from_number = str(data.get("From", "") or "")
    to_number = str(data.get("To", "") or "")

    raw_duration = data.get("CallDuration", "0") or "0"
    try:
        call_duration = int(raw_duration)
    except (TypeError, ValueError):
        call_duration = 0

    category, is_terminal = _STATUS_MAP.get(call_status, ("other", False))

    return {
        "CallSid": call_sid,
        "CallStatus": call_status,
        "CallDuration": call_duration,
        "Category": category,
        "IsTerminal": is_terminal,
        "From": from_number,
        "To": to_number,
    }
