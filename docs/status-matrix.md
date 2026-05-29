# Noodle architecture status matrix

Live status of the components called out in
[architecture-improvement-plan.md](architecture-improvement-plan.md). Updated as
tasks land.

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
| 5b | Run lifecycle rewired through the durable queue (replaces `_QueuedError` + `_execute_queued_run`) | Planned |
| 6 | Dead-letter behaviour + replay of dead-lettered runs | Scaffolded (queue.py knows `dead_lettered`; runner does not yet route to it) |

The queue service is fully functional, but `runner.start_run` still uses the
legacy in-memory remote-only requeue path. Until Task 5b lands the durable
queue is dormant on the dispatch path — only its schema and service API are
exercised.

## Phase 3 — worker / runner hardening

| Task | Area | Status |
| --- | --- | --- |
| 7 | Runner leases + heartbeats (server-side lease, agent-side heartbeat) | Planned (lease columns exist on `RunQueueEntry`; agent has no heartbeat) |
| 8 | Webhook ingress role | Scaffolded (`webhook_role` config exists; `routers/webhooks.py` does not branch on it) |
| 9 | Scheduler leader election | Planned |

## Phase 4 — artifacts

| Task | Area | Status |
| --- | --- | --- |
| 10 | Artifact backend interface | Planned (`services/artifacts.py` is local-FS only) |
| 11 | S3-compatible artifact backend | Planned |

## Phase 5 — observability + backpressure UI

| Task | Area | Status |
| --- | --- | --- |
| 12 | Queue stats endpoint (`/ops/queue`) | Shipped |
| 13 | Run timeline endpoint (`/runs/{id}/timeline`) | Shipped |
| 14 | UI ops dashboard (queue depth, oldest queued, worker capacity, runner heartbeats) | Planned |

## Phase 6 — credentials + unsafe-node policy

| Task | Area | Status |
| --- | --- | --- |
| 15 | Credential test contract (per-type test endpoints) | Beta (`services/credential_tests.py` exists; node-manifest test handler metadata not wired) |
| 16 | Unsafe-node policy (warn / require approval / block on activation) | Planned |

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
| CI against Postgres + real subprocess run | Planned |
| Graceful drain on SIGTERM | Planned (`shutdown_active_runs` cancels; no drain mode) |
| Migrate-then-start ordering documented + enforced | Planned |
| Queue/lease config surface (lease seconds, backoff, max attempts) | Planned (constants in `services/queue.py`) |
| Backpressure latency measurement in `RUNTIME_MODE=local` | Planned |
| Secret-handling boundary preserved through Task 5b | Planned (no resolved credentials may land on `RunQueueEntry`) |
| License + supply-chain audit | Planned |
