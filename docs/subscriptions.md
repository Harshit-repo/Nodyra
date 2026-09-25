# Subscriptions and automatic licence renewal

Nodyra licences are Ed25519-signed strings verified **offline**. That has always
been the right design — an air-gapped instance can validate its own entitlement
with no call to us, and nobody can forge a key without the private signing key.

The cost of it was that a key is self-contained: nothing renewed it, nothing
revoked it when a card failed, and nothing recorded who had what. Keys were
minted by hand with `tools/mint_license.py`, which works for one customer at a
time and not for a subscription.

This page describes the layer that closes that gap. Offline verification is
unchanged; everything here is optional and additive.

---

## The two sides

| | Runs where | Turned on by | What it does |
|---|---|---|---|
| **Issuer** | One instance, operated by the vendor | `LICENSE_ISSUER_ENABLED=true` | Receives Stripe events, keeps the subscription registry, signs licences |
| **Refresh** | Every customer instance | `LICENSE_SERVER_URL` | Renews its own licence before the current one lapses |

The issuer routes are **not mounted at all** unless `LICENSE_ISSUER_ENABLED` is
on. A customer deployment cannot expose issuance even if it is handed a signing
key by mistake.

---

## Running the issuer

Set these on the one instance that acts as your licence server:

```bash
LICENSE_ISSUER_ENABLED=true
# The Ed25519 PRIVATE key whose public half is baked into every build.
# Mount it as a secret. Possession of this is possession of unlimited free
# Enterprise licences.
LICENSE_SIGNING_KEY="$(cat .secrets/license_signing_private.pem)"

STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_TIERS='{"price_1AbcPro":"pro","price_1XyzEnt":"enterprise"}'

BILLING_SUCCESS_URL=https://nodyra.example.com/settings#license
BILLING_CANCEL_URL=https://nodyra.example.com/pricing
```

Point a Stripe webhook endpoint at `https://<issuer>/billing/webhook` and
subscribe it to:

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`

### What happens on a purchase

1. Checkout completes. Stripe posts `checkout.session.completed`; the registry
   records the customer and mints a **refresh token**, returned once in that
   webhook response and never stored in the clear (only a SHA-256 digest is).
2. `customer.subscription.created` follows carrying the price. The price is
   mapped to an edition through `STRIPE_PRICE_TIERS` and merged into the same
   registry row.
3. The customer's instance calls `POST /billing/license` with its refresh token
   and receives a signed key.

A price with no mapping is **refused at checkout** rather than taking payment
and granting nothing.

### Idempotency

Stripe retries a webhook until it sees a 2xx, and can deliver the same event
twice even after one. Every processed event id is recorded, keyed by the
provider's own id, so a retried `checkout.session.completed` cannot mint a
second licence for one payment.

### Authentication

The webhook holds no Nodyra credential, so the HMAC signature over the **raw
request body** is the authentication: signature match in constant time, plus a
five-minute timestamp tolerance that bounds replay of a captured body. A
malformed or mis-signed request gets a 400 — `4xx` tells Stripe not to retry
something we can never accept.

---

## Customer-side renewal

On a customer instance:

```bash
LICENSE_SERVER_URL=https://licences.nodyra.com
LICENSE_REFRESH_TOKEN=<the token issued at checkout>
# Renew once the key is inside this many days of expiry.
LICENSE_REFRESH_WINDOW_DAYS=14
```

An hourly loop checks the installed key and renews it when it enters the window.

**Leaving `LICENSE_SERVER_URL` blank disables the loop entirely.** An air-gapped
deployment never reaches out and keeps verifying offline exactly as before.

### Why issued keys outlive the billing period

`LICENSE_VALIDITY_DAYS` defaults to **45** — deliberately longer than a monthly
subscription. The margin is the safety property: a licence server outage, a DNS
failure or a missed renewal costs weeks of grace rather than cutting a customer
off at the period boundary.

### What failure looks like

| Situation | Behaviour |
|---|---|
| Licence server unreachable, slow, or returning errors | Logged; the installed key is untouched and keeps working |
| Subscription cancelled (`402`) | Logged as an error; the current key still runs to its own expiry, giving an operator days to notice |
| Renewed key fails signature verification | Logged loudly as a probable signing-key rotation, rather than surfacing as features quietly disappearing |
| `NODYRA_LICENSE_KEY` pinned in the environment | The loop does nothing — an explicit operator choice wins, and a database write they cannot see would not take effect anyway |

Nothing in this path can degrade a running instance. That is the design
constraint it is written to.

### `past_due` is still entitled

A failed payment does not stop production automation the same hour. Stripe's
dunning has days to succeed; the key's own expiry is the backstop if it never
does.

---

## Manual subscriptions

Enterprise contracts, trials and comped accounts do not need Stripe. Insert a
`license_subscriptions` row directly with a tier, seats and status, generate a
refresh token, and the same renewal path works. `STRIPE_SECRET_KEY` can stay
blank; only the Stripe-specific routes are inert.

`tools/mint_license.py` also still works and produces **byte-identical**
keys — moving a hand-managed customer onto a subscription does not invalidate
the key they already installed.

---

## What customers see

*Settings → Plan & licence* shows the current edition, its caps, and a
comparison of what each edition adds. A Community user who hits the five-seat
cap can see which edition lifts it instead of only being told no.

The comparison is served from the same `TIER_DEFAULTS` the enforcement code
reads, so what is advertised cannot drift from what is granted.
