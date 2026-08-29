"""Stripe checkout and webhook handling for self-serve subscriptions.

Deliberately talks to Stripe's REST API over ``httpx`` rather than pulling in the
``stripe`` SDK: the three calls needed here are simple form posts, and the SDK
brings a large transitive tree into an image that already ships a workflow
engine. The one piece worth writing carefully is webhook signature
verification, which is implemented below to Stripe's documented scheme.

Everything here is inert unless ``stripe_secret_key`` is set. A vendor running
enterprise contracts by hand can use the subscription registry with no payment
provider at all.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

STRIPE_API_BASE = "https://api.stripe.com/v1"

# Stripe's own recommended tolerance. Rejecting older signatures bounds replay
# of a captured webhook body to five minutes.
SIGNATURE_TOLERANCE_SECONDS = 300

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class BillingNotConfigured(RuntimeError):
    """Raised when a Stripe call is attempted without credentials."""


class WebhookVerificationError(ValueError):
    """Raised when an inbound webhook fails signature verification."""


@dataclass(frozen=True)
class SubscriptionEvent:
    """The subset of a Stripe event this system acts on."""

    event_id: str
    event_type: str
    provider_subscription_id: str | None
    provider_customer_id: str | None
    customer_email: str
    customer_name: str
    tier: str | None
    seats: int
    status: str
    # Unix seconds when access should end, or None for "renews indefinitely".
    ends_at: int | None


def stripe_enabled() -> bool:
    return bool(settings.stripe_secret_key)


def _headers() -> dict[str, str]:
    if not settings.stripe_secret_key:
        raise BillingNotConfigured("stripe_secret_key is not configured")
    return {
        "Authorization": f"Bearer {settings.stripe_secret_key}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


# ── Webhook signature verification ─────────────────────────────────────────


def _parse_signature_header(header: str) -> tuple[int | None, list[str]]:
    """Split ``t=1234,v1=abc,v1=def`` into its timestamp and v1 signatures."""
    timestamp: int | None = None
    signatures: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                return None, []
        elif key == "v1":
            signatures.append(value)
    return timestamp, signatures


def verify_webhook(payload: bytes, signature_header: str, *, now: float | None = None) -> dict:
    """Verify a Stripe webhook and return its parsed JSON body.

    Three things must hold, and all three matter:

    * the header parses and carries at least one ``v1`` signature;
    * the HMAC-SHA256 of ``"{timestamp}.{raw body}"`` under the endpoint secret
      matches one of them, compared in constant time;
    * the timestamp is inside the tolerance, so a body captured off the wire
      cannot be replayed indefinitely.

    The raw bytes must be the exact request body. Re-serialising the parsed JSON
    changes key order and whitespace and the signature will not match.
    """
    import json

    secret = settings.stripe_webhook_secret
    if not secret:
        raise BillingNotConfigured("stripe_webhook_secret is not configured")
    if not signature_header:
        raise WebhookVerificationError("missing Stripe-Signature header")

    timestamp, signatures = _parse_signature_header(signature_header)
    if timestamp is None or not signatures:
        raise WebhookVerificationError("malformed Stripe-Signature header")

    moment = now if now is not None else time.time()
    if abs(moment - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
        raise WebhookVerificationError("webhook timestamp outside tolerance")

    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise WebhookVerificationError("webhook signature mismatch")

    try:
        body = json.loads(payload)
    except ValueError as exc:
        raise WebhookVerificationError(f"webhook body is not JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise WebhookVerificationError("webhook body is not a JSON object")
    return body


# ── Event interpretation ───────────────────────────────────────────────────

# Stripe subscription status → our registry status. Anything unmapped is
# treated as cancelled, which fails closed: an unknown state must not keep a
# subscription entitled.
_STATUS_MAP = {
    "active": "active",
    "trialing": "trialing",
    "past_due": "past_due",
    "unpaid": "past_due",
    "incomplete": "past_due",
    "paused": "cancelled",
    "canceled": "cancelled",
    "incomplete_expired": "cancelled",
}

HANDLED_EVENT_TYPES = frozenset({
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
})


def _tier_for_price(price_id: str | None) -> str | None:
    if not price_id:
        return None
    return settings.stripe_price_tiers.get(price_id)


def _first_item(subscription: dict) -> dict:
    items = ((subscription.get("items") or {}).get("data")) or []
    return items[0] if items else {}


def interpret_event(event: dict) -> SubscriptionEvent | None:
    """Reduce a Stripe event to what the registry needs, or None to ignore it.

    Returning None for unhandled types is deliberate: the endpoint still answers
    200 so Stripe stops retrying an event we will never act on.
    """
    event_type = str(event.get("type") or "")
    if event_type not in HANDLED_EVENT_TYPES:
        return None
    obj = ((event.get("data") or {}).get("object")) or {}

    if event_type == "checkout.session.completed":
        details = obj.get("customer_details") or {}
        return SubscriptionEvent(
            event_id=str(event.get("id") or ""),
            event_type=event_type,
            provider_subscription_id=_as_id(obj.get("subscription")),
            provider_customer_id=_as_id(obj.get("customer")),
            customer_email=str(obj.get("customer_email") or details.get("email") or ""),
            customer_name=str(details.get("name") or ""),
            # Checkout does not carry the price; the subscription.created event
            # that follows does, and it is what sets the tier.
            tier=None,
            seats=0,
            status="active",
            ends_at=None,
        )

    item = _first_item(obj)
    price_id = _as_id((item.get("price") or {}).get("id") or item.get("price"))
    stripe_status = str(obj.get("status") or "")
    status = (
        "cancelled"
        if event_type == "customer.subscription.deleted"
        else _STATUS_MAP.get(stripe_status, "cancelled")
    )
    # cancel_at_period_end keeps the subscription active until the period ends;
    # the row stays entitled and simply stops renewing.
    ends_at = obj.get("cancel_at") or obj.get("current_period_end")
    return SubscriptionEvent(
        event_id=str(event.get("id") or ""),
        event_type=event_type,
        provider_subscription_id=_as_id(obj.get("id")),
        provider_customer_id=_as_id(obj.get("customer")),
        customer_email="",
        customer_name="",
        tier=_tier_for_price(price_id),
        seats=int(item.get("quantity") or 0),
        status=status,
        ends_at=int(ends_at) if ends_at else None,
    )


def _as_id(value: Any) -> str | None:
    """Stripe expands objects inconsistently: a field is an id or a whole object."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        got = value.get("id")
        return str(got) if got else None
    return None


# ── Outbound calls ─────────────────────────────────────────────────────────


async def create_checkout_session(
    *, price_id: str, quantity: int = 1, customer_email: str = "", client_reference: str = ""
) -> dict:
    """Create a Stripe Checkout session and return it.

    The caller sends the customer to ``result["url"]``. Nothing is issued here —
    the licence is minted from the webhook, so a checkout the customer abandons
    at the card form leaves no trace.
    """
    if not settings.billing_success_url or not settings.billing_cancel_url:
        raise BillingNotConfigured(
            "billing_success_url and billing_cancel_url must be set before checkout"
        )
    form: dict[str, str] = {
        "mode": "subscription",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": str(max(1, quantity)),
        "success_url": settings.billing_success_url,
        "cancel_url": settings.billing_cancel_url,
        # Lets the customer manage seats from the Stripe-hosted page.
        "line_items[0][adjustable_quantity][enabled]": "true",
        "line_items[0][adjustable_quantity][minimum]": "1",
    }
    if customer_email:
        form["customer_email"] = customer_email
    if client_reference:
        form["client_reference_id"] = client_reference

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(
            f"{STRIPE_API_BASE}/checkout/sessions", data=form, headers=_headers()
        )
    if response.status_code >= 400:
        # Stripe's error body names the offending parameter; surfacing it is the
        # difference between a five-second fix and a support ticket. It contains
        # no secret — the API key travels in the header, not the body.
        raise BillingNotConfigured(
            f"Stripe rejected the checkout session ({response.status_code}): "
            f"{response.text[:400]}"
        )
    return response.json()


async def create_portal_session(*, provider_customer_id: str, return_url: str) -> dict:
    """Create a Stripe billing-portal session so a customer can self-serve.

    Card updates, plan changes and cancellation all happen on Stripe's hosted
    page, which keeps card data and dunning entirely out of this codebase.
    """
    form = {"customer": provider_customer_id, "return_url": return_url}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(
            f"{STRIPE_API_BASE}/billing_portal/sessions", data=form, headers=_headers()
        )
    if response.status_code >= 400:
        raise BillingNotConfigured(
            f"Stripe rejected the portal session ({response.status_code}): "
            f"{response.text[:400]}"
        )
    return response.json()
