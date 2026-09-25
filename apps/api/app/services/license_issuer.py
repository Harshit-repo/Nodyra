"""Vendor-side license issuance.

``tools/mint_license.py`` already signs keys by hand, which is enough to sell to
one customer at a time. It is not enough to run a subscription: a hand-minted key
is self-contained, so nothing renews it, nothing revokes it when a card fails,
and nothing records who has what. This module is the same signing operation
driven by a subscription record instead of a person at a terminal.

Only the single instance the vendor runs as its license server imports this with
a key configured (``license_issuer_enabled``). A customer's deployment never
signs anything — it verifies offline against the public key, exactly as before,
and keeps working with no network access to us.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import settings
from app.services.licensing import Edition, _public_key

# Statuses that entitle a subscription to a freshly-signed key. ``past_due`` is
# deliberately included: a failed payment should not cut off production
# automation the same hour. Stripe's dunning has days to succeed, and the key's
# own expiry is the backstop if it never does.
ENTITLED_STATUSES = frozenset({"active", "trialing", "past_due"})

REFRESH_TOKEN_BYTES = 32


class IssuerNotConfigured(RuntimeError):
    """Raised when issuance is attempted with no signing key available."""


@dataclass(frozen=True)
class IssuedLicense:
    key: str
    expires_at: datetime
    tier: str


def _signing_key() -> Ed25519PrivateKey:
    pem = (settings.license_signing_key or "").strip()
    if not pem:
        raise IssuerNotConfigured(
            "license_signing_key is not set; this instance cannot issue licences"
        )
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    except Exception as exc:  # noqa: BLE001 — any parse failure is the same fault
        raise IssuerNotConfigured(f"license_signing_key is not a valid PEM key: {exc}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise IssuerNotConfigured(
            "license_signing_key must be an Ed25519 private key "
            f"(got {type(key).__name__})"
        )
    verifier = _public_key()
    if verifier is None or key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ) != verifier.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw):
        raise IssuerNotConfigured("The signing key does not match this build's license verification key")
    return key


def validate_signing_configuration() -> None:
    _signing_key()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def sign_payload(payload: dict) -> str:
    """Sign a licence payload into the ``<b64url(json)>.<b64url(sig)>`` format.

    Byte-for-byte the format ``licensing._verify`` accepts and
    ``tools/mint_license.py`` produces — keys minted either way are
    interchangeable, so moving a hand-managed customer onto a subscription does
    not invalidate the key they already installed.
    """
    key = _signing_key()
    body = _b64url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return f"{body}.{_b64url(key.sign(body.encode()))}"


def new_refresh_token() -> tuple[str, str]:
    """Return ``(token, sha256_hex)``. Only the digest is ever stored."""
    token = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_for_subscription(subscription, *, now: datetime | None = None) -> IssuedLicense:
    """Sign a licence for one subscription row.

    The expiry is always ``license_validity_days`` from now rather than the
    subscription's billing anniversary. That is the renewal safety margin: the
    customer's instance refreshes well inside the window, and a refresh outage
    costs them weeks of grace instead of cutting them off at the period
    boundary.
    """
    moment = now or datetime.now(UTC)
    if subscription.status not in ENTITLED_STATUSES:
        raise ValueError(
            f"subscription {subscription.id} is {subscription.status!r}; "
            f"only {sorted(ENTITLED_STATUSES)} may be issued a licence"
        )
    if subscription.tier not in {Edition.PRO.value, Edition.ENTERPRISE.value}:
        raise ValueError("Subscription has no recognized paid edition")
    if type(subscription.seats) is not int or subscription.seats < 0:
        raise ValueError("Subscription seats must be a nonnegative integer")

    expires = moment + timedelta(days=max(1, settings.license_validity_days))
    # Never issue past a hard subscription end date (a cancellation scheduled
    # for period end, or a fixed-term contract).
    if subscription.expires_at is not None:
        hard_stop = subscription.expires_at
        if hard_stop.tzinfo is None:
            hard_stop = hard_stop.replace(tzinfo=UTC)
        expires = min(expires, hard_stop)
    if expires <= moment:
        raise ValueError("Subscription entitlement has expired")

    payload = {
        "tier": subscription.tier,
        "customer": subscription.customer_name or subscription.customer_email or "",
        "issued_at": int(moment.timestamp()),
        "expires_at": int(expires.timestamp()),
        # Ties the key back to a registry row so support can answer "what does
        # this customer actually have?" from the key alone.
        "subscription_id": subscription.id,
    }
    if subscription.seats:
        payload["seats"] = subscription.seats
    if subscription.extra_features:
        payload["features"] = sorted(str(f) for f in subscription.extra_features)

    return IssuedLicense(
        key=sign_payload(payload), expires_at=expires, tier=subscription.tier
    )


def should_refresh(expires_at: float | int | None, *, now: float | None = None) -> bool:
    """Whether a key this close to expiry should be renewed now.

    A perpetual key (no expiry) never refreshes.
    """
    if not expires_at:
        return False
    moment = now if now is not None else time.time()
    window = max(1, settings.license_refresh_window_days) * 86400
    return float(expires_at) - moment <= window
