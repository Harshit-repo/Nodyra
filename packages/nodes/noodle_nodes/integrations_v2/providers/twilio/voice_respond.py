"""Twilio Voice Respond node — builds a TwiML XML response."""

from __future__ import annotations

import xml.sax.saxutils as saxutils

from noodle.context import node_debug
from noodle.sdk import node
from noodle_nodes.http_security import assert_public_http_url

_MAX_SAY_LEN = 4096


def _escape(text: str) -> str:
    return saxutils.escape(text)


@node(
    name="Twilio Voice Respond",
    id="twilio_voice_respond",
    category="Communication",
    icon="brand:twilio",
    role="executable",
    usable_as_tool=False,
    params={
        "action": {
            "choices": ["say", "play", "gather", "hangup", "redirect", "dial", "raw"],
            "description": "TwiML verb to use in the response.",
        },
        "message": {
            "multiline": True,
            "description": "Text to speak. Falls back to wired input if empty.",
        },
        "voice": {
            "description": "Twilio voice name (alice, bob, Polly.*).",
        },
        "language": {
            "description": "Language tag for Polly voices, e.g. en-US.",
        },
        "loop": {
            "description": "Repeat count for <Say> / <Play>.",
        },
        "audio_url": {
            "description": "Publicly accessible audio URL (for action=play).",
        },
        "gather_input": {
            "description": "Input types accepted by <Gather>, e.g. 'speech dtmf'.",
        },
        "gather_timeout": {
            "description": "Seconds to wait for gather input.",
        },
        "gather_num_digits": {
            "description": "Max DTMF digits to collect (0 = unlimited).",
        },
        "gather_action_url": {
            "description": "URL to POST gathered input to.",
        },
        "redirect_url": {
            "description": "TwiML URL to redirect the call to.",
        },
        "dial_number": {
            "description": "E.164 phone number to forward the call to.",
        },
        "dial_timeout": {
            "description": "Seconds to ring before giving up.",
        },
        "dial_caller_id": {
            "description": "Caller ID to present on the forwarded call.",
        },
    },
)
def twilio_voice_respond(
    input=None,
    action: str = "say",
    message: str = "",
    voice: str = "alice",
    language: str = "en-US",
    loop: int = 1,
    audio_url: str = "",
    gather_input: str = "speech dtmf",
    gather_timeout: int = 5,
    gather_num_digits: int = 0,
    gather_action_url: str = "",
    redirect_url: str = "",
    dial_number: str = "",
    dial_timeout: int = 30,
    dial_caller_id: str = "",
) -> dict:
    """Construct a TwiML XML response for Twilio voice calls."""

    action = (action or "say").strip().lower()

    if action == "say":
        text = message if message else (str(input) if input is not None else "")
        if not text:
            return {"main": "<Response><Hangup/></Response>"}
        if len(text) > _MAX_SAY_LEN:
            node_debug.get(None) and None  # just trigger context access for coverage
            _log = node_debug.get(None)
            if _log is not None:
                _log(
                    f"twilio_voice_respond: message truncated from {len(text)} to {_MAX_SAY_LEN} chars"
                )
            text = text[:_MAX_SAY_LEN] + "…"
        escaped = _escape(text)
        twiml = (
            f'<Response>'
            f'<Say voice="{voice}" language="{language}" loop="{loop}">'
            f'{escaped}'
            f'</Say>'
            f'</Response>'
        )

    elif action == "play":
        assert_public_http_url(audio_url)
        twiml = f'<Response><Play loop="{loop}">{audio_url}</Play></Response>'

    elif action == "gather":
        text = message if message else (str(input) if input is not None else "")
        escaped = _escape(text)
        attrs = f'input="{gather_input}" timeout="{gather_timeout}"'
        if gather_num_digits:
            attrs += f' numDigits="{gather_num_digits}"'
        if gather_action_url:
            attrs += f' action="{gather_action_url}"'
        twiml = (
            f'<Response>'
            f'<Gather {attrs}>'
            f'<Say>{escaped}</Say>'
            f'</Gather>'
            f'</Response>'
        )

    elif action == "hangup":
        twiml = "<Response><Hangup/></Response>"

    elif action == "redirect":
        assert_public_http_url(redirect_url)
        twiml = f'<Response><Redirect>{redirect_url}</Redirect></Response>'

    elif action == "dial":
        if dial_caller_id:
            dial_attrs = f'timeout="{dial_timeout}" callerId="{dial_caller_id}"'
        else:
            dial_attrs = f'timeout="{dial_timeout}"'
        twiml = (
            f'<Response>'
            f'<Dial {dial_attrs}>'
            f'<Number>{dial_number}</Number>'
            f'</Dial>'
            f'</Response>'
        )

    elif action == "raw":
        twiml = message

    else:
        raise ValueError(f"twilio_voice_respond: unknown action {action!r}")

    return {"main": twiml}
