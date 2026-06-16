# Noodle Technical Audit Scope

> Created 2026-06-16 on branch `feat/arch-program-phase5`. This is a ground-up
> production/open-source/SaaS readiness audit. It **builds on** the existing
> in-repo audit trail rather than restarting it — see "Prior work" below.

## Objective

Assess Noodle for four release gates and produce actionable, evidence-based
findings with fixes where safe:

1. **Investor/demo readiness** — does it work end-to-end and look credible?
2. **Open-source release readiness** — DX, docs, security hygiene, repo cleanliness.
3. **Self-hosted production readiness** — deployment, data safety, execution correctness.
4. **SaaS / multi-tenant readiness** — isolation, sandboxing, abuse resistance, scaling.

Noodle is a **Python-first, self-hostable workflow automation platform** (n8n-in-spirit,
but node logic is pure Python). The audit therefore weights **Python execution safety**,
**workflow-graph correctness**, **node/plugin extensibility**, **queue/worker robustness**,
**multi-tenant isolation**, and **workflow-builder UX** most heavily.

## Prior work (do not duplicate — extend)

The repo already contains a serious, resumable audit trail. New findings must
cross-reference these and avoid re-reporting closed items:

- `docs/production-readiness-audit.md` — multi-wave audit, resumable ledger (through 2026-06-08).
- `docs/production-review-2026-06-14-audit.md` — Opus pass, pre-mortem + findings ledger.
- `docs/production-review-2026-06-14.md`, `docs/architecture.md`,
  `docs/architecture-improvement-plan.md`, `docs/frontend-audit.md`,
  `docs/multi-tenancy-plan.md`, `docs/licensing-plan.md`.

This audit's deliverables live under `audit/` and at repo root (scope, map,
final report) so they are clearly the **current pass** and don't collide with the
historical `docs/` ledgers.

## Repository Overview (discovered)

Monorepo. Backend = Python (FastAPI + SQLAlchemy async + Alembic). Frontend =
React 18 + Vite + TypeScript + Zustand + TanStack Query + @xyflow/react. uv
workspace for Python; npm for web.

| Area | Path | Size (approx) |
|---|---|---|
| API service (FastAPI) | `apps/api/app` | 100 files, ~27.7k LOC |
| Execution engine core | `packages/core/noodle` | 24 files, ~6.6k LOC |
| Node library | `packages/nodes/noodle_nodes` | 86 files, ~34.3k LOC |
| Remote runner agent | `packages/runner` | 7 files, ~0.8k LOC |
| Runtime helpers | `packages/runtime` | 5 files, ~0.9k LOC |
| Code-first exporter | `packages/exporter` | 5 files, ~0.7k LOC |
| Web frontend | `apps/web/src` | ~38.7k LOC TS/TSX |
| Worker entrypoint | `apps/api/app/worker_main.py` | (worker reuses `app`) |
| Migrations | `apps/api/alembic/versions` | 51 migrations |
| Tests | `**/tests` | 145 Python test files, 39 web test files |
| Deploy | `deploy/` | docker-compose, Dockerfile.python, Helm chart |
| CI | `.github/workflows/ci.yml` | single workflow |
| Docs | `docs/` | ~30 docs incl. prior audits |

> Note: `apps/worker/worker` contains no `.py` files — the worker runs from
> `apps/api/app/worker_main.py`. The `apps/worker` dir is a candidate for cleanup
> (verify before deleting).

## Audit Areas

### 1. Architecture
System design; module boundaries (api ↔ core engine ↔ nodes); API surface;
workflow execution engine (`packages/core/noodle/engine`); node system; durable
queue + worker split (DISPATCH_ROLE control/worker/disabled); state/persistence;
DB design (51 migrations); extensibility & plugin/node architecture; remote
runner pools (agent/docker/k8s); sandbox-per-run isolation; long-term scalability.

### 2. Backend
Routers (`apps/api/app/routers`, 24 routers); services layer
(`apps/api/app/services`, ~50 modules); models/schemas; auth/z; execution logic;
durable queue (`services/queue.py`, Postgres SKIP LOCKED); error handling;
logging/tracing (`app/tracing.py`); validation; dependency mgmt (uv.lock);
async/background loops in `main.py` lifespan; DB queries; migrations; security;
test coverage.

### 3. Frontend
App structure (`apps/web/src`); routing (react-router v6); state (Zustand store
in `editor/store`, TanStack Query in `queries/`); API client (`api.ts`); workflow
canvas (`editor/Canvas.tsx`, xyflow); node UI (`editor/NodeCard`, `NodeDetails`,
fields); forms; error/loading states; a11y (`editor/A11yModal`); responsiveness;
visual polish; reusable components; performance; type safety.

### 4. Workflow Builder / Canvas
Node drag; edge creation (`NoodleEdge`); node config (Inspector/NDV);
validation; port logic (`PortLegend`, `PortDataViewer`); execution status
(`barStatus`, `SaveIndicator`); layout; undo/redo; keyboard
(`CommandPalette`); large-workflow perf; serialization; error highlighting;
loops/map groups (`LoopFrame`, `MapGroupNode`); subworkflows/metanodes.

### 5. Node System
Built-in nodes; node schema/contract (`packages/core/noodle/sdk.py`, `models.py`);
I/O ports; validation; runtime execution (`engine/node_exec.py`); Python code
nodes; AI/agent nodes (`engine/agent.py`, `ai_runtime.py`); trigger nodes;
integrations_v2 providers; per-node error handling; versioning; node tests
(`packages/nodes/tests`).

### 6. Execution Engine
DAG build & validation (`engine/validation.py`); cycle detection; dependency
resolution; scheduler (`engine/scheduler.py`); parallelism; retries; timeouts;
cancellation; resume-from-failed (`services/run_resume.py`); error propagation;
result persistence (`services/run_persistence.py`); logs/events
(`services/events.py`, Redis broker); node/run status; idempotency; resource
limits; loops (`engine/loops.py`); subworkflows (`engine/subworkflows.py`).

### 7. Queue & Worker System
Durable queue design (`services/queue.py`, `RunQueueEntry`, SKIP LOCKED);
QUEUE_BACKEND=redis|db; worker startup/shutdown drain (`worker_main.py`);
leasing/ack; retry/visibility; dead-letter; failure recovery; concurrency;
backpressure; leader election (`services/leader_election.py`); remote dispatch
(`services/remote_dispatch.py`); observability. **Deliverable also recommends
keep/replace/simplify.**

### 8. Node System internals & contracts — see Area 5; deep dive on contract consistency.

### 9. Python Code Execution Safety (CRITICAL)
Sandbox model (`services/isolation.py`, `unsafe_nodes.py`, `sandbox_policy.py`,
`sandbox_pool.py`, `container_runtime.py`, `process_isolation.py`); subprocess
vs Docker (runc/gVisor/Kata) isolation; per-env venvs (`services/venv.py`,
`backends/`); fs/network/env access; secrets exposure; timeouts; CPU/mem limits;
import restrictions; expression sandbox (`noodle/expr.py`, `expr_preview_worker.py`).

### 10. Logging, Observability, Errors
`app/tracing.py`; structured logs; run/exec IDs; correlation; redaction
(`services/redaction.py`); metrics (`services/metering.py`); health
(`routers/health.py`, `ops.py`); swallowed exceptions; sensitive data in logs.

### Frontend areas 11–17 — architecture, canvas, node UI, API integration, state, polish, performance (see §5 of prompt).

### 18. Infrastructure & Deployment
`deploy/Dockerfile.python`, `deploy/docker-compose.yml`, Helm chart; uv
`--locked` sync; build cache; `.dockerignore`; root-user; healthchecks; prod
config; volume persistence (envs/artifacts/pg); api/worker container split;
broker config; reverse-proxy/HTTPS assumptions; CI (`ci.yml`); env var docs
(`.env.example`).

### 19. Testing
pytest (asyncio auto, importlib mode), pytest-timeout, Postgres + sqlite lanes;
vitest + Playwright e2e; coverage gaps in engine/auth/queue/sandbox; fixtures;
mocking; CI command parity.

### 20. Documentation
README, `docs/*`, deployment, node-dev, integration-dev, MCP, licensing;
setup accuracy; architecture clarity; troubleshooting.

### 21. Security (dedicated report)
Auth/z, secrets (`crypto.py`, `org_keys.py`), CORS, CSRF, XSS (dompurify usage),
SSRF (webhooks/http nodes/provider_webhooks), injection, code-exec sandbox,
file uploads/artifacts, dependency vulns, container security, multi-tenant
isolation (`tenancy.py`, `isolation.py`, `org_limits.py`), rate limiting, audit log.

### 22. Performance / 23. Product-UX / 24. Architecture review — dedicated reports.

## Audit Methodology

Each area is reviewed in phases: (1) read & map relevant files; (2) identify
problems with file:line evidence; (3) classify severity; (4) explain impact;
(5) recommend fix; (6) implement safe fixes incrementally; (7) add/recommend
tests. Product/architecture decisions are flagged **separately** from bugs.

## Severity Levels

- **Critical** — security / data loss / execution failure / multi-tenant escape.
- **High** — major bug or scalability blocker.
- **Medium** — maintainability / performance / product issue.
- **Low** — polish, cleanup, naming, documentation.

## Final Output Format (per finding)

Area · Files reviewed · Findings · Severity · Why it matters · Recommended fix ·
Implementation notes · Test recommendations · Status (Not started / Reviewed /
Fixed / Needs decision).

## Deliverables index

- `NOODLE_TECHNICAL_AUDIT_SCOPE.md` (this file)
- `NOODLE_REPOSITORY_MAP.md`
- `audit/AUDIT_PROGRESS.md` (running tracker)
- `audit/backend/*.md`, `audit/frontend/*.md`, `audit/infrastructure/*.md`,
  `audit/testing/*.md`, `audit/documentation/*.md`, `audit/security/SECURITY_REVIEW.md`,
  `audit/performance/PERFORMANCE_REVIEW.md`, `audit/product/PRODUCT_UX_REVIEW.md`,
  `audit/architecture/ARCHITECTURE_REVIEW.md`
- `NOODLE_AUDIT_FINAL_REPORT.md` (master report)
