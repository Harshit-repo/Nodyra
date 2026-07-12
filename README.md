# Nodyra

> **Nodyra** was previously developed under the working name *Noodle*.

Nodyra is a self-hostable, Python-native workflow automation platform for
teams that want automation, data movement, and operational runbooks to live
close to their Python stack.

Every node is a plain Python function registered through the Nodyra SDK. Users
build workflows on a React Flow canvas, run them in isolated Python
environments, inspect every node input/output, persist artifacts outside the
database, and publish versioned workflow releases for production execution.

## Let Claude build your workflows

Nodyra has a first-class MCP server. Connect Claude Code in one line:

```bash
claude mcp add --transport http nodyra https://your-instance/mcp \
  --header "Authorization: Bearer <api-token>"
```

Then ask for the workflow you want and watch it appear on the canvas: editable,
testable, and deployable. See the [full guide](docs/mcp-quickstart.md).

## Security model

Nodyra supports three execution postures. Pick one per deployment:

1. **Trusted single-tenant (default):** workflow authors are trusted; code
   nodes run in warm, per-environment worker subprocesses on the host at
   native speed. Correct for a team running its own instance.
2. **Sandboxed:** set `EXECUTION_SANDBOX=auto|required` and each run executes
   in a disposable hardened container: capabilities dropped, read-only root
   filesystem, CPU/memory/pids limits, a dedicated egress-isolated network,
   and gVisor/Kata when installed. Recommended whenever workflows execute
   AI-generated or otherwise untrusted code. One-command enable:
   `docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.sandbox.yml up`.
3. **Multi-tenant:** `MULTI_TENANCY_ENABLED=true` **requires** the sandbox
   (`EXECUTION_SANDBOX=required`); the server refuses to boot otherwise.
   Tenant isolation combines per-org row-level security, org-scoped
   encryption keys, per-org quotas/fairness, and per-(org, environment)
   container pools.

Startup is fail-closed: unsafe combinations (default `SECRET_KEY` with auth
enabled, missing internal token in a split topology, a requested-but-bypassed
sandbox) abort boot rather than degrade silently. See [SECURITY.md](SECURITY.md).

## Why Nodyra

Nodyra is designed for teams that need more than point-and-click integrations:

- Python-native execution: built-in, uploaded, and custom nodes run as Python,
  not JavaScript wrappers around Python work.
- Environment-aware runtime: workflows run inside per-environment virtualenvs
  so packages like pandas, boto3, psycopg, or internal libraries resolve in the
  same interpreter that executes the workflow.
- Production control plane: RBAC, credentials, audit logs, workflow releases,
  deployments, scheduled runs, webhook auth, retry-from-failed-node, and
  failure workflows.
- Enterprise data handling: typed output serialization, durable artifacts,
  output caps, retention rules, redaction, and per-node logs/debug data.
- Self-hostable architecture: FastAPI, PostgreSQL, Redis, React/Vite, a
  durable run queue with standalone dispatch workers, and optional
  Kubernetes packaging.

## Build workflows with an AI agent (MCP)

Nodyra is an MCP server. Claude, Cursor, or any MCP client can create, edit,
validate, run, and publish workflows through 61 tools. See
[docs/mcp-quickstart.md](docs/mcp-quickstart.md) for a one-paste setup.

## CLI & Python SDK

`pip install nodyra-client` gives you the `nodyra` CLI and a typed Python SDK:
login, list workflows, import/export Python workflow modules, and run workflows
from CI with `--watch` outcome exit codes. See
[packages/client/README.md](packages/client/README.md).

## Current Status

Nodyra is an active product codebase. The local and Docker stacks are usable,
and the platform includes a broad v1 enterprise surface: a durable run queue
with leases, dead-letter, and replay; scheduler leader election; runner pools
with heartbeats and remote/SSH onboarding; pluggable local/S3 artifact storage;
credential test-on-save; and an unsafe-node activation policy. See
[docs/status-matrix.md](docs/status-matrix.md) for the per-component shipped /
beta / scaffolded / planned breakdown. Known gaps include a first-class UI
operations dashboard and a first-class admin restart control.

## Key Capabilities

### Workflow Authoring

- Drag-and-drop React Flow editor.
- Node palette generated from Python node manifests.
- Inspector with typed parameters, credential pickers, expression fields, and
  fixed/expression mode for string values.
- Input/output data viewer for every node.
- Per-node logs, errors, timing, debug variables, and output previews.
- Pinned data and reusable upstream output cache for targeted node runs.
- AI workflow draft builder that creates editable graphs instead of hidden
  agent execution.
- Starter graph generation from uploaded Python modules.

### Execution

- Topological graph execution with branching and targeted runs.
- Manual, webhook, schedule, deployment, and error-workflow triggers.
- Per-node retry, retry backoff, jitter, timeout, and "always output data"
  support.
- Warm subprocess runtime per environment.
- Elastic per-environment runner pools with min/max worker sizing.
- Global top-level run concurrency cap.
- Sub-workflows with production-safe draft/published graph selection.
- Retry from failed node using cached successful upstream outputs.

### Enterprise Controls

- Local auth with first-user owner setup.
- RBAC roles: `viewer`, `editor`, `admin`, `owner`.
- Owner/admin user management and invitation flow.
- Scoped credentials: global, workflow, environment, and runner pool.
- Encrypted credentials at rest.
- Credential redaction in logs, outputs, run events, and API responses.
- Read-only credential test connections for supported integrations.
- Audit log surface.
- Workflow draft vs published versions.
- GitOps two-way GitHub sync for workflow definitions
  ([docs/gitops.md](docs/gitops.md)).
- Deployments pinned to workflow versions.
- Error workflows and failure payloads.
- Workspace settings for retention, output caps, artifacts, timezones, and
  runtime limits.

### Data And Artifacts

- Typed output serialization for DataFrame, datetime, date, time, Decimal,
  tuple, set, frozenset, bytes, and bytearray.
- DatasetRef table handles keep large tabular data artifact-backed as Parquet
  while passing schema, row count, and preview metadata through the workflow.
- DatasetRef-native nodes include Records To Dataset, Dataset Preview,
  Dataset Filter, Dataset Select Columns, Dataset Limit, DuckDB SQL,
  Dataset To Records, and CSV Write.
- No pickle-based restoration.
- Unknown Python objects are preview-only and not automatically rehydrated.
- Durable per-run artifact metadata in the database.
- Artifact bytes stored outside the database.
- Retention cleanup cascades run rows and artifact files.
- Output and artifact size caps.

### Built-In Nodes And Integrations

The built-in node library includes core logic, data, transform, system, AI,
SaaS, communication, storage, and cloud/devops nodes.

Representative integrations include:

- Slack
- Discord
- SMTP / Gmail-style email
- Google Sheets
- Notion
- GitHub
- Postgres
- MySQL
- S3 / AWS-style storage
- OpenAI
- Anthropic
- Stripe
- Airtable
- HTTP and webhook nodes
- Execute Command
- CSV, XML, HTML, gzip, and transform utilities

## Architecture

```text
Browser UI
   |
   | HTTP / WebSocket
   v
FastAPI API
   |
   | SQLAlchemy / Alembic
   v
PostgreSQL or SQLite

FastAPI API
   |
   | run dispatch
   v
RuntimePool
   |
   | per-environment subprocess
   v
python -m nodyra_runtime
   |
   | executes WorkflowGraph
   v
Nodyra engine + node registry

Redis + the durable run queue power optional worker scale-out
(``DISPATCH_ROLE=worker`` processes started via ``python -m app.worker_main``).
Artifacts are stored outside the database through the artifact service.
```

### Repository Layout

```text
apps/
  api/        FastAPI server, Alembic migrations, auth, runs, credentials
              (also the standalone dispatch worker: python -m app.worker_main)
  web/        React 18 + Vite + React Flow application

packages/
  core/       execution engine, SDK, models, typed serialization, artifacts
  nodes/      built-in node library and official integration nodes
  exporter/   workflow to Python script / Docker bundle
  runtime/    per-environment subprocess runtime

deploy/
  docker-compose.yml
  Dockerfiles
  helm/nodyra/

docs/
  architecture.md
  datasetref.md

plan.md       milestone and slice history
HANDOFF.md    compact engineering handoff context
```

### Runtime Model

Nodyra separates the control plane from the Python execution plane.

- The API validates requests, stores workflows, resolves credentials, applies
  redaction, persists runs, and streams events.
- The runtime pool owns warm Python subprocesses per environment.
- Each subprocess runs inside the selected environment's interpreter.
- Uploaded code modules are discovered statically with AST for previews and
  palette generation.
- Uploaded code is executed only during workflow execution, inside the runtime
  trust boundary.

This model keeps editor preview safe while still allowing real Python code to
run during trusted workflow execution.

## Security Model

### Authentication And Authorization

- `AUTH_REQUIRED=true` is used in Docker by default.
- The first registered account becomes `owner`.
- Registration closes after the first user unless explicitly enabled.
- Owners/admins can create and manage users.
- Role permissions are enforced server-side.
- Sensitive user-management permissions require an authenticated actor even if
  local auth is disabled for development.

### Credentials And Secrets

- Credentials are encrypted at rest with Fernet-derived encryption.
- The API never returns decrypted credential values.
- Workflows store credential references, not inline plaintext secrets.
- Credential scopes limit visibility:
  - global
  - workflow
  - environment
  - runner pool
- Multi-field credentials resolve to a single dictionary at runtime.
- Redaction is applied to logs, node outputs, events, and API responses.

### Code Execution Boundary

Nodyra intentionally runs workflow code in the operator's trust boundary. This
is appropriate for self-hosted automation, internal data operations, and trusted
workflow authors.

Important boundaries:

- Preview and manifest generation are AST-only.
- Runtime execution happens in the workflow environment subprocess.
- The Code node and uploaded modules can execute arbitrary Python.
- Multi-tenant SaaS isolation would require additional sandboxing such as
  containers, gVisor, Firecracker, or remote runner isolation.

### Recommended Production Controls

- Use a strong `NODYRA_SECRET_KEY`.
- Use a strong `INTERNAL_API_TOKEN`.
- Keep `AUTH_REQUIRED=true`.
- Keep public registration disabled.
- Terminate TLS at a reverse proxy or ingress.
- Restrict API, Redis, Postgres, and MinIO network access.
- Back up Postgres and artifact volumes together.
- Treat workflow authors as trusted automation engineers unless stronger
  runtime sandboxing is added.

## Deployment

### Local Docker Stack

Create `deploy/.env` with production-style local secrets:

```bash
INTERNAL_API_TOKEN=replace-with-a-long-random-token
NODYRA_SECRET_KEY=replace-with-a-long-random-secret
AUTH_REQUIRED=true
AUTH_ALLOW_REGISTRATION=false
```

Start the stack:

```bash
docker compose -f deploy/docker-compose.yml up --build -d
```

Open:

```text
http://localhost:5173
```

Health checks:

```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```

The compose stack includes:

- PostgreSQL
- Redis
- MinIO
- FastAPI API (control plane, `DISPATCH_ROLE=disabled`, leader-elected scheduler)
- Dispatch worker (`DISPATCH_ROLE=worker`, executes runs)
- Vite web app

### Local Developer Stack

Requirements:

- Python 3.12
- uv
- Node.js 20+
- Docker Desktop, when using local Postgres/Redis/MinIO

Install Python dependencies:

```bash
uv sync --all-packages
```

Start infrastructure:

```bash
docker compose -f deploy/docker-compose.yml up postgres redis minio
```

Run migrations:

```bash
cd apps/api
uv run alembic upgrade head
```

Start the API:

```bash
cd apps/api
uv run uvicorn app.main:app --reload --port 8000
```

Start the web app:

```bash
cd apps/web
npm install
npm run dev
```

### Kubernetes

A Helm chart skeleton lives in `deploy/helm/nodyra`. Production Kubernetes
deployments should provide managed services or hardened in-cluster services for:

- PostgreSQL
- Redis
- object/artifact storage
- ingress and TLS
- secret management
- persistent runtime environment storage, if warm environments are retained

## Configuration

Core API settings are environment variables loaded by `apps/api/app/config.py`.

| Variable | Purpose | Typical production value |
| --- | --- | --- |
| `DATABASE_URL` | SQLAlchemy database URL | managed PostgreSQL |
| `REDIS_URL` | Redis URL for broker/cache use | managed Redis |
| `QUEUE_BACKEND` | `redis` for multi-process topologies | `redis` |
| `DISPATCH_ROLE` | `inline` / `worker` / `disabled` execution role | `disabled` on API, `worker` on workers |
| `WORKER_LABELS` | Optional worker capability labels such as `gpu=a100,mem=high` | set on specialized workers |
| `CORS_ORIGINS` | Allowed web origins | public web URL |
| `PUBLIC_API_URL` | Public API origin for provider webhook callbacks | public HTTPS API URL |
| `AUTH_REQUIRED` | Require login | `true` |
| `AUTH_ALLOW_REGISTRATION` | Allow open registration | `false` |
| `AUTH_REGISTRATION_ROLE` | Default role when registration is open | `viewer` |
| `NODYRA_SECRET_KEY` / `SECRET_KEY` | token and credential encryption secret | strong secret |
| `INTERNAL_API_TOKEN` | API to worker shared secret | strong secret |
| `USE_SUBPROCESS_RUNNER` | run workflows in env subprocesses | `true` |
| `ENVS_DIR` | virtualenv storage path | persistent volume |
| `ARTIFACTS_DIR` | artifact file storage path | persistent volume |
| `ENABLE_INPROCESS_SCHEDULER` | API-owned scheduler loop | one scheduler owner only |
| `APP_TIMEZONE` | default schedule timezone | IANA timezone |
| `MAX_CONCURRENT_RUNS` | global top-level concurrency cap | sized to host capacity |
| `RUNNER_IDLE_SECONDS` | warm worker idle reap delay | workload dependent |
| `MAX_OUTPUT_BYTES` | persisted per-node output cap | workload dependent |

Several settings can also be managed through the admin Settings page and are
read through the live settings service.

## Operations

### Run History And Debugging

Operators can inspect:

- all runs
- per-workflow run history
- run status and duration
- trigger type
- per-node status
- per-node input/output
- logs and captured errors
- pinned outputs
- artifacts

Retry-from-failed-node rebuilds a cache from successful upstream nodes and
reruns the failed node and descendants.

### Versioned Releases

Workflows have editable drafts and published versions.

- Editor saves update the draft graph.
- Publish creates a workflow version.
- Deployments pin to a version.
- Production paths execute published versions.
- Manual editor runs can execute drafts for iteration.

This prevents unpublished edits from changing production behavior until a user
explicitly publishes and updates deployments.

### Scheduling And Webhooks

- Schedule nodes use cron-style scheduling.
- Workflow deployments can own production schedules.
- `SCHEDULER_ROLE=leader` gives multi-replica deployments a single scheduler owner.
- Webhook nodes support test-mode listening in the editor.
- Production webhook routes return `401` when a path matches but auth fails.
- Webhook auth modes include none, basic, header, and query.

### Retention And Storage

Use retention settings to keep storage bounded:

- `run_retention_days`
- `run_retention_max_per_workflow`
- `max_output_bytes`
- `max_artifact_bytes`
- `max_artifacts_per_run`

Back up the database and artifact storage together. A run row can reference
artifact metadata and files outside the database.

### Observability

Available surfaces:

- API health endpoints: `/health/live`, `/health/ready`
- metrics endpoint: `/metrics`
- audit log
- run events over WebSocket
- per-node logs and timings
- worker and API container logs

Recommended production additions:

- centralized log collection
- uptime checks against `/health/ready`
- Postgres backup monitoring
- Redis memory monitoring
- artifact volume usage alerts
- run failure alert workflows

## Development And Testing

Run backend, runtime, and core tests:

```bash
uv run pytest packages/core/tests apps/api/tests packages/runtime/tests
```

Run node package tests:

```bash
uv run pytest packages/nodes/tests
```

Run Python lint checks:

```bash
uv run ruff check packages/nodes apps/api/app
```

Run frontend checks:

```bash
cd apps/web
npm run typecheck
npm run build
```

## API And Extension Points

### Python Node SDK

Nodes are plain Python functions decorated with `@node`.

```python
from nodyra.sdk import node


@node(name="Normalize Customer", id="normalize_customer", category="Data")
def normalize_customer(input: dict, lowercase_email: bool = True) -> dict:
    customer = dict(input)
    if lowercase_email and customer.get("email"):
        customer["email"] = customer["email"].lower()
    return customer
```

### Uploaded Code Modules

Uploaded `.py` modules are discovered with AST:

- top-level `def` and `async def` become candidate nodes
- the first parameter is treated as the wired input port
- remaining parameters become config fields
- `*args` and `**kwargs` functions are skipped
- source is not executed during preview or palette generation

At runtime, modules execute inside the workflow environment subprocess.

### Credentials In Nodes

Node parameters can declare credential metadata using helpers in
`packages/nodes/nodyra_nodes/_creds.py`:

- `cred_single(...)` for API keys, tokens, webhook URLs, and passwords
- `cred_multi(...)` for grouped credentials such as username/password or AWS
  key pairs

The UI renders a credential picker and stores only a reference in workflow JSON.

## Backup And Upgrade Guidance

Before upgrading:

1. Back up PostgreSQL.
2. Back up artifact storage.
3. Back up environment/package storage if rebuild time matters.
4. Review Alembic migrations in `apps/api/alembic/versions`.
5. Run `alembic upgrade head` during deployment.
6. Smoke test login, workflow list, one manual run, and one webhook/schedule
   path.

For Docker Compose, the API container runs migrations before starting Uvicorn.

## Roadmap

Planned or designed areas:

- remote runner agents and VM/runner-pool execution
- owner-only API restart control
- stronger sandboxing options for untrusted code
- OAuth browser flows for supported integrations
- LLM-backed workflow builder with server-side graph validation
- per-workflow concurrency limits
- hot-resize of runner pool semaphores
- richer deployment promotion flows

## Documentation

- `docs/architecture.md` contains a deeper architecture walkthrough.
- `docs/deployment.md` covers local, Docker Compose, and Helm deployment.
- [Backup, restore & upgrades](docs/backup-restore.md).
- `docs/nodes.md` covers built-in nodes, uploaded code modules, artifacts, and
  DatasetRef patterns.
- `docs/integration-development.md` covers spec-driven v2 provider operations,
  triggers, credentials, dynamic options, transport, and tests.
- `docs/provider-coverage-matrix.md` tracks official provider operation and
  trigger coverage.
- `docs/status-matrix.md` tracks shipped/beta/scaffolded/planned architecture
  tasks.
- `CONTRIBUTING.md` and `CONTRIBUTOR_LICENSE_AGREEMENT.md` describe the
  contribution intake and CLA process.
- `plan.md` contains milestone and slice history.
- `HANDOFF.md` contains compact context for engineering handoff.

## License

Nodyra is **fair-code** licensed:

- The core is distributed under the
  [Nodyra Sustainable Use License](LICENSE) — free to use, modify, and
  self-host for internal business and personal purposes. Offering Nodyra to
  third parties as a hosted or managed service requires a commercial agreement.
- Enterprise-gated features (multi-tenancy, SSO/SAML/OIDC, SCIM, audit-log
  export, external KMS, license enforcement) are covered by the
  [Nodyra Enterprise License](LICENSE.enterprise) and require a valid license
  key.
- The Nodyra name and logo are trademarks — see [TRADEMARK.md](TRADEMARK.md).

Contributions require a signed [CLA](CONTRIBUTOR_LICENSE_AGREEMENT.md); see
[CONTRIBUTING.md](CONTRIBUTING.md). For the reasoning behind this model, see
[docs/licensing-and-monetization-report.md](docs/licensing-and-monetization-report.md).
