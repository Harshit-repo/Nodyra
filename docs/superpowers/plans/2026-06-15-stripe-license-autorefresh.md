# Stripe Billing + Auto-Refreshing License Server — Build Plan

> Status: **PLAN.** Lets self-service Pro customers pay monthly via Stripe and have
> their Noodle instance keep a valid license key in sync automatically, with no
> manual paste and no destructive lapse. Builds on the existing offline-verified
> licensing system (`apps/api/app/services/licensing.py`, `tools/mint_license.py`).
> Harry has no Stripe account yet — Part B (the in-repo client) is buildable and
> testable now; Part A (the server) is staged behind getting Stripe set up.

## Why this design

Today a license is an Ed25519-signed key pasted manually (env or Settings →
License), cached 30s, also read from `system_settings.license_key`. That's right
for annual/perpetual/air-gapped deals — wrong for monthly self-service, which
needs automatic expiry + renewal. The fix is the industry-standard **short-lived
heartbeat key** pattern (Sentry/GitLab cloud-connected):

- The vendor runs a tiny **license server** that holds the private key and knows
  who is currently paid (synced from Stripe webhooks).
- It mints **short-lived keys (~35-day expiry)**.
- Each instance stores a long-lived **activation token** and **pulls** a fresh
  key daily, writing it to `system_settings.license_key`.
- If the subscription lapses, the server stops issuing fresh keys; the last key
  expires within ~35 days; the **existing** `_build()` soft-downgrades to
  Community — nothing is deleted, workflows keep running.
- **Verification stays 100% offline** (baked public key). Only *fetching* the
  newest key is online. Air-gapped/Enterprise customers keep manual annual keys.

The 35-day window (vs the 30-day billing cycle) gives a grace buffer so a late
Stripe webhook or a day of downtime never wrongly downgrades a paying customer.

---

## Part A — License Server (separate small service, vendor-only)

A standalone FastAPI app (deploy on Fly/Render/Railway). **Never** part of the
shipped product. Reuses the signing logic from `tools/mint_license.py`.

### Data (one SQLite/Postgres table)
`subscriptions`: `activation_token (uuid, pk)`, `stripe_customer_id`,
`stripe_subscription_id`, `tier`, `seats`, `status` (active/past_due/canceled),
`current_period_end`, `customer_label`, `created_at`.

### Endpoints
| Method/Path | Purpose |
|---|---|
| `POST /stripe/webhook` | Verify Stripe signature. On `checkout.session.completed` → create `subscriptions` row + a fresh `activation_token`; email it to the customer with install instructions. On `invoice.paid` / `customer.subscription.updated` → set `status=active`, bump `current_period_end`. On `customer.subscription.deleted` / `invoice.payment_failed` (after retries) → `status=canceled`/`past_due`. |
| `GET /v1/license?token=...` | The instance's daily pull. Look up token. If `status` active (or `past_due` within grace) → mint a key with `expires_at = now + 35d`, `tier`, `seats`, `customer`, return `{ "license_key": "<body.sig>" }`. Else return `204`/`402` (instance keeps its current key until it naturally expires). Rate-limit per token. |
| `GET /healthz` | Liveness. |

### Minting
Extract the `_sign` payload/sign helper from `tools/mint_license.py` into a shared
`sign_license(payload, private_key)` so the server and CLI share one code path.
Private key from env (`NOODLE_LICENSE_SIGNING_KEY`), never on disk in prod.

### Stripe setup (when account exists)
- One Product "Noodle Pro", one recurring Price ($49/mo + a $490/yr price).
- Checkout Session (hosted) — link from the landing page replaces the current
  `mailto:` waitlist CTA.
- Webhook endpoint pointed at `POST /stripe/webhook`; store the signing secret.

---

## Part B — In-repo auto-refresh client (buildable + testable NOW)

All in `apps/api`. Small, and unit-testable against a fake server (no Stripe).

### Config (`app/config.py`)
- `license_server_url: str = ""`  (e.g. `https://license.noodle.dev`)
- `license_activation_token: str = ""`
- `license_refresh_interval_hours: int = 24`
Both empty (default) = feature off = today's manual behavior, bit-for-bit.

### Refresh service (`app/services/license_refresh.py`)
```text
async def refresh_once(session_factory, http) -> str | None:
    if not (settings.license_server_url and settings.license_activation_token):
        return None
    resp = GET {url}/v1/license?token={token}   # short timeout, retries
    if resp.status != 200: return None          # keep current key; never clobber
    key = resp.json()["license_key"]
    if licensing._verify(key) is None: return None   # reject anything unsigned
    # write to system_settings.license_key, then:
    licensing.invalidate_license_cache()
    return key
```
- Reuses `licensing._verify` so a compromised/spoofed server can't inject an
  unsigned key — the baked public key is still the root of trust.
- Writes through the existing `system_settings.license_key` column (migration
  0051 already added it) — no new migration.

### Background loop (`app/main.py` lifespan)
Add a `_license_refresh_loop` next to the existing retention/scheduler loops,
wrapped in `run_as_system()` (MT-safe seam), interval = config, `logger.exception`
on failure (never crash boot). Run once at startup, then every N hours.

### Settings UI (`SettingsPage.tsx` License panel)
Show "Auto-renew: on (last synced 3h ago)" when a server+token are configured;
keep the manual paste box for air-gapped/Enterprise.

### Tests (`apps/api/tests/test_license_refresh.py`)
- token+url set, fake server returns a key signed by the **test** keypair
  (`tests/_license_keys.py`) → written to DB, cache invalidated, edition flips.
- server 204/500/timeout → current key untouched (no clobber).
- server returns an **unsigned/forged** key → rejected, current key kept.
- feature off (empty config) → loop is a no-op (existing suite stays green).

---

## Build order
1. Part B config + `license_refresh.py` + tests (TDD) — no Stripe needed.
2. Part B lifespan loop + Settings UI.
3. Extract shared `sign_license()` helper (refactor `mint_license.py`).
4. Part A server + Stripe wiring (once the Stripe account exists).
5. Swap landing-page waitlist `mailto:` → Stripe Checkout link.

## Out of scope
Usage-based metering, seat auto-sync, online revocation (key just expires),
self-serve Enterprise (stays sales-led with manual annual keys).
