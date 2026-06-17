# Licensing & Tiered Feature Gating Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a hybrid licensing layer that resolves a Noodle instance to one of three editions (Community/Pro/Enterprise) and gates resource counts (environments, runners, active deployments, seats) and capabilities (sandbox, MT, observability, SSO, …).

**Architecture:** A new async service `app/services/licensing.py` resolves a `License` from a signed Ed25519 key (env `NOODLE_LICENSE_KEY` first, else the `system_settings.license_key` DB row), cached on a TTL exactly like `live_settings`/`org_limits`. With no key, the instance is Community and behaves as today plus the Community count caps. Resource caps are enforced at the four create paths (+ the deployment activate transition); capabilities via a `require_feature` FastAPI dependency and lifespan reconciliation. Entitlements surface to the SPA through the existing `/auth/required` bootstrap response.

**Tech Stack:** FastAPI, SQLAlchemy async, Pydantic, Alembic, `cryptography` (Ed25519 — already a direct dep `cryptography>=42.0`), React/TypeScript + Zustand store, Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-06-14-licensing-feature-gating-design.md`

---

## File Structure

**Backend — create:**
- `apps/api/app/services/licensing.py` — `Edition`, `Feature`, `ResourceLimits`, `License`, tier defaults, token verification, async cached `current_license()`, `has_feature()`, `resource_limit()`, `enforce_resource_cap()`, `require_feature()`.
- `apps/api/tools/mint_license.py` — offline CLI to sign a license payload with the private key (NOT imported by the app; not shipped to customers).
- `apps/api/alembic/versions/0051_license_key.py` — adds `license_key` TEXT column to `system_settings`.
- `apps/api/tests/test_licensing.py` — unit tests for the resolver + caps + gates.
- `apps/api/tests/_license_keys.py` — committed **test-only** Ed25519 keypair + a `make_key()` helper for tests.

**Backend — modify:**
- `apps/api/app/config.py` — add `license_key: str = ""` and `license_public_key: str = ""` settings.
- `apps/api/app/models.py` — add `SystemSetting.license_key`.
- `apps/api/app/schemas.py` — extend `AuthRequiredResponse`; add `LicenseInfo`, `LicenseApply`.
- `apps/api/app/routers/auth.py` — populate new `AuthRequiredResponse` fields in `auth_required`; add seats cap to `create_user`.
- `apps/api/app/routers/environments.py` — environments cap in `create_environment`.
- `apps/api/app/routers/runner_pools.py` — runners cap in `create_runner_pool`.
- `apps/api/app/routers/deployments.py` — active-deployments cap in `create_deployment` (when `active`) + `update_deployment` (`becoming_active`).
- `apps/api/app/routers/system.py` (or wherever `system_settings` admin routes live — confirm in Task 6) — add `GET /license` + `POST /license` + `DELETE /license`.
- `apps/api/app/main.py` — lifespan capability reconciliation (sandbox/OTel/MT vs license) + warnings.

**Frontend — create/modify (Task 7):**
- `apps/web/src/hooks/useEntitlements.ts` — `has(feature)`, `atLimit(kind, count)`, `edition`.
- `apps/web/src/pages/LicensePage.tsx` (or a section in the existing Security/Settings page) — view edition, paste/remove key.
- Store/`AuthState` extension + gated affordances on the four create buttons.

**Docs:**
- `docs/deployment.md` — "Editions & licensing" section.

---

## Task 1: Licensing core module (resolver, tiers, token verify)

**Files:**
- Create: `apps/api/tests/_license_keys.py`
- Create: `apps/api/app/services/licensing.py`
- Modify: `apps/api/app/config.py`
- Create: `apps/api/tests/test_licensing.py`

- [ ] **Step 1: Add a committed test-only keypair helper**

Create `apps/api/tests/_license_keys.py`:

```python
"""Test-only Ed25519 keypair + license minting helper.

NOT used in production. Production verifies against the public key baked into
``app/services/licensing.py`` (or ``settings.license_public_key``); tests point
``settings.license_public_key`` at TEST_PUBLIC_KEY_PEM and mint with this private
key so we never need the real private key in the repo.
"""
from __future__ import annotations

import base64
import json
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Deterministic test key (32 bytes). Regenerating is fine — it only affects tests.
_TEST_SEED = b"noodle-test-license-signing-seed"  # 32 bytes
_PRIV = Ed25519PrivateKey.from_private_bytes(_TEST_SEED)

TEST_PUBLIC_KEY_PEM = _PRIV.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()


def make_key(payload: dict) -> str:
    """Sign ``payload`` with the test private key → '<b64url(json)>.<b64url(sig)>'."""
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    sig = _PRIV.sign(body)
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=")
    return f"{body.decode()}.{sig_b64.decode()}"


def pro_key(**overrides) -> str:
    payload = {"tier": "pro", "customer": "Test Co",
               "issued_at": int(time.time()), "expires_at": int(time.time()) + 3600}
    payload.update(overrides)
    return make_key(payload)
```

- [ ] **Step 2: Add config settings for the license**

In `apps/api/app/config.py`, add after the `mcp_server_enabled` line (near other feature flags):

```python
    # Licensing (see app/services/licensing.py). A signed Ed25519 license key
    # set here (env NOODLE_LICENSE_KEY) takes precedence over the DB-stored key.
    # Blank → resolve from system_settings.license_key, else Community edition.
    license_key: str = ""
    # PEM-encoded Ed25519 public key used to verify license keys. Blank → use
    # the key baked into app/services/licensing.py. Tests override this.
    license_public_key: str = ""
```

- [ ] **Step 3: Write failing tests for the resolver**

Create `apps/api/tests/test_licensing.py`:

```python
import time

import pytest

from app.config import settings
from app.services import licensing
from app.services.licensing import Edition, Feature
from tests._license_keys import TEST_PUBLIC_KEY_PEM, make_key, pro_key


@pytest.fixture(autouse=True)
def _use_test_pubkey(monkeypatch):
    monkeypatch.setattr(settings, "license_public_key", TEST_PUBLIC_KEY_PEM)
    monkeypatch.setattr(settings, "license_key", "")
    licensing.invalidate_license_cache()
    yield
    licensing.invalidate_license_cache()


@pytest.mark.asyncio
async def test_no_key_is_community():
    lic = await licensing.current_license()
    assert lic.edition is Edition.COMMUNITY
    assert lic.valid is True
    assert not await licensing.has_feature(Feature.SANDBOX)
    assert await licensing.resource_limit("environments") == 3


@pytest.mark.asyncio
async def test_valid_pro_key(monkeypatch):
    monkeypatch.setattr(settings, "license_key", pro_key())
    licensing.invalidate_license_cache()
    lic = await licensing.current_license()
    assert lic.edition is Edition.PRO
    assert await licensing.has_feature(Feature.SANDBOX)
    assert await licensing.resource_limit("environments") == 10
    assert await licensing.resource_limit("deployments") == 0  # unlimited


@pytest.mark.asyncio
async def test_enterprise_unlocks_mt(monkeypatch):
    monkeypatch.setattr(settings, "license_key", make_key(
        {"tier": "enterprise", "customer": "Big Co"}))
    licensing.invalidate_license_cache()
    assert await licensing.has_feature(Feature.MULTI_TENANCY)
    assert await licensing.resource_limit("environments") == 0


@pytest.mark.asyncio
async def test_tampered_key_falls_back_to_community(monkeypatch):
    bad = pro_key()[:-4] + "AAAA"
    monkeypatch.setattr(settings, "license_key", bad)
    licensing.invalidate_license_cache()
    lic = await licensing.current_license()
    assert lic.edition is Edition.COMMUNITY
    assert lic.notice is not None


@pytest.mark.asyncio
async def test_expired_key_downgrades(monkeypatch):
    monkeypatch.setattr(settings, "license_key", make_key(
        {"tier": "pro", "expires_at": int(time.time()) - 10}))
    licensing.invalidate_license_cache()
    lic = await licensing.current_license()
    assert lic.edition is Edition.COMMUNITY
    assert "expired" in (lic.notice or "").lower()


@pytest.mark.asyncio
async def test_explicit_feature_grant_extends_tier(monkeypatch):
    # A Pro key that additionally grants one Enterprise feature.
    monkeypatch.setattr(settings, "license_key", make_key(
        {"tier": "pro", "features": ["sso"]}))
    licensing.invalidate_license_cache()
    assert await licensing.has_feature(Feature.SSO)
    assert await licensing.has_feature(Feature.SANDBOX)  # still has Pro defaults


@pytest.mark.asyncio
async def test_explicit_limit_override(monkeypatch):
    monkeypatch.setattr(settings, "license_key", make_key(
        {"tier": "pro", "limits": {"environments": 25}}))
    licensing.invalidate_license_cache()
    assert await licensing.resource_limit("environments") == 25
```

- [ ] **Step 4: Run the tests — expect failure**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_licensing.py -q`
Expected: FAIL (`ModuleNotFoundError: app.services.licensing`).

- [ ] **Step 5: Implement `app/services/licensing.py`**

```python
"""Edition/feature resolution from a signed offline license key.

Resolution order: settings.license_key (env) → system_settings.license_key (DB)
→ Community. Verified locally against an Ed25519 public key (settings.license_public_key
or the baked default). Cached on a short TTL like live_settings/org_limits.
0 limit = unlimited (matches the org_limits convention).
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, replace
from enum import Enum

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.config import settings as boot_settings

# Production public key. Replace the placeholder with the PEM for the keypair
# whose PRIVATE half you keep offline (tools/mint_license.py). Until replaced,
# only settings.license_public_key (tests / staging) verifies anything.
_BAKED_PUBLIC_KEY_PEM = ""

_CACHE_TTL_SECONDS = 30.0


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
class ResourceLimits:
    environments: int
    runners: int
    deployments: int
    seats: int


@dataclass(frozen=True)
class License:
    edition: Edition
    features: frozenset
    limits: ResourceLimits
    customer: str | None
    expires_at: int | None
    valid: bool
    notice: str | None


_PRO_FEATURES = frozenset({Feature.SANDBOX, Feature.OBSERVABILITY, Feature.GIT_SYNC})
_ENT_FEATURES = _PRO_FEATURES | frozenset({
    Feature.MULTI_TENANCY, Feature.SSO, Feature.EXTERNAL_KMS,
    Feature.AUDIT_LOGS, Feature.ADVANCED_RBAC, Feature.DEDICATED_POOLS,
})

# (features, ResourceLimits) per tier. 0 = unlimited.
TIER_DEFAULTS: dict[Edition, tuple[frozenset, ResourceLimits]] = {
    Edition.COMMUNITY: (frozenset(), ResourceLimits(environments=3, runners=1,
                                                    deployments=3, seats=2)),
    Edition.PRO: (_PRO_FEATURES, ResourceLimits(environments=10, runners=5,
                                                deployments=0, seats=10)),
    Edition.ENTERPRISE: (_ENT_FEATURES, ResourceLimits(environments=0, runners=0,
                                                       deployments=0, seats=0)),
}

_COMMUNITY = License(
    edition=Edition.COMMUNITY,
    features=TIER_DEFAULTS[Edition.COMMUNITY][0],
    limits=TIER_DEFAULTS[Edition.COMMUNITY][1],
    customer=None, expires_at=None, valid=True, notice=None,
)

_cache: tuple[float, License] | None = None


def invalidate_license_cache() -> None:
    global _cache
    _cache = None


def _public_key() -> Ed25519PublicKey | None:
    pem = boot_settings.license_public_key or _BAKED_PUBLIC_KEY_PEM
    if not pem:
        return None
    try:
        key = serialization.load_pem_public_key(pem.encode())
        return key if isinstance(key, Ed25519PublicKey) else None
    except ValueError:
        return None


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _verify(raw: str) -> dict | None:
    """Return the payload dict if signature is valid, else None."""
    key = _public_key()
    if key is None or "." not in raw:
        return None
    body, sig = raw.rsplit(".", 1)
    try:
        key.verify(_b64url_decode(sig), body.encode())
        return json.loads(_b64url_decode(body))
    except (InvalidSignature, ValueError, json.JSONDecodeError):
        return None


def _community(notice: str | None) -> License:
    return replace(_COMMUNITY, notice=notice) if notice else _COMMUNITY


def _build(payload: dict) -> License:
    try:
        edition = Edition(payload.get("tier", "community"))
    except ValueError:
        return _community("Unknown license tier; treating as Community.")
    exp = payload.get("expires_at")
    if exp is not None and time.time() > exp:
        return _community("License expired; reverted to Community.")
    base_features, base_limits = TIER_DEFAULTS[edition]
    extra = set()
    for name in payload.get("features", []):
        try:
            extra.add(Feature(name))
        except ValueError:
            pass
    overrides = payload.get("limits", {}) or {}
    limits = ResourceLimits(
        environments=overrides.get("environments", base_limits.environments),
        runners=overrides.get("runners", base_limits.runners),
        deployments=overrides.get("deployments", base_limits.deployments),
        seats=overrides.get("seats", base_limits.seats),
    )
    return License(
        edition=edition, features=frozenset(base_features) | frozenset(extra),
        limits=limits, customer=payload.get("customer"), expires_at=exp,
        valid=True, notice=None,
    )


async def _db_license_key() -> str | None:
    from app.db import SessionLocal
    from app.models import SystemSetting
    try:
        async with SessionLocal() as session:
            row = await session.get(SystemSetting, "singleton")
            return getattr(row, "license_key", None) if row else None
    except (OperationalError, ProgrammingError):
        return None


async def current_license() -> License:
    global _cache
    now = time.monotonic()
    if _cache is not None and now < _cache[0]:
        return _cache[1]
    raw = boot_settings.license_key or (await _db_license_key()) or ""
    if not raw:
        lic = _COMMUNITY
    else:
        payload = _verify(raw)
        lic = _build(payload) if payload is not None else _community(
            "License key signature invalid; treating as Community.")
    _cache = (now + _CACHE_TTL_SECONDS, lic)
    return lic


async def has_feature(feature: Feature) -> bool:
    return feature in (await current_license()).features


async def resource_limit(name: str) -> int:
    return getattr((await current_license()).limits, name)
```

- [ ] **Step 6: Run the tests — expect pass**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_licensing.py -q`
Expected: PASS (7 tests).

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/services/licensing.py apps/api/app/config.py \
        apps/api/tests/test_licensing.py apps/api/tests/_license_keys.py
git commit -m "feat(licensing): edition/feature resolver from signed license key"
```

---

## Task 2: Offline license-minting CLI

**Files:**
- Create: `apps/api/tools/mint_license.py`

- [ ] **Step 1: Implement the minting CLI**

Create `apps/api/tools/mint_license.py`:

```python
"""Offline license-key minting tool. Run by the vendor, never shipped.

Generate a keypair once:
    python -m tools.mint_license keygen --out license_key
  -> writes license_key (private, KEEP SECRET) + license_key.pub (PEM public).
  Paste the .pub contents into _BAKED_PUBLIC_KEY_PEM in app/services/licensing.py.

Mint a key:
    python -m tools.mint_license sign --private license_key --tier pro \
        --customer "Acme Inc" --seats 10 --days 365
"""
from __future__ import annotations

import argparse
import base64
import json
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _keygen(out: str) -> None:
    priv = Ed25519PrivateKey.generate()
    with open(out, "wb") as f:
        f.write(priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
    with open(out + ".pub", "wb") as f:
        f.write(priv.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo))
    print(f"wrote {out} (private) and {out}.pub (public)")


def _sign(args) -> None:
    with open(args.private, "rb") as f:
        priv = serialization.load_pem_private_key(f.read(), password=None)
    payload = {"tier": args.tier, "customer": args.customer,
               "issued_at": int(time.time())}
    if args.seats:
        payload["seats"] = args.seats
    if args.days:
        payload["expires_at"] = int(time.time()) + args.days * 86400
    if args.feature:
        payload["features"] = args.feature
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    sig = base64.urlsafe_b64encode(priv.sign(body)).rstrip(b"=")
    print(f"{body.decode()}.{sig.decode()}")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    kg = sub.add_parser("keygen"); kg.add_argument("--out", default="license_key")
    sg = sub.add_parser("sign")
    sg.add_argument("--private", required=True)
    sg.add_argument("--tier", required=True, choices=["pro", "enterprise"])
    sg.add_argument("--customer", required=True)
    sg.add_argument("--seats", type=int, default=0)
    sg.add_argument("--days", type=int, default=0)
    sg.add_argument("--feature", action="append", default=[])
    args = p.parse_args()
    if args.cmd == "keygen":
        _keygen(args.out)
    else:
        _sign(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test the CLI round-trips with the resolver**

Run (from `apps/api`):
```bash
.venv/Scripts/python.exe -m tools.mint_license keygen --out /tmp/lk
.venv/Scripts/python.exe -m tools.mint_license sign --private /tmp/lk --tier pro --customer Demo --days 30
```
Expected: prints `<body>.<sig>`. (Manual check only — automated verification is covered by Task 1 tests using the in-repo test key.)

- [ ] **Step 3: Commit**

```bash
git add apps/api/tools/mint_license.py
git commit -m "feat(licensing): offline keygen + license-minting CLI"
```

---

## Task 3: Persist the license key (migration + model + DB load)

**Files:**
- Modify: `apps/api/app/models.py:865` (`SystemSetting`)
- Create: `apps/api/alembic/versions/0051_license_key.py`
- Modify: `apps/api/tests/test_licensing.py` (add DB-source test)

- [ ] **Step 1: Add the model column**

In `apps/api/app/models.py`, inside `SystemSetting`, add after `worker_rss_soft_budget_bytes`:

```python
    license_key: Mapped[str | None] = mapped_column(Text, nullable=True)
```
(`Text` is already imported in models.py.)

- [ ] **Step 2: Write the migration**

Create `apps/api/alembic/versions/0051_license_key.py`:

```python
"""add license_key to system_settings

Revision ID: 0051_license_key
Revises: 0050_workflow_mcp
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0051_license_key"
down_revision: str | None = "0050_workflow_mcp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("system_settings") as batch:
        batch.add_column(sa.Column("license_key", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("system_settings") as batch:
        batch.drop_column("license_key")
```

- [ ] **Step 3: Add a failing DB-source test**

Append to `apps/api/tests/test_licensing.py`:

```python
@pytest.mark.asyncio
async def test_key_resolved_from_db_when_env_blank(db_session, monkeypatch):
    from app.services.live_settings import ensure_singleton_row
    from app.db import SessionLocal
    from app.models import SystemSetting
    monkeypatch.setattr(settings, "license_key", "")
    await ensure_singleton_row()
    async with SessionLocal() as s:
        row = await s.get(SystemSetting, "singleton")
        row.license_key = pro_key()
        await s.commit()
    licensing.invalidate_license_cache()
    lic = await licensing.current_license()
    assert lic.edition is Edition.PRO
```
(Use the project's existing async DB fixture name; if it is not `db_session`, match the fixture used elsewhere in `apps/api/tests` — confirm via `conftest.py`.)

- [ ] **Step 4: Run migration + tests**

Run:
```bash
cd apps/api && .venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m pytest tests/test_licensing.py -q
```
Expected: migration applies cleanly; all licensing tests PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/models.py apps/api/alembic/versions/0051_license_key.py apps/api/tests/test_licensing.py
git commit -m "feat(licensing): persist license key on system_settings"
```

---

## Task 4: Resource-count caps at create paths

**Files:**
- Modify: `apps/api/app/services/licensing.py` (add `LicenseLimitError` + `enforce_resource_cap`)
- Modify: `apps/api/app/routers/environments.py:134`
- Modify: `apps/api/app/routers/runner_pools.py:290`
- Modify: `apps/api/app/routers/deployments.py:201` and `:277`
- Modify: `apps/api/app/routers/auth.py:377`
- Create: `apps/api/tests/test_license_caps.py`

- [ ] **Step 1: Write failing cap tests**

Create `apps/api/tests/test_license_caps.py`:

```python
import pytest

from app.config import settings
from app.services import licensing


@pytest.fixture(autouse=True)
def _community(monkeypatch):
    monkeypatch.setattr(settings, "license_key", "")
    monkeypatch.setattr(settings, "license_public_key", "")
    licensing.invalidate_license_cache()
    yield
    licensing.invalidate_license_cache()


@pytest.mark.asyncio
async def test_environment_cap_blocks_fourth(client, auth_headers):
    # Community cap = 3. The global env already exists, so creating 3 more
    # crosses the cap on the 3rd create. Assert a 402 appears.
    statuses = []
    for i in range(4):
        r = await client.post("/environments",
                              json={"name": f"env{i}", "python_version": "3.12"},
                              headers=auth_headers)
        statuses.append(r.status_code)
    assert 402 in statuses
    # The 402 body names the cap.
    blocked = next(r for r in [statuses] if 402 in r)  # placeholder; see note
```
NOTE for implementer: match the repo's existing API-test harness (the `client`/`auth_headers` fixtures used across `apps/api/tests` — e.g. `test_review_bugs.py`). Adjust the count math to the actual seeded environment count: read one existing env-creation test first and mirror its setup. The assertion that matters: **once the licensed count is reached, the next create returns 402 with a body naming `environments`, the current count, and `community`.**

- [ ] **Step 2: Run — expect failure (creates currently always succeed)**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_license_caps.py -q`
Expected: FAIL (no 402 — caps not enforced yet).

- [ ] **Step 3: Add the cap helper to `licensing.py`**

Append to `apps/api/app/services/licensing.py`:

```python
from fastapi import HTTPException, status as _http_status
from sqlalchemy import func, select


class LicenseLimitError(HTTPException):
    def __init__(self, kind: str, current: int, limit: int, edition: Edition):
        super().__init__(
            status_code=_http_status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "license_limit",
                "resource": kind, "current": current, "limit": limit,
                "edition": edition.value,
                "message": (f"{kind}: {current}/{limit} on the {edition.value} "
                            f"edition. Upgrade to raise this limit."),
            },
        )


# kind -> (model attr path). Imported lazily to avoid circulars.
async def enforce_resource_cap(session, kind: str) -> None:
    """Raise LicenseLimitError if creating one more `kind` would exceed the
    licensed cap. 0 = unlimited. Counts are org-filtered automatically by the
    ORM hook (a no-op while single-tenant)."""
    limit = await resource_limit(kind)
    if limit == 0:
        return
    from app.models import Deployment, Environment, RunnerPool, User
    model = {"environments": Environment, "runners": RunnerPool,
             "deployments": Deployment, "seats": User}[kind]
    stmt = select(func.count()).select_from(model)
    if kind == "deployments":
        stmt = stmt.where(model.active.is_(True))
    current = await session.scalar(stmt) or 0
    if current >= limit:
        lic = await current_license()
        raise LicenseLimitError(kind, current, limit, lic.edition)
```

- [ ] **Step 4: Wire `environments.py`**

In `create_environment`, immediately after `await validate_pool_assignment(...)` (line ~133) and before constructing `env = Environment(...)`:

```python
    from app.services.licensing import enforce_resource_cap
    await enforce_resource_cap(session, "environments")
```

- [ ] **Step 5: Wire `runner_pools.py`**

In `create_runner_pool`, as the first statement of the body (before `pool = RunnerPool(...)`):

```python
    from app.services.licensing import enforce_resource_cap
    await enforce_resource_cap(session, "runners")
```

- [ ] **Step 6: Wire `deployments.py` (create + activate)**

In `create_deployment`, inside the `if body.active:` block (after the unsafe-node checks at line ~203):

```python
        from app.services.licensing import enforce_resource_cap
        await enforce_resource_cap(session, "deployments")
```

In `update_deployment`, right after `becoming_active = bool(body.active) and not deployment.active` (line ~277):

```python
    if becoming_active:
        from app.services.licensing import enforce_resource_cap
        await enforce_resource_cap(session, "deployments")
```

- [ ] **Step 7: Wire `auth.py` (seats)**

In `create_user`, after the duplicate-email check (`existing is not None`) and before `user = User(...)` (line ~377):

```python
    from app.services.licensing import enforce_resource_cap
    await enforce_resource_cap(session, "seats")
```

- [ ] **Step 8: Run cap tests + the four affected router suites**

Run:
```bash
cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_license_caps.py \
  tests/test_environments.py tests/test_deployments.py tests/test_runner_pools.py -q
```
Expected: PASS. (If a pre-existing test now hits a cap because it creates many envs/users under Community, raise the limit for that test by setting a Pro/Enterprise key via monkeypatch, OR split it — do NOT weaken the cap default.)

- [ ] **Step 9: Commit**

```bash
git add apps/api/app/services/licensing.py apps/api/app/routers/environments.py \
        apps/api/app/routers/runner_pools.py apps/api/app/routers/deployments.py \
        apps/api/app/routers/auth.py apps/api/tests/test_license_caps.py
git commit -m "feat(licensing): enforce Community resource caps at create paths"
```

---

## Task 5: Capability gates + lifespan reconciliation

**Files:**
- Modify: `apps/api/app/services/licensing.py` (add `require_feature`)
- Modify: `apps/api/app/routers/orgs.py:55` (MT gate)
- Modify: `apps/api/app/main.py` (lifespan: reconcile sandbox/OTel/MT vs license)
- Modify: `apps/api/tests/test_licensing.py` (gate + reconciliation tests)

- [ ] **Step 1: Add `require_feature` dependency**

Append to `apps/api/app/services/licensing.py`:

```python
def require_feature(feature: Feature):
    """FastAPI dependency: 402 unless the active license grants `feature`."""
    async def _dep() -> None:
        if not await has_feature(feature):
            lic = await current_license()
            raise HTTPException(
                status_code=_http_status.HTTP_402_PAYMENT_REQUIRED,
                detail={"error": "feature_locked", "feature": feature.value,
                        "edition": lic.edition.value,
                        "message": f"'{feature.value}' requires a higher edition "
                                   f"(current: {lic.edition.value})."},
            )
    return _dep
```

- [ ] **Step 2: Write failing gate + reconciliation tests**

Append to `apps/api/tests/test_licensing.py`:

```python
@pytest.mark.asyncio
async def test_require_feature_blocks_without_entitlement(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(settings, "license_key", "")
    licensing.invalidate_license_cache()
    dep = licensing.require_feature(Feature.MULTI_TENANCY)
    with pytest.raises(HTTPException) as exc:
        await dep()
    assert exc.value.status_code == 402


@pytest.mark.asyncio
async def test_require_feature_passes_with_enterprise(monkeypatch):
    monkeypatch.setattr(settings, "license_key", make_key({"tier": "enterprise"}))
    licensing.invalidate_license_cache()
    dep = licensing.require_feature(Feature.MULTI_TENANCY)
    assert await dep() is None


@pytest.mark.asyncio
async def test_reconcile_disables_unlicensed_capabilities(monkeypatch):
    from app.services.licensing import reconcile_capabilities
    monkeypatch.setattr(settings, "license_key", "")
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    monkeypatch.setattr(settings, "execution_sandbox", "required")
    monkeypatch.setattr(settings, "otel_enabled", True)
    licensing.invalidate_license_cache()
    warnings = await reconcile_capabilities()
    assert settings.multi_tenancy_enabled is False
    assert settings.execution_sandbox == "off"
    assert settings.otel_enabled is False
    assert len(warnings) == 3
```

- [ ] **Step 3: Run — expect failure**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_licensing.py -k "require_feature or reconcile" -q`
Expected: FAIL (`reconcile_capabilities` undefined).

- [ ] **Step 4: Implement `reconcile_capabilities`**

Append to `apps/api/app/services/licensing.py`:

```python
async def reconcile_capabilities() -> list[str]:
    """At startup, force config-requested capabilities the license does NOT
    grant back to their disabled state and return human-readable warnings.
    Never raises — self-hosted operators must still boot."""
    warnings: list[str] = []
    if boot_settings.multi_tenancy_enabled and not await has_feature(Feature.MULTI_TENANCY):
        boot_settings.multi_tenancy_enabled = False
        warnings.append("multi_tenancy_enabled requires the Enterprise edition; "
                        "disabled (running single-tenant).")
    if boot_settings.execution_sandbox != "off" and not await has_feature(Feature.SANDBOX):
        boot_settings.execution_sandbox = "off"
        warnings.append("execution_sandbox requires the Pro edition or higher; "
                        "disabled (running the subprocess pool).")
    if boot_settings.otel_enabled and not await has_feature(Feature.OBSERVABILITY):
        boot_settings.otel_enabled = False
        warnings.append("otel_enabled requires the Pro edition or higher; disabled.")
    return warnings
```

- [ ] **Step 5: Call it in the lifespan**

In `apps/api/app/main.py`, inside the lifespan startup (before sandbox/OTel init and before MT-dependent setup — locate the existing `init_sandbox()` / OTel init calls and place this just above them):

```python
    from app.services.licensing import reconcile_capabilities
    for _w in await reconcile_capabilities():
        logger.warning("licensing: %s", _w)
```
(Use the module's existing logger name; if main.py uses `logging.getLogger(__name__)` as `logger`, reuse it.)

- [ ] **Step 6: Gate the MT enable path in `orgs.py`**

In `apps/api/app/routers/orgs.py`, the guard at line ~55 currently rejects when `multi_tenancy_enabled` is off. Since `reconcile_capabilities` already forces MT off without an Enterprise license, no per-route change is strictly required — but add belt-and-suspenders to `create_org` by adding the dependency to the router/route decorator:

```python
from app.services.licensing import require_feature, Feature
# on the create_org route decorator:
#   dependencies=[Depends(require_feature(Feature.MULTI_TENANCY))]
```
(Apply to the org create/management routes that mutate tenancy. Read the file's existing `dependencies=[...]` usage and match style.)

- [ ] **Step 7: Run the licensing suite**

Run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_licensing.py -q`
Expected: PASS (all, including the two gate tests + reconciliation).

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/services/licensing.py apps/api/app/main.py apps/api/app/routers/orgs.py apps/api/tests/test_licensing.py
git commit -m "feat(licensing): capability gates + startup reconciliation"
```

---

## Task 6: License management API + `/auth/required` surface

**Files:**
- Modify: `apps/api/app/schemas.py:612` (`AuthRequiredResponse`) + add `LicenseInfo`, `LicenseApply`
- Modify: `apps/api/app/routers/auth.py:333` (`auth_required` populates new fields)
- Add license routes: confirm the right router (the admin `system_settings` routes — grep `system_settings` / `ensure_singleton_row` usage; likely `app/routers/system.py` or `ops.py`). Add `GET/POST/DELETE /license`.
- Modify: `apps/api/tests/test_licensing.py` (endpoint tests)

- [ ] **Step 1: Extend `AuthRequiredResponse` + add license schemas**

In `apps/api/app/schemas.py`, extend `AuthRequiredResponse`:

```python
class AuthRequiredResponse(BaseModel):
    auth_required: bool
    signed_in: bool
    registration_open: bool
    multi_tenancy: bool = False
    edition: str = "community"
    entitlements: list[str] = Field(default_factory=list)
    limits: dict[str, int] = Field(default_factory=dict)
    license_notice: str | None = None
    user: UserInfo | None = None
```

Add near it:

```python
class LicenseInfo(BaseModel):
    edition: str
    customer: str | None = None
    expires_at: int | None = None
    entitlements: list[str]
    limits: dict[str, int]
    notice: str | None = None


class LicenseApply(BaseModel):
    license_key: str = Field(min_length=1)
```

- [ ] **Step 2: Populate the fields in `auth_required`**

In `apps/api/app/routers/auth.py`, replace the `AuthRequiredResponse(...)` return in `auth_required` with one that includes license data:

```python
    from app.services.licensing import current_license
    lic = await current_license()
    return AuthRequiredResponse(
        auth_required=settings.auth_required,
        signed_in=user is not None,
        registration_open=count == 0 or settings.auth_allow_registration,
        multi_tenancy=settings.multi_tenancy_enabled,
        edition=lic.edition.value,
        entitlements=[f.value for f in lic.features],
        limits={"environments": lic.limits.environments, "runners": lic.limits.runners,
                "deployments": lic.limits.deployments, "seats": lic.limits.seats},
        license_notice=lic.notice,
        user=UserInfo.model_validate(user) if user is not None else None,
    )
```

- [ ] **Step 3: Add the license management endpoints**

In the confirmed admin router (owner/admin-protected, where `system_settings` is edited), add:

```python
from app.schemas import LicenseApply, LicenseInfo
from app.services import licensing
from app.services.live_settings import ensure_singleton_row
from app.db import SessionLocal
from app.models import SystemSetting


def _license_info(lic) -> LicenseInfo:
    return LicenseInfo(
        edition=lic.edition.value, customer=lic.customer, expires_at=lic.expires_at,
        entitlements=[f.value for f in lic.features],
        limits={"environments": lic.limits.environments, "runners": lic.limits.runners,
                "deployments": lic.limits.deployments, "seats": lic.limits.seats},
        notice=lic.notice,
    )


@router.get("/license", response_model=LicenseInfo)  # add owner/admin dependency
async def get_license():
    return _license_info(await licensing.current_license())


@router.post("/license", response_model=LicenseInfo)  # owner-only dependency
async def apply_license(body: LicenseApply):
    await ensure_singleton_row()
    async with SessionLocal() as session:
        row = await session.get(SystemSetting, "singleton")
        row.license_key = body.license_key
        await session.commit()
    licensing.invalidate_license_cache()
    lic = await licensing.current_license()
    if not lic.valid or (lic.edition is licensing.Edition.COMMUNITY and lic.notice):
        # Key rejected — surface why, but it is persisted; UI shows the notice.
        pass
    return _license_info(lic)


@router.delete("/license", response_model=LicenseInfo)  # owner-only dependency
async def remove_license():
    async with SessionLocal() as session:
        row = await session.get(SystemSetting, "singleton")
        if row is not None:
            row.license_key = None
            await session.commit()
    licensing.invalidate_license_cache()
    return _license_info(await licensing.current_license())
```
Match the file's existing auth dependency (owner/admin) and `router` prefix. Mirror the protection used by the existing `system_settings` update route.

- [ ] **Step 4: Endpoint tests**

Append to `apps/api/tests/test_licensing.py` (use the repo's `client`/owner-`auth_headers` fixtures):

```python
@pytest.mark.asyncio
async def test_apply_and_remove_license_endpoint(client, owner_headers, monkeypatch):
    monkeypatch.setattr(settings, "license_public_key", TEST_PUBLIC_KEY_PEM)
    monkeypatch.setattr(settings, "license_key", "")
    r = await client.post("/license", json={"license_key": pro_key()},
                          headers=owner_headers)
    assert r.status_code == 200 and r.json()["edition"] == "pro"
    r2 = await client.delete("/license", headers=owner_headers)
    assert r2.json()["edition"] == "community"


@pytest.mark.asyncio
async def test_auth_required_exposes_edition(client):
    r = await client.get("/auth/required")
    body = r.json()
    assert body["edition"] == "community"
    assert "environments" in body["limits"]
```

- [ ] **Step 5: Run + full API suite sanity**

Run:
```bash
cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_licensing.py tests/test_auth.py -q
.venv/Scripts/python.exe -m pytest -q   # full suite; expect only the known pre-existing failures
```
Expected: licensing/auth PASS; full suite green except the documented pre-existing failures (webhook-session FK test, queue fairness flooding test).

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/schemas.py apps/api/app/routers/auth.py <license-router-file> apps/api/tests/test_licensing.py
git commit -m "feat(licensing): license management API + /auth/required entitlements"
```

---

## Task 7: Frontend — entitlements hook, gated affordances, License page

**Files:**
- Modify: the SPA auth/bootstrap store where `/auth/required` is consumed (grep `auth/required` / `multi_tenancy` in `apps/web/src`) — add `edition`, `entitlements`, `limits`, `license_notice` to `AuthState`.
- Create: `apps/web/src/hooks/useEntitlements.ts`
- Modify: the four create buttons (Environments, Runner Pools, Deployments, Users/Security) to disable + show upgrade tooltip when `atLimit`.
- Create: `apps/web/src/pages/LicensePage.tsx` (or a License section in the existing Security/Settings page) + route + nav entry.
- Modify: app-level banner to show `license_notice` when present.
- Test: `apps/web/src/hooks/useEntitlements.test.ts` (Vitest).

- [ ] **Step 1: Extend the auth store type + parsing**

Find where `AuthRequiredResponse` is fetched (grep `auth/required` in `apps/web/src`). Add to the `AuthState` (mirroring the existing `multi_tenancy` field):

```typescript
edition: string;            // 'community' | 'pro' | 'enterprise'
entitlements: string[];
limits: Record<string, number>;   // 0 = unlimited
licenseNotice: string | null;
```
Populate them from the response (default `edition: 'community'`, `entitlements: []`, `limits: {}`, `licenseNotice: null`).

- [ ] **Step 2: Failing Vitest for the hook**

Create `apps/web/src/hooks/useEntitlements.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { computeEntitlements } from "./useEntitlements";

describe("entitlements", () => {
  it("atLimit true when count reaches a finite cap", () => {
    const e = computeEntitlements({ edition: "community", entitlements: [],
      limits: { environments: 3 }, licenseNotice: null });
    expect(e.atLimit("environments", 3)).toBe(true);
    expect(e.atLimit("environments", 2)).toBe(false);
  });
  it("atLimit false when cap is 0 (unlimited)", () => {
    const e = computeEntitlements({ edition: "enterprise", entitlements: [],
      limits: { environments: 0 }, licenseNotice: null });
    expect(e.atLimit("environments", 999)).toBe(false);
  });
  it("has() reflects entitlements", () => {
    const e = computeEntitlements({ edition: "pro", entitlements: ["sandbox"],
      limits: {}, licenseNotice: null });
    expect(e.has("sandbox")).toBe(true);
    expect(e.has("sso")).toBe(false);
  });
});
```

- [ ] **Step 3: Run — expect failure**

Run: `cd apps/web && npx vitest run src/hooks/useEntitlements.test.ts`
Expected: FAIL (module missing).

- [ ] **Step 4: Implement the hook**

Create `apps/web/src/hooks/useEntitlements.ts`:

```typescript
import { useAuthStore } from "../store"; // match the actual store import path

export interface EntitlementState {
  edition: string;
  entitlements: string[];
  limits: Record<string, number>;
  licenseNotice: string | null;
}

export function computeEntitlements(s: EntitlementState) {
  return {
    edition: s.edition,
    has: (feature: string) => s.entitlements.includes(feature),
    atLimit: (kind: string, count: number) => {
      const cap = s.limits[kind] ?? 0;
      return cap !== 0 && count >= cap;
    },
    limit: (kind: string) => s.limits[kind] ?? 0,
    notice: s.licenseNotice,
  };
}

export function useEntitlements() {
  const edition = useAuthStore((st) => st.edition);
  const entitlements = useAuthStore((st) => st.entitlements);
  const limits = useAuthStore((st) => st.limits);
  const licenseNotice = useAuthStore((st) => st.licenseNotice);
  return computeEntitlements({ edition, entitlements, limits, licenseNotice });
}
```
(Adjust the store selector style to the repo's Zustand usage.)

- [ ] **Step 5: Run — expect pass**

Run: `cd apps/web && npx vitest run src/hooks/useEntitlements.test.ts`
Expected: PASS.

- [ ] **Step 6: Gate the four create buttons**

On the Environments, RunnerPools, Deployments, and Users/Security create controls, disable the button when `useEntitlements().atLimit(kind, currentCount)` and render a tooltip: `"{Kind} limit reached on the {edition} edition — upgrade to add more."` Use the existing list length for `currentCount` (for deployments, count active ones). Also handle the API `402` (`error: license_limit`/`feature_locked`) in the shared error display so a server-side block shows the upgrade message rather than a generic toast.

- [ ] **Step 7: License settings section**

Add a License view (new page or a section in the existing Security/Settings page) showing `edition`, `customer`, `expires_at`, seat/limit usage, a textarea to paste a key (POST `/license`), and a "Remove license" action (DELETE `/license`, behind `useConfirm`). On success, refetch `/auth/required` so the whole UI updates. Show `licenseNotice` as a persistent app banner when set.

- [ ] **Step 8: Typecheck, build, vitest**

Run:
```bash
cd apps/web && npm run typecheck && npm run build && npx vitest run
```
Expected: clean typecheck + build; all Vitest green.

- [ ] **Step 9: Commit**

```bash
git add apps/web/src
git commit -m "feat(licensing): entitlements hook, gated create buttons, License settings"
```

---

## Task 8: Docs

**Files:**
- Modify: `docs/deployment.md`

- [ ] **Step 1: Add an "Editions & licensing" section**

Document: the three editions + the §3 tier matrix; how to apply a key (env `NOODLE_LICENSE_KEY` or Settings → License); that Community is the default with no key; expiry → graceful Community downgrade; and that capability flags (`multi_tenancy_enabled`, `execution_sandbox`, `otel_enabled`) are reconciled against the license at startup (a flag set without the entitlement is disabled with a logged warning). Mention the vendor mints keys with `tools/mint_license.py` and the public key is baked into `app/services/licensing.py`.

- [ ] **Step 2: Commit**

```bash
git add docs/deployment.md
git commit -m "docs: editions & licensing in deployment guide"
```

---

## Final verification

- [ ] Full backend suite: `cd apps/api && .venv/Scripts/python.exe -m pytest -q` — green except documented pre-existing failures.
- [ ] Core/nodes unaffected: `cd packages/core && python -m pytest -q` and `cd packages/nodes && python -m pytest -q` (run sequentially — never two package suites concurrently on Windows; spawn deadlocks).
- [ ] Frontend: `cd apps/web && npm run typecheck && npm run build && npx vitest run`.
- [ ] Manual: with no key → `/auth/required` reports `community` + caps bite at 3/1/3/2; apply a Pro test key → caps lift to 10/5/∞/10 and sandbox unlocks; remove → back to Community.

## Self-review notes (addressed)
- **Spec coverage:** hybrid model (Task 1 env+DB sources; per-org tier seam left as `organizations.tier` future column — explicitly deferred in spec §5, no task, intentional). Signed key + offline verify (Task 1/2). Tier matrix (Task 1 `TIER_DEFAULTS`). Resource caps (Task 4). Capability gates + reconciliation (Task 5). `/auth/required` surface + management API (Task 6). Frontend (Task 7). Docs (Task 8). Pricing is product-only, no code — correctly absent.
- **Correction vs spec:** spec §4.4 named `/auth/me`; the real bootstrap endpoint is `/auth/required` (`/auth/me` returns bare `UserInfo`). Plan targets `/auth/required`. Spec §5 said "no structural migration"; `system_settings` is columnar, so Task 3 adds the `license_key` column via migration `0051`.
- **Async correction:** resolver is async (DB-backed key) — `has_feature`/`resource_limit`/`require_feature` are async, matching `live_settings`/`org_limits`.
- **Type consistency:** `Edition`/`Feature`/`ResourceLimits`/`License`, `enforce_resource_cap(session, kind)`, `require_feature(feature)`, `reconcile_capabilities()`, `current_license()` used consistently across tasks.
