# Multi-Tenancy, Enterprise Features & Per-Workflow Git Sync — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> This is the **master implementation plan** spanning three workstreams. Workstreams 2 and 3 are fully detailed and executable as written. Workstream 1 (tenancy) details Phase A task-by-task; phases B/C/E/F are specified to file/schema/endpoint level and should each be expanded into their own plan file before execution (they depend on Phase A's landed shape).

**Goal:** Turn Noodle into a sellable open-core platform: license-gated enterprise features, org-based multi-tenancy (soft tenancy per the decided scope in `docs/multi-tenancy-plan.md`), and git-backed workflow sync.

**Architecture:** Three workstreams ordered by risk/reward: (1) the licensing/feature-gate substrate (small, no schema churn, unblocks revenue and gates everything else), (2) per-workflow git sync (bounded feature, Pro-gated, builds on the existing `WorkflowVersion` snapshot model), (3) the multi-tenancy A–C+E+F bundle (the multi-month re-architecture, per the decided scope in `docs/multi-tenancy-plan.md`).

**Tech Stack:** FastAPI + SQLAlchemy 2 async + Alembic (next revision: `0040`), Postgres RLS, `cryptography` (Ed25519 + existing Fernet envelope), `git` CLI via `asyncio.subprocess`, React/Vite frontend.

**Source designs:** `docs/multi-tenancy-plan.md` (tenancy decisions — all locked decisions there are binding here), `docs/licensing-plan.md` (tier table, token format).

---

## Current-state facts this plan is grounded in

- **No tenancy dimension.** All ~19 tenant-owned tables in `apps/api/app/models.py` are flat/global. `User.role` is a single global RBAC role; `apps/api/app/security.py` maps `Bearer → User → role` via `_PERMISSION_MIN_ROLE`.
- **Sessions** come from `apps/api/app/db.py:get_session` (`async_sessionmaker`, `expire_on_commit=False`). No request-scoped context object exists.
- **Tokens** are HMAC-signed JSON (`services/crypto.py:create_token`, payload `{"sub", "exp", "typ": "session"}`). Adding claims is backward-compatible.
- **Secrets** already use envelope encryption: per-credential DEK wrapped by a master KEK derived from `SECRET_KEY` (`crypto.py`). Per-org KEK slots in as one more wrap layer.
- **Workflow storage:** `Workflow.draft_graph` (JSON) + immutable `WorkflowVersion` rows; `POST /workflows/{id}/publish` snapshots draft → version. This is the exact seam git sync hooks.
- **Export seam exists:** `routers/export.py` + `packages/exporter/noodle_exporter` already serialize a graph out of the system.
- **Latest migration:** `0039_fk_and_index_alignment.py`. Revision IDs must stay ≤32 chars (known Postgres footgun).
- **Settings:** `app/config.py` Pydantic settings + `SystemSetting(id="singleton")` DB row + `services/live_settings.py` hot-reload.

---

# Workstream 1 — Licensing & feature gates (do first)

Why first: tiny blast radius, no migrations beyond one table, and both git sync (`git_versioning` feature) and tenancy-era enterprise features (SSO, external KMS) need a gate to hang off. Tier table and token format are already decided in `docs/licensing-plan.md` §2–3.

### Task L1: License verification service

**Files:**
- Create: `apps/api/app/services/licensing.py`
- Test: `apps/api/tests/test_licensing.py`

- [ ] **Step 1: Write failing tests for parse/verify/expiry/fallback**

```python
# apps/api/tests/test_licensing.py
import base64
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services import licensing


@pytest.fixture()
def keypair(monkeypatch):
    priv = Ed25519PrivateKey.generate()
    pub_raw = priv.public_key().public_bytes_raw()
    monkeypatch.setattr(licensing, "_PUBLIC_KEY_B64", base64.b64encode(pub_raw).decode())
    licensing.invalidate_cache()
    return priv


def mint(priv, **overrides) -> str:
    payload = {
        "v": 1,
        "tier": "pro",
        "licensee": "Acme Corp",
        "issued_at": "2026-06-10T00:00:00Z",
        "expires_at": None,
        "limits": {},
        "features": [],
        "license_id": "lic_test",
        **overrides,
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    sig = base64.urlsafe_b64encode(priv.sign(body)).rstrip(b"=")
    return f"NOODLE-LICENSE-v1.{body.decode()}.{sig.decode()}"


def test_valid_license_resolves_tier(keypair):
    lic = licensing.verify_license(mint(keypair))
    assert lic.tier == "pro"
    assert lic.status == "valid"
    assert lic.limits["max_active_workflows"] == 50


def test_tampered_payload_rejected(keypair):
    token = mint(keypair)
    head, body, sig = token.split(".")
    evil = base64.urlsafe_b64encode(b'{"tier":"enterprise"}').rstrip(b"=").decode()
    assert licensing.verify_license(f"{head}.{evil}.{sig}").status == "invalid"


def test_expired_license_falls_back_to_community(keypair):
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 86400))
    lic = licensing.verify_license(mint(keypair, expires_at=past))
    assert lic.status == "expired"
    assert lic.limits == licensing.TIER_LIMITS["community"]
    assert lic.licensee == "Acme Corp"  # soft-fail keeps showing who it was


def test_per_deal_limit_overrides(keypair):
    lic = licensing.verify_license(mint(keypair, limits={"max_active_workflows": 200}))
    assert lic.limits["max_active_workflows"] == 200


def test_no_license_is_community():
    lic = licensing.verify_license(None)
    assert lic.tier == "community"
    assert lic.limits["max_active_workflows"] == 5
```

- [ ] **Step 2: Run, verify fail** — `uv run pytest apps/api/tests/test_licensing.py -v` → FAIL (module missing)

- [ ] **Step 3: Implement `services/licensing.py`**

```python
"""Offline license verification (open-core feature gating).

A license is `NOODLE-LICENSE-v1.<b64url(payload)>.<b64url(ed25519 sig)>`.
Only the *public* key ships in the product; verification is offline.
See docs/licensing-plan.md for the payload contract.
"""

import base64
import json
import time
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Vendor public key (base64 raw 32 bytes). Generated once by scripts/issue_license.py.
_PUBLIC_KEY_B64 = "REPLACE_WITH_REAL_PUBLIC_KEY_AT_KEYGEN_TIME"

_PREFIX = "NOODLE-LICENSE-v1"

# 0 = unlimited (matches run_retention_days convention).
TIER_LIMITS: dict[str, dict[str, int]] = {
    "community": {
        "max_active_workflows": 5, "max_total_workflows": 25,
        "max_active_deployments": 2, "max_environments": 2,
        "max_runner_pools": 0, "max_users": 2,
        "max_concurrent_runs": 4, "min_schedule_interval_seconds": 900,
        "max_credentials": 10, "max_code_modules": 10,
        "max_run_retention_days": 7,
    },
    "pro": {
        "max_active_workflows": 50, "max_total_workflows": 0,
        "max_active_deployments": 25, "max_environments": 10,
        "max_runner_pools": 3, "max_users": 15,
        "max_concurrent_runs": 25, "min_schedule_interval_seconds": 60,
        "max_credentials": 0, "max_code_modules": 0,
        "max_run_retention_days": 90,
    },
    "enterprise": {
        "max_active_workflows": 0, "max_total_workflows": 0,
        "max_active_deployments": 0, "max_environments": 0,
        "max_runner_pools": 0, "max_users": 0,
        "max_concurrent_runs": 0, "min_schedule_interval_seconds": 60,
        "max_credentials": 0, "max_code_modules": 0,
        "max_run_retention_days": 0,
    },
}

TIER_FEATURES: dict[str, frozenset[str]] = {
    "community": frozenset(),
    "pro": frozenset({"audit_export", "git_versioning", "rbac_granular"}),
    "enterprise": frozenset({
        "audit_export", "git_versioning", "rbac_granular",
        "sso", "scim", "external_secrets",
    }),
}


@dataclass(frozen=True)
class License:
    tier: str = "community"
    status: str = "unlicensed"  # unlicensed | valid | expired | invalid
    licensee: str = ""
    license_id: str = ""
    expires_at: str | None = None
    limits: dict[str, int] = field(default_factory=lambda: dict(TIER_LIMITS["community"]))
    features: frozenset[str] = frozenset()


_cache: tuple[str | None, License] | None = None


def invalidate_cache() -> None:
    global _cache
    _cache = None


def _b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def verify_license(token: str | None) -> License:
    global _cache
    if _cache is not None and _cache[0] == token:
        return _cache[1]
    lic = _verify(token)
    _cache = (token, lic)
    return lic


def _verify(token: str | None) -> License:
    if not token:
        return License()
    invalid = License(status="invalid")
    parts = token.strip().split(".")
    if len(parts) != 3 or parts[0] != _PREFIX:
        return invalid
    body, sig = parts[1], parts[2]
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(_PUBLIC_KEY_B64))
        pub.verify(_b64url_decode(sig), body.encode())
        payload = json.loads(_b64url_decode(body))
    except (InvalidSignature, ValueError, TypeError):
        return invalid
    tier = payload.get("tier", "community")
    if tier not in TIER_LIMITS:
        return invalid
    expires_at = payload.get("expires_at")
    expired = bool(expires_at) and _parse_ts(expires_at) < time.time()
    effective_tier = "community" if expired else tier
    limits = dict(TIER_LIMITS[effective_tier])
    if not expired:
        for key, value in (payload.get("limits") or {}).items():
            if key in limits and isinstance(value, int):
                limits[key] = value
    features = frozenset() if expired else (
        TIER_FEATURES[tier] | frozenset(payload.get("features") or [])
    )
    return License(
        tier=tier,
        status="expired" if expired else "valid",
        licensee=payload.get("licensee", ""),
        license_id=payload.get("license_id", ""),
        expires_at=expires_at,
        limits=limits,
        features=features,
    )


def _parse_ts(value: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
```

- [ ] **Step 4: Run, verify pass** — `uv run pytest apps/api/tests/test_licensing.py -v` → PASS
- [ ] **Step 5: Commit** — `git commit -m "feat(license): offline Ed25519 license verification service"`

### Task L2: License storage + admin endpoints

**Files:**
- Modify: `apps/api/app/models.py` (add `license_key: Mapped[str]` Text column to `SystemSetting`, default `""`)
- Create: `apps/api/alembic/versions/0040_license_key.py` (add column, server_default `''`)
- Modify: `apps/api/app/routers/system_settings.py` — add `GET /system/license` (returns `License` dataclass fields, never the raw token) and `PUT /system/license` (`{"license_key": "..."}`, verify before save, reject `status == "invalid"`, call `licensing.invalidate_cache()`), both behind `require_permission("user:manage")`
- Modify: `apps/api/app/schemas.py` — `LicenseInfo` / `LicenseUpdate` Pydantic models
- Test: `apps/api/tests/test_license_endpoints.py` (PUT invalid → 422; PUT valid → GET shows tier/licensee; viewer role → 403)

- [ ] Steps: failing test → migration → endpoints → pass → commit `feat(license): store + admin endpoints for license key`

### Task L3: Limit enforcement at create/activate time

**Files:**
- Create: `apps/api/app/services/limits.py`
- Modify (one guard call each, at the top of the create/activate handler):
  - `routers/workflows.py:create_workflow` + `update_workflow` (activating: `body.active is True`) → `max_total_workflows` / `max_active_workflows`
  - `routers/deployments.py` create + activate → `max_active_deployments`
  - `routers/environments.py` create → `max_environments`
  - `routers/runner_pools.py` create → `max_runner_pools`
  - `routers/auth.py` invite/create user → `max_users`
  - `routers/credentials.py` create → `max_credentials`
  - `routers/code_modules.py` create → `max_code_modules`
- Test: `apps/api/tests/test_limits.py`

- [ ] **Core helper (complete):**

```python
# apps/api/app/services/limits.py
"""COUNT(*)-at-create-time license limit checks. Soft-fail: existing
resources are never touched; only NEW creation/activation is blocked."""

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.licensing import verify_license
from app.services.live_settings import get_system_settings  # existing accessor


async def current_license(session: AsyncSession):
    row = await get_system_settings(session)
    return verify_license(getattr(row, "license_key", "") or None)


async def enforce_limit(
    session: AsyncSession, limit_key: str, count_stmt, resource_label: str
) -> None:
    lic = await current_license(session)
    ceiling = lic.limits.get(limit_key, 0)
    if ceiling == 0:  # 0 = unlimited
        return
    count = (await session.execute(select(func.count()).select_from(count_stmt))).scalar() or 0
    if count >= ceiling:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "message": f"{resource_label} limit reached for the "
                           f"{lic.tier} tier ({count}/{ceiling}).",
                "limit": limit_key,
                "tier": lic.tier,
                "upgrade": True,
            },
        )
```

Example call site (workflows): `await enforce_limit(session, "max_active_workflows", select(Workflow).where(Workflow.active.is_(True)).subquery(), "Active workflows")` before flipping `active=True`.

- [ ] Steps per call site: failing test (create N at ceiling → 402, ceiling 0 → unlimited) → wire guard → pass → commit `feat(license): enforce <resource> limit`

### Task L4: Feature-flag dependency + vendor CLI + UI

**Files:**
- Modify: `apps/api/app/services/limits.py` — add:

```python
def require_feature(feature: str):
    async def dependency(session: AsyncSession = Depends(get_session)) -> None:
        lic = await current_license(session)
        if feature not in lic.features:
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED,
                detail={"message": f"'{feature}' requires a Pro or Enterprise license.",
                        "feature": feature, "tier": lic.tier, "upgrade": True},
            )
    return dependency
```

- Create: `scripts/issue_license.py` — vendor-only CLI: `keygen` (writes `.secrets/license_signing.key`, prints public b64 to paste into `licensing.py`), `issue --tier pro --licensee "Acme" --expires 2027-06-10 [--limit k=v ...] [--feature f ...]`. Uses `Ed25519PrivateKey`; mirrors the mint helper from Task L1's test.
- Modify: `apps/web/src/SettingsPage.tsx` — "License" card: current tier/licensee/expiry badge, paste-key input → `PUT /system/license`; surface 402 responses app-wide in `apps/web/src/api.ts` error handling as an "Upgrade" toast.
- [ ] Commit: `feat(license): feature gates, issuing CLI, settings UI`

### Task L5: Audit log export (`audit_export`, Pro+)

**Files:**
- Modify: `apps/api/app/routers/audit.py` — add `GET /audit/export?format=csv|json&from=&to=` streaming all `AuditEvent` rows in range, behind `Depends(require_feature("audit_export"))` + `require_permission("audit:read")`
- Modify: `apps/web/src/ActivityPage.tsx` — Export button (disabled with upgrade tooltip when unlicensed)
- Test: `apps/api/tests/test_audit_export.py` (CSV header + row count; 402 without license)

- [ ] Steps: failing test → streaming endpoint (`csv.writer` over an async generator) → pass → commit `feat(license): audit log export endpoint`

**Workstream 1 acceptance:** unlicensed instance behaves exactly as today until a ceiling is hit; pasting a Pro key raises ceilings live (no restart — license read goes through `live_settings`); all existing tests green.

---

# Workstream 2 — Per-workflow Git sync (`git_versioning`, Pro+)

## Design (decided here)

**Model — repo-per-instance, file-per-workflow, n8n-style push/pull.** One configured git connection (per org once tenancy lands; columns carry `org_id` from day one, nullable until Phase A backfills). Every linked workflow serializes to a deterministic JSON file; push commits selected workflows, pull imports changes as **drafts** (never overwrites published versions silently). Promotion flow: dev instance pushes → prod instance pulls.

**Repo layout:**

```
<base_path>/
  workflows/<slug>--<workflow_id>.json     # slug for humans, id for identity
  modules/<scope>/<name>.py                # workflow-scoped CodeModules ride along
  .noodle/manifest.json                    # format_version, exported_at map
```

**Canonical file format** (`format_version: 1`): sorted-key JSON, LF, trailing newline. Contains `id, name, graph (draft of the *published* version being pushed), environment_name, settings {allow_concurrent, run_timeout_seconds, error_alerts}, credentials: [{ref_key, name, type}]`. **Credential ids are mapped to `{name, type}` references on export and re-resolved by name+type on import** — secrets never leave the instance. Volatile fields (timestamps, run state, `published_version`, internal queue state) are excluded so diffs are meaningful.

**Git transport:** `git` CLI via `asyncio.create_subprocess_exec` (no shell), one clone per connection under `settings.git_sync_dir` (`./data/git-sync/<connection_id>`). Auth: HTTPS PAT via one-shot `http.extraHeader` config argument (never in the remote URL, never logged), or SSH deploy key via `GIT_SSH_COMMAND` + key written to a `0600` temp file for the duration of the call. Token/key stored as a normal encrypted `Credential` row (type `git_https_token` / `git_ssh_key`) so it inherits envelope encryption + redaction.

**Conflict policy:** pull computes per-workflow status by comparing repo file hash vs `workflow_git_state.last_synced_content_hash` vs current local serialization: `in_sync | ahead (local changes) | behind (repo changes) | diverged`. `behind` → import file as new draft + new published version (with notes `git: <commit short sha>`); `diverged` → import as draft only and flag for human review in the UI. Push refuses non-fast-forward (pull first). No merge machinery in v1.

### Task G1: Schema — `git_connections` + `workflow_git_state`

**Files:**
- Modify: `apps/api/app/models.py`
- Create: `apps/api/alembic/versions/0041_git_sync.py`
- Test: `apps/api/tests/test_git_models.py` (round-trip insert/select both tables)

- [ ] **Models (complete):**

```python
class GitConnection(Base):
    """A configured git remote for workflow sync. One active per org/instance."""

    __tablename__ = "git_connections"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    repo_url: Mapped[str] = mapped_column(String(500), nullable=False)
    branch: Mapped[str] = mapped_column(String(120), nullable=False, default="main")
    base_path: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    auth_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="https_token")
    credential_id: Mapped[str | None] = mapped_column(
        ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True
    )
    auto_push_on_publish: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    commit_author_name: Mapped[str] = mapped_column(String(160), default="Noodle", nullable=False)
    commit_author_email: Mapped[str] = mapped_column(
        String(200), default="noodle@localhost", nullable=False
    )
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkflowGitState(Base):
    """Per-workflow sync bookkeeping against the org's GitConnection."""

    __tablename__ = "workflow_git_state"

    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), primary_key=True
    )
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("git_connections.id", ondelete="CASCADE"), index=True, nullable=False
    )
    path: Mapped[str] = mapped_column(String(300), nullable=False)
    last_pushed_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="SET NULL"), nullable=True
    )
    last_synced_commit: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    last_synced_content_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] Migration `0041_git_sync` creates both tables. Commit: `feat(git-sync): schema for git connections and per-workflow sync state`

### Task G2: Canonical workflow serializer (the determinism core)

**Files:**
- Create: `apps/api/app/services/git_serialize.py`
- Test: `apps/api/tests/test_git_serialize.py`

- [ ] **Step 1: Failing tests**

```python
# apps/api/tests/test_git_serialize.py
from app.services.git_serialize import workflow_to_doc, doc_to_import, serialize_doc, slug_path


def _graph():
    return {"nodes": [{"id": "n1", "type": "code", "position": {"x": 1, "y": 2},
                       "data": {"params": {"code": "return 1", "credential_id": "cred123"}}}],
            "edges": []}


def test_serialization_is_deterministic_and_credential_free():
    doc = workflow_to_doc(
        workflow_id="wf1", name="My Flow", graph=_graph(),
        environment_name="default",
        settings={"allow_concurrent": True, "run_timeout_seconds": None, "error_alerts": {}},
        credential_names={"cred123": ("Slack Bot", "slack")},
    )
    text1, text2 = serialize_doc(doc), serialize_doc(doc)
    assert text1 == text2
    assert "cred123" not in text1                       # ids never exported
    assert doc["credentials"] == [{"ref_key": "cred:0", "name": "Slack Bot", "type": "slack"}]
    assert doc["graph"]["nodes"][0]["data"]["params"]["credential_id"] == "cred:0"
    assert text1.endswith("\n") and "\r" not in text1


def test_round_trip_restores_credential_ids():
    doc = workflow_to_doc(
        workflow_id="wf1", name="My Flow", graph=_graph(), environment_name=None,
        settings={}, credential_names={"cred123": ("Slack Bot", "slack")},
    )
    imported = doc_to_import(doc, resolve_credential=lambda name, type_: "newcred9")
    assert imported.graph["nodes"][0]["data"]["params"]["credential_id"] == "newcred9"
    assert imported.name == "My Flow"


def test_slug_path_is_stable_and_safe():
    assert slug_path("My Flow!", "abc123") == "workflows/my-flow--abc123.json"
```

- [ ] **Step 2: Run → FAIL.** **Step 3: Implement:**

```python
# apps/api/app/services/git_serialize.py
"""Deterministic, credential-free workflow <-> file serialization (format v1).

Export maps credential ids -> {name, type} refs so secrets and instance-local
ids never enter the repo; import re-resolves by (name, type) on the target.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable

FORMAT_VERSION = 1
_CRED_KEYS = ("credential_id",)  # node param keys that hold credential references


def slug_path(name: str, workflow_id: str) -> str:
    slug = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-") or "workflow"
    return f"workflows/{slug}--{workflow_id}.json"


def _walk_credential_ids(graph: dict, replace: Callable[[str], str]) -> dict:
    graph = json.loads(json.dumps(graph))  # deep copy
    for node in graph.get("nodes", []):
        params = (node.get("data") or {}).get("params") or {}
        for key in _CRED_KEYS:
            if isinstance(params.get(key), str) and params[key]:
                params[key] = replace(params[key])
    return graph


def workflow_to_doc(
    *, workflow_id: str, name: str, graph: dict, environment_name: str | None,
    settings: dict, credential_names: dict[str, tuple[str, str]],
) -> dict:
    refs: dict[str, str] = {}
    credentials: list[dict] = []

    def to_ref(cred_id: str) -> str:
        if cred_id not in refs:
            cred_name, cred_type = credential_names.get(cred_id, (cred_id, "unknown"))
            refs[cred_id] = f"cred:{len(credentials)}"
            credentials.append({"ref_key": refs[cred_id], "name": cred_name, "type": cred_type})
        return refs[cred_id]

    return {
        "format_version": FORMAT_VERSION,
        "id": workflow_id,
        "name": name,
        "environment_name": environment_name,
        "settings": settings,
        "credentials": credentials,
        "graph": _walk_credential_ids(graph, to_ref),
    }


def serialize_doc(doc: dict) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class WorkflowImport:
    workflow_id: str
    name: str
    graph: dict
    environment_name: str | None
    settings: dict


def doc_to_import(
    doc: dict, resolve_credential: Callable[[str, str], str | None]
) -> WorkflowImport:
    if doc.get("format_version") != FORMAT_VERSION:
        raise ValueError(f"Unsupported workflow file format: {doc.get('format_version')}")
    by_ref = {c["ref_key"]: c for c in doc.get("credentials", [])}

    def from_ref(ref: str) -> str:
        cred = by_ref.get(ref)
        if cred is None:
            return ref  # not a ref we minted; leave untouched
        return resolve_credential(cred["name"], cred["type"]) or ""

    return WorkflowImport(
        workflow_id=doc["id"],
        name=doc["name"],
        graph=_walk_credential_ids(doc["graph"], from_ref),
        environment_name=doc.get("environment_name"),
        settings=doc.get("settings") or {},
    )
```

- [ ] **Step 4: Run → PASS.** **Step 5: Commit** `feat(git-sync): deterministic credential-free workflow serializer`

> Note: audit `_CRED_KEYS` against the real node manifests before shipping — any param key that stores a credential id (check `packages/nodes` manifests for `credential` field types) must be listed, and a test should assert the list matches the manifest scan.

### Task G3: Git client (subprocess wrapper)

**Files:**
- Create: `apps/api/app/services/git_client.py`
- Modify: `apps/api/app/config.py` — add `git_sync_dir: str = "./data/git-sync"`, `git_binary: str = "git"`, `git_timeout_seconds: float = 60.0`
- Test: `apps/api/tests/test_git_client.py` (uses a local bare repo fixture — no network)

- [ ] **Test fixture pattern (complete):**

```python
@pytest.fixture()
def bare_remote(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True)
    return str(remote)
```

Tests: `clone_or_open` then `write file → commit → push` lands in remote (`git -C remote.git log`); second client pulls and sees the file; push with stale head raises `GitSyncError("non-fast-forward")`; auth header argv never appears in raised error text.

- [ ] **Implementation core:**

```python
# apps/api/app/services/git_client.py
"""Thin async wrapper around the git CLI. One working clone per connection
under settings.git_sync_dir. All commands run with shell=False; auth material
is passed per-invocation (config arg / env), never persisted to .git/config
and never included in error messages."""

import asyncio
import base64
import os
import stat
import tempfile
from pathlib import Path

from app.config import settings


class GitSyncError(RuntimeError):
    pass


class GitClient:
    def __init__(self, connection_id: str, repo_url: str, branch: str,
                 *, https_token: str | None = None, ssh_key_pem: str | None = None,
                 author_name: str = "Noodle", author_email: str = "noodle@localhost"):
        self.workdir = Path(settings.git_sync_dir) / connection_id
        self.repo_url, self.branch = repo_url, branch
        self._token, self._ssh_key = https_token, ssh_key_pem
        self._author = (author_name, author_email)

    def _auth_args_env(self) -> tuple[list[str], dict[str, str], list[str]]:
        """Returns (extra -c args, env overrides, temp files to clean up)."""
        args: list[str] = []
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        cleanup: list[str] = []
        if self._token:
            basic = base64.b64encode(f"x-access-token:{self._token}".encode()).decode()
            args += ["-c", f"http.extraHeader=Authorization: Basic {basic}"]
        if self._ssh_key:
            fd, key_path = tempfile.mkstemp(prefix="noodle-git-key-")
            with os.fdopen(fd, "w") as fh:
                fh.write(self._ssh_key)
            os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
            env["GIT_SSH_COMMAND"] = (
                f'ssh -i "{key_path}" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new'
            )
            cleanup.append(key_path)
        return args, env, cleanup

    async def _git(self, *argv: str, cwd: Path | None = None) -> str:
        extra, env, cleanup = self._auth_args_env()
        try:
            proc = await asyncio.create_subprocess_exec(
                settings.git_binary, *extra, *argv,
                cwd=str(cwd or self.workdir), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(
                proc.communicate(), timeout=settings.git_timeout_seconds
            )
        finally:
            for path in cleanup:
                try: os.unlink(path)
                except OSError: pass
        if proc.returncode != 0:
            message = err.decode(errors="replace")
            if self._token:
                message = message.replace(self._token, "***")
            if "non-fast-forward" in message or "fetch first" in message:
                raise GitSyncError("non-fast-forward: remote has new commits; pull first")
            raise GitSyncError(f"git {argv[0]} failed: {message[:500]}")
        return out.decode(errors="replace")

    async def clone_or_open(self) -> None:
        if (self.workdir / ".git").exists():
            await self._git("fetch", "origin", self.branch)
            return
        self.workdir.parent.mkdir(parents=True, exist_ok=True)
        await self._git("clone", "--branch", self.branch, "--single-branch",
                        self.repo_url, str(self.workdir), cwd=self.workdir.parent)

    async def checkout_latest(self) -> str:
        await self._git("fetch", "origin", self.branch)
        await self._git("checkout", "-B", self.branch, f"origin/{self.branch}")
        return (await self._git("rev-parse", "HEAD")).strip()

    async def commit_and_push(self, message: str) -> str:
        await self._git("add", "--all")
        status = await self._git("status", "--porcelain")
        if not status.strip():
            return (await self._git("rev-parse", "HEAD")).strip()
        name, email = self._author
        await self._git("-c", f"user.name={name}", "-c", f"user.email={email}",
                        "commit", "-m", message)
        await self._git("push", "origin", self.branch)
        return (await self._git("rev-parse", "HEAD")).strip()
```

- [ ] Tests pass → commit `feat(git-sync): async git CLI client with scoped auth`

### Task G4: Sync service — status / push / pull

**Files:**
- Create: `apps/api/app/services/git_sync.py`
- Test: `apps/api/tests/test_git_sync_service.py` (bare-remote fixture + SQLite session, full push→pull round trip across two "instances")

Responsibilities (each its own TDD step):
- [ ] `compute_status(session, connection) -> list[WorkflowSyncStatus]`: for every workflow (and every repo file without a local workflow), compare `content_hash(serialize(current published graph))` vs `WorkflowGitState.last_synced_content_hash` vs repo file hash → `in_sync | ahead | behind | diverged | new_local | new_remote`.
- [ ] `push(session, connection, workflow_ids, message, actor)`: checkout latest → serialize each workflow (graph = latest **published** `WorkflowVersion`, not the draft — pushing unpublished drafts is a footgun) + its workflow-scoped `CodeModule`s into `modules/` → write files → `commit_and_push` → update `WorkflowGitState` rows → `log_audit("git_push", "workflow", ...)` per workflow.
- [ ] `pull(session, connection, actor)`: checkout latest → for each repo workflow file: `new_remote` → create `Workflow` (preserving the file's `id` if free, else new id + state row mapping); `behind` → set `draft_graph` from file **and** publish a version with notes `git: {short_sha}`; `diverged` → set draft only, return as `needs_review`; resolve credentials by `(name, type)` via `resolve_credential`, collect unresolved as warnings (import proceeds; node param left empty + warning surfaced). Update state rows; audit `git_pull`.
- [ ] Concurrency: per-connection `asyncio.Lock` (module-level dict) so two pushes can't interleave a working tree.
- [ ] Commit `feat(git-sync): push/pull/status sync service`

### Task G5: API router + publish hook + license gate

**Files:**
- Create: `apps/api/app/routers/git_sync.py`; register in `apps/api/app/main.py`
- Modify: `apps/api/app/security.py` — add `"git:read": "editor"`, `"git:write": "editor"`, `"git:configure": "admin"` to `_PERMISSION_MIN_ROLE`
- Modify: `apps/api/app/routers/workflows.py:publish_workflow` — after commit, if a connection has `auto_push_on_publish`, fire-and-forget `git_sync.push` for that workflow (log failures to `GitConnection.last_error`, never fail the publish)
- Test: `apps/api/tests/test_git_router.py`

Endpoints (all `Depends(require_feature("git_versioning"))` from Task L4):
- [ ] `PUT /git/connection` (configure; `git:configure`) / `GET /git/connection` / `DELETE /git/connection`
- [ ] `POST /git/connection/test` — clone/fetch dry-run, returns ok/error (`git:configure`)
- [ ] `GET /git/status` — `compute_status` output (`git:read`)
- [ ] `POST /git/push` — `{workflow_ids: [...], message: str}` (`git:write`)
- [ ] `POST /git/pull` — returns `{imported: [...], updated: [...], needs_review: [...], warnings: [...]}` (`git:write`)
- [ ] Commit `feat(git-sync): REST endpoints, RBAC perms, auto-push-on-publish`

### Task G6: Frontend

**Files:**
- Modify: `apps/web/src/SettingsPage.tsx` — "Git Sync" card (repo URL, branch, auth credential picker, auto-push toggle, Test Connection)
- Modify: `apps/web/src/WorkflowsPage.tsx` — per-row sync badge from `GET /git/status` (`in_sync`/`ahead`/`behind`/`diverged`), bulk "Push…"/"Pull" toolbar actions with themed confirm (`ConfirmProvider`)
- Modify: `apps/web/src/EditorPage.tsx` header — sync chip for the open workflow + "Push to git" action
- Modify: `apps/web/src/api.ts`, `apps/web/src/types.ts` — client methods + types
- [ ] Tests colocated per existing convention (`*.test.tsx`); commit `feat(web): git sync settings, status badges, push/pull UI`

**Workstream 2 acceptance:** two local instances sharing a bare repo can round-trip a workflow (push from A, pull on B, run on B with B's credentials matched by name); repo files contain no secrets and no instance-local credential ids; feature 402s without a Pro license.

---

# Workstream 3 — Multi-tenancy A–C+E+F bundle

All locked decisions from `docs/multi-tenancy-plan.md` §11 apply: RLS **and** session scoping from day one, `X-Org-Id` header routing, mandatory Postgres CI lane, E+F pulled forward while data is single-default-org, compute/iteration metering, per-org `subworkflow_slot`.

## Phase A — Data isolation (detailed)

### Task A1: `Organization` + `Membership` models, default-org migration

**Files:**
- Modify: `apps/api/app/models.py`
- Create: `apps/api/alembic/versions/0042_orgs.py`
- Test: `apps/api/tests/test_tenancy_models.py`

- [ ] **Models (complete):**

```python
class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    # Phase E: this org's KEK, wrapped by the master KEK (or external KMS ref).
    wrapped_org_kek: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id", name="uq_membership_org_user"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] Migration `0042_orgs`: create both tables; insert org `id="default", slug="default", name="Default"`; insert a membership per existing user copying `User.role` → `Membership.role`. **Keep `User.role` until Phase B completes** (security.py still reads it while the flag is off).
- [ ] Commit `feat(tenancy): organizations + memberships with default-org backfill`

### Task A2: Tenant request context + ORM session scoping

**Files:**
- Create: `apps/api/app/tenancy.py`
- Modify: `apps/api/app/config.py` — `multi_tenancy_enabled: bool = False`
- Modify: `apps/api/app/db.py` — wire the `do_orm_execute` listener + per-session GUC
- Test: `apps/api/tests/test_tenancy_scoping.py`

- [ ] **Implementation (complete):**

```python
# apps/api/app/tenancy.py
"""Request-scoped tenant context + the two enforcement layers.

Layer 2 (ORM, all backends): a with_loader_criteria filter auto-appended to
every SELECT against a model that has org_id.
Layer 1 (Postgres RLS) is applied per-session via SET LOCAL in db.py and
enforced by policies from migration 0044 — the DB-level backstop.
"""

from contextvars import ContextVar

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import event, orm
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

current_org_id: ContextVar[str | None] = ContextVar("current_org_id", default=None)

DEFAULT_ORG_ID = "default"


def active_org_id() -> str | None:
    if not settings.multi_tenancy_enabled:
        return None  # flag off: behave exactly as today (no filtering)
    return current_org_id.get() or DEFAULT_ORG_ID


def install_org_filter(session_factory) -> None:
    """Append `org_id = :current` to every ORM SELECT of an org-scoped model."""

    @event.listens_for(session_factory.sync_session_class, "do_orm_execute")
    def _add_filter(execute_state):
        org_id = active_org_id()
        if org_id is None or not execute_state.is_select:
            return
        from app import models  # late import to avoid cycles

        for model in models.ORG_SCOPED_MODELS:
            execute_state.statement = execute_state.statement.options(
                orm.with_loader_criteria(
                    model, lambda cls: cls.org_id == org_id, include_aliases=True
                )
            )


async def resolve_org(
    x_org_id: str | None = Header(default=None),
    user=Depends("app.security:optional_current_user"),  # wired concretely in security.py
) -> str | None:
    """FastAPI dependency: validate membership BEFORE any data access, then
    set the ContextVar both enforcement layers read."""
    if not settings.multi_tenancy_enabled:
        return None
    org_id = x_org_id or DEFAULT_ORG_ID
    # membership check implemented in security.py (needs the session); raises
    # 403 when the authenticated user is not a member of org_id.
    current_org_id.set(org_id)
    return org_id
```

`models.py` gains `ORG_SCOPED_MODELS: list[type[Base]]` (populated in Task A3). `db.py:get_session` adds, when `multi_tenancy_enabled` and dialect is Postgres: `await session.execute(text("SET LOCAL app.current_org = :org"), {"org": active_org_id()})` inside the transaction start, and the engine setup calls `install_org_filter(SessionLocal)`.

- [ ] Tests: flag off → no filtering (existing suite green); flag on + ContextVar set → ORM query returns only that org's rows (SQLite-runnable). Commit `feat(tenancy): request org context + ORM session scoping`

### Task A3: `org_id` columns + batched backfill

**Files:**
- Modify: `apps/api/app/models.py` (add to each listed model + populate `ORG_SCOPED_MODELS`)
- Create: `apps/api/alembic/versions/0043_org_id_cols.py`

- [ ] Add `org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False, server_default="default")` to: `workflows`, `credentials`, `environments`, `deployments`, `code_modules`, `pinned_data`, `runner_pools`, `workflow_versions`, `runs`, `run_batches`, `run_queue`, `provider_trigger_subscriptions`, `schedule_state`, `audit_events`, `git_connections`, `workflow_git_state`. Child tables (`node_runs`, `run_events`, `run_approvals`, `artifacts`, `runners`) inherit tenant via parent FK — **no column** (decided).
- [ ] Backfill batched (`UPDATE ... WHERE org_id IS NULL LIMIT 10000` loop) ordered largest-first: `node_runs` has no column, so the big ones are `runs`, `run_queue`, `workflow_versions`, `audit_events`. Set NOT NULL + FK after backfill. Revision id ≤32 chars.
- [ ] All create paths set `org_id=active_org_id() or "default"` — one helper `tenancy.stamp(obj)` called in every `session.add` site (grep: `session.add(`).
- [ ] Commit `feat(tenancy): org_id on all tenant-owned tables + batched backfill`

### Task A4: Postgres RLS policies

**Files:**
- Create: `apps/api/alembic/versions/0044_rls.py` (dialect-gated: no-op on SQLite)

- [ ] Per org-scoped table:

```sql
ALTER TABLE workflows ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflows FORCE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON workflows
  USING (org_id = current_setting('app.current_org', true))
  WITH CHECK (org_id = current_setting('app.current_org', true));
```

Plus a `noodle_admin` bypass note: migrations/maintenance run as the table owner with `FORCE` — document that ops scripts must `SET app.current_org` or use a dedicated role. Child tables get policies via `EXISTS (SELECT 1 FROM runs r WHERE r.id = run_id AND r.org_id = current_setting(...))`.
- [ ] Commit `feat(tenancy): Postgres RLS policies on all tenant tables`

### Task A5: Postgres CI lane + isolation proof tests

**Files:**
- Modify: `.github/workflows/<ci file>` — add a `postgres:16` service job running `pytest -m postgres`
- Create: `apps/api/tests/test_tenancy_isolation_pg.py` (`@pytest.mark.postgres`)

- [ ] The four mandated tests from the design doc A6: raw-SQL cross-org read/update/delete refused (proves RLS, bypassing the ORM); ORM scoping test (both backends); migration-applies-on-Postgres test; `SET LOCAL` reset-between-requests test (pooled connection can't leak the previous org). Plus flag-off parity: full existing suite with `multi_tenancy_enabled=false`.
- [ ] Commit `test(tenancy): Postgres CI lane proving RLS isolation`

### Task A6: Membership-based authorization

**Files:**
- Modify: `apps/api/app/security.py`
- Test: `apps/api/tests/test_org_rbac.py`

- [ ] `require_permission` resolves the actor's role **within the request org** when the flag is on: `SELECT role FROM memberships WHERE org_id=:org AND user_id=:user` (cached on `request.state`); falls back to `User.role` when the flag is off. `resolve_org`'s membership validation lives here too (it has the session). Org owner replaces instance owner for org-scoped `user:manage`.
- [ ] Commit `feat(tenancy): org-membership RBAC resolution`

## Phase E — Per-org KEK (immediately after A, while everything is org "default")

**Files:** `apps/api/app/services/crypto.py`, `apps/api/alembic/versions/0045_org_kek.py`, `apps/api/tests/test_org_kek.py`

- [ ] Add to crypto: `generate_org_kek() -> bytes`, `wrap_org_kek(kek) -> str` (master Fernet), `encrypt_credential_for_org(data, org_kek) -> (ciphertext, wrapped_dek)` — DEK now wrapped by the **org KEK** instead of the master. `decrypt_credential` gains an `org_kek` parameter with the two legacy fallbacks kept (org-KEK → master-KEK-wrapped-DEK → KEK-direct).
- [ ] Pluggable provider: `class KekProvider(Protocol): async def get_org_kek(org_id) -> bytes` with `EnvMasterKekProvider` default; the Vault/AWS-KMS implementations are the Enterprise `external_secrets` feature (Workstream 1 gate), interface landed now.
- [ ] Migration `0045`: mint a KEK for org `default`, store `wrapped_org_kek`, rewrap every existing `encrypted_dek` under it (a no-op-risk sweep precisely because everything is still one org — the whole reason E is pulled forward).
- [ ] Commit `feat(tenancy): per-org KEK envelope with pluggable provider`

## Phase F — Storage namespacing (with E)

**Files:** `apps/api/app/services/artifacts.py`, `artifact_backends.py`, `s3_artifact_backend.py`, migration `0046_artifact_org_prefix.py`

- [ ] Every new `storage_key` becomes `{org_id}/{existing_key}`; download/signed-URL paths assert the key's org prefix matches `active_org_id()` (404 otherwise — don't leak existence). Migration renames existing local files / S3 objects under `default/` (batched, resumable; local = `os.rename`, S3 = copy+delete with a progress cursor in `system_settings`).
- [ ] Commit `feat(tenancy): org-namespaced artifact storage`

## Phase B — Org UX (memberships, invitations, switching)

Specified to endpoint level; expand into its own plan when A lands:

- Org CRUD (`POST/GET/PATCH /orgs`, owner-only delete with confirm), org-scoped invitations (existing invite flow gains `org_id` + role), `GET /me/orgs`, org switcher in the web header (persist selection, send `X-Org-Id` on every `api.ts` call), session token unchanged (org is per-request, not per-token — decided in A4).
- SSO/OIDC (Enterprise, `require_feature("sso")`): `sso_connections` table (org_id, issuer_url, client_id, encrypted client_secret via org KEK, email_domain, default_role), authlib code flow at `/auth/sso/{org_slug}/login|callback`, JIT membership creation by verified email domain. SCIM 2.0 (`/scim/v2/Users|Groups`, bearer per org) after SSO ships.

## Phase X — Execution isolation ("D-lite", pulled forward)

> **Why this exists (revision 2026-06-10):** the original A–C+E+F scope gives *data*
> isolation only. Workflow code still executes in shared warm host processes, and
> `runtime_pool.py` `_RuntimeProcess.spawn` does `env = dict(os.environ)` — every
> Code node inherits the API's **`SECRET_KEY` (master KEK), `DATABASE_URL`, and all
> OAuth client secrets**. Under multi-tenancy that lets any org's code decrypt every
> org's credentials, making RLS cosmetic against a malicious author. Phase X closes
> the cheap holes now and wires the real boundary through the existing RunnerPool
> docker/k8s seam, leaving only deep sandbox hardening (gVisor, egress policy) in
> the deferred Phase D.
>
> **Honest tier model:**
> - **Tier 0 (today):** shared warm pool, full env inheritance — trusted authors only.
> - **Tier 1 (X1–X3):** process hygiene — secrets out of worker env, per-org warm
>   pools, org-scoped artifact paths. Stops cross-org *leakage through provided
>   channels*. Does **not** stop a determined attacker: a worker running as the same
>   OS user can still read `.env` / SQLite files from disk.
> - **Tier 2 (X4):** org-pinned **container-per-run** runner pools — the real code
>   isolation boundary for untrusted orgs. Built on the existing `RunnerPool`
>   `docker`/`kubernetes` providers + `remote_dispatch`, so this is enforcement and
>   plumbing, not new infrastructure.
> - **Phase D (still deferred):** network egress policy, syscall sandboxing
>   (gVisor/Firecracker), metadata-endpoint blocking, per-run disk quotas.

### Task X1: Minimal env allowlist for worker spawn (do FIRST — standalone hardening, no schema deps)

**Files:**
- Modify: `apps/api/app/services/runtime_pool.py` (`_RuntimeProcess.spawn`, the `env = dict(os.environ)` at ~line 149)
- Test: `apps/api/tests/test_worker_env.py`

- [ ] **Step 1: Failing test**

```python
# apps/api/tests/test_worker_env.py
from app.services.runtime_pool import _worker_env


def test_secrets_never_reach_worker_env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "super-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "oauth-secret")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "tok")
    env = _worker_env()
    for forbidden in ("SECRET_KEY", "DATABASE_URL", "GOOGLE_OAUTH_CLIENT_SECRET",
                      "INTERNAL_API_TOKEN"):
        assert forbidden not in env


def test_required_os_vars_pass_through(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    env = _worker_env()
    assert env["PATH"] == "/usr/bin"
    assert env["NOODLE_CODE_NODE_TIMEOUT_SECONDS"]  # always set explicitly
```

- [ ] **Step 2: Implement.** The runtime worker reads exactly one env var
  (`NOODLE_CODE_NODE_TIMEOUT_SECONDS`, `packages/runtime/noodle_runtime/server.py:78`);
  everything else arrives over stdio. Allowlist (cross-platform): `PATH`, `HOME`,
  `TEMP`, `TMP`, `TMPDIR`, `LANG`, `LC_ALL`, `SYSTEMROOT`, `SYSTEMDRIVE`, `COMSPEC`,
  `WINDIR`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `PROGRAMDATA`,
  `PYTHONIOENCODING`, plus any `NOODLE_*` already set. Build in a new module-level
  `_worker_env() -> dict[str, str]` and use it in `spawn` instead of
  `dict(os.environ)`. Case-insensitive matching on Windows.
- [ ] **Step 3: Full suite green** (workers must still boot: run an existing
  runtime-pool integration test). Watch for venv activation vars — the worker is
  launched by absolute interpreter path, so `VIRTUAL_ENV` is not required.
- [ ] **Step 4: Commit** `fix(security): workers no longer inherit API secrets via environment`

### Task X2: Per-org default environment + (org, env) pool integrity

**Files:** `apps/api/app/services/runtime_pool.py`, `apps/api/app/routers/auth.py` /org-creation path, test `apps/api/tests/test_org_envs.py`

- [ ] When `multi_tenancy_enabled`: org creation provisions an org-owned default
  `Environment` (the `is_global` env remains owned by org `default` only — no
  cross-org sharing of warm processes, since pools are keyed by `environment_id`
  and environments become org-owned in A3).
- [ ] Defense in depth: pool acquire asserts `env.org_id == active_org_id()` when
  the flag is on (raises rather than executing in a foreign org's warm process).
- [ ] Workflow/deployment env pickers (`routers/environments.py` list) are already
  org-scoped by A2's session filter — add an explicit test.
- [ ] Commit `feat(tenancy): per-org default environments; no cross-org warm-process sharing`

### Task X3: Org-scoped artifact paths for workers + per-run scratch dir

**Files:** `apps/api/app/services/runtime_pool.py` (the `"artifacts_dir": str(artifact_base_dir())` in the run request, ~line 285), `apps/api/app/services/artifacts.py`

- [ ] Pass `artifacts_dir = {base}/{org_id}` to the worker (created lazily); host-side
  artifact finalize validates the recorded `storage_key` stays under the run's org
  prefix (rejects `..`/absolute escapes). Aligns with Phase F's key namespacing.
- [ ] Each run executes with `cwd` set to a per-run scratch dir under
  `{base}/scratch/{org_id}/{run_id}`, deleted on completion (best-effort).
- [ ] Commit `feat(tenancy): org-namespaced worker artifact dir + per-run scratch cwd`

### Task X4: Org-pinned sandboxed runner pools (the Tier-2 boundary)

**Files:** `apps/api/app/services/queue.py` + `remote_dispatch.py` (dispatch-time enforcement), `apps/api/app/models.py` (per-org settings column), test `apps/api/tests/test_org_pool_pinning.py`

- [ ] Per-org settings row (Phase C table; until then a column on `organizations`):
  `execution_isolation: Mapped[str]` — `"shared"` (default, Tier 1) or
  `"dedicated_pool"`.
- [ ] Dispatch enforcement: when an org is `dedicated_pool`, every run **must**
  resolve to a `RunnerPool` whose `org_id` matches and whose `provider` is
  `docker`/`kubernetes` (container-per-run); otherwise the run fails admission with
  `queue_reason="isolation_required"` instead of silently running on the shared host
  pool. Environment/workflow/deployment pool overrides are validated against the
  same rule at write time.
- [ ] Sub-workflow calls inherit the parent run's pool binding (closes the
  in-process sub-workflow hole for dedicated orgs; map fan-out rides the same path).
- [ ] UI: org settings card explaining the trade-off (cold-start per run vs isolation).
- [ ] Commit `feat(tenancy): dedicated-pool execution isolation enforcement per org`

## Phase C — Quotas, fair scheduling, metering

Expand into its own plan when A lands; the decided shape:

- `SystemSetting` singleton → per-org row (`org_id` PK; migration moves the singleton to org `default`); add per-org quota columns: `max_concurrent_runs`, `executions_per_day`, `max_map_width`, `max_loop_iterations`, `max_inflight_subworkflows`, `storage_quota_bytes`.
- `services/queue.py` lease query: add per-org in-flight accounting to the predicate + weighted round-robin across orgs with backlog; extend `ix_run_queue_lease` to lead with `org_id`; new `queue_reason="org_quota_exceeded"`.
- `subworkflow_slot` throttle in `runtime_pool.py` keyed per-org (today global — the map-fan-out noisy-neighbor hole).
- New `run_meters` table (org_id, day, runs, compute_seconds, iterations, node_runs_rows) written from the queue completion path — the loop-evasion countermeasure (a 10k-iteration loop is one run but meters as its real work) and the billing substrate.

---

## Sequencing & dependency graph

> **Revised 2026-06-10 (decided): multi-tenancy ships first**, with execution
> isolation (Phase X) woven in. Licensing and git sync follow. G5's license gate
> (`require_feature`) therefore depends on W1 landing before git sync ships.

```
X1  Worker env allowlist        ~1 day     FIRST — standalone security fix, no deps
A1–A6  Phase A data isolation   ~4–6 wks   (RLS + PG CI lane from first commit)
E + F  Org KEK + storage ns     ~1–2 wks   (before real multi-org data exists)
X2–X3  Per-org pools + paths    ~1 wk
B   Org UX / invitations        ~2–3 wks   (own plan; SSO/SCIM Enterprise-gated)
X4  Dedicated-pool isolation    ~1–2 wks   (Tier-2 boundary for untrusted orgs)
C   Quotas/fairness/metering    ~2–3 wks   (own plan)
W1  Licensing (L1→L5)           ~1.5–2 wks
W2  Git sync (G1→G6)            ~2–3 wks   (G5 needs W1's require_feature)
Phase D (deep sandbox)          deferred   (egress policy, gVisor — funded cloud build)
```

Git sync tables still carry nullable `org_id` from day one (Task G1) and join the A3 backfill list, so there is no retrofit whichever order they land in.

## Risks (delta from the design docs)

- **A3's blast radius is every `session.add` site.** The `tenancy.stamp` helper + a CI grep check (`session.add(` without a preceding `stamp(`) keeps stragglers visible.
- **`_CRED_KEYS` drift (git sync):** node manifests must be the source of truth for which params hold credential ids; lock with a manifest-scan test or imported workflows silently keep dangling ids.
- **License public key bootstrap:** Task L1 ships with a placeholder constant; run `scripts/issue_license.py keygen` and replace before any release build (add a release-checklist assert that the placeholder string is gone).
- **RLS + `expire_on_commit=False` + pooled connections:** the `SET LOCAL` test in A5 is the guard; never switch to session-level `SET` or a pooled connection leaks the previous request's org.
- **Tier-1 isolation is hygiene, not a security boundary.** After X1–X3 a malicious author on the shared pool can still read host files (`.env`, SQLite) as the worker's OS user. Orgs with untrusted authors must be `dedicated_pool` (X4). Documentation and the org settings UI must say this plainly — do not market soft tenancy as hard isolation.
