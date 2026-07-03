"""Tests for IMAP email polling trigger."""

from __future__ import annotations

import email
import imaplib
from email.message import Message
from unittest.mock import MagicMock, patch

from nodyra_nodes.integrations_v2.providers.imap.triggers import (
    _creds_dict,
    _decode_header_value,
    _parse_email,
    poll_imap,
)
from nodyra_nodes.integrations_v2.registry import is_registered_provider_trigger
from nodyra_nodes.integrations_v2.specs import ProviderTriggerPollContext


def _ctx(params: dict, cursor: dict | None = None) -> ProviderTriggerPollContext:
    return ProviderTriggerPollContext(params=params, cursor=cursor or {})


_CREDS = {
    "host": "imap.example.com",
    "port": "993",
    "username": "user@example.com",
    "password": "secret",
    "tls": "true",
}

_PARAMS = {"credentials": _CREDS, "mailbox": "INBOX"}


def _make_simple_email(subject: str = "Hello", body: str = "World") -> bytes:
    msg = Message()
    msg["Subject"] = subject
    msg["From"] = "alice@example.com"
    msg["To"] = "bob@example.com"
    msg["Message-ID"] = "<test123@mail.example.com>"
    msg["Date"] = "Thu, 19 Jun 2026 10:30:00 +0000"
    msg.set_payload(body)
    msg.set_type("text/plain")
    return msg.as_bytes()


def _mock_imap(
    search_uids: list[bytes] = None,
    raw_email: bytes = None,
    message_id_hdr: bytes = None,
) -> MagicMock:
    conn = MagicMock(spec=imaplib.IMAP4_SSL)
    if search_uids is None:
        search_uids = [b"1"]
    uid_str = b" ".join(search_uids)
    conn.search.return_value = ("OK", [uid_str])
    conn.select.return_value = ("OK", [])

    if raw_email is None:
        raw_email = _make_simple_email()
    fetch_rfc822 = [((b"1 (RFC822 {%d})" % len(raw_email), raw_email), b")")]
    conn.fetch.return_value = ("OK", fetch_rfc822)

    mid_bytes = message_id_hdr or b"Message-ID: <test123@mail.example.com>\r\n\r\n"
    conn.fetch.side_effect = None
    # Return appropriate data based on fetch type
    def _fetch(uid, spec):
        spec_str = spec.decode() if isinstance(spec, bytes) else spec
        if "RFC822" in spec_str:
            return ("OK", [(b"1 (RFC822 {%d})" % len(raw_email), raw_email)])
        # BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]
        return ("OK", [(b"1 (BODY[HEADER.FIELDS (MESSAGE-ID)] {%d})" % len(mid_bytes), mid_bytes)])

    conn.fetch.side_effect = _fetch
    conn.store.return_value = ("OK", [])
    conn.close.return_value = ("OK", [])
    conn.logout.return_value = ("BYE", [])
    return conn


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


def test_decode_header_utf8():
    encoded = "=?UTF-8?B?SGVsbG8gV29ybGQ=?="  # "Hello World"
    assert _decode_header_value(encoded) == "Hello World"


def test_decode_header_plain():
    assert _decode_header_value("plain text") == "plain text"


def test_creds_dict_from_dict():
    d = {"host": "imap.test", "username": "u"}
    assert _creds_dict(d) == {"host": "imap.test", "username": "u"}


def test_parse_email_extracts_fields():
    raw = _make_simple_email("Subject line", "Body text")
    msg = email.message_from_bytes(raw)
    result = _parse_email(msg, include_attachments=False, attachment_max_size=10_000_000)
    assert result["subject"] == "Subject line"
    assert "alice@example.com" in result["from"]
    assert result["body_text"] == "Body text"
    assert result["attachments"] == []


# ---------------------------------------------------------------------------
# Poll function tests
# ---------------------------------------------------------------------------


@patch("nodyra_nodes.integrations_v2.providers.imap.triggers._connect")
def test_first_run_seeds_without_events(mock_connect):
    conn = _mock_imap(search_uids=[b"1", b"2", b"3"])
    mock_connect.return_value = conn

    result = poll_imap(_ctx(_PARAMS, cursor={}))

    assert result.events == []
    assert "seen_message_ids" in result.cursor
    assert isinstance(result.cursor["seen_message_ids"], list)


@patch("nodyra_nodes.integrations_v2.providers.imap.triggers._connect")
def test_subsequent_poll_returns_new_email(mock_connect):
    raw = _make_simple_email("New email", "New body")
    conn = _mock_imap(search_uids=[b"1"], raw_email=raw)
    mock_connect.return_value = conn

    # Cursor has been seeded but no "test123" yet
    ctx = _ctx(_PARAMS, cursor={"seen_message_ids": []})
    result = poll_imap(ctx)

    assert len(result.events) == 1
    evt = result.events[0]
    assert evt["subject"] == "New email"
    assert "alice@example.com" in evt["from"]
    assert "<test123@mail.example.com>" in result.cursor["seen_message_ids"]


@patch("nodyra_nodes.integrations_v2.providers.imap.triggers._connect")
def test_deduplication_skips_seen_emails(mock_connect):
    raw = _make_simple_email("Seen email", "seen body")
    conn = _mock_imap(search_uids=[b"1"], raw_email=raw)
    mock_connect.return_value = conn

    # message_id is already in seen set
    ctx = _ctx(_PARAMS, cursor={"seen_message_ids": ["<test123@mail.example.com>"]})
    result = poll_imap(ctx)

    assert result.events == []


@patch("nodyra_nodes.integrations_v2.providers.imap.triggers._connect")
def test_search_failure_returns_empty(mock_connect):
    conn = MagicMock(spec=imaplib.IMAP4_SSL)
    conn.select.return_value = ("OK", [])
    conn.search.return_value = ("NO", [b""])
    conn.close.return_value = ("OK", [])
    conn.logout.return_value = ("BYE", [])
    mock_connect.return_value = conn

    ctx = _ctx(_PARAMS, cursor={"seen_message_ids": []})
    result = poll_imap(ctx)
    assert result.events == []


@patch("nodyra_nodes.integrations_v2.providers.imap.triggers._connect")
def test_mark_as_read_calls_store(mock_connect):
    raw = _make_simple_email()
    conn = _mock_imap(search_uids=[b"1"], raw_email=raw)
    mock_connect.return_value = conn

    ctx = _ctx({**_PARAMS, "mark_as_read": "true"}, cursor={"seen_message_ids": []})
    poll_imap(ctx)

    conn.store.assert_called_once_with(b"1", "+FLAGS", "\\Seen")


@patch("nodyra_nodes.integrations_v2.providers.imap.triggers._connect")
def test_no_mark_as_read(mock_connect):
    raw = _make_simple_email()
    conn = _mock_imap(search_uids=[b"1"], raw_email=raw)
    mock_connect.return_value = conn

    ctx = _ctx({**_PARAMS, "mark_as_read": "false"}, cursor={"seen_message_ids": []})
    poll_imap(ctx)

    conn.store.assert_not_called()


def test_trigger_registered():
    assert is_registered_provider_trigger("imap_email_trigger")
