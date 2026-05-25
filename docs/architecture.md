# Architecture

Noodle is a self-hostable, n8n-style workflow automation platform where every
node is pure Python.

## Components

- **`apps/web`** — React + React Flow editor (Vite). Drag-and-drop canvas,
  node palette built from manifests, inspector with per-parameter forms and an
  n8n-style ƒx (fixed/expression) toggle on string fields, Environments /
  Credentials / Activity / Executions / Code Library pages, and live
  test-mode driven by a WebSocket connection to the API.
- **`apps/api`** — FastAPI server. Owns the database schema, the node registry
  endpoint, workflow CRUD with versioning, environment management, runs,
  webhook ingress, credentials vault, audit log, auth (local), Prometheus
  metrics, the per-run artifacts surface, deployments (cron/interval +
  default params), code modules (upload-to-nodes), retention prune, and a
  WebSocket channel for live run events.
- **`apps/worker`** — Celery worker. Optional scale-out scheduler (Beat) +
  off-API workflow dispatch via HTTP. Gated by `enable_inprocess_scheduler`
  so the in-process loop and Beat never double-fire.
- **`packages/core`** — the engine, node SDK (`@node` decorator, AST-only
  module-function discovery), shared Pydantic models, typed-value
  serialization, and the artifacts runtime helpers. The same engine runs
  inside the API, inside env subprocesses, and inside exported scripts.
- **`packages/nodes`** — the built-in node library (core transform/data nodes
  plus first-pass official integrations for Slack, Discord, SMTP, Google
  Sheets, Notion, GitHub, Postgres, MySQL, S3, OpenAI, Anthropic, Stripe, and
  Airtable).
- **`packages/runtime`** — the in-environment runner package. A workflow's
  env subprocess (`python -u -m noodle_runtime`) keeps a warm process per env
  and executes graphs inside the env's interpreter. Pool-managed
  (`runtime_pool`) with configurable per-env warm-process count and a global
  cap.
- **`packages/exporter`** — workflow → `.py` script and Docker bundle codegen.

## Execution model

Following n8n: a node has one or more named **input ports** (triggers have
none), one or more named **output ports** (most have one named `main`;
branching nodes like `if` have `true`/`false`), and a set of **config
parameters** edited in the inspector. Edges connect an upstream output to a
downstream input — they never wire individual parameters.

User-uploaded code modules follow the same model with one twist: every
top-level `def` becomes a node with a single virtual `input` port (the
upstream data envelope, exposed as `{{ $json }}` in expressions) and every
function parameter rendered in the inspector. The engine filters kwargs to
what the function actually accepts before calling, so the virtual `input`
port is not passed to functions that don't declare it.

The engine (`noodle.engine.execute`) walks the graph in topological order,
resolving each node's wired inputs from upstream outputs and its config
parameters from the inspector. It supports:

- **branching** — consumers of an untaken branch are skipped;
- **partial execution** — `cache` supplies precomputed outputs;
- **targeted runs** — `targets` restricts execution to a subset + ancestors;
- **per-node retry** with `retry_wait_seconds`, `retry_backoff`, jitter;
- **per-node timeout** (`timeout_seconds`) — async or sync-via-`to_thread`;
- **per-node log capture** — context-local stdout/stderr buffer that does
  NOT mutate the process-global `sys.stdout`, so concurrent runs and
  sub-workflows on the same host don't cross-capture;
- **typed output serialization** — DataFrame, datetime, Decimal, tuple, set,
  bytes cross the WebSocket/storage boundary as `{__noodle_typed__: true,
  ...}` envelopes that the UI renders as native types;
- **artifacts** — Code/user-module nodes call `artifacts.write_*` to write
  bytes outside the DB; refs flow through node outputs as small JSON marker
  dicts and the UI renders them as artifact cards;
- **live events** — `on_event` fires per-node start/finish with timing.

## Triggers

- **Manual** — the editor's Run button calls the run dispatch endpoint.
- **Webhook** — `/webhook/{path}` looks up active workflows whose graph
  starts with a `webhook_trigger` node matching that path, seeds the request
  as the trigger node's output, and dispatches a run. `/webhook-test/{path}`
  captures requests for the editor's test panel without triggering execution.
- **Schedule** — DB-backed in-process loop in the API checks active
  `schedule_trigger` nodes and **active Deployments** on a configurable tick
  and fires due workflows via croniter (real cron). `last_fired` lives in
  the DB so restarts don't replay or skip fires.
- **Deployments** — first-class entity (workflow + schedule + default
  parameters + active flag + optional env override). When a workflow has
  active deployments they are the canonical schedule source; any in-graph
  `schedule_trigger` is ignored to avoid double-fire.

## Parameters, expressions, and re-run/retry

- `POST /workflows/{id}/run` accepts `parameters: dict | None`. Parameters
  are seeded into the first node whose type is in `TRIGGER_TYPES`
  (manual/webhook/schedule) via `cache={nid: {"main": params}}`; if none
  exists, all root nodes are seeded.
- Inspector string fields support `{{ $json.field }}` and
  `{{ $node["id"].main.field }}` expressions. The ƒx toggle in the inspector
  flips a field between fixed value and expression mode with explicit visual
  state (amber border + solid amber button when expression).
- `POST /runs/{run_id}/rerun` re-dispatches with the same workflow/version/params.
- `POST /runs/{run_id}/retry` rebuilds `cache` from successful upstream
  `NodeRun.output` and sets `targets` to the failed node + descendants.

## Storage

PostgreSQL is the production database; the migrations apply to SQLite too,
which is what the local dev stack uses.

| Migration | Purpose |
|-----------|---------|
| `0001_baseline` | initial |
| `0002_workflows` | `workflows`, `workflow_versions` |
| `0003_environments` | `environments` + `workflows.environment_id` |
| `0004_runs` | `runs`, `node_runs` |
| `0005_enterprise` | `users`, `credentials`, `audit_events` |
| `0006_pinned` | `pinned_data` |
| `0007_node_run_observability` | `node_runs.logs/started_at/finished_at/duration_ms` |
| `0008_schedule_state` | `schedule_state` (DB-backed cron `last_fired`) |
| `0009_node_run_debug` | `node_runs.debug` (per-node variable previews) |
| `0010_deployments` | `deployments` (workflow + schedule + default params) |
| `0011_code_modules` | `code_modules` (uploaded Python, scoped global/env/workflow) |
| `0012_artifacts` | `artifacts` (per-run file metadata; bytes on disk) |

## Retention

Runs and their outputs persist forever by default. To keep SQLite/Postgres
bounded:

- `run_retention_days` — age cap; expired runs (and their `node_runs` +
  `artifacts` rows + artifact files) are dropped on a configurable tick.
- `run_retention_max_per_workflow` — keep only the N most recent per
  workflow.
- `max_output_bytes` — per-port cap; oversize outputs are replaced with
  `{_truncated: true, size, preview}` before persisting.
- `max_artifact_bytes` / `max_artifacts_per_run` — per-write and per-run
  limits on artifact storage.

The retention loop is gated on `enable_inprocess_scheduler` so a multi-API
deployment can choose one owner.

## Security

- Credentials are encrypted at rest with Fernet (key derived from
  `secret_key`). The API never returns plaintext — only the field names.
- Passwords use PBKDF2-HMAC-SHA256 (200k rounds, per-user salt).
- Session tokens are HMAC-signed (`sub` + `exp`).
- Code-module preview/manifest discovery is **AST-only** — uploaded Python
  is never executed in the API process. Runtime execution only happens
  inside the workflow's env subprocess, the same trust boundary as the
  Code node.
- Artifact storage keys are server-generated; file names are sanitized and
  storage paths are `relative_to(base_dir)`-checked to block traversal.
- Local-process execution is intentional — workflows run in the operator's
  trust boundary, matching n8n's self-hosted model. Multi-tenant SaaS would
  require container or gVisor isolation on top.
