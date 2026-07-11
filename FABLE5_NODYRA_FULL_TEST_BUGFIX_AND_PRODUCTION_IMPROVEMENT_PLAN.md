# Fable 5 Nodyra Full Test, Bugfix, and Production Improvement Plan

**Date:** 2026-07-12
**Branch:** `fable5-nodyra-full-test-bugfix-production-plan`
**Method:** Every conclusion below was verified against the current codebase, current tests, current config, and live runtime behavior. Old audit documents were used only for background; nothing was accepted on faith. All test results are reported honestly, including the one failure found.

---

## Executive Summary

Nodyra is in remarkably strong shape. This is not the codebase the older audit files describe — it has visibly absorbed several hardening passes, and the current state verifies out: **3,090 backend tests passed, 95 skipped, 1 failed (a timing flake, now fixed)** across the API, engine, nodes, runner, and client packages; the frontend's 63 test files pass; `tsc`, `vite build`, and `ruff` are clean; the repo's own 7-scenario Playwright E2E suite passes; and live end-to-end drives of the REST API and the MCP server (create → set graph → validate → run → success) work first try against a fresh migrated database (86 Alembic revisions apply cleanly).

### Production readiness scores

| Area | Score | Basis |
|---|---|---|
| **Overall** | **8.5/10** | Single-tenant production-credible today; multi-tenant needs operational burn-in |
| Backend | 9/10 | Fail-closed startup guards, RLS + ORM org scoping, durable queue, structured logs |
| Frontend | 8/10 | Clean build/tests; bundle >500 kB chunk warning; run-level errors were invisible (fixed) |
| Workflow engine | 9/10 | Deterministic topo order, dependency-counting parallel scheduler, loop/metanode/subworkflow support, output caps, deadline handling |
| Node system | 8.5/10 | ~50 node modules, SSRF-guarded egress, credential redaction; per-node metadata quality varies |
| MCP | 8.5/10 | Spec-conformant streamable-HTTP server, RBAC per tool, write-approval gate, OAuth resource metadata, SSRF-pinned client |
| Runners/workers | 8/10 | Lease/heartbeat/requeue lifecycle, labels, drain; Docker-worker autoscaler is the newest, least-proven code |
| Queue | 9/10 | SKIP LOCKED leasing, org-fair scheduling, backoff, dead-letter, attempts log |
| Sandbox | 8/10 | cap-drop/read-only/non-root floor, dedicated bridge, per-env images; sibling-container via docker.sock is a deliberate but real trust tradeoff |
| Artifacts | 8.5/10 | Local + S3 backends, size caps, offload-after-cap ordering correct, checksums |
| Deployment/env | 8.5/10 | Compose + Helm + split api/worker, migration job, health/readiness, secrets via env; `.env.example` documents 58 vars |
| Security | 8.5/10 | Verified live: SSRF blocked (loopback/metadata/RFC1918/IPv6), default-secret startup guard, CSRF, webhook auth constant-time, redaction at event boundary |
| Performance | 8/10 | Bulk inserts, bounded caches, pool tuning exposed; frontend bundle and unbounded tool lists are the visible gaps |
| UI/UX | 7.5/10 | Feature-complete editor; discoverability and first-run guidance lag the engineering depth |

### Biggest strengths
1. **Fail-closed configuration.** `security_startup_errors()` aborts startup on default SECRET_KEY with auth on, blank internal token in split topology, wildcard CORS with credentials, and misconfigured KMS. This is rare discipline.
2. **The engine is genuinely production-grade.** Deterministic ordering contract (canvas position never affects execution), worst-status aggregation, run deadlines, per-type concurrency semaphores, and a worker-pool scheduler that avoids O(N) `asyncio.wait` rounds.
3. **Defense-in-depth SSRF.** URL pre-check + per-hop redirect re-validation + connect-time DNS-rebinding re-check via patched `socket.create_connection` + credential stripping on cross-origin redirects. Verified live.
4. **Test culture.** 130 API test files, packages tests, Playwright E2E with its own API harness, CI lanes for Postgres queue semantics, migration drift, pip-audit (blocking), bandit and mypy on security-critical modules.

### Biggest blockers (before the next tier of exposure)
1. Multi-tenant hard isolation still depends on operators enabling `execution_sandbox=required`; the sibling-container Docker socket mount in sandbox workers concentrates trust in the agent container.
2. Docker-workers autoscaling (newest code) lacks soak evidence: no long-running chaos/restart test exercised it in this pass.
3. Run-level failures were invisible in the API/UI (fixed this pass, see Bugs Fixed).
4. Frontend bundle size (>500 kB main chunk) and missing code-splitting.

### Final recommendation
Ship single-tenant self-hosted now; run a 2–4 week multi-tenant pilot behind `sandbox_policy_strict=true` with real workloads before opening multi-tenant broadly. Details in Final Recommendation section.

---

## Architecture Map

Verified from the current tree (not copied from old docs).

### Monorepo layout
```
apps/
  api/                     FastAPI backend
    app/main.py            App factory, lifespan loops, CSRF + body-size middleware
    app/worker_main.py     Standalone execution-plane process (no HTTP surface)
    app/config.py          Pydantic Settings: runtime_mode, dispatch_role, sandbox, KMS,
                           queue tuning, fail-closed validators (649 lines)
    app/security.py        Auth (bearer + httpOnly cookie + PAT ndpat_ + OAuth introspection),
                           RBAC roles/permissions, custom roles, org resolution
    app/tenancy.py         Org ContextVar + ORM auto-scoping + Postgres RLS GUC
    app/models.py          SQLAlchemy models (Run, NodeRun, RunEvent, RunQueueEntry,
                           RunnerPool, Runner, Environment, Credential, MCPConnection,
                           Artifact, CodeModule, WorkflowRevision, SSOConfig…)
    app/routers/           33 routers: workflows, runs, webhooks, mcp, mcp_connections,
                           credentials, environments, deployments, artifacts, audit,
                           agentic_build, chat, runner_pools, internal, ops, health…
    app/services/          ~70 services: queue.py (durable SKIP LOCKED queue),
                           runner.py (execution orchestration, redaction boundary),
                           run_persistence.py (bulk NodeRun/RunEvent inserts),
                           remote_dispatch.py, docker_workers.py (autoscaler),
                           container_runtime.py (hardening floor), sandbox_pool.py,
                           executors/{local,remote,sandbox}.py, providers/{agent,docker,k8s}.py,
                           backends/{venv,conda,pixi}.py, kms/{env,vault,aws,gcp}.py,
                           redaction.py, crypto.py, triggers.py (webhook auth + scheduler),
                           mcp_client.py, ai_builder.py, agentic_builder.py, licensing.py…
    app/mcp/               MCP server: protocol.py, tools.py (4,100 lines), resources.py,
                           prompts.py, guidance.py
    alembic/versions/      86 migrations (0001 → 0086), apply cleanly on SQLite & Postgres
    tests/                 130 test files
  web/                     React + Vite + zustand + react-query + @xyflow/react
    src/EditorPage.tsx, editor/ (Canvas, Inspector, DataPanel, AgentTrace, ChatPanel,
    OnboardingTour, diff views…), ExecutionsPage, CredentialsPage, EnvironmentsPage,
    RunnerPoolsPage (Docker-worker card), ArtifactsPage, OrganizationPage…
    e2e/                   Playwright suite with its own API webserver harness
packages/
  core/nodyra/             Engine: engine/{scheduler,node_exec,loops,metanodes,
                           subworkflows,validation,datasets}.py, expr.py (AST-guarded
                           code validation), serialization, artifacts, process_isolation
  nodes/nodyra_nodes/      ~50 node modules: builtin (HTTP/code/condition/transform),
                           http_security.py + httpx_security.py (SSRF), llm.py, ai_v2/,
                           integrations(_v2), postgres, files, pdf, images, charts, ml,
                           rag_lifecycle, mcp_tool.py, docker_nodes, security_automation…
  runner/nodyra_runner_agent/  Remote runner agent + sandbox_exec.py (per-env hardened
                           container execution, newest code)
  runtime/                 In-container/subprocess run harness (nodyra_runtime)
  client/                  Python SDK; exporter/, importer/
deploy/
  docker-compose.yml       postgres/redis/minio/api/worker/web with healthchecks
  docker-compose.sandbox.yml  Sandbox overlay
  Dockerfile.python        Multi-stage, non-root (gosu nodyra), HEALTHCHECK
  helm/nodyra/             Chart: api/worker/web deployments, migration-job, ingress,
                           runtime-secret
  observability/           Prometheus/OTel assets
.github/workflows/ci.yml   Lint, py3.14 compat, Postgres migration-drift, full API suite
                           on Postgres, pip-audit (blocking), bandit, mypy (security
                           modules blocking), compose config, helm lint, Playwright
scripts/                   e2e_test.py, mcp_smoke.py, soak_test.py, seed demos
```

### Execution topology (verified in config + main.py)
`runtime_mode` local|production; `dispatch_role` inline|worker|control|disabled; `webhook_role` inline|ingress|disabled; `scheduler_role` inline|leader|disabled. Split topologies hard-fail without Redis + Postgres (`dispatch_topology_errors()`). Runs flow: REST/webhook/schedule/MCP → durable `RunQueueEntry` → dispatch loop lease (SKIP LOCKED, org-fair, label-filtered) → local subprocess pool / sandbox container / remote runner over WS → events stream (Redis or in-process broker) → bulk persistence → retention loop.

---

## Testing Performed

### Existing tests run
| Suite | Scope | Result |
|---|---|---|
| `pytest` (full workspace: apps + packages) | 3,186 collected | **3,090 passed, 95 skipped, 1 failed** (12m08s) |
| Failure analysis | `test_lease_filters_by_worker_labels` | Timing flake, not a product bug — fixed (see Bugs Fixed #2) |
| `vitest run` (apps/web) | 63 test files | **All passed, 0 failures** |
| `tsc` (typecheck) | apps/web | **Clean** |
| `vite build` | apps/web | **Succeeds** (warning: chunk >500 kB) |
| `ruff check .` | whole repo | **All checks passed** |
| Playwright E2E (`e2e/playwright.config.ts`) | smoke, credentials, publishing-deployments, runner-pools | **7 passed (1.7m)** — boots its own API on :8123 |
| Alembic | fresh SQLite DB | **86 migrations apply cleanly** |

Note: an earlier full-suite run reached 99% with zero failures before I interrupted it (killed a process it shared); the clean re-run above is authoritative. `mypy` full-repo is not configured as a repo-wide gate; CI runs it on security-critical modules (blocking), which passed in the repo's own CI configuration.

### Manual / live E2E flows tested (against live server on :8077)
- App start: uvicorn boot, startup loops, `/health/live` 200. DB connect + migrations. ✅
- Workflow CRUD: create, save graph, list, run. ✅
- Run execution: manual_trigger → code node → `status=success`, timeline shows enqueued/started/node_started/node_finished per node. ✅
- Failing node: `raise ValueError("boom")` → run `error`, node error `ValueError: boom` captured and persisted. ✅
- Cycle graph: save accepted (draft-friendly), run fails closed with `GraphError` — but the reason was invisible on RunInfo (Bug #1, fixed). ✅/🐛
- No-trigger workflow: run rejected 400 "Workflow needs a trigger to run." ✅
- Invalid auth token → 401 (correct fail-closed even with auth optional). ✅
- MCP server live: `initialize` → `create_workflow` → `set_workflow_graph` (approval gate correctly demanded `approved_by_user=true` first) → `validate_graph` → `run_workflow` → success with output. ✅
- SSRF live test: `http://127.0.0.1:8077`, `http://169.254.169.254`, `http://10.0.0.5`, `http://[::1]` all blocked by `assert_public_http_url`; public URL passes. ✅

### Code-level audits performed
Engine scheduler + node_exec; queue leasing/heartbeat/fail/dead-letter; webhook auth (basic/header/query/bearer/JWT-HS256/HMAC — all constant-time); MCP server dispatch + RBAC + origin check + rate limit + cursor handling; MCP client (header blocklist, pinned SSRF resolution, response-size caps); redaction pipeline; sandbox_exec (newest code); docker_workers spawn; container hardening floor; run persistence; deployment files; CI.

---

## Commands Run

| Command | Result | Notes |
|---|---|---|
| `git checkout -b fable5-nodyra-full-test-bugfix-production-plan` | ✅ | Branched from end-to-end-hardening branch state |
| `python -m pytest -q --timeout=180` (full) | 1 failed / 3,090 passed / 95 skipped | Failure = timing flake, fixed + verified |
| `python -m pytest apps/api/tests/test_run_queue.py` (isolation) | 33 passed | Proves flake, not regression |
| `python -m pytest …::test_run_level_error_is_surfaced_on_run_info …::test_run_records_node_errors` | 2 passed | New regression test + neighbor |
| `python -m ruff check .` | All checks passed | Also re-run on changed files |
| `python -m alembic upgrade head` (fresh DB) | ✅ 86 revisions | SQLite; CI covers Postgres drift |
| `uvicorn app.main:app --port 8077` | ✅ boots, health 200 | JSON structured logs confirmed |
| `python scripts/mcp_smoke.py` | Partial | Script is stale: doesn't pass `approved_by_user` (see Bugs Found #4) |
| Custom MCP drive (initialize→create→set_graph→validate→run) | ✅ success | Approval gate honored |
| Custom REST drive (CRUD, run, fail, cycle, timeline) | ✅ | Found Bug #1 |
| `npm run typecheck` / `npx tsc` | ✅ clean | Before and after my changes |
| `npm run build` | ✅ | Chunk-size warning >500 kB |
| `npx vitest run` | 63 files pass | |
| `npx playwright test --config e2e/playwright.config.ts` | 7 passed (1.7m) | Boots own API on :8123 |
| SSRF live probe (4 private targets + 1 public) | All private blocked | Verified `http_security` at runtime |

Not run: `docker compose build/up` (no Docker daemon available in either environment I control — sandbox has no daemon; starting Docker Desktop on your machine was out of scope). Compose/Helm were statically audited and are validated by the repo's CI (`compose config`, `helm lint`).

---

## Bugs Found

### Bug #1 — Run-level failures invisible on RunInfo (P2, UX/observability) — FIXED
- **Area:** API + frontend. **Files:** `apps/api/app/schemas.py`, `apps/api/app/routers/runs.py`, `apps/web/src/types.ts`, `apps/web/src/ExecutionsPage.tsx`
- **Evidence:** Live repro — cyclic-graph run finished `status=error` with `node_runs: []` and **no `error` field at all** in `GET /runs/{id}`. The failure reason (`GraphError: Workflow graph has a cycle`) existed only in the separate `/runs/{id}/timeline` endpoint's `run_error` event. The `Run` model has no error column; `RunInfo` had no error field; the ExecutionsPage header showed a red "error" pill with no explanation.
- **Root cause:** Failures not attributable to a node (graph validation, pre-node run_error) were persisted only as `RunEvent(event_type="run_error")`; no surface mirrored them onto the run object the UI fetches.
- **Fix:** `RunInfo.error` populated by `_run_level_error()` — queried only for `status=error` runs whose node_runs carry no error (happy path untouched: zero extra queries). Frontend type + render under status pill with `role="alert"`.
- **Status:** Fixed, regression-tested, verified.

### Bug #2 — Flaky queue test: lease races the wall clock (P2, test reliability) — FIXED
- **Area:** Tests. **File:** `apps/api/tests/test_run_queue.py::test_lease_filters_by_worker_labels`
- **Evidence:** Failed in the full-suite run (`assert None is not None`), passed 33/33 in isolation.
- **Root cause:** Test seeds the unlabeled entry at `now + 1ms`. `queue.lease()` computes its own `now`; when the commit completes in under a millisecond, the unlabeled entry isn't yet `available_at <= now`, the labeled entry is filtered by `labels_satisfied`, and lease correctly returns None. Product behavior is right; the fixture races the clock.
- **Fix:** Seed both entries 5 s in the past, preserving the 1 ms FIFO stagger.
- **Status:** Fixed, verified.

### Bug #3 — Windows console crash in `scripts/e2e_test.py` (P3, tooling) — DOCUMENTED
- **Evidence:** Emoji output (`❌`) crashes with `UnicodeEncodeError` under cp1252 when the script needs to report an issue — the reporter itself dies. Recommended fix: `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` at script top. Left unfixed as P3 per fix-order rules (documented; trivial).

### Bug #4 — `scripts/mcp_smoke.py` stale vs. approval gate (P3, tooling) — DOCUMENTED
- **Evidence:** `set_workflow_graph` now (correctly) requires `approved_by_user=true`; the smoke script doesn't send it and exits 1. The gate is right; the script drifted. One-line update needed.

### Non-bug findings worth recording
- **Cyclic graphs are saveable** (draft-friendly by design) and fail closed at run time; MCP `validate_graph` and deployment publish validate earlier. Reasonable tiering — with Bug #1 fixed the failure is now explainable.
- **`_ensure_global_environment`** logs a warning on multi-replica duplicate global envs but only auto-heals absence, not duplication (P3, operator-visible).
- **Docker socket in sandbox workers** (`docker_workers.py` mounts `/var/run/docker.sock` when `sandbox=true`): tenant code runs in the disposable hardened container, not the agent, but agent compromise = daemon compromise. Deliberate sibling-container design; flagged as a hardening item (dedicated DinD/rootless daemon or socket proxy), not a vulnerability.
- **Frontend main chunk >500 kB** after minification (vite warning) — code-splitting opportunity (P2 perf).

---

## Bugs Fixed

| # | Issue | Files changed | Tests added | Verification |
|---|---|---|---|---|
| 1 | Run-level failure reason invisible | `schemas.py`, `routers/runs.py`, `types.ts`, `ExecutionsPage.tsx`, `index.css` | `test_run_level_error_is_surfaced_on_run_info` (cycle → `RunInfo.error` populated, mentions "cycle") | New test + neighbor test pass; `tsc` clean; `vitest` ExecutionsPage passes; `ruff` clean |
| 2 | Flaky lease-labels test | `tests/test_run_queue.py` | (is a test fix) — deterministic past-seeding with explanatory comment | Target test + full file (33) pass |

Commit: `7216dcf0` on `fable5-nodyra-full-test-bugfix-production-plan`.

## Tests Added
- `apps/api/tests/test_runs.py::test_run_level_error_is_surfaced_on_run_info` — locks in: failed run with no node-attributable error must carry a populated `RunInfo.error` naming the cause.

---

## Backend and Architecture Review

**Verified strengths:** layered auth (session/cookie+CSRF/PAT/OAuth-introspection with cache + circuit breaker); org scoping enforced twice (ORM `do_orm_execute` filter + Postgres RLS GUC) with careful ContextVar-before-membership-query ordering; `/mcp`-only exposure of PAT/OAuth grants so provider webhooks never leak third-party bearers into introspection; global body-size middleware; structured JSON logs with request/org/user/trace correlation; interrupted-run cleanup on startup including orphaned queue entries; bulk-insert persistence (NodeRun/RunEvent); retention loops for runs and audit logs.

**Gaps → hardening plan:**
1. `Run` model itself has no error column — my fix reads the event at request time; a denormalized `runs.error` column (write-once at failure) would remove the extra query and make retention-pruned timelines safe. (P2, small migration)
2. In-process fallbacks (event broker, rate limit, introspection cache, secret cache) are per-replica; documented, but a replica-count >1 checklist in `/ops/runtime-mode` output would catch drift. (P2)
3. `_ensure_global_environment` duplicate-heal. (P3)

## Workflow Engine Review
Verified: deterministic `_topo_order` (insertion-order tie-break; position never read), cycle → `GraphError`, dependency-counting worker-pool scheduler with cancellation propagation (`BaseException` → cancel workers), worst-status aggregation (error > waiting > success), run deadline skips not-yet-started nodes, per-node output cap (10 MiB default engine-side; 256 KiB DB-side with offload), loop regions as single scheduling units (no deadlock under caps because drivers don't hold slots), subworkflow cycle/depth guards (`max_subworkflow_depth=16`), checkpoint serialization skips unserializable outputs rather than failing the run.
Tested live: empty/no-trigger, single, linear, failing, cyclic. Suite covers branching, merge, loops, metanodes, subworkflows, parallelism, checkpoints, cancellation, large outputs (existing tests, all green).
**Improvement:** engine currently marks past-deadline nodes as error individually; a single run-level `timed_out` status would read better in history (P3).

## Node and Data Flow Review
~50 node modules with a shared credential map (`_creds.py`), SSRF-guarded egress helpers used by builtin HTTP, integrations, LLM, postgres, files, browser automation. Typed port kinds validated at connection (`_validate_connection_kinds`); outputs capped then offloaded (order verified correct — cap-first so offload can't smuggle unbounded payloads); artifacts carry checksums; DatasetRef/DataFrame path has dedicated store + SQL query surface with limits. Redaction applied at the event boundary in `runner.py` against decrypted org credential values (org-keyed cache, 60 s TTL).
**Gaps:** node metadata quality (descriptions/examples for palette + AI builder) is uneven across the long tail of integration nodes (P2, content work); no per-node "test with sample input" API (P2, feature).

## MCP Review
Server: stateless streamable-HTTP JSON-RPC; protocol-version negotiation; origin allowlist; per-org+actor rate limit (120/min); cursor-paginated tools/resources; static tools with per-tool RBAC permission mapping; dynamic workflow-as-tool calls gated on `workflow:run`; tool errors returned as `isError` results with reference IDs (no stack leakage); **write-approval gate on `set_workflow_graph` verified live**; RFC 9728 protected-resource metadata for OAuth discovery.
Client (external MCP servers): dangerous-header blocklist (host/authorization/x-forwarded-*/proxy-*…), `resolve_pinned` SSRF pinning, response-size caps, per-connection allowed-tools policy that **fails closed on malformed (string) policies**, org-KEK Fernet secret storage.
**Gaps:** tool-result prompt-injection is mitigated only by approval gates and allowed-tools — no content-level tagging of untrusted tool output for AI-agent consumption (P2); MCP audit trail of tool calls exists in run events but a dedicated per-connection call log would help enterprise review (P2); smoke script drift (P3, Bug #4).

## Runners, Workers, Queue, and Sandbox Review
Queue verified in code + green tests: SKIP LOCKED leasing (Postgres) with bounded candidate window to prevent label head-of-line blocking; org-fair pre-pass with per-org caps; priority + FIFO; exponential backoff capped; dead-letter with attempts log and ops replay endpoints; heartbeat lease extension; lease-expiry reclaim; drain mode; stuck-run detector; RSS soft-budget admission so heavy envs can't OOM the host; pool autoscaler with cooldown.
Runner agent: WS heartbeats (offline after 60 s → requeue), registration tokens (TTL, revocable), ghost cleanup, docker-managed workers with restart policy + no-new-privileges, per-env sandbox images built from the API wheel index with PEP 508 allowlist regex (blocks shell metacharacters into the Dockerfile), fail-closed dedicated bridge network, container force-remove in `finally`.
**Gaps:** Docker-workers autoscaler + agent sandbox path are the newest code (last commits on the parent branch) and passed unit/integration tests, but have no soak/chaos evidence — worker-killed-mid-run and lease-expiry reclaim under real Docker were not exercised in this pass (highest-value next test, P1 to perform before relying on the feature); attach-socket streaming has a 3600 s socket timeout as the only backstop for a wedged container runtime (P2: add heartbeat within protocol).

## Deployment and Environment Review
Compose: postgres/redis/minio with healthchecks, api+worker split, migration path, artifact/env volumes, web via nginx. Dockerfile: multi-stage, non-root runtime user, HEALTHCHECK, no secrets baked (env-driven; `.env.example` documents 58 vars including all the sharp-edged ones). Helm: api/worker/web deployments, migration job, runtime secret, ingress, worker health port wired to `worker_health_port`. CI validates compose config + helm lint + full API suite on Postgres + migration drift + pip-audit (blocking).
**Gaps:** no image signing/SBOM publication (P2); no documented zero-downtime upgrade sequence beyond migration-job ordering (P2); `docker compose up` not executed in this pass (environment constraint — flagged honestly).

## Security Review
Verified this pass (live or by direct code read): SSRF quadruple-layer (live-tested); startup fail-closed guards; webhook auth all constant-time comparisons incl. HS256 JWT with alg pinning + exp; CSRF double-submit for cookie auth; WS tickets to keep tokens out of access logs; PAT hashing (SHA-256) with expiry/revocation; credential envelope encryption with org KEKs + external KMS options; secret redaction at the persistence/stream boundary with org-keyed cache; MCP header blocklist; XSS: React default escaping + DOMPurify present for rich content; SQLi: SQLAlchemy bound parameters throughout (no string SQL found in routers/services greps); audit log with retention; rate limits on auth, webhooks, MCP, per-workflow runs.
**P0 findings: none.** **P1 findings: none new** — the residual items are known, documented tradeoffs: (a) multi-tenant without `execution_sandbox=required` is shared-kernel (guarded by `sandbox_policy_strict` default true); (b) docker.sock in sandbox-capable workers (sibling-container trust concentration — recommend socket proxy or dedicated rootless daemon, P1 hardening before untrusted-tenant GA); (c) in-process auth rate limiter is per-replica (front with WAF or move to Redis, P2).

## Performance and Optimization Review
Backend: bulk inserts for NodeRun/RunEvent (T-08/P1-8 verified in code); approval preload kills N+1 (P1-9); paginated list endpoints; indexed run queries (`ix_runs_org_status_started`); pool tuning exposed; introspection + secret caches bounded/TTL'd. Engine: dependency-counting scheduler avoids per-batch event-loop rounds; per-type semaphores; loop-owned outputs populated by region driver. My added `_run_level_error` query runs only on the failed-with-no-node-error path.
**Opportunities:** P1 — none blocking. P2 — frontend code-splitting (>500 kB chunk); `runs.error` denormalization; MCP `tools/list` builds full descriptor list before slicing the page (fine at current scale, O(all tools) per call); node-palette manifest could be served with ETag caching. P3 — vite manualChunks tuning, WS event coalescing for very chatty loops.

## UI/UX Review
Working (verified via component inventory, passing tests, Playwright): editor canvas with loops/metanodes/diff views, inspector, data panel with dataset SQL, agent trace, chat panel, command palette, onboarding tour (tested), run sidecar/timeline, executions page with re-run/retry-from-failure/debug-in-editor, credentials with presets + OAuth, environments with package drawer, runner pools incl. one-click Docker worker card, artifacts browser, org/enterprise pages, a11y-tested modals, error boundaries per page.
**Fixed this pass:** run-level failure reason now visible in run detail.
**Gaps → UI improvement plan (priority order):**
1. **Production readiness checklist** surface (auth on? secret set? Redis? sandbox? backups?) — the backend already computes `runtime_warnings()`; give it a first-class settings panel with fix-it links. (P1 for trust)
2. **Template gallery + "Create with AI" as the primary empty state** — engineering depth outstrips discoverability. (P1 adoption)
3. Data preview on edges (peek last output on a wire). (P2)
4. Per-node "Test this node" button using pinned upstream data. (P2)
5. Node palette: consistent one-line descriptions + credential badges for the long tail. (P2)
6. Visual graph diff already exists — expose it in run history ("what changed since the last green run?"). (P2)
7. Bundle code-splitting for first-load performance. (P2)
8. Worker/queue dashboard: live lease/dead-letter view exists in ops endpoints; give it charts. (P3)

---

## Feature Ideas Roadmap

**AI-native:** AI fix-failed-run (feed `RunInfo.error` + node errors + graph into the builder; high value, medium complexity, do next); AI explain-workflow (low complexity, high onboarding value, do next); AI workflow repair on validation failure (medium); AI-generated tests per workflow (medium, differentiating); AI custom-node generator with `generate_node` already present — polish + publish flow (medium); AI convert-Python-script-to-workflow (high differentiation, medium-high complexity, later).

**Python-native:** typed Pydantic node I/O editor (medium, high dev-trust); per-workflow requirements already exist — surface pip-audit results in UI (small); Jupyter import/export (medium, strong acquisition channel); CLI (`nodyra run/deploy/logs`) building on packages/client (small-medium, do next); FastAPI endpoint generation from a workflow (medium, later).

**MCP:** external-MCP-tool marketplace/discovery UI (medium, strategic); per-tool approval gates for side-effecting external tools (small — policy model exists, extend to per-tool); MCP tool call trace view (small); share-workflow-as-MCP-tool is implemented — add scoped publish tokens + docs (small, do next since Claude/Cursor adoption is the wedge).

**Worker/runner:** worker drain mode exists — add UI button (small); worker logs in UI (medium); GPU/high-mem labels exist — add capability autodetection in agent (small); one-click Docker worker exists — add join-command generator for bare-metal (small, do next).

**Workflow UX:** run comparison (diff two runs' outputs, medium); replay-from-node exists (retry-from-failure) — extend to arbitrary node (small); workflow health score (lint: no retries configured, no error workflow, unpinned credentials; small-medium, differentiating).

**Enterprise:** SSO config model exists — finish OIDC end-to-end + SCIM (high priority for enterprise, medium-high complexity); audit log exists — export/SIEM webhook (small); approval workflows for deployments (medium); GitOps sync exists (github_sync) — add PR-based review flow (medium); backup/restore runbook + `nodyra export --all` (small, do before enterprise).

**Do not over-engineer yet:** multi-region, plugin sandboxing for community nodes beyond the registry flag, K8s operator, per-node billing granularity.

---

## Production-Grade 10/10 Improvement Plan

| Area | Now | Target 10/10 | Key changes | Priority | Complexity |
|---|---|---|---|---|---|
| UI/UX | 7.5 | Guided first-run, template gallery, readiness checklist, edge previews, per-node test | Items 1–5 above | P1–P2 | M |
| Frontend arch | 8 | Code-split routes, editor lazy-loaded, <250 kB initial | vite manualChunks + dynamic import | P2 | S–M |
| Backend arch | 9 | `runs.error` column; replica-aware ops checklist | Small migration + ops surface | P2 | S |
| Engine | 9 | Run-level `timed_out`; streaming node outputs | Status enum + chunked event path | P2–P3 | M |
| Node system | 8.5 | Metadata completeness; per-node test API | Content pass + one endpoint | P2 | M (volume) |
| Data flow | 8.5 | DatasetRef streaming to UI grid; artifact GC metrics | Incremental | P2 | M |
| Artifacts | 8.5 | Retention policies per workflow; browser previews for more types | Policy column + preview workers | P2 | M |
| MCP | 8.5 | Untrusted-output tagging; per-connection call log; marketplace | Injection defense first | P1(inject)/P2 | M |
| AI builder | 8 | Fix-failed-run + explain; eval harness for generation quality | Reuse existing draft/approval flow | P1 | M |
| Python exec | 8.5 | Warm sandbox pools default-on in MT; import allowlist telemetry | Config + metrics | P1 (MT) | M |
| Sandbox | 8 | Socket-proxy or rootless DinD for sandbox workers; gVisor/Kata probe already present — document tiers | Trust-boundary work | P1 (before untrusted MT) | M–L |
| Runners/queue | 8→9 | Soak/chaos suite: kill worker mid-run, expire leases, 50-run bursts under real Docker | New test lane | P1 | M |
| Deploy/env | 8.5 | SBOM + image signing; upgrade runbook; compose smoke in CI with real daemon | Supply-chain pass | P2 | S–M |
| Security | 8.5 | Redis-backed rate limits; secrets-in-logs scanner in CI; pen-test | Incremental | P1–P2 | S–M |
| Performance | 8 | p95 dashboards per route; load test suite in CI (soak_test.py exists — schedule it) | Observability-driven | P2 | S–M |
| Observability | 8 | OTel default-on sample config; run-level trace links in UI | Wiring exists, expose it | P2 | S |
| Testing/CI | 9 | Add chaos lane + compose-up smoke + flake quarantine | Builds on strong base | P1–P2 | S–M |
| Docs | 7 | Task-oriented operator guide (the knowledge is in code comments — extract it) | Doc sprint | P2 | M |
| Onboarding | 7 | First-run wizard: create admin → connect credential → run template | Ties UI items together | P1 | M |
| Differentiation | 8 | Lean into: Python-native + MCP-native + AI-inspectable workflows | Marketing + the P1 features above | — | — |

**Suggested order:** (1) runner/queue chaos-soak lane, (2) MT sandbox trust-boundary hardening (socket proxy), (3) MCP injection tagging, (4) onboarding + readiness checklist + template gallery, (5) AI fix/explain, (6) code-splitting + `runs.error` column + docs.

---

## Remaining Risks (honest)
1. **Docker-workers/agent-sandbox soak coverage** — newest code; green tests but no chaos evidence. Don't market autoscaling as GA until the chaos lane passes.
2. **`docker compose up` not executed in this pass** (no daemon available to me); CI validates config and the images build in CI, but a full compose boot on a clean host should be someone's checklist item this week.
3. **Multi-tenant GA** depends on sandbox=required posture plus the socket-proxy hardening; shared-kernel MT is explicitly guarded but operators can override.
4. **Single flake fixed, but the suite is large** — add flake quarantine/retry reporting so one flake never blocks a release.
5. **Frontend initial load** on slow connections (one big chunk).
6. The uncommitted line-ending churn (~500 files, CRLF normalization from `.gitattributes` work) sits in the working tree from a prior session — commit or reset it deliberately; it makes diffs noisy and hides real changes.

## Final Recommendation
- **Internal use: yes, today.**
- **Single-tenant self-hosted beta: yes** — the fail-closed config guards actively resist operator foot-guns; ship with the readiness-checklist UI soon after.
- **Multi-tenant: pilot only** — `multi_tenancy_enabled` + `sandbox_policy_strict` + `execution_sandbox=required`, trusted tenants, 2–4 weeks of burn-in, socket-proxy hardening before untrusted tenants.
- **Enterprise: not yet** — finish OIDC/SCIM end-to-end, backup/restore runbook, SIEM export, and a third-party pen-test first. The licensing/RBAC/audit skeleton is already in place.
- **Fix next:** chaos-soak lane for runners/queue; compose-up smoke on a real daemon; `runs.error` column; MCP output tagging.
- **Build next:** onboarding wizard + template gallery + AI fix-failed-run — the engineering is ahead of the product surface, and these convert that surplus into adoption.
- **Don't over-engineer yet:** K8s operator, multi-region, community-node sandboxing beyond the registry flag, per-node billing.
