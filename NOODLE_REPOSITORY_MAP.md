# Noodle Repository Map

> Independent map produced 2026-06-16 by direct inspection (not derived from
> prior audit docs). Branch `feat/arch-program-phase5`.

## Stack at a glance

- **Backend:** FastAPI, SQLAlchemy (async), Alembic, Pydantic. Python ≥ 3.12.
- **Frontend:** React 18 + TypeScript + Vite 5; Zustand (editor store), TanStack
  Query (server cache), @xyflow/react 12 (canvas), react-router 6, DOMPurify, marked,
  plotly basic, lucide/phosphor/simple-icons.
- **Datastores:** Postgres (prod) / SQLite+aiosqlite (dev/test); Redis (event
  broker + optional queue backend); MinIO/S3 (artifacts).
- **Package mgmt:** uv workspace (`pyproject.toml`, `uv.lock`); npm for web.
- **Queue:** custom durable queue over Postgres `SELECT … FOR UPDATE SKIP LOCKED`
  (`QUEUE_BACKEND=db`) or Redis (`QUEUE_BACKEND=redis`). No Celery/RQ.
- **Execution isolation:** subprocess runner + per-env venvs; optional
  container-per-run sandbox (runc / gVisor / Kata); remote runner pools
  (agent over WebSocket, docker, k8s).
- **Lint/format/types:** ruff (E,F,I,UP,B; line 100); tsc; vitest; playwright; pytest.

## Top-level layout

```
apps/
  api/      FastAPI service + worker entrypoint + alembic + tests + tools
  web/      React/Vite frontend (src/, e2e/)
  worker/   (appears empty of .py — verify; worker actually = apps/api/app/worker_main.py)
packages/
  core/     noodle/ — execution engine, models, expr sandbox, SDK, serialization
  nodes/    noodle_nodes/ — built-in node library (86 files) + integrations_v2
  runner/   noodle_runner_agent/ — remote runner agent (WebSocket)
  runtime/  noodle_runtime/ — shared runtime helpers
  exporter/ noodle_exporter/ — code-first workflow export
deploy/     Dockerfile.python, docker-compose.yml, helm/
docs/       architecture, deployment, node/integration dev, prior audits, plans
scripts/    operational scripts
brand/      homepage + icons
.github/workflows/ci.yml
```

## Backend entry points

- **API:** `apps/api/app/main.py` — `FastAPI(lifespan=…)`. Lifespan starts many
  background loops: `run_queue_dispatch_loop`, `broker_reaper_loop`,
  `scheduler_loop` (triggers), `retention_loop`, `runner_heartbeat_loop`,
  `cloud_idle_terminate_loop`, `idle_reaper_loop`, expr preview worker. CORS via
  `CORS_ORIGINS`. Mounts 24 routers.
- **Worker:** `apps/api/app/worker_main.py` — `python -m app.worker_main`.
  HTTP-less execution plane: dispatch loop + warm runtime pool + idle reaper +
  sandbox pool. SIGTERM/SIGINT graceful drain.
- **Config:** `apps/api/app/config.py` (`settings`). DB: `app/db.py`. Redis:
  `app/redis_client.py`. ORM: `app/models.py`. Pydantic schemas: `app/schemas.py`.
  Auth/crypto helpers: `app/security.py`, `services/crypto.py`. Tenancy:
  `app/tenancy.py`. Tracing: `app/tracing.py`.

### Routers (`apps/api/app/routers`, 24)

`auth`, `orgs`, `workflows`, `runs`, `nodes`, `credentials`, `environments`,
`deployments`, `runner_pools`, `webhooks`, `provider_webhooks`, `chat`,
`chat_public`, `code_modules`, `artifacts`, `export`, `expressions`, `mcp`,
`audit`, `ops`, `health`, `internal`, `pinned`, `system_settings`.

### Service layer (`apps/api/app/services`, ~50 modules)

Execution: `runner.py`, `queue.py`, `run_persistence.py`, `run_resume.py`,
`run_alerts.py`, `executors/{base,local,remote,sandbox}.py`,
`runtime_pool.py`, `remote_dispatch.py`, `dispatcher_health.py`,
`leader_election.py`. Isolation/sandbox: `isolation.py`, `unsafe_nodes.py`,
`sandbox_policy.py`, `sandbox_pool.py`, `container_runtime.py`,
`process_isolation`(core), `venv.py`, `backends/{venv,conda,pixi,tools}.py`,
`providers/{agent,docker,k8s}.py`, `wheel_index.py`, `package_preflight.py`,
`ssh_onboard.py`. Tenancy/limits: `org_keys.py`, `org_limits.py`, `metering.py`,
`licensing.py`, `live_settings.py`, `system_settings`. Secrets/creds:
`crypto.py`, `credentials.py`, `credential_types.py`, `credential_tests.py`,
`oauth.py`. Data: `datasets_query.py`, `artifacts.py`, `artifact_backends.py`,
`s3_artifact_backend.py`, `retention.py`. Misc: `events.py`, `triggers.py`,
`provider_triggers.py`, `subworkflows.py`, `graph_utils.py`, `redaction.py`,
`expr_preview.py`, `ai_builder.py`, `chat_service.py`, `starter_graph.py`,
`ws_ticket.py`, `audit.py`.

### Execution engine (`packages/core/noodle`)

`engine/scheduler.py` (DAG orchestration), `engine/node_exec.py` (node run),
`engine/validation.py` (graph validation), `engine/loops.py`, `engine/agent.py`
(AI agent loop), `engine/datasets.py`, `engine/metanodes.py`,
`engine/subworkflows.py`, `engine/types.py`. Plus `models.py`, `sdk.py`
(node-author API), `expr.py` + `expr_preview_worker.py` (expression sandbox),
`process_isolation.py`, `serialization.py`, `context.py`, `artifacts.py`,
`datasets.py`, `ai_runtime.py`, `node_tool.py`, `packages.py`.

## Frontend modules (`apps/web/src`)

- **Pages:** `WorkflowsPage`, `EditorPage`, `ExecutionsPage`, `ActivityPage`,
  `CredentialsPage`, `EnvironmentsPage`, `DeploymentsPage`, `RunnerPoolsPage`,
  `OrganizationPage`, `SecurityPage`, `SettingsPage`, `CodeLibraryPage`,
  `LoginPage`, `ChatPublicPage`.
- **Editor/canvas (`src/editor`):** `Canvas` (xyflow), `NodeCard`, `NodeDetails`/
  `NodeDetailModal`, `Inspector`, `NodePalette`, `NoodleEdge`, `LoopFrame`,
  `MapGroupNode`, `NodeGroup`, `CommandPalette`, `DataPanel`, `PortDataViewer`,
  `ChatPanel`, charts (`ChartView`/`PlotlyChartView`/`ReportView`),
  `WorkflowHistory`, `WorkflowSettingsModal`, `SaveIndicator`, `PublishPill`,
  `fields/`, `node-details/`, `store/` (Zustand).
- **Infra:** `api.ts` (client), `queries/` (TanStack), `hooks/`, `authBootstrap.ts`,
  `ErrorBoundary`, `ToastProvider`, `ConfirmProvider`.

## Build & run

- Stack (Docker): `docker compose -f deploy/docker-compose.yml up --build -d`
  (postgres, redis, minio, api, worker).
- Dev infra only: `docker compose -f deploy/docker-compose.yml up postgres redis minio`.
- DB: `uv run alembic upgrade head` (from `apps/api`).
- API: `uv run uvicorn app.main:app --reload --port 8000`.
- Worker: `python -m app.worker_main` (`DISPATCH_ROLE=worker`).
- Web: `npm run dev` (Vite, port 5173).
- Tests: `uv run pytest …`; `npm run typecheck && npm run build`; `npm test`;
  `npm run test:e2e`.

## Suspicious / cleanup candidates (to verify, not yet acted on)

- **Build/log noise committed-or-tracked at root:** `.codex-web-dev.*.log`,
  `.web-uiux-dev-5174.log`, `.agent-node-check*.log/.db`, `_pytest_full.log`,
  `.api.*.log` — should be gitignored/removed.
- **Screenshot PNGs at repo root:** `bc-combined.png`, `editor-runner-*.png`,
  `env-modal-polish.png`, `ui-*.png` — move to `docs/` or remove.
- **Three venvs at root:** `.venv`, `.venv-runner`, `.venv-test` (ensure gitignored).
- **`apps/api/envs/<id>/` committed?** Per-env built venvs under version control
  would be a serious repo-bloat/leak risk — verify `.gitignore` covers them.
- **`apps/worker/`** has no Python — confirm it's dead and remove.
- **`dist/`** at root — verify it's a build artifact and gitignored.
- **Untracked `LICENSE`, `LICENSE.enterprise`, `TRADEMARK.md`** — newly added,
  not yet committed.

## Initial architectural observations (independent, pre-deep-dive)

1. **Clean engine/api separation.** Execution engine lives in `packages/core`
   independent of FastAPI — good for testability and for the code-first exporter.
2. **Worker reuses the API app package** rather than a standalone service. Pragmatic,
   but couples worker memory/deps to the whole API import graph — review startup cost.
3. **Many lifespan background loops in one process** (dispatch, scheduler,
   retention, reapers, heartbeats). Role flags (`DISPATCH_ROLE`, `SCHEDULER_ROLE`,
   `ENABLE_INPROCESS_SCHEDULER`) gate them — verify single-leader guarantees so two
   API replicas don't double-run schedulers/retention.
4. **Security-sensitive surface is large:** code execution, expression eval,
   webhooks/SSRF, credentials/secrets, multi-tenant isolation, remote runners.
   These are the audit's priority.
5. **51 migrations on a 0.0.1 project** — fast schema churn; check for
   destructive/irreversible migrations and squash strategy before OSS release.
