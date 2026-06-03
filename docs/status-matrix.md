# Noodle architecture status matrix

Live status of the components called out in
[architecture-improvement-plan.md](architecture-improvement-plan.md). Updated as
tasks land.

> Reconciled against the code on 2026-06-03. Most of Phases 2–6 had shipped well
> ahead of this doc (durable-queue dispatch, dead-letter, leader election, runner
> heartbeats, pluggable + S3 artifacts, credential tests, unsafe-node policy);
> the statuses below now reflect the wired code paths, not the original plan.

Categories:

- **Shipped** — implemented, tested, on the default code path.
- **Beta** — implemented and tested but not yet the default, or behind a flag.
- **Experimental** — partial implementation; do not depend on it.
- **Scaffolded** — types/migrations/interfaces exist but no live code path uses them.
- **Planned** — designed in the plan, no code yet.

## Phase 1 — runtime mode + status

| Task | Area | Status |
| --- | --- | --- |
| 1 | `RUNTIME_MODE`, `QUEUE_BACKEND`, `SCHEDULER_ROLE`, `WEBHOOK_ROLE`, `ARTIFACT_STORAGE_BACKEND` config + production warnings | Shipped |
| 2 | `/ops/runtime-mode` endpoint | Shipped |
| 3 | Architecture status docs (this file) | Shipped |

## Phase 2 — durable queue

| Task | Area | Status |
| --- | --- | --- |
| 4 | `run_queue` table + `RunQueueEntry` model (migration `0020`) | Shipped |
| 5 | `services/queue.py` interface (enqueue / lease / heartbeat / complete / fail / cancel / requeue_expired_leases / stats) — DB backend | Shipped |
| 5b | Run lifecycle rewired through the durable queue (replaces the legacy in-memory requeue path) | Shipped (`services/runner.py` enqueues/leases/marks-running/fails/completes via `services/queue.py`; local runs park as durable `queued` entries when at capacity) |
| 6 | Dead-letter behaviour + replay of dead-lettered runs | Shipped (`queue.fail` → `dead_lettered` after `max_attempts`; `/ops/dead-letter` list + `/ops/dead-letter/replay`) |

The durable queue is now the live dispatch path: `runner.start_run` enqueues a
`RunQueueEntry`, the dispatch loop leases it, and lease expiry/backoff/dead-letter
are exercised end to end. The Postgres CI lane runs the `SELECT ... FOR UPDATE
SKIP LOCKED` lease path against a real backend (see operational gaps below).

## Phase 3 — worker / runner hardening

| Task | Area | Status |
| --- | --- | --- |
| 7 | Runner leases + heartbeats (server-side lease, agent-side heartbeat) | Shipped (`runner_heartbeat_loop` in lifespan; `remote_dispatch` writes `last_seen_at` + marks runners offline past `runner_offline_after_seconds` and requeues their runs; queue `lease`/`requeue_expired_leases`) |
| 8 | Webhook ingress role | Shipped (`WEBHOOK_ROLE=disabled` leaves editor capture mounted but does not mount production `/webhook/{path}` or provider-managed `/provider-webhook/{subscription_id}` ingress) |
| 9 | Scheduler leader election | Shipped (`services/leader_election.py`; `scheduler_role=leader` runs the scheduler/retention loops only while holding the DB advisory lock) |

## Phase 4 — artifacts

| Task | Area | Status |
| --- | --- | --- |
| 10 | Artifact backend interface | Shipped (`services/artifact_backends.py` pluggable backend; `artifact_storage_backend` config) |
| 11 | S3-compatible artifact backend | Shipped (`services/s3_artifact_backend.py`, registered at startup when `artifact_storage_backend=s3`; bucket/region/endpoint config) |

## Phase 5 — observability + backpressure UI

| Task | Area | Status |
| --- | --- | --- |
| 12 | Queue stats endpoint (`/ops/queue`) | Shipped |
| 13 | Run timeline endpoint (`/runs/{id}/timeline`) | Shipped |
| 14 | UI ops dashboard (queue depth, oldest queued, worker capacity, runner heartbeats) | Planned |

## Phase 6 — credentials + unsafe-node policy

| Task | Area | Status |
| --- | --- | --- |
| 15 | Credential test contract (per-type test endpoints) | Shipped (`services/credential_tests.py` + `POST /credentials/{id}/test` and `/credentials/test-handlers`) |
| 16 | Unsafe-node policy (warn / require approval / block on activation) | Shipped (`services/unsafe_nodes.py` enforced in `routers/deployments.py`; `unsafe_node_policy` config) |

## Phase 7 — execution semantics

| Task | Area | Status |
| --- | --- | --- |
| 17 | Topological branch ordering (no canvas-layout dependence) | Shipped (engine sorts topologically; doc + UI labels still planned) |
| 18 | Replay-from-failed-node contract | Beta (`/runs/{id}/retry` reuses upstream outputs; full replay-with-pinned contract not formalised) |

## Phase 8 — authoring UX

| Task | Area | Status |
| --- | --- | --- |
| 19 | Node picker quality pass (category chips, recents, command-palette mode) | Beta (chips + recents shipped; keyboard palette planned) |
| 20 | Debug-in-editor (open run snapshot on canvas) | Planned |

## Operational gaps tracked separately

These are the production-readiness gaps from the plan's appendix that fall
outside the 20 tasks above.

| Gap | Status |
| --- | --- |
| CI against Postgres + real subprocess run | Shipped (`.github/workflows/ci.yml` `postgres` job runs the SKIP-LOCKED queue path + a real `noodle_runtime` subprocess against `postgres:16`; `conftest` honours `NOODLE_TEST_DATABASE_URL`) |
| Graceful drain on SIGTERM | Shipped (`queue_drain` flag stops new leases; lifespan drains in-flight runs within `queue_dispatch_shutdown_timeout_seconds`, then cancels) |
| Migrate-then-start ordering documented + enforced | Partial (compose/Helm run `alembic upgrade` before the API starts; not yet documented as a hard contract) |
| Queue/lease config surface (lease seconds, backoff, max attempts) | Shipped (`queue_lease_seconds`, `queue_retry_backoff_*`, `queue_default_max_attempts`, etc. in `config.py`) |
| Backpressure latency measurement in `RUNTIME_MODE=local` | Planned |
| Secret-handling boundary preserved through Task 5b | Shipped (queue rows store ids/status only; credentials are decrypted at dispatch in `runner._execute_run`, never persisted on `RunQueueEntry`) |
| License + supply-chain audit | Planned (no `LICENSE` yet) |
