# Noodle Production Review — 2026-06-14

> **Purpose:** Pre-mortem, bug catalogue, and handover brief for agents taking over production hardening. Branch: `feat/arch-program-phase5`. Multi-tenancy is the primary new surface; treat every finding through that lens first.

---

## Quick-Reference Severity Matrix

| # | File | Line | Severity | Category | Status |
|---|------|------|----------|----------|--------|
| F-1 | `app/services/run_alerts.py` | 61 | 🔴 CRITICAL | MT isolation — silent failure | ✅ Fixed |
| F-2 | `app/services/remote_dispatch.py` | 159 | 🔴 CRITICAL | Security — unauthenticated runner | Open |
| F-3 | `app/routers/credentials.py` | 84 | 🔴 CRITICAL | MT isolation — IDOR | Open |
| F-4 | `app/routers/workflows.py` | 191 | 🟠 HIGH | MT isolation — cross-org link | Open |
| F-5 | `app/services/runner.py` | 430 | 🟠 HIGH | Quota enforcement — bypass via 0 | N/A (0=unlimited by design) |
| F-6 | `app/services/sandbox_policy.py` | 20 | 🟠 HIGH | Sandbox — opt-out escape | Open |
| F-7 | `app/mcp/tools.py` | 436 | 🟡 MEDIUM | MCP — publish always fails w/o notes | ✅ Fixed |
| F-8 | `app/routers/runs.py` | 100 | 🟡 MEDIUM | Crash — IndexError on empty versions | N/A (already guarded) |
| F-9 | `app/routers/deployments.py` | 122 | 🟡 MEDIUM | Data loss — runner_pool_id dropped | ✅ Fixed |
| F-10 | `app/services/runtime_pool.py` | 536 | 🟡 MEDIUM | Reliability — zombie processes | Open |
| F-11 | `app/services/container_runtime.py` | 262 | 🟡 MEDIUM | Reliability — no Docker build timeout | Open |
| F-12 | `app/routers/runs.py` | 369 | 🟠 HIGH | MT isolation — cancel no-ops for non-default orgs | ✅ Fixed |
| F-13 | `app/main.py` | 96 | 🟡 MEDIUM | Reliability — two-session startup gap | Open |
| F-14 | `apps/web/src/api.ts` | 207 | 🟡 MEDIUM | Frontend — X-Org-Id not sent when null | Open |

---

## F-1 — `dispatch_error_handlers` runs under wrong org context [CRITICAL]

**File:** `apps/api/app/services/run_alerts.py:61`
**Called from:** `apps/api/app/services/runner.py:1151`

### What's wrong
`dispatch_error_handlers` is called with the bare `SessionLocal` factory and no org context:

```python
# runner.py:1151
await run_alerts.dispatch_error_handlers(
    SessionLocal,       # ← bare factory, no org context set
    start_run,
    run_id=run_id,
    ...
)
```

Inside `dispatch_error_handlers`, a new session opens with no `ContextVar` set. `active_org_id()` therefore returns `DEFAULT_ORG_ID`. The `session.get(Run, run_id)` on line 62 is ORM-filtered to the default org only.

### Impact
- **Every non-default org's failed runs have their error workflows silently skipped.** The function returns early because `run is None` for any run not in the default org.
- Alert webhooks are also never fired for non-default orgs.
- Secondary: if a cross-org `error_workflow_id` has been persisted (see F-4), the `start_run_fn` at line 125 dispatches org-B's workflow with org-A's full payload (run IDs, node errors, logs) — data exfiltration.

### Fix
Wrap with `run_as_system()` so the session operates across all orgs:

```python
# run_alerts.py:61
async with session_factory() as session:
    from app.tenancy import run_as_system
    with run_as_system():
        run = await session.get(Run, run_id)
        ...
```

---

## F-2 — Runner WebSocket accepts any `runner_id` without authentication [CRITICAL]

**File:** `apps/api/app/services/remote_dispatch.py:159`

### What's wrong
`handle_runner_connect` accepts the `runner_id` from the URL path. No token, HMAC, or shared secret is verified:

```python
async def handle_runner_connect(self, runner_id: str, ws: Any) -> None:
    conn = _AgentConnection(runner_id=runner_id, ws=ws)
    async with self._lock:
        self._agents[runner_id] = conn   # ← unconditional registration
    async with SessionLocal() as session:
        runner = await session.get(Runner, runner_id)
        if runner is not None:
            runner.status = "online"     # ← DB lookup ≠ authentication
```

Anyone who can reach the WS endpoint and knows (or guesses) a valid `runner_id` can hijack that runner slot and receive run payloads — including `env_payload` with credential refs and graph secrets.

### Impact
- Credential theft via runner impersonation on internal network exposure.
- Legitimate runner is silently displaced; assigned runs are delivered to the attacker.

### Fix
Require a pre-shared runner secret (stored in `Runner.secret_hash` at registration time). Verify it during the WebSocket handshake before registering the connection:

```python
# Verify HMAC of runner_id + timestamp with runner.secret_hash
token = ws.headers.get("X-Runner-Token")
if not verify_runner_token(runner, token):
    await ws.close(code=4001, reason="unauthorized")
    return
```

---

## F-3 — `session.get()` identity-map bypass on Credential — IDOR [CRITICAL]

**File:** `apps/api/app/routers/credentials.py:84`

### What's wrong
```python
async def _load(session: AsyncSession, cred_id: str) -> Credential:
    cred = await session.get(Credential, cred_id)   # ← identity-map risk
    if cred is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Credential not found")
    return cred
```

`session.get()` checks SQLAlchemy's identity map before issuing a SELECT. If a `Credential` from org-B was loaded earlier in the same session under `skip_org_filter=True` (e.g., from the queue dispatch path), the org filter hook is never invoked. Org-A can read, update, delete, or test org-B's credential.

The artifacts router already documents this danger and uses `populate_existing=True`; credentials do not.

### Impact
- IDOR: read, write, delete credentials across tenant boundaries.
- All mutating credential endpoints flow through `_load`.

### Fix
```python
async def _load(session: AsyncSession, cred_id: str) -> Credential:
    cred = await session.get(
        Credential, cred_id,
        options=[],
        populate_existing=True,   # ← forces real SELECT, triggers org filter
    )
    if cred is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Credential not found")
    return cred
```

---

## F-4 — Cross-org `error_workflow_id` persisted without org check [HIGH]

**Files:** `apps/api/app/routers/deployments.py:191`, `apps/api/app/routers/workflows.py` (equivalent pattern)

### What's wrong
```python
# deployments.py:191
if body.error_workflow_id is not None and await session.get(
    Workflow, body.error_workflow_id
) is None:
    raise HTTPException(...)
```

`session.get(Workflow, id)` may hit the identity map (same bypass as F-3). Even if it does a real SELECT, it only checks existence — not that the workflow belongs to the same org. An org-A admin can set `error_workflow_id` to a workflow owned by org-B.

### Impact
Combined with F-1 (once fixed): org-A's failure payload (workflow name, node errors, logs, run IDs) is injected as parameters into org-B's error workflow execution.

### Fix
After the existence check, verify `error_workflow.org_id == active_org_id()`:

```python
err_wf = await session.scalar(
    select(Workflow).where(Workflow.id == body.error_workflow_id)
)
if err_wf is None:
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Error workflow not found")
# org check: err_wf.org_id is already filtered by ORM layer if using .scalar()
```

Using `session.scalar(select(...).where(...))` rather than `session.get()` ensures the ORM hook fires.

---

## F-5 — `executions_per_day=0` disables quota enforcement [HIGH]

**File:** `apps/api/app/services/runner.py:430`

### What's wrong
```python
if limits.executions_per_day:   # ← falsy check — 0 skips the branch entirely
    used = await metering.runs_today(session, wf_obj.org_id)
    if used >= limits.executions_per_day:
        raise ValueError("Daily execution quota reached ...")
await metering.record_run_started(session, wf_obj.org_id)
```

Setting `executions_per_day=0` (via `PATCH /orgs/{id}/settings`) stores 0, which is falsy. The quota branch is skipped, giving the org unlimited daily runs. Also, two concurrent `start_run` calls both read the same committed meter count before either commits — the hard ceiling can be exceeded by the burst concurrency.

### Fix
1. Change the guard: `if limits.executions_per_day is not None and limits.executions_per_day > 0:`
2. For the race: use `SELECT ... FOR UPDATE` on the meter row (Postgres) or an advisory lock to serialize the check-and-increment.

---

## F-6 — Sandbox enforcement is opt-out, not opt-in [HIGH]

**File:** `apps/api/app/services/sandbox_policy.py:20`

### What's wrong
The sandbox policy startup check can be bypassed by setting `SANDBOX_POLICY_STRICT=false`. If an operator sets this during a migration or debug session and forgets to revert it, user code runs in the shared host process pool with full filesystem and network access to the API process and co-tenant data.

### Impact
- Complete sandbox escape — one env-var misconfiguration on a live multi-tenant instance exposes all tenants' execution environments.

### Fix
Consider opt-in instead of opt-out for production mode. At minimum, log a loud `CRITICAL` warning and emit an audit event when `SANDBOX_POLICY_STRICT=false` is detected with `MULTI_TENANCY_ENABLED=true`. Better: refuse startup in that combination.

---

## F-7 — MCP `publish_workflow` always fails when `notes` is omitted [MEDIUM]

**File:** `apps/api/app/mcp/tools.py:436`

### What's wrong
```python
WorkflowPublishRequest(notes=str(args.get("notes") or "") or None)
```

When `notes` is not provided: `str("") or None` evaluates to `None`. `WorkflowPublishRequest.notes` is typed `str = ""` (not `Optional[str]`). Pydantic raises a `ValidationError`, caught as `McpToolError("Publish failed: ...")`.

Every no-notes MCP publish call fails.

### Fix
```python
WorkflowPublishRequest(notes=str(args.get("notes") or ""))
```

---

## F-8 — `workflow.versions[-1]` IndexError on zero-version workflow [MEDIUM]

**File:** `apps/api/app/routers/runs.py:100`

### What's wrong
```python
latest = workflow.versions[-1]   # ← IndexError if workflow.versions is []
```

A workflow that has never been saved has an empty `versions` list. A user who POSTs `/workflows/{id}/run` on a brand-new workflow gets an unhandled `IndexError` → HTTP 500. Same pattern exists at `_draft_graph` and `_graph_for_run`.

### Fix
```python
if not workflow.versions:
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Workflow has no saved versions.")
latest = workflow.versions[-1]
```

---

## F-9 — `runner_pool_id` silently dropped for Deployments [MEDIUM]

**Files:** `apps/api/app/routers/deployments.py:122` and `200`

### What's wrong
1. `create_deployment` (line 200): `Deployment(...)` constructor never receives `runner_pool_id=body.runner_pool_id`. Every new deployment gets `runner_pool_id=NULL` regardless of what the client sends.
2. `_info()` builder (line 122–140): Never reads `deployment.runner_pool_id`. Every GET/POST/PUT deployment response returns `runner_pool_id: null`.
3. `update_deployment` (line 241): Doesn't handle `body.runner_pool_id` in the update block.

### Fix
```python
# create_deployment, add:
runner_pool_id=body.runner_pool_id,

# _info(), add:
runner_pool_id=deployment.runner_pool_id,

# update_deployment, add:
if body.runner_pool_id is not None:
    deployment.runner_pool_id = body.runner_pool_id
```

---

## F-10 — Fire-and-forget `proc.close()` creates zombie processes [MEDIUM]

**File:** `apps/api/app/services/runtime_pool.py:536`

### What's wrong
```python
asyncio.create_task(proc.close())   # ← no reference stored, no done-callback
```

Exceptions from `proc.close()` are silently swallowed. If the event loop shuts down before the task completes, the subprocess is never waited on and becomes a zombie. Under load, hundreds of untracked tasks accumulate.

### Fix
Store a reference and add a done-callback that logs exceptions:
```python
task = asyncio.create_task(proc.close())
task.add_done_callback(lambda t: t.exception() and logger.error("proc.close failed: %s", t.exception()))
```

---

## F-11 — Docker image build has no timeout [MEDIUM]

**File:** `apps/api/app/services/container_runtime.py:262`

### What's wrong
`client.images.build()` is called inside `run_in_executor` with no timeout. If the Docker daemon hangs or pip stalls (slow PyPI mirror, network partition), the thread blocks forever, filling the thread pool and blocking all subsequent runs.

### Fix
Wrap with `asyncio.wait_for(build_coro, timeout=settings.docker_build_timeout_seconds)` and propagate the timeout as a run failure.

---

## F-12 — `cancel_run` opens new SessionLocal with no org context [HIGH]

**File:** `apps/api/app/routers/runs.py:369` → `apps/api/app/services/runner.py:622`

### What's wrong
`cancel_workflow_run` delegates to `cancel_run()`. `cancel_run` opens a brand-new `SessionLocal` with no `current_org_id` ContextVar set — `active_org_id()` falls back to `DEFAULT_ORG_ID`. `session.get(Run, run_id)` is ORM-filtered to default org only. Non-default org runs return `None`; the cancel is silently dropped.

### Impact
Non-default org members get a phantom 404 when cancelling their own live runs; runs keep executing.

### Fix
Pass the org_id explicitly or call `cancel_run` within `run_as_system()`.

---

## F-13 — Two-session startup gap in `_mark_interrupted_runs` [MEDIUM]

**File:** `apps/api/app/main.py:96`

### What's wrong
`_mark_interrupted_runs` uses two separate DB sessions — one to cancel `Run` rows, another to clean up orphaned `RunQueueEntry` rows. A crash between the two commits leaves queue entries stuck as `status='running'` permanently with no active run. The dispatch loop never re-leases them; those run slots are lost until manual DB intervention.

### Fix
Combine both operations in a single transaction, or wrap the second operation in `run_as_system()` with explicit restart logic on startup.

---

## F-14 — Frontend omits `X-Org-Id` header when `orgId` is null [MEDIUM]

**File:** `apps/web/src/api.ts:207`

### What's wrong
```typescript
if (orgId) headers['X-Org-Id'] = orgId;   // ← null/empty → header omitted
```

A user who has never switched orgs (orgId is null in localStorage) makes all API calls without the header. In a strict multi-tenant deployment, the server resolves these to `DEFAULT_ORG_ID` — which may be wrong if the user's primary org is not the default.

### Fix
Always send the header with the resolved org ID (defaulting to the user's primary org from the session).

---

## Pre-Mortem: How This Breaks in Production

### Scenario 1 — Non-default org onboarding (Day 1)
An enterprise customer signs up, is placed in `org-enterprise-1`. They build a workflow, add credentials (OAuth), and configure an error-handler workflow. Everything appears to work in the editor.

**What actually fails:**
- F-12: They can never cancel a running job (cancel silently no-ops).
- F-1: If any run fails, the error-handler is never triggered.
- F-9: Their runner pool assignment is never persisted; all runs land on the default pool.
- F-3 (latent): If anyone accidentally triggers a cache-hit scenario, their credentials are readable cross-org.

### Scenario 2 — OAuth credential setup for any non-default org
Customer initiates an OAuth flow. After the provider redirect, the callback arrives without `X-Org-Id`. The credential is stamped to `DEFAULT_ORG_ID`. The customer sees the credential in their org (the UI re-fetches with the right org header), but the backend row is owned by `DEFAULT_ORG_ID` — shared with every default-org member. On the next cred rotation, the default org's cred is updated, and enterprise org's workflow silently starts using the wrong credential.

### Scenario 3 — Remote Runner deployment
An enterprise customer deploys a self-hosted runner. A malicious actor on the same private network connects a WebSocket to `/runners/{known_runner_id}`. They receive all run payloads assigned to that pool — including encrypted credential payloads and workflow graphs. No token validation prevents this.

### Scenario 4 — MCP-enabled workflow publishing
An LLM agent is given MCP access. The agent calls `publish_workflow` without a `notes` argument. The call fails every time with `McpToolError("Publish failed: 1 validation error...")`. The agent loops retrying, consuming credits. The only fix is for the calling agent to always supply a `notes` string.

### Scenario 5 — Quota bypass by setting executions_per_day=0
An org owner, confused about the "0 = unlimited" semantics, sets their own quota to 0 thinking it means "disable runs". Instead, they bypass the quota gate entirely. In a billing scenario this means unbounded compute consumption.

---

## Multi-Tenancy Correctness Checklist

For any agent continuing this work, verify these invariants hold for **every router and background task**:

### A. Session/ORM Access Patterns
- [ ] All `session.get(Model, id)` calls on org-scoped models use `populate_existing=True` OR use `session.scalar(select(Model).where(Model.id == id))` instead
- [ ] All background tasks that need cross-org access are wrapped with `run_as_system()`
- [ ] No `SessionLocal()` is opened in background tasks without explicit org context or `run_as_system()`
- [ ] `session.execute(..., execution_options={"skip_org_filter": True})` is only used for legitimate cross-org reads (list my orgs, membership queries)

### B. Foreign Key Cross-Org References
- [ ] `error_workflow_id` validation uses `session.scalar(select(...).where(...))` not `session.get()`
- [ ] Any FK reference to a resource in another table verifies the target's `org_id` matches the request org
- [ ] Subworkflow ID references are validated within the same org

### C. Background Services
- [ ] `dispatch_error_handlers` — wrap body in `run_as_system()`
- [ ] `cancel_run` — pass org context or use `run_as_system()`
- [ ] `_mark_interrupted_runs` — already uses SystemLocal; verify
- [ ] `run_resume` — check for org context on re-queued runs
- [ ] Queue dispatch loop — verify `run_as_system()` wraps all `session.get(Run, ...)` calls
- [ ] Retention sweep — already uses `run_as_system()` (verify)

### D. OAuth / External Callbacks
- [ ] Embed `org_id` in OAuth state token (signed, not just header-based)
- [ ] Callback validates state token org vs current request org before persisting credential

### E. Runner Security
- [ ] Runner WebSocket authentication — shared secret or mTLS
- [ ] `runner_id` validated against DB + authenticated before registration in `_agents`

---

## Production Readiness Gates

Before enabling `MULTI_TENANCY_ENABLED=true` in production, all of the following must be resolved:

| Gate | Finding | Blocker? |
|------|---------|----------|
| Error handlers work for all orgs | F-1 | YES |
| Runner auth | F-2 | YES (if remote runners exposed) |
| Credential IDOR | F-3 | YES |
| Cross-org error_workflow_id | F-4 | YES |
| Quota bypass | F-5 | YES (billing) |
| Sandbox enforcement | F-6 | YES (MT) |
| Cancel works for all orgs | F-12 | YES |
| runner_pool_id persisted | F-9 | NO (UX regression) |
| Docker timeout | F-11 | NO (reliability) |
| MCP publish notes | F-7 | NO (MCP-only) |

---

## Handover Context for Incoming Agents

### Codebase Map (critical paths)

```
apps/api/app/
├── tenancy.py          # ORM org-filter hooks (Layer 2) + run_as_system()
├── security.py         # resolve_org, require_role, require_permission
├── models.py           # All ORM models; org-scoped models have org_id column
├── main.py             # App factory, startup (_mark_interrupted_runs), CSRF
├── services/
│   ├── runner.py       # start_run, _start_run_impl, _execute_run — THE CORE
│   ├── run_alerts.py   # Error workflow + alert webhook dispatch
│   ├── metering.py     # runs_today, record_run_started (quota accounting)
│   ├── org_limits.py   # effective_limits() — org quota resolution
│   ├── org_keys.py     # KEK management (encryption key per org)
│   ├── remote_dispatch.py  # Runner WebSocket management
│   ├── queue.py        # Durable queue dispatch loop
│   ├── sandbox_pool.py # Sandbox worker pool (process-per-run)
│   ├── runtime_pool.py # Runtime process pool (warm pool)
│   ├── container_runtime.py # Docker management
│   └── credentials.py  # Credential encryption/decryption
├── routers/
│   ├── orgs.py         # Org CRUD + member management
│   ├── runs.py         # Run lifecycle (start/cancel/replay)
│   ├── credentials.py  # Credential CRUD + OAuth
│   ├── deployments.py  # Deployment CRUD
│   └── mcp.py          # MCP server endpoint
└── mcp/
    └── tools.py        # MCP tool implementations (call FastAPI routes directly)
```

### Key Invariants to Preserve

1. **`current_org_id` ContextVar** must be set before any ORM access on org-scoped data in request handlers. Set by `resolve_org` global dependency on every request.

2. **Background tasks** that legitimately access all orgs MUST use `run_as_system()` context manager. Failing to do so causes data from non-default orgs to be invisible (fail-closed, not fail-open).

3. **`session.get(Model, id)`** on any org-scoped model should be replaced with `session.scalar(select(Model).where(Model.id == id))` OR use `populate_existing=True`. The identity map bypasses the ORM filter hook.

4. **`stamp(obj)`** is called automatically at flush time for new ORM objects (via `before_flush` hook). Do not call it manually unless you need a custom org_id.

5. **`run_as_system()`** is a context manager, not a session factory. The session still uses the SQLAlchemy filter hooks — `run_as_system()` sets `SYSTEM_CONTEXT` which makes `active_org_id()` return `None`, which disables the hook.

### How to Run Tests

```bash
cd apps/api
pytest tests/test_org_isolation_enforcement.py -v   # MT isolation
pytest tests/test_tenancy_isolation_pg.py -v        # Postgres RLS
pytest tests/test_org_rbac.py -v                    # RBAC
pytest tests/test_mcp_server.py -v                  # MCP
pytest tests/test_run_meters.py -v                  # Quota
```

### Alembic Migration Chain (this branch)

Migrations 0040–0050 implement multi-tenancy. They must be applied in order. Key ones:
- `0040_orgs.py` — creates `organizations`, `memberships` tables
- `0041_org_id_cols.py` — adds `org_id` column to all scoped tables
- `0042_rls.py` — installs Postgres RLS policies
- `0043_org_kek.py` — per-org encryption key storage
- `0046_queue_org_index.py` — queue dispatch performance index

Do NOT skip any migration. Apply with: `alembic upgrade head`

---

*Generated 2026-06-14. Branch: `feat/arch-program-phase5`. Reviewer: Claude Sonnet 4.6.*
