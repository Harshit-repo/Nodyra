# Fable 5 Nodyra 10/10 Full System Audit and Upgrade Plan

**Date:** 2026-07-03
**Branch:** `fable5-nodyra-10-10-architecture-runtime-ux-audit`
**Auditor:** Fable 5 (independent, fresh-eyes pass — no prior audit conclusions trusted)

---

## Executive Summary

| Dimension | Score |
|---|---|
| **Overall** | **8.3 / 10** |
| Architecture | 9 |
| Workflow engine | 9 |
| Runners / workers | 8.5 |
| Queue / durable execution | 9 |
| Sandbox execution | 8 |
| MCP (server + client) | 8 |
| Artifacts / result storage | 8 |
| Deployment / environment | 8 |
| Security | 8.5 |
| Performance | 8 |
| UI/UX | 7.5 |
| Product / Nodyra readiness | 6.5 |

**Biggest strengths**

1. **The execution plane is genuinely production-grade.** Durable DB queue with `SKIP LOCKED` leasing, org-fair scheduling, exponential backoff, dead-lettering, lease-expiry requeue, cross-process cancellation, checkpoint-based crash resume, and a dependency-counting engine scheduler. This is architecture most workflow startups don't reach until year three.
2. **Security posture is fail-closed by design.** Startup guards abort boot on unsafe configs (default SECRET_KEY + auth, missing internal token in split topology, sandbox config lies, wildcard CORS + credentials). Envelope encryption with external KMS options, secret redaction on every event path, SSRF DNS pinning, webhook auth required by default in production.
3. **The sandbox is real, not aspirational.** Hardened containers (cap_drop ALL, no-new-privileges, read-only rootfs, mem/cpu/pids/ulimits, dedicated egress-isolated network, gVisor/Kata auto-detection), warm-pooled per (org, env), with a policy layer that refuses unsafe multi-tenant configs.
4. **Test discipline:** 2,903 backend tests green (17.6 min), 439 web tests green, CI with Postgres lane, alembic drift check, pip-audit blocking.
5. **Breadth:** 379 registered nodes + 47 integration providers + 61 MCP tools.

**Biggest blockers**

1. **The flagship "MCP tools as workflow nodes" feature was broken at runtime** — every `mcp_tool` node execution raised `NameError` (import dropped in commit `aa575191`; zero execution-level test coverage). **Fixed in this session with regression tests.**
2. **The branch tip failed its own CI lint gate** (59 ruff errors, incl. the 2 undefined-name errors that were the broken import). **Fixed in this session.**
3. **The product story lags the code by weeks.** README/SECURITY.md still say sandboxing and multi-tenancy are "out of scope for v1" while the code ships both. Nobody reading the repo learns the MCP server exists. The rename to Nodyra has not started in the app.
4. **No UI surface for the platform's best capabilities:** sandbox mode is invisible in the UI, there is no artifact browser, no MCP tool discovery panel, and AI workflow creation is not an entry point on the home page.

**Biggest opportunities**

- Ship the Phase 2 rename + Phase 4 product items (MCP quickstart, template gallery, replay-from-node UI) — the code substrate for a premium launch already exists.
- Make sandbox a **product feature** (settings UI, health check, one-line enable) instead of an env var.
- Unify AI building into one obvious "Create with AI" entry point.

**Final recommendation:** The platform is ready for a single-tenant beta **today** at the infrastructure level; what stands between it and a high-class launch is product packaging (rename, docs, UI surfacing of existing capabilities), not engine work. Do not rebuild anything. Execute Phases 2–4 of the existing roadmap, add the sandbox/artifact/MCP UI surfaces, and run the soak test.

---

## Critical Instruction Compliance

- **Old audits were not trusted.** The seven root-level audit reports (`BACKEND_PRODUCTION_AUDIT_2026-06-16.md`, `NOODLE_ARCHITECTURE_ENGINE_ASSESSMENT.md`, `TENANT_ENTERPRISE_READINESS_AUDIT_AND_FIX_REPORT.md`, etc.) and `docs/audits/` were not used as evidence. Every claim in this report traces to current source, tests, config, or a command run in this session.
- **Verification was direct:** I read the engine (`packages/core/nodyra/engine/*`), queue (`app/services/queue.py`), runner (`app/services/runner.py`), executors, sandbox pool + container hardening, artifact backends, MCP server/client, config, security, tenancy, deploy files, CI workflows, migrations, and the frontend; I ran the full backend suite, the web suite, web typecheck, ruff, and alembic head checks myself.
- **`docs/superpowers/plans/*2026-07-02*`** (master roadmap, phase1-stabilize, phase2-rename, phase3-cli, phase4-product) were read **only** for product direction and rename/superpower alignment (Phase 10 section below). Implementation status was verified from code — e.g., the plans' claim that "packages/client is already nodyra-client" was confirmed by reading `packages/client/pyproject.toml`.

---

## Architecture Map

Verified from source on this branch (not copied from docs):

```
apps/
  api/app/
    main.py                    # FastAPI control plane; lifespan starts 13 leader-locked loops
    worker_main.py             # Standalone execution worker (DISPATCH_ROLE=worker, no HTTP)
    config.py                  # Pydantic settings; fail-closed security/topology validators
    security.py                # Sessions, PAT scopes, OAuth introspection, roles/permissions
    tenancy.py                 # Org ContextVar, ORM auto-scoping, Postgres RLS GUC
    models.py                  # ~30 tables (Run, RunQueueEntry, NodeRun, Artifact, MCPConnection…)
    routers/                   # 33 routers: workflows, runs, webhooks, mcp, mcp_connections,
                               #   artifacts, environments, deployments, ops, auth, orgs, admin…
    mcp/                       # MCP server: protocol.py, tools.py (61 tools), resources, prompts
    services/
      queue.py                 # Durable DB queue: lease/heartbeat/fail/DLQ/replay + dispatch loop
      runner.py                # Run orchestration: prepare→execute→persist; checkpoint debounce
      executors/               # RunExecutor seam: local.py (warm subprocess), sandbox.py, remote.py
      sandbox_pool.py          # Warm hardened-container pool keyed (org_id, env_id)
      sandbox_policy.py        # Fail-closed startup policy (MT ⇒ sandbox required)
      container_runtime.py     # hardening_kwargs, runtime detect (kata>runsc>runc), image builds
      runtime_pool.py          # Warm subprocess pool, global slots, autoscaler, RSS budget
      remote_dispatch.py       # Agent/docker/k8s runner-pool dispatch over WebSocket
      artifacts.py / artifact_backends.py / s3_artifact_backend.py   # metadata + pluggable bytes
      run_persistence.py / run_resume.py / stuck_run_detector.py
      events.py                # TopicBroker: in-proc + Redis pub/sub with replay history
      mcp_client.py            # External MCP servers: discover/call, DNS-pinned SSRF guard
      credentials.py / crypto.py / kms/ / org_keys.py   # DEK-per-credential envelope encryption
      environment_builds.py / backends/ (venv, conda, pixi)
      licensing.py / metering.py / org_limits.py / retention.py / leader_election.py
  worker/                      # (empty shell — real worker entrypoint lives in apps/api/app/worker_main.py)
  web/src/
    App.tsx                    # Code-split routes, per-route error boundaries
    EditorPage.tsx (2,091 LOC) # editor/ Canvas.tsx (1,368), NodePalette, Inspector, NDVPanels,
                               #   ChatPanel + AgenticBuildPanel (AI build), RunSidecar/, diff views,
                               #   OnboardingTour, CommandPalette
    queries/, shell/, settings/ (MCP connections, KMS, SSO, roles, audit log pages)
packages/
  core/nodyra/                 # Engine: scheduler.py (dep-counting), node_exec.py, loops, metanodes,
                               #   subworkflows, validation; expr.py; artifacts.py (LocalArtifactStore);
                               #   process_isolation.py (per-env ProcessPoolExecutors); sdk.py
  nodes/nodyra_nodes/          # 379 @node functions + integrations_v2/ (48 providers) + ai_v2/ suite
  runtime/nodyra_runtime/      # Newline-framed JSON runtime server (subprocess + container protocol)
  runner/nodyra_runner_agent/  # Remote runner agent
  client/nodyra_client/        # Python client + `nodyra` CLI (already renamed)
  exporter/, importer/
deploy/
  docker-compose.yml           # postgres/redis/minio/api(control)/worker/web; sandbox opt-in comments
  Dockerfile.python, helm/nodyra/ (api+worker+web deployments, migration job, ingress)
apps/api/alembic/versions/     # 80 migrations, single head (0079), downgrades present, CI drift check
.github/workflows/ci.yml       # ruff, pytest(+timeout), 3.14 lane, PG lane, alembic drift,
                               #   pip-audit (blocking), bandit/mypy (advisory), web tc/test/build, e2e
```

Execution flow (verified): `start_run` (gates: trigger targeting, rate limit, pool precedence, dedicated-pool isolation, daily quota at admission, package preflight, single-flight lock) → `RunQueueEntry` enqueue → dispatch loop leases (org-fair, provider-filtered, local-budget aware) → `_execute_queued_entry` (checkpoint/NodeRun resume, replay seeds, trace rejoin) → `_execute_run_impl` → executor (local warm pool | sandbox container | remote pool) → events through broker (Redis in split topology) → `persist_run_outcome` + artifact refs + error-handler dispatch.

---

## Scorecard

| # | Area | Score | Why | What makes it 10/10 |
|---|---|---|---|---|
| 1 | Overall architecture | 9 | Clean control/execution-plane split, executor seam, explicit topology roles, no hidden dispatch paths | Split `runner.py` (1,711 LOC); delete the empty `apps/worker` shell; ADRs for the run protocol |
| 2 | Workflow engine | 9 | Dep-counting scheduler, loop regions, metanodes, subworkflow invariants, run deadlines, per-type semaphores, defensive cancellation | Event-sourced node lifecycle (see Engine Review); mid-flight node cancellation on deadline |
| 3 | Node system | 9 | 379 nodes, manifest params, kind/schema validation, tool-mode adapter, dynamic MCP nodes | Per-node execution tests for thin wrapper nodes (the `mcp_tool` bug proves the gap); custom-node publish flow |
| 4 | Python-native experience | 9 | Code node, uploadable code modules, per-env venv/conda/pixi, exporter to plain scripts, typed IO | Per-workflow requirements (Phase 4 Task 6); notebook round-trip |
| 5 | Python code execution safety | 8 | Code nodes isolated in per-env ProcessPoolExecutors; policy layer rejects config lies; timeouts evict wedged pools | Default `code_node_timeout_seconds` > 0; seccomp profile on sandbox; docs matching reality |
| 6 | Sandbox execution | 8 | Hardened containers, warm pool, off/auto/required, fail-closed MT policy, egress-isolated network | UI surface + health check; per-workflow/per-node sandbox policy; enabled-by-default compose profile |
| 7 | Runners | 8.5 | Local/sandbox/remote executors, autoscaler, RSS budgeting, heartbeats, offline requeue, ghost cleanup | Runner-pool live observability panel; k8s Job provider productionized |
| 8 | Workers | 9 | Dedicated entrypoint, graceful drain, leases only what it can host, stuck-run detector, env-build queue | Worker-side Prometheus endpoint; multi-worker soak test evidence |
| 9 | Queue / durable execution | 9 | SKIP LOCKED, org fairness, DLQ + replay w/ seeds, lease expiry, HOL fix, checkpoint resume | Monotonic event sequence numbers (roadmap P2); queue metrics dashboards |
| 10 | Webhooks | 9 | 5 auth modes, auth-required default (prod), rate limits, body caps, listen-gated test capture, redacted buffers | Replay protection (nonce/timestamp) for HMAC mode; per-path stats surface |
| 11 | MCP server | 8.5 | 61 tools incl. incremental graph ops w/ optimistic concurrency, approval gates, OAuth 2.1 + scoped PATs, origin checks | Quickstart docs (P0 — nobody knows it exists); tool-call trace UI; resource pagination |
| 12 | MCP client / external tools | 7.5 | Connections CRUD, discovery, DNS-pinned SSRF guard, header blocklist, org-KEK secrets — but the execution node was broken (fixed) | Discovery UI → drag tool to canvas; approval gates + side-effect metadata; org allowlist policy |
| 13 | AI workflow builder | 8 | 1,990-LOC builder + agentic variant, chat panel, draft modal, validated graph ops | "Create with AI" on the home page; full node-vocabulary (Phase 4 Task 2); fix-failed-run loop |
| 14 | AI agent node/runtime | 8 | ai_v2: agents, tools, guardrails, memory, retrievers, MCP tools inside agents, approval pause/resume through the durable queue | Agent eval harness surfaced in UI; cost/token budgets per run |
| 15 | Artifacts / result storage | 8 | Pluggable local/S3, worker→S3 rehoming, org-scoped keys, redacted previews, caps, retention, signed URLs | Artifact browser UI; checksums + content addressing; lineage to DatasetRef |
| 16 | Credentials / secrets | 9 | DEK-per-credential envelope encryption, KMS (vault/aws/gcp), redaction on all event paths, OAuth flows, credential tests | Credential-sharing policies (P3); rotation reminders |
| 17 | Auth / RBAC / multi-tenancy | 9 | Cookie+CSRF & Bearer & scoped PATs & OAuth introspection, WS tickets, custom roles, org RLS + ORM scoping, SSO/SAML, audit log | SCIM (P3); session management UI (list/revoke devices) |
| 18 | Database / migrations | 8.5 | 80 migrations, single head, downgrades, CI drift check, indexed hot paths | Data-retention policy docs; pgbouncer guidance for scale |
| 19 | Deployment / Docker / Helm | 8 | Split-topology compose w/ health checks + required secrets, helm w/ migration job | Sandbox-on production compose profile; backup/restore + upgrade guide; smoke `docker compose up` in CI |
| 20 | Environment / configuration | 9 | Exemplary config.py: fail-closed errors, advisory warnings, topology validation | `.env.example` missing every `SANDBOX_*`/`EXECUTION_SANDBOX` var; config reference doc generated from Settings |
| 21 | Observability | 8 | OTel spans through queue hops, Prometheus gauges/histograms, JSON logs w/ run_id, /ops surfaces, replica heartbeats | Shipped Grafana dashboards + alert rules; log/trace correlation guide |
| 22 | Testing / CI | 9 | 2,903 + 439 tests green; PG lane, 3.14 lane, drift check, pip-audit blocking | Execution-level tests for every node wrapper; soak test in CI (roadmap); mutation testing on engine |
| 23 | Performance | 8 | O(1) scheduling per node, warm pools, checkpoint debounce, output caps, batched org-limit fetches | Soak/load evidence; canvas virtualization for 500+ node graphs; DB EXPLAIN audit of hot list endpoints |
| 24 | Security | 8.5 | Fail-closed guards, envelope crypto, SSRF pinning, redaction, CSRF, scopes, egress policy | mypy/bandit blocking on security modules; pen-test before public launch; MCP client approval gates |
| 25 | Frontend architecture | 8 | React Query + zustand, code-split, per-route error boundaries, 76 test files, typecheck clean | Break up EditorPage (2,091), WorkflowsPage (1,434), Canvas (1,368); bundle budget in CI |
| 26 | UI/UX | 7.5 | Onboarding welcome + tour, command palette, run sidecar, diff views, NDV panels, empty states | AI-create entry point; sandbox settings; artifact browser; MCP discovery panel; run timeline |
| 27 | Onboarding / first-run | 7 | Welcome card + 3 steps + template strip + editor tour | "First run in 5 minutes" guided path with sample data; production-readiness checklist |
| 28 | Node library breadth/quality | 9 | 379 nodes across data/AI/files/geo/security/ML + 47 SaaS providers | Per-provider docs pages; template gallery exercising the long tail |
| 29 | Legacy-name→Nodyra rename readiness | 6 | Excellent executable plan + client pre-renamed + brand homepage in tree; product rename had not yet started at audit time | Execute Phase 2: sweep + `git mv` + env fallbacks + guard script in CI |
| 30 | Product-market differentiation | 8 | MCP-first bidirectional (server AND client), Python-native, AI builder, real sandbox, self-host | Tell the story: docs, quickstart, template gallery, launch site (already drafted in `brand/`) |

---

## Architecture and Engine Review

### What is good (verified)

- **Scheduler** (`packages/core/nodyra/engine/scheduler.py`): dependency-counting execution replaces level barriers; each node starts the moment its predecessors complete. Worker-coroutine pool with sentinel shutdown and full cancellation propagation (`REL-2` handling at scheduler.py:401-406). Deterministic topo order independent of canvas position (documented contract). Loop regions and transparent metanodes are expanded before planning so the core scheduler sees a flat DAG.
- **Node execution** (`node_exec.py`): retries, per-node + per-type timeouts, `$error` output ports (n8n-style visual error handling), context-local stdout capture (safe under concurrent runs), dataset auto-promotion, process isolation for `code` nodes.
- **Subworkflows**: cycle/depth invariants enforced in the engine with explicit meta (no ContextVar smuggling); host resolves children; inline-vs-call outcomes.
- **State machine**: queue status (`queued/leased/running/waiting/completed/failed/cancelled/dead_lettered`) is deliberately separate from user-facing `Run.status`, with documented mapping. Approval-waiting runs park (`waiting`) and resume through `replay_seed`.
- **Durable execution**: checkpoint after each successful node (debounced 250 ms with guaranteed final flush; O(1) incremental serialisation; 1 MiB cap with truncation), resume from checkpoint fast path or NodeRun-row reconstruction fallback.
- **Host isolation of concerns**: `RunExecutor` protocol keeps persistence out of executors; `runner._execute_run` is the single bookkeeping chokepoint.

### Findings and risks

1. **`runner.py` is a god file** (1,711 LOC): checkpointing, context prep, three executor paths, MCP hook install, module registration, error-handler dispatch. It is well-commented but the highest-coupling file in the backend. *Recommendation: extract checkpointing and the in-process execution branch; keep the file under ~800 LOC.*
2. **Run-deadline does not cancel in-flight nodes** (scheduler.py:272-283): past the deadline, new nodes are refused but a running node is never interrupted. For a hung `code` node with no per-node timeout (default `code_node_timeout_seconds=0`), the run can exceed its wall clock indefinitely until the stuck-run detector (default 1,800 s) fires. *Recommendation: default code timeout (e.g. 600 s) + optional hard-cancel at deadline.*
3. **Checkpoint truncation is silently lossy**: a >1 MiB checkpoint is truncated; resume from a truncated checkpoint silently recomputes missing nodes. Correct but unobservable. *Emit a `checkpoint_truncated` run event.*
4. **Should it be more event-sourced?** No — not now. The current model (broker events + capped `run_events` + NodeRun rows + checkpoint) delivers the operational benefits of event sourcing without the storage/replay machinery. The roadmap's monotonic sequence-number item (P2) is the right-sized next step. A `WorkflowCompiler/GraphValidator/ExecutionPlanner/...` decomposition **already effectively exists** (`validation.py`, `_build_plan`, `_execute_nodes`, executors, `run_persistence`, `events`, `artifact_backends`, `SandboxPool`) — renaming modules to match that taxonomy is cosmetic; do not rebuild.
5. **Versioning is adequate**: workflows have versions + revisions + rollback + diff (incl. MCP tools for all of these). Strict immutable version pinning per run is already the dispatch rule (`_execute_queued_entry` graph selection, runner.py:1579-1601 — carefully reasoned draft-vs-version replay).

**Verdict: preserve and polish. No redesign warranted.**

---

## Runners, Workers, and Queue Review

Traced through the failure matrix (code-level reasoning; suite covers most transitions):

| Scenario | Behaviour (verified in code) |
|---|---|
| Run starts / completes | enqueue → lease → `mark_running` → executor → `complete` + `persist_run_outcome` |
| Node fails | `$error` port or node error → worst-status aggregation; error handlers dispatched post-run |
| Retry | `fail(retryable=True)` → exponential backoff (5 s base, 300 s cap), max attempts → DLQ |
| Cancel (same process) | task cancel → `CancelledError` → terminal `cancelled` write |
| Cancel (split topology) | API marks entry cancelled → worker's `_cancel_reconcile` sweep cancels the local task |
| Worker dies | lease expires → `requeue_expired_leases` resets entry **and** the Run row to `queued`; resume from checkpoint |
| API dies | queue is DB-durable; worker unaffected; events replay from Redis history (capped 10 k) |
| Local pool saturated | lease rolled back, `exclude_local` prevents head-of-line blocking of remote entries |
| Remote pool at capacity | `_QueuedError` → requeue with backoff; DLQ mirror to `Run.status=error` so runs never look queued forever |
| Org flood | org-fair pre-pass leases from least-loaded org; capped orgs parked with `queue_reason="org_quota_exceeded"` |
| Hung node | stuck-run detector (no node progress past grace) marks error |
| Large output | 10 MiB engine cap; 256 KiB persistence cap with preview stub; artifact offloading |
| Zombie processes | pool eviction terminate→kill ladder (`process_isolation.py:148-162`); container `init: true` (tini) |
| Graceful shutdown | drain flag stops leasing; in-flight waits bounded; pools/sandboxes flushed |

**Risks / gaps**

1. **Soak evidence is missing.** The roadmap's definition-of-done includes a worker-kill + cancel-storm soak run; it has not happened. This is the single most valuable reliability action remaining.
2. **`_workflow_single_flight_locks` grows unboundedly** on SQLite deployments (one Lock per workflow ever run; trivial memory, but a leak by definition).
3. **Worker heartbeats extend leases only while the run task lives**; a worker wedged in a non-async C extension will hold the lease until expiry — acceptable (lease expiry is the backstop), worth documenting.
4. **`apps/worker/` is an empty shell package** — confusing to contributors; the real worker is `apps/api/app/worker_main.py`. Delete or move.

**10/10 runner/worker architecture:** the current one, plus soak-test evidence, a per-pool live dashboard, and the k8s Job runner promoted from seam to supported provider.

---

## Sandbox Execution Review

### Current behaviour (verified)

- **Modes:** `execution_sandbox = off | auto | required` (`config.py:237`). `auto` probes Docker and falls back to subprocess with a warning; `required` refuses to boot without a working daemon + runtime (`sandbox_pool.init_sandbox`).
- **Hardening (container_runtime.hardening_kwargs):** cap_drop ALL, no-new-privileges, read-only rootfs, tmpfs /tmp (256 m), mem 1 g / cpu 1.0 / pids 256 / nofile+nproc ulimits, tini init, dedicated bridge network (`nodyra-sandbox` — no postgres/redis/minio reachability), isolation runtime auto-pick **kata > runsc (gVisor) > runc**.
- **Pooling:** warm per (org_id, env_id) — cross-tenant container reuse impossible by construction; TTL reaper; max-runs recycling; image = thin env layer over a base image with schema versioning.
- **Policy (fail-closed, `sandbox_policy.py`):** multi-tenancy **requires** `execution_sandbox=required` + subprocess runner + dedicated network unless `sandbox_policy_strict=false` is explicitly set; the "config lie" (sandbox requested but in-process runner would bypass it) aborts boot even single-tenant.
- **Execution path:** `runner._execute_run_impl` routes to `SandboxExecutor` whenever the pool is active and no remote pool is set (runner.py:1295-1318). Cancellation hard-kills the container. Non-clean protocol exit marks the worker dead (never reused).

### Answers

- **Available today?** Yes, end-to-end. **Mandatory?** For multi-tenant, yes (fail-closed). **Optional?** Single-tenant via `EXECUTION_SANDBOX=auto|required`. **Safe?** The container profile matches the task's "hardened_docker" spec already; the biggest residual risks are Docker-socket exposure to the worker (documented sibling-container model) and no seccomp/AppArmor profile pinning.
- **Single-tenant enablement gap is packaging, not code:** the compose file ships it commented out; `.env.example` doesn't mention `EXECUTION_SANDBOX` or any `SANDBOX_*` var at all; there is **no UI surface** (no settings card, no health check, no per-workflow policy).

### Sandbox modes vs. the task's recommended set

| Requested mode | Status |
|---|---|
| `off` | ✅ exists, marked unsafe by policy checks |
| `process` | ✅ exists (default subprocess warm pool + per-env ProcessPool isolation for code nodes) |
| `docker` | ✅ exists (`auto`/`required` with runc) |
| `hardened_docker` | ✅ **is the only docker flavor shipped** — hardening is not optional |
| `remote_worker` | ✅ exists (runner pools: agent/docker/kubernetes providers) |
| `kubernetes_job` | ◻ seam exists (provider enum), not productionized — correctly deferred |

### Single-tenant sandbox plan (P1)

1. **Compose profile:** add `profiles: ["sandboxed"]` service overlay enabling `EXECUTION_SANDBOX: auto` + the docker.sock mount, so `docker compose --profile sandboxed up` is the one-liner. Keep default profile unchanged.
2. **`.env.example`:** document `EXECUTION_SANDBOX`, `SANDBOX_RUNTIME`, `SANDBOX_NETWORK`, `SANDBOX_MEM_LIMIT`, `SANDBOX_CPU_LIMIT`, `SANDBOX_WARM_*`.
3. **UI (Settings → Execution):** read-only card first — mode, runtime (runc/gVisor/Kata), warm/active counts (from `pool.describe()`), and a red "sandbox off — code runs with host privileges" banner when off. Then: per-workflow `sandbox: required` toggle that refuses dispatch to the plain pool (mirror of the org-level `dedicated_pool` gate that already exists at runner.py:584-601).
4. **Unsafe-node linkage:** `unsafe_nodes` findings already gate deployment activation; add "…or run sandboxed" as the suggested remediation in that API response.
5. **Tests:** policy matrix tests exist; add one integration test that `execution_sandbox=required` + fake docker client routes a run through `SandboxExecutor` (executor selection test), and one Docker-unavailable-in-`auto` fallback test if not present.

### Acceptance criteria (from the task) — current status

Easy single-tenant enable ◻ (env var only) · MT mandatory ✅ · host-file access prevented ✅ (read-only rootfs, no binds) · host env leakage prevented ✅ (explicit env only) · CPU/mem/time limits ✅ · network control ✅ (dedicated bridge; per-connection egress policy exists separately) · secret redaction ✅ · stdout capture ✅ (demuxed protocol; noise-tolerant E-10) · cleanup ✅ (force-remove, reaper, recycle caps) · testable ✅ · docs ◻ (deployment.md section exists; README contradicts it) · fail-safe without Docker ✅ (`auto` warns, `required` aborts).

---

## Artifact and Result Storage Review

### Current behaviour (verified)

- Node outputs >256 KiB (`max_output_bytes`) are stub-truncated in the DB with previews; files/dataframes/reports go through `LocalArtifactStore` → artifact refs collected from the event stream → `persist_artifact_refs` writes metadata rows and **rehomes bytes to S3** when configured (worker writes locally; API uploads; local scratch reclaimed) — a correct pattern for warm pools without per-worker credentials.
- Metadata: `Artifact` table with run/node/org scoping, kind, content_type, size, redacted preview/metadata. Org stamped explicitly (background context has no request org).
- Access: routers enforce permissions (`artifact:write`/`artifact:delete`, org scoping via tenancy filter); downloads stream or 307-redirect to time-limited signed URLs (S3); local path resolution guards directory escape (`_resolve_local_path`).
- Caps: 50 MiB/artifact, 100/run (org-limit overridable). Retention loop deletes runs + artifacts + per-run scratch dirs; deletes are batched per backend.

### Does it need optimization? Mostly no — it needs surfacing.

Answers to the task's checklist: DB stores only metadata ✅ · large outputs offloaded ✅ · tenant-scoped ✅ · permission-checked ✅ · secret redaction ✅ · safe paths/URLs ✅ · streaming download ✅ · pluggable local/S3(MinIO/R2/B2) ✅ · retention ✅ · orphan cleanup ✅ (best-effort) · previews ◻ (stored ref previews only; no thumbnail generation) · compression ✖ · checksums ✖ · versioning/content-addressing ✖ · lineage ◻ (DatasetRef exists separately; not unified) · encryption at rest ✖ (delegate to bucket/volume encryption — acceptable, document it).

### Recommended 10/10 artifact work (ranked)

1. **Artifact browser UI (P1):** per-run artifact list exists in data panels; add a workspace-level browser (filter by workflow/run/kind/date, preview, download, delete). This is the visible half of an already-excellent system.
2. **Checksums (P2):** SHA-256 at write time on `LocalArtifactStore`; verify on rehome. Cheap and unlocks dedup later.
3. **GCS/Azure backends (P2):** the protocol makes this a ~200-line adapter each; S3-compatible covers most today.
4. **DatasetRef unification + lineage (P2/P3):** record producing (run, node) on datasets; show lineage in the UI.
5. **Do not build:** content-addressed store, artifact versioning, preview generation service — premature until usage data exists.

---

## MCP Review

### Server (strong — 8.5/10)

- **61 tools** (`app/mcp/tools.py`): full CRUD, incremental graph ops (add/patch/remove node/edge) with **optimistic concurrency** (`expected_graph_revision`) and revision recording, validation summaries, publish/rollback/diff, runs (start/wait/cancel/retry/events), schedules, environments incl. build jobs, credentials listing, run approvals, node catalog search with config suggestions.
- **Workflow-as-tool:** `enable_mcp_tool` exposes any workflow as a first-class MCP tool with a validated parameters schema (`_validate_mcp_parameters_schema`, argument validation at call time).
- **Auth:** scoped PATs (`ndpat_`), optional external OAuth 2.1 AS with introspection + caching + circuit breaker, scope→role mapping per tool, origin checking, protected-resource metadata endpoint. Destructive tools require explicit `approve=true` (`_require_explicit_mcp_approval`).
- **Resources + prompts** exist (`nodyra://` URIs after the rename).

### Client (7.5/10 after this session's fix)

- Connections CRUD + discovery (`tools/list`), DNS-pinned SSRF guard on every request (`resolve_pinned`), dangerous-header blocklist, org-KEK-encrypted secrets, tool→node manifest conversion, `mcp_tool` node executing through a platform hook with per-run ContextVar hygiene.
- **BUG (P1, fixed here):** `mcp_tool` node raised `NameError` on every execution — the expression-helper import was dropped by commit `aa575191`. No test executed the node (only manifest conversion was covered). Fixed + 3 execution-level regression tests (`packages/nodes/tests/test_mcp_tool_node.py`).

### Can Claude/Cursor build workflows through MCP? Yes — the tool surface is unusually complete (incremental edits + validation + publish + run + watch events). **The gap is that nothing tells anyone.** Phase 4 Task 1 (quickstart doc + Settings card) is correctly rated the marketing P0.

### 10/10 MCP roadmap (concurring with + extending the repo's own plans)

P0/P1: quickstart docs + Settings surfacing · MCP tool-call trace in run details (Phase 4 Task 4) · fix shipped here. P2: discovery UI (browse a connected server's tools → drag to canvas as preconfigured `mcp_tool` node) · approval gates + side-effect metadata for external tools · org-level server allowlist. P3: MCP server registry/marketplace, prompt-injection guidance in agent docs.

---

## Deployment and Environment Review

- **Compose** (`deploy/docker-compose.yml`): correct production shape — API as `control`, worker as `worker`, Postgres/Redis health-gated, MinIO with required password, required `SECRET_KEY`/`INTERNAL_API_TOKEN` (`:?` fail-fast), migrations on API start, worker healthcheck via /proc cmdline, named volumes for envs/artifacts. Sandbox enablement documented inline but commented.
- **Helm**: api/worker/web deployments + migration job + ingress + secret template. Not exercised in CI (acceptable pre-GA; smoke it before enterprise claims).
- **Config validation is the standout**: `security_startup_errors()` + `dispatch_topology_errors()` abort boot; `runtime_warnings()` surface degraded-production configs via `/ops/runtime-mode`.
- **Gaps (P1):** `.env.example` lacks the entire sandbox var family; no backup/restore or upgrade guide (the rename plan will add `docs/upgrading-to-nodyra.md` — fold DB backup guidance in); no `docker compose build` smoke in CI; README deployment claims lag the split-topology reality.

Migrations: 80 revisions, single head (`0079_environment_build_jobs`), downgrades present, **CI enforces Postgres drift-check** — better hygiene than most mature products.

---

## UI/UX Review

Verified from source and test suites (no live browser pass in this session — flagged honestly in Remaining Risks).

**Good:** code-split routes with per-route error boundaries (the "black screen" class is designed against, App.tsx:72); onboarding welcome card with 3-step explainer + template strip + dismiss persistence; editor onboarding tour; command palette; run sidecar; execution timeline; workflow version diff UI (nodes/edges/params); NDV panels with credential picker; dataset SQL modal; Plotly/chart views; a11y work (useModalA11y, focus management tests); toast/confirm/prompt system replacing native dialogs; 439 green component tests.

**Premium gaps (what keeps UX at 7.5):**

1. **AI creation is buried.** ChatPanel/AgenticBuildPanel/AiDraftModal live inside the editor; the home page's empty state offers "New workflow" + templates but no "Create with AI" — for an AI-first product this is the #1 conversion miss.
2. **No sandbox surface** — a security differentiator that is invisible.
3. **No artifact browser** — outputs are only reachable through run panels.
4. **No MCP discovery panel** — external tools must be configured blind (connection page exists, tool browsing doesn't).
5. **Template gallery is a strip of buttons**, not a browsable gallery with descriptions/previews (Phase 4 Task 5 covers this).
6. **God components** (EditorPage 2,091 LOC) will slow every future UX iteration.
7. **Small polish:** the web title still used the former name; one unhandled error in the Vitest run (OnboardingTour test — suite still green) was worth chasing.

**10/10 additions** (beyond the roadmap's): run timeline with per-node durations as the default run view; data preview on edges (roadmap P2); keyboard-shortcut help overlay; workflow health score card (P3).

---

## Security Review

**Verified strong:** fail-closed startup guards (default secret + auth boundary, blank internal token in split topology, wildcard CORS + credentials, KMS field validation, sandbox config lies) · envelope encryption with DEK-per-credential + external KMS options · secret redaction applied to events, logs, artifact previews, webhook capture buffers · webhook auth required by default in production, 5 methods incl. HMAC + JWT · SSRF: DNS-pinned requests for MCP + HTTP nodes, private-egress policy switch, header blocklists both directions · CSRF double-submit for cookie sessions, Bearer exempt · WS tickets keep tokens out of access logs · scoped PATs, custom roles, permission-gated ops/artifact/registry routes · RLS + ORM auto-scoping + org GUC on Postgres · rate limits (auth, webhooks, per-workflow runs) with trusted-proxy awareness · audit log with retention · `pip-audit` blocking in CI.

**No P0 findings.** P1/P2 observations:

| # | Severity | Finding | Recommendation |
|---|---|---|---|
| S1 | P1 (fixed) | `mcp_tool` runtime break — availability, and evidence of a coverage hole in dual-use surface | fixed + regression tests |
| S2 | P2 | External MCP tool calls have **no approval gate** (internal MCP server tools do) — a malicious/compromised external server can be invoked by any run without human review | approval gates + side-effect metadata (roadmap P2) — endorse and prioritize |
| S3 | P2 | Docs contradict the security model (README says no sandbox/MT) — operators may make wrong trust decisions in *both* directions | rewrite README/SECURITY.md security section (Phase 2 rename sweep is the natural moment) |
| S4 | P2 | `unsafe_node_policy` default `warn` single-tenant is fine, but nothing suggests sandboxing as remediation | link findings → sandbox toggle (see Sandbox plan) |
| S5 | P3 | bandit/mypy advisory-only on security modules | promote to blocking for `security.py`, `crypto.py`, `tenancy.py`, `mcp/` |
| S6 | P3 | Webhook HMAC mode has no replay protection | optional timestamp+nonce window |

---

## Performance and Optimization Review

**Backend:** hot list endpoints paginate; org-limit fetches batched (T-07 fix visible in `_org_fair_order`); queue notify eliminates poll latency (in-proc event + Redis pub/sub); Prometheus histograms on run duration. **Engine:** dependency-counting scheduler removed O(N) wait rounds; semaphore-gated worker pool; per-type concurrency. **Checkpointing:** debounced, incremental (O(1) per node), capped. **Runners:** warm subprocess + warm container pools, autoscaler, RSS soft budget (reserves measured env RSS before admission — unusually sophisticated), idle reapers, recycle ceilings. **Artifacts:** offloading keeps fat outputs off the API; stub previews keep list endpoints slim. **Frontend:** route code-splitting, memoization passes (documented plan files), React Query caching.

**Classified opportunities:**

- **P0:** none found.
- **P1:** soak/load test (throughput + lease timing under multi-worker) — everything else is theory until this runs; add an index audit (`EXPLAIN`) for `runs` + `run_queue_entries` hot filters on Postgres (indexes exist; verify plans).
- **P2:** canvas virtualization for very large graphs; `LocalBackend.stats()` walks the whole artifact tree on each ops call (cache it); event-history replay cap tuning (10 k events).
- **P3:** Redis queue backend (interface reserved — explicitly premature, agree); queue sharding (roadmap agrees: premature).

---

## Nodyra Rename and Superpowers Alignment

**What the 2026-07-02 plans say (direction only):** 4-phase path — stabilize (done: `98641c1a`), full rename (Python packages, env vars w/ one-release fallback, Redis keys, cookies, MCP URIs; keep `ndpat_`, DB tables, alembic history), CLI v1 (`nodyra-client` on PyPI, `run watch`, rich/`--json`), P1 product items (MCP quickstart P0, AI-builder vocabulary, replay-from-node UI, MCP trace, template gallery, per-workflow requirements, HuggingFace provider). Decision log is locked and sensible.

**Verified current state at audit time:** Phase 1 committed. Phase 2 had **not started** in product code: the web title, Python packages, MCP URIs, and session cookies still used the former name. Pre-positioned: `packages/client` was already `nodyra_client` with a `nodyra` CLI entry point; `brand/homepage/nodyra.html` (uncommitted, in-flight); landing-page plan existed.

**Assessment of the strategy: correct.** Full rename pre-public-release is the only cheap moment; the case-preserving sweep + `git mv` + guard-script approach is the right mechanism; keeping DB/table/alembic names avoids pointless churn; the environment-variable migration needed one-release compatibility where technically safe. One addition: **fold the README/SECURITY.md security-model rewrite into the rename sweep** (S3 above) — the rename touches every doc anyway, and shipping "Nodyra" with a security section describing an obsolete product would undermine the premium positioning.

**Launch-readiness sequencing (recommended):** Phase 2 rename → Phase 4 Task 1 (MCP quickstart — it is the differentiator story) → template gallery + replay-from-node → Phase 3 CLI (parallel) → sandbox/artifact/MCP UI surfaces (this report's P1s) → soak test → launch.

---

## Bugs Found

| # | Sev | Area | File(s) | Evidence / Repro | Root cause | Fix | Status |
|---|---|---|---|---|---|---|---|
| BUG-1 | **P1** | MCP client node | `packages/nodes/nodyra_nodes/mcp_tool.py` | `ruff` F821; any workflow running an `mcp_tool` node → `NameError: name 'build_context' is not defined` (line 38 executes unconditionally) | Commit `aa575191` ("fix params format") dropped the expression-helper import; only manifest-conversion was tested, never node execution | Re-added import; added 3 execution-level regression tests | **Fixed** ✅ |
| BUG-2 | **P1** | CI / repo health | 30 files | `uv run ruff check .` → 59 errors at branch tip; CI's blocking `ruff check .` step ⇒ tip cannot merge | Recent commits (incl. `65f2d82c`) landed without the lint gate | 42 auto-fixed (imports/unused); manual: `node_registry.py` logger placement, `test_custom_roles.py`/`test_engine_validation.py` blind-exception asserts → `IntegrityError`/`GraphError`, `test_file_nodes.py` section imports noqa'd, `scripts/test_mcp_e2e.py` import order. `ruff check .` now passes | **Fixed** ✅ (per Harry: future lint issues to be noted, not chased) |
| BUG-3 | P2 | Docs/security | `README.md`, `SECURITY.md` | README §"Security model" claims sandbox/MT "out of scope for v1"; code ships both (`sandbox_pool.py`, `tenancy.py`) | Docs never updated after MT Phase D | Rewrite during Phase 2 rename sweep | Open |
| BUG-4 | P2 | Env/config docs | `.env.example` | No `EXECUTION_SANDBOX`/`SANDBOX_*` entries despite full config support | Sandbox slice shipped without example-env update | Add documented block | Open |
| BUG-5 | P3 | Web tests | `OnboardingTour` test | vitest: "1 error" (unhandled) alongside 439 passing | Unawaited async in test teardown (suspected) | Chase when touching onboarding | Open |
| BUG-6 | P3 | Repo hygiene | `apps/worker/` (empty pkg), root-level stale audit reports, malformed local artifact directory | Listed in repo root | Phase 1 hygiene task incomplete | Fold into Phase 2 sweep | Open |

---

## Fixes Made

1. **BUG-1 — `mcp_tool` NameError**
   - Files: `packages/nodes/nodyra_nodes/mcp_tool.py` (+1 line import)
   - Tests added: `packages/nodes/tests/test_mcp_tool_node.py` — executes the node function directly: dispatch through the platform hook, `{{ $json.* }}` expression resolution from inputs, empty-arguments default. 3 tests, all passing.
   - Verification: `uv run pytest packages/nodes/tests/test_mcp_tool_node.py -q` → 3 passed.
2. **BUG-2 — lint gate red**
   - `ruff --fix` for import-sort/unused-import/alias classes (42 errors, ~28 files), plus 4 manual fixes that improve tests (blind `Exception` asserts replaced with the precise `IntegrityError`/`GraphError`).
   - Verification: `uv run ruff check .` → "All checks passed!"; targeted re-run of every behaviourally-touched test file → 110 passed.

No other code was changed. Pre-existing uncommitted work in the tree (`apps/web/src/LoginPage.tsx`, `apps/web/src/WorkflowsPage.tsx`, `brand/homepage/nodyra.html`) was left untouched.

---

## 10/10 Upgrade Plan

Format: current → target 10/10; gaps; changes; priority; complexity; acceptance.

### Area: Workflow Engine (9 → 10)
- Gaps: no mid-flight cancellation at run deadline; silent checkpoint truncation; default code timeout 0.
- Changes: hard-cancel option at deadline; `checkpoint_truncated` event; `code_node_timeout_seconds` default 600.
- Tests: deadline-cancels-running-node; truncation event assertion.
- Priority P2 · Complexity S · Accept: hung code node terminates at deadline with a clear error.

### Area: Runners/Workers/Queue (8.5–9 → 10)
- Gaps: no soak evidence; single-flight lock map growth; empty `apps/worker`.
- Changes: soak script (worker kill + cancel storm + webhook flood, zero stuck runs) run against compose and recorded; bounded lock map; delete shell package.
- Priority **P1** · Complexity M · Accept: soak report committed; zero stuck/duplicated runs.

### Area: Sandbox (8 → 10)
- Gaps: packaging + UI (see Sandbox Review plan, items 1–5).
- Priority **P1** (compose profile, .env.example, read-only settings card) / P2 (per-workflow policy) · Complexity M · Accept: single-tenant user enables sandbox with one flag, sees runtime + health in Settings; per-workflow "require sandbox" refuses plain-pool dispatch.

### Area: Artifacts (8 → 10)
- Changes: artifact browser UI (P1, M); checksums (P2, S); GCS/Azure backends (P2, M); lineage (P3).
- Accept: user finds any artifact in ≤3 clicks from home; checksum stored per artifact.

### Area: MCP (8–8.5 → 10)
- Changes: quickstart docs + Settings card (**P0 marketing**, S); tool-call trace in run view (P1, M); discovery UI drag-to-canvas (P2, M); external-tool approval gates + allowlist (P2, M).
- Accept: fresh user connects Claude in one paste; every external tool call visible in run trace.

### Area: Deployment/Env (8 → 10)
- Changes: sandboxed compose profile + env docs (P1, S); backup/restore + upgrade guide (P1, S); compose build smoke in CI (P2, S); helm smoke (P3).
- Accept: `docker compose --profile sandboxed up` works first try on a fresh Linux host.

### Area: Security (8.5 → 10)
- Changes: external-MCP approval gates (P2); README/SECURITY rewrite (P1, in rename); blocking mypy/bandit on security modules (P3); pre-launch pen test (P1 before public multi-tenant).
- Accept: no doc/reality contradictions; dual-use surfaces gated.

### Area: Performance (8 → 10)
- Changes: load test + EXPLAIN audit (P1, M); canvas virtualization (P2, M); artifact stats caching (P2, S).
- Accept: documented throughput numbers (runs/min, p95 latency) at 3 workers.

### Area: Frontend/UI/UX (7.5 → 10)
- Changes: "Create with AI" home entry (P1, S); template gallery (P1, in Phase 4); sandbox settings card (P1); artifact browser (P1); replay-from-node UI (P1, in Phase 4); EditorPage decomposition (P2, L); run timeline default view (P2); MCP discovery panel (P2); fix-failed-run AI loop (P2).
- Accept: first-time user reaches a successful run in <5 minutes via template or AI; failed run → replay from node without re-running the whole graph.

### Area: Nodyra rename/product (6.5 → 10)
- Changes: execute Phase 2 exactly as planned (P1, L — mechanical); Phase 3 CLI (P1, M); Phase 4 items (P1); landing page ship (P1, mostly done in `brand/`).
- Accept: rename gate green in CI; `pip install nodyra-client` works; README tells the MCP + sandbox story truthfully.

---

## Prioritized Roadmap

### P0 — Must fix immediately
1. ~~`mcp_tool` runtime break~~ — **fixed this session**.
2. ~~CI lint gate red at tip~~ — **fixed this session**.
3. Commit these fixes; nothing else qualifies as P0. *(MCP quickstart is the "marketing P0" per the product plan — scheduled in P1 below.)*

### P1 — Required for a serious beta / public launch
1. Phase 2 rename (with README/SECURITY security-model rewrite folded in).
2. Soak test run + committed report (worker kill, cancel storm, webhook flood).
3. MCP quickstart docs + Settings surfacing (Phase 4 Task 1).
4. Sandbox packaging: compose profile, `.env.example` block, Settings read-only card + health.
5. "Create with AI" home entry point; template gallery (Phase 4 Task 5); replay-from-node UI (Phase 4 Task 3).
6. Artifact browser UI.
7. Backup/restore + upgrade docs.
8. Phase 3 CLI v1 → PyPI.

### P2 — Differentiators
MCP discovery UI (drag tool to canvas) · external-MCP approval gates + side-effect metadata + org allowlist · MCP tool-call trace · fix-failed-run AI loop · AI test generation · data preview on edges · per-workflow sandbox policy · run comparison view · artifact checksums + GCS/Azure · canvas virtualization · EditorPage decomposition · event sequence numbers.

### P3 — Enterprise / future
kubernetes_job sandbox provider · SCIM · per-org usage analytics · credential sharing policies · MCP marketplace · workflow health score · queue sharding (explicitly premature) · mutation testing on engine.

---

## Tests Run

| Command | Result |
|---|---|
| `uv run pytest -q --timeout=120` (full backend: apps + packages) | **2,903 passed, 89 skipped, 0 failed** (17 m 36 s) |
| `npm test -- --run` (apps/web) | **439 passed, 1 skipped** across 76 files (1 unhandled-error warning, suite green — BUG-5) |
| `npm run typecheck` (apps/web) | clean |
| `uv run ruff check .` | 59 errors at tip → **"All checks passed!"** after fixes |
| `uv run pytest packages/nodes/tests/test_mcp_tool_node.py -q` | 3 passed (new regression tests) |
| Re-run of all lint-touched test files (validation/custom-roles/file-nodes/node-registry/mcp-tool) | 110 passed |
| `alembic heads` | single head `0079_environment_build_jobs` |

Not run (honestly): `docker compose build/up` (no Docker probe on this Windows host this session), live browser UX pass, soak/load test, mypy (advisory in CI).

## Tests Added

- `packages/nodes/tests/test_mcp_tool_node.py` — 3 execution-level tests for the `mcp_tool` node (dispatch, expression resolution, empty-args default). Closes the coverage hole that let BUG-1 ship.

## Files Changed

- `packages/nodes/nodyra_nodes/mcp_tool.py` — restore dropped expression-helper import (BUG-1).
- `packages/nodes/tests/test_mcp_tool_node.py` — **new** regression tests.
- Lint (BUG-2): `ruff --fix` across ~28 files (import order/unused imports only — no behaviour change), plus manual edits to `apps/api/app/routers/node_registry.py`, `apps/api/tests/test_custom_roles.py`, `packages/core/tests/test_engine_validation.py`, `packages/nodes/tests/test_file_nodes.py`, `scripts/test_mcp_e2e.py`.
- This report: `FABLE5_NODYRA_10_10_FULL_SYSTEM_AUDIT_AND_UPGRADE_PLAN.md`.

Untouched pre-existing working-tree changes: `apps/web/src/LoginPage.tsx`, `apps/web/src/WorkflowsPage.tsx`, `brand/homepage/nodyra.html`, `.agents/`, `.github/skills/`, `docs/superpowers/plans/2026-06-30-nodyra-landing-page.md`.

---

## Remaining Risks

1. **No soak/load evidence.** The queue/runner design is excellent on paper and under unit tests; multi-worker behaviour under churn is unproven. Highest-value next action.
2. **No live UI verification in this audit** — UX findings are code-derived; a browser pass may surface additional polish items.
3. **Docker/Helm paths not smoke-tested here** (Windows host, no daemon probe); compose is well-formed but unexercised this session.
4. **The lint regression pattern** (tip failing its own CI gate) suggests commits are landing without local gates — branch protection (Phase 1 Task 9, still open per the roadmap's manual checklist) is the structural fix.
5. **Rename risk window:** cookie/Redis-prefix breaking changes need the documented drain + re-login window; a deployed instance upgraded mid-queue would strand entries.
6. **Coverage holes of the BUG-1 shape may exist elsewhere** — thin wrapper nodes with manifest-only tests. A cheap sweep: import + call every registered node with stub ctx where feasible.

---

## Final Recommendation

- **Single-tenant beta: ready now** (with the two fixes committed). The engine, queue, sandbox, and security layers exceed beta bar; ship behind the existing `runtime_mode=production` guardrails.
- **Multi-tenant: architecturally ready, operationally not yet.** The fail-closed MT policy, RLS, org fairness, quotas, and sandbox-required posture are in place. Run the soak test, the pen test, and a real multi-worker staging burn-in before accepting untrusted tenants.
- **Enterprise: not yet** — Helm is unsmoked, SCIM/analytics absent, kubernetes_job sandbox is a seam. Correctly sequenced as P3.
- **Build next (in order):** commit fixes → Phase 2 rename → soak test → MCP quickstart + sandbox packaging + AI entry point + artifact browser → CLI → launch as Nodyra.
- **Do not over-engineer:** no engine rewrite, no event-sourcing migration, no queue sharding, no Redis queue backend, no content-addressed artifacts. The code is ahead of the product — every 10/10 gap that matters is packaging, surfacing, and proof, not architecture.
- **Fastest path to 10/10:** the platform's differentiators (bidirectional MCP, hardened sandbox, durable Python-native engine) already exist in code. Two to three weeks of product work — rename, docs that tell the truth, four UI surfaces, and one soak report — moves the overall score from 8.3 to launch-grade.
