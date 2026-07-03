"""Tests for the Stripe event trigger v2."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import nodyra_nodes  # noqa: F401
from nodyra.sdk import registry
from nodyra_nodes.integrations_v2.providers.stripe import triggers as stripe_triggers
from nodyra_nodes.integrations_v2.specs import ProviderTriggerRequest


def _stripe_signature_header(
    raw_body: bytes,
    secret: str,
    timestamp: int | None = None,
) -> str:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    payload = f"{ts}.{raw_body.decode()}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def test_stripe_trigger_is_registered() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    assert "stripe_event_trigger_v2" in manifests
    m = manifests["stripe_event_trigger_v2"]
    assert m.name == "Stripe Event Trigger"
    assert m.category == "Triggers"


def test_stripe_trigger_verifies_signature() -> None:
    secret = "whsec_test"
    body = {
        "id": "evt_1",
        "type": "payment_intent.succeeded",
        "data": {"object": {}},
    }
    raw = json.dumps(body).encode()
    event = stripe_triggers.handle_stripe_event(
        ProviderTriggerRequest(
            headers={"Stripe-Signature": _stripe_signature_header(raw, secret)},
            query={},
            body=body,
            raw_body=raw,
        ),
        {"webhook_secret": secret, "event_types": "payment_intent.succeeded"},
    )
    assert event.response_status == 202
    assert event.dedupe_key == "stripe:evt_1"
    assert event.payload is not None
    assert event.payload["event_type"] == "payment_intent.succeeded"


def test_stripe_trigger_rejects_bad_signature() -> None:
    raw = b'{"id":"evt_1","type":"payment_intent.succeeded"}'
    event = stripe_triggers.handle_stripe_event(
        ProviderTriggerRequest(
            headers={"Stripe-Signature": "t=123,v1=badhash"},
            query={},
            body={},
            raw_body=raw,
        ),
        {"webhook_secret": "whsec_test", "event_types": ""},
    )
    assert event.response_status == 401
    assert event.payload is None


def test_stripe_trigger_filters_event_types() -> None:
    secret = "whsec_test"
    body = {"id": "evt_2", "type": "customer.created", "data": {"object": {}}}
    raw = json.dumps(body).encode()
    event = stripe_triggers.handle_stripe_event(
        ProviderTriggerRequest(
            headers={"Stripe-Signature": _stripe_signature_header(raw, secret)},
            query={},
            body=body,
            raw_body=raw,
        ),
        {"webhook_secret": secret, "event_types": "payment_intent.succeeded"},
    )
    assert event.payload is None
    assert event.response_status == 200


def test_stripe_trigger_accepts_all_when_event_types_blank() -> None:
    secret = "whsec_test"
    body = {"id": "evt_3", "type": "anything.happened", "data": {"object": {}}}
    raw = json.dumps(body).encode()
    event = stripe_triggers.handle_stripe_event(
        ProviderTriggerRequest(
            headers={"Stripe-Signature": _stripe_signature_header(raw, secret)},
            query={},
            body=body,
            raw_body=raw,
        ),
        {"webhook_secret": secret, "event_types": ""},
    )
    assert event.response_status == 202
    assert event.payload is not None
