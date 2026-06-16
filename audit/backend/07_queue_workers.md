# 07 — Queue & Worker System

Independent review, 2026-06-16. Files: `apps/api/app/services/queue.py`,
`apps/api/app/worker_main.py`, `apps/api/app/services/remote_dispatch.py` (refs),
`apps/api/app/services/leader_election.py` (refs), `apps/api/app/models.py`
(`RunQueueEntry` + indexes).

## Architecture
Custom durable queue over a single `RunQueueEntry` table — **no Celery/RQ/Dramatiq**.
`QUEUE_BACKEND=redis` adds a pub/sub *wakeup* channel on top of DB polling;
`none` is pure DB polling (single-process). Leasing uses Postgres
`SELECT … FOR UPDATE SKIP LOCKED`; SQLite (dev) degrades to plain select (no row
locking, single-process only). Worker = `python -m app.worker_main`
(`DISPATCH_ROLE=worker`), API replicas enqueue + optionally dispatch
agent/k8s pools (`control`).

## What is solid (verified)
- **Idempotent enqueue** — one entry per `run_id`; re-enqueue of a live entry is
  a no-op, of a terminal entry revives the row (no duplicates) (`queue.py:152-205`).
- **Concurrency-safe leasing** — `with_for_update(skip_locked=True)` *only* on
  Postgres (`queue.py:331`); two workers never lease the same row.
- **Fair scheduling (anti-starvation)** — `_org_fair_order` leases from the org
  with fewest in-flight first, enforces per-org `max_concurrent_runs`, and tags
  capped orgs `queue_reason="org_quota_exceeded"` for the backpressure UI. A
  1000-entry flood from one tenant interleaves instead of starving others
  (`queue.py:208-273`).
- **Retry with exponential backoff** — `base * 2^(attempts-1)` capped at
  `queue_retry_backoff_max` (`queue.py:456-462`).
- **Dead-letter + replay** — attempts exhausted → `dead_lettered` (terminal,
  replayable); non-retryable → `failed`. `attempts_log` records every transition
  with correct JSON dirty-flag handling (rebinds the attribute — a real
  SQLAlchemy gotcha handled correctly) (`queue.py:428-494`).
- **Crash recovery (the critical one)** — `requeue_expired_leases` reclaims
  `leased` entries past `lease_expires_at` (worker presumed lost), requeueing if
  attempts remain else failing; **confirmed invoked** by `run_queue_dispatch_loop`
  (`queue.py:786`) and backed by a dedicated index (`models.py:981`). Heartbeat
  (`heartbeat`) extends leases for long runs.
- **Graceful drain** — `queue_drain` stops leasing while in-flight runs finish;
  `worker_main` wires SIGTERM/SIGINT → drain → timeout → cancel.
- **Approval parking** — `wait_for_approval`/`resume_waiting` park a run off the
  active set and resume it with a `replay_seed`.

## Findings

### QUEUE-1 — Fair pre-pass caps at 5 orgs per lease; possible wasted ticks (LOW)
With multi-tenancy on, `lease` tries only the 5 fairest orgs
(`(await _org_fair_order(...))[:5]`, `queue.py:339`). If all 5 drain between the
pre-pass and the locked select, `lease` returns `None` even though a 6th org has
eligible work; the entry waits until the next poll tick (`queue_dispatch_poll_seconds`,
default 1s).
- **Impact:** at most ~1s extra latency under heavy multi-tenant contention; not a
  correctness issue. Throughput is fine because the wakeup channel re-pokes.
- **Fix (optional):** loop the whole fair order, or retry the pre-pass once on a
  full miss before yielding the tick.
- **Status:** Reviewed — low priority.

### QUEUE-2 — SQLite path is single-process only (INFO, by design)
SKIP LOCKED is Postgres-only; on SQLite two dispatch loops could double-lease.
This is already enforced elsewhere (`dispatch_topology_errors()` requires
Postgres for `worker/control/disabled`), so it can't happen in a split topology —
just call it out in deployment docs so nobody runs two workers on SQLite.
- **Status:** Doc.

### QUEUE-3 — Observability: queue depth/lag metrics (LOW–MEDIUM)
There's a `stats` surface and the dead-letter ops API, but confirm Prometheus/OTel
counters exist for: queue depth by status, oldest-eligible age (lag), lease
reclaim count, dead-letter rate. These are the four signals an operator needs to
detect a stuck dispatch loop or a worker outage. (OTel is present but off by
default — `otel_enabled`.)
- **Status:** Needs verification / likely enhancement.

## Keep / replace / simplify recommendation
**Keep the custom queue.** It is well-matched to the product: durable, DB-backed
(no extra broker to operate beyond Postgres), SKIP-LOCKED-correct, fair, with
retry/dead-letter/replay/crash-recovery already implemented and tested. Adopting
Celery/RQ would add an operational dependency and *lose* the org-fair scheduling
and run-state integration that are first-class here. The only thing Redis adds
(wakeup latency) is already optional and gracefully degrades to polling. Invest in
**metrics (QUEUE-3)** rather than a rewrite.

## Tests to add
- Crash recovery: lease an entry, advance clock past `lease_expires_at`, assert
  `requeue_expired_leases` requeues (attempts remain) / fails (exhausted).
- Fairness: one org with 100 queued + another with 1 queued → the singleton org
  leases within the first few dispatches (CI Postgres lane).
