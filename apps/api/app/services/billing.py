"""Stripe checkout and webhook handling for self-serve subscriptions.

Deliberately talks to Stripe's REST API over ``httpx`` rather than pulling in the
``stripe`` SDK: the calls needed here are simple form posts and lookups, and the SDK
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
from urllib.parse import quote, urlsplit

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
    if not any(candidate.isascii() and hmac.compare_digest(expected, candidate) for candidate in signatures):
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
    "unpaid": "cancelled",
    "incomplete": "cancelled",
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
            # Checkout itself is not proof of payment or of an entitled tier.
            status="pending",
            ends_at=None,
        )

    item = _first_item(obj)
    price_id = _as_id(item.get("price"))
    stripe_status = str(obj.get("status") or "")
    status = (
        "cancelled"
        if event_type == "customer.subscription.deleted"
        else _STATUS_MAP.get(stripe_status, "cancelled")
    )
    # Basil moved billing periods onto subscription items. A normal renewal
    # gets bounded outage grace; scheduled cancellation is a hard stop.
    period_end = item.get("current_period_end") or obj.get("current_period_end")
    ends_at = obj.get("cancel_at")
    if not ends_at and period_end:
        ends_at = int(period_end) + (
            0 if obj.get("cancel_at_period_end") else settings.license_validity_days * 86400
        )
    tier = _tier_for_price(price_id)
    # One installation subscription grants the advertised edition's seats.
    # Unknown/add-on/multi-item prices must not inherit an old or default tier.
    items = (obj.get("items") or {}).get("data") or []
    if tier not in {"pro", "enterprise"} or len(items) != 1 or item.get("quantity", 1) != 1 or not ends_at:
        status = "cancelled"
    return SubscriptionEvent(
        event_id=str(event.get("id") or ""),
        event_type=event_type,
        provider_subscription_id=_as_id(obj.get("id")),
        provider_customer_id=_as_id(obj.get("customer")),
        customer_email="",
        customer_name="",
        tier=tier,
        seats=0,
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

    The vendor sends the customer to ``result["url"]``. Nothing is issued here;
    verified payment updates the registry, then the vendor issues activation.
    """
    if not settings.billing_success_url or not settings.billing_cancel_url:
        raise BillingNotConfigured(
            "billing_success_url and billing_cancel_url must be set before checkout"
        )
    if quantity != 1:
        raise BillingNotConfigured("Checkout sells one installation per subscription")
    for url in (settings.billing_success_url, settings.billing_cancel_url):
        parsed = urlsplit(url)
        if not parsed.hostname or parsed.username or parsed.password or (
            parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
        ):
            raise BillingNotConfigured("Billing return URLs require HTTPS (loopback HTTP is allowed for testing)")
    form: dict[str, str] = {
        "mode": "subscription",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "success_url": settings.billing_success_url,
        "cancel_url": settings.billing_cancel_url,
    }
    if customer_email:
        form["customer_email"] = customer_email
    if client_reference:
        form["client_reference_id"] = client_reference

    return await _request("POST", "/checkout/sessions", data=form)


async def create_portal_session(*, provider_customer_id: str, return_url: str) -> dict:
    """Create a Stripe billing-portal session so a customer can self-serve.

    Card updates, plan changes and cancellation all happen on Stripe's hosted
    page, which keeps card data and dunning entirely out of this codebase.
    """
    form = {"customer": provider_customer_id, "return_url": return_url}
    return await _request("POST", "/billing_portal/sessions", data=form)


async def _request(method: str, endpoint: str, **kwargs) -> dict:
    headers = _headers()
    headers["Stripe-Version"] = "2025-03-31.basil"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.request(method, STRIPE_API_BASE + endpoint, headers=headers, **kwargs)
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Do not echo provider bodies, customer information, or credentials.
        raise BillingNotConfigured("Stripe is unavailable or rejected the request; check the issuer configuration and retry.") from exc
    if not isinstance(result, dict):
        raise BillingNotConfigured("Stripe returned an invalid response")
    return result


async def retrieve_subscription(subscription_id: str) -> dict:
    subscription = await _request("GET", "/subscriptions/" + quote(subscription_id, safe=""))
    if subscription.get("id") != subscription_id:
        raise BillingNotConfigured("Stripe returned an unexpected subscription")
    return subscription
