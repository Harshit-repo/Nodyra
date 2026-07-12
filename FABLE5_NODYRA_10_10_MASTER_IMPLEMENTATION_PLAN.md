# Nodyra 10/10 Master Implementation Plan

**Date:** 2026-07-12 · **Author:** Fable 5 · **Branch baseline:** `fable5-nodyra-full-test-bugfix-production-plan` (commit `772b068a`)
**Companion evidence:** `FABLE5_NODYRA_FULL_TEST_BUGFIX_AND_PRODUCTION_IMPROVEMENT_PLAN.md` (verified test results, live E2E, security probes)

This plan takes every area of Nodyra from its current verified score to 10/10. It is written so that **any competent LLM (or human) can pick up any task and implement it without additional context**: every task names its real files (all paths verified to exist in the current tree), gives step-by-step instructions, defines machine-checkable acceptance criteria, and lists the exact verification commands. The plan was self-checked twice (see §12: Verification Log) — every cited path, endpoint, and "current state" claim was re-confirmed against the codebase, and coverage/consistency was re-audited after drafting.

---

## 1. How to use this plan (Implementation Protocol for LLMs)

Follow this protocol for **every** task. Do not skip steps. Do not fake results.

### 1.1 Environment
- Repo root: the directory containing `pyproject.toml` with `[tool.uv.workspace]`.
- Python: use the existing venv (`.venv/Scripts/python.exe` on Windows, or `uv run` with Python ≥3.12). Backend tests: `python -m pytest -q --timeout=180`.
- Frontend: `cd apps/web` then `npm run typecheck`, `npx vitest run`, `npm run build`, `npx playwright test --config e2e/playwright.config.ts`.
- Lint: `python -m ruff check .` from repo root (must stay "All checks passed").
- Local API for manual verification: `cd apps/api && python -m uvicorn app.main:app --port 8000` with `DATABASE_URL=sqlite+aiosqlite:///<abs-path>/dev-plan.db` after `python -m alembic upgrade head`.

### 1.2 Per-task loop
1. **Branch:** `git checkout -b <task-id>-short-description` from the integration branch.
2. **Read first:** open every file listed under "Files"; read enough surrounding code to match existing conventions (the codebase is heavily commented with rationale — preserve that style).
3. **Test first when feasible:** write the failing regression/acceptance test before the implementation.
4. **Smallest safe diff:** do not reformat unrelated code; do not rewrite working subsystems.
5. **Verify:** run the task's "Verify" commands *and* `python -m ruff check .` *and* the nearest test file(s). Record real output.
6. **Migrations:** new DB columns/tables require a new file in `apps/api/alembic/versions/` numbered after the current head (`0086_run_queue_required_labels.py` at time of writing — re-check with `ls apps/api/alembic/versions | sort | tail`). CI has a migration-drift lane; models and migrations must agree.
7. **Commit:** `<task-id>: <imperative summary>` with a body explaining root cause/approach. One task per PR.
8. **Update this plan:** flip the task's checkbox and append one line to §12 Verification Log with the commands you ran and their real results.

### 1.3 Hard rules
- Never mark a task done with failing or skipped-because-broken tests.
- Never widen security posture (no new default-open flags; anything risky must fail closed like `security_startup_errors()` in `apps/api/app/config.py`).
- Every user-visible error message must say what happened *and* what to do next.
- Every new setting goes in `apps/api/app/config.py` **and** `.env.example` **and** (if deploy-relevant) `deploy/docker-compose.yml` + `deploy/helm/nodyra/values.yaml`.
- New endpoints need RBAC via `require_permission(...)`/`require_role(...)` from `apps/api/app/security.py`, org-scoping awareness (`apps/api/app/tenancy.py`), and a test in `apps/api/tests/`.

### 1.4 Scoring rubric (what 10/10 means)
An area is 10/10 when: (a) all its P1+P2 tasks below are done; (b) its acceptance criteria are enforced by CI (not by hope); (c) an operator/user can discover and use the capability without reading source code; (d) failure modes are observable (metric or UI or log with actionable message).

---

## 2. Current scores → targets

| # | Area | Current | After P1s | 10/10 when |
|---|---|---|---|---|
| 1 | UI/UX | 7.5 | 8.5 | UX-01..06 + ONB-01..03 done |
| 2 | Frontend architecture | 8 | 8.5 | FE-01..03 done |
| 3 | Backend architecture | 9 | 9 | BE-01..03 done |
| 4 | Workflow engine | 9 | 9.5 | ENG-01..03 done |
| 5 | Node system | 8.5 | 8.5 | NODE-01..03 done |
| 6 | Node-to-node data flow | 8.5 | 8.5 | DATA-01..03 done |
| 7 | Artifacts/result storage | 8.5 | 8.5 | ART-01..02 done |
| 8 | MCP | 8.5 | 9 | MCP-01..05 done |
| 9 | AI builder | 8 | 9 | AI-01..03 done |
| 10 | Python code execution | 8.5 | 8.5 | PY-01..02 done |
| 11 | Sandbox | 8 | 9 | SBX-01..03 done |
| 12 | Runners/workers/queue | 8 | 9.5 | RUN-01..03 done |
| 13 | Deployment/env | 8.5 | 9.5 | DEP-01..03 done |
| 14 | Security | 8.5 | 9.5 | SEC-01..05 done |
| 15 | Performance | 8 | 8.5 | PERF-01..03 done |
| 16 | Observability | 8 | 8.5 | OBS-01..03 done |
| 17 | Testing/CI | 9 | 9.5 | CI-01..05 done |
| 18 | Documentation | 7.5 | 8 | DOC-01..03 done |
| 19 | Onboarding | 7 | 8.5 | ONB-01..03 done |
| 20 | Product differentiation | 8 | 8.5 | DIF-01..03 done |

Phase completion (§4) is what actually moves areas to 10 — the tables in §5 map every task to its area.

---

## 3. Ground-truth notes for implementers (verified, so you don't re-litigate them)

These were **verified against the current code** during planning — do not "fix" them, they already work:
- Frontend routes are **already lazy-loaded** (`apps/web/src/App.tsx` uses `lazy()`/`Suspense` per page). The oversized chunk is vendor weight (Monaco, @xyflow/react, chart libs), not missing route splitting → FE-01 targets `manualChunks`, not `lazy()`.
- Rate limiting is **already Redis-backed** with logged per-process fallback (`apps/api/app/services/rate_limit.py::_allow_redis`) → SEC-02 adds a multi-replica *test*, not an implementation.
- OIDC is implemented; **SAML is stubs** (`apps/api/app/services/sso.py` header comment) → ENT-01 scope is SAML + SCIM, not OIDC.
- Backup/restore doc exists (`docs/backup-restore.md`, includes pg_dump + volume tar + SECRET_KEY warning) → ENT-03 scope is an automated *restore drill*, not writing the doc.
- A Python SDK + CLI already exist (`packages/client/nodyra_client/{client.py,cli/main.py}`) → PY/DIF tasks extend them.
- The git index tracks 1,332 files and **no** logs/DBs/dist junk; however the *working tree* carries ~500 modified files of line-ending churn from a previous session → HK-01.
- `security_startup_errors()` already hard-fails on default SECRET_KEY + auth, wildcard CORS + credentials, blank internal token in split topology, bad KMS config; `sandbox_policy_strict` (default true) already forces `execution_sandbox=required` under multi-tenancy (`apps/api/app/services/sandbox_policy.py`).
- Run-level failure reasons are now surfaced on `RunInfo.error` (fixed in `7216dcf0`; regression test `apps/api/tests/test_runs.py::test_run_level_error_is_surfaced_on_run_info`). ENG-02 denormalizes this into a column.
- Ops surface already exists: `/ops/runtime-mode`, `/ops/queue`, `/ops/drain`, dead-letter list/replay, `/metrics` (`apps/api/app/routers/ops.py`) → UX-01 and OBS-02 build on these, they don't create them.

---

## 4. Phases and ordering (dependency-safe)

| Phase | Goal | Tasks | Gate to next phase |
|---|---|---|---|
| **0. Housekeeping** (days) | Clean baseline | HK-01, MCP-04, CI-03 | Working tree clean; full suite green twice consecutively |
| **1. Trust & correctness** (1–2 wk) | Prove the execution plane under failure | RUN-01, CI-02/DEP-01, SEC-01/SBX-01, MCP-01, ENG-02 | Chaos lane + compose smoke green in CI 5 consecutive runs |
| **2. Adoption surface** (1–2 wk) | Convert engineering into product | UX-01, UX-02, ONB-01, ONB-02, AI-01 | New-user path: signup→credential→template→green run ≤10 min, measured |
| **3. Platform depth** (2–3 wk) | P2 correctness/completeness | ENG-01, BE-01..03, NODE-01..02, DATA-01..02, ART-01..02, SBX-02..03, MCP-02..03, PY-01 | All area P2 ACs green in CI |
| **4. Performance & observability** (1 wk) | Measure, then tune | FE-01..03, PERF-01..03, OBS-01..02 | Dashboards live; budgets enforced in CI |
| **5. Enterprise** (2–3 wk) | Sellable to orgs | ENT-01..04, OBS-03, SEC-05 | Restore drill automated; SSO E2E test green |
| **6. Differentiation** (ongoing) | Widen the moat | AI-02..03, PY-02, DIF-01..03, NODE-03, DOC-01..03 | — |

Rules: within a phase tasks are parallelizable unless a dependency is noted. P3 tasks may be done opportunistically but never before Phase 1 completes.

---

## 5. Tasks by area

Field legend — **P:** priority · **C:** complexity (S/M/L) · **Files:** verified real paths · **AC:** acceptance criteria · **Verify:** commands whose real output proves the AC.

### 5.0 Housekeeping (HK)

- [ ] **HK-01 — Resolve the line-ending churn in the working tree** · P1 · C:S
  **Files:** ~500 modified files (run `git status --short | wc -l`), `.gitattributes`
  **Steps:** (1) Confirm churn is CRLF-only: `git diff --stat` should show ≈equal +/- on docs/specs; spot-check 3 files with `git diff -- <file> | head -50` — real changes live in `packages/runner/nodyra_runner_agent/sandbox_exec.py` (+23), `packages/runner/tests/test_sandbox_exec.py` (+57), `packages/nodes/nodyra_nodes/llm.py` and siblings. (2) Commit the real changes first, in their own commit, with tests run. (3) Commit the normalization as a separate `chore: normalize line endings per .gitattributes` commit (or `git checkout --` it if the team prefers; decide once, document in commit body). Never mix the two.
  **AC:** `git status --short` empty; full pytest + vitest green after.
  **Verify:** `git status --short | wc -l` → 0; `python -m pytest -q --timeout=180` → 0 failed.

### 5.1 Runners / Workers / Queue (RUN) — current 8

- [ ] **RUN-01 — Chaos & soak CI lane for the execution plane** · **P1** · C:M
  **Files (new):** `scripts/chaos_test.py`, `.github/workflows/ci.yml` (new job `chaos`), reuse `scripts/soak_test.py` + `docs/soak-testing.md`
  **Steps:** Compose-boot postgres+redis+api+worker (see CI-02's harness — build once, share). Script scenarios, each asserting via REST: (a) enqueue 50 runs (simple 2-node code workflow), assert all reach terminal state ≤N min, none lost/duplicated (`/ops/queue` totals reconcile with run list); (b) `docker kill` the worker mid-run → assert lease expires (`queue_lease_seconds=10` for the lane) and the run is re-leased and completes after worker restart; (c) force a run to fail 3× (code node raising) → assert it dead-letters and `/ops/dead-letter` replay re-runs it; (d) cancel a running run → terminal `cancelled`, container/subprocess gone (`docker ps` / process table check); (e) hung code node (sleep > timeout) → node timeout error, run `error`, worker stays healthy for the next run.
  **AC:** All 5 scenarios pass in CI on Postgres+Redis; total lane time ≤15 min; zero orphaned containers/processes at teardown (script asserts).
  **Verify:** `python scripts/chaos_test.py --base http://localhost:8000` locally, then the CI job on a PR.
  **Depends on:** CI-02 harness.

- [ ] **RUN-02 — Protocol heartbeat for sandboxed attach streaming** · P2 · C:S
  **Files:** `packages/runner/nodyra_runner_agent/sandbox_exec.py`, `packages/runtime/` (emit side), `packages/runner/tests/test_sandbox_exec.py`
  **Steps:** Today the only wedge protection is a 3600 s socket timeout (`sock.settimeout(3600.0)`). Add a periodic `{"type":"hb"}` line from `nodyra_runtime` every 30 s; in `run_workflow_sandboxed`, drop socket timeout to 120 s and treat `hb` as a no-op keepalive. If no bytes for 120 s → kill container, emit `run_error` "sandbox runtime stopped responding (no heartbeat for 120s)".
  **AC:** Wedged-container test (fake socket yielding nothing) fails the run in ≤130 s with the actionable message; normal runs unaffected.
  **Verify:** `python -m pytest packages/runner/tests/test_sandbox_exec.py -q`.

- [ ] **RUN-03 — Worker logs in UI** · P2 · C:M
  **Files:** `apps/api/app/routers/runner_pools.py`, `apps/api/app/services/docker_workers.py`, `apps/web/src/RunnerPoolsPage.tsx`, test `apps/api/tests/test_docker_workers_api.py`
  **Steps:** Add `GET /runner-pools/{pool_id}/runners/{runner_id}/logs?tail=500` — for docker-managed runners call `container.logs(tail=...)` in an executor (container name is in `runner.capabilities["container_name"]`, set by `spawn_docker_runner`); for WS agents return the runner's recent heartbeat/dispatch history instead with a clear "logs available for docker-managed workers only" message. RBAC: `runner_pool:write`. Redact with `app.services.redaction.redact_value` before returning. UI: "View logs" on the worker card, monospace modal, auto-refresh 5 s toggle.
  **AC:** Logs render within 2 s for a docker worker; secrets never appear (test seeds a credential value into logs and asserts redaction); non-docker runner shows the friendly message, not a 500.
  **Verify:** `python -m pytest apps/api/tests/test_docker_workers_api.py -q`; manual UI check.

- [ ] **RUN-04 — Worker capability autodetection** · P3 · C:S
  **Files:** `packages/runner/nodyra_runner_agent/` (agent startup), `apps/api/app/routers/internal.py` (registration payload)
  **Steps:** On agent start, detect `gpu` (nvidia-smi present), `mem_gb` (psutil), `docker` (daemon ping) and merge into advertised labels unless explicitly overridden.
  **AC:** Registered runner rows show detected labels; explicit `worker_labels` still win.

### 5.2 Deployment / Environment (DEP) — current 8.5

- [ ] **DEP-01 / CI-02 — Compose-up smoke lane in CI** · **P1** · C:M
  **Files:** `.github/workflows/ci.yml` (new job `compose-smoke`), `deploy/docker-compose.yml`, new `scripts/compose_smoke.py`
  **Steps:** GitHub ubuntu runners have Docker. Job: `docker compose -f deploy/docker-compose.yml up -d --build` with a generated non-default `SECRET_KEY` env; wait for api+worker+web health (compose healthchecks exist); then via REST: create workflow (manual_trigger→code), run, poll to `success`; upload+download one artifact; hit `/metrics`; `docker compose down -v`. Fail the job on any non-2xx or timeout ≥180 s.
  **AC:** Green on PRs; total ≤12 min; proves image build + migration + queue dispatch + artifact volume wiring on every change.
  **Verify:** the CI job itself; locally `python scripts/compose_smoke.py` against a manual compose-up.

- [ ] **DEP-02 — SBOM + image signing on release** · P2 · C:S
  **Files:** `.github/workflows/release.yml`
  **Steps:** Add steps: `anchore/sbom-action`