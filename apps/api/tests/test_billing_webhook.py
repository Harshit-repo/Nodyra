"""Stripe webhook verification and event interpretation.

The webhook endpoint is unauthenticated by necessity — Stripe holds no Nodyra
credential — so the HMAC signature over the raw body *is* the authentication.
Everything a paying customer is entitled to flows from these bytes, which makes
this the highest-stakes parsing code in the billing path.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from app.config import settings
from app.services import billing

SECRET = "whsec_test_secret_value"


def _sign(payload: bytes, *, secret: str = SECRET, timestamp: int | None = None) -> str:
    moment = timestamp if timestamp is not None else int(time.time())
    signature = hmac.new(
        secret.encode(), f"{moment}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={moment},v1={signature}"


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(
        settings, "stripe_price_tiers", {"price_pro": "pro", "price_ent": "enterprise"}
    )


def _body(**overrides) -> bytes:
    event = {"id": "evt_1", "type": "customer.subscription.updated", "data": {"object": {}}}
    event.update(overrides)
    return json.dumps(event).encode()


# ── Signature verification ─────────────────────────────────────────────────


def test_a_correctly_signed_webhook_is_accepted():
    payload = _body()
    assert billing.verify_webhook(payload, _sign(payload))["id"] == "evt_1"


def test_an_unsigned_request_is_rejected():
    with pytest.raises(billing.WebhookVerificationError):
        billing.verify_webhook(_body(), "")


@pytest.mark.parametrize(
    "header",
    ["garbage", "t=notanumber,v1=abc", "v1=abc", "t=123", "t=123,v0=abc"],
)
def test_malformed_signature_headers_are_rejected(header):
    with pytest.raises(billing.WebhookVerificationError):
        billing.verify_webhook(_body(), header)


def test_a_signature_from_the_wrong_secret_is_rejected():
    payload = _body()
    with pytest.raises(billing.WebhookVerificationError):
        billing.verify_webhook(payload, _sign(payload, secret="whsec_attacker"))


def test_a_tampered_body_is_rejected():
    """The signature covers the bytes, so editing the tier after signing fails."""
    original = _body()
    header = _sign(original)
    tampered = original.replace(b'"evt_1"', b'"evt_2"')
    with pytest.raises(billing.WebhookVerificationError):
        billing.verify_webhook(tampered, header)


def test_an_old_signature_is_rejected():
    """Bounds replay of a body captured off the wire."""
    payload = _body()
    stale = int(time.time()) - billing.SIGNATURE_TOLERANCE_SECONDS - 60
    with pytest.raises(billing.WebhookVerificationError, match="tolerance"):
        billing.verify_webhook(payload, _sign(payload, timestamp=stale))


def test_a_future_signature_is_rejected():
    """A clock far ahead is as suspicious as one far behind."""
    payload = _body()
    ahead = int(time.time()) + billing.SIGNATURE_TOLERANCE_SECONDS + 60
    with pytest.raises(billing.WebhookVerificationError, match="tolerance"):
        billing.verify_webhook(payload, _sign(payload, timestamp=ahead))


def test_multiple_v1_signatures_are_all_considered():
    """Stripe sends several during a secret rotation; any valid one suffices."""
    payload = _body()
    moment = int(time.time())
    good = _sign(payload, timestamp=moment).split("v1=")[1]
    header = f"t={moment},v1={'0' * 64},v1={good}"
    assert billing.verify_webhook(payload, header)["id"] == "evt_1"


def test_a_non_json_body_is_rejected_even_when_correctly_signed():
    payload = b"not json at all"
    with pytest.raises(billing.WebhookVerificationError, match="not JSON"):
        billing.verify_webhook(payload, _sign(payload))


def test_a_json_array_body_is_rejected():
    payload = b"[1, 2, 3]"
    with pytest.raises(billing.WebhookVerificationError):
        billing.verify_webhook(payload, _sign(payload))


def test_verification_requires_a_configured_secret(monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", "")
    payload = _body()
    with pytest.raises(billing.BillingNotConfigured):
        billing.verify_webhook(payload, _sign(payload))


# ── Event interpretation ───────────────────────────────────────────────────


def _subscription_event(event_type: str, **obj) -> dict:
    base = {
        "id": "sub_123",
        "customer": "cus_456",
        "status": "active",
        "current_period_end": 1800000000,
        "items": {"data": [{"price": {"id": "price_pro"}, "quantity": 1}]},
    }
    base.update(obj)
    return {"id": "evt_x", "type": event_type, "data": {"object": base}}


def test_a_new_subscription_maps_price_to_tier_and_keeps_edition_seats():
    event = billing.interpret_event(_subscription_event("customer.subscription.created"))
    assert event is not None
    assert event.tier == "pro"
    assert event.seats == 0  # use the edition's included seats
    assert event.status == "active"
    assert event.provider_subscription_id == "sub_123"
    assert event.provider_customer_id == "cus_456"


def test_an_unmapped_price_grants_no_tier():
    """Buying a price this deployment has no mapping for must not silently
    grant the default — the row keeps whatever tier it already had."""
    event = billing.interpret_event(
        _subscription_event(
            "customer.subscription.updated",
            items={"data": [{"price": {"id": "price_unknown"}, "quantity": 1}]},
        )
    )
    assert event is not None
    assert event.tier is None


@pytest.mark.parametrize(
    "stripe_status,expected",
    [
        ("active", "active"),
        ("trialing", "trialing"),
        ("past_due", "past_due"),
        ("unpaid", "cancelled"),
        ("incomplete", "cancelled"),
        ("canceled", "cancelled"),
        ("incomplete_expired", "cancelled"),
        ("paused", "cancelled"),
        ("something_stripe_added_later", "cancelled"),
    ],
)
def test_status_mapping_fails_closed(stripe_status, expected):
    """An unmapped status must never leave a subscription entitled."""
    event = billing.interpret_event(
        _subscription_event("customer.subscription.updated", status=stripe_status)
    )
    assert event is not None and event.status == expected


def test_deletion_always_cancels_regardless_of_reported_status():
    event = billing.interpret_event(
        _subscription_event("customer.subscription.deleted", status="active")
    )
    assert event is not None and event.status == "cancelled"


def test_checkout_completion_captures_the_customer_but_not_the_tier():
    """Checkout carries no price; the subscription.created that follows does."""
    event = billing.interpret_event(
        {
            "id": "evt_checkout",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "subscription": "sub_new",
                    "customer": "cus_new",
                    "customer_details": {"email": "buyer@example.com", "name": "Buyer Co"},
                }
            },
        }
    )
    assert event is not None
    assert event.provider_subscription_id == "sub_new"
    assert event.customer_email == "buyer@example.com"
    assert event.customer_name == "Buyer Co"
    assert event.tier is None


def test_expanded_objects_and_bare_ids_both_resolve():
    """Stripe expands references inconsistently depending on the endpoint."""
    expanded = billing.interpret_event(
        _subscription_event("customer.subscription.updated", customer={"id": "cus_obj"})
    )
    assert expanded is not None and expanded.provider_customer_id == "cus_obj"


def test_unhandled_event_types_are_ignored():
    """Returning None lets the endpoint answer 200 so Stripe stops retrying."""
    assert billing.interpret_event({"id": "e", "type": "invoice.created", "data": {}}) is None


def test_a_subscription_with_no_items_does_not_crash():
    event = billing.interpret_event(
        _subscription_event("customer.subscription.updated", items={"data": []})
    )
    assert event is not None and event.tier is None and event.seats == 0


def test_cancel_at_takes_precedence_over_period_end():
    """A scheduled cancellation is the real end of access."""
    event = billing.interpret_event(
        _subscription_event("customer.subscription.updated", cancel_at=1700000000)
    )
    assert event is not None and event.ends_at == 1700000000


def test_modern_item_period_gives_bounded_outage_grace(monkeypatch):
    monkeypatch.setattr(settings, "license_validity_days", 45)
    event = billing.interpret_event(_subscription_event(
        "customer.subscription.updated", current_period_end=None,
        items={"data": [{"price": "price_pro", "quantity": 1, "current_period_end": 1800000000}]},
    ))
    assert event.status == "active"
    assert event.ends_at == 1800000000 + 45 * 86400


def test_cancel_at_period_end_does_not_extend_the_paid_term():
    event = billing.interpret_event(_subscription_event("customer.subscription.updated", cancel_at_period_end=True))
    assert event.ends_at == 1800000000


@pytest.mark.parametrize("quantity", [0, 2, 100])
def test_checkout_quantity_is_not_mistaken_for_licensed_seats(quantity):
    event = billing.interpret_event(_subscription_event("customer.subscription.updated", items={"data": [{"price": "price_pro", "quantity": quantity}]}))
    assert event.status == "cancelled"
