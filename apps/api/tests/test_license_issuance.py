"""License issuance, renewal, and the subscription lifecycle.

The point of this layer is that a paying customer's instance never silently
drops to Community, and a cancelled one does not keep its entitlement forever.
Both directions are tested here, end to end through the HTTP endpoints a real
Stripe integration and a real customer instance would use.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI

from app.config import settings
from app.services import billing, license_issuer, licensing

# A throwaway signing keypair. The real private key never enters the repo; this
# one exists so tests can verify the whole sign → verify round trip.
_SEED = b"nodyra-test-issuer-signing-seed!"  # exactly 32 bytes
_PRIVATE = Ed25519PrivateKey.from_private_bytes(_SEED)

PRIVATE_PEM = _PRIVATE.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()

PUBLIC_PEM = _PRIVATE.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()

WEBHOOK_SECRET = "whsec_issuance_tests"


@pytest.fixture(autouse=True)
def _issuer(monkeypatch):
    """Configure this process as the vendor's licence server."""
    monkeypatch.setattr(settings, "license_issuer_enabled", True)
    monkeypatch.setattr(settings, "license_signing_key", PRIVATE_PEM)
    monkeypatch.setattr(settings, "license_public_key", PUBLIC_PEM)
    monkeypatch.setattr(settings, "license_key", "")
    monkeypatch.setattr(settings, "license_validity_days", 45)
    monkeypatch.setattr(settings, "stripe_webhook_secret", WEBHOOK_SECRET)
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_price_tiers", {"price_pro": "pro"})
    licensing.invalidate_license_cache()
    yield
    licensing.invalidate_license_cache()


@pytest.fixture
async def issuer_client(client, monkeypatch):
    """A client for an instance acting as the vendor's licence server.

    Built as a separate ASGI app rather than by mounting the issuer router onto
    the global one: mounting is permanent for the process, so a single test
    would leave issuance exposed for every test that followed — including the
    one asserting a customer instance does *not* expose it.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app as main_app
    from app.routers import billing as billing_router

    issuer_app = FastAPI()
    issuer_app.include_router(billing_router.router)
    issuer_app.include_router(billing_router.issuer_router)
    # Same session/tenancy wiring the `client` fixture installed.
    issuer_app.dependency_overrides = dict(main_app.dependency_overrides)
    # Exercise vendor operations separately from their authorization boundary.
    issuer_app.dependency_overrides[billing_router.require_billing_owner] = lambda: None
    stripe_state = {}

    async def retrieve(subscription_id):
        return copy.deepcopy(stripe_state[subscription_id])

    monkeypatch.setattr(billing, "retrieve_subscription", retrieve)

    transport = ASGITransport(app=issuer_app)
    async with AsyncClient(transport=transport, base_url="http://issuer") as http_client:
        http_client.stripe_state = stripe_state
        http_client.issuer_app = issuer_app
        yield http_client


class _Row:
    """A minimal stand-in for a LicenseSubscription row."""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", "sub-row-1")
        self.tier = kwargs.get("tier", "pro")
        self.seats = kwargs.get("seats", 0)
        self.extra_features = kwargs.get("extra_features", [])
        self.status = kwargs.get("status", "active")
        self.expires_at = kwargs.get("expires_at")
        self.customer_name = kwargs.get("customer_name", "Acme Inc")
        self.customer_email = kwargs.get("customer_email", "ops@acme.test")


# ── Signing ────────────────────────────────────────────────────────────────


def test_an_issued_key_verifies_and_grants_its_tier():
    issued = license_issuer.issue_for_subscription(_Row(tier="pro", seats=10))
    payload = licensing._verify(issued.key)
    assert payload is not None, "the issued key did not verify against the public key"
    assert payload["tier"] == "pro"
    assert payload["seats"] == 10
    assert payload["customer"] == "Acme Inc"
    assert payload["subscription_id"] == "sub-row-1"


def test_issued_keys_are_interchangeable_with_hand_minted_ones():
    """The format must match tools/mint_license.py exactly, or moving a
    hand-managed customer onto a subscription would invalidate the key they
    already installed."""
    issued = license_issuer.issue_for_subscription(_Row())
    body, _, signature = issued.key.partition(".")
    assert body and signature
    assert "." not in body, "body must be a single base64url segment"
    decoded = json.loads(license_issuer.base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    assert set(decoded) >= {"tier", "customer", "issued_at", "expires_at"}


def test_extra_feature_grants_are_carried_into_the_key():
    issued = license_issuer.issue_for_subscription(
        _Row(tier="pro", extra_features=["sso", "audit_logs"])
    )
    payload = licensing._verify(issued.key)
    assert payload["features"] == ["audit_logs", "sso"]


def test_validity_is_longer_than_a_billing_period():
    """The margin is what stops a late renewal cutting a customer off."""
    issued = license_issuer.issue_for_subscription(_Row())
    days = (issued.expires_at - datetime.now(UTC)).days
    assert days >= 31, f"issued validity is only {days} days; a monthly renewal has no margin"


def test_a_hard_end_date_caps_the_issued_expiry():
    """A cancellation scheduled for period end must not be overridden by the
    standard validity window."""
    hard_stop = datetime.now(UTC) + timedelta(days=3)
    issued = license_issuer.issue_for_subscription(_Row(expires_at=hard_stop))
    assert issued.expires_at == hard_stop


@pytest.mark.parametrize("status", ["cancelled", "expired", "unknown"])
def test_an_unentitled_subscription_is_never_issued_a_key(status):
    with pytest.raises(ValueError, match=status):
        license_issuer.issue_for_subscription(_Row(status=status))


@pytest.mark.parametrize("status", ["active", "trialing", "past_due"])
def test_entitled_statuses_are_issued(status):
    """past_due is deliberately entitled: a failed payment should not stop
    production automation the same hour Stripe's dunning begins."""
    assert license_issuer.issue_for_subscription(_Row(status=status)).key


def test_issuance_without_a_signing_key_fails_loudly(monkeypatch):
    monkeypatch.setattr(settings, "license_signing_key", "")
    with pytest.raises(license_issuer.IssuerNotConfigured):
        license_issuer.issue_for_subscription(_Row())


def test_a_non_ed25519_signing_key_is_rejected(monkeypatch):
    """An RSA key would sign happily and produce keys nothing can verify."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    rsa_pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        .decode()
    )
    monkeypatch.setattr(settings, "license_signing_key", rsa_pem)
    with pytest.raises(license_issuer.IssuerNotConfigured, match="Ed25519"):
        license_issuer.issue_for_subscription(_Row())


# ── Refresh tokens ─────────────────────────────────────────────────────────


def test_refresh_tokens_are_stored_only_as_digests():
    token, digest = license_issuer.new_refresh_token()
    assert len(token) >= 32
    assert digest == hashlib.sha256(token.encode()).hexdigest()
    assert token not in digest


def test_refresh_tokens_are_unique():
    assert license_issuer.new_refresh_token()[0] != license_issuer.new_refresh_token()[0]


def test_should_refresh_respects_the_window(monkeypatch):
    monkeypatch.setattr(settings, "license_refresh_window_days", 14)
    now = time.time()
    assert license_issuer.should_refresh(now + 5 * 86400, now=now) is True
    assert license_issuer.should_refresh(now + 30 * 86400, now=now) is False
    # An already-expired key still wants renewing.
    assert license_issuer.should_refresh(now - 86400, now=now) is True


def test_a_perpetual_key_never_refreshes():
    assert license_issuer.should_refresh(None) is False
    assert license_issuer.should_refresh(0) is False


# ── End to end through the HTTP endpoints ──────────────────────────────────


def _signed(payload: dict) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    moment = int(time.time())
    signature = hmac.new(
        WEBHOOK_SECRET.encode(), f"{moment}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return body, {"stripe-signature": f"t={moment},v1={signature}"}


def _subscription_payload(event_id: str, *, status: str = "active", sub: str = "sub_e2e") -> dict:
    return {
        "id": event_id,
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "id": sub,
                "customer": "cus_e2e",
                "status": status,
                "current_period_end": int(time.time()) + 30 * 86400,
                "items": {"data": [{"price": {"id": "price_pro"}, "quantity": 1}]},
            }
        },
    }


async def _post_webhook(client, payload: dict):
    obj = copy.deepcopy(payload.get("data", {}).get("object", {}))
    if payload["type"].startswith("customer.subscription."):
        if payload["type"] == "customer.subscription.deleted":
            obj["status"] = "canceled"
        client.stripe_state[obj["id"]] = obj
    elif payload["type"] == "checkout.session.completed" and obj.get("subscription"):
        client.stripe_state.setdefault(obj["subscription"], _subscription_payload("state", sub=obj["subscription"])["data"]["object"])
    body, headers = _signed(payload)
    return await client.post("/billing/webhook", content=body, headers=headers)


async def _activate(client, subscription_id):
    response = await client.post(f"/billing/subscriptions/{subscription_id}/activation")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


async def test_a_purchase_creates_a_renewable_subscription(issuer_client):
    """The whole point: pay, and the instance can renew itself from then on."""
    resp = await _post_webhook(issuer_client, _subscription_payload("evt_buy"))
    assert resp.status_code == 200, resp.text
    created = resp.json()
    assert created["status"] == "applied"
    assert "refresh_token" not in created
    token = (await _activate(issuer_client, created["subscription_id"]))["refresh_token"]

    licence = await issuer_client.post("/billing/license", json={"refresh_token": token})
    assert licence.status_code == 200, licence.text
    key = licence.json()["license_key"]

    payload = licensing._verify(key)
    assert payload is not None and payload["tier"] == "pro"
    assert licensing.verify_license_key(key).limits.seats == 10


async def test_a_retried_webhook_does_not_issue_a_second_licence(issuer_client):
    """Stripe retries until it sees a 2xx and can deliver twice even after one."""
    payload = _subscription_payload("evt_dupe")
    first = await _post_webhook(issuer_client, payload)
    second = await _post_webhook(issuer_client, payload)

    assert first.json()["status"] == "applied"
    assert second.json()["status"] == "duplicate"
    assert "refresh_token" not in second.json()


async def test_cancelling_stops_renewal(issuer_client):
    """The gap manual minting could never close: a self-contained key cannot be
    revoked, so a cancelled customer kept their entitlement until expiry."""
    created = await _post_webhook(issuer_client, _subscription_payload("evt_c1"))
    token = (await _activate(issuer_client, created.json()["subscription_id"]))["refresh_token"]
    assert (
        await issuer_client.post("/billing/license", json={"refresh_token": token})
    ).status_code == 200

    cancelled = dict(_subscription_payload("evt_c2", sub="sub_e2e"))
    cancelled["type"] = "customer.subscription.deleted"
    assert (await _post_webhook(issuer_client, cancelled)).status_code == 200

    refused = await issuer_client.post("/billing/license", json={"refresh_token": token})
    assert refused.status_code == 402, refused.text
    assert "cancelled" in refused.json()["detail"]


async def test_an_unknown_refresh_token_is_refused_indistinguishably(issuer_client):
    """Same answer for never-existed and revoked, so the endpoint cannot be
    used to probe which tokens were ever issued."""
    resp = await issuer_client.post(
        "/billing/license", json={"refresh_token": "x" * 40}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Unknown or revoked token"


async def test_an_unsigned_webhook_is_refused(issuer_client):
    resp = await issuer_client.post(
        "/billing/webhook", content=json.dumps(_subscription_payload("evt_bad")).encode()
    )
    assert resp.status_code == 400


async def test_a_tampered_webhook_is_refused(issuer_client):
    body, headers = _signed(_subscription_payload("evt_tamper"))
    tampered = body.replace(b'"quantity": 1', b'"quantity": 4000')
    resp = await issuer_client.post("/billing/webhook", content=tampered, headers=headers)
    assert resp.status_code == 400


async def test_an_unhandled_event_is_acknowledged_not_retried(issuer_client):
    resp = await _post_webhook(
        issuer_client, {"id": "evt_ignore", "type": "invoice.paid", "data": {"object": {}}}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


async def test_checkout_then_subscription_merge_into_one_row(issuer_client):
    """Neither event alone has everything: checkout carries the customer,
    subscription.created carries the price."""
    checkout = {
        "id": "evt_co",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "subscription": "sub_merge",
                "customer": "cus_merge",
                "customer_details": {"email": "buyer@merge.test", "name": "Merge Co"},
            }
        },
    }
    first = await _post_webhook(issuer_client, checkout)
    assert first.status_code == 200
    subscription_id = first.json()["subscription_id"]

    second = await _post_webhook(
        issuer_client, _subscription_payload("evt_co2", sub="sub_merge")
    )
    assert second.json()["subscription_id"] == subscription_id, "a second row was created"

    activation = await _activate(issuer_client, subscription_id)
    licence = await issuer_client.post("/billing/license", json={"refresh_token": activation["refresh_token"]})
    payload = licensing._verify(licence.json()["license_key"])
    assert payload["tier"] == "pro"
    assert payload["customer"] == "Merge Co"


# ── Customer-facing surface ────────────────────────────────────────────────


async def test_plans_lists_every_edition_and_marks_the_current_one(client):
    resp = await client.get("/billing/plans")
    assert resp.status_code == 200, resp.text
    plans = {p["edition"]: p for p in resp.json()}
    assert set(plans) == {"community", "pro", "enterprise"}
    assert sum(1 for p in plans.values() if p["current"]) == 1
    # An unlicensed instance is Community, and Community must be reachable
    # without payment.
    assert plans["community"]["current"] is True
    assert plans["pro"]["limits"]["seats"] >= plans["community"]["limits"]["seats"]


async def test_status_reports_whether_the_instance_can_renew_itself(client):
    resp = await client.get("/billing/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["edition"] == "community"
    # No licence server configured in the default fixture.
    assert body["auto_renew"] is False


async def test_issuer_routes_are_absent_on_a_customer_instance(client):
    """A customer deployment must not expose issuance even if misconfigured —
    the router is not mounted, so the path does not exist."""
    resp = await client.post("/billing/license", json={"refresh_token": "x" * 40})
    assert resp.status_code == 404


async def test_the_licence_endpoint_is_rate_limited(issuer_client, monkeypatch):
    """Unauthenticated by necessity, and every call costs a database lookup plus
    an Ed25519 signature. Guessing a 256-bit token is not the threat; spending
    the licence server's CPU anonymously is."""
    monkeypatch.setattr(settings, "license_refresh_rate_limit_per_minute", 3)

    statuses = [
        (
            await issuer_client.post("/billing/license", json={"refresh_token": "z" * 40})
        ).status_code
        for _ in range(6)
    ]
    assert 429 in statuses, statuses
    # The cap engages after the allowance, not before it.
    assert statuses[0] != 429


async def test_a_zero_limit_disables_throttling(issuer_client, monkeypatch):
    """Matches the convention used by every other limit in the codebase, so an
    operator behind a WAF that already throttles can turn it off."""
    monkeypatch.setattr(settings, "license_refresh_rate_limit_per_minute", 0)
    for _ in range(8):
        resp = await issuer_client.post(
            "/billing/license", json={"refresh_token": "y" * 40}
        )
        assert resp.status_code == 403, resp.text


async def test_activation_rotation_revokes_only_the_previous_refresh_token(issuer_client):
    created = (await _post_webhook(issuer_client, _subscription_payload("evt_rotate"))).json()
    first = await _activate(issuer_client, created["subscription_id"])
    second = await _activate(issuer_client, "sub_e2e")
    assert (await issuer_client.post("/billing/license", json={"refresh_token": first["refresh_token"]})).status_code == 403
    assert (await issuer_client.post("/billing/license", json={"refresh_token": second["refresh_token"]})).status_code == 200
    assert licensing.verify_license_key(first["license_key"]).valid


@pytest.mark.parametrize("payment_status", ["unpaid", "incomplete", "canceled"])
async def test_checkout_does_not_grant_access_for_an_unpaid_subscription(issuer_client, payment_status):
    issuer_client.stripe_state["sub_unpaid"] = _subscription_payload("current", status=payment_status, sub="sub_unpaid")["data"]["object"]
    checkout = {"id": "evt_unpaid", "type": "checkout.session.completed", "data": {"object": {"subscription": "sub_unpaid", "customer": "cus_test"}}}
    response = await _post_webhook(issuer_client, checkout)
    assert response.status_code == 200
    assert "refresh_token" not in response.json()
    activation = await issuer_client.post("/billing/subscriptions/sub_unpaid/activation")
    assert activation.status_code == 402


async def test_stale_active_event_cannot_reactivate_a_cancelled_subscription(issuer_client):
    await _post_webhook(issuer_client, _subscription_payload("evt_live"))
    issuer_client.stripe_state["sub_e2e"]["status"] = "canceled"
    body, headers = _signed(_subscription_payload("evt_old"))
    assert (await issuer_client.post("/billing/webhook", content=body, headers=headers)).status_code == 200
    assert (await issuer_client.post("/billing/subscriptions/sub_e2e/activation")).status_code == 402


async def test_unmapped_price_never_grants_the_default_pro_tier(issuer_client):
    event = _subscription_payload("evt_unknown_price")
    event["data"]["object"]["items"]["data"][0]["price"]["id"] = "price_unknown"
    assert (await _post_webhook(issuer_client, event)).status_code == 200
    assert (await issuer_client.post("/billing/subscriptions/sub_e2e/activation")).status_code == 402


async def test_refresh_rechecks_cancellation_even_if_webhook_was_missed(issuer_client):
    await _post_webhook(issuer_client, _subscription_payload("evt_paid"))
    activation = await _activate(issuer_client, "sub_e2e")
    issuer_client.stripe_state["sub_e2e"]["status"] = "unpaid"
    assert (await issuer_client.post("/billing/license", json={"refresh_token": activation["refresh_token"]})).status_code == 402


async def test_missing_stripe_response_is_retryable_and_not_acknowledged(issuer_client, monkeypatch):
    async def unavailable(_):
        raise billing.BillingNotConfigured("Stripe unavailable")
    monkeypatch.setattr(billing, "retrieve_subscription", unavailable)
    assert (await _post_webhook(issuer_client, _subscription_payload("evt_retry"))).status_code == 503


@pytest.mark.parametrize("role,expected", [(None, 401), ("viewer", 403), ("admin", 403), ("owner", 404)])
async def test_activation_requires_signed_in_instance_owner(issuer_client, role, expected):
    from app.models import User
    from app.routers.billing import require_billing_owner
    from app.security import optional_current_user

    issuer_client.issuer_app.dependency_overrides.pop(require_billing_owner)
    issuer_client.issuer_app.dependency_overrides[optional_current_user] = lambda: User(id="owner-test", email="vendor@example.test", role=role) if role else None
    response = await issuer_client.post("/billing/subscriptions/missing/activation")
    assert response.status_code == expected, response.text


def test_expired_contract_cannot_receive_an_already_expired_key():
    with pytest.raises(ValueError, match="expired"):
        license_issuer.issue_for_subscription(_Row(expires_at=datetime.now(UTC) - timedelta(days=1)))


@pytest.mark.parametrize("tier", ["community", "unknown"])
def test_issuer_refuses_non_paid_tiers(tier):
    with pytest.raises(ValueError, match="edition"):
        license_issuer.issue_for_subscription(_Row(tier=tier))


async def test_vendor_checkout_uses_hosted_subscription_and_fixed_installation_price(issuer_client, monkeypatch):
    from urllib.parse import parse_qs

    import httpx

    monkeypatch.setattr(settings, "billing_success_url", "https://nodyra.example/docs.html#/licensing")
    monkeypatch.setattr(settings, "billing_cancel_url", "https://nodyra.example/#pricing")
    captured = []
    def stripe(request):
        captured.append(request)
        return httpx.Response(200, json={"id": "cs_test_ready", "url": "https://checkout.stripe.com/c/pay/cs_test_ready"})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(**kw, transport=httpx.MockTransport(stripe)))
    response = await issuer_client.post("/billing/checkout", json={"price_id": "price_pro", "customer_email": "buyer@example.test"})
    assert response.status_code == 200, response.text
    assert response.json()["checkout_url"].startswith("https://checkout.stripe.com/")
    form = parse_qs(captured[0].content.decode())
    assert form["mode"] == ["subscription"]
    assert form["line_items[0][quantity]"] == ["1"]
    assert not any("adjustable_quantity" in key for key in form)
    assert captured[0].headers["stripe-version"] == "2025-03-31.basil"
    assert (await issuer_client.post("/billing/checkout", json={"price_id": "price_pro", "quantity": 2})).status_code == 422


async def test_checkout_refuses_bad_price_or_unusable_signing_configuration(issuer_client, monkeypatch):
    assert (await issuer_client.post("/billing/checkout", json={"price_id": "price_unknown"})).status_code == 400
    monkeypatch.setattr(settings, "license_signing_key", "")
    assert (await issuer_client.post("/billing/checkout", json={"price_id": "price_pro"})).status_code == 503


async def test_provider_failure_is_actionable_without_exposing_response_secrets(issuer_client, monkeypatch):
    import httpx
    monkeypatch.setattr(settings, "billing_success_url", "https://nodyra.example/done")
    monkeypatch.setattr(settings, "billing_cancel_url", "https://nodyra.example/cancel")
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(**kw, transport=httpx.MockTransport(lambda request: httpx.Response(400, text="private-provider-detail"))))
    response = await issuer_client.post("/billing/checkout", json={"price_id": "price_pro"})
    assert response.status_code == 503
    assert "private-provider-detail" not in response.text


async def test_portal_return_url_is_chosen_by_the_vendor_configuration(issuer_client, monkeypatch):
    captured = {}
    monkeypatch.setattr(settings, "billing_success_url", "https://nodyra.example/done")
    async def portal(**kwargs):
        captured.update(kwargs)
        return {"url": "https://billing.stripe.com/p/session/test"}
    monkeypatch.setattr(billing, "create_portal_session", portal)
    response = await issuer_client.post("/billing/portal", json={"provider_customer_id": "cus_test", "return_url": "https://untrusted.example"})
    assert response.status_code == 200
    assert captured["return_url"] == "https://nodyra.example/done"


def test_test_signing_authorities_differ_from_the_production_verifier():
    from tests._license_keys import TEST_PUBLIC_KEY_PEM
    assert PUBLIC_PEM.strip() != licensing._BAKED_PUBLIC_KEY_PEM.strip()
    assert TEST_PUBLIC_KEY_PEM.strip() != licensing._BAKED_PUBLIC_KEY_PEM.strip()


async def test_buyer_installs_delivered_key_and_bad_replacement_preserves_it(issuer_client, client):
    await _post_webhook(issuer_client, _subscription_payload("evt_buyer"))
    activation = await _activate(issuer_client, "sub_e2e")
    installed = await client.put("/system-settings/license", json={"license_key": activation["license_key"]})
    assert installed.status_code == 200, installed.text
    assert installed.json()["edition"] == "pro"
    assert installed.json()["limits"]["seats"] == 10
    assert (await client.put("/system-settings/license", json={"license_key": "invalid-replacement"})).status_code == 400
    assert (await client.get("/system-settings/license")).json()["edition"] == "pro"


async def test_duplicate_webhooks_are_serialized_on_postgresql(issuer_client):
    import asyncio

    from tests.conftest import TEST_DATABASE_URL

    if not (TEST_DATABASE_URL or "").startswith("postgresql"):
        pytest.skip("PostgreSQL advisory-lock integration test")
    event = _subscription_payload("evt_concurrent")
    issuer_client.stripe_state["sub_e2e"] = event["data"]["object"]
    body, headers = _signed(event)
    responses = await asyncio.gather(*[
        issuer_client.post("/billing/webhook", content=body, headers=headers)
        for _ in range(2)
    ])
    assert all(response.status_code == 200 for response in responses)
    assert sorted(response.json()["status"] for response in responses) == ["applied", "duplicate"]


async def test_failed_activation_audit_does_not_revoke_existing_refresh_token(issuer_client, monkeypatch):
    from app.routers import billing as router

    await _post_webhook(issuer_client, _subscription_payload("evt_atomic"))
    activation = await _activate(issuer_client, "sub_e2e")
    async def unavailable(*args, **kwargs):
        raise RuntimeError("audit storage unavailable")
    monkeypatch.setattr(router, "log_audit", unavailable)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await issuer_client.post("/billing/subscriptions/sub_e2e/activation")
    renewal = await issuer_client.post("/billing/license", json={"refresh_token": activation["refresh_token"]})
    assert renewal.status_code == 200, renewal.text
