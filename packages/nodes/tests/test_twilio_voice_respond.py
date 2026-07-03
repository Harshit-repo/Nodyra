"""Tests for the twilio_voice_respond node."""

from __future__ import annotations

import pytest

from nodyra_nodes.http_security import UnsafeHttpTargetError
from nodyra_nodes.integrations_v2.providers.twilio.voice_respond import twilio_voice_respond


def _call(**kwargs):
    """Invoke the node function directly."""
    return twilio_voice_respond(**kwargs)


def test_say_generates_twiml():
    result = _call(action="say", message="Hello")
    assert "<Say" in result["main"]
    assert "Hello</Say>" in result["main"]
    assert result["main"].startswith("<Response>")


def test_say_with_empty_message_uses_input():
    result = _call(action="say", message="", input="Hi there")
    assert "Hi there" in result["main"]
    assert "<Say" in result["main"]


def test_say_with_no_message_and_no_input_hangs_up():
    result = _call(action="say", message="", input=None)
    assert "<Hangup/>" in result["main"]
    assert "<Say" not in result["main"]


def test_play_generates_play_twiml():
    result = _call(action="play", audio_url="https://example.com/a.mp3")
    assert "<Play" in result["main"]
    assert "https://example.com/a.mp3" in result["main"]
    assert result["main"].startswith("<Response>")


def test_gather_generates_gather_twiml():
    result = _call(action="gather", message="Press 1")
    assert "<Gather" in result["main"]
    assert "<Say>Press 1</Say>" in result["main"]
    assert result["main"].startswith("<Response>")


def test_hangup_generates_hangup():
    result = _call(action="hangup")
    assert "<Hangup/>" in result["main"]
    assert result["main"].startswith("<Response>")


def test_redirect_generates_redirect():
    result = _call(action="redirect", redirect_url="https://example.com/twiml")
    assert "<Redirect>" in result["main"]
    assert "https://example.com/twiml" in result["main"]
    assert result["main"].startswith("<Response>")


def test_dial_generates_dial():
    result = _call(action="dial", dial_number="+15005550006")
    assert "<Dial" in result["main"]
    assert "<Number>+15005550006</Number>" in result["main"]
    assert result["main"].startswith("<Response>")


def test_message_truncation():
    long_msg = "A" * 5000
    result = _call(action="say", message=long_msg)
    say_content_start = result["main"].index(">", result["main"].index("<Say")) + 1
    say_content_end = result["main"].index("</Say>")
    say_content = result["main"][say_content_start:say_content_end]
    assert say_content.endswith("…")
    # 4096 chars + 1 ellipsis char = 4097
    assert len(say_content) <= 4097


def test_message_escapes_xml_injection():
    result = _call(action="say", message="<b>hello</b>")
    assert "&lt;b&gt;hello&lt;/b&gt;" in result["main"]
    assert "<b>" not in result["main"]


def test_audio_url_ssrf_blocked():
    with pytest.raises(UnsafeHttpTargetError):
        _call(action="play", audio_url="http://169.254.169.254/latest/meta-data/")
