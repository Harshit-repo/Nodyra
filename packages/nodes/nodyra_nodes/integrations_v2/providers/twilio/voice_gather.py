"""Twilio Voice Gather and Voicemail Detect nodes."""

from __future__ import annotations

import xml.sax.saxutils as saxutils

from nodyra.sdk import node
from nodyra_nodes.http_security import assert_public_http_url


def _escape(text: str) -> str:
    return saxutils.escape(text)


@node(
    name="Twilio Voice Gather",
    id="twilio_voice_gather",
    category="Communication",
    icon="brand:twilio",
    role="executable",
    usable_as_tool=False,
    params={
        "mode": {
            "choices": ["prompt", "parse"],
            "description": '"prompt" generates TwiML <Gather>; "parse" extracts gathered data from the wired input.',
        },
        "input_type": {
            "choices": ["dtmf", "speech", "both"],
            "description": "Type of input to collect.",
        },
        "prompt": {
            "multiline": True,
            "description": "What to say before listening.",
        },
        "num_digits": {
            "description": "Max DTMF digits (0 = unlimited).",
        },
        "finish_on_key": {
            "description": "DTMF key that ends input.",
        },
        "speech_timeout": {
            "description": "Seconds of silence before speech is final.",
        },
        "speech_language": {
            "description": "Language hint for Twilio speech, e.g. en-US.",
        },
        "speech_hints": {
            "description": "Comma-separated vocabulary hints.",
        },
        "max_speech_duration": {
            "description": "Max recording length in seconds.",
        },
        "action_url": {
            "description": "URL to POST gathered data to (optional).",
        },
    },
)
def twilio_voice_gather(
    input=None,
    mode: str = "prompt",
    input_type: str = "dtmf",
    prompt: str = "",
    num_digits: int = 0,
    finish_on_key: str = "#",
    speech_timeout: int = 5,
    speech_language: str = "en-US",
    speech_hints: str = "",
    max_speech_duration: int = 15,
    action_url: str = "",
) -> dict:
    """Collect DTMF/speech input from a caller or parse gathered Twilio params."""

    mode = (mode or "prompt").strip().lower()

    if mode == "prompt":
        # Build <Gather> TwiML
        attrs = f'input="{input_type}" timeout="{speech_timeout}" language="{speech_language}"'
        if num_digits > 0:
            attrs += f' numDigits="{num_digits}"'
        if finish_on_key:
            attrs += f' finishOnKey="{finish_on_key}"'
        if speech_hints:
            attrs += f' hints="{_escape(speech_hints)}"'
        if max_speech_duration > 0:
            attrs += f' speechTimeout="{max_speech_duration}"'
        if action_url:
            assert_public_http_url(action_url)
            attrs += f' action="{action_url}"'

        inner = ""
        if prompt:
            inner = f"<Say>{_escape(prompt)}</Say>"

        twiml = f"<Response><Gather {attrs}>{inner}</Gather></Response>"
        return {"main": twiml}

    elif mode == "parse":
        # Extract gathered data from the wired input dict
        data = input if isinstance(input, dict) else {}

        digits = str(data.get("Digits", "") or "")
        speech_result = str(data.get("SpeechResult", "") or "")
        raw_confidence = data.get("Confidence", "0")
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            confidence = 0.0

        # Detect which input type was present
        if speech_result:
            detected_input_type = "speech"
        elif digits:
            detected_input_type = "dtmf"
        else:
            detected_input_type = "dtmf"

        call_sid = str(data.get("CallSid", "") or "")

        return {
            "main": {
                "Digits": digits,
                "SpeechResult": speech_result,
                "Confidence": confidence,
                "InputType": detected_input_type,
                "CallSid": call_sid,
            }
        }

    else:
        raise ValueError(f"twilio_voice_gather: unknown mode {mode!r}")


@node(
    name="Voicemail Detect",
    id="voicemail_detect",
    category="Communication",
    icon="brand:twilio",
    role="executable",
    usable_as_tool=False,
    params={
        "amd_enabled": {
            "description": "Whether AMD was requested on this call.",
        },
        "voicemail_behavior": {
            "choices": ["skip", "leave_message", "hangup"],
            "description": '"skip", "leave_message", or "hangup".',
        },
    },
)
def voicemail_detect(
    input=None,
    amd_enabled: bool = True,
    voicemail_behavior: str = "skip",
) -> dict:
    """Detect whether an outbound call was answered by a human or voicemail (AMD)."""

    data = input if isinstance(input, dict) else {}

    answered_by = str(data.get("AnsweredBy", "") or "unknown")
    call_sid = str(data.get("CallSid", "") or "")

    is_voicemail = answered_by.startswith("machine") or answered_by == "fax"

    return {
        "main": {
            "AnsweredBy": answered_by,
            "CallSid": call_sid,
            "IsVoicemail": is_voicemail,
            "Behavior": voicemail_behavior,
        }
    }
