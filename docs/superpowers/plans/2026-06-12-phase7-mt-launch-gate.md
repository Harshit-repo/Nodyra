# Phase 7: Multi-Tenancy Launch Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two remaining MT launch blockers — C2 (cross-org background-loop correctness tests) and C3 (httpOnly cookie sessions + CSRF double-submit + CSP + one-time WS tickets).

**Architecture:**  
- **C2** is purely additive: a new test module verifying each background loop (retention, queue dispatch, scheduler, heartbeats) processes ALL orgs under `run_as_system()` and is silently org-filtered without it. A static guard test asserts every loop start-site in `main.py` wraps `_as_system()`.  
- **C3** adds dual-mode auth (httpOnly cookie OR Bearer both accepted everywhere), a CSRF double-submit gate that only fires on cookie-auth mutating requests, a one-time Redis-backed WS ticket to replace the token-in-URL pattern, and a `<meta>` CSP tag on the SPA. Bearer auth is preserved for runners and backward-compat API consumers.

**Tech Stack:** FastAPI, pytest-asyncio, httpx AsyncClient, SQLAlchemy async, Pydantic, Redis (aioredis), React 18 + Vite, TypeScript.

**Validation commands:**
```powershell
# Backend
cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests -q
# Frontend
cd apps\web; npm run typecheck; npm run test; npm run build
# E2E smoke
cd apps\web; npm run test:e2e
```

---

## File map

| File | Action | Purpose |
|------|--------|---------|
| `apps/api/tests/test_cross_org_loops.py` | Create | C2: all five cross-org loop tests |
| `apps/api/tests/conftest.py` | Modify | Patch `queue` module `SessionLocal` (2 lines) |
| `apps/api/app/config.py` | Modify | Five new C3 config keys |
| `apps/api/app/services/ws_ticket.py` | Create | One-time WS upgrade tickets (Redis + in-memory fallback) |
| `apps/api/app/schemas.py` | Modify | `WsTicketResponse` schema |
| `apps/api/app/security.py` | Modify | `_extract_token` helper; cookie fallback in all auth deps |
| `apps/api/app/main.py` | Modify | `auth_gate` accepts cookie; new `_csrf_gate` middleware |
| `apps/api/app/routers/auth.py` | Modify | Login/register set cookies; `POST /auth/logout`; `POST /auth/ws-ticket` |
| `apps/api/app/routers/runs.py` | Modify | WS handler: `?ticket=` preferred, `?token=` deprecated fallback |
| `apps/api/tests/test_cookie_auth.py` | Create | C3 backend tests (10 cases) |
| `apps/web/src/api.ts` | Modify | CSRF header; remove `setToken` from login; async WS ticket URL; `logout()` |
| `apps/web/src/LoginPage.tsx` | Modify | Remove `setToken(result.token)` call |
| `apps/web/src/App.tsx` | Modify | `signOut()` calls server logout endpoint |
| `apps/web/index.html` | Modify | CSP `<meta>` tag |

---

## Track A — C2: Cross-org background-loop tests

### Task 1: Conftest patch for queue module + `mt_enabled` fixture

**Files:**
- Modify: `apps/api/tests/conftest.py`

The queue service is the only background-loop module NOT already patched in conftest. Tests that call `queue.py` functions directly will otherwise hit the real DB.

- [x] **Step 1: Add queue module to conftest imports and patch map**

In `apps/api/tests/conftest.py`, add after the existing `import app.services.subworkflows` line:

```python
import app.services.queue as queue_module
```

In the `client` fixture, add `queue_module` to the `originals` dict and the patch block:

```python
# In originals dict:
queue_module: queue_module.SessionLocal,

# In the patch block (same group as retention_module):
queue_module.SessionLocal = test_session
```

And in the restore loop at the end of the fixture, `queue_module` is already covered by `for module, original in originals.items()`.

- [x] **Step 2: Verify suite still passes**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests -q`
Expected: same pass/fail counts as before (this is a non-behavioral change).

- [x] **Step 3: Commit**

```bash
git add apps/api/tests/conftest.py
git commit -m "test(conftest): patch queue.SessionLocal in client fixture for MT loop tests"
```

---

### Task 2: C2 — Retention cross-org test

**Files:**
- Create: `apps/api/tests/test_cross_org_loops.py`

- [x] **Step 1: Create the test file with retention test**

```python
# apps/api/tests/test_cross_org_loops.py
"""C2 — Cross-org background-loop correctness tests.

Each test verifies two things:
  1. Without run_as_system(): the loop silently skips non-default-org data
     (the isolation gap that would silently skip tenants if the _as_system
     wrapper were accidentally removed).
  2. With run_as_system(): the loop processes ALL orgs correctly.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

import app.services.queue as queue_module
import app.services.remote_dispatch as remote_dispatch_module
import app.services.retention as retention_module
import app.services.triggers as triggers_module
from app.config import settings
from app.models import (
    Organization,
    Run,
    RunQueueEntry,
    Runner,
    RunnerPool,
    User,
    Workflow,
    WorkflowVersion,
)
from app.services.queue import requeue_expired_leases
from app.tenancy import DEFAULT_ORG_ID, run_as_system


@pytest.fixture(autouse=True)
def _mt_on():
    old = settings.multi_tenancy_enabled
    settings.multi_tenancy_enabled = True
    yield
    settings.multi_tenancy_enabled = old


async def _insert(session_factory, *objects):
    """Insert rows bypassing org filter (system context)."""
    async with session_factory() as session:
        with run_as_system():
            for obj in objects:
                session.add(obj)
            await session.commit()


async def _count(session_factory, model, **where):
    async with session_factory() as session:
        with run_as_system():
            stmt = select(func.count()).select_from(model)
            for k, v in where.items():
                stmt = stmt.where(getattr(model, k) == v)
            return int(await session.scalar(stmt) or 0)


ORG_B = "org-b-loop-test"
OLD = datetime.now(UTC) - timedelta(days=365)


async def test_retention_skips_non_default_org_without_system(client):
    """prune_old_runs() without run_as_system() must not touch org-b runs."""
    settings.run_retention_days = 1

    org_b_id = ORG_B + "-retention"
    wf_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    await _insert(
        retention_module.SessionLocal,
        Organization(id=org_b_id, name="Org B Retention", slug="org-b-ret"),
        Workflow(
            id=wf_id,
            org_id=org_b_id,
            name="WF",
            active=True,
        ),
        Run(
            id=run_id,
            org_id=org_b_id,
            workflow_id=wf_id,
            status="success",
            started_at=OLD,
            finished_at=OLD,
        ),
    )

    # Call WITHOUT run_as_system — should NOT prune org-b
    await retention_module.prune_old_runs(datetime.now(UTC))
    assert await _count(retention_module.SessionLocal, Run, id=run_id) == 1, (
        "prune without run_as_system must not delete non-default-org runs"
    )

    # Call WITH run_as_system — should prune org-b
    with run_as_system():
        await retention_module.prune_old_runs(datetime.now(UTC))
    assert await _count(retention_module.SessionLocal, Run, id=run_id) == 0, (
        "prune with run_as_system must delete non-default-org runs"
    )
```

- [x] **Step 2: Run the test**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_cross_org_loops.py::test_retention_skips_non_default_org_without_system -v`
Expected: PASS (or fail only if prune_old_runs reads `settings.run_retention_days` via live_settings — then also set `settings.run_retention_max_per_workflow = 0`; live_settings falls back to `settings` in tests).

- [x] **Step 3: Commit**

```bash
git add apps/api/tests/test_cross_org_loops.py
git commit -m "test(c2): retention loop skips non-default org without run_as_system"
```

---

### Task 3: C2 — Queue dispatch cross-org test

**Files:**
- Modify: `apps/api/tests/test_cross_org_loops.py`

- [x] **Step 1: Add the queue test**

Append to `test_cross_org_loops.py`:

```python
async def test_queue_requeue_skips_non_default_org_without_system(client):
    """requeue_expired_leases() without system context must not see org-b entries."""
    org_b_id = ORG_B + "-queue"
    wf_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    entry_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    expired = now - timedelta(seconds=1)

    await _insert(
        queue_module.SessionLocal,
        Organization(id=org_b_id, name="Org B Queue", slug="org-b-q"),
        Workflow(id=wf_id, org_id=org_b_id, name="WF Q", active=True),
        Run(
            id=run_id,
            org_id=org_b_id,
            workflow_id=wf_id,
            status="running",
            started_at=now,
        ),
        RunQueueEntry(
            id=entry_id,
            org_id=org_b_id,
            run_id=run_id,
            workflow_id=wf_id,
            status="leased",
            lease_expires_at=expired,
            attempt=1,
            max_attempts=3,
        ),
    )

    # Call WITHOUT run_as_system — org-b entry must be invisible
    async with queue_module.SessionLocal() as session:
        acted = await requeue_expired_leases(session, now=now)
    assert acted == 0, "requeue without run_as_system must not touch org-b entries"

    # Call WITH run_as_system — must see and requeue the org-b entry
    async with queue_module.SessionLocal() as session:
        with run_as_system():
            acted = await requeue_expired_leases(session, now=now)
    assert acted == 1, "requeue with run_as_system must process org-b entries"
```

- [x] **Step 2: Run the test**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_cross_org_loops.py::test_queue_requeue_skips_non_default_org_without_system -v`
Expected: PASS.

- [x] **Step 3: Commit**

```bash
git add apps/api/tests/test_cross_org_loops.py
git commit -m "test(c2): queue requeue skips non-default org without run_as_system"
```

---

### Task 4: C2 — Scheduler session isolation test

**Files:**
- Modify: `apps/api/tests/test_cross_org_loops.py`

Rather than invoking the full `_tick()` (which would dispatch runs), this test verifies the ORM filter is active on the query that `_tick` uses to discover schedulable workflows — the `select(Workflow).where(Workflow.active == True)` family in triggers.py.

- [x] **Step 1: Add the scheduler test**

Append to `test_cross_org_loops.py`:

```python
async def test_scheduler_session_isolates_org_b_workflows(client):
    """Scheduler session queries are org-filtered without run_as_system()."""
    org_b_id = ORG_B + "-scheduler"
    wf_id = str(uuid.uuid4())

    await _insert(
        triggers_module.SessionLocal,
        Organization(id=org_b_id, name="Org B Sched", slug="org-b-sched"),
        Workflow(id=wf_id, org_id=org_b_id, name="Sched WF", active=True),
    )

    # Without run_as_system: active workflow in org-b must be invisible
    async with triggers_module.SessionLocal() as session:
        rows = (
            await session.scalars(
                select(Workflow).where(Workflow.active.is_(True), Workflow.id == wf_id)
            )
        ).all()
    assert len(rows) == 0, (
        "scheduler session without run_as_system must not see org-b workflows"
    )

    # With run_as_system: must be visible
    async with triggers_module.SessionLocal() as session:
        with run_as_system():
            rows = (
                await session.scalars(
                    select(Workflow).where(
                        Workflow.active.is_(True), Workflow.id == wf_id
                    )
                )
            ).all()
    assert len(rows) == 1, (
        "scheduler session with run_as_system must see org-b workflows"
    )
```

- [x] **Step 2: Run the test**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_cross_org_loops.py::test_scheduler_session_isolates_org_b_workflows -v`
Expected: PASS.

- [x] **Step 3: Commit**

```bash
git add apps/api/tests/test_cross_org_loops.py
git commit -m "test(c2): scheduler session isolates non-default org without run_as_system"
```

---

### Task 5: C2 — Heartbeat cross-org test + `_as_system` guard

**Files:**
- Modify: `apps/api/tests/test_cross_org_loops.py`

- [x] **Step 1: Add the heartbeat and guard tests**

Append to `test_cross_org_loops.py`:

```python
async def test_heartbeat_skips_non_default_org_runs_without_system(client):
    """mark_stale_runners_offline without run_as_system must not requeue org-b runs."""
    import app.services.remote_dispatch as rd_module
    from app.services.remote_dispatch import mark_stale_runners_offline

    org_b_id = ORG_B + "-heartbeat"
    pool_id = str(uuid.uuid4())
    runner_id = str(uuid.uuid4())
    wf_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    stale = now - timedelta(seconds=settings.runner_offline_after_seconds + 1)

    await _insert(
        remote_dispatch_module.SessionLocal,
        Organization(id=org_b_id, name="Org B HB", slug="org-b-hb"),
        RunnerPool(id=pool_id, org_id=org_b_id, name="pool-b", provider="docker"),
        Runner(
            id=runner_id,
            pool_id=pool_id,
            status="online",
            last_seen_at=stale,
        ),
        Workflow(id=wf_id, org_id=org_b_id, name="WF HB", active=True),
        Run(
            id=run_id,
            org_id=org_b_id,
            workflow_id=wf_id,
            status="running",
            started_at=stale,
            runner_id=runner_id,
        ),
    )

    # WITHOUT run_as_system: the stale runner's org-b runs must NOT be requeued
    await mark_stale_runners_offline(now=now)

    async with remote_dispatch_module.SessionLocal() as session:
        with run_as_system():
            run_status = await session.scalar(
                select(Run.status).where(Run.id == run_id)
            )
    assert run_status == "running", (
        "heartbeat without run_as_system must not requeue org-b in-flight runs"
    )

    # WITH run_as_system: must see and requeue
    with run_as_system():
        await mark_stale_runners_offline(now=now)

    async with remote_dispatch_module.SessionLocal() as session:
        with run_as_system():
            run_status = await session.scalar(
                select(Run.status).where(Run.id == run_id)
            )
    assert run_status == "queued", (
        "heartbeat with run_as_system must requeue org-b in-flight runs when runner is stale"
    )


def test_main_loop_tasks_all_wrapped_in_as_system():
    """Static guard: every background-loop asyncio.create_task call in main.py
    must pass through _as_system() so MT filtering is bypassed for cross-org work.

    This test parses the source of main.py and asserts that every
    ``asyncio.create_task(...)`` call either:
      - wraps a call to ``_as_system(...)`` directly, OR
      - is itself inside ``_make_loop_task`` (which calls ``_as_system``).

    Fail this test → you added a bare ``asyncio.create_task(some_loop())``
    without the system-context wrapper.
    """
    import ast
    import inspect
    from app.main import lifespan  # noqa: F401 — import to get the source module

    import app.main as main_module

    src = inspect.getsource(main_module)
    tree = ast.parse(src)

    create_task_calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_task"
        ):
            # Stringify the first argument to the create_task call
            if node.args:
                create_task_calls.append(ast.unparse(node.args[0]))

    bare_calls = [
        c for c in create_task_calls
        if "_as_system(" not in c and "_make_loop_task(" not in c
    ]
    assert not bare_calls, (
        f"Found asyncio.create_task calls NOT wrapped in _as_system: {bare_calls}. "
        "Background loops must use _as_system() so all orgs are visible under MT."
    )
```

- [x] **Step 2: Check `mark_stale_runners_offline` signature**

Grep for the function signature:
```powershell
Select-String -Path "apps\api\app\services\remote_dispatch.py" -Pattern "async def mark_stale_runners_offline"
```
If the function takes no `now` parameter, remove `now=now` from the call and insert `now` via monkeypatching or set the runner's `last_seen_at` far enough in the past that the real `datetime.now(UTC)` already exceeds the threshold.

- [x] **Step 3: Run all C2 tests**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_cross_org_loops.py -v`
Expected: all 5 tests PASS.

- [x] **Step 4: Run the full API suite**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests -q`
Expected: same pass/fail as before plus 5 new passes.

- [x] **Step 5: Commit**

```bash
git add apps/api/tests/test_cross_org_loops.py
git commit -m "test(c2): heartbeat cross-org isolation + _as_system static guard"
```

---

## Track B — C3: Session hardening

### Task 6: C3 — Config additions

**Files:**
- Modify: `apps/api/app/config.py`

- [x] **Step 1: Write the failing test (config keys exist)**

```python
# In apps/api/tests/test_cookie_auth.py (will be created in Task 12)
# Write this test first, run it, confirm it fails.
from app.config import settings

def test_c3_config_keys_exist():
    assert hasattr(settings, "session_cookie_name")
    assert hasattr(settings, "session_cookie_secure")
    assert hasattr(settings, "session_cookie_samesite")
    assert hasattr(settings, "csrf_header_name")
    assert hasattr(settings, "ws_ticket_ttl_seconds")
```

Create the file now with just this test:

```bash
# Create the file:
```

```python
# apps/api/tests/test_cookie_auth.py
from app.config import settings


def test_c3_config_keys_exist():
    assert hasattr(settings, "session_cookie_name")
    assert hasattr(settings, "session_cookie_secure")
    assert hasattr(settings, "session_cookie_samesite")
    assert hasattr(settings, "csrf_header_name")
    assert hasattr(settings, "ws_ticket_ttl_seconds")
```

- [x] **Step 2: Run to verify it fails**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_cookie_auth.py::test_c3_config_keys_exist -v`
Expected: FAIL with `AttributeError` or assertion error.

- [x] **Step 3: Add config keys**

In `apps/api/app/config.py`, after the `auth_rate_limit_per_minute` line, add:

```python
    # C3 session cookie settings
    session_cookie_name: str = "noodle_session"
    # Set secure=False only when running behind HTTP (local dev without HTTPS).
    # In production with TLS this must be True.
    session_cookie_secure: bool = True
    session_cookie_samesite: Literal["strict", "lax", "none"] = "strict"
    # CSRF double-submit header name (must match the JS header in api.ts)
    csrf_header_name: str = "X-CSRF-Token"
    # Lifetime (seconds) for one-time WS upgrade tickets.
    ws_ticket_ttl_seconds: int = 30
```

Also add `"strict", "lax", "none"` to the `Literal` imports at the top of the file (they are already imported since `Literal` is used for `runtime_mode` etc., but the values need no change — just the type annotation).

- [x] **Step 4: Run the test**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_cookie_auth.py::test_c3_config_keys_exist -v`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add apps/api/app/config.py apps/api/tests/test_cookie_auth.py
git commit -m "feat(config): C3 session cookie + CSRF + WS ticket config keys"
```

---

### Task 7: C3 — WS ticket service

**Files:**
- Create: `apps/api/app/services/ws_ticket.py`

- [x] **Step 1: Write the failing tests**

Append to `apps/api/tests/test_cookie_auth.py`:

```python
import pytest
from app.services import ws_ticket


@pytest.fixture(autouse=True)
async def _clear_inmem_tickets():
    ws_ticket._INMEM.clear()
    yield
    ws_ticket._INMEM.clear()


async def test_ws_ticket_create_and_consume():
    ticket = await ws_ticket.create_ticket("user-123")
    assert isinstance(ticket, str) and len(ticket) > 10
    user_id = await ws_ticket.consume_ticket(ticket)
    assert user_id == "user-123"


async def test_ws_ticket_single_use():
    ticket = await ws_ticket.create_ticket("user-abc")
    await ws_ticket.consume_ticket(ticket)
    second = await ws_ticket.consume_ticket(ticket)
    assert second is None


async def test_ws_ticket_unknown_returns_none():
    result = await ws_ticket.consume_ticket("does-not-exist")
    assert result is None
```

- [x] **Step 2: Run to verify they fail**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_cookie_auth.py -k "ws_ticket" -v`
Expected: FAIL with `ModuleNotFoundError`.

- [x] **Step 3: Implement the service**

```python
# apps/api/app/services/ws_ticket.py
"""One-time WebSocket upgrade tickets.

Browsers cannot send custom headers on WS upgrades, so we replace the
token-in-URL pattern (?token=...) with a short-lived throwaway ticket.
The client calls POST /auth/ws-ticket, receives a 30-second single-use
opaque token, and passes it as ?ticket=... on the WS URL.  The WS handler
consumes it atomically (lookup + delete) to authenticate the upgrade.

Storage: Redis when available (atomic GETDEL), in-memory dict otherwise
(dev / SQLite mode with no Redis).  The in-memory store is per-process and
does not survive restarts — acceptable for dev; production requires Redis.
"""

import asyncio
import secrets
from datetime import UTC, datetime, timedelta

from app.config import settings

_INMEM: dict[str, tuple[str, datetime]] = {}
_INMEM_LOCK = asyncio.Lock()


async def _redis():
    try:
        import app.redis_client as _rc
        return _rc.redis_client
    except Exception:
        return None


async def create_ticket(user_id: str) -> str:
    """Mint a single-use ticket valid for ``settings.ws_ticket_ttl_seconds``."""
    ticket = secrets.token_urlsafe(32)
    r = await _redis()
    if r is not None:
        await r.setex(
            f"noodle:wst:{ticket}",
            settings.ws_ticket_ttl_seconds,
            user_id,
        )
    else:
        async with _INMEM_LOCK:
            _INMEM[ticket] = (
                user_id,
                datetime.now(UTC) + timedelta(seconds=settings.ws_ticket_ttl_seconds),
            )
    return ticket


async def consume_ticket(ticket: str) -> str | None:
    """Return the user_id and delete the ticket atomically.

    Returns None if the ticket is unknown, expired, or already consumed.
    """
    r = await _redis()
    if r is not None:
        raw = await r.getdel(f"noodle:wst:{ticket}")
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)

    async with _INMEM_LOCK:
        entry = _INMEM.pop(ticket, None)
        if entry is None:
            return None
        user_id, expires = entry
        if datetime.now(UTC) > expires:
            return None
        return user_id
```

- [x] **Step 4: Run the tests**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_cookie_auth.py -k "ws_ticket" -v`
Expected: 3 PASS.

- [x] **Step 5: Commit**

```bash
git add apps/api/app/services/ws_ticket.py apps/api/tests/test_cookie_auth.py
git commit -m "feat(api): WS ticket service — Redis-backed one-time upgrade tickets"
```

---

### Task 8: C3 — WsTicketResponse schema + auth dep updates

**Files:**
- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/security.py`

- [x] **Step 1: Add WsTicketResponse to schemas**

In `apps/api/app/schemas.py`, after `TokenResponse`:

```python
class WsTicketResponse(BaseModel):
    ticket: str
```

Find `TokenResponse` first:

```python
# apps/api/app/schemas.py — add after TokenResponse
class WsTicketResponse(BaseModel):
    ticket: str
```

- [x] **Step 2: Write the failing auth tests**

Append to `apps/api/tests/test_cookie_auth.py`:

```python
import pytest
from httpx import AsyncClient


async def test_login_sets_session_cookie(client: AsyncClient):
    resp = await client.post(
        "/api/auth/register",
        json={"email": "cookie@test.com", "password": "Passw0rd!", "name": "Cookie"},
    )
    assert resp.status_code == 201
    assert "noodle_session" in resp.cookies, "login must Set-Cookie: noodle_session"
    assert "noodle_csrf" in resp.cookies, "login must Set-Cookie: noodle_csrf"
    # CSRF cookie must NOT be httpOnly (JS needs to read it)
    csrf_cookie = resp.headers.get("set-cookie", "")
    session_cookie_header = [
        h for h in resp.headers.getlist("set-cookie")
        if "noodle_session" in h
    ]
    assert session_cookie_header, "noodle_session cookie missing"
    assert "httponly" in session_cookie_header[0].lower(), "noodle_session must be httpOnly"
    csrf_cookie_header = [
        h for h in resp.headers.getlist("set-cookie")
        if "noodle_csrf" in h
    ]
    assert csrf_cookie_header, "noodle_csrf cookie missing"
    assert "httponly" not in csrf_cookie_header[0].lower(), "noodle_csrf must NOT be httpOnly"


async def test_cookie_auth_protects_endpoint(client: AsyncClient):
    # Register to get session cookie
    await client.post(
        "/api/auth/register",
        json={"email": "cookieauth@test.com", "password": "Passw0rd!", "name": "C"},
    )
    # GET /auth/me with cookie (no Bearer) — must work
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 200


async def test_bearer_auth_still_works(client: AsyncClient):
    resp = await client.post(
        "/api/auth/register",
        json={"email": "bearer@test.com", "password": "Passw0rd!", "name": "B"},
    )
    token = resp.json()["token"]
    # Use Bearer header, no cookie
    resp2 = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        cookies={},  # clear auto-sent cookies
    )
    assert resp2.status_code == 200


async def test_csrf_required_for_cookie_auth_post(client: AsyncClient):
    await client.post(
        "/api/auth/register",
        json={"email": "csrf@test.com", "password": "Passw0rd!", "name": "X"},
    )
    # POST without CSRF header while using cookie auth — must 403
    # (create a workflow as a mutating request that isn't in the exempt list)
    resp = await client.post(
        "/api/workflows", json={"name": "test"}, headers={"Authorization": ""}
    )
    # With SameSite=Strict the cookie is sent (same origin in test), but no CSRF header
    # Note: httpx test client doesn't enforce SameSite, so we simulate by not sending
    # the CSRF header.  The cookie IS present (auto-sent by httpx cookie jar).
    # Expected: 403 (CSRF gate rejects) when no Bearer override.
    assert resp.status_code == 403


async def test_bearer_auth_bypasses_csrf(client: AsyncClient):
    resp = await client.post(
        "/api/auth/register",
        json={"email": "bypasscsrf@test.com", "password": "Passw0rd!", "name": "Y"},
    )
    token = resp.json()["token"]
    # Bearer auth — no CSRF header needed
    resp2 = await client.post(
        "/api/workflows",
        json={"name": "bearer-wf"},
        headers={"Authorization": f"Bearer {token}"},
        cookies={},
    )
    assert resp2.status_code == 201


async def test_csrf_valid_header_allows_request(client: AsyncClient):
    await client.post(
        "/api/auth/register",
        json={"email": "csrfvalid@test.com", "password": "Passw0rd!", "name": "V"},
    )
    csrf_value = client.cookies.get("noodle_csrf", "")
    resp = await client.post(
        "/api/workflows",
        json={"name": "csrf-wf"},
        headers={"X-CSRF-Token": csrf_value},
    )
    assert resp.status_code == 201


async def test_logout_clears_cookies(client: AsyncClient):
    await client.post(
        "/api/auth/register",
        json={"email": "logout@test.com", "password": "Passw0rd!", "name": "L"},
    )
    csrf_value = client.cookies.get("noodle_csrf", "")
    resp = await client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": csrf_value},
    )
    assert resp.status_code == 204
    # Session cookie must be expired/cleared
    assert client.cookies.get("noodle_session") in (None, "")


async def test_ws_ticket_endpoint(client: AsyncClient):
    await client.post(
        "/api/auth/register",
        json={"email": "wstix@test.com", "password": "Passw0rd!", "name": "W"},
    )
    csrf_value = client.cookies.get("noodle_csrf", "")
    resp = await client.post(
        "/api/auth/ws-ticket",
        headers={"X-CSRF-Token": csrf_value},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "ticket" in data and len(data["ticket"]) > 10
```

- [x] **Step 3: Run to verify they fail**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_cookie_auth.py -k "not ws_ticket and not config" -v`
Expected: multiple FAIL (endpoints don't set cookies yet, CSRF middleware doesn't exist).

- [x] **Step 4: Update security.py — dual Bearer+cookie extraction**

Replace `apps/api/app/security.py` content:

```python
"""Authentication and RBAC helpers.

Auth can be disabled for local development. When it is enabled, endpoints
using these dependencies require a valid bearer token or session cookie and
enforce the role minimum for the requested permission.
"""

from collections.abc import Awaitable, Callable

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Membership, Organization, User
from app.services.crypto import verify_token
from app.tenancy import DEFAULT_ORG_ID, current_org_id

VALID_ROLES = ("viewer", "editor", "admin", "owner")

_ROLE_RANK = {
    "viewer": 10,
    "editor": 20,
    "admin": 30,
    "owner": 40,
}

_PERMISSION_MIN_ROLE = {
    "workflow:write": "editor",
    "workflow:run": "editor",
    "deployment:write": "editor",
    "deployment:run": "editor",
    "code_module:write": "editor",
    "pinned:write": "editor",
    "artifact:write": "editor",
    "artifact:delete": "editor",
    "credential:read": "editor",
    "credential:test": "editor",
    "credential:write": "admin",
    "environment:write": "admin",
    "runner_pool:write": "admin",
    "audit:read": "admin",
    "user:manage": "admin",
    "ops:drain": "admin",
    "ops:dead-letter:read": "editor",
    "ops:dead-letter:replay": "admin",
}


def normalize_role(role: str | None) -> str:
    value = (role or "viewer").strip().lower()
    if value not in _ROLE_RANK:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported role '{role}'. Expected one of: {', '.join(VALID_ROLES)}.",
        )
    return value


def role_allows(role: str, minimum: str) -> bool:
    return _ROLE_RANK.get(role, 0) >= _ROLE_RANK[minimum]


def _extract_token(request: Request) -> str | None:
    """Return the raw token string from Authorization: Bearer header OR session cookie.

    Bearer header takes priority; cookie is the fallback for browser sessions.
    Returns None when neither is present.
    """
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth.removeprefix("Bearer ").strip()
    return request.cookies.get(settings.session_cookie_name)


async def current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    token = _extract_token(request)
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user_id = verify_token(token)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user


async def optional_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User | None:
    token = _extract_token(request)
    if token is None:
        if settings.auth_required:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
        return None
    user_id = verify_token(token)
    if user_id is None:
        if settings.auth_required:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
        return None
    user = await session.get(User, user_id)
    if user is None:
        if settings.auth_required:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
        return None
    return user


async def _lenient_session_user(
    request: Request, session: AsyncSession
) -> User | None:
    """The session user, or None — never raises.

    Used by resolve_org which runs on every request including webhook ingress
    where the Authorization header carries a webhook credential, not a Noodle
    session token.
    """
    token = _extract_token(request)
    if token is None:
        return None
    user_id = verify_token(token)
    if user_id is None:
        return None
    return await session.get(User, user_id)


async def resolve_org(
    request: Request,
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
    session: AsyncSession = Depends(get_session),
) -> str | None:
    """Resolve and validate the request's organization (multi-tenancy).

    Registered as a global app dependency so every request sets the org context.
    """
    if not settings.multi_tenancy_enabled:
        return None
    user = await _lenient_session_user(request, session)
    return await resolve_org_for(x_org_id, user, session)


async def resolve_org_for(
    x_org_id: str | None,
    user: User | None,
    session: AsyncSession,
) -> str | None:
    if not settings.multi_tenancy_enabled:
        return None
    org_id = (x_org_id or DEFAULT_ORG_ID).strip() or DEFAULT_ORG_ID
    current_org_id.set(org_id)
    if user is None:
        if org_id != DEFAULT_ORG_ID:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Sign-in required to access this organization.",
            )
        return org_id
    if await session.get(Organization, org_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    member = await session.scalar(
        select(Membership.id).where(
            Membership.org_id == org_id, Membership.user_id == user.id
        )
    )
    if member is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Not a member of this organization."
        )
    return org_id


async def _role_for(
    session: AsyncSession, user: User, org_id: str | None
) -> str:
    if not settings.multi_tenancy_enabled or org_id is None:
        return user.role
    membership_role = await session.scalar(
        select(Membership.role).where(
            Membership.org_id == org_id, Membership.user_id == user.id
        )
    )
    if membership_role is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Not a member of this organization."
        )
    return membership_role


def require_role(
    minimum: str, *, require_authenticated: bool = False
) -> Callable[..., Awaitable[User | None]]:
    minimum = normalize_role(minimum)

    async def dependency(
        request: Request,
        org_id: str | None = Depends(resolve_org),
        session: AsyncSession = Depends(get_session),
    ) -> User | None:
        user = await optional_current_user(request=request, session=session)
        if user is None:
            if require_authenticated:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED,
                    "Sign-in required for this operation.",
                )
            return None
        role = await _role_for(session, user, org_id)
        if not role_allows(role, minimum):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires {minimum} role or higher.",
            )
        return user

    return dependency


_REQUIRES_AUTHENTICATED = frozenset({"user:manage"})


def require_permission(permission: str) -> Callable[..., Awaitable[User | None]]:
    minimum = _PERMISSION_MIN_ROLE.get(permission)
    if minimum is None:
        raise ValueError(f"Unknown RBAC permission: {permission}")
    return require_role(
        minimum,
        require_authenticated=permission in _REQUIRES_AUTHENTICATED,
    )
```

- [x] **Step 5: Run tests — expect partial pass**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_cookie_auth.py -v`
Some tests still fail (cookie not set yet, CSRF middleware not installed, logout endpoint missing).

Full suite: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests -q`
The existing auth endpoint tests should still pass (Bearer still works).

- [x] **Step 6: Commit**

```bash
git add apps/api/app/schemas.py apps/api/app/security.py apps/api/tests/test_cookie_auth.py
git commit -m "feat(security): dual Bearer+cookie auth — _extract_token helper, all deps updated"
```

---

### Task 9: C3 — Login/register/logout/ws-ticket endpoints

**Files:**
- Modify: `apps/api/app/routers/auth.py`

- [x] **Step 1: Add cookie helpers and update endpoints**

In `apps/api/app/routers/auth.py`, add after the existing imports:

```python
import secrets

from fastapi import Response
```

Add helper functions after `_token_response`:

```python
def _new_csrf_token() -> str:
    return secrets.token_hex(32)


def _set_session_cookies(response: Response, token: str, csrf: str) -> None:
    """Set httpOnly session cookie + readable CSRF cookie on the response."""
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        max_age=settings.auth_token_ttl_seconds,
        path="/",
    )
    # CSRF cookie must NOT be httpOnly so JS can read it for the double-submit check
    response.set_cookie(
        key="noodle_csrf",
        value=csrf,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        max_age=settings.auth_token_ttl_seconds,
        path="/",
    )
```

Update `_token_response` to accept an optional pre-minted token:

```python
def _token_response(user: User, token: str | None = None) -> TokenResponse:
    return TokenResponse(
        token=token or create_token(user.id),
        user=UserInfo(
            id=user.id,
            email=user.email,
            name=user.name,
            company=user.company,
            role=user.role,
        ),
    )
```

Update `login` handler:

```python
@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    await _enforce_auth_rate_limit(request, "login")
    user = await session.scalar(select(User).where(User.email == _email(body.email)))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid email or password"
        )
    token = create_token(user.id)
    _set_session_cookies(response, token, _new_csrf_token())
    return _token_response(user, token)
```

Update `register` handler (same pattern — find `return _token_response(user)` and add cookie):

```python
    # At end of register handler, replace:
    #   return _token_response(user)
    # with:
    token = create_token(user.id)
    _set_session_cookies(response, token, _new_csrf_token())
    return _token_response(user, token)
```

The `register` endpoint signature needs `response: Response` added — add it as a parameter after `request: Request`:

```python
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
```

Add at the end of `auth.py`, after all existing endpoints:

```python
from app.schemas import WsTicketResponse
from app.security import current_user as _current_user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response):
    """Clear session and CSRF cookies."""
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie("noodle_csrf", path="/")


@router.post("/ws-ticket", response_model=WsTicketResponse)
async def ws_ticket_endpoint(user: User = Depends(_current_user)):
    """Mint a short-lived single-use WS upgrade ticket for the current user."""
    from app.services.ws_ticket import create_ticket
    return WsTicketResponse(ticket=await create_ticket(user.id))
```

- [x] **Step 2: Run the cookie auth tests**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_cookie_auth.py -k "cookie or logout or ws_ticket_endpoint" -v`
Expected: `test_login_sets_session_cookie`, `test_logout_clears_cookies`, `test_ws_ticket_endpoint` PASS.
CSRF tests still fail (middleware not yet installed).

- [x] **Step 3: Commit**

```bash
git add apps/api/app/routers/auth.py
git commit -m "feat(auth): set-cookie on login/register, POST /auth/logout, POST /auth/ws-ticket"
```

---

### Task 10: C3 — CSRF middleware + auth_gate cookie support

**Files:**
- Modify: `apps/api/app/main.py`

- [x] **Step 1: Add CSRF middleware**

In `apps/api/app/main.py`, add the following middleware **after** `_body_size_limit` and **before** `_security_headers` (i.e., insert between the two `@app.middleware("http")` blocks):

```python
@app.middleware("http")
async def _csrf_gate(request: Request, call_next):
    """CSRF double-submit validation for cookie-auth mutating requests.

    Only fires when:
      - The request method is state-changing (POST/PUT/PATCH/DELETE), AND
      - The request carries the noodle_session cookie (cookie-auth path), AND
      - The request does NOT carry a valid Bearer token (Bearer is CSRF-safe).

    Validates X-CSRF-Token header == noodle_csrf cookie value.
    Exempt: /auth/login, /auth/register, webhook ingress, runner WS.
    """
    if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return await call_next(request)

    # Bearer auth is inherently CSRF-safe: the header can't be set by cross-site forms
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return await call_next(request)

    # No session cookie → not a browser cookie-auth session; pass through
    session_cookie = request.cookies.get(settings.session_cookie_name, "")
    if not session_cookie:
        return await call_next(request)

    # Exempt specific prefixes and paths (login/register before the cookie exists,
    # webhook ingress uses its own auth, runner WS uses purpose tokens)
    path = request.url.path
    _CSRF_EXEMPT_PREFIXES = ("/webhook", "/provider-webhook", "/runner-pools/ws")
    _CSRF_EXEMPT_PATHS = {"/api/auth/login", "/api/auth/register", "/auth/login", "/auth/register"}
    if path in _CSRF_EXEMPT_PATHS or any(path.startswith(p) for p in _CSRF_EXEMPT_PREFIXES):
        return await call_next(request)

    # Double-submit: X-CSRF-Token header must equal noodle_csrf cookie
    csrf_header = request.headers.get(settings.csrf_header_name, "")
    csrf_cookie = request.cookies.get("noodle_csrf", "")
    if not csrf_header or not csrf_cookie or csrf_header != csrf_cookie:
        return JSONResponse(
            {"detail": "CSRF token missing or invalid"}, status_code=403
        )

    return await call_next(request)
```

- [x] **Step 2: Update auth_gate to accept session cookie**

Replace the `auth_gate` middleware body's token extraction with the dual-mode check:

```python
@app.middleware("http")
async def auth_gate(request: Request, call_next):
    if not settings.auth_required or request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    if path in _AUTH_EXEMPT_PATHS or any(
        path == p or path.startswith(f"{p}/") for p in _AUTH_EXEMPT_PREFIXES
    ):
        return await call_next(request)

    # Accept Bearer header OR session cookie
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        if verify_token(header.removeprefix("Bearer ")) is None:
            return JSONResponse({"detail": "Invalid or expired token"}, status_code=401)
        return await call_next(request)

    session_cookie = request.cookies.get(settings.session_cookie_name, "")
    if session_cookie and verify_token(session_cookie) is not None:
        return await call_next(request)

    # Allow ``?token=`` on browser-navigation routes (artifact downloads)
    query_token = request.query_params.get("token")
    if query_token and verify_token(query_token) is not None:
        return await call_next(request)

    return JSONResponse({"detail": "Authentication required"}, status_code=401)
```

- [x] **Step 3: Run the full test suite**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests/test_cookie_auth.py -v`
Expected: all 10 cookie auth tests PASS.

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests -q`
Expected: same pass/fail as before plus 10 new passes. Fix any regressions before proceeding.

- [x] **Step 4: Commit**

```bash
git add apps/api/app/main.py
git commit -m "feat(api): CSRF double-submit middleware + cookie auth in auth_gate"
```

---

### Task 11: C3 — WS handler update (ticket + deprecated token fallback)

**Files:**
- Modify: `apps/api/app/routers/runs.py`

- [x] **Step 1: Locate and update the WS auth handler**

Find the `run_events` WebSocket handler (grep for `@router.websocket("/ws/runs/{run_id}")`).

Replace the auth block:

```python
# Before (existing):
if settings.auth_required:
    token = websocket.query_params.get("token", "")
    if not token:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
    if verify_token(token) is None:
        await websocket.close(code=1008)
        return

# After:
if settings.auth_required:
    from app.services.ws_ticket import consume_ticket
    user_id: str | None = None

    # Preferred: one-time ticket (no session token in logs)
    ticket = websocket.query_params.get("ticket", "")
    if ticket:
        user_id = await consume_ticket(ticket)
    else:
        # Deprecated fallback: ?token= or Authorization header
        token = websocket.query_params.get("token", "")
        if not token:
            auth_header = websocket.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                token = auth_header[7:].strip()
        if token:
            user_id = verify_token(token)

    if user_id is None:
        await websocket.close(code=1008)
        return
```

- [x] **Step 2: Run API tests**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests -q`
Expected: same pass counts (WS tests still use `?token=` fallback).

- [x] **Step 3: Commit**

```bash
git add apps/api/app/routers/runs.py
git commit -m "feat(ws): accept ?ticket= for WS upgrade auth; ?token= deprecated fallback"
```

---

### Task 12: C3 — Frontend CSRF + login/logout/WS flow

**Files:**
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/LoginPage.tsx`
- Modify: `apps/web/src/App.tsx`

- [x] **Step 1: Update api.ts**

In `apps/web/src/api.ts`:

**a) Add CSRF cookie reader (after `setUser` function):**

```typescript
function getCsrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)noodle_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}
```

**b) Update `request()` to send the CSRF header on mutating methods:**

```typescript
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const method = (init?.method ?? "GET").toUpperCase();
  const baseHeaders: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (token) baseHeaders.Authorization = `Bearer ${token}`;
  const orgId = getOrgId();
  if (orgId) baseHeaders["X-Org-Id"] = orgId;
  // Add CSRF token for state-changing requests (double-submit pattern)
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = getCsrfToken();
    if (csrf) baseHeaders["X-CSRF-Token"] = csrf;
  }
  const headers = {
    ...baseHeaders,
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  const resp = await safeFetch(BASE + path, { ...init, headers });
  // ... rest unchanged
```

**c) Add `logout()` API function (near the bottom, in the auth section):**

```typescript
export async function logout(): Promise<void> {
  try {
    await request<void>("/auth/logout", { method: "POST" });
  } catch {
    // Best-effort; clear local state regardless
  }
  setToken(null);
  setUser(null);
}
```

**d) Replace `runEventsUrl()` with an async function that uses a WS ticket:**

Find the existing `runEventsUrl` function and replace it:

```typescript
export async function runEventsUrl(runId: string): Promise<string> {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const base = `${proto}//${window.location.host}/ws/runs/${runId}`;
  // Mint a one-time ticket so the session token never appears in server logs
  try {
    const resp = await request<{ ticket: string }>("/auth/ws-ticket", {
      method: "POST",
    });
    return `${base}?ticket=${encodeURIComponent(resp.ticket)}`;
  } catch {
    // Fallback to legacy token-in-URL for unauthenticated or auth-disabled mode
    const token = getToken();
    return token ? `${base}?token=${encodeURIComponent(token)}` : base;
  }
}
```

- [x] **Step 2: Update all callers of runEventsUrl to await it**

Search for `runEventsUrl(` in the codebase:

```powershell
Select-String -Path "apps\web\src\**\*.tsx","apps\web\src\**\*.ts" -Pattern "runEventsUrl\("
```

For each caller, change `runEventsUrl(runId)` → `await runEventsUrl(runId)` and ensure the calling function is `async`. The primary caller is likely `editor/store.ts` or `ExecutionsPage.tsx`.

- [x] **Step 3: Update LoginPage.tsx — remove setToken call**

Find in `apps/web/src/LoginPage.tsx`:

```typescript
setToken(result.token);
```

Remove that line. The server now sets the httpOnly session cookie. The `result.token` in the response body is kept for backward-compat API consumers but the browser doesn't need to store it.

If `result.token` is used elsewhere in `LoginPage.tsx`, keep the variable but do not call `setToken`.

- [x] **Step 4: Update App.tsx — signOut calls server logout**

Find `signOut` in `apps/web/src/App.tsx`. Replace the body with:

```typescript
const signOut = async () => {
  await api.logout();  // clears server cookie + local state
  setUser(null);       // clear React state
  setToken(null);      // belt-and-braces for any remaining localStorage token
};
```

Make sure `signOut` is declared `async` and any callers `await` it (or handle it as a fire-and-forget if the UI redirects immediately).

- [x] **Step 5: Run typecheck and tests**

Run: `cd apps\web; npm run typecheck`
Expected: no errors (fix type errors if `runEventsUrl` callers are not awaited).

Run: `cd apps\web; npm run test`
Expected: all vitest tests pass. If `ChatPanel.test.tsx` or other tests mock `runEventsUrl`, update them to handle the async version:
```typescript
// In test mocks:
jest.spyOn(api, 'runEventsUrl').mockResolvedValue('ws://test/ws/runs/123');
```

- [x] **Step 6: Commit**

```bash
git add apps/web/src/api.ts apps/web/src/LoginPage.tsx apps/web/src/App.tsx
git commit -m "feat(web): CSRF header, remove localStorage token on login, async WS ticket URL, server logout"
```

---

### Task 13: C3 — CSP meta tag

**Files:**
- Modify: `apps/web/index.html`

- [x] **Step 1: Locate index.html**

```powershell
Get-Content "apps\web\index.html"
```

- [x] **Step 2: Add the CSP meta tag**

In `apps/web/index.html`, inside `<head>`, add after the `<meta charset="UTF-8">` line:

```html
<!-- Content-Security-Policy: tightened for production.
     connect-src 'self' covers same-origin API + WS on /api and /ws paths.
     style-src 'unsafe-inline' required for Radix UI + CSS-in-JS edge cases.
     worker-src blob: required for Vite HMR + any Web Worker usage.
     Note: frame-ancestors must be a response header (not meta) — set it in
     nginx/proxy with: add_header Content-Security-Policy "frame-ancestors 'none'";
-->
<meta http-equiv="Content-Security-Policy"
  content="default-src 'self';
           script-src 'self';
           style-src 'self' 'unsafe-inline';
           img-src 'self' data: blob:;
           connect-src 'self' ws: wss:;
           font-src 'self';
           worker-src 'self' blob:;
           object-src 'none';">
```

- [x] **Step 3: Run build to verify CSP doesn't break the app**

Run: `cd apps\web; npm run build`
Expected: build succeeds with no errors. If Vite reports hash mismatches (inline scripts rejected by CSP), check Vite's `build.cssCodeSplit` and ensure no inline event handlers in custom HTML.

- [x] **Step 4: Run typecheck + tests + e2e**

Run: `cd apps\web; npm run typecheck; npm run test`
Expected: green.

Run e2e smoke: `cd apps\web; npm run test:e2e`
Expected: 3/3 pass (the login flow now uses cookies — the e2e suite should still work since httpx and Playwright both follow cookies automatically).

- [x] **Step 5: Commit**

```bash
git add apps/web/index.html
git commit -m "feat(web): strict Content-Security-Policy meta tag (C3)"
```

---

### Task 14: Full suite verification + branch cleanup

- [x] **Step 1: Run full backend suite**

Run: `cd apps\api; ..\..\.venv\Scripts\python.exe -m pytest tests -q`
Expected: all existing tests pass plus 15 new passes (5 C2 + 10 C3).
Known pre-existing failures (NOT regressions): `test_dispatch_webhook_passes_shared_session_to_resolve_node_auth` and `test_queue_fairness` flood test.

- [x] **Step 2: Run full frontend suite**

Run: `cd apps\web; npm run typecheck; npm run test; npm run build`
Expected: all green.

- [x] **Step 3: E2E smoke**

Run: `cd apps\web; npm run test:e2e`
Expected: 3/3 pass.

- [x] **Step 4: Final commit and merge**

```bash
git add -A
git commit -m "chore(phase7): phase 7 MT launch gate — C2 loop tests + C3 session hardening complete"
```

---

## Self-review

### Spec coverage

| Item | Task(s) |
|------|---------|
| C2 retention cross-org | Task 2 |
| C2 queue dispatch cross-org | Task 3 |
| C2 scheduler cross-org | Task 4 |
| C2 heartbeat cross-org | Task 5 |
| C2 `_as_system` CI guard | Task 5 |
| C3 httpOnly cookie sessions | Tasks 9, 10 |
| C3 CSRF double-submit | Task 10 |
| C3 strict CSP | Task 13 |
| C3 WS one-time ticket | Tasks 7, 11, 12 |
| C3 Bearer kept for runners + API | Tasks 8, 10 (Bearer path preserved) |
| MT Phase E (org-KEK) | Already done in multi-tenancy branch |
| Expression preview isolation | Already done (Phase 1) |
| License decision | Skipped per user decision |

### Placeholder scan

No TBD or TODO sections. All code is complete.

### Type consistency

- `_token_response(user, token)` — added `token` param in Task 9; callers updated in same task.
- `runEventsUrl()` — changed to `async` in Task 12; callers updated in same step.
- `current_user` dependency — now takes `Request` not `Header`; all injection sites use FastAPI's DI so no callers need updating.
- `_lenient_session_user(request, session)` — signature changed from `(authorization, session)` to `(request, session)`; the one call site in `resolve_org` is updated in Task 8.
