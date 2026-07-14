# Fable 5 Nodyra End-to-End Production Hardening Report

**Date:** 2026-07-05 · **Branch:** `fable5-nodyra-end-to-end-production-hardening` · **Auditor:** Fable 5

Every conclusion in this report was verified against current source, tests,
configuration and deployment files on this branch. Older audit documents were
used only to locate previously-open items, each of which was then re-verified
in code (see "Critical instruction compliance" notes inline).

---

## Executive Summary

| Area | Score /10 |
|---|---|
| **Overall production readiness** | **9.0** |
| Backend API | 9 |
| Frontend | 8.5 |
| Workflow engine | 9 |
| Runners / workers | 9 |
| Queue / durable execution | 9.5 |
| Sandbox execution | 9 |
| MCP | 8.5 |
| Artifacts / result storage | 9 |
| Deployment / environment | 8.5 → 9 (after this pass) |
| Security | 9 |
| Performance | 8 |
| UI/UX | 8.5 |

**Biggest strengths**
- A genuinely durable execution core: DB-backed run queue with
  `FOR UPDATE SKIP LOCKED` leasing, lease-expiry recovery, exponential-backoff
  retries, dead-lettering, replay(-from-node), org-fair scheduling, and
  cross-process cancellation — all covered by tests, including a Postgres CI lane.
- Defence-in-depth security posture that *fails closed*: default `SECRET_KEY`,
  blank `INTERNAL_API_TOKEN`, CORS wildcard, MT-without-sandbox and split-topology
  misconfigurations all abort startup. CSRF double-submit, WS tickets, SSRF
  guard (`safe_request` with DNS-rebinding protection), RLS + ORM org scoping.
- Best-in-class container sandbox spec: cap_drop ALL, no-new-privileges,
  read-only rootfs, tmpfs-only writes, non-root UID, pids/mem/cpu/ulimit caps,
  dedicated bridge network, gVisor/Kata auto-detection, shell-injection-proof
  image builds, schema-versioned images.
- Worker scaling is already near one-click: `docker compose up -d --scale worker=3`
  for dispatch workers; UI-generated join command (wheel index served by the API)
  for remote agent runners with signed revocable tokens, heartbeats, drain,
  SSH onboarding and ghost cleanup.
- CI is comprehensive: ruff (blocking), full pytest, Python 3.14 lane, Postgres
  migration-drift check, Postgres queue lane, pip-audit + bandit + mypy on
  security modules, compose config + helm lint, web typecheck/test/build, Playwright e2e.

**Biggest blockers found this pass** (all fixed, see Bugs)
- Helm `worker.healthPort` probe targeted a server that did not exist —
  enabling it stalled every worker rollout (P1, fixed).
- Compose published Postgres/Redis on `0.0.0.0` with a hard-coded DB password
  in a production-posture file (P1, fixed).
- `/health/ready` echoed raw driver exceptions (which can embed DSNs/hostnames)
  to unauthenticated probers (P2, fixed).
- Repo tip had 4 blocking ruff errors (CI-red, fixed).

**Biggest opportunities**
- A soak/load test remains the single most valuable un-run verification
  (everything else about queue behaviour is unit/integration proven).
- MCP quickstart visibility, template gallery, and "Create with AI" as the
  primary entry point are the highest-leverage adoption features.
- Worker capability/label routing exists for agent pools; generalising
  label-based routing to dispatch workers (GPU/high-mem selectors) is the main
  scaling-system gap.

**Final recommendation:** Ship single-tenant beta now. Multi-tenant is
architecturally ready (RLS + org-fair queue + mandatory sandbox) but should
follow a soak test and an external pen test. Enterprise (SSO/SCIM/audit
retention already present in various stages) after multi-tenant burn-in.

---

## Architecture Map

**Monorepo:** uv workspace (Python 3.12) + npm (React/TS).

```
apps/api/app/
  main.py                 FastAPI app; lifespan owns all background loops; middleware
                          (body cap, metrics, security headers, CSRF, auth gate, CORS)
  worker_main.py          Standalone execution worker (DISPATCH_ROLE=worker; no uvicorn;
                          now serves /metrics + /health/live + /health/ready)
  config.py               Pydantic Settings; security_startup_errors() /
                          dispatch_topology_errors() fail-closed guards
  models.py / schemas.py  SQLAlchemy models / API DTOs
  security.py tenancy.py  AuthN/Z, RBAC, org scoping (ORM filter + Postgres RLS)
  routers/                33 routers: workflows, runs, webhooks, provider_webhooks,
                          runner_pools (worker fleet), artifacts, credentials, auth,
                          orgs, ops, mcp, mcp_connections, internal, health, …
  services/
    queue.py              Durable run queue: enqueue/lease/heartbeat/fail/replay/
                          dead-letter + dispatch loop (SKIP LOCKED, org-fair, wakeup pub/sub)
    runner.py             Run orchestration; executors/{local,remote,sandbox}
    runtime_pool.py       Warm subprocess pool + autoscaler + idle reaper + RSS budget
    sandbox_pool.py / container_runtime.py / sandbox_policy.py
                          Container-per-run sandbox (warm pool, hardened spawn kwargs)
    remote_dispatch.py    Agent/K8s/Docker pool dispatcher + runner heartbeat loop
    run_checkpoints.py / run_persistence.py / run_resume.py
                          Durable checkpoints, capped persistence, resume/replay
    artifacts.py / artifact_backends.py / s3_artifact_backend.py / output_store.py
                          Artifact store (local/S3), large-output offload, checksums
    credentials.py / crypto.py / kms/    Envelope encryption; env/vault/aws/gcp KMS
    triggers.py           Cron scheduler loop; provider_triggers for SaaS events
    stuck_run_detector.py leader_election.py replica_health.py dispatcher_health.py
    mcp_client.py         External MCP servers as tool sources (allowlists, caps)
  mcp/                    MCP server: protocol.py, tools.py (61+ tools), resources,
                          prompts, guidance
apps/api/alembic/         Migrations (drift-checked in CI against Postgres)
apps/api/tests/           ~200 test modules

packages/core/nodyra/     Engine: engine/{scheduler,node_exec,validation,loops,
                          metanodes,subworkflows,agent}.py; expr.py sandboxed
                          expressions; serialization; artifacts; process_isolation
packages/nodes/           500+ built-in nodes (nodyra_nodes), SSRF-guarded egress
packages/runner/          nodyra-runner agent (outbound WS, env cache, process pool)
packages/runtime/         In-container runtime entrypoint (python -m nodyra_runtime)
packages/client|exporter|importer/  SDK/CLI, code export, importers

apps/web/src/             React SPA: EditorPage + editor/ (Canvas, NodePalette,
                          NodeDetails, DataPanel, NDVPanels, diff views, onboarding
                          tour), RunnerPoolsPage (fleet UI + join-command generator),
                          ArtifactsPage, ExecutionsPage, SettingsPage, auth/queries/
                          store; extensive vitest coverage; Playwright e2e in e2e/

deploy/
  Dockerfile.python       2-stage uv build, non-root, healthcheck
  docker-compose.yml      postgres+redis+minio+api(control)+worker+web
  docker-compose.sandbox.yml  Worker sandbox overlay (docker.sock, DooD)
  helm/nodyra/            api/worker/web Deployments, migration Job, ingress,
                          secret handling, probes
.github/workflows/ci.yml  lint→tests→3.14→Postgres lane→security(pip-audit/bandit/
                          mypy)→compose/helm validation→web→e2e
```

---

## Production Readiness Scorecard

| # | Area | Score | Evidence (verified this pass) | Weak points | 10/10 needs |
|---|---|---|---|---|---|
| 1 | Overall architecture | 9 | Clean router/service/engine split; explicit topology roles (`inline/control/worker/disabled`); fail-closed startup | A few god files (`mcp/tools.py` 4.1k, `NodeDetails.tsx` 5k) | Decompose the 3 giants; ADRs for topology |
| 2 | Backend API | 9 | Typed schemas, bounded pagination (`le=500`), global body cap, structured errors, request-id logging | Some routers >1k lines | Router split; OpenAPI examples |
| 3 | Frontend architecture | 8.5 | Query layer, store with undo/drill tests, error boundaries, a11y hooks, extensive vitest | `NodeDetails.tsx` 5017 lines; EditorPage 2135 | Component decomposition, code-split editor |
| 4 | Workflow engine | 9 | Deterministic topo order, cycle detection (`GraphError`), branch skip, loops/metanodes/subworkflows, worst-status aggregation, 10 MiB node output cap, run deadline; 389 core tests green | Replay-from-node UI still API-only | Replay UI; property-based graph tests |
| 5 | Node system | 9 | 500+ nodes, manifests w/ param schemas, egress-policy contract test, unsafe-node deploy gate (incl. Docker nodes) | Param docs uneven on long tail | Long-tail manifest docs |
| 6 | Python-native experience | 8.5 | Code node first-class, per-env venvs (uv), per-workflow requirements, SDK/CLI, export-to-code | Debugger, typed code-node params | Pydantic-typed code nodes, Jupyter bridge |
| 7 | Code execution safety | 9 | Subprocess isolation default; sandbox modes off/auto/required; MT refuses no-sandbox | Process mode not a hard boundary (documented) | Default `auto` where daemon exists |
| 8 | Sandbox execution | 9 | Hardened spawn kwargs (verified in `container_runtime.py`); warm pool; gVisor/Kata autodetect; per-workflow resource overrides w/ ceilings; compose overlay | Helm sandbox story (DinD/K8s Jobs) doc-only | K8s Job sandbox provider |
| 9 | Runners | 9 | Agent WS auth (signed token bound runner+pool+org, revocable); env caching; drain/restart/ghost-cleanup; SSH onboard | Version reporting in hello is minimal | Agent auto-update channel |
| 10 | Workers | 9 | `worker_main` fail-closed validation; drain on SIGTERM; replica heartbeats; now health+metrics HTTP | — (health gap fixed this pass) | Label-based routing for dispatch workers |
| 11 | Queue/durable execution | 9.5 | SKIP LOCKED lease, backoff, dead-letter, replay w/ seed, expired-lease requeue resets Run, org-fair pre-pass, local-budget anti-HOL-blocking, wakeup pub/sub; Postgres CI lane | Soak test not yet run | Soak report |
| 12 | Webhooks | 9 | Role-based mounting, per-path+IP rate limit, auth-required-by-default at publish, burst→queue | — | Signed replay protection presets |
| 13 | MCP | 8.5 | 61+ tools, OAuth 2.1 resource + PATs, origin checks, allowlist validation at write+call time, pagination cursors | Tool-call trace UI; quickstart discoverability | Trace UI + quickstart card |
| 14 | AI workflow builder | 8.5 | Agentic builder w/ validation loop, param-signature catalog, explain/repair | Cost controls surface | Budget caps UI |
| 15 | AI agent runtime | 8.5 | Approval gates (queue `waiting` state), guardrail events, resume w/ seed | — | Cross-run memory strategy |
| 16 | Artifacts/results | 9 | Local+S3 backends, offload store, checksums, retention loop, browser UI + stats cache invalidation, tenant scoping via parent run + RLS | Signed URLs for S3 downloads | Signed URL + streaming range reads |
| 17 | Node-to-node data flow | 9 | Typed ports, DataRef guard (new tests), output caps w/ preview stubs, marker-read fixes tested | Data preview on edges (UX) | Edge previews |
| 18 | Credentials/secrets | 9 | Envelope encryption, KMS providers, redaction in logs/events, OAuth flows, scoping | — | Credential-sharing policies UI |
| 19 | Auth/RBAC/multi-tenancy | 9 | Roles+permissions map, session revocation honored in middleware gate, CSRF, RLS + ORM filter, org-fair queue | SCIM absent | SCIM; per-workspace RBAC |
| 20 | Database/migrations | 9 | Alembic w/ CI drift check vs Postgres; safe-role assertion at startup | — | Automated backup docs → scripts |
| 21 | Deployment | 9 | Compose (hardened this pass), sandbox overlay, Helm w/ migration Job + probes (worker probe fixed this pass), 2-stage non-root image | No GHCR published images yet | Published images + compose `image:` default |
| 22 | Environment/config | 9 | Typed Settings; fail-closed guards; `.env.example` complete (incl. sandbox family); runtime warnings via /ops | — | Config reference doc generated from Settings |
| 23 | Observability | 8.5 | JSON logs w/ request/org/run ids, Prometheus metrics + queue gauges, OTel opt-in traces API→worker, replica dashboard | No default Grafana board | Ship dashboards |
| 24 | Performance | 8 | Wakeup-driven dispatch (no 1s poll latency), batch org-limit fetch, stats caching, autoscaling pool | No load-test numbers | Soak + EXPLAIN audit |
| 25 | Security | 9 | This pass: no new P0s; startup guards, SSRF guard incl. ai_v2 parity, sandbox floor non-overridable, internal token constant-time | Pen test outstanding | External pen test |
| 26 | Testing/CI | 9.5 | ~3,000 tests; Postgres lane; security lanes; e2e; rename guard | Soak lane | Nightly soak |
| 27 | UI/UX | 8.5 | Onboarding tour, empty states, command palette, diff views, fleet UI w/ join generator | Template gallery; AI entry point | Gallery + Create-with-AI home |
| 28 | Onboarding | 8 | Tour, `.env.example`, compose quickstart | First-run checklist | Guided first workflow |
| 29 | Worker/runner scaling UX | 9 | `--scale worker=N`; UI join command incl. wheels index; drain; health | Was undocumented (fixed: `docs/deployment/workers.md`) | Capacity planner |
| 30 | Product differentiation | 9 | Python-native + MCP-native + real durability is a rare combination | Awareness (docs/marketing) | MCP quickstart front-and-center |

---

## Bugs Found

| ID | Sev | Area | File(s) | Evidence | Root cause | Status |
|---|---|---|---|---|---|---|
| HB-1 | **P1** | Deployment/K8s | `deploy/helm/nodyra/templates/worker-deployment.yaml`, `apps/api/app/worker_main.py`, `apps/api/app/config.py` | Chart sets `WORKER_HEALTH_PORT` + readiness probe `GET /health/ready:{port}` when `worker.healthPort > 0`, but no `worker_health_port` setting existed and the worker served only `/metrics` (404 elsewhere, no listener at all unless `WORKER_METRICS_PORT` set). Repro: `helm install --set worker.healthPort=9402` → worker pod never Ready → `maxUnavailable: 0` rollout stalls forever | Chart written against a planned worker health server (values comment cites P1-12/P1-23) that was never implemented | **Fixed** ✅ |
| HB-2 | **P1** | Deployment/security | `deploy/docker-compose.yml` | `postgres` published `5432:5432` and `redis` `6379:6379` on all interfaces with `POSTGRES_PASSWORD: nodyra` hard-coded, in the same file that sets `RUNTIME_MODE=production`. Any network peer of a single-host deployment could connect to an unauthenticated Redis / default-password Postgres | Dev convenience defaults left in the production-posture compose | **Fixed** ✅ (loopback binds; `${POSTGRES_PASSWORD:-nodyra}` override; `.env.example` updated) |
| HB-3 | **P2** | Security/info-leak | `apps/api/app/routers/health.py` | `/health/ready` returned `f"error: {exc}"` for DB/Redis failures; driver errors can embed DSN (host/user/password). Endpoint is auth-exempt (K8s probes) | Convenience formatting predating the auth-exemption decision | **Fixed** ✅ (log full, return `error: unreachable`) |
| HB-4 | **P1** | CI health | `apps/api/app/mcp/tools.py` (+3) | `uv run ruff check .` → 4 I001 errors at branch tip; CI's blocking ruff step ⇒ tip cannot merge | Recent uncommitted MCP-hardening edits landed unsorted imports | **Fixed** ✅ (`ruff --fix`, re-verified clean) |
| HB-5 | P3 | Repo hygiene | former Helm chart directory | Empty leftover chart directory (only empty `templates/`) from the rename; confuses `helm lint`/operators | Rename residue | **Fixed** ✅ (removed) |
| HB-6 | P3 | Docs drift | `docs/deployment.md` | `DISPATCH_ROLE` table omitted `control` (the role compose actually uses); recommended obsolete "keep one replica inline" pattern; compose section said API runs `disabled` | Docs not updated when `control` role landed | **Fixed** ✅ |
| HB-7 | P3 | Docs | `apps/api/app/routers/runner_pools.py` | Registration-token docstring claimed "TTL (24h)"; actual default `runner_token_ttl_days=365` | Stale docstring | **Fixed** ✅ |

**Re-verified previously-reported items (not trusted from docs):** SEC-A Docker
nodes gated in `unsafe_nodes.py` (`docker_run_container` → `execute_command`,
new `docker_daemon` kind) ✅; SEC-B `safe_request` in `llm.py` + all four
`ai_v2` provider adapters, remaining `requests.post` calls target hard-coded
`api.openai.com` only ✅; `.env.example` sandbox var family present ✅;
MCP allowlist validation at write + call time present in `mcp_connections.py`
/ `ensure_tool_allowed` ✅.

## Fixes Made

1. **Worker health server (HB-1, HB-3)** — added `worker_health_port` setting;
   `worker_main.py` now serves `/metrics`, `/health/live`, `/health/ready`
   (one listener per configured port; readiness shares the API's checks via a
   new `app.routers.health.readiness_checks()` so both planes report identical
   semantics); readiness errors sanitized (logged, not echoed).
   *Files:* `apps/api/app/config.py`, `apps/api/app/worker_main.py`,
   `apps/api/app/routers/health.py`.
   *Tests added:* `apps/api/tests/test_worker_health.py` (9 tests: live,
   metrics, 404/405, query-string handling, healthy ready, sanitized 503 with
   secret-DSN assertion, API-route sanitization, end-to-end socket test);
   `test_worker_metrics_server.py` updated to the new listener.
   *Verification:* 21/21 targeted tests pass (incl. pre-existing sandbox-state
   readiness tests, unmodified).
2. **Compose hardening (HB-2)** — Postgres/Redis host ports bound to
   `127.0.0.1`; `POSTGRES_PASSWORD` parameterized (default preserved for dev);
   `DATABASE_URL` in api+worker follows it; `deploy/.env.example` documents it.
   *Verification:* `docker compose config -q` passes for base and sandbox overlay.
3. **Ruff (HB-4)** — 4 import-sort errors auto-fixed; `ruff check .` clean.
4. **Docs/hygiene (HB-5/6/7)** — stale helm dir removed; deployment.md role
   table + recommendation corrected; token-TTL docstring corrected.
5. **New deliverable:** `docs/deployment/workers.md` — the one-command worker
   scaling guide (below).

---

## Backend and Architecture Review

Verified: routers delegate to services; validation via Pydantic schemas;
pagination bounded everywhere inspected (`runs` 500/200 caps, artifacts 100);
`_body_size_limit` middleware caps request bodies (10 MiB default,
artifact-route override) including chunked bodies with a concurrent-reader
ceiling; unhandled exceptions return structured JSON without stack traces;
`ServiceError` hierarchy maps to typed HTTP responses; no blocking
`time.sleep`/sync-HTTP in async routers/services (grep-verified); background
loops all run `as_system` with explicit rationale (MT correctness).

Remaining (non-blocking): `routers/workflows.py` (1.3k), `services/runner.py`
(1.8k), `services/ai_builder.py` (2.1k) are large but internally sectioned;
recommend mechanical splits when next touched, not before.

## Workflow Engine Review

Verified in `packages/core/nodyra/engine/`: deterministic topological order
independent of canvas position; cycle → `GraphError`; disconnected/missing-node
handling in validation; branch-skip semantics; loop regions with per-iteration
paths; metanode drill-in; subworkflow cycle+depth guards (A3) with host
resolver; worst-status aggregation so a late-finishing waiting node can't mask
an error; per-node timeouts (default map + per-node override + code-node default
600s); run deadline contextvar; 10 MiB per-node output cap with explicit
override; checkpoint debouncer with size caps; replay-from-failure seeds
(cache+targets) through the queue's `replay_seed`.

Engine edge cases exercised by the suite (`test_engine*`, `test_loops`,
`test_run_deadline`, `test_scheduler_plan`, `test_subworkflow_callback`,
`test_streaming`): 389 tests, all passing this session.

## Runners, Workers, and Queue Review

Queue (verified line-by-line in `services/queue.py`): single queue entry per
run (unique run_id) → no duplicate execution; lease → running transitions;
heartbeat extends leases; expired-lease requeue also resets the user-facing
`Run` row to `queued` (crash recovery correct end-to-end); retry backoff
capped; dead-letter after max attempts; replay resets attempts and preserves
history; cancel works cross-process via `_cancel_reconcile`; org-fair pre-pass
excludes quota-capped orgs and marks them for the backpressure UI; local
admission budget prevents a saturated local pool head-of-line blocking remote
dispatches; drain mode keeps recovering leases while refusing new work.

Workers: `worker_main` validates topology fail-closed (Redis broker mandatory),
enforces sandbox policy, drains on SIGTERM, and (new) serves health/metrics.
Agent runners: outbound-WS only, token-bound identity, capability hello, env
caching, cancel/drain/restart, offline detection (15s ping / 60s cutoff)
requeues in-flight runs.

Scaling risks: none structural found. The open verification item is a soak
test (100+ queued runs, multi-worker, kill-a-worker) — recommended before the
multi-tenant launch, not before single-tenant beta.

## One-Click Worker Scaling Plan

**Current state: already 90% built — the missing 10% was documentation and the
worker health probe, both delivered this pass.**

- Compose: `docker compose up -d --scale worker=3` works today (no
  container_name/port conflicts; shared env+artifact volumes; SKIP LOCKED
  leasing makes N workers safe by construction).
- Helm: `--set worker.replicas=5`; probes now real.
- Remote machines: UI → Runner Pools → token → generated
  `pip install … / nodyra-runner register / start` join command (wheel index
  served by the API at `/runner-pools/wheels/`); or SSH onboarding which does
  it all server-side, optionally as a systemd unit.
- Registration tokens: signed, org+pool+runner-bound, TTL 365d, revocable by
  row delete; WS re-verifies binding; artifact uploads re-verify per request.
- Health: fleet endpoint `/runner-pools/health`, per-runner last-seen/load,
  run-history buckets, replica dashboard from Redis heartbeats; worker
  `/health/ready` (new) for infra probes.
- Routing today: per-workflow/deployment pool pinning; provider split
  (worker=local+docker, control=agent+k8s); org fairness; env-affinity via
  runner env cache; RSS soft budget for heavy envs.

**Delivered:** `docs/deployment/workers.md` (compose/helm/agent scaling,
env vars, token flow, sandbox+artifact interplay, upgrade procedure).

**Recommended next (priority order):**
1. Label/capability-based routing for *dispatch* workers (workers advertise
   labels via env, queue entries carry requirements) — M effort, unlocks
   GPU/high-mem tiers without agent pools. (P2)
2. Published `nodyra/api` + `nodyra/worker` images so the join story is
   `docker run` not `docker build`. (P1 for launch, S effort)
3. Worker capacity planner card (queue depth vs. worker slots) in Ops UI. (P3)

**Answers to the required questions:** users can scale today (yes, safely —
lease semantics make duplicates impossible); it is now documented; artifacts
work across workers via shared volume or S3 (compose ships MinIO); sandbox
works on workers via the overlay (DooD siblings); remote runners work behind
NAT (outbound WS); credentials never leave the platform for dispatch workers
(same KEK) and are delivered per-run to agents; upgrades: API first (migration
Job), workers roll with drain — documented.

## Sandbox Execution Review

Modes verified in code: `off` (default, trusted single-tenant),
`auto` (containers when daemon reachable, warn-fallback), `required`
(startup-refusal; **mandatory under multi-tenancy** — `sandbox_policy.py`
aborts MT-without-required unless `sandbox_policy_strict=false` explicitly
acknowledges shared-kernel execution). Per-workflow `execution_mode=inherit|
sandboxed|standard` with `sandbox_workflow_default`; per-workflow resource
overrides are write-time validated *and* clamped again at spawn against
`sandbox_max_{memory_mb,cpu,tmpfs_mb}`.

Isolation floor (non-overridable, `container_runtime.hardening_kwargs`):
cap_drop ALL · no-new-privileges · read-only rootfs · tmpfs /tmp only ·
non-root uid 65532 · pids/mem/cpu/nofile/nproc limits · dedicated bridge
network (no postgres/redis/minio reachability) · tini init · runtime
auto-probe kata>runsc>runc. Image builds validate package specs against shell
metacharacters (RD-2) and version images (v3) so stale recipes are never reused.
Cleanup: warm pool TTL + max-runs recycling + shutdown flush; single-tenant
enablement is one flag (`EXECUTION_SANDBOX=auto`) or the compose overlay.

Remaining recommendation: a Kubernetes-Job sandbox provider for Helm users who
won't mount docker.sock (P2); readiness already surfaces sandbox state.

## Artifacts and Result Storage Review

Verified: DB stores only refs + previews; big outputs offloaded
(`output_store`, marker-read sites regression-tested in
`test_output_store.py`); per-node output caps with truncation stubs; artifact
rows carry checksum, content-type, size, metadata; local backend path-resolves
safely; S3 backend registered at startup with fail-noisy fallback; retention
loop prunes runs + artifacts + audit logs; org scoping via parent run
(populate_existing to defeat identity-map bypass) + storage-key namespace for
run-less uploads; workspace artifact browser with bounded pagination and stats
cache invalidation; downloads support `?token=` with full session-revocation
checks in the auth gate.

Recommendations: S3 signed-URL downloads (skip API proxying) P2; GCS/Azure
backends P3; lineage P3.

## MCP Review

Server: streamable-HTTP stateless JSON-RPC, batch support, protocol-version
negotiation, cursor pagination, origin allowlist, OAuth 2.1
protected-resource metadata + introspection or org-scoped PATs, per-request
rate limiting, permission mapping onto the same RBAC table as REST. 61+ tools
including incremental graph editing with optimistic concurrency, validate,
publish, run, watch-events; guidance prompts for builder agents.

Client: external MCP servers as connections with `allowed_tools` validated on
create/update *and* re-checked at call time (`ensure_tool_allowed`), response
size caps (`mcp_max_response_bytes`), timeouts; `mcp_tool` node executes with
expression context (regression-tested after the July NameError fix).

Gaps (unchanged priorities): tool-call trace in run details (P1), quickstart
surfacing in Settings + README (the "marketing P0"), discovery UI
drag-to-canvas (P2), approval gates for side-effecting external tools (P2).

## Deployment and Environment Review

Compose: full stack with healthchecks + `depends_on` conditions; API runs
migrations then serves; worker separated with its own command/healthcheck;
`--scale worker=N` supported; sandbox overlay; MinIO included;
secrets required via `:?` interpolation (SECRET_KEY, INTERNAL_API_TOKEN,
MINIO password) — now also parameterized Postgres password and loopback-only
DB/Redis ports (this pass). Helm: migration Job, RollingUpdate with
maxUnavailable 0, existingSecret support, TLS-noted ingress, trustedProxyCount
default 1 — worker readiness probe now backed by a real server (this pass).
Image: 2-stage uv build, no tests/dev-cache in final layer, non-root user,
HEALTHCHECK. Config: `security_startup_errors()` aborts on default secret /
blank internal token / CORS wildcard under auth, KMS field validation;
`runtime_warnings()` surfaces prod-behaving-like-local via `/ops/runtime-mode`.

Remaining: publish images to a registry (P1 for adoption); backup/restore doc
exists (`docs/backup-restore.md`) — link it from README (P3).

## Security Review

No P0 findings. This pass: HB-2 (network-exposed default-cred DB/Redis in
compose) and HB-3 (readiness error echo) fixed; internal endpoints use
constant-time compare and refuse blank tokens in production; auth gate
enforces session revocation even for `?token=` downloads; CSRF double-submit
with an exempt-list that is method-aware; webhook publish requires auth config
by default (`webhook_require_auth=true`); SSRF guard is uniform across llm/ai_v2
(re-verified — remaining raw posts are fixed-host api.openai.com); sandbox
floor non-overridable; RLS backs ORM scoping on Postgres; secrets redacted from
logs/events. Outstanding: external pen test before public multi-tenant (P1);
rate-limit `/mcp` per-token (currently per-IP+token mix) (P3).

## Performance and Optimization Review

- Backend: wakeup-driven dispatch eliminates poll latency; org-limit
  batch-fetch (T-07) removes N+1 in lease path; stats caching with
  invalidation on artifact delete; bounded list endpoints; pool autoscaler
  raises global concurrency under queue pressure with cooldown.
- Engine: output caps prevent DB/API/UI choke; checkpoint debouncing bounds
  write amplification; process pools keyed per env avoid re-imports.
- Workers: warm subprocess pool + sandbox warm pool + env-affinity on agents;
  RSS soft budget prevents heavy-env OOM stampedes.
- Frontend: heaviest components are the known P2s (NodeDetails 5k lines);
  canvas is React Flow with memoized nodes; virtualize palette/log viewers next.
- Classification: P0 none · P1 soak/load test + published images ·
  P2 label routing, S3 signed URLs, editor decomposition, EXPLAIN audit ·
  P3 dashboards, capacity planner.

## UI/UX Review

Strong: onboarding tour, command palette, diff/history views, data panels with
port viewers, fleet management with join-command generation and drain buttons,
artifact browser, empty states, toasts, error boundaries, a11y test coverage.
High-class recommendations (priority): Create-with-AI as the home primary
action (P1) · template gallery (P1) · MCP quickstart card in Settings (P1) ·
run timeline as default run view (P2) · edge data previews (P2) · per-node
test button (P2) · workflow health score + production-readiness checklist
(P2) · AI fix-failed-run loop (P2).

## Feature Ideas Roadmap

**Worker/runner:** published worker image + `docker run` join (P1/S) ·
dispatch-worker labels + routing (P2/M) · worker capacity planner (P3/M) ·
GPU pool template (P2/S) · agent auto-update channel (P3/M).
**AI-native:** builder cost caps (P2) · AI fix-failed-run (P2/M, high wow) ·
AI workflow optimizer & test generation (P3) · security review pre-deploy (P3).
**Python-native:** typed code-node params via Pydantic (P2/M, differentiator) ·
Jupyter import/export (P3) · per-node requirements (exists per-workflow; P3) ·
FastAPI endpoint generation from workflow (P2/M — pairs with deployments).
**MCP:** quickstart + Settings card (P1/S — the differentiator story) ·
tool-call trace (P1/M) · discovery UI → drag-to-canvas (P2/M) · approval gates
for side-effecting external tools (P2/M) · marketplace/registry (P3/L).
**Workflow UX:** template gallery (P1/M) · replay-from-node UI (P1/S — API
exists) · run comparison (P3) · graph diff already shipped ✅.
**Enterprise:** SSO exists; SCIM (P2/M) · credential-sharing policies (P2) ·
environment promotion + GitOps sync (partially exists via GitHub sync; P2) ·
admin analytics (P3).

## 10/10 Upgrade Plan (deltas only)

| Area | Now | Target | Required changes | Priority/Complexity | Acceptance |
|---|---|---|---|---|---|
| Queue | 9.5 | 10 | Soak test (100 runs, 3 workers, kill-worker, webhook burst) committed as report + nightly CI lane | P1/M | Zero lost/duplicated runs in report |
| Deployment | 9 | 10 | Publish images; compose defaults to `image:`; backup link in README | P1/S | `docker compose up` with no build |
| MCP | 8.5 | 10 | Quickstart card + docs; tool-call trace UI; discovery panel | P1/M | New user connects Claude in <5 min |
| Workers | 9 | 10 | Dispatch-worker labels + routing; capacity planner | P2/M | GPU workflow routes to GPU worker only |
| Frontend | 8.5 | 10 | NodeDetails/EditorPage decomposition; palette virtualization; template gallery | P2/L | Editor TTI < 1.5s on 200-node graph |
| Security | 9 | 10 | External pen test; MCP per-token rate limits | P1/M | Pen-test report, criticals = 0 |
| Observability | 8.5 | 10 | Grafana dashboards + alert rules shipped in deploy/ | P2/S | Dashboard imports clean |

## Tests Run

| Command | Result |
|---|---|
| `uv run pytest packages/core/tests -q` | **389 passed** (25.6s) |
| `uv run pytest apps/api/tests -q` | **1233 passed, 6 skipped** (11m 44s) |
| `uv run pytest packages -q` | **1817 passed, 83 skipped** (3m 00s; includes core) |
| `npm run typecheck && npx vitest run` (apps/web) | **tsc clean; 451 passed, 1 skipped** (80 files) |
| `uv run python scripts/check_rename.py` | **clean** |
| `uv run ruff check .` | **All checks passed** (after fixing 4 I001) |
| `docker compose config -q` (base + sandbox overlay) | **OK** |
| Targeted: `test_worker_health.py`, `test_worker_metrics_server.py`, `test_sandbox_policy.py` | **31 passed** |

## Tests Added

- `apps/api/tests/test_worker_health.py` — 9 tests: worker HTTP listener
  (live/metrics/404/405/query-string), readiness healthy + degraded, secret-DSN
  non-leak assertions on both worker listener and API route, end-to-end socket
  probe against the real asyncio server.
- `apps/api/tests/test_worker_metrics_server.py` — updated to the unified
  listener (still asserts OpenMetrics serving).

## Files Changed

- `apps/api/app/config.py` — `worker_health_port` setting
- `apps/api/app/worker_main.py` — unified worker HTTP listener (metrics+health)
- `apps/api/app/routers/health.py` — shared `readiness_checks()`; sanitized errors
- `apps/api/app/routers/runner_pools.py` — TTL docstring correction
- `apps/api/app/mcp/tools.py` + 3 files — import sorting (ruff)
- `deploy/docker-compose.yml` — loopback DB/Redis ports; parameterized PG password
- `deploy/.env.example` — POSTGRES_PASSWORD documented
- Former Helm chart directory — removed (empty rename leftover)
- `docs/deployment.md` — `control` role documented; stale recommendation fixed
- `docs/deployment/workers.md` — **new** worker scaling guide
- `apps/api/tests/test_worker_health.py` — **new**
- `apps/api/tests/test_worker_metrics_server.py` — updated
- `FABLE5_NODYRA_END_TO_END_PRODUCTION_HARDENING_REPORT.md` — this report

(Also present on this branch: the ~30 uncommitted files from the preceding
node/data-flow audit — SSRF parity, Docker-node gating, output-store fixes,
AI-catalog params — reviewed and re-verified here, reported separately in
`FABLE5_NODE_LIBRARY_AND_DATAFLOW_PRODUCTION_AUDIT.md`.)

## Remaining Risks

1. **No soak/load test has ever run.** Queue semantics are unit/integration
   proven (incl. a Postgres lane), but sustained multi-worker throughput,
   lease timing under load, and webhook bursts are unmeasured. Highest-value
   next verification.
2. **Helm sandbox** relies on docker.sock (not available on most managed K8s);
   K8s-Job sandbox provider is design-only.
3. **Windows dev-box test flake**: one historic disk-full SQLite flake under
   the ~3,000-test combined run (environmental; passes in isolation).
4. **Pen test outstanding** before public multi-tenant exposure.
5. **No published container images** — every deploy builds from source today.

## Final Recommendation

- **Single-tenant beta: YES — ship.** Fail-closed config, durable queue,
  sandbox opt-in via one flag, one-command scaling, green CI.
- **Multi-tenant: architecturally ready, gate on:** soak test, pen test,
  `EXECUTION_SANDBOX=required` deployment recipe validated on a real host.
- **Enterprise: after MT burn-in** — SSO/licensing/KMS/audit exist; add SCIM
  + credential-sharing policies + dashboards.
- **Build next:** MCP quickstart surfacing, template gallery + Create-with-AI,
  published images, replay-from-node UI, soak test.
- **Do not over-engineer yet:** K8s-Job sandbox provider, MCP marketplace,
  multi-region, per-node requirements — none are launch-blocking.
