# Architecture

Noodle is a self-hostable, n8n-style workflow automation platform where every
node is pure Python.

## Components

- **`apps/web`** — React + React Flow editor (Vite). Drag-and-drop canvas,
  node palette built from manifests, inspector with per-parameter forms,
  Environments / Credentials / Activity pages, and live test-mode driven by a
  WebSocket connection to the API.
- **`apps/api`** — FastAPI server. Owns the database schema, the node registry
  endpoint, workflow CRUD with versioning, environment management, runs,
  webhook ingress, credentials vault, audit log, auth (local), Prometheus
  metrics, and a WebSocket channel for live run events.
- **`apps/worker`** — Celery worker skeleton. Production deployments would
  move env-runner execution and the trigger scheduler here; the current dev
  setup runs them in-process inside the API for zero-infra demos.
- **`packages/core`** — the engine, node SDK (`@node` decorator), and shared
  Pydantic models. The same engine runs inside the API and inside exported
  scripts, so behaviour is identical everywhere.
- **`packages/nodes`** — the built-in node library (core transform/data nodes
  plus first-pass official integrations for Slack, Discord, SMTP, Google
  Sheets, Notion, GitHub, Postgres, MySQL, S3, OpenAI, Anthropic, Stripe, and
  Airtable).
- **`packages/runtime`** — the in-environment runner package. A workflow's
  env-runner subprocess loads this to execute graphs inside the venv's
  interpreter. (Currently exists as a hook point; execution today is
  in-process in the API.)
- **`packages/exporter`** — workflow → `.py` script and Docker bundle codegen.

## Execution model

Following n8n: a node has one or more named **input ports** (triggers have
none), one or more named **output ports** (most have one named `main`;
branching nodes like `if` have `true`/`false`), and a set of **config
parameters** edited in the inspector. Edges connect an upstream output to a
downstream input — they never wire individual parameters.

The engine (`noodle.engine.execute`) walks the graph in topological order,
resolving each node's wired inputs from upstream outputs and its config
parameters from the inspector. It supports **branching** (consumers of an
untaken branch are skipped), **partial execution** (`cache` supplies
precomputed outputs), **targeted runs** (`targets` restricts execution to a
subset and its ancestors), and **live events** (`on_event` fires per-node
start/finish).

## Triggers

- **Manual** — the editor's Run button calls the run dispatch endpoint.
- **Webhook** — `/webhook/{path}` looks up active workflows whose graph starts
  with a `webhook_trigger` node matching that path, seeds the request as the
  trigger node's output, and dispatches a run. `/webhook-test/{path}` captures
  requests for the editor's test panel without triggering execution.
- **Schedule** — an in-process loop in the API checks active schedule
  triggers on a 30-second tick and fires due workflows. Celery Beat is the
  production-grade target.

## Storage

PostgreSQL is the production database; the migrations apply to SQLite too,
which is what the local dev stack uses. The schema has been added one
milestone at a time:

| Migration | Tables |
|-----------|--------|
| `0001_baseline` | (empty) |
| `0002_workflows` | `workflows`, `workflow_versions` |
| `0003_environments` | `environments`, `workflows.environment_id` |
| `0004_runs` | `runs`, `node_runs` |
| `0005_enterprise` | `users`, `credentials`, `audit_events` |

## Security

- Credentials are encrypted at rest with Fernet (key derived from
  `secret_key`). The API never returns plaintext values — only the field
  names.
- Passwords use PBKDF2-HMAC-SHA256 (200k rounds, per-user salt).
- Session tokens are HMAC-signed (`sub` + `exp`). No JWT library required.
- Local-process execution is intentional — workflows run in the operator's
  trust boundary, matching n8n's self-hosted model. Multi-tenant SaaS would
  require container or gVisor isolation on top.
