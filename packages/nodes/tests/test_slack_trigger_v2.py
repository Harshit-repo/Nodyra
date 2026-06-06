"""Tests for the Slack event trigger v2."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import noodle_nodes  # noqa: F401
from noodle.sdk import registry
from noodle_nodes.integrations_v2.providers.slack import triggers as slack_triggers
from noodle_nodes.integrations_v2.specs import ProviderTriggerRequest


def _signed_headers(
    raw_body: bytes,
    signing_secret: str,
    timestamp: int | None = None,
) -> dict[str, str]:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    basestring = f"v0:{ts}:{raw_body.decode()}".encode()
    sig = "v0=" + hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": sig,
        "Content-Type": "application/json",
    }


def test_slack_trigger_is_registered() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    assert "slack_event_trigger_v2" in manifests
    m = manifests["slack_event_trigger_v2"]
    assert m.name == "Slack Event Trigger"
    assert m.category == "Triggers"


def test_slack_trigger_url_challenge() -> None:
    raw = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    secret = "signing_secret"
    event = slack_triggers.handle_slack_event(
        ProviderTriggerRequest(
            headers=_signed_headers(raw, secret),
            query={},
            body={"type": "url_verification", "challenge": "abc123"},
            raw_body=raw,
        ),
        {"signing_secret": secret, "event_types": "message"},
    )
    assert event.response_status == 200
    assert event.response_body == {"challenge": "abc123"}
    assert event.payload is None


def test_slack_trigger_rejects_bad_signature() -> None:
    raw = b'{"type":"event_callback","event":{"type":"message"}}'
    event = slack_triggers.handle_slack_event(
        ProviderTriggerRequest(
            headers={
                "X-Slack-Request-Timestamp": str(int(time.time())),
                "X-Slack-Signature": "v0=badhash",
            },
            query={},
            body={"type": "event_callback"},
            raw_body=raw,
        ),
        {"signing_secret": "secret", "event_types": "message"},
    )
    assert event.response_status == 401
    assert event.payload is None


def test_slack_trigger_rejects_stale_timestamp() -> None:
    secret = "signing_secret"
    raw = b'{"type":"event_callback","event":{"type":"message"}}'
    stale_ts = int(time.time()) - 400
    event = slack_triggers.handle_slack_event(
        ProviderTriggerRequest(
            headers=_signed_headers(raw, secret, timestamp=stale_ts),
            query={},
            body={"type": "event_callback"},
            raw_body=raw,
        ),
        {"signing_secret": secret, "event_types": "message"},
    )
    assert event.response_status == 401
    assert event.payload is None


def test_slack_trigger_filters_unwanted_event_types() -> None:
    secret = "signing_secret"
    body = {
        "type": "event_callback",
        "event": {"type": "reaction_added"},
        "event_id": "Ev1",
    }
    raw = json.dumps(body).encode()
    event = slack_triggers.handle_slack_event(
        ProviderTriggerRequest(
            headers=_signed_headers(raw, secret),
            query={},
            body=body,
            raw_body=raw,
        ),
        {"signing_secret": secret, "event_types": "message"},
    )
    assert event.payload is None
    assert event.response_status == 200


def test_slack_trigger_dispatches_matching_event() -> None:
    secret = "signing_secret"
    body = {
        "type": "event_callback",
        "team_id": "T123",
        "event_id": "Ev42",
        "event": {"type": "message", "text": "hello", "user": "U1", "channel": "C1"},
    }
    raw = json.dumps(body).encode()
    event = slack_triggers.handle_slack_event(
        ProviderTriggerRequest(
            headers=_signed_headers(raw, secret),
            query={},
            body=body,
            raw_body=raw,
        ),
        {"signing_secret": secret, "event_types": "message, reaction_added"},
    )
    assert event.response_status == 202
    assert event.dedupe_key == "slack:Ev42"
    assert event.payload is not None
    assert event.payload["event_type"] == "message"
    assert event.payload["team_id"] == "T123"
