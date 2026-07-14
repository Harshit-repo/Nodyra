# Nodyra 10/10 Production Plan — LLM-Implementable Edition

**Date:** 2026-07-12
**Branch convention:** one branch per work item: `tenten/<ITEM-ID>-short-slug`
**Companion doc:** `FABLE5_NODYRA_FULL_TEST_BUGFIX_AND_PRODUCTION_IMPROVEMENT_PLAN.md` (evidence and current-state audit this plan is built on)

This plan takes every area of Nodyra from its current verified score to 10/10. It is written so that **any LLM (or engineer) can pick up a single work item and implement it without additional context**: every item names the exact files, the steps, the acceptance criteria, and the verification commands. Items are independent unless a `Depends:` line says otherwise.

---

## 0. Global Conventions (read first, apply to every item)

### 0.1 Environment
- Repo root: the active Windows workspace. Python venv: `.venv\Scripts\python.exe` (Python 3.12.5). Node 22.
- Backend tests: `.\.venv\Scripts\python.exe -m pytest -q --timeout=180 -p no:cacheprovider` from repo root (runs `apps` + `packages` per `pyproject.toml testpaths`).
- Frontend: `cd apps\web` then `npx tsc`, `npx vitest run`, `npm run build`, `npx playwright test --config e2e/playwright.config.ts`.
- Lint: `.\.venv\Scripts\python.exe -m ruff check .` must stay "All checks passed!".

### 0.2 Rules for every work item
1. Read the target files fully before editing. Match existing style (comments explain *why*; 100-char lines; `ruff` select E,F,I,UP,B).
2. Smallest safe change. No drive-by refactors. No new dependencies unless the item says so.
3. Every behavior change ships with a test in the same commit. Regression tests go next to the closest existing test file.
4. New settings go in `apps/api/app/config.py` with a comment block explaining the default, and a line in `.env.example`.
5. New endpoints: add RBAC via `require_permission(...)` from `apps/api/app/security.py`; add the permission to `_PERMISSION_MIN_ROLE` if new.
6. DB changes: new Alembic revision in `apps/api/alembic/versions/` numbered `0087_...` onward, with `upgrade()` **and** `downgrade()`. Run `python -m alembic upgrade head` against a fresh SQLite DB and confirm CI's Postgres drift lane passes.
7. Frontend types mirror backend schemas: update `apps/web/src/types.ts` in the same commit as any `apps/api/app/schemas.py` change.
8. Never log or persist secrets. Any new output path that can carry node data must pass through `redact_value` (see `apps/api/app/services/runner.py` usage of `apps/api/app/services/redaction.py`).
9. Commit message format: `<type>(<area>): <summary>` + body explaining why. Types: feat, fix, perf, docs, test, chore, sec.
10. Definition of done for every item: code + tests + docs touched, `ruff` clean, `tsc` clean (if web touched), targeted tests pass, full suite not regressed (run at least the affected test files), acceptance criteria demonstrably met.

### 0.3 Severity/priority legend
- **P0** do first (safety/trust), **P1** before public/multi-tenant GA, **P2** high value, **P3** polish. Complexity: **S** (<½ day), **M** (½–2 days), **L** (2–5 days).

### 0.4 Master execution order (phases)
- **Phase A (trust & correctness):** RQ-1, RQ-2, SBX-1, SEC-1, SEC-2, MCP-1, BE-1, TEST-1
- **Phase B (production operations):** DEP-1..3, OBS-1..3, PERF-1..2, ART-1, PY-1
- **Phase C (product surface):** ONB-1..3, UI-1..6, AI-1..3, NODE-1..3, DF-1..2
- **Phase D (scale & enterprise):** ENG-1..2, FE-1..2, MCP-2..3, SEC-3, DOC-1..3, DIFF-1..2, TEST-2..3
Within a phase, items are parallelizable unless `Depends:` says otherwise.

---

## 1. Runners / Workers / Queue — current 8/10 → 10/10

### RQ-1 — Chaos/soak test lane for the durable queue and workers (P0, L)
**Why:** the queue logic (`apps/api/app/services/queue.py`) is unit-green but worker-death, lease-expiry, and burst behavior have no automated end-to-end evidence. `docs/soak-testing.md` and `scripts/soak_test.py` exist but are not a CI lane.
**Files:** new `apps/api/tests/chaos/test_worker_chaos.py`; `scripts/soak_test.py` (extend); `.github/workflows/ci.yml` (new job `chaos`, `if: github.event_name == 'schedule' || contains(github.event.pull_request.labels.*.name, 'chaos')`, nightly cron).
**Steps:**
1. Build an async harness that starts the app with `DATABASE_URL` = the CI Postgres service (mirror the existing "Postgres queue + real subprocess" job's env in `ci.yml`), `dispatch_role=inline`, `queue_backend=redis` (CI Redis service).
2. Scenarios (each its own test): (a) enqueue 50 runs of a 3-node code workflow, assert all reach terminal state, none duplicated (`RunQueueEntry` count == runs, no run executed twice — assert via `NodeRun` counts); (b) lease an entry with `queue.lease(...)`, simulate worker death by never heartbeating, advance `lease_expires_at` into the past via direct UPDATE, run `queue.requeue_expired(...)` (see the expiry sweep used by the dispatch loop in `apps/api/app/services/queue.py` around the `lease_expires_at.is_not(None)` query), assert the entry re-leases and the run completes; (c) exhaust `max_attempts` with a workflow whose code node raises, assert entry lands `dead_lettered` with populated `attempts_log`, then replay via the ops dead-letter replay endpoint (`apps/api/app/routers/ops.py`) and assert it re-runs; (d) cancel mid-run via `POST /runs/{id}/cancel` and assert queue entry status `cancelled`, no zombie subprocess (poll `psutil` children if available; otherwise assert the runtime pool released the slot via `/ops/pool` stats).
3. Wire `scripts/soak_test.py` to run scenario (a) at 500 runs in the nightly job only.
**Acceptance criteria:** all four scenarios pass 20 consecutive runs locally (`--count 20` via pytest-repeat is NOT installed — loop in bash instead); nightly CI lane green 7 days.
**Verify:** `.\.venv\Scripts\python.exe -m pytest apps/api/tests/chaos -q` (SQLite fallback must skip-with-reason on scenarios needing SKIP LOCKED, using the same guard style as existing Postgres-only tests — grep `NODYRA_TEST_DATABASE_URL` in `apps/api/tests/conftest.py`).

### RQ-2 — Docker-workers autoscaler burn-in + safety rails (P0, M)
**Why:** `apps/api/app/services/docker_workers.py` (autoscale loop, `spawn_docker_runner`) is the newest code; it has unit tests (`apps/api/tests/test_docker_workers.py`, `test_docker_workers_api.py`) but no failure-injection coverage.
**Files:** `apps/api/app/services/docker_workers.py`; `apps/api/tests/test_docker_workers.py` (extend).
**Steps:**
1. Add tests with a fake docker client (follow the existing fake pattern in `apps/api/tests/test_docker_workers.py`): (a) `containers.run` raises mid-scale-up → placeholder Runner row rolled back (already coded — lock it in with a test asserting no orphan `Runner` rows); (b) autoscale loop tick with daemon unreachable → loop logs and continues (no crash, no tight spin — assert one warning per tick); (c) scale-down never removes a runner with in-flight runs (assert via a Runner with `Run.status=='running'` attached).
2. Add a max-consecutive-failure circuit breaker to the autoscale loop: after 5 consecutive daemon errors, back off to 10× `docker_autoscale_tick_seconds` until one success. New setting not required — derive from existing `docker_autoscale_tick_seconds`.
**Acceptance criteria:** the three failure tests pass; breaker verified by a test that counts sleep intervals via monkeypatched `asyncio.sleep`.
**Verify:** `pytest apps/api/tests/test_docker_workers.py -q`.

### RQ-3 — Worker observability: per-worker live view (P2, M)
**Why:** ops endpoints exist (`/ops/queue`, `/ops/pool`, `/ops/replicas` in `apps/api/app/routers/ops.py`) but there is no single "workers" panel joining runners, leases, and heartbeats.
**Files:** `apps/api/app/routers/ops.py` (new `GET /ops/workers`); `apps/api/app/schemas.py`; `apps/web/src/RunnerPoolsPage.tsx` (add a "Live workers" table fed by the new endpoint); `apps/web/src/types.ts`; new test `apps/api/tests/test_ops_workers.py`.
**Steps:** endpoint returns, per `Runner` row: id, name, pool, `last_seen_at`, capabilities, current leased/running entry count (join `RunQueueEntry.leased_by`), labels. RBAC: `ops:pool:read`. Frontend: table with stale-heartbeat highlighting (>60 s → amber, offline per `runner_offline_after_seconds` → red).
**Acceptance criteria:** endpoint returns correct counts in test with two fake runners; page renders (vitest snapshot with mocked query).
**Verify:** `pytest apps/api/tests/test_ops_workers.py -q && cd apps/web && npx vitest run src/RunnerPoolsPage.dockerWorkers.test.tsx`.

---

## 2. Sand
