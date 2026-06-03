# Noodle Licensing & Feature-Gating Plan (Open-Core)

> Status: **PLAN ONLY — not implemented.** This document describes how to add
> license-gated feature limits to self-hosted Noodle. Nothing here is built yet.

## 1. Goal & Strategy

Monetize self-hosted Noodle via an **open-core** model: the product stays fully
self-hostable and free for small/personal use (the **Community** tier), while
higher resource ceilings and a set of "operate-at-scale / enterprise" features
require a paid **license key**.

This is the same model n8n, GitLab, Sentry and PostHog use. The free tier is the
adoption funnel; the paid tiers monetize the things teams/companies actually pay
for. Crucially this needs **no multi-tenancy** — each customer runs their own
instance, so we can ship revenue features long before any SaaS/cloud build.

### Design principles
- **Offline verification, no phone-home.** A license is a signed token. The
  product embeds only a *public* key and verifies the signature locally. No
  network call, works air-gapped, respects privacy. (Vendor holds the private
  signing key and mints licenses with a CLI.)
- **Un-forgeable.** Ed25519 signature over the license payload. Customers can't
  edit limits or expiry without invalidating the signature.
- **Soft-fail, never destructive.** Over-limit blocks *new* creation only. It
  never deletes, deactivates, or hides existing resources. Downgrading/expiry
  leaves everything running; you just can't add more until you're back under the
  limit.
- **Limits are data, not code.** The tier→limits mapping lives in one table so
  ceilings can be tuned without a release.
- **`0 = unlimited`** convention everywhere (matches existing settings like
  `run_retention_days`).

## 2. Tier Model

Three tiers. Community is the unlicensed default. Pro and Enterprise are unlocked
by a signed license whose payload names the tier and (optionally) overrides
individual limits.

| Capability / Limit                     | Community (free) | Pro            | Enterprise     |
|----------------------------------------|------------------|----------------|----------------|
| **Active workflows** (enabled)         | 5                | 50             | Unlimited      |
| **Total workflows** (incl. disabled)   | 25               | Unlimited      | Unlimited      |
| **Active deployments**                 | 2                | 25             | Unlimited      |
| **Environments**                       | 2                | 10             | Unlimited      |
| **Runner pools** (remote)              | 0 (in-proc only) | 3              | Unlimited      |
| **Team members / users**               | 2                | 15             | Unlimited      |
| **Concurrent runs ceiling**            | 4                | 25             | Operator-set   |
| **Schedule granularity (min interval)**| 15 min           | 1 min          | 1 min          |
| **Credentials stored**                 | 10               | Unlimited      | Unlimited      |
| **Code library modules**               | 10               | Unlimited      | Unlimited      |
| **Run history retention (max days)**   | 7                | 90             | Operator-set   |
| SSO / SAML / OIDC                       | —                | —              | ✓              |
| SCIM user provisioning                  | —                | —              | ✓              |
| RBAC (granular roles)                   | basic            | ✓              | ✓              |
| Audit log export                        | —                | ✓              | ✓              |
| External secrets manager (Vault/KMS)    | —                | —              | ✓              |
| Git-backed workflow versioning          | —                | ✓              | ✓              |
| Priority support / SLA                  | —                | email          | ✓              |

> Numbers above are **starting suggestions** — easy to tune. The mechanism is
> what matters; the exact ceilings are a pricing decision.

### Recommended free-tier "count" limits to enforce first (cheapest wins)
These are simple `COUNT(*) >= limit` checks at create time and give the clearest
upgrade prompts:

1. **Active workflows** — the headline limit (most-felt by growing users).
2. **Active deployments** — your original idea; ties directly to "running things".
3. **Environments** — your original idea.
4. **Users / team members** — natural "invite your team → upgrade" trigger.
5. **Runner pools** — remote execution is inherently a scale/Pro feature.
6. **Credentials** and **code modules** — softer, add later.

## 3. License Token Format

A license is a single opaque string the customer pastes into Settings. Internally
it is two base64url parts joined by a dot: `payload.signature`.

```
NOODLE-LICENSE-v1.<base64url(payload_json)>.<base64url(ed25519_sig)>
```

`payload_json` (signed, tamper-proof):
```jsonc
{
  "v": 1,
  "tier": "pro",                 // community | pro | enterprise
  "licensee": "Acme Corp",       // shown in UI
  "issued_at": "2026-06-02T00:00:00Z",
  "expires_at": "2027-06-02T00:00:00Z", // null = perpetual
  "limits": {                    // OPTIONAL per-deal overrides; null/absent = tier default
    "max_active_workflows": 200,
    "max_environments": 0        // 0 = unlimited for this customer
  },
  "features": ["sso", "audit_export", "git_versioning"], // feature flags
  "license_id": "lic_01J..."     // for support/revocation tracking
}
```

### Verification rules (in order)
1. Parse the three dot-separated segments; reject if malformed.
2. Verify Ed25519 signature against the **embedded public key**. Reject if invalid.
3. Check `expires_at`: if past, treat as **expired** → fall back to Community
   limits **but keep showing the licensee/tier as "expired"** (soft-fail).
4. Resolve effective limits: `tier defaults` overlaid by any `limits` overrides.
5. Cache the parsed/verified result in memory (license string rarely changes).

### Keys
- **Public key**: embedded as a base64 constant in
  `apps/api/app/services/licensing.py`. Safe to ship.
- **Private signing key**: vendor-only, stored outside the repo (gitignored
  `.secrets/` or a secrets manager). Never committed. Used solely by the issuing
  CLI.

## 4. Files to Add / Change

> Nothing below is written yet — this is the build checklist.

### New files
| File | Purpose |
|------|---------|
| `apps/api/app/services/licensing.py` | Core: embed public key, `parse_and_verify(token)`, `get_active_license()` (reads `system_settings.license_key`, caches), `effective_limits()`, `has_feature(name)`, tier-default `LIMITS` table, and `enforce_*` helpers raising HTTP 402. |
| `apps/api/app/routers/license.py` | `GET /license` (current tier, licensee, expiry, effective limits, current usage counts) and `PUT /license` (admin pastes/updates key; validates before saving; invalidates cache). |
| `apps/api/alembic/versions/0031_license_key.py` | Adds `system_settings.license_key TEXT NOT NULL DEFAULT ''`. Idempotent inspector guard like 0030. |
| `scripts/issue_license.py` | **Vendor CLI.** Reads private key from `NOODLE_LICENSE_SIGNING_KEY` (or `.secrets/…`), takes `--tier --licensee --expires --limit k=v --feature x`, prints the signed license string. Never shipped to customers. |
| `apps/api/tests/test_licensing.py` | Unit tests: valid/invalid/expired/forged tokens; each enforced limit blocks at threshold; a valid Pro license raises the ceiling; `0 = unlimited`. |

### Changed files
| File | Change |
|------|--------|
| `apps/api/app/config.py` | Add `license_key: str = ""` (boot fallback; the live value is the `system_settings` row). |
| `apps/api/app/models.py` | Add `license_key: Mapped[str]` to `SystemSetting`. |
| `apps/api/app/services/live_settings.py` | Add `license_key` to `LiveSettings` dataclass + both load paths + `_from_boot`. |
| `apps/api/app/schemas.py` | Add `license_key` to `SystemSettingsInfo`/`Update`; add `LicenseInfo` response schema (tier, licensee, expiry, limits, usage). |
| `apps/api/app/routers/deployments.py` | In `create_deployment`, before `session.add`: `await enforce_deployment_limit(session)`. |
| `apps/api/app/routers/environments.py` | In `create_environment`, before `session.add`: `await enforce_environment_limit(session)`. |
| `apps/api/app/routers/workflows.py` | Gate **active-workflow** count on create/enable; gate **total workflows** on create. |
| `apps/api/app/routers/runner_pools.py` | Gate runner-pool creation (Community = 0 → remote pools are Pro+). |
| `apps/api/app/routers/auth.py` (users) | Gate user creation/invite on `max_users`. |
| `apps/api/app/main.py` | `app.include_router(license.router)`. |
| `apps/api/tests/conftest.py` | Default the gate **off** for the existing suite (set an unlimited Community override, or `settings.license_enforcement = False`) so current tests stay green; `test_licensing.py` flips it on explicitly. |

### Frontend (later, separate task)
- Settings → **License** page: paste key, show tier/licensee/expiry + a usage
  meter per limit ("3 / 5 active workflows").
- Intercept **402** responses globally → show an "Upgrade to Pro" modal with the
  limit that was hit and a link to pricing.
- Badge in the header showing current tier.

## 5. Enforcement Mechanics

A single helper pattern keeps it DRY and uses the **request session** (no
background `SessionLocal`, so it's test-clean):

```text
async def enforce_limit(session, *, count_query, limit_key, label):
    lic = await get_active_license()          # cached; Community if none/expired
    limit = effective_limits(lic)[limit_key]  # int; 0 = unlimited
    if limit == 0:
        return
    current = await session.scalar(count_query)   # SELECT count(*) ...
    if current >= limit:
        raise HTTPException(402, detail={
            "error": "license_limit",
            "message": f"{label} limit reached ({current}/{limit}) on the {lic.tier} plan.",
            "limit": limit, "current": current,
            "tier": lic.tier, "limit_key": limit_key,
        })
```

- **HTTP 402 Payment Required** with structured `detail` so the UI can render a
  precise upgrade prompt. (Distinct from 403 permissions and 409 conflicts.)
- "Active workflow" / "active deployment" counts filter on the existing
  `active`/enabled flags, so disabling something frees a slot.
- Schedule-granularity and concurrency limits are enforced where those values
  are validated/applied (schedule create; `max_concurrent_runs` clamp in
  `live_settings`), not via count helpers.

## 6. Edge Cases & Decisions

- **Expiry = soft downgrade.** On expiry, effective limits revert to Community.
  Existing resources keep running; only new creation past the Community ceiling
  is blocked. UI shows "license expired — renew".
- **Already over the limit after a downgrade.** Never auto-delete. Block new
  creates until usage falls back under the ceiling. (e.g. expired-from-Pro user
  with 30 workflows keeps all 30, can't add #31 until under 5… realistically we
  only block *new* creates, we don't force them to delete — document this clearly).
- **Clock tampering** to dodge expiry: out of scope for offline licensing;
  acceptable risk for self-hosted (industry-standard). Enterprise deals can add
  online activation later if needed.
- **Air-gapped installs**: fully supported — verification is offline.
- **Test isolation**: gate defaults off in `conftest`; the existing 100+ tests
  must stay green. Only `test_licensing.py` enables enforcement.
- **Bypass risk (open source)**: a determined user can recompile to remove the
  check. That's true of all open-core; the license is for *honest companies*
  whose legal/procurement won't run unlicensed software. Not a DRM problem.

## 7. Build Order (when implementing)

1. `licensing.py` core (keys, verify, tier `LIMITS` table, `enforce_*`) + unit
   tests for verify/expiry/forgery — pure, no DB.
2. Model column + migration `0031` + `live_settings`/`schemas` wiring.
3. `license.py` router (GET/PUT) + `main.py` include.
4. Wire the first 3 gates: deployments, environments, active workflows.
5. `scripts/issue_license.py` vendor CLI + generate the real keypair into
   `.secrets/` (gitignored).
6. `conftest` default-off + `test_licensing.py` happy/limit/upgrade paths.
7. Remaining gates: users, runner pools, credentials, code modules.
8. Frontend License page + 402 upgrade modal (separate PR).
9. Run full backend suite; confirm green.

## 8. Out of Scope (explicitly deferred)
- Cloud/SaaS multi-tenancy, billing integration (Stripe), per-tenant isolation,
  sandboxing — these belong to the later **Noodle Cloud** effort, not this
  self-hosted licensing work.
- Online license activation / revocation server.
- Usage-based metering/billing.

---

## 9. Software License (the legal foundation)

> **Why this section exists:** the *feature-gate license keys* described above are
> only enforceable if the **source code itself** carries a license that (a) lets
> people legally self-host the free product, and (b) forbids circumventing the
> paid gates or reselling Noodle as a hosted service. Today the repo has **no
> `LICENSE` file at all**, which under copyright law means *all rights reserved* —
> nobody may legally use, modify, or self-host it. That must be fixed for the
> open-core plan to work.

### Decision: two-layer license (the n8n / GitLab model)

| Layer | Scope | License | Rationale |
|-------|-------|---------|-----------|
| **Core** | Everything except the `ee/` enterprise modules | **Business Source License 1.1** (BSL) with an Additional Use Grant permitting all use *except* offering Noodle as a hosted/managed service to third parties; **Change Date** = 4 years after each release → converts to **Apache 2.0** | Free to self-host & use internally; blocks a competitor from cloning "Noodle Cloud"; auto-opens over time so it's not permanently proprietary. Same approach as Sentry, MariaDB, (now) Terraform/Redis. |
| **Enterprise** | The licensing/enforcement code and paid features (`app/services/licensing.py`, SSO, audit export, external secrets, etc.) — by convention under an `ee/` path or clearly marked modules | **Proprietary "Noodle Enterprise Edition License"** — usable only with a valid license key | Makes stripping out or circumventing the gate a clear license violation, not just bad manners. Same as GitLab's `ee/` folder. |

### Why not the alternatives
- **MIT / Apache-only (permissive):** anyone — including a hyperscaler — can take
  the whole codebase, delete `licensing.py`, and sell a competing hosted Noodle.
  This is exactly what pushed Elastic, MongoDB, Redis, HashiCorp to relicense.
  Only choose pure Apache 2.0 if community trust + distro inclusion matter more
  than blocking competitors, and you rely on brand + hosted convenience as the moat.
- **AGPL:** copyleft network clause deters the enterprise customers we want to
  *sell* to (their legal teams often ban AGPL), without actually stopping a
  determined competitor who is willing to open their changes.

### Trademark (cheapest, strongest lever)
- Register / assert the **"Noodle" name and logo** independently of the code
  license. Even a permitted fork **may not call itself "Noodle."** This protects
  the brand regardless of which code license is chosen. Add a `TRADEMARK.md`.

### Files to add (drafted alongside this plan)
| File | Content |
|------|---------|
| `LICENSE` | BSL 1.1 filled in with Noodle parameters (Licensor, Change Date, Change License = Apache 2.0, Additional Use Grant). |
| `LICENSE.enterprise` | Proprietary Noodle Enterprise Edition License covering `ee/`-marked modules and the license-key system. |
| `TRADEMARK.md` | Short policy: the name/logo are trademarks; forks must rename. |
| `pyproject.toml` (`license` field) + file headers | Reference the chosen licenses so packaging metadata is accurate. |

### How it ties back to the feature gates
- `app/services/licensing.py` and the enterprise features are **covered by
  `LICENSE.enterprise`**, so removing/bypassing the key check is a breach of that
  license — the legal teeth behind the technical Ed25519 verification.
- The BSL **Additional Use Grant** is the clause that stops "Noodle Cloud
  competitors"; the **Change Date → Apache 2.0** keeps the community comfortable
  that it isn't locked up forever.

> ⚠️ **Not legal advice.** Have an attorney review the BSL *Additional Use Grant*
> wording and the Enterprise license before publishing — those exact terms decide
> whether you can actually stop a competitor. The drafts below are a starting
> point, not a substitute for counsel.
