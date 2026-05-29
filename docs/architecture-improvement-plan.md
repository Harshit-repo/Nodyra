# Noodle Architecture Improvement Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** harden Noodle from a promising Python-native workflow builder into a production-ready workflow automation platform with clear local/distributed modes, durable execution, observable queueing, safe credentials, and best-in-class authoring ergonomics.

**Production target (v1):** single-tenant self-hosting with **trusted workflow authors**. Noodle's warm per-environment subprocess pools and arbitrary-Python Code/uploaded-module nodes run inside the operator's trust boundary by design; this is the right model for teams running their own instance. Untrusted/multi-tenant execution would require per-run disposable isolation (containers/gVisor/Firecracker), discarding the warm-pool performance model — that is explicitly **out of scope for v1** and tracked separately against the remote-runner seam. Every task below should be read against the single-tenant target.

**Architecture:** keep the current FastAPI + React + Python runtime foundation, but make process boundaries explicit: API/editor, scheduler leader, webhook ingress, durable queue, local/remote workers, runner pools, metadata DB, and artifact storage. Local mode remains simple; production mode becomes durable and horizontally scalable.

**Tech Stack:** FastAPI, SQLAlchemy async, Postgres/SQLite, Redis or equivalent queue, Python subprocess runtime, remote runner agent/Docker/Kubernetes providers, React/Vite/React Flow, OpenTelemetry, Prometheus-compatible metrics.

---

## Current baseline

Noodle already has:

- FastAPI control plane in `apps/api`.
- React Flow editor in `apps/web`.
- Core DAG engine in `packages/core/noodle/engine.py`.
- Node SDK and manifests in `packages/core/noodle/sdk.py` and `packages/core/noodle/models.py`.
- Built-in nodes in `packages/nodes/noodle_nodes`.
- Runtime subprocess pool in `apps/api/app/services/runtime_pool.py`.
- Remote dispatch/runner-pool model in `apps/api/app/services/remote_dispatch.py` and `packages/runner`.
- Runs, node runs, credentials, audit events, artifacts, runner pools, deployments, pinned data.

The architecture is broad; the next work should clarify production semantics and harden execution rather than adding disconnected features.

### Audit findings (2026-05-29)

A code audit before implementation surfaced four facts that change how some tasks
below must be approached. They are folded into the relevant tasks but are listed
here so the assumptions are explicit:

1. **A partial queue already exists, and it is not durable.** `runner.py` already
   uses `Run.status == "queued"`, an `_execute_queued_run()` re-dispatch path, and
   `remote_dispatch.queue_dispatch_loop()` + `RemoteDispatcher.queue_run()` /
   `signal_capacity()`. This queue is **in-memory and remote-only**: it exists to
   re-attempt dispatch when *runner-pool* capacity frees up. It has no durable
   queue rows, no leases, no priority, no attempts/dead-letter, and does not cover
   local subprocess runs. Phase 2 must **subsume and replace** this ad-hoc path
   with the durable queue, not run a second queue alongside it.

2. **Run status vocabulary differs from the plan's state machine.** The code today
   uses `pending`, `running`, `queued`, `success`, `error`, `cancelled`. The plan's
   target states use `succeeded`/`failed`/`dead_lettered`/`leased`/`retrying`. The
   durable queue introduces its own `RunQueueEntry.status` column (see Task 4) and
   **must not** silently rename `Run.status` values that the web UI, `/ops`
   endpoints, and existing tests depend on. Map between the two explicitly; do not
   rename `Run.status` enums in place.

3. **The engine already executes in topological order with no canvas dependence.**
   `engine._topo_order` is a Kahn topological sort with a deterministic `sorted(node_id)`
   tiebreak — it never reads x/y position. Task 17 is therefore mostly a
   **documentation + UI-labelling** task plus an optional switch of the tiebreak from
   node-id order to stable insertion order; the core semantics fix is already shipped.

4. **Sub-workflows intentionally bypass the global concurrency cap.** Wrapping a
   sub-workflow run in the global semaphore deadlocks a parent that holds the only
   slot (see `HANDOFF.md` §7). The durable queue and any lease/backpressure logic
   **must preserve this bypass** for `parent_run_id is not None` runs.

## Target architecture

### Local mode

Use for development, demos, and single-user/self-hosted workflows.

- One FastAPI process.
- React web app served separately or via dev server.
- SQLite allowed.
- In-process scheduler.
- Local warm subprocess runtime pools.
- Local filesystem artifacts.
- Optional Celery/worker disabled.

### Production mode

Use for team/self-hosted production.

- API/editor service: workflow CRUD, auth, credentials, editor sessions, run reads.
- Scheduler leader: cron/polling/persistent trigger ownership.
- Webhook ingress service: receives webhooks and quickly enqueues runs.
- Durable queue: stores run jobs and backpressure state.
- Worker service: executes local subprocess runtimes or dispatches to runner pools.
- Remote runner agents: agent/Docker/Kubernetes providers with heartbeats and leases.
- Postgres metadata DB.
- S3-compatible artifact storage.
- Redis/NATS/RabbitMQ queue, selected and documented as canonical.
- OpenTelemetry collector and metrics scraper.

## Principles

1. Local mode must stay easy.
2. Production mode must not be implicit local mode plus environment variables.
3. Every run must have an explainable state: queued, leased, running, retrying, dead-lettered, cancelled, succeeded, failed.
4. The UI must show bottleneck reason: global limit, workflow limit, environment limit, runner-pool capacity, missing runner, or queue outage.
5. Artifacts/binary data should not live in memory or unbounded DB columns.
6. User code must have an explicit trust boundary.
7. Execution semantics must not depend silently on canvas layout.

---

## Phase 1: Architecture status and configuration clarity

### Task 1: Add runtime mode config

**Objective:** define explicit local vs production runtime mode.

**Files:**
- Modify: `apps/api/app/config.py`
- Test: `apps/api/tests/test_settings_endpoints.py` or new `apps/api/tests/test_config_runtime_mode.py`

**Behavior:**

Add config:

- `RUNTIME_MODE=local|production`
- `QUEUE_BACKEND=none|redis`
- `ARTIFACT_STORAGE_BACKEND=local|s3`
- `SCHEDULER_ROLE=inline|leader|disabled`
- `WEBHOOK_ROLE=inline|ingress|disabled`

Validation:

- `RUNTIME_MODE=local` may use SQLite, local artifacts, inline scheduler.
- `RUNTIME_MODE=production` must warn/fail if using SQLite, local-only artifacts, or no durable queue unless explicitly overridden.

**Verification:**

Run:

```bash
UV_PROJECT_ENVIRONMENT=/tmp/noodle-uv-venv UV_LINK_MODE=copy uv run --package noodle-api pytest apps/api/tests/test_config_runtime_mode.py -q
```

### Task 2: Add `/ops/runtime-mode` endpoint

**Objective:** make the active architecture visible in the UI and API.

**Files:**
- Modify: `apps/api/app/routers/ops.py`
- Modify: `apps/api/app/schemas.py`
- Test: `apps/api/tests/test_ops.py`

Return:

- mode
- database dialect
- queue backend
- scheduler role
- webhook role
- artifact backend
- runner providers enabled
- warnings

### Task 3: Add architecture status docs

**Objective:** remove ambiguity between shipped, beta, scaffolded, and planned.

**Files:**
- Modify: `docs/architecture.md`
- Create: `docs/status-matrix.md`
- Modify: `docs/README.md`

Status categories:

- Shipped
- Beta
- Experimental
- Scaffolded
- Planned

---

## Phase 2: Durable queue and run leases

### Task 4: Define run queue schema

**Objective:** make queued work durable and queryable.

**Files:**
- Modify: `apps/api/app/models.py`
- Create migration under `apps/api/alembic/versions/`
- Test: `apps/api/tests/test_run_queue.py`

Model fields:

- id
- run_id
- workflow_id
- environment_id
- runner_pool_id
- status: queued, leased, running, completed, failed, dead_lettered, cancelled
- priority
- queue_reason
- attempts
- max_attempts
- available_at
- leased_by
- lease_expires_at
- created_at / updated_at

This is a **new** `run_queue` table; it does not replace `Run`. `Run.status`
remains the user-facing run state (`pending`/`running`/`queued`/`success`/`error`/
`cancelled`) and must not be renamed. `RunQueueEntry.status` is the orchestration
state. Keep a documented mapping between the two (e.g. queue `dead_lettered` →
run `error` with a `dead_lettered` detail).

### Task 5: Add queue service interface

**Objective:** isolate queue backend choice from run orchestration.

**Files:**
- Create: `apps/api/app/services/queue.py`
- Test: `apps/api/tests/test_run_queue.py`

Interface:

- enqueue(run_id, reason, priority)
- lease(worker_id, capabilities)
- heartbeat(lease_id)
- complete(run_id)
- fail(run_id, retryable, error)
- cancel(run_id)
- requeue_expired_leases()
- stats()

Start with DB-backed queue for correctness, then add Redis as an optimization/backend.

**Boundary with Celery/Redis:** the DB-backed run queue is the canonical source of
truth for *run* scheduling and backpressure. Celery/Redis stay scoped to optional
scheduler scale-out (Beat) and must not become a second run-dispatch path. When
`QUEUE_BACKEND=redis` is later added it is an implementation of this same interface,
not a parallel system.

### Task 5b: Rewire the run lifecycle onto the queue

**Objective:** make the durable queue the single dispatch path, replacing the
existing in-memory remote-only queued path.

**Files:**
- Modify: `apps/api/app/services/runner.py` (`start_run`, `_execute_run`, `_execute_queued_run`)
- Modify: `apps/api/app/services/remote_dispatch.py` (`queue_dispatch_loop`, `queue_run`, `signal_capacity`)
- Test: `apps/api/tests/test_run_queue.py`, `apps/api/tests/test_runs.py`

**Behavior:**

- `start_run` enqueues a `RunQueueEntry` instead of (or in addition to) directly
  driving dispatch; a queue worker leases entries and calls the existing dispatch.
- Replace `_execute_queued_run` + `signal_capacity` re-attempt logic with
  queue `lease()`/`requeue_expired_leases()`.
- **Preserve the sub-workflow bypass:** runs with `parent_run_id` must not be
  gated by the global cap (Audit finding 4). Either skip the queue for sub-workflow
  runs or give them an uncapped lane.
- Local subprocess runs and remote runner-pool runs both flow through one queue.
- No behavior change in `RUNTIME_MODE=local` default: inline dispatch latency must
  stay effectively immediate when capacity is available.

This is the riskiest task in Phase 2 — it touches the core run path. Land Tasks 4
and 5 (additive) first, then do this rewire behind tests with the full
`test_runs.py` suite green before and after.

### Task 6: Add dead-letter behavior

**Objective:** prevent failing runs from disappearing or retrying forever.

**Files:**
- Modify: `apps/api/app/services/queue.py`
- Modify: `apps/api/app/services/runner.py`
- Test: `apps/api/tests/test_run_queue.py`

Behavior:

- Retry until `max_attempts`.
- Move to dead-letter after final failure.
- Store last error and retry history.
- UI/API can replay dead-lettered runs.

---

## Phase 3: Worker and runner hardening

### Task 7: Add runner leases and heartbeats

**Objective:** safely detect lost workers/agents.

**Files:**
- Modify: `apps/api/app/models.py`
- Modify: `apps/api/app/services/remote_dispatch.py`
- Modify: `packages/runner/noodle_runner_agent/ws_client.py`
- Test: `apps/api/tests/test_runner_pools.py`

Behavior:

- Runner sends heartbeat.
- Assigned run gets lease.
- Lease expires if runner disappears.
- Expired run is requeued or failed depending on idempotency/retry policy.

### Task 8: Separate webhook ingress

**Objective:** avoid webhook bursts starving editor/API responsiveness.

**Files:**
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/routers/webhooks.py`
- Test: `apps/api/tests/test_triggers.py`

Behavior:

- `WEBHOOK_ROLE=ingress` serves production webhook paths and enqueues runs.
- `WEBHOOK_ROLE=disabled` rejects production webhook paths.
- Test webhook paths remain API/editor-local for builder UX.

### Task 9: Scheduler leadership

**Objective:** prevent duplicate schedule firing in multi-process deployments.

**Files:**
- Modify: `apps/api/app/services/triggers.py`
- Create: `apps/api/app/services/leader_election.py`
- Test: `apps/api/tests/test_triggers.py`

Start with DB advisory-lock-style leader record; later support Redis/etcd if needed.

---

## Phase 4: Artifact storage production path

### Task 10: Add artifact backend interface

**Objective:** make local vs S3 artifact storage pluggable.

**Files:**
- Modify: `apps/api/app/services/artifacts.py`
- Test: `apps/api/tests/test_artifacts.py`

Interface:

- put(run_id, node_id, name, bytes, content_type)
- get(artifact_id)
- delete(artifact_id)
- signed_url(artifact_id)
- stats()

### Task 11: Add S3-compatible artifact backend

**Objective:** support production artifacts via S3/MinIO.

**Files:**
- Modify: `apps/api/app/services/artifacts.py`
- Modify: `docs/deployment.md`
- Test: `apps/api/tests/test_artifacts.py`

Use S3-compatible settings:

- endpoint URL
- bucket
- region
- access key credential reference
- secret key credential reference

---

## Phase 5: Observability and backpressure UI

### Task 12: Add queue stats endpoint

**Objective:** let API/UI explain why work is slow.

**Files:**
- Modify: `apps/api/app/routers/ops.py`
- Test: `apps/api/tests/test_ops.py`

Return:

- queued/running/dead-lettered counts
- oldest queued run age
- queue wait p50/p95
- bottleneck counts by reason
- worker/runner capacity

### Task 13: Add run timeline endpoint

**Objective:** expose queue wait, lease, runtime, retries, node timings.

**Files:**
- Modify: `apps/api/app/routers/runs.py`
- Test: `apps/api/tests/test_runs.py`

Return ordered events:

- enqueued
- leased
- started
- node_started
- node_finished
- retry_scheduled
- completed/failed/cancelled

### Task 14: Add UI operations dashboard

**Objective:** make production health visible.

**Files:**
- Modify: `apps/web/src/ExecutionsPage.tsx`
- Modify: `apps/web/src/RunnerPoolsPage.tsx`
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/editor.css`

Show:

- Queue depth
- Oldest queued run
- Worker capacity
- Runner heartbeats
- Environment warm pool status
- Artifact storage usage

---

## Phase 6: Credentials and unsafe-node policy

### Task 15: Add credential test contract

**Objective:** every serious integration can implement test-on-save.

**Files:**
- Modify: `packages/core/noodle/models.py`
- Modify: `apps/api/app/services/credentials.py`
- Test: `apps/api/tests/test_credentials_v2.py`

Add optional credential test handler metadata in node manifests.

### Task 16: Add unsafe node policy

**Objective:** warn/block risky workflows before production activation.

**Files:**
- Modify: `apps/api/app/services/deployments.py`
- Modify: `apps/web/src/EditorPage.tsx`
- Test: `apps/api/tests/test_deployments.py`

Risky nodes:

- Code
- Execute Command
- SSH
- filesystem nodes
- HTTP private IP targets
- SQL with expressions

Policies:

- allow
- warn
- require approval
- block

---

## Phase 7: Execution semantics improvements

### Task 17: Document and enforce branch ordering

**Objective:** guarantee execution order depends only on graph edges, never on
canvas layout, and make that contract visible.

> **Audit note:** `engine._topo_order` already performs a Kahn topological sort with
> a deterministic `sorted(node_id)` tiebreak and never reads x/y position. The core
> semantics fix is **already shipped**. This task is now: (a) document the contract,
> (b) optionally switch the tiebreak from node-id to stable insertion order, and
> (c) surface branch labels in the UI. Add a regression test that shuffling node
> x/y positions does not change execution order.

**Files:**
- Modify: `packages/core/noodle/engine.py`
- Modify: `docs/architecture.md`
- Test: `packages/core/tests/test_engine.py`

Recommendation:

- Use graph/topological order based on edges and stable node insertion order.
- Do not use x/y canvas position for semantics.
- Show branch labels in UI.

### Task 18: Add workflow replay contract

**Objective:** make failed-run debugging reproducible.

**Files:**
- Modify: `apps/api/app/routers/runs.py`
- Modify: `apps/api/app/services/runner.py`
- Test: `apps/api/tests/test_runs.py`

Behavior:

- Replay whole run.
- Replay from failed node using previous successful upstream node outputs.
- Replay with pinned data in manual mode only.

---

## Phase 8: Authoring UX quality pass

### Task 19: Node picker quality pass

**Objective:** make finding nodes fast and understandable.

**Files:**
- Modify: `apps/web/src/editor/NodePalette.tsx`
- Modify: `apps/web/src/editor.css`

Already started:

- category chips
- recommendations
- recents/favorites
- trigger/action/auth/unsafe/output badges

Next:

- keyboard command-palette mode
- exact-match ranking
- operation/resource grouping for integrations

### Task 20: Debug in editor

**Objective:** load failed execution data into the workflow editor.

**Files:**
- Modify: `apps/web/src/ExecutionsPage.tsx`
- Modify: `apps/web/src/EditorPage.tsx`
- Modify: `apps/web/src/editor/store.ts`
- Modify: `apps/api/app/routers/runs.py`

Behavior:

- “Debug in editor” opens workflow editor with run snapshots.
- Failed node is selected.
- Upstream outputs are pinned/cached.
- User can replay from failed node.

---

## Validation commands

Backend/core/nodes:

```bash
UV_PROJECT_ENVIRONMENT=/tmp/noodle-uv-venv UV_LINK_MODE=copy uv run --package noodle-nodes pytest packages/nodes/tests/test_builtin_nodes.py -q
UV_PROJECT_ENVIRONMENT=/tmp/noodle-uv-venv UV_LINK_MODE=copy uv run pytest packages/core/tests apps/api/tests -q
```

On Windows (this dev machine), `uv run` fails to (re)create the project venv
because it tries to make a `lib64` symlink without Developer Mode. Use the
already-built `.venv` interpreter directly instead:

```powershell
# Backend / core / nodes — run from each package dir so `app`/`noodle` import
cd apps/api;        ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd packages\core;   ..\..\.venv\Scripts\python.exe -m pytest tests -q
cd packages\nodes;  ..\..\.venv\Scripts\python.exe -m pytest tests -q
```

Frontend:

```bash
npm install
npm run typecheck
npm run build
```

Run frontend commands from:

```bash
cd apps/web
```

## Recommended implementation order

1. Config/runtime mode status.
2. Durable DB-backed run queue (schema + service), then rewire the run lifecycle
   onto it (Task 5b) with `test_runs.py` green before and after.
3. Queue stats/run timeline APIs.
4. UI backpressure/run timeline.
5. Runner leases/heartbeats.
6. Webhook ingress separation.
7. Scheduler leadership.
8. S3-compatible artifacts.
9. Credential test/OAuth foundations.
10. Unsafe-node policy.

This order gives Noodle production clarity before increasing distributed complexity.

---

## Production-readiness gaps not covered by Tasks 1–20

The 20 tasks harden the architecture, but "production-worthy" also requires the
following operational gaps to be closed. These were surfaced by the 2026-05-29
audit and are tracked here so they are not lost between phases. They are scoped to
the single-tenant, trusted-author target stated at the top.

1. **CI against the real stack.** The suite runs on SQLite in-process. Add a CI
   lane that runs `apps/api` tests against PostgreSQL and exercises at least one
   real subprocess run, so Postgres-only behaviour (row locking in `queue.lease`,
   server defaults, migration parity) is actually covered. Without this, the
   durable-queue concurrency guarantees are untested on the backend that matters.

2. **Graceful drain on shutdown.** `shutdown_active_runs` cancels in-flight runs
   on SIGTERM. Production deploys/restarts need a drain mode: stop leasing new
   queue entries, let leased runs finish (bounded), then exit — otherwise every
   deploy mid-run produces avoidable `cancelled`/requeued runs.

3. **Migration safety in deploy.** Document and enforce the
   migrate-then-start ordering, and confirm every migration is
   forward-only-safe on Postgres (the additive nullable-column pattern in
   `0018`/`0019` is good; keep it).

4. **Queue/lease config surface.** Lease duration, heartbeat interval, retry
   backoff, and `max_attempts` must be configuration (per Task 1's settings), not
   the hard-coded constants currently in `services/queue.py`.

5. **Backpressure honesty in `RUNTIME_MODE=local`.** The queue must add no
   perceptible latency to local single-user runs when capacity is free; the
   checkpoint must measure this, not assume it.

6. **Secret handling unchanged at the new boundary.** Task 5b moves where runs
   are launched; decryption must still happen only at dispatch (`runner._execute_run`)
   and never be persisted on a `RunQueueEntry`. The queue row stores ids and
   status only — never resolved credentials or graph secrets.

7. **License + supply chain.** No license file exists (README notes this). A
   production release needs a license decision and a dependency-audit step.
