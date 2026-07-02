# Fable 5 — Noodle Full App Exploration Report

Date: 2026-07-02 · Branch: `fable5-full-noodle-audit-optimization-product-review`
Method: full-repo exploration (code-verified, not doc-derived), knowledge-graph analysis (15.8k nodes / 60.5k edges), file-level reads of every execution-critical module.

---

## 1. Architecture Map

### Monorepo layout (uv workspace)

| Path | What it is |
|---|---|
| `apps/api/app/` | FastAPI backend (~21k LoC): 33 routers, 60+ services |
| `apps/api/alembic/` | 79 migrations (0001 → 0078) |
| `apps/api/tests/` | ~100 backend test files |
| `apps/web/src/` | React 18 + Vite + zustand + React Query SPA (~46.5k LoC TSX/TS) |
| `packages/core/noodle/` | The workflow **engine** (scheduler, node_exec, loops, metanodes, subworkflows, validation, expr, SDK, serialization, process isolation) |
| `packages/nodes/noodle_nodes/` | **Node library**: ~50 modules, 400+ node types (builtin, LLM, AI-v2 agents, data science, integrations_v2 providers, MCP tool node) |
| `packages/runtime/` | `noodle_runtime` — subprocess runtime server run inside env venvs / sandbox containers |
| `packages/runner/` | `noodle_runner_agent` — remote runner agent (WS to API, process pool) |
| `packages/exporter/` | Workflow → Python module/script/Docker export |
| `packages/importer/` | Python script → workflow import |
| `packages/client/` | Python client library |
| `deploy/` | docker-compose (postgres+redis+minio+api+worker+web), Helm chart, hardened Python Dockerfile |
| `.github/workflows/` | ci.yml, release.yml |

### Backend architecture (verified from code)

- **App wiring** (`app/main.py`): global `resolve_org` dependency (tenancy), middleware stack (CORS outermost → tenant-context scope → auth gate → CSRF double-submit → security headers/CSP → metrics → body-size cap 10MiB), lifespan boots 12 background loops (scheduler, retention, queue dispatch, broker reaper, stuck-run detector, idle reaper, autoscaler, gh-sync, ghost cleanup, replica+runner heartbeats, cloud idle terminate) with leader election and role gating (`dispatch_role` = inline/control/disabled/worker; `scheduler_role` = inline/leader/disabled).
- **Fail-closed startup**: `security_startup_errors()` (default SECRET_KEY under auth/MT, missing internal token in split topology) and `dispatch_topology_errors()` (split topology requires Postgres + Redis) abort boot.
- **Auth/RBAC** (`app/security.py`): bearer + httpOnly-cookie dual auth, CSRF, PATs (`ndpat_`, SHA-256 hashed, scoped, org-bound), external OAuth introspection for MCP (cached, circuit-breaker), role table (viewer/editor/admin/owner) + ~35 permissions, custom roles (replace semantics), instance-level vs org-level permission split.
- **Multi-tenancy** (`app/tenancy.py`): two enforcement layers — ORM `do_orm_execute` hook auto-appends `org_id` criteria to every SELECT of every org-scoped model (discovered from mapper registry) + Postgres RLS GUC (`SET LOCAL app.current_org`); fail-closed default-org fallback; `run_as_system()` opt-out for background loops; startup asserts the PG role has NOBYPASSRLS.
- **Queue** (`services/queue.py`): durable DB-backed run queue (RunQueueEntry): enqueue/lease/heartbeat/complete/fail/cancel/replay/requeue_expired_leases; priority + FIFO; SKIP LOCKED on Postgres; exponential backoff; dead-letter; per-org fair scheduling (fewest-in-flight-first) with `max_concurrent_runs` caps and `org_quota_exceeded` parking; Redis pub/sub wakeup with polling fallback; drain mode.
- **Run orchestration** (`services/runner.py`, 1,660 lines): `start_run` → pool resolution chain (deployment → workflow → environment → global env), dedicated-pool isolation enforcement (X4), per-day execution quotas at admission, package preflight, single-flight gate (PG row lock / SQLite asyncio.Lock), durable queue ledger, checkpointing after every completed node (1MiB cap, incremental serialization), checkpoint/NodeRun-based resume, replay seeds, redaction word-list, artifact-ref collection, error-handler workflow dispatch.
- **Executors** (`services/executors/`): Local (warm subprocess pool), Sandbox (disposable hardened containers keyed by org+env, Phase D), Remote (WS-connected runner agents / docker / kubernetes pools).
- **Events** (`services/events.py`): Redis pub/sub + history list (1h TTL, 10k replay cap) with in-process fallback; transport pinned once at startup; run broker + workflow broker; reaper loop.
- **Workers** (`app/worker_main.py`): standalone execution plane; requires DISPATCH_ROLE=worker + Redis; graceful SIGTERM drain.

### Engine (`packages/core/noodle/engine/`)

- `scheduler.py`: dependency-counting execution (not level barriers), deterministic topo order (insertion-order tie-breaks, canvas position never affects order), cycle detection, targeted runs (targets + ancestors), cache-seeded partial runs, worker-pool with semaphore-bounded concurrency, per-type concurrency semaphores, run deadline, status aggregation (error > waiting > success), 10MiB default per-node output cap.
- `node_exec.py`: input wiring (multi-connection ports, ai_tool fan-in), expression evaluation (`{{ }}` templates incl. nested containers), retries with exponential backoff + jitter, per-type default timeouts, process isolation for `code` nodes, per-node stdout/stderr capture via ContextVar proxy (concurrency-safe), tool-mode adapters, `$error` virtual output port (n8n-style continue-on-error), node hooks (log/webhook/call_workflow with SSRF guard), agent action-loop with approval pause (AgentApprovalRequired → RunStatus.waiting), JSON-schema typed I/O validation for code nodes (with `$ref` sanitization), streaming `emit_chunk`.
- `loops.py` / `metanodes.py` / `subworkflows.py`: loop regions (each/while/until) as single scheduling units; transparent metanode inlining; subworkflow resolver with cycle/depth invariants.

### MCP (strategic surface)

- **Server** (`routers/mcp.py` + `app/mcp/`): streamable-HTTP stateless JSON transport, protocol-version negotiation, Origin allowlist check, RFC 9728 protected-resource metadata + OAuth challenge, rate limit (120/min per org:actor), cursor pagination.
- **Tools** (`app/mcp/tools.py`, 2,781 lines): ~45 static tools (list/get workflows, node catalog, run/cancel/retry runs, run events, node runs, full graph CRUD incl. per-node patch/add/remove/edge ops, code-node create/update, versions/revisions/diff/rollback, schedules CRUD, error handler, approvals list/resolve, environments, credentials metadata) + **dynamic per-workflow tools** (`mcp_enabled` workflows exposed by name with custom JSON-schema). Optimistic concurrency (`expected_graph_revision`), audit logging on every mutation, GitHub-sync enqueue, live workflow events to the editor.
- **Client** (`services/mcp_client.py` + `routers/mcp_connections.py` + `noodle_nodes/mcp_tool.py`): external MCP connections (URL + bearer/header auth, org-KEK Fernet-encrypted secrets), tool discovery → node manifests (`mcp:{conn}:{tool}`), `mcp_tool` node executes via platform hook installed per-run.

### Node library

~400 node types across: builtin (triggers/webhook/http/condition/loop/merge/code/subworkflow), LLM (chat/agent/RAG/structured output/evals/training), AI-v2 (agents with tool ports, guardrails, subagents), data science (pandas/polars/duckdb datasets with DatasetRef auto-promotion, statistics, ML, charts, synthetic data, model monitoring/serving), files (CSV/Excel/Parquet/PDF/DOCX/S3/URL), integrations (Slack, Sheets, Notion, GitHub, Stripe, Airtable + integrations_v2 provider framework with per-provider operations/triggers: Outlook, Kafka, IMAP, Postgres LISTEN, filesystem watch…), storage (Redis/S3), browser automation, geospatial, security. Package requirements per node with preflight; `unsafe_nodes.classify` for approval gating.

### Frontend

React Flow (`@xyflow/react`) canvas with custom nodes/edges, loop frames, metanode drill-in with breadcrumbs, zustand store split into slices (graph/run/drill/clipboard/childWorkflow), React Query for data, Monaco editor for code nodes, NDV-style node details (5,000-line NodeDetails.tsx), run sidecar (timeline/diff/stats), AI builder modal + agentic build panel, chat panel, command palette, onboarding tour, diff views, GitHub sync UI, org/settings/security/runner-pool/environments admin pages, WS-driven live updates with ticket auth.

---

## 2. Feature Inventory

**Complete & mature**: visual editor; draft/publish versioning with diff + rollback + revision log; durable queue with retries/dead-letter/replay; checkpoint resume; loops/branches/metanodes/subworkflows; per-node retry/timeout/on-error; webhooks (test + production, path secrets, provider webhooks); schedules (cron/interval, TZ-aware, deployment-pinned versions); credentials (encrypted, org KEK, OAuth flows, credential tests); environments (per-env venvs, package preflight, wheel index); runner pools (docker/k8s/agent/SSH onboarding); sandbox isolation (Phase D); MCP server (tools/resources/prompts) + MCP client nodes; AI builder (draft/refine); AI agents with approval gates; artifacts (local/S3, retention); multi-tenancy (orgs, memberships, custom roles, quotas, metering, fairness); SSO/SAML/OIDC; audit log; licensing/entitlements; observability (Prometheus metrics, OTEL tracing, structured JSON logs, request IDs); ops surface (drain, dead-letter, pools, replicas); GitHub workflow sync; export to Python/Docker; import from Python.

**Partial**: AI test generation (evals nodes exist; no builder-integrated test-gen); visual graph diff exists but AI-change preview/approve flow is coarse (draft-level, not per-change); dynamic MCP tool nodes (manifests generated, but no discovery UI polish); Python debugging (no step-through/debugger attach); template gallery (workflowTemplates.ts exists, thin).

**Missing** (relative to the product thesis): MCP tool-call trace UI; MCP server allowlist policy; per-workflow requirements.txt; typed Python node SDK surfaced in UI (schemas exist in engine); notebook import/export; CLI; data preview on wires; run comparison; workflow health score.

---

## 3. Strengths

1. **The engine is genuinely good.** Deterministic scheduling, dependency counting, loop regions as units, per-type concurrency, run deadlines, output caps, contextvar-scoped log capture — this is more rigorous than n8n's engine and most OSS competitors.
2. **Security depth is unusual for this stage**: two-layer tenancy (ORM + RLS), fail-closed startup, SSRF defense with DNS-rebinding re-validation at connect time (`http_security.safe_request`), redirect credential-stripping, CSRF double-submit, PAT scoping, KEK-per-org credential encryption, sandbox policy enforcement.
3. **MCP server is a real differentiator** — 45 tools incl. incremental graph editing with optimistic concurrency and live canvas events. An external agent (Claude/Cursor) can build, inspect, run, debug, and publish workflows. Nobody else in this space has this depth.
4. **Ops maturity**: durable queue, org fairness, dead-letter + replay, drain mode, leader election, stuck-run detector, replica heartbeats, dispatcher health.
5. **Test culture**: ~100 backend test files, engine tests, frontend store/component tests, e2e Playwright config.

## 4. Weaknesses / Risks

1. **God files**: `mcp/tools.py` 2,781; `builtin.py` 2,464; `llm.py` 2,098; `ai_builder.py` 1,990; `NodeDetails.tsx` 5,000; `EditorPage.tsx` 2,091. Change-risk concentrated.
2. **Backpressure head-of-line blocking**: dispatch loop breaks the whole lease loop when a local run hits zero local budget — remote-pool entries behind it starve for that tick (and repeatedly, since ordering is stable). (`services/queue.py` dispatch loop)
3. **Event delivery robustness**: `TopicBroker.publish` fire-and-forgets `loop.create_task` without holding a reference (GC can drop tasks; interleaving can reorder Redis rpush history).
4. **Live-settings regression**: `_execute_run_impl` initializes `live=None` and never loads it (refactor artifact) → live overrides for artifact caps silently ignored (boot settings still apply).
5. **Checkpoint write per node completion** = 1 UPDATE+commit per node; chatty for 100+ node graphs (bounded but adds latency under load).
6. **MCP `httpx` calls bypass the `socket.create_connection` DNS-rebinding patch** (that guard wraps `requests` only); pre-flight `assert_public_http_url` still applies but the connect-time re-validation doesn't.
7. **Repo hygiene**: ~30 stray PNG/log/audit files at root; six historical audit reports; three venvs + per-env venvs checked into the working tree (gitignored but heavy); C: disk exhaustion on this dev box broke uv.
8. **Discoverability debt**: the feature surface (metanodes, tool-mode, datasets, MCP enablement, approval gates) far outruns onboarding/docs.

## 5. Biggest Opportunities

1. **Own the "AI agent builds your workflow" wedge** — the MCP server depth + visual editor is the moat; invest in trace/approval UX and marketing around Claude Code/Cursor integration.
2. **Python-native story**: exporter/importer + typed code nodes exist — surface them (per-workflow requirements, notebook round-trip, CLI, `pip install noodle-client`).
3. **MCP marketplace/discovery UI**: make external MCP servers first-class visual citizens (browse tools → drag as nodes).
4. **Template gallery + onboarding** as adoption levers; the engine is ready, the first-five-minutes experience isn't.
