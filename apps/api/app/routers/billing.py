"""Self-serve subscription endpoints.

Two audiences share this router, and the split matters:

* **Issuer routes** (``/billing/webhook``, ``/billing/license``,
  ``/billing/checkout``, ``/billing/portal``) run only on the single instance
  the vendor operates as its licence server. They are not mounted at all unless
  ``license_issuer_enabled`` is on, so a customer's deployment does not expose
  them even misconfigured.
* **Customer routes** (``/billing/plans``, ``/billing/status``) run everywhere
  and describe what this instance is entitled to.

The webhook is unauthenticated by necessity — Stripe cannot hold a Nodyra
token — so it authenticates by HMAC signature over the raw body instead, and is
idempotent because Stripe retries.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import LicenseSubscription, ProcessedWebhookEvent
from app.security import audit_recorder, get_client_ip, require_permission
from app.services import billing, license_issuer, rate_limit
from app.services.audit import AuditRecorder
from app.services.licensing import Edition, current_license
from app.tenancy import run_as_system

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


# ── Customer-facing: what am I on, and what could I be on? ──────────────────


class PlanInfo(BaseModel):
    edition: str
    name: str
    price_id: str = ""
    features: list[str]
    limits: dict[str, int]
    current: bool = False


@router.get("/plans", response_model=list[PlanInfo])
async def list_plans() -> list[PlanInfo]:
    """The editions this build supports and which one is active.

    Drives the in-product upgrade path: a Community user who hits a seat cap can
    see what lifts it instead of only being told no.
    """
    from app.services.licensing import TIER_DEFAULTS

    active = (await current_license()).edition
    price_by_tier = {tier: pid for pid, tier in settings.stripe_price_tiers.items()}
    plans: list[PlanInfo] = []
    for edition in (Edition.COMMUNITY, Edition.PRO, Edition.ENTERPRISE):
        features, limits = TIER_DEFAULTS[edition]
        plans.append(
            PlanInfo(
                edition=edition.value,
                name=edition.value.capitalize(),
                price_id=price_by_tier.get(edition.value, ""),
                features=sorted(f.value for f in features),
                limits={
                    "environments": limits.environments,
                    "runners": limits.runners,
                    "deployments": limits.deployments,
                    "seats": limits.seats,
                },
                current=edition == active,
            )
        )
    return plans


class BillingStatus(BaseModel):
    edition: str
    customer: str | None
    expires_at: int | None
    # Whether this instance can renew its own key, or needs a human to paste one.
    auto_renew: bool
    notice: str | None


@router.get("/status", response_model=BillingStatus)
async def billing_status() -> BillingStatus:
    lic = await current_license()
    return BillingStatus(
        edition=lic.edition.value,
        customer=lic.customer,
        expires_at=lic.expires_at,
        auto_renew=bool(settings.license_server_url and settings.license_refresh_token),
        notice=lic.notice,
    )


# ── Issuer-only routes ─────────────────────────────────────────────────────

issuer_router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutRequest(BaseModel):
    price_id: str = Field(min_length=1, max_length=255)
    quantity: int = Field(default=1, ge=1, le=1000)
    customer_email: str = Field(default="", max_length=320)


@issuer_router.post("/checkout")
async def create_checkout(
    body: CheckoutRequest,
    audit: AuditRecorder = Depends(audit_recorder),
    _: None = Depends(require_permission("admin:billing")),
) -> dict:
    """Start a Stripe Checkout session for a subscription."""
    if body.price_id not in settings.stripe_price_tiers:
        # Refusing unknown prices stops a caller buying a price this deployment
        # has no mapping for, which would take payment and grant nothing.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Unknown price id. Add it to stripe_price_tiers with the edition it grants.",
        )
    try:
        session_obj = await billing.create_checkout_session(
            price_id=body.price_id,
            quantity=body.quantity,
            customer_email=body.customer_email,
        )
    except billing.BillingNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    await audit(
        "checkout_started",
        "subscription",
        str(session_obj.get("id") or ""),
        f"price={body.price_id} quantity={body.quantity}",
    )
    return {"checkout_url": session_obj.get("url"), "session_id": session_obj.get("id")}


class PortalRequest(BaseModel):
    provider_customer_id: str = Field(min_length=1, max_length=255)
    return_url: str = Field(min_length=1, max_length=2000)


@issuer_router.post("/portal")
async def create_portal(
    body: PortalRequest,
    _: None = Depends(require_permission("admin:billing")),
) -> dict:
    """Hand the customer a Stripe billing-portal link.

    Card updates, plan changes and cancellation all happen on Stripe's page, so
    no card data ever reaches this codebase.
    """
    try:
        session_obj = await billing.create_portal_session(
            provider_customer_id=body.provider_customer_id, return_url=body.return_url
        )
    except billing.BillingNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return {"portal_url": session_obj.get("url")}


async def _apply_event(
    session: AsyncSession, event: billing.SubscriptionEvent
) -> LicenseSubscription | None:
    """Create or update the registry row an event refers to.

    Matching is by subscription id, falling back to customer id: the
    ``checkout.session.completed`` event can arrive before Stripe has attached
    the subscription, and the ``customer.subscription.created`` that follows
    carries the price. Neither event alone has everything, so both merge into
    one row.
    """
    row: LicenseSubscription | None = None
    if event.provider_subscription_id:
        row = await session.scalar(
            select(LicenseSubscription).where(
                LicenseSubscription.provider_subscription_id
                == event.provider_subscription_id
            )
        )
    if row is None and event.provider_customer_id:
        row = await session.scalar(
            select(LicenseSubscription).where(
                LicenseSubscription.provider_customer_id == event.provider_customer_id,
                LicenseSubscription.provider_subscription_id.is_(None),
            )
        )
    if row is None:
        row = LicenseSubscription(provider="stripe")
        session.add(row)

    if event.provider_subscription_id:
        row.provider_subscription_id = event.provider_subscription_id
    if event.provider_customer_id:
        row.provider_customer_id = event.provider_customer_id
    if event.customer_email:
        row.customer_email = event.customer_email
    if event.customer_name:
        row.customer_name = event.customer_name
    if event.tier:
        row.tier = event.tier
    if event.seats:
        row.seats = event.seats
    row.status = event.status
    if event.ends_at:
        row.expires_at = datetime.fromtimestamp(event.ends_at, tz=UTC)

    # A subscription that has become entitled and has no refresh token yet gets
    # one now, so the very first licence handed over is renewable.
    if row.status in license_issuer.ENTITLED_STATUSES and not row.refresh_token_hash:
        token, digest = license_issuer.new_refresh_token()
        row.refresh_token_hash = digest
        # Surfaced once, on this response only; never stored in the clear.
        row._issued_refresh_token = token  # type: ignore[attr-defined]
    return row


@issuer_router.post("/webhook", include_in_schema=False)
async def stripe_webhook(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    """Receive a Stripe subscription event.

    Unauthenticated by necessity — Stripe holds no Nodyra credential — so the
    HMAC signature over the raw body *is* the authentication. The raw bytes must
    be used verbatim; re-serialising the parsed JSON breaks the signature.
    """
    payload = await request.body()
    try:
        event = billing.verify_webhook(
            payload, request.headers.get("stripe-signature", "")
        )
    except billing.WebhookVerificationError as exc:
        # 400, not 401: Stripe treats 4xx as "do not retry", which is right for
        # a body we can never accept.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except billing.BillingNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    event_id = str(event.get("id") or "")
    if not event_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "event has no id")

    with run_as_system():
        # Idempotency first. Stripe retries until it sees a 2xx and can deliver
        # the same event twice even after one; without this a retried checkout
        # would mint a second licence for a single payment.
        if await session.get(ProcessedWebhookEvent, event_id) is not None:
            return {"status": "duplicate", "event_id": event_id}

        interpreted = billing.interpret_event(event)
        session.add(
            ProcessedWebhookEvent(
                id=event_id,
                provider="stripe",
                event_type=str(event.get("type") or ""),
            )
        )
        if interpreted is None:
            # Acknowledge so Stripe stops retrying an event we never act on.
            await session.commit()
            return {"status": "ignored", "event_id": event_id}

        row = await _apply_event(session, interpreted)
        await session.commit()
        await session.refresh(row)

    logger.info(
        "billing: applied %s for subscription %s (status=%s tier=%s)",
        interpreted.event_type,
        row.id,
        row.status,
        row.tier,
    )
    response: dict = {"status": "applied", "event_id": event_id, "subscription_id": row.id}
    token = getattr(row, "_issued_refresh_token", None)
    if token:
        response["refresh_token"] = token
    return response


class LicenseRequest(BaseModel):
    refresh_token: str = Field(min_length=16, max_length=512)


@issuer_router.post("/license")
async def fetch_license(
    body: LicenseRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Mint a fresh licence for a subscription, given its refresh token.

    This is what a customer's instance calls on its own to renew. It is
    authenticated by the bearer token issued at checkout, not by a Nodyra
    session: the caller is a machine with no user.
    """
    # Unauthenticated, and every call costs a lookup plus an Ed25519 signature.
    # The token is 256 bits so guessing it is not the threat — spending the
    # licence server's CPU anonymously is. A healthy instance renews about once
    # a fortnight, so this cap is invisible to real callers.
    if not await rate_limit.allow(
        "billing_license",
        get_client_ip(request),
        limit=settings.license_refresh_rate_limit_per_minute,
    ):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many licence requests; retry shortly.",
        )
    digest = license_issuer.hash_refresh_token(body.refresh_token)
    with run_as_system():
        row = await session.scalar(
            select(LicenseSubscription).where(
                LicenseSubscription.refresh_token_hash == digest
            )
        )
        if row is None:
            # Same answer for an unknown token and a revoked one, so the
            # endpoint cannot be used to probe which tokens ever existed.
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Unknown or revoked token")
        if row.status not in license_issuer.ENTITLED_STATUSES:
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED,
                f"Subscription is {row.status}; no licence will be issued.",
            )
        try:
            issued = license_issuer.issue_for_subscription(row)
        except license_issuer.IssuerNotConfigured as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

        row.last_refreshed_at = datetime.now(UTC)
        row.refresh_count += 1
        await session.commit()

    return {
        "license_key": issued.key,
        "expires_at": int(issued.expires_at.timestamp()),
        "tier": issued.tier,
    }
