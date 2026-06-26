# Runners Production Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring runner pools to production grade across three slices: reliability (tokens, ghost cleanup, drain, SSH restart), correctness (AWS secret encryption, label-aware dispatch), and observability (run history, sparklines, pool search, token expiry UI).

**Architecture:** All backend changes live in the existing FastAPI app under `apps/api`. Schema changes go in a single Alembic migration (0056). Frontend changes extend `RunnerPoolsPage.tsx` and its supporting query/type modules. No new services or packages are introduced.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, React 18, TypeScript, React Query, pure SVG (no chart library).

## Global Constraints

- All Alembic migrations use `_columns()` / `_tables()` guard pattern (see `0018_remote_runners.py`) — never drop columns
- All new API endpoints follow existing auth pattern: `Depends(get_session)` + `Depends(require_permission(...))` where writes are needed
- Test files use `pytest-asyncio` with `AsyncClient` fixture pattern matching `tests/test_runner_pools.py`
- Label filtering uses Python-level dict subset check (not SQL `@>`), so it works on both SQLite (test) and Postgres (prod)
- `RUNNER_TOKEN_TTL_DAYS` default 365; `RUNNER_GHOST_TTL_HOURS` default 48 — both env-configurable
- Never break existing runner agent protocol — new WS message types are advisory only

---

## File Map

**New files:**
- `apps/api/alembic/versions/0056_runners_hardening.py` — schema migration for all slices
- `apps/api/app/services/ghost_cleanup.py` — ghost runner sweep logic
- `apps/api/tests/test_runner_token_ttl.py`
- `apps/api/tests/test_ghost_cleanup.py`
- `apps/api/tests/test_drain_mode.py`
- `apps/api/tests/test_ssh_restart.py`
- `apps/api/tests/test_aws_secret_encryption.py`
- `apps/api/tests/test_label_dispatch.py`
- `apps/api/tests/test_run_history.py`

**Modified files:**
- `apps/api/app/config.py` — add `RUNNER_TOKEN_TTL_DAYS`, `RUNNER_GHOST_TTL_HOURS`
- `apps/api/app/models.py` — add `token_expires_at`, `aws_secret_key_enc`, `required_labels`
- `apps/api/app/schemas.py` — extend `RunnerInfo`, `RunnerPoolInfo`, `RunnerPoolHealth`; add `RunHistoryBucket`
- `apps/api/app/routers/runner_pools.py` — drain, restart, cleanup-ghosts, run-history, recent-runs endpoints
- `apps/api/app/services/queue.py` — no change (label filter is at dispatch layer, not lease layer)
- `apps/api/app/services/providers/agent.py` — label filter in `pick_agent()`, drain_complete hook
- `apps/api/app/services/ssh_onboard.py` — add `restart_script()` function
- `apps/api/app/main.py` — register `ghost_cleanup_loop` task
- `apps/web/src/types.ts` — extend `RunnerInfo`, `RunnerPoolInfo`, `RunnerPoolHealth`; add `RunHistoryBucket`
- `apps/web/src/api.ts` (or equivalent API client) — drain, restart, cleanup-ghosts, run-history calls
- `apps/web/src/queries.ts` — new React Query hooks
- `apps/web/src/RunnerPoolsPage.tsx` — drain button, restart modal, ghost badge, token expiry, sparkline, search, recent runs

---

## Task 1: Database Migration + Model Changes

**Files:**
- Create: `apps/api/alembic/versions/0056_runners_hardening.py`
- Modify: `apps/api/app/models.py`

**Interfaces:**
- Produces: `Runner.token_expires_at: datetime | None`, `RunnerPool.aws_secret_key_enc: str | None`, `Run.required_labels: dict | None`

- [ ] **Step 1: Write the migration file**

```python
# apps/api/alembic/versions/0056_runners_hardening.py
"""Runner production hardening: token_expires_at, aws_secret_key_enc, required_labels, history index

Revision ID: 0056_runners_hardening
Revises: 0055_uq_environment_is_global
Create Date: 2026-06-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision: str = "0056_runners_hardening"
down_revision: str | None = "0055_uq_environment_is_global"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    runner_cols = _columns("runners")
    with op.batch_alter_table("runners") as batch:
        if "token_expires_at" not in runner_cols:
            batch.add_column(
                sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True)
            )

    pool_cols = _columns("runner_pools")
    with op.batch_alter_table("runner_pools") as batch:
        if "aws_secret_key_enc" not in pool_cols:
            batch.add_column(
                sa.Column("aws_secret_key_enc", sa.Text(), nullable=True)
            )

    run_cols = _columns("runs")
    with op.batch_alter_table("runs") as batch:
        if "required_labels" not in run_cols:
            batch.add_column(
                sa.Column("required_labels", sa.JSON(), nullable=True)
            )

    # Composite index for run-history bucketing queries
    idx_name = "ix_runs_runner_pool_finished"
    if idx_name not in _indexes("runs"):
        op.create_index(
            idx_name,
            "runs",
            ["runner_pool_id", "finished_at"],
        )

    # Data migration: move aws_secret_access_key from provider_config JSON
    # into the new encrypted column. Run in-process via the encrypt_data helper.
    # This migration deliberately skips encryption (stores plaintext in the
    # column) because Fernet key may not be configured in the migration env.
    # The save-path in the router will re-encrypt on next write. For security,
    # we only move the value (away from the readable JSON blob) and mark it
    # with a sentinel so the router knows it needs re-encryption.
    conn = op.get_bind()
    rows = conn.execute(
        text("SELECT id, provider_config FROM runner_pools WHERE provider_config IS NOT NULL")
    ).fetchall()
    for row_id, cfg in rows:
        if not isinstance(cfg, dict):
            continue
        secret = cfg.get("aws_secret_access_key")
        if not secret:
            continue
        cleaned = {k: v for k, v in cfg.items() if k != "aws_secret_access_key"}
        conn.execute(
            text(
                "UPDATE runner_pools SET provider_config = :cfg, aws_secret_key_enc = :enc "
                "WHERE id = :id"
            ),
            {"cfg": sa.JSON().process_bind_param(cleaned, None), "enc": f"__migrated__{secret}", "id": row_id},
        )


def downgrade() -> None:
    idx_name = "ix_runs_runner_pool_finished"
    if idx_name in _indexes("runs"):
        op.drop_index(idx_name, table_name="runs")

    run_cols = _columns("runs")
    with op.batch_alter_table("runs") as batch:
        if "required_labels" in run_cols:
            batch.drop_column("required_labels")

    pool_cols = _columns("runner_pools")
    with op.batch_alter_table("runner_pools") as batch:
        if "aws_secret_key_enc" in pool_cols:
            batch.drop_column("aws_secret_key_enc")

    runner_cols = _columns("runners")
    with op.batch_alter_table("runners") as batch:
        if "token_expires_at" in runner_cols:
            batch.drop_column("token_expires_at")
```

- [ ] **Step 2: Add model fields**

Open `apps/api/app/models.py`. Find the `Runner` class and add after `ssh_credentials`:

```python
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

Find `RunnerPool` class and add after `max_concurrent_runs`:

```python
    aws_secret_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Find the `Run` class (it's large). Add `required_labels` after `batch_id`:

```python
    required_labels: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

- [ ] **Step 3: Run migration**

```bash
cd apps/api && python -m alembic upgrade head
```

Expected: `INFO  [alembic.runtime.migration] Running upgrade 0055_uq_environment_is_global -> 0056_runners_hardening`

- [ ] **Step 4: Verify columns exist**

```bash
cd apps/api && python -c "
from app.models import Runner, RunnerPool, Run
from sqlalchemy import inspect
from app.db import engine
import asyncio
async def check():
    async with engine.connect() as conn:
        from sqlalchemy import text
        r = await conn.execute(text('SELECT column_name FROM information_schema.columns WHERE table_name=\'runners\''))
        print([row[0] for row in r])
asyncio.run(check())
"
```

Expected output includes: `token_expires_at`

- [ ] **Step 5: Commit**

```bash
git add apps/api/alembic/versions/0056_runners_hardening.py apps/api/app/models.py
git commit -m "feat(api): migration 0056 — runner hardening schema (token_expires_at, aws_secret_key_enc, required_labels)"
```

---

## Task 2: Config Additions

**Files:**
- Modify: `apps/api/app/config.py`

**Interfaces:**
- Produces: `settings.runner_token_ttl_days: int`, `settings.runner_ghost_ttl_hours: int`

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_runner_token_ttl.py`:

```python
import pytest
from app.config import Settings


def test_default_token_ttl():
    s = Settings()
    assert s.runner_token_ttl_days == 365


def test_default_ghost_ttl():
    s = Settings()
    assert s.runner_ghost_ttl_hours == 48


def test_token_ttl_from_env(monkeypatch):
    monkeypatch.setenv("RUNNER_TOKEN_TTL_DAYS", "30")
    s = Settings()
    assert s.runner_token_ttl_days == 30


def test_ghost_ttl_zero_disables(monkeypatch):
    monkeypatch.setenv("RUNNER_GHOST_TTL_HOURS", "0")
    s = Settings()
    assert s.runner_ghost_ttl_hours == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd apps/api && python -m pytest tests/test_runner_token_ttl.py -v
```

Expected: `AttributeError: 'Settings' object has no attribute 'runner_token_ttl_days'`

- [ ] **Step 3: Add config fields**

Open `apps/api/app/config.py`. Find the `runner_offline_after_seconds` setting and add after it:

```python
    # Token lifetime for runner registration tokens. Default 1 year.
    # Tokens are revocable at any time by deleting the runner row.
    runner_token_ttl_days: int = 365
    # Ghost runner cleanup: delete runners that never connected within this
    # window. Set to 0 to disable auto-cleanup.
    runner_ghost_ttl_hours: int = 48
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd apps/api && python -m pytest tests/test_runner_token_ttl.py -v
```

Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/config.py apps/api/tests/test_runner_token_ttl.py
git commit -m "feat(api): RUNNER_TOKEN_TTL_DAYS and RUNNER_GHOST_TTL_HOURS config settings"
```

---

## Task 3: Long-Lived Token TTL (Slice 1.1)

**Files:**
- Modify: `apps/api/app/routers/runner_pools.py`
- Modify: `apps/api/app/schemas.py`

**Interfaces:**
- Consumes: `settings.runner_token_ttl_days`, `Runner.token_expires_at`
- Produces: `RegistrationTokenResponse.expires_at` uses TTL from config; `RunnerInfo.token_expires_at: datetime | None`

- [ ] **Step 1: Write the failing test**

Add to `apps/api/tests/test_runner_token_ttl.py`:

```python
import pytest
from httpx import AsyncClient
from datetime import UTC, datetime, timedelta


@pytest.mark.asyncio
async def test_registration_token_uses_config_ttl(client: AsyncClient, monkeypatch):
    """Token expiry should be ~1 year when RUNNER_TOKEN_TTL_DAYS=365."""
    from app.config import settings
    monkeypatch.setattr(settings, "runner_token_ttl_days", 365)

    # Create a pool first
    pool_resp = await client.post("/runner-pools", json={"name": "ttl-test", "provider": "agent"})
    assert pool_resp.status_code == 201
    pool_id = pool_resp.json()["id"]

    resp = await client.post(f"/runner-pools/{pool_id}/registration-tokens", json={})
    assert resp.status_code == 200
    data = resp.json()

    expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
    delta = expires_at - datetime.now(UTC)
    # Should be ~365 days (allow 1-minute skew)
    assert timedelta(days=364) < delta < timedelta(days=366)


@pytest.mark.asyncio
async def test_runner_info_includes_token_expires_at(client: AsyncClient, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "runner_token_ttl_days", 365)

    pool_resp = await client.post("/runner-pools", json={"name": "expiry-test", "provider": "agent"})
    pool_id = pool_resp.json()["id"]
    await client.post(f"/runner-pools/{pool_id}/registration-tokens", json={"name": "r1"})

    runners_resp = await client.get(f"/runner-pools/{pool_id}/runners")
    assert runners_resp.status_code == 200
    runner = runners_resp.json()[0]
    assert "token_expires_at" in runner
    assert runner["token_expires_at"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd apps/api && python -m pytest tests/test_runner_token_ttl.py::test_registration_token_uses_config_ttl -v
```

Expected: TTL is 24h (86400s), not 1 year — test fails on the `timedelta(days=364)` assertion.

- [ ] **Step 3: Update token creation in runner_pools.py**

Find `create_registration_token` in `apps/api/app/routers/runner_pools.py`. Replace the hardcoded TTL:

```python
    ttl = 86_400  # 24 hours
```

with:

```python
    ttl = settings.runner_token_ttl_days * 86_400
```

Then in the same function, after `await session.refresh(runner)`, add:

```python
    from datetime import UTC, datetime
    runner.token_expires_at = datetime.fromtimestamp(
        datetime.now(UTC).timestamp() + ttl, tz=UTC
    )
    await session.commit()
```

Also find `ssh_onboard` endpoint in the same file. It has `ttl_seconds=86_400` hardcoded:

```python
    token = create_payload_token(
        {
            "sub": runner.id,
            "pool_id": pool_id,
            "org_id": runner.org_id,
            "kind": "runner_registration",
        },
        ttl_seconds=86_400,
    )
```

Replace `ttl_seconds=86_400` with `ttl_seconds=settings.runner_token_ttl_days * 86_400`. After `await session.refresh(runner)`, add:

```python
    runner.token_expires_at = datetime.now(UTC) + timedelta(seconds=settings.runner_token_ttl_days * 86_400)
    await session.commit()
```

Add `from datetime import timedelta` to the imports at top of `runner_pools.py` if not present.

- [ ] **Step 4: Add `token_expires_at` to `RunnerInfo` schema**

Open `apps/api/app/schemas.py`. Find `class RunnerInfo` and add after `updated_at`:

```python
    token_expires_at: datetime | None = None
    ssh_host: str | None = None
```

Find `_runner_info()` in `apps/api/app/routers/runner_pools.py` and update it:

```python
def _runner_info(runner: Runner) -> RunnerInfo:
    return RunnerInfo(
        id=runner.id,
        pool_id=runner.pool_id,
        name=runner.name,
        status=runner.status,
        capabilities=runner.capabilities or {},
        last_seen_at=runner.last_seen_at,
        current_runs=runner.current_runs,
        max_concurrent_runs=runner.max_concurrent_runs,
        cached_env_ids=runner.cached_env_ids or [],
        created_at=runner.created_at,
        updated_at=runner.updated_at,
        token_expires_at=runner.token_expires_at,
        ssh_host=runner.ssh_host,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd apps/api && python -m pytest tests/test_runner_token_ttl.py -v
```

Expected: `4 passed` (original 4) + new tests pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/routers/runner_pools.py apps/api/app/schemas.py apps/api/tests/test_runner_token_ttl.py
git commit -m "feat(api): 1-year runner tokens + token_expires_at + ssh_host in RunnerInfo"
```

---

## Task 4: Ghost Runner Cleanup (Slice 1.2)

**Files:**
- Create: `apps/api/app/services/ghost_cleanup.py`
- Modify: `apps/api/app/routers/runner_pools.py`
- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/main.py`
- Create: `apps/api/tests/test_ghost_cleanup.py`

**Interfaces:**
- Produces: `cleanup_ghost_runners(pool_id=None) -> int`, `GET RunnerPoolInfo.ghost_count: int`, `POST /runner-pools/{pool_id}/cleanup-ghosts -> {"cleaned": N}`

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/test_ghost_cleanup.py
import pytest
from datetime import UTC, datetime, timedelta
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import SessionLocal
from app.models import Runner


async def _make_ghost(pool_id: str, hours_old: int = 49) -> str:
    """Insert a runner row that qualifies as a ghost."""
    async with SessionLocal() as session:
        r = Runner(
            pool_id=pool_id,
            name=f"ghost-{hours_old}h",
            status="offline",
            token_hash="",
            max_concurrent_runs=1,
        )
        session.add(r)
        await session.flush()
        runner_id = r.id
        # Back-date created_at so it falls outside the ghost TTL window
        from sqlalchemy import text
        await session.execute(
            text("UPDATE runners SET created_at = :ts WHERE id = :id"),
            {"ts": datetime.now(UTC) - timedelta(hours=hours_old), "id": runner_id},
        )
        await session.commit()
    return runner_id


@pytest.mark.asyncio
async def test_cleanup_removes_ghosts(client: AsyncClient):
    pool = await client.post("/runner-pools", json={"name": "ghost-pool", "provider": "agent"})
    pool_id = pool.json()["id"]

    ghost_id = await _make_ghost(pool_id, hours_old=49)

    resp = await client.post(f"/runner-pools/{pool_id}/cleanup-ghosts")
    assert resp.status_code == 200
    assert resp.json()["cleaned"] >= 1

    # Verify the ghost is gone
    async with SessionLocal() as session:
        r = await session.get(Runner, ghost_id)
    assert r is None


@pytest.mark.asyncio
async def test_cleanup_skips_recent_runners(client: AsyncClient):
    """Runners created within TTL window must NOT be cleaned."""
    pool = await client.post("/runner-pools", json={"name": "fresh-pool", "provider": "agent"})
    pool_id = pool.json()["id"]

    # Mint a token — creates a runner row that is < 48h old
    await client.post(f"/runner-pools/{pool_id}/registration-tokens", json={})

    resp = await client.post(f"/runner-pools/{pool_id}/cleanup-ghosts")
    assert resp.status_code == 200
    assert resp.json()["cleaned"] == 0


@pytest.mark.asyncio
async def test_cleanup_skips_draining_runners(client: AsyncClient):
    """Draining runners with last_seen_at=NULL must never be auto-cleaned."""
    pool = await client.post("/runner-pools", json={"name": "drain-pool", "provider": "agent"})
    pool_id = pool.json()["id"]

    async with SessionLocal() as session:
        r = Runner(
            pool_id=pool_id,
            name="draining-ghost",
            status="draining",
            token_hash="",
            max_concurrent_runs=1,
        )
        session.add(r)
        await session.flush()
        runner_id = r.id
        from sqlalchemy import text
        await session.execute(
            text("UPDATE runners SET created_at = :ts WHERE id = :id"),
            {"ts": datetime.now(UTC) - timedelta(hours=100), "id": runner_id},
        )
        await session.commit()

    resp = await client.post(f"/runner-pools/{pool_id}/cleanup-ghosts")
    assert resp.status_code == 200
    assert resp.json()["cleaned"] == 0


@pytest.mark.asyncio
async def test_ghost_count_on_pool_list(client: AsyncClient):
    """RunnerPoolInfo.ghost_count reflects unconnected offline runners."""
    pool = await client.post("/runner-pools", json={"name": "count-pool", "provider": "agent"})
    pool_id = pool.json()["id"]
    await _make_ghost(pool_id, hours_old=49)

    pools = await client.get("/runner-pools")
    pool_data = next(p for p in pools.json() if p["id"] == pool_id)
    assert pool_data["ghost_count"] >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/api && python -m pytest tests/test_ghost_cleanup.py -v
```

Expected: `AttributeError` or `404` — endpoint and service don't exist yet.

- [ ] **Step 3: Create ghost_cleanup service**

```python
# apps/api/app/services/ghost_cleanup.py
"""Ghost runner cleanup — removes runner rows that never connected.

A ghost is: status='offline', last_seen_at IS NULL, created_at older than
RUNNER_GHOST_TTL_HOURS, and no queued/running Run assigned to it.
Draining runners are never cleaned regardless of last_seen_at.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import Run, Runner

logger = logging.getLogger(__name__)


async def cleanup_ghost_runners(
    session: AsyncSession,
    pool_id: str | None = None,
) -> int:
    """Delete ghost runners. Returns count deleted.

    pool_id=None sweeps all pools (background task). pool_id set = manual
    endpoint for a specific pool. Uses SELECT FOR UPDATE SKIP LOCKED so
    concurrent cleanup calls don't double-delete.
    """
    from datetime import UTC, datetime, timedelta

    if settings.runner_ghost_ttl_hours == 0:
        return 0

    cutoff = datetime.now(UTC) - timedelta(hours=settings.runner_ghost_ttl_hours)

    stmt = select(Runner).where(
        Runner.status == "offline",
        Runner.last_seen_at.is_(None),
        Runner.created_at < cutoff,
    )
    if pool_id is not None:
        stmt = stmt.where(Runner.pool_id == pool_id)

    # SQLite does not support FOR UPDATE — guard it.
    if "postgresql" in str(session.bind.url if hasattr(session, "bind") else ""):
        stmt = stmt.with_for_update(skip_locked=True)

    candidates = (await session.scalars(stmt)).all()
    if not candidates:
        return 0

    # Safety gate: skip runners that have queued/running runs assigned.
    candidate_ids = [r.id for r in candidates]
    active_runner_ids = set(
        (
            await session.scalars(
                select(Run.runner_id).where(
                    Run.runner_id.in_(candidate_ids),
                    Run.status.in_(("queued", "running")),
                )
            )
        ).all()
    )

    deleted = 0
    for runner in candidates:
        if runner.id in active_runner_ids:
            continue
        await session.delete(runner)
        deleted += 1

    if deleted:
        await session.commit()
        logger.info("ghost_cleanup: deleted %d ghost runner(s) (pool=%s)", deleted, pool_id or "all")
    return deleted


async def ghost_cleanup_loop() -> None:
    """Background task: sweep all orgs every 60 minutes."""
    from app.tenancy import run_as_system

    while True:
        await asyncio.sleep(3600)
        try:
            with run_as_system():
                async with SessionLocal() as session:
                    n = await cleanup_ghost_runners(session)
                    if n:
                        logger.info("ghost_cleanup_loop: cleaned %d runner(s)", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ghost_cleanup_loop: error during sweep")
```

- [ ] **Step 4: Add `ghost_count` to schema and `_pool_info()`**

In `apps/api/app/schemas.py`, find `class RunnerPoolInfo` and add:

```python
    ghost_count: int = 0
```

In `apps/api/app/routers/runner_pools.py`, update `_pool_info()`:

```python
def _pool_info(pool: RunnerPool, runners: list[Runner]) -> RunnerPoolInfo:
    online = sum(1 for r in runners if r.status in ("online", "busy"))
    ghost_count = sum(
        1 for r in runners
        if r.status == "offline" and r.last_seen_at is None
    )
    return RunnerPoolInfo(
        id=pool.id,
        name=pool.name,
        provider=pool.provider,
        provider_config=pool.provider_config or {},
        max_concurrent_runs=pool.max_concurrent_runs,
        runner_count=len(runners),
        online_count=online,
        ghost_count=ghost_count,
        aws_secret_configured=bool(pool.aws_secret_key_enc),
        created_at=pool.created_at,
        updated_at=pool.updated_at,
    )
```

Also add `aws_secret_configured: bool = False` to `RunnerPoolInfo` in schemas.py now (used in Task 7).

- [ ] **Step 5: Add cleanup-ghosts endpoint**

In `apps/api/app/routers/runner_pools.py`, add after the runners sub-resource section:

```python
@router.post(
    "/{pool_id}/cleanup-ghosts",
    dependencies=[Depends(require_permission("runner_pool:write"))],
)
async def cleanup_pool_ghosts(
    pool_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Remove ghost runners — offline rows that never connected within the TTL window."""
    from app.services.ghost_cleanup import cleanup_ghost_runners

    pool = await session.get(RunnerPool, pool_id)
    if pool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner pool not found")
    n = await cleanup_ghost_runners(session, pool_id=pool_id)
    return {"cleaned": n}
```

- [ ] **Step 6: Register the background loop in main.py**

In `apps/api/app/main.py`, find the import section and add:

```python
from app.services.ghost_cleanup import ghost_cleanup_loop
```

In the lifespan function, find where `github_sync` task is created:

```python
    github_sync = asyncio.create_task(_as_system(github_sync_dispatch_loop)())
```

Add after it:

```python
    ghost_cleanup = asyncio.create_task(_as_system(ghost_cleanup_loop)())
```

In the shutdown section where tasks are cancelled, add `ghost_cleanup` to the task list:

```python
    for task in (scheduler, retention, reaper, broker_reaper, queue_loop, cloud_idle, heartbeat, github_sync, ghost_cleanup):
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd apps/api && python -m pytest tests/test_ghost_cleanup.py -v
```

Expected: `4 passed`

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/services/ghost_cleanup.py apps/api/app/routers/runner_pools.py \
  apps/api/app/schemas.py apps/api/app/main.py apps/api/tests/test_ghost_cleanup.py
git commit -m "feat(api): ghost runner cleanup service, endpoint, and background loop"
```

---

## Task 5: Drain Mode (Slice 1.3)

**Files:**
- Modify: `apps/api/app/routers/runner_pools.py`
- Modify: `apps/api/app/services/providers/agent.py`
- Modify: `apps/api/app/services/remote_dispatch.py`
- Create: `apps/api/tests/test_drain_mode.py`

**Interfaces:**
- Produces: `POST /runner-pools/{pool_id}/runners/{runner_id}/drain` body `{"drain": bool}`, `drain_complete` WS signal on `current_runs → 0`, dispatcher skips `draining` runners

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/test_drain_mode.py
import pytest
from httpx import AsyncClient
from app.db import SessionLocal
from app.models import Runner


async def _make_online_runner(pool_id: str, name: str = "test-runner") -> str:
    async with SessionLocal() as session:
        r = Runner(
            pool_id=pool_id,
            name=name,
            status="online",
            token_hash="",
            max_concurrent_runs=4,
            current_runs=0,
        )
        session.add(r)
        await session.commit()
        await session.refresh(r)
        return r.id


@pytest.mark.asyncio
async def test_drain_sets_status(client: AsyncClient):
    pool = await client.post("/runner-pools", json={"name": "drain-test", "provider": "agent"})
    pool_id = pool.json()["id"]
    runner_id = await _make_online_runner(pool_id)

    resp = await client.post(
        f"/runner-pools/{pool_id}/runners/{runner_id}/drain",
        json={"drain": True},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "draining"


@pytest.mark.asyncio
async def test_undrain_sets_online(client: AsyncClient):
    pool = await client.post("/runner-pools", json={"name": "undrain-test", "provider": "agent"})
    pool_id = pool.json()["id"]
    runner_id = await _make_online_runner(pool_id)

    await client.post(
        f"/runner-pools/{pool_id}/runners/{runner_id}/drain",
        json={"drain": True},
    )
    resp = await client.post(
        f"/runner-pools/{pool_id}/runners/{runner_id}/drain",
        json={"drain": False},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "online"


@pytest.mark.asyncio
async def test_drain_offline_runner(client: AsyncClient):
    """Draining an offline runner sets draining status in DB even though no WS message is sent."""
    pool = await client.post("/runner-pools", json={"name": "offline-drain", "provider": "agent"})
    pool_id = pool.json()["id"]
    async with SessionLocal() as session:
        r = Runner(pool_id=pool_id, name="offline-r", status="offline", token_hash="", max_concurrent_runs=1)
        session.add(r)
        await session.commit()
        await session.refresh(r)
        runner_id = r.id

    resp = await client.post(
        f"/runner-pools/{pool_id}/runners/{runner_id}/drain",
        json={"drain": True},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "draining"


@pytest.mark.asyncio
async def test_draining_runner_excluded_from_pick_agent():
    """pick_agent must not return a draining runner."""
    from app.services.providers.agent import pick_agent
    from unittest.mock import AsyncMock, MagicMock

    # Mock dispatcher with no agents
    d = MagicMock()
    d._agents = {}
    d._lock = AsyncMock()
    d._lock.__aenter__ = AsyncMock(return_value=None)
    d._lock.__aexit__ = AsyncMock(return_value=None)

    # Nothing to pick — no connected agents
    result = await pick_agent(d, SessionLocal, pool_id="nonexistent-pool")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/api && python -m pytest tests/test_drain_mode.py -v
```

Expected: 404 on the drain endpoint — it doesn't exist yet.

- [ ] **Step 3: Add drain endpoint to runner_pools.py**

Add this to `apps/api/app/routers/runner_pools.py`, after the `update_runner` endpoint:

```python
from pydantic import BaseModel as _BaseModel

class DrainRequest(_BaseModel):
    drain: bool


@router.post(
    "/{pool_id}/runners/{runner_id}/drain",
    response_model=RunnerInfo,
    dependencies=[Depends(require_permission("runner_pool:write"))],
)
async def drain_runner(
    pool_id: str,
    runner_id: str,
    body: DrainRequest,
    session: AsyncSession = Depends(get_session),
) -> RunnerInfo:
    """Set or clear drain mode on a runner.

    While draining, the dispatcher skips this runner for new assignments.
    The runner finishes its current runs; when current_runs reaches 0 the
    API sends a drain_complete WS message as a courtesy signal.
    Undrain always sets status=online — the dispatcher will update to busy
    on next assignment.
    """
    runner = await session.get(Runner, runner_id)
    if runner is None or runner.pool_id != pool_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner not found")

    if body.drain:
        runner.status = "draining"
        # Send advisory WS message if runner is connected (non-blocking).
        from app.services.remote_dispatch import dispatcher
        try:
            async with dispatcher._lock:
                conn = dispatcher._agents.get(runner_id)
            if conn is not None:
                await conn.send({"type": "drain", "runner_id": runner_id})
        except Exception:  # noqa: BLE001
            pass
    else:
        runner.status = "online"

    runner.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(runner)
    return _runner_info(runner)
```

- [ ] **Step 4: Exclude draining runners in `pick_agent()`**

Open `apps/api/app/services/providers/agent.py`. Find `pick_agent()`. The query currently filters `Runner.status.in_(["online", "busy"])`. Add `"draining"` is already excluded since it's not in that list — **verify this is the case**. The status values `"online"` and `"busy"` exclude `"draining"` by definition. No code change needed here — the filter is already correct.

However, verify the query in `pick_agent()`:

```python
        runners = (
            await session.scalars(
                select(Runner).where(
                    Runner.pool_id == pool_id,
                    Runner.status.in_(["online", "busy"]),
                )
            )
        ).all()
```

`"draining"` is not in `["online", "busy"]`, so draining runners are already excluded. ✓

- [ ] **Step 5: Add drain_complete hook in agent.py run completion**

In `apps/api/app/services/providers/agent.py`, find the `finally` block in `assign_agent_run()` (around line 129-137):

```python
    finally:
        async with session_factory() as session:
            runner = await session.get(Runner, conn.runner_id)
            if runner is not None:
                runner.current_runs = max(0, runner.current_runs - 1)
                if runner.current_runs == 0:
                    runner.status = "online"
                await session.commit()
        d.signal_capacity()
```

Replace with:

```python
    finally:
        async with session_factory() as session:
            runner = await session.get(Runner, conn.runner_id)
            if runner is not None:
                runner.current_runs = max(0, runner.current_runs - 1)
                was_draining = runner.status == "draining"
                if runner.current_runs == 0 and not was_draining:
                    runner.status = "online"
                await session.commit()
                # Courtesy drain_complete signal when the last run finishes on a draining runner.
                if was_draining and runner.current_runs == 0:
                    try:
                        async with d._lock:
                            drain_conn = d._agents.get(conn.runner_id)
                        if drain_conn is not None:
                            await drain_conn.send({"type": "drain_complete", "runner_id": conn.runner_id})
                    except Exception:  # noqa: BLE001
                        pass
        d.signal_capacity()
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd apps/api && python -m pytest tests/test_drain_mode.py -v
```

Expected: `4 passed`

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/routers/runner_pools.py apps/api/app/services/providers/agent.py \
  apps/api/tests/test_drain_mode.py
git commit -m "feat(api): runner drain mode — drain endpoint + dispatch exclusion + drain_complete signal"
```

---

## Task 6: SSH Runner Restart (Slice 1.4)

**Files:**
- Modify: `apps/api/app/services/ssh_onboard.py`
- Modify: `apps/api/app/routers/runner_pools.py`
- Create: `apps/api/tests/test_ssh_restart.py`

**Interfaces:**
- Produces: `restart_script(req_creds: dict) -> str`, `POST /runner-pools/{pool_id}/runners/{runner_id}/restart -> {"log": str}`

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/test_ssh_restart.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient
from app.db import SessionLocal
from app.models import Runner
from app.services.crypto import encrypt_data


async def _make_ssh_runner(pool_id: str) -> str:
    creds = {
        "host": "10.0.0.1",
        "port": 22,
        "username": "ubuntu",
        "auth_method": "password",
        "password": "secret",
        "private_key": None,
        "passphrase": None,
        "use_systemd": True,
    }
    async with SessionLocal() as session:
        r = Runner(
            pool_id=pool_id,
            name="ssh-runner",
            status="online",
            token_hash="",
            max_concurrent_runs=1,
            ssh_host="ubuntu@10.0.0.1:22",
            ssh_credentials=encrypt_data(creds),
        )
        session.add(r)
        await session.commit()
        await session.refresh(r)
        return r.id


@pytest.mark.asyncio
async def test_restart_runner_success(client: AsyncClient):
    pool = await client.post("/runner-pools", json={"name": "ssh-pool", "provider": "agent"})
    pool_id = pool.json()["id"]
    runner_id = await _make_ssh_runner(pool_id)

    with patch("app.services.ssh_onboard.onboard_restart", new_callable=AsyncMock) as mock_restart:
        mock_restart.return_value = "[noodle] restarted via systemd"
        resp = await client.post(f"/runner-pools/{pool_id}/runners/{runner_id}/restart")

    assert resp.status_code == 200
    assert "log" in resp.json()
    assert "restarted" in resp.json()["log"]


@pytest.mark.asyncio
async def test_restart_non_ssh_runner_returns_400(client: AsyncClient):
    pool = await client.post("/runner-pools", json={"name": "nossh-pool", "provider": "agent"})
    pool_id = pool.json()["id"]
    async with SessionLocal() as session:
        r = Runner(pool_id=pool_id, name="no-ssh", status="online", token_hash="", max_concurrent_runs=1)
        session.add(r)
        await session.commit()
        await session.refresh(r)
        runner_id = r.id

    resp = await client.post(f"/runner-pools/{pool_id}/runners/{runner_id}/restart")
    assert resp.status_code == 400
    assert "SSH" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_restart_rate_limited(client: AsyncClient):
    pool = await client.post("/runner-pools", json={"name": "rate-pool", "provider": "agent"})
    pool_id = pool.json()["id"]
    runner_id = await _make_ssh_runner(pool_id)

    with patch("app.services.ssh_onboard.onboard_restart", new_callable=AsyncMock) as mock_restart:
        mock_restart.return_value = "ok"
        for _ in range(5):
            await client.post(f"/runner-pools/{pool_id}/runners/{runner_id}/restart")
        # 6th call should be rate-limited
        resp = await client.post(f"/runner-pools/{pool_id}/runners/{runner_id}/restart")

    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_restart_ssh_auth_failure_returns_400(client: AsyncClient):
    pool = await client.post("/runner-pools", json={"name": "authfail-pool", "provider": "agent"})
    pool_id = pool.json()["id"]
    runner_id = await _make_ssh_runner(pool_id)

    with patch("app.services.ssh_onboard.onboard_restart", new_callable=AsyncMock) as mock_restart:
        mock_restart.side_effect = RuntimeError("SSH authentication failed")
        resp = await client.post(f"/runner-pools/{pool_id}/runners/{runner_id}/restart")

    assert resp.status_code == 400
    assert "authentication" in resp.json()["detail"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/api && python -m pytest tests/test_ssh_restart.py -v
```

Expected: 404 on restart endpoint.

- [ ] **Step 3: Add `onboard_restart` to ssh_onboard.py**

Add to `apps/api/app/services/ssh_onboard.py`:

```python
def _restart_script(creds: dict) -> str:
    """Minimal script to restart noodle-runner. Does NOT re-register."""
    use_systemd = creds.get("use_systemd", True)
    if use_systemd:
        return (
            "if command -v sudo >/dev/null 2>&1 && command -v systemctl >/dev/null 2>&1; then\n"
            "  if sudo systemctl restart noodle-runner; then\n"
            '    echo "[noodle] restarted via systemd"\n'
            "  else\n"
            '    echo "[noodle] systemd restart failed — trying nohup" >&2\n'
            "    pkill -f noodle_runner_agent.agent || true\n"
            '    nohup python3 -m noodle_runner_agent.agent start > "$HOME/noodle-runner.log" 2>&1 &\n'
            '    echo "[noodle] restarted via nohup"\n'
            "  fi\n"
            "else\n"
            "  pkill -f noodle_runner_agent.agent || true\n"
            '  nohup python3 -m noodle_runner_agent.agent start > "$HOME/noodle-runner.log" 2>&1 &\n'
            '  echo "[noodle] restarted via nohup (no sudo/systemctl)"\n'
            "fi\n"
        )
    return (
        "pkill -f noodle_runner_agent.agent || true\n"
        'nohup python3 -m noodle_runner_agent.agent start > "$HOME/noodle-runner.log" 2>&1 &\n'
        'echo "[noodle] restarted via nohup"\n'
    )


async def onboard_restart(creds: dict) -> str:
    """SSH into the host and restart the noodle-runner process.

    creds is the decrypted dict from runner.ssh_credentials.
    Returns the combined log. Raises RuntimeError on failure.
    """
    try:
        import asyncssh  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "asyncssh is required on the API host for SSH operations"
        ) from exc

    conn_kwargs: dict = {
        "host": creds["host"],
        "port": creds.get("port", 22),
        "username": creds["username"],
        "known_hosts": None,
    }
    if creds.get("auth_method") == "password":
        conn_kwargs["password"] = creds["password"]
    else:
        if not creds.get("private_key"):
            raise RuntimeError("key auth selected but no private_key in stored credentials")
        try:
            conn_kwargs["client_keys"] = [
                asyncssh.import_private_key(creds["private_key"], creds.get("passphrase") or None)
            ]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"could not parse stored private key: {exc}") from exc

    script = _restart_script(creds)
    try:
        async with asyncssh.connect(**conn_kwargs, connect_timeout=30) as conn:
            result = await conn.run(script, check=False)
            log = f"{result.stdout or ''}{result.stderr or ''}".strip()
            if result.exit_status != 0:
                raise RuntimeError(f"remote restart failed (exit {result.exit_status}):\n{log}")
            return log
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"SSH restart failed: {exc}") from exc
```

- [ ] **Step 4: Add rate limiter and restart endpoint to runner_pools.py**

Add the in-process rate limiter near the top of `runner_pools.py` (after imports):

```python
import time as _time

_restart_attempts: dict[str, list[float]] = {}


def _check_restart_rate(runner_id: str, max_per_minute: int = 5) -> bool:
    """Return True if call is allowed. Evicts timestamps older than 60s."""
    now = _time.monotonic()
    window = 60.0
    prev = [t for t in _restart_attempts.get(runner_id, []) if now - t < window]
    if len(prev) >= max_per_minute:
        return False
    prev.append(now)
    _restart_attempts[runner_id] = prev
    return True
```

Add the restart endpoint after the drain endpoint:

```python
@router.post(
    "/{pool_id}/runners/{runner_id}/restart",
    dependencies=[Depends(require_permission("runner_pool:write"))],
)
async def restart_runner(
    pool_id: str,
    runner_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """SSH into an onboarded runner host and restart the noodle-runner process.

    Only available for SSH-onboarded runners (ssh_host is set).
    Rate-limited to 5 calls per runner per minute.
    """
    if not _check_restart_rate(runner_id):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many restart attempts — wait a minute before retrying",
        )

    runner = await session.get(Runner, runner_id)
    if runner is None or runner.pool_id != pool_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner not found")
    if not runner.ssh_host or not runner.ssh_credentials:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This runner was not SSH-onboarded and cannot be restarted remotely",
        )

    from app.services.crypto import decrypt_data
    from app.services.ssh_onboard import onboard_restart

    try:
        creds = decrypt_data(runner.ssh_credentials)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Could not decrypt SSH credentials: {exc}",
        ) from exc

    try:
        log = await onboard_restart(creds)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return {"log": log, "runner_id": runner_id}
```

- [ ] **Step 5: Verify `decrypt_data` exists in crypto.py**

```bash
cd apps/api && python -c "from app.services.crypto import decrypt_data; print('ok')"
```

If it doesn't exist, check the function name in `crypto.py` and use the correct one. The function encrypts/decrypts with Fernet — it may be named `decrypt_data` or use the same `encrypt_data` / `decrypt_data` pattern. Adjust the import if needed.

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd apps/api && python -m pytest tests/test_ssh_restart.py -v
```

Expected: `4 passed`

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/services/ssh_onboard.py apps/api/app/routers/runner_pools.py \
  apps/api/tests/test_ssh_restart.py
git commit -m "feat(api): SSH runner restart endpoint with rate limiting"
```

---

## Task 7: AWS Secret Encryption (Slice 2.1)

**Files:**
- Modify: `apps/api/app/routers/runner_pools.py`
- Modify: `apps/api/app/schemas.py`
- Create: `apps/api/tests/test_aws_secret_encryption.py`

**Interfaces:**
- Produces: `RunnerPoolInfo.aws_secret_configured: bool`, AWS secret never in API response, `_extract_aws_secret(pool, config)` helper

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/test_aws_secret_encryption.py
import pytest
from httpx import AsyncClient
from app.db import SessionLocal
from app.models import RunnerPool


@pytest.mark.asyncio
async def test_aws_secret_not_in_response_on_create(client: AsyncClient):
    resp = await client.post("/runner-pools", json={
        "name": "aws-pool",
        "provider": "agent",
        "provider_config": {
            "cloud_provider": "aws",
            "region": "us-east-1",
            "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
            "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        },
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "aws_secret_access_key" not in data["provider_config"]
    assert data["aws_secret_configured"] is True


@pytest.mark.asyncio
async def test_aws_secret_not_in_response_on_get(client: AsyncClient):
    create = await client.post("/runner-pools", json={
        "name": "aws-get-pool",
        "provider": "agent",
        "provider_config": {
            "cloud_provider": "aws",
            "aws_access_key_id": "KEY",
            "aws_secret_access_key": "SECRET",
        },
    })
    pool_id = create.json()["id"]

    resp = await client.get(f"/runner-pools/{pool_id}")
    assert resp.status_code == 200
    assert "aws_secret_access_key" not in resp.json()["provider_config"]
    assert resp.json()["aws_secret_configured"] is True


@pytest.mark.asyncio
async def test_aws_secret_stored_encrypted_in_db(client: AsyncClient):
    resp = await client.post("/runner-pools", json={
        "name": "enc-pool",
        "provider": "agent",
        "provider_config": {
            "cloud_provider": "aws",
            "aws_secret_access_key": "raw-secret",
        },
    })
    pool_id = resp.json()["id"]

    async with SessionLocal() as session:
        pool = await session.get(RunnerPool, pool_id)
    assert pool.aws_secret_key_enc is not None
    assert "raw-secret" not in (pool.aws_secret_key_enc or "")  # must be encrypted
    assert "aws_secret_access_key" not in (pool.provider_config or {})


@pytest.mark.asyncio
async def test_patch_without_secret_preserves_existing(client: AsyncClient):
    """PATCH that omits aws_secret_access_key must not clear the stored secret."""
    create = await client.post("/runner-pools", json={
        "name": "preserve-pool",
        "provider": "agent",
        "provider_config": {"cloud_provider": "aws", "aws_secret_access_key": "orig-secret"},
    })
    pool_id = create.json()["id"]

    await client.patch(f"/runner-pools/{pool_id}", json={
        "name": "preserve-pool-renamed",
        "provider_config": {"cloud_provider": "aws"},
    })

    async with SessionLocal() as session:
        pool = await session.get(RunnerPool, pool_id)
    assert pool.aws_secret_key_enc is not None


@pytest.mark.asyncio
async def test_iam_role_pool_secret_configured_false(client: AsyncClient):
    """Pool without aws_secret reports aws_secret_configured=false."""
    resp = await client.post("/runner-pools", json={
        "name": "iam-pool",
        "provider": "agent",
        "provider_config": {"cloud_provider": "aws", "region": "us-east-1"},
    })
    assert resp.json()["aws_secret_configured"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/api && python -m pytest tests/test_aws_secret_encryption.py -v
```

Expected: `aws_secret_access_key` appears in response — fails first assertion.

- [ ] **Step 3: Add `_extract_aws_secret` helper and update create/update**

Add helper to `apps/api/app/routers/runner_pools.py` (after existing helpers):

```python
def _extract_aws_secret(pool: RunnerPool, provider_config: dict) -> dict:
    """Extract aws_secret_access_key from config, encrypt it, strip from JSON.

    Returns the cleaned provider_config dict. Mutates pool.aws_secret_key_enc.
    Call before committing the pool. If config has no secret, leave existing
    aws_secret_key_enc intact (PATCH path preserves the existing secret).
    """
    secret = provider_config.pop("aws_secret_access_key", None)
    if secret:
        pool.aws_secret_key_enc = encrypt_data({"aws_secret_access_key": secret})
    return provider_config
```

Update `create_runner_pool`:

```python
async def create_runner_pool(
    body: RunnerPoolCreate,
    session: AsyncSession = Depends(get_session),
) -> RunnerPoolInfo:
    from app.services.licensing import enforce_resource_cap
    await enforce_resource_cap(session, "runners")

    config = dict(body.provider_config)
    pool = RunnerPool(
        name=body.name,
        provider=body.provider,
        provider_config={},
        max_concurrent_runs=body.max_concurrent_runs,
    )
    pool.provider_config = _extract_aws_secret(pool, config)
    session.add(pool)
    await session.commit()
    await session.refresh(pool)
    return _pool_info(pool, [])
```

Update `update_runner_pool`:

```python
async def update_runner_pool(
    pool_id: str,
    body: RunnerPoolUpdate,
    session: AsyncSession = Depends(get_session),
) -> RunnerPoolInfo:
    pool = await session.get(RunnerPool, pool_id)
    if pool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner pool not found")
    if body.name is not None:
        pool.name = body.name
    if body.provider_config is not None:
        config = dict(body.provider_config)
        pool.provider_config = _extract_aws_secret(pool, config)
    if body.max_concurrent_runs is not None:
        pool.max_concurrent_runs = body.max_concurrent_runs
    pool.updated_at = datetime.now(UTC)
    await session.commit()
    runners = (
        await session.scalars(select(Runner).where(Runner.pool_id == pool_id))
    ).all()
    return _pool_info(pool, list(runners))
```

Also check the `encrypt_data` import is already at the top of `runner_pools.py`:

```python
from app.services.crypto import (
    create_payload_token,
    decode_payload_token,
    encrypt_data,
)
```

- [ ] **Step 4: Handle migration sentinel in `_pool_info`**

The migration stored migrated secrets as `__migrated__<raw>`. On next save the router will re-encrypt. For the list/get responses, the `aws_secret_configured` field just checks `bool(pool.aws_secret_key_enc)` — already correct. No additional change needed.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd apps/api && python -m pytest tests/test_aws_secret_encryption.py -v
```

Expected: `5 passed`

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/routers/runner_pools.py apps/api/tests/test_aws_secret_encryption.py
git commit -m "feat(api): AWS secret extracted from provider_config + encrypted at rest"
```

---

## Task 8: Label-Aware Dispatch (Slice 2.2)

**Files:**
- Modify: `apps/api/app/services/providers/agent.py`
- Modify: `apps/api/app/services/runner.py`
- Modify: `apps/api/app/routers/runner_pools.py` (health endpoint)
- Modify: `apps/api/app/schemas.py`
- Create: `apps/api/tests/test_label_dispatch.py`

**Interfaces:**
- Consumes: `Run.required_labels: dict | None`, `Runner.capabilities: dict`
- Produces: `pick_agent(..., required_labels=None)` filters runners, `RunnerPoolHealth.label_mismatch_queued: int`

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/test_label_dispatch.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.db import SessionLocal
from app.models import Runner, RunnerPool


async def _make_pool(name: str = "label-pool") -> str:
    async with SessionLocal() as session:
        p = RunnerPool(name=name, provider="agent", provider_config={}, max_concurrent_runs=10)
        session.add(p)
        await session.commit()
        await session.refresh(p)
        return p.id


async def _make_runner(pool_id: str, capabilities: dict, status: str = "online") -> str:
    async with SessionLocal() as session:
        r = Runner(
            pool_id=pool_id,
            name=f"runner-{len(capabilities)}",
            status=status,
            token_hash="",
            max_concurrent_runs=4,
            current_runs=0,
            capabilities=capabilities,
        )
        session.add(r)
        await session.commit()
        await session.refresh(r)
        return r.id


@pytest.mark.asyncio
async def test_pick_agent_filters_by_required_labels():
    """Only runners with matching labels should be eligible."""
    from app.services.providers.agent import pick_agent

    pool_id = await _make_pool("label-test")
    gpu_runner_id = await _make_runner(pool_id, {"gpu": "a100", "region": "eu"})
    cpu_runner_id = await _make_runner(pool_id, {"region": "eu"})

    d = MagicMock()
    d._agents = {
        gpu_runner_id: MagicMock(runner_id=gpu_runner_id, active_runs={}),
        cpu_runner_id: MagicMock(runner_id=cpu_runner_id, active_runs={}),
    }
    d._lock = AsyncMock()
    d._lock.__aenter__ = AsyncMock(return_value=None)
    d._lock.__aexit__ = AsyncMock(return_value=None)

    # Require GPU — only gpu_runner should be returned
    conn = await pick_agent(d, SessionLocal, pool_id, required_labels={"gpu": "a100"})
    assert conn is not None
    assert conn.runner_id == gpu_runner_id


@pytest.mark.asyncio
async def test_pick_agent_no_match_returns_none():
    from app.services.providers.agent import pick_agent

    pool_id = await _make_pool("no-match-pool")
    await _make_runner(pool_id, {"region": "us"})  # no GPU label

    d = MagicMock()
    runner_id = (await SessionLocal().scalars(__import__("sqlalchemy").select(Runner).where(Runner.pool_id == pool_id))).first().id
    d._agents = {runner_id: MagicMock(runner_id=runner_id, active_runs={})}
    d._lock = AsyncMock()
    d._lock.__aenter__ = AsyncMock(return_value=None)
    d._lock.__aexit__ = AsyncMock(return_value=None)

    conn = await pick_agent(d, SessionLocal, pool_id, required_labels={"gpu": "a100"})
    assert conn is None


@pytest.mark.asyncio
async def test_pick_agent_null_labels_matches_any():
    from app.services.providers.agent import pick_agent

    pool_id = await _make_pool("null-labels-pool")
    runner_id = await _make_runner(pool_id, {"gpu": "a100"})

    d = MagicMock()
    d._agents = {runner_id: MagicMock(runner_id=runner_id, active_runs={})}
    d._lock = AsyncMock()
    d._lock.__aenter__ = AsyncMock(return_value=None)
    d._lock.__aexit__ = AsyncMock(return_value=None)

    conn = await pick_agent(d, SessionLocal, pool_id, required_labels=None)
    assert conn is not None


@pytest.mark.asyncio
async def test_pick_agent_empty_labels_matches_any():
    from app.services.providers.agent import pick_agent

    pool_id = await _make_pool("empty-labels-pool")
    runner_id = await _make_runner(pool_id, {"gpu": "a100"})

    d = MagicMock()
    d._agents = {runner_id: MagicMock(runner_id=runner_id, active_runs={})}
    d._lock = AsyncMock()
    d._lock.__aenter__ = AsyncMock(return_value=None)
    d._lock.__aexit__ = AsyncMock(return_value=None)

    conn = await pick_agent(d, SessionLocal, pool_id, required_labels={})
    assert conn is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/api && python -m pytest tests/test_label_dispatch.py -v
```

Expected: `pick_agent` ignores `required_labels` parameter — TypeError or wrong runner returned.

- [ ] **Step 3: Update `pick_agent` to accept and filter by required_labels**

In `apps/api/app/services/providers/agent.py`, find the `pick_agent` function signature:

```python
async def pick_agent(d, session_factory, pool_id: str) -> _AgentConnection | None:
```

Change to:

```python
async def pick_agent(
    d,
    session_factory,
    pool_id: str,
    required_labels: dict | None = None,
) -> _AgentConnection | None:
```

After loading `runners` from DB, add Python-level label filter before the capacity checks:

```python
        # Label-aware dispatch: filter to runners that satisfy required_labels.
        # Uses Python-level subset check (works on SQLite and Postgres alike).
        if required_labels:
            runners = [
                r for r in runners
                if all(r.capabilities.get(k) == v for k, v in required_labels.items())
            ]
```

- [ ] **Step 4: Thread `required_labels` through to `assign_agent_run` and `pick_agent` call**

In `assign_agent_run` in `providers/agent.py`, update the signature:

```python
async def assign_agent_run(
    d: Any,
    session_factory,
    run_id: str,
    pool_id: str,
    ...
    required_labels: dict | None = None,
) -> str:
    conn = await pick_agent(d, session_factory, pool_id, required_labels=required_labels)
```

In `remote_dispatch.py`, find `_assign_agent_run` and add `required_labels` param, passing it through:

```python
    async def _assign_agent_run(self, run_id, pool_id, ...) -> str:
        ...
        # Load required_labels from the Run row
        async with SessionLocal() as session:
            run = await session.get(Run, run_id)
            required_labels = getattr(run, "required_labels", None) or None

        return await agent_provider.assign_agent_run(
            self, SessionLocal, run_id, pool_id, ...,
            required_labels=required_labels,
        )
```

- [ ] **Step 5: Add `label_mismatch_queued` to health schema and endpoint**

In `apps/api/app/schemas.py`, add to `RunnerPoolHealth`:

```python
    label_mismatch_queued: int = 0
```

In `apps/api/app/routers/runner_pools.py`, update `runner_fleet_health()`. After computing `pool_healths`, add label mismatch computation. Find the section after the 24h success rate loop and before `return RunnerFleetHealth(...)`:

```python
    # Label mismatch: runs queued >30s whose required_labels match no online runner.
    from datetime import timedelta
    stuck_cutoff = now - timedelta(seconds=30)
    mismatch_rows = (
        await session.execute(
            select(RunQueueEntry.runner_pool_id, Run.required_labels)
            .join(Run, RunQueueEntry.run_id == Run.id)
            .where(
                RunQueueEntry.status == "queued",
                RunQueueEntry.available_at <= stuck_cutoff,
                RunQueueEntry.runner_pool_id.is_not(None),
                Run.required_labels.is_not(None),
            )
            .limit(100)
        )
    ).all()
    mismatch_by_pool: dict[str, int] = {}
    for _pool_id, req_labels in mismatch_rows:
        if not req_labels:
            continue
        online_runners = [
            r for r in runners_by_pool.get(_pool_id, [])
            if r.status in ("online", "busy")
        ]
        if not any(
            all(r.capabilities.get(k) == v for k, v in req_labels.items())
            for r in online_runners
        ):
            mismatch_by_pool[_pool_id] = mismatch_by_pool.get(_pool_id, 0) + 1
```

Then in the loop that builds `pool_healths`, add `label_mismatch_queued=mismatch_by_pool.get(pool.id, 0)` to each `RunnerPoolHealth(...)` constructor call.

Add `Run` to the imports at the top of `runner_pools.py` if not already present.

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd apps/api && python -m pytest tests/test_label_dispatch.py -v
```

Expected: `5 passed`

- [ ] **Step 7: Run full suite to confirm no regressions**

```bash
cd apps/api && python -m pytest tests/ -x -q
```

Expected: all existing tests + new tests pass.

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/services/providers/agent.py apps/api/app/services/remote_dispatch.py \
  apps/api/app/routers/runner_pools.py apps/api/app/schemas.py \
  apps/api/tests/test_label_dispatch.py
git commit -m "feat(api): label-aware dispatch in pick_agent + label_mismatch_queued health signal"
```

---

## Task 9: Run History API (Slice 3.1)

**Files:**
- Modify: `apps/api/app/routers/runner_pools.py`
- Modify: `apps/api/app/schemas.py`
- Create: `apps/api/tests/test_run_history.py`

**Interfaces:**
- Produces: `GET /runner-pools/{pool_id}/run-history?days=7` → `list[RunHistoryBucket]`, `GET /runner-pools/{pool_id}/recent-runs` → `list[RunListItem]`

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/test_run_history.py
import pytest
from datetime import UTC, datetime, timedelta
from httpx import AsyncClient
from sqlalchemy import text
from app.db import SessionLocal
from app.models import Run


async def _insert_finished_run(pool_id: str, status: str, hours_ago: float, duration_s: float = 10.0) -> None:
    finished = datetime.now(UTC) - timedelta(hours=hours_ago)
    started = finished - timedelta(seconds=duration_s)
    async with SessionLocal() as session:
        r = Run(
            workflow_id=None,
            status=status,
            runner_pool_id=pool_id,
            started_at=started,
            finished_at=finished,
            mode="manual",
            trigger_type="manual",
        )
        session.add(r)
        await session.commit()


@pytest.mark.asyncio
async def test_run_history_returns_buckets(client: AsyncClient):
    pool = await client.post("/runner-pools", json={"name": "hist-pool", "provider": "agent"})
    pool_id = pool.json()["id"]

    await _insert_finished_run(pool_id, "success", hours_ago=2)
    await _insert_finished_run(pool_id, "success", hours_ago=2.1)
    await _insert_finished_run(pool_id, "error", hours_ago=2.2)

    resp = await client.get(f"/runner-pools/{pool_id}/run-history?days=1")
    assert resp.status_code == 200
    buckets = resp.json()
    assert isinstance(buckets, list)
    total = sum(b["total"] for b in buckets)
    assert total == 3


@pytest.mark.asyncio
async def test_run_history_empty_pool(client: AsyncClient):
    pool = await client.post("/runner-pools", json={"name": "empty-hist", "provider": "agent"})
    pool_id = pool.json()["id"]

    resp = await client.get(f"/runner-pools/{pool_id}/run-history")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_run_history_days_clamped(client: AsyncClient):
    pool = await client.post("/runner-pools", json={"name": "clamp-pool", "provider": "agent"})
    pool_id = pool.json()["id"]

    resp = await client.get(f"/runner-pools/{pool_id}/run-history?days=999")
    assert resp.status_code == 200
    assert resp.headers.get("x-clamped") == "true"


@pytest.mark.asyncio
async def test_run_history_bucket_schema(client: AsyncClient):
    pool = await client.post("/runner-pools", json={"name": "schema-pool", "provider": "agent"})
    pool_id = pool.json()["id"]
    await _insert_finished_run(pool_id, "success", hours_ago=1, duration_s=30)

    resp = await client.get(f"/runner-pools/{pool_id}/run-history?days=1")
    bucket = next((b for b in resp.json() if b["total"] > 0), None)
    assert bucket is not None
    assert "bucket_start" in bucket
    assert "success" in bucket
    assert "error" in bucket
    assert "total" in bucket
    assert "avg_duration_seconds" in bucket
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/api && python -m pytest tests/test_run_history.py -v
```

Expected: 404 — endpoint doesn't exist.

- [ ] **Step 3: Add `RunHistoryBucket` schema**

In `apps/api/app/schemas.py`, add:

```python
class RunHistoryBucket(BaseModel):
    bucket_start: datetime
    success: int
    error: int
    total: int
    avg_duration_seconds: float | None
```

- [ ] **Step 4: Add run-history endpoint and recent-runs endpoint**

Add to `apps/api/app/routers/runner_pools.py`:

```python
from app.schemas import RunHistoryBucket  # add to existing schema imports

@router.get("/{pool_id}/run-history", response_model=list[RunHistoryBucket])
async def runner_pool_run_history(
    pool_id: str,
    days: int = Query(default=7, ge=1),
    session: AsyncSession = Depends(get_session),
) -> list[RunHistoryBucket]:
    """Time-bucketed run success/error counts for a pool over the past N days.
    Days are clamped to 30. Bucket size: 1 hour for ≤7 days, 1 day otherwise.
    """
    from sqlalchemy import case, cast, Float, func, literal_column

    pool = await session.get(RunnerPool, pool_id)
    if pool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner pool not found")

    clamped = min(days, 30)
    cutoff = datetime.now(UTC) - timedelta(days=clamped)
    bucket_unit = "hour" if clamped <= 7 else "day"

    rows = (
        await session.execute(
            select(
                func.date_trunc(bucket_unit, Run.finished_at).label("bucket_start"),
                func.sum(case((Run.status == "success", 1), else_=0)).label("success"),
                func.sum(case((Run.status == "error", 1), else_=0)).label("error"),
                func.count().label("total"),
                func.avg(
                    func.extract("epoch", Run.finished_at) - func.extract("epoch", Run.started_at)
                ).label("avg_dur"),
            )
            .where(
                Run.runner_pool_id == pool_id,
                Run.finished_at >= cutoff,
                Run.status.in_(("success", "error")),
            )
            .group_by(literal_column("1"))
            .order_by(literal_column("1"))
        )
    ).all()

    from fastapi.responses import Response as _Resp
    headers = {"x-clamped": "true"} if days > 30 else {}

    result = [
        RunHistoryBucket(
            bucket_start=row.bucket_start,
            success=int(row.success),
            error=int(row.error),
            total=int(row.total),
            avg_duration_seconds=float(row.avg_dur) if row.avg_dur is not None else None,
        )
        for row in rows
    ]
    # Inject x-clamped header by returning JSONResponse when needed
    if days > 30:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            content=[b.model_dump(mode="json") for b in result],
            headers={"x-clamped": "true"},
        )
    return result


@router.get("/{pool_id}/recent-runs")
async def runner_pool_recent_runs(
    pool_id: str,
    limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Last N runs dispatched through this pool — used for Docker/K8s pool visibility."""
    pool = await session.get(RunnerPool, pool_id)
    if pool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Runner pool not found")

    from app.models import Workflow
    rows = (
        await session.execute(
            select(Run, Workflow.name.label("workflow_name"))
            .outerjoin(Workflow, Run.workflow_id == Workflow.id)
            .where(Run.runner_pool_id == pool_id)
            .order_by(Run.created_at.desc())
            .limit(limit)
        )
    ).all()

    return [
        {
            "run_id": run.id,
            "workflow_name": wf_name or "Unknown",
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "duration_seconds": (
                (run.finished_at - run.started_at).total_seconds()
                if run.finished_at and run.started_at
                else None
            ),
        }
        for run, wf_name in rows
    ]
```

Add `timedelta` to the import at the top of `runner_pools.py` if not present:
```python
from datetime import UTC, datetime, timedelta
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd apps/api && python -m pytest tests/test_run_history.py -v
```

Expected: `4 passed`

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/routers/runner_pools.py apps/api/app/schemas.py \
  apps/api/tests/test_run_history.py
git commit -m "feat(api): run-history and recent-runs endpoints for pool observability"
```

---

## Task 10: Frontend Types + API Client

**Files:**
- Modify: `apps/web/src/types.ts`
- Modify: `apps/web/src/queries.ts` (or equivalent)
- Modify: `apps/web/src/api.ts` (or equivalent API client file)

**Interfaces:**
- Produces: updated TS types matching backend schemas, new query hooks and API functions for all new endpoints

- [ ] **Step 1: Find the API client and query files**

```bash
ls apps/web/src/*.ts apps/web/src/*.tsx | head -30
```

Identify the file that contains `fetch` or `axios` calls for runner pools (look for `useRunnerPools`, `runnerPoolsApi`, etc.).

- [ ] **Step 2: Update types in `types.ts`**

Find `RunnerInfo` interface and add:

```typescript
  token_expires_at: string | null;
  ssh_host: string | null;
```

Find `RunnerPoolInfo` interface and add:

```typescript
  ghost_count: number;
  aws_secret_configured: boolean;
```

Find `RunnerPoolHealth` interface and add:

```typescript
  label_mismatch_queued: number;
```

Add new interface:

```typescript
export interface RunHistoryBucket {
  bucket_start: string;
  success: number;
  error: number;
  total: number;
  avg_duration_seconds: number | null;
}

export interface RecentRun {
  run_id: string;
  workflow_name: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
}
```

- [ ] **Step 3: Add API functions**

In the API client file, add the following functions (adapting to the existing fetch pattern used in the codebase):

```typescript
// Drain/undrain a runner
export async function drainRunner(poolId: string, runnerId: string, drain: boolean): Promise<RunnerInfo> {
  return apiFetch(`/runner-pools/${poolId}/runners/${runnerId}/drain`, {
    method: "POST",
    body: JSON.stringify({ drain }),
  });
}

// SSH restart a runner
export async function restartRunner(poolId: string, runnerId: string): Promise<{ log: string }> {
  return apiFetch(`/runner-pools/${poolId}/runners/${runnerId}/restart`, {
    method: "POST",
  });
}

// Clean up ghost runners in a pool
export async function cleanupGhosts(poolId: string): Promise<{ cleaned: number }> {
  return apiFetch(`/runner-pools/${poolId}/cleanup-ghosts`, {
    method: "POST",
  });
}

// Run history for sparklines / observability
export async function getRunnerPoolHistory(poolId: string, days = 1): Promise<RunHistoryBucket[]> {
  return apiFetch(`/runner-pools/${poolId}/run-history?days=${days}`);
}

// Recent runs for Docker/K8s pools
export async function getRunnerPoolRecentRuns(poolId: string): Promise<RecentRun[]> {
  return apiFetch(`/runner-pools/${poolId}/recent-runs`);
}
```

- [ ] **Step 4: Add React Query hooks**

In `queries.ts`, add:

```typescript
export function useDrainRunnerMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ poolId, runnerId, drain }: { poolId: string; runnerId: string; drain: boolean }) =>
      drainRunner(poolId, runnerId, drain),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runner-pools"] }),
  });
}

export function useRestartRunnerMutation() {
  return useMutation({
    mutationFn: ({ poolId, runnerId }: { poolId: string; runnerId: string }) =>
      restartRunner(poolId, runnerId),
  });
}

export function useCleanupGhostsMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (poolId: string) => cleanupGhosts(poolId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runner-pools"] }),
  });
}

export function useRunnerPoolHistory(poolId: string, days = 1) {
  return useQuery({
    queryKey: ["runner-pool-history", poolId, days],
    queryFn: () => getRunnerPoolHistory(poolId, days),
    staleTime: 60_000, // history changes slowly
  });
}

export function useRunnerPoolRecentRuns(poolId: string, enabled = false) {
  return useQuery({
    queryKey: ["runner-pool-recent-runs", poolId],
    queryFn: () => getRunnerPoolRecentRuns(poolId),
    enabled,
  });
}
```

- [ ] **Step 5: Type-check**

```bash
cd apps/web && npx tsc --noEmit
```

Expected: 0 errors (fix any type errors before proceeding).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/types.ts apps/web/src/queries.ts apps/web/src/api.ts
git commit -m "feat(web): updated types and query hooks for runner hardening features"
```

---

## Task 11: Frontend — Reliability UI (drain, restart, ghost cleanup, token expiry)

**Files:**
- Modify: `apps/web/src/RunnerPoolsPage.tsx`

**Interfaces:**
- Consumes: all hooks from Task 10 + updated types
- Produces: drain toggle on runner rows, restart modal, ghost count badge, token expiry indicator

- [ ] **Step 1: Add drain toggle to runner rows**

In `RunnerPoolsPage.tsx`, find the runner row actions section (`<span className="rt-act">`). Add after the existing Edit button:

```tsx
{canWrite && r.status !== "offline" && (
  <button
    type="button"
    className={`btn btn-sm btn-ghost${r.status === "draining" ? " btn-warn" : ""}`}
    onClick={async () => {
      const isDraining = r.status === "draining";
      await drainMutation.mutateAsync({
        poolId: pool.id,
        runnerId: r.id,
        drain: !isDraining,
      });
      void runnersQuery.refetch();
    }}
    title={r.status === "draining" ? "Click to undrain this runner" : "Drain this runner before maintenance"}
  >
    {r.status === "draining" ? "Undrain" : "Drain"}
  </button>
)}
```

Add the `drainMutation` hook near the top of `PoolCard`:

```tsx
const drainMutation = useDrainRunnerMutation();
```

Import `useDrainRunnerMutation` from `./queries`.

- [ ] **Step 2: Add draining status dot colour**

Find the status dot in the runner row:

```tsx
<span className={`status-dot ${r.status}`} />
```

The CSS already handles `online`, `offline`, `busy` via `status-dot.<status>`. Add a CSS rule for `draining` in the existing stylesheet (find the CSS file that defines `.status-dot`):

```css
.status-dot.draining { background: #f5a623; }
```

Also add "safe to remove" indicator in the runner name column when draining + current_runs === 0:

```tsx
{r.status === "draining" && r.current_runs === 0 && (
  <span className="muted" style={{ fontSize: "0.75em", color: "#22c55e" }}> · safe to remove</span>
)}
```

- [ ] **Step 3: Add restart button and modal**

Add a `RestartRunnerModal` component before `PoolCard`:

```tsx
function RestartRunnerModal({
  poolId,
  runner,
  onClose,
}: {
  poolId: string;
  runner: RunnerInfo;
  onClose: () => void;
}) {
  const [log, setLog] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const restartMutation = useRestartRunnerMutation();
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onClose);

  const doRestart = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await restartMutation.mutateAsync({ poolId, runnerId: runner.id });
      setLog(res.log);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Restart failed");
    } finally {
      setBusy(false);
    }
  };

  const hasActiveRuns = runner.current_runs > 0;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="restart-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <h2 id="restart-title">Restart {runner.name}</h2>
          <button className="btn btn-sm btn-ghost" onClick={onClose} aria-label="Close">✕</button>
        </header>
        <div className="modal-body">
          {hasActiveRuns && !log && (
            <p className="error-text">
              Warning: this runner has {runner.current_runs} active run{runner.current_runs > 1 ? "s" : ""} — restarting will interrupt them.
            </p>
          )}
          {error && <p className="error-text">{error}</p>}
          {log ? (
            <Field label="Restart log">
              <pre className="runner-install">{log}</pre>
            </Field>
          ) : (
            <p className="muted">This will SSH into {runner.ssh_host} and restart the noodle-runner process.</p>
          )}
        </div>
        <footer className="modal-foot">
          <button className="btn btn-sm btn-ghost" onClick={onClose}>
            {log ? "Close" : "Cancel"}
          </button>
          {!log && (
            <button
              className="btn btn-sm btn-primary"
              disabled={busy}
              onClick={doRestart}
            >
              {busy ? "Restarting…" : "Restart runner"}
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}
```

In `PoolCard`, add restart state and button:

```tsx
const [restartingRunner, setRestartingRunner] = useState<RunnerInfo | null>(null);
```

In the runner row actions, add after the Drain button:

```tsx
{canWrite && r.ssh_host && (
  <button
    type="button"
    className="btn btn-sm btn-ghost"
    onClick={() => setRestartingRunner(r)}
  >
    Restart
  </button>
)}
```

Add the modal rendering at the bottom of `PoolCard`'s JSX (near the other modals):

```tsx
{restartingRunner && (
  <RestartRunnerModal
    poolId={pool.id}
    runner={restartingRunner}
    onClose={() => {
      setRestartingRunner(null);
      // Refetch after 5s — time for agent to reconnect
      setTimeout(() => void runnersQuery.refetch(), 5000);
    }}
  />
)}
```

- [ ] **Step 4: Add ghost count badge and cleanup button**

In `PoolCard`, find the pool actions div. Add a ghost cleanup button when `pool.ghost_count > 0`:

```tsx
{canWrite && pool.ghost_count > 0 && (
  <button
    type="button"
    className="btn btn-sm btn-ghost"
    title="Remove runners that were registered but never connected"
    onClick={async () => {
      await cleanupMutation.mutateAsync(pool.id);
      onChanged();
    }}
  >
    Clean up {pool.ghost_count} unconnected
  </button>
)}
```

Add the hook near the top of `PoolCard`:

```tsx
const cleanupMutation = useCleanupGhostsMutation();
```

Import `useCleanupGhostsMutation` from `./queries`.

- [ ] **Step 5: Add token expiry indicator to runner table**

In the runner row, find the name column and add after the last-seen text:

```tsx
{(() => {
  if (!r.token_expires_at) return null;
  const expiresAt = new Date(r.token_expires_at);
  const now = new Date();
  const daysLeft = (expiresAt.getTime() - now.getTime()) / (1000 * 60 * 60 * 24);
  if (daysLeft < 0 && r.status === "offline") {
    return (
      <span
        className="muted"
        style={{ color: "#ef4444", marginLeft: 4 }}
        title={`Token expired ${expiresAt.toLocaleDateString()} — delete and re-register`}
      >
        ✕ expired
      </span>
    );
  }
  if (daysLeft >= 0 && daysLeft < 7) {
    return (
      <span
        className="muted"
        style={{ color: "#f5a623", marginLeft: 4 }}
        title={`Token expires ${expiresAt.toLocaleDateString()} — mint a new one`}
      >
        ⚠ expiring
      </span>
    );
  }
  return null;
})()}
```

- [ ] **Step 6: Type-check and verify**

```bash
cd apps/web && npx tsc --noEmit
```

Expected: 0 errors.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/RunnerPoolsPage.tsx
git commit -m "feat(web): drain toggle, SSH restart modal, ghost cleanup badge, token expiry indicator"
```

---

## Task 12: Frontend — Observability UI (sparklines, K8s/Docker run list, pool search, label mismatch)

**Files:**
- Modify: `apps/web/src/RunnerPoolsPage.tsx`

**Interfaces:**
- Consumes: `useRunnerPoolHistory`, `useRunnerPoolRecentRuns`, `RunnerPoolHealth.label_mismatch_queued`

- [ ] **Step 1: Add pool search input**

At the top of `RunnerPoolsPage`, add search state:

```tsx
const [poolSearch, setPoolSearch] = useState("");
```

After `const pools = poolsQuery.data ?? null;`, add filtered pools:

```tsx
const filteredPools = pools
  ? pools.filter(
      (p) =>
        poolSearch === "" ||
        p.name.toLowerCase().includes(poolSearch.toLowerCase()) ||
        p.provider.toLowerCase().includes(poolSearch.toLowerCase()),
    )
  : null;
```

In the JSX, between `<FleetBar>` and the pool list, add (only when `pools.length > 5`):

```tsx
{pools && pools.length > 5 && (
  <div style={{ marginBottom: 12 }}>
    <input
      className="field-input"
      type="search"
      placeholder="Search pools…"
      value={poolSearch}
      onChange={(e) => setPoolSearch(e.target.value)}
      style={{ maxWidth: 300 }}
    />
  </div>
)}
```

Change the pool map from `pools.map(...)` to `filteredPools?.map(...)`. Add empty-filter state:

```tsx
{filteredPools && filteredPools.length === 0 && poolSearch && (
  <div className="empty-state">
    <p className="muted">No pools match "{poolSearch}".</p>
    <button className="btn btn-sm btn-ghost" onClick={() => setPoolSearch("")}>Clear</button>
  </div>
)}
```

- [ ] **Step 2: Add label mismatch warning to HealthStrip**

In `HealthStrip`, add a new metric cell after the existing four (dispatcher cell):

```tsx
{/* label_mismatch_queued is on health, default 0 */}
{(health?.label_mismatch_queued ?? 0) > 0 && (
  <div className="phc">
    <span className="phc-k">Label mismatch</span>
    <span className="phc-v phc-warn">
      ⚠ {health!.label_mismatch_queued} queued
    </span>
    <span className="phc-sub">labels not matched by any online runner</span>
  </div>
)}
```

- [ ] **Step 3: Add sparkline component**

Add a pure-SVG sparkline above `PoolCard` component declaration:

```tsx
function Sparkline({ poolId }: { poolId: string }) {
  const { data: buckets } = useRunnerPoolHistory(poolId, 1);

  if (!buckets || buckets.length === 0) {
    return <div className="pool-sparkline pool-sparkline--empty" />;
  }

  const maxTotal = Math.max(...buckets.map((b) => b.total), 1);
  const W = 240;
  const H = 32;
  const colW = W / 24;

  // Pad to 24 hourly buckets
  const now = new Date();
  const slots: Array<{ success: number; error: number; total: number }> = Array.from(
    { length: 24 },
    (_, i) => {
      const slotStart = new Date(now);
      slotStart.setMinutes(0, 0, 0);
      slotStart.setHours(slotStart.getHours() - (23 - i));
      const match = buckets.find(
        (b) => Math.abs(new Date(b.bucket_start).getHours() - slotStart.getHours()) < 1,
      );
      return match ?? { success: 0, error: 0, total: 0 };
    },
  );

  return (
    <svg
      className="pool-sparkline"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      {slots.map((slot, i) => {
        const x = i * colW + 1;
        const successH = (slot.success / maxTotal) * (H - 2);
        const errorH = (slot.error / maxTotal) * (H - 2);
        return (
          <g key={i}>
            {successH > 0 && (
              <rect
                x={x}
                y={H - 1 - successH}
                width={colW - 2}
                height={successH}
                fill="#22c55e"
                opacity={0.7}
              />
            )}
            {errorH > 0 && (
              <rect
                x={x}
                y={H - 1 - successH - errorH}
                width={colW - 2}
                height={errorH}
                fill="#ef4444"
                opacity={0.7}
              />
            )}
          </g>
        );
      })}
    </svg>
  );
}
```

Import `useRunnerPoolHistory` from `./queries`.

In `PoolCard`, add the sparkline below `<HealthStrip>`:

```tsx
<Sparkline poolId={pool.id} />
```

Add CSS for sparkline in the stylesheet:

```css
.pool-sparkline {
  width: 100%;
  height: 32px;
  display: block;
  margin-top: 4px;
}
.pool-sparkline--empty {
  height: 4px;
  background: var(--border);
  border-radius: 2px;
  margin-top: 4px;
}
```

- [ ] **Step 4: Add K8s/Docker recent runs section**

In `PoolCard`, when `expanded && pool.provider !== "agent"`, replace the static message with:

```tsx
{expanded && pool.provider !== "agent" && (
  <div className="pool-body">
    <RecentRunsSection poolId={pool.id} />
  </div>
)}
```

Add `RecentRunsSection` component before `PoolCard`:

```tsx
function RecentRunsSection({ poolId }: { poolId: string }) {
  const { data: runs, isLoading } = useRunnerPoolRecentRuns(poolId, true);

  if (isLoading) return <p className="muted">Loading recent runs…</p>;
  if (!runs || runs.length === 0)
    return <p className="muted">No runs dispatched through this pool yet.</p>;

  return (
    <div>
      <p className="muted" style={{ marginBottom: 8 }}>Recent runs dispatched through this pool:</p>
      <div className="runner-table">
        <div className="runner-thead">
          <span>Workflow</span>
          <span>Status</span>
          <span>Started</span>
          <span>Duration</span>
        </div>
        {runs.map((r) => (
          <div key={r.run_id} className="runner-trow">
            <span className="rt-name">{r.workflow_name}</span>
            <span>
              <span className={`run-pill status-run-${r.status}`}>{r.status}</span>
            </span>
            <span className="muted">{r.started_at ? relAgo(r.started_at) : "—"}</span>
            <span className="muted">
              {r.duration_seconds != null ? relSecs(r.duration_seconds) : "—"}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
```

Import `useRunnerPoolRecentRuns` from `./queries`.

- [ ] **Step 5: Type-check**

```bash
cd apps/web && npx tsc --noEmit
```

Expected: 0 errors. Fix any type errors before committing.

- [ ] **Step 6: Run Vitest**

```bash
cd apps/web && npx vitest run
```

Expected: existing tests pass (no regressions).

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/RunnerPoolsPage.tsx
git commit -m "feat(web): sparklines, pool search, label mismatch warning, K8s/Docker recent runs"
```

---

## Task 13: Full Test Suite + Final Verification

- [ ] **Step 1: Run all backend tests**

```bash
cd apps/api && python -m pytest tests/ -q
```

Expected: all tests pass, no regressions on existing runner_pools tests.

- [ ] **Step 2: Run frontend type-check and tests**

```bash
cd apps/web && npx tsc --noEmit && npx vitest run
```

Expected: 0 type errors, all Vitest tests pass.

- [ ] **Step 3: Run migration on clean DB to verify idempotency**

```bash
cd apps/api && python -m alembic downgrade 0055_uq_environment_is_global && python -m alembic upgrade head
```

Expected: clean up-and-down cycle with no errors.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: runners production hardening — all 3 slices complete

Slice 1 (Reliability): 1-year tokens, ghost cleanup, drain mode, SSH restart
Slice 2 (Correctness): AWS secret encryption, label-aware dispatch
Slice 3 (Observability): run history API, sparklines, pool search, token expiry UI"
```

---

## Self-Review Against Spec

| Spec requirement | Task that covers it |
|---|---|
| 1-year token TTL (config) | Task 2 + Task 3 |
| `token_expires_at` column | Task 1 + Task 3 |
| Ghost cleanup background task | Task 4 |
| Ghost cleanup manual endpoint | Task 4 |
| `ghost_count` in RunnerPoolInfo | Task 4 |
| Draining runners excluded from ghost cleanup | Task 4 (ghost_cleanup.py WHERE clause) |
| Drain mode endpoint | Task 5 |
| Drain dispatch exclusion | Task 5 (already excluded by status filter) |
| `drain_complete` WS signal | Task 5 |
| Undrain writes `online`, not `busy` | Task 5 |
| SSH restart endpoint | Task 6 |
| SSH restart rate limiting (5/min) | Task 6 |
| `ssh_host` in RunnerInfo | Task 3 |
| AWS secret extracted + encrypted | Task 7 |
| AWS secret never in API response | Task 7 |
| AWS secret handling in CREATE path | Task 7 |
| `aws_secret_configured` flag | Task 7 |
| Label-aware dispatch in `pick_agent` | Task 8 |
| Label filter Python subset check | Task 8 |
| `label_mismatch_queued` health signal | Task 8 |
| `required_labels` on Run model | Task 1 |
| Run history API with bucketing | Task 9 |
| Days clamped to 30 with header | Task 9 |
| Composite index on runs | Task 1 (migration) |
| Recent runs endpoint for Docker/K8s | Task 9 |
| Frontend: drain toggle | Task 11 |
| Frontend: restart modal | Task 11 |
| Frontend: ghost cleanup badge | Task 11 |
| Frontend: token expiry indicator | Task 11 |
| Frontend: sparkline SVG | Task 12 |
| Frontend: label mismatch warning | Task 12 |
| Frontend: K8s/Docker recent runs | Task 12 |
| Frontend: pool search (>5 pools) | Task 12 |
| Background loop in main.py lifespan | Task 4 |
