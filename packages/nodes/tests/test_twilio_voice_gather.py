"""Tests for twilio_voice_gather and voicemail_detect nodes."""

from __future__ import annotations

from noodle_nodes.integrations_v2.providers.twilio.voice_gather import (
    twilio_voice_gather,
    voicemail_detect,
)

# ---------------------------------------------------------------------------
# twilio_voice_gather tests
# ---------------------------------------------------------------------------


def test_prompt_mode_generates_gather_twiml():
    """mode=prompt with speech input produces <Gather> and <Say> TwiML."""
    result = twilio_voice_gather(
        mode="prompt",
        prompt="Say hello",
        input_type="speech",
    )
    twiml = result["main"]
    assert "<Gather" in twiml
    assert "<Say>Say hello</Say>" in twiml
    assert twiml.startswith("<Response>")
    assert twiml.endswith("</Response>")


def test_prompt_mode_with_dtmf():
    """mode=prompt with dtmf and num_digits=1 includes numDigits attribute."""
    result = twilio_voice_gather(
        mode="prompt",
        input_type="dtmf",
        num_digits=1,
    )
    twiml = result["main"]
    assert 'numDigits="1"' in twiml
    assert "<Gather" in twiml


def test_prompt_mode_with_action_url():
    """mode=prompt with a valid action_url includes action= in <Gather>."""
    result = twilio_voice_gather(
        mode="prompt",
        action_url="https://example.com/handle",
    )
    twiml = result["main"]
    assert 'action="https://example.com/handle"' in twiml
    assert "<Gather" in twiml


def test_parse_mode_extracts_digits():
    """mode=parse extracts DTMF digits and sets InputType='dtmf'."""
    result = twilio_voice_gather(
        mode="parse",
        input={"Digits": "42", "CallSid": "CA1"},
    )
    data = result["main"]
    assert data["Digits"] == "42"
    assert data["InputType"] == "dtmf"
    assert data["CallSid"] == "CA1"
    assert data["SpeechResult"] == ""


def test_parse_mode_extracts_speech():
    """mode=parse extracts speech result and confidence correctly."""
    result = twilio_voice_gather(
        mode="parse",
        input={"SpeechResult": "hello world", "Confidence": "0.95", "CallSid": "CA1"},
    )
    data = result["main"]
    assert data["SpeechResult"] == "hello world"
    assert abs(data["Confidence"] - 0.95) < 1e-9
    assert data["InputType"] == "speech"
    assert data["CallSid"] == "CA1"


def test_prompt_escapes_xml():
    """Dangerous XML characters in prompt are escaped in output TwiML."""
    result = twilio_voice_gather(
        mode="prompt",
        prompt="<test>&",
    )
    twiml = result["main"]
    # The raw characters must NOT appear literally
    assert "<test>" not in twiml
    assert "&amp;" in twiml or "&lt;" in twiml
    # Escaped form should be present
    assert "&lt;test&gt;&amp;" in twiml


# ---------------------------------------------------------------------------
# voicemail_detect tests
# ---------------------------------------------------------------------------


def test_detects_human():
    """AnsweredBy=human yields IsVoicemail=False."""
    result = voicemail_detect(input={"AnsweredBy": "human", "CallSid": "CA1"})
    data = result["main"]
    assert data["IsVoicemail"] is False
    assert data["AnsweredBy"] == "human"
    assert data["CallSid"] == "CA1"


def test_detects_machine():
    """AnsweredBy=machine_start yields IsVoicemail=True."""
    result = voicemail_detect(input={"AnsweredBy": "machine_start", "CallSid": "CA1"})
    data = result["main"]
    assert data["IsVoicemail"] is True
    assert data["AnsweredBy"] == "machine_start"


def test_unknown_answeredby():
    """AnsweredBy=unknown yields IsVoicemail=False and AnsweredBy='unknown'."""
    result = voicemail_detect(input={"AnsweredBy": "unknown"})
    data = result["main"]
    assert data["IsVoicemail"] is False
    assert data["AnsweredBy"] == "unknown"
