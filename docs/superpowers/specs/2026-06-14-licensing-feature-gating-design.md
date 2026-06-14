# Licensing & Tiered Feature Gating — Design Spec

**Date:** 2026-06-14
**Branch:** `feat/arch-program-phase5`
**Status:** Approved design, pending implementation plan

## 1. Goal & Context

Noodle launches **single-tenant** (`multi_tenancy_enabled=False`) with multi-tenancy
dormant on the backend. To monetize, we need a licensing layer that gates features and
resource counts into three commercial editions:

- **Community** — free, self-hosted, single-tenant. The adoption engine.
- **Pro** — paid subscription, self-hosted. Team & scale.
- **Enterprise** — sales-led. Compliance, isolation, governance.

The platform already has the right *numeric* seam: `app/services/org_limits.py` →
`EffectiveLimits` with an override chain (`org_settings` row → instance default,
`0 = unlimited`). What is missing is (a) the **license source**, (b) **boolean
entitlements** (`has_feature(...)`), and (c) **resource-count gating** (environments,
runners, deployments, seats). This spec adds those without disturbing existing behavior:
**with no license key present, the instance resolves to Community and behaves exactly as
today, minus the new Community count caps.**

### Non-goals (YAGNI)
- Cloud billing / payment processing. We ship the *mechanism* (apply a key → unlock); a
  Stripe/checkout flow is a later, separate project.
- Per-org tiering at runtime. Tier is **instance-level** now. A per-org override layer is
  *designed for* (see §4.4) but not built.
- Execution-volume metering as a Community lever. `RunMeter` exists for future cloud usage
  billing; self-hosted Community is gated on **counts + capabilities**, not run volume.

## 2. License Model (Hybrid)

Two resolution sources, checked in order, both feeding one resolver:

1. **Signed offline license key** (primary, self-hosted). An Ed25519-signed token set via
   `NOODLE_LICENSE_KEY` env var **or** uploaded in the UI and persisted to
   `system_settings`. Verified locally against a **public key baked into the codebase** —
   no phone-home, air-gap friendly (n8n / GitLab self-hosted model).
2. **Per-org DB tier** (seam only, for future cloud). A `tier` column reserved on
   `organizations`. Not consulted while `multi_tenancy_enabled=False`. Documented here so
   the resolver signature anticipates it; the column + lookup land with cloud, not now.

If neither yields a valid, unexpired license → **Community**.

### License token payload
```json
{
  "tier": "pro",                    // "community" | "pro" | "enterprise"
  "customer": "Acme Inc",
  "seats": 10,                      // informational + seat cap source
  "issued_at": 1765432100,
  "expires_at": 1797000000,         // unix seconds; omitted = perpetual
  "features": ["sandbox", "sso"],   // optional explicit grants beyond the tier default
  "limits": {"environments": 25}    // optional explicit numeric overrides
}
```
Signature is detached (`<base64url(payload)>.<base64url(sig)>`). The public key is a
module constant; the **private key never ships** (kept by Harry to mint keys via a small
offline CLI script, `tools/mint_license.py`).

### Expiry behavior
An expired license **gracefully downgrades to Community** — it never bricks running
workflows or deletes data. The UI shows a persistent "license expired" banner. Resources
already over the Community cap keep working but **block new creation** until renewed
(no destructive enforcement).

## 3. Tier Matrix

| Capability | Community | Pro | Enterprise |
|---|---|---|---|
| Environments | 3 | 10 | unlimited |
| Runners (pools) | 1 | 5 | unlimited |
| Active deployments | 3 | unlimited | unlimited |
| Seats (users) | 2 | 10 | unlimited |
| All nodes (AI/ML, integrations) | ✅ | ✅ | ✅ |
| MCP server, webhooks, scheduling | ✅ | ✅ | ✅ |
| Sandboxed execution (`execution_sandbox`) | — | ✅ | ✅ |
| Observability (OpenTelemetry) | — | ✅ | ✅ |
| Git sync / env promotion (when built) | — | ✅ | ✅ |
| Multi-tenancy / organizations | — | — | ✅ |
| SSO / SAML / SCIM (when built) | — | — | ✅ |
| Org-KEK / external KMS | — | — | ✅ |
| Audit logs / advanced RBAC | — | — | ✅ |
| Dedicated execution pools | — | — | ✅ |
| Support | Community | Email | SLA + priority |

`unlimited = 0` (matches the existing `org_limits` convention). Feature flags that gate
not-yet-built surfaces (SSO, git sync, audit logs) are defined now so enforcement is a
one-line check when those features land.

### Pricing (recorded for product, not enforced in code)
- Community: **$0**.
- Pro: **$49/mo per instance** ($490/yr).
- Enterprise: **Contact sales** (indicative floor ~$18k–30k/yr).

Prices are starting hypotheses to validate with the first paying customers. The code
stores tiers, not prices — pricing is external config.

## 4. Architecture

### 4.1 New module: `app/services/licensing.py`
Single source of truth. Pure-ish (no request state); cached like `live_settings`.

```python
class Edition(str, Enum):
    COMMUNITY = "community"
    PRO = "pro"
    ENTERPRISE = "enterprise"

class Feature(str, Enum):
    SANDBOX = "sandbox"
    OBSERVABILITY = "observability"
    GIT_SYNC = "git_sync"
    MULTI_TENANCY = "multi_tenancy"
    SSO = "sso"
    EXTERNAL_KMS = "external_kms"
    AUDIT_LOGS = "audit_logs"
    ADVANCED_RBAC = "advanced_rbac"
    DEDICATED_POOLS = "dedicated_pools"

@dataclass(frozen=True)
class License:
    edition: Edition
    features: frozenset[Feature]
    limits: ResourceLimits          # environments, runners, deployments, seats
    customer: str | None
    expires_at: int | None
    valid: bool                     # False + edition=COMMUNITY when key bad/expired
    notice: str | None              # surfaced to UI (e.g. "license expired")

def current_license() -> License            # cached, ~5s TTL like live_settings
def has_feature(feature: Feature) -> bool    # current_license().edition grant or explicit
def resource_limit(name: str) -> int         # 0 = unlimited
def invalidate_license_cache() -> None
```

- `TIER_DEFAULTS: dict[Edition, (features, ResourceLimits)]` encodes §3.
- A license's explicit `features` / `limits` **add to / override** its tier defaults (lets
  us issue a Pro key with one Enterprise feature without a custom tier).
- Verification helper `_verify_token(raw) -> dict | None` (Ed25519 via `cryptography`,
  already a transitive dep — confirm in plan). Bad signature/expiry → `None` → Community.

### 4.2 Resource-count gating
Counts are not in `EffectiveLimits` today. Add a thin guard called at create-time:

```python
# app/services/licensing.py
async def enforce_resource_cap(session, kind: str) -> None:
    """Raise LicenseLimitError(402/403) if creating one more `kind` would exceed
    the licensed cap. kind in {"environments","runners","deployments","seats"}."""
```
Wired into the four create paths:
- `routers/environments.py:create_environment` → `enforce_resource_cap(.., "environments")`
- `routers/runner_pools.py:create_runner_pool` → `"runners"`
- `routers/deployments.py:create_deployment` → `"deployments"` (counts deployment rows;
  whether to count only `active=True` is Open Question §9)
- `routers/auth.py:create_user` → `"seats"`

Count query is scoped the same way the list endpoints are (org filter is a no-op while
single-tenant, correct automatically when MT turns on).

### 4.3 Capability gating
A FastAPI dependency for route-level gates and an inline check for service-level:

```python
def require_feature(feature: Feature) -> Callable:   # dependency → 402 if missing
```
Applied to (initial set; expands as features ship):
- Sandbox: `execution_sandbox != "off"` honored only when `has_feature(SANDBOX)`; else the
  startup policy forces `off` with a warning (mirrors existing `sandbox_policy_strict`).
- Org routes (`routers/orgs.py`) already 400 when `multi_tenancy_enabled` is off; add
  `has_feature(MULTI_TENANCY)` as the licensing gate so enabling MT also requires Enterprise.
- OTel init in `main.py` lifespan checks `has_feature(OBSERVABILITY)`.

Capabilities that are config-driven (sandbox, MT, OTel) get a **startup reconciliation**:
if config requests a feature the license doesn't grant, log a clear warning and disable it
rather than failing to boot (self-hosted operator-friendly).

### 4.4 Surfacing to the frontend
Extend the existing pattern (`AuthRequiredResponse.multi_tenancy`) — add to `/auth/me`:
```python
class AuthRequiredResponse(BaseModel):
    ...
    edition: str = "community"
    entitlements: list[str] = []        # Feature values granted
    limits: dict[str, int] = {}         # resource caps, 0 = unlimited
    license_notice: str | None = None   # banner text (expiry etc.)
```
Frontend (`store` / `AuthState`) reads these; gated buttons render a lock + "Upgrade"
affordance instead of erroring, and over-cap create buttons disable with a tooltip. A small
`useEntitlements()` hook centralizes `has(feature)` / `atLimit(kind, currentCount)`.

### 4.5 License management UI
A **Security/Settings → License** section: shows current edition, customer, expiry, seat
usage, and the resource caps; a textarea to paste a key (POST `/license`, admin/owner only,
persisted to `system_settings`, cache invalidated); a "remove license" action (→ Community).
Reuses the existing settings-page patterns (skeletons, `useConfirm`, themed forms).

### 4.6 Minting CLI
`tools/mint_license.py` — offline script Harry runs to sign a key from the private key
(env/file path arg). Not shipped to customers; not imported by the app. Produces the
`<payload>.<sig>` string to hand a Pro/Enterprise customer.

## 5. Data / Migration
- New table `licenses` is **not** needed — the active key lives in the existing
  `system_settings` singleton row (new key `license_key`). One migration adds nothing
  structural; the value is written via the management endpoint.
- Reserved-for-cloud: `organizations.tier` column. **Deferred** — added with the cloud
  workstream, not this spec. Mentioned so the resolver signature is forward-compatible.

## 6. Error Handling
- Bad/missing/expired key → silent fallback to Community + `notice`. Never 500.
- Over-cap create → `402 Payment Required` (or 403; decide in plan) with a body naming the
  cap, current count, and edition — so the UI can show "Environments: 3/3 on Community.
  Upgrade to Pro for 10." Never a generic error.
- Capability route hit without entitlement → `402` with the same shape.
- Config requests an unlicensed capability → boot succeeds, capability disabled, warning
  logged (and surfaced via `/ops/runtime-mode` alongside the existing warnings).

## 7. Testing
- `test_licensing.py`: token verify (valid/tampered/expired/wrong-key), tier-default
  resolution, explicit feature/limit overrides, Community fallback, cache invalidation.
- Resource-cap tests: create up to cap succeeds, next is blocked, unlimited (0) never
  blocks; per kind (environments/runners/deployments/seats).
- Capability gates: `require_feature` 402 when absent, passes when granted; sandbox/OTel/MT
  startup reconciliation downgrades cleanly.
- `/auth/me` exposes edition/entitlements/limits; default (no key) = Community parity.
- **Parity guard:** the full existing suite passes unchanged with no license key present,
  except newly-expected Community-cap behavior (assert the 3/1/3/2 caps explicitly).

## 8. Build Order (for the plan)
1. `licensing.py` core (Edition/Feature/License, token verify, tier defaults, cache) + tests.
2. `mint_license.py` CLI + a committed test keypair fixture.
3. Resource-cap guard + wire into 4 create routes + tests.
4. `require_feature` dependency + capability gates (sandbox/MT/OTel reconciliation) + tests.
5. `/auth/me` surface + `/license` management endpoint + tests.
6. Frontend: `useEntitlements`, gated/over-cap affordances, License settings page.
7. Docs: deployment.md "Editions & licensing" section.

## 9. Open Questions (resolve in plan, not blockers)
- Deployments cap: count all deployment rows vs only `active=True`. Leaning all rows.
- Over-cap status code: 402 vs 403. Leaning 402 (semantically "upgrade").
- Confirm `cryptography` (Ed25519) is already in the API dependency set; if not, add it.
