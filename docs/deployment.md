# Deployment

> **⚠️ Trust boundary — read before exposing Nodyra.** With the default
> `EXECUTION_SANDBOX=off`, workflow Code nodes and uploaded code modules run
> **arbitrary Python in the worker process on the Nodyra host** — deploy for
> **single-tenant, trusted authors** only: put it behind authentication and
> restrict edit/deploy access to people you trust to run code on the host.
> To serve untrusted authors, enable [sandboxed
> execution](#sandboxed-execution) (per-run hardened containers, gVisor where
> available); multi-tenancy refuses to start without it. See
> [SECURITY.md](../SECURITY.md).

## Local development

```sh
uv sync --all-packages

# api
DATABASE_URL=sqlite+aiosqlite:///./dev.db \
  uv run alembic upgrade head --config apps/api/alembic.ini
DATABASE_URL=sqlite+aiosqlite:///./dev.db \
  uv run uvicorn app.main:app --app-dir apps/api --reload --port 8000

# web
cd apps/web && npm install && npm run dev
```

For a full Postgres-backed dev stack, run `docker compose -f
deploy/docker-compose.yml up postgres redis` and swap `DATABASE_URL` for the
compose Postgres URL.

## docker-compose

`deploy/docker-compose.yml` brings up the full stack — Postgres, Redis, the
API as a control plane (running migrations on startup, `DISPATCH_ROLE=control`,
leader-elected scheduler), a dispatch worker (`DISPATCH_ROLE=worker`, executes
runs), and the web dev server. Open <http://localhost:5173> after the API is
healthy.

### Execution topology (`DISPATCH_ROLE`)

| Role | Process | Leases queue entries | Needs |
|------|---------|----------------------|-------|
| `inline` (default) | API | all (local + agent + docker + kubernetes) | SQLite or Postgres |
| `control` | API | agent + kubernetes only (their WebSockets terminate here) | Postgres + Redis |
| `disabled` | API | none — enqueues only | Postgres + Redis |
| `worker` | `python -m app.worker_main` | local + docker | Postgres + Redis |

Recommended production shape: N API replicas with `DISPATCH_ROLE=control` +
`SCHEDULER_ROLE=leader`, M workers, one shared Postgres + Redis (`control`
keeps the split — workers still run all local/docker execution — while also
dispatching agent/kubernetes runner pools, whose WebSockets terminate on the
API; use `disabled` only when you run no such pools). Workers drain
gracefully on SIGTERM (stop leasing, wait
`QUEUE_DISPATCH_SHUTDOWN_TIMEOUT_SECONDS`, then cancel); a worker lost
mid-run is recovered by lease expiry, which requeues the entry and resets the
run for another worker.

**Scaling workers** — add execution capacity with one command
(`docker compose up -d --scale worker=3`, helm `worker.replicas`, or an agent
runner join token from the UI): see [deployment/workers.md](deployment/workers.md).

`GET /ops/runtime-mode` reports `replica_safe` and
`replica_unsafe_reasons`. If Redis is disabled while the deployment is scaled
or auth/multi-tenancy is enabled, those fields call out per-replica fallbacks
such as run-event buffers, rate-limit counters, OAuth introspection cache, and
secret-redaction cache invalidation. Helm sets `API_REPLICA_COUNT` and
`WORKER_REPLICA_COUNT` automatically; set them yourself in other orchestrators
when using multiple API or worker replicas.

## Kubernetes (Helm)

```sh
helm install nodyra deploy/helm/nodyra \
  --set postgres.url=postgresql+asyncpg://nodyra:nodyra@postgres:5432/nodyra \
  --set redis.url=redis://redis:6379/0 \
  --set secret.key=$(openssl rand -hex 32) \
  --set secret.internalApiToken=$(openssl rand -hex 32) \
  --set api.corsOrigins=https://nodyra.example.com
```

The chart deploys the API (control plane), web, and the dispatch worker.
Postgres and Redis are expected to be installed separately. Enable the bundled Ingress
with `--set ingress.enabled=true` — it routes `/api`, `/ws`, `/mcp`, and the
MCP protected-resource metadata path to the API, and everything else to the
web app.

## Configuration flags

All flags read from environment variables; `apps/api/app/config.py` is the
source of truth.

### Execution

| Setting | Default | Purpose |
|---------|---------|---------|
| `USE_SUBPROCESS_RUNNER` | `true` | Run graphs inside a per-env warm subprocess. |
| `RUNNER_POOL_SIZE` | `1` | Warm processes per env. >1 enables same-env parallelism but each holds a copy of the env's heavy imports. |
| `MAX_CONCURRENT_RUNS` | `8` | Global cap on top-level run dispatch. Sub-workflows bypass this cap (they run in-process on the parent's host). |
| `RUNNER_IDLE_SECONDS` | `300` | Reap warm subprocesses idle for this long. |
| `RUN_SYNCHRONOUSLY` | `false` | Tests only — block on dispatch instead of fire-and-forget. |
| `RUNTIME_HEARTBEAT_INTERVAL_SECONDS` | `15` | Runtime-to-host heartbeat interval. Applied consistently to subprocesses and sandbox containers. |
| `RUNTIME_HEARTBEAT_TIMEOUT_SECONDS` | `45` | Retire and clean up a runtime that emits no valid active-request event within this window. Must exceed the heartbeat interval. |
| `RUNTIME_NO_PROGRESS_TIMEOUT_SECONDS` | `900` | Retire a live runtime that emits only heartbeats and no workflow progress for this long. `0` disables. |

Heartbeat loss and no-progress are intentionally distinct. The first detects a
dead process, broken pipe, blocked event loop, or disconnected container. The
second detects a runtime that is alive but wedged. The overall workflow timeout
remains a third, independent wall-clock ceiling; continuing heartbeats never
extend it. Keep the no-progress timeout above any legitimate long-running node
timeout, or set it to `0` for workflows that intentionally stay inside one node
for longer than the configured bound.

### Scheduler

| Setting | Default | Purpose |
|---------|---------|---------|
| `ENABLE_INPROCESS_SCHEDULER` | `true` | DB-backed cron loop runs inside the API process. Set `SCHEDULER_ROLE=leader` on multi-replica deployments so one replica owns it. |
| `APP_TIMEZONE` | OS-detected | Fallback timezone for schedules without their own `tz`. |

### Retention & limits

| Setting | Default | Purpose |
|---------|---------|---------|
| `RUN_RETENTION_DAYS` | `14` | Age cap on runs (0 = unlimited). Deletes runs, node_runs, artifacts metadata, and artifact files. |
| `RUN_RETENTION_MAX_PER_WORKFLOW` | `0` | Keep only N most recent per workflow (0 = unlimited count, age-only). |
| `RUN_RETENTION_TICK_SECONDS` | `3600` | Retention loop tick. |
| `MAX_OUTPUT_BYTES` | `262144` (256 KB) | Per-port output cap. Oversize replaced with `{_truncated, size, preview}` before persisting. |

### Artifacts

| Setting | Default | Purpose |
|---------|---------|---------|
| `ARTIFACTS_DIR` | `./artifacts` | Local artifact storage root. |
| `ARTIFACT_STORAGE_BACKEND` | `local` | `local` or `s3`. With `s3`, workers still write bytes to `ARTIFACTS_DIR` and the API rehomes them to the bucket in `persist_artifact_refs`; on upload failure the row is kept on the local backend so refs are never orphaned. |
| `ARTIFACT_S3_BUCKET` | unset | Required when backend is `s3`. |
| `ARTIFACT_S3_REGION` | unset | AWS region; empty for non-AWS endpoints. |
| `ARTIFACT_S3_ENDPOINT` | unset | Custom endpoint for MinIO/R2/B2 (S3 API). Credentials come from the standard boto3 chain — the API never stores access keys. |
| `MAX_ARTIFACT_BYTES` | `52428800` (50 MB) | Per-write size cap. 0 = unlimited. |
| `MAX_ARTIFACTS_PER_RUN` | `100` | Per-run count cap. 0 = unlimited. |

### Queue & dispatch

| Setting | Default | Purpose |
|---------|---------|---------|
| `QUEUE_LEASE_SECONDS` | `30` | Lease TTL for a leased run queue entry. Heartbeats extend by this amount; expired leases are requeued. |
| `QUEUE_RETRY_BACKOFF_BASE_SECONDS` | `5` | Base for exponential backoff on retry (`base * 2^(attempts-1)`). |
| `QUEUE_RETRY_BACKOFF_MAX_SECONDS` | `300` | Backoff cap. |
| `QUEUE_DEFAULT_MAX_ATTEMPTS` | `3` | Default `max_attempts` for new queue entries. |
| `QUEUE_DISPATCH_POLL_SECONDS` | `1.0` | Dispatch loop tick. |
| `QUEUE_MAX_DISPATCHES_PER_TICK` | `25` | Upper bound on leases granted per tick. |
| `QUEUE_DISPATCH_SHUTDOWN_TIMEOUT_SECONDS` | `5.0` | How long the dispatch loop waits for in-flight work on shutdown. |
| `QUEUE_DRAIN` | `false` | When `true`, the dispatch loop stops leasing new entries but keeps requeueing expired leases. Use the `/ops/drain` endpoint to toggle at runtime — see below. |
| `WORKER_LABELS` | unset | Worker-only comma-separated capability labels, for example `gpu=a100,mem=high`. Runs with `required_labels` only lease to workers whose labels contain every requested key/value pair. |
| `API_REPLICA_COUNT` / `WORKER_REPLICA_COUNT` | `1` | Advisory replica counts used by `/ops/runtime-mode` to flag Redis-less per-replica fallbacks before scale-out. Helm sets these automatically. |

#### Worker label routing

Set `WORKER_LABELS` on specialized worker pools to keep hardware- or
dependency-specific runs off general workers. Examples:

```sh
WORKER_LABELS=gpu=a100,mem=high python -m app.worker_main
docker compose -f deploy/docker-compose.yml up -d --scale worker=2
helm upgrade nodyra deploy/helm/nodyra --set worker.labels=gpu=a100
```

The dispatch loop advertises each worker's labels and slot counts. The
Executions page's Ops dashboard and `GET /ops/capacity` show available local
worker slots, live dispatchers, and queued runs blocked by unsatisfied labels.

#### Graceful drain

Production deploys/restarts should drain before terminating:

```sh
# Stop accepting new leases (leased runs continue until done).
curl -X POST -H 'Authorization: Bearer $ADMIN_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"draining": true}' \
  https://nodyra.example.com/ops/drain

# Wait for /ops/queue stats to settle (no leased entries), then terminate.
# Re-enable after the new revision is up:
curl -X POST -H 'Authorization: Bearer $ADMIN_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"draining": false}' \
  https://nodyra.example.com/ops/drain
```

`GET /ops/drain` returns `{"draining": bool}` and is unauthenticated for use
in readiness scripts. `POST /ops/drain` requires the `ops:drain` permission
(admin role).

### Auth & security

| Setting | Default | Purpose |
|---------|---------|---------|
| `AUTH_REQUIRED` | `false` | Bearer-token gate on all routes except public auth, health, webhook, provider-webhook, and internal callback paths. |
| `SECRET_KEY` | dev placeholder | Encrypts credentials + signs session tokens. **Must** be rotated for production. |
| `INTERNAL_API_TOKEN` | unset | Shared secret for `/internal/*` worker callbacks when `AUTH_REQUIRED=true`. |
| `CORS_ORIGINS` | `*` (dev) | Comma-separated allowed origins. |
| `QUEUE_BACKEND` | `redis` in deploy manifests | Also backs shared auth/webhook/MCP/workflow rate-limit counters. Production scale-out without Redis is reported as `replica_safe=false` by `/ops/runtime-mode`. |

### External callbacks

| Setting | Default | Purpose |
|---------|---------|---------|
| `PUBLIC_API_URL` | `http://localhost:8000` fallback | Externally reachable API base URL used for provider-managed trigger callbacks such as GitHub repository webhooks. Set this to the public HTTPS API origin in production. |
| `MCP_AUTHORIZATION_SERVER_URL` | unset | Optional external OAuth 2.1 issuer advertised through MCP protected-resource metadata. |
| `MCP_OAUTH_INTROSPECTION_URL` | unset | RFC 7662-style endpoint used to validate external MCP access tokens; required when `MCP_AUTHORIZATION_SERVER_URL` is set. |
| `MCP_OAUTH_CLIENT_ID` / `MCP_OAUTH_CLIENT_SECRET` | unset | Client credentials used only for token introspection. Store the secret in the deployment secret manager. |
| `WEBHOOK_ROLE` | `ingress` | Controls whether webhook/provider-webhook ingress routes are mounted. Use `ingress` or `inline` to receive inbound webhook traffic, and `disabled` for API replicas that should never receive production webhook traffic. |

Provider-managed triggers create callback URLs like
`$PUBLIC_API_URL/provider-webhook/{subscription_id}` during workflow
activation/publish. The URL must be reachable by the external provider over
HTTPS before activating those workflows.

## Migration ordering

Always run `alembic upgrade head` to completion **before** starting any
process that opens a write connection (API, worker, Beat). The Helm chart
runs an init container for this and the `docker-compose` API entrypoint
gates on it. Out-of-order startup is the most common cause of "column does
not exist" errors after a deploy.

Migration policy:

- Migrations are forward-only. Additive nullable columns + backfills are
  preferred so a brief window of mixed-revision processes is safe.
- Drops are split across releases: nullable + stop-writing in release N,
  drop in release N+1 once every replica has rolled.
- The same migration head must be deployed to API, worker, and Beat in
  lockstep. The Helm chart does this by sharing the same image tag.

## Observability

- `GET /health/live` — liveness probe (no dependencies).
- `GET /health/ready` — readiness probe (Postgres + Redis).
- `GET /system/status` — JSON status with version, uptime, counts.
- `GET /metrics` — Prometheus text format.
  - `nodyra_code_validation_blocked_total{reason,target}` increments when
    Code node validation rejects blocked imports, names, attributes, or
    statement types. Alert on sustained increases; they often indicate an
    attempted sandbox escape or a workflow author using the wrong integration
    surface.
- `GET /runs` (paginated, filterable) and the **Executions** page in the UI
  show every run across the system with per-node logs, timing, and outputs.
- `deploy/observability/prometheus-alerts.yml` — starter alerts for API
  scrape failures, HTTP 5xx rate, queue backlog, dead letters, missing worker
  capacity, and long-running executions.
- `deploy/observability/grafana-dashboard.json` — starter dashboard for queue
  depth, active runs, HTTP throughput, and run-duration percentiles.

### Distributed tracing (OpenTelemetry)

Off by default. To enable, set on every API replica **and** worker:

    OTEL_ENABLED=true
    OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318/v1/traces   # OTLP/HTTP

The compose bundle includes a ready-to-use collector and Jaeger UI:

    docker compose -f deploy/docker-compose.yml --profile observability up -d

That profile reads `deploy/observability/otel-collector.yaml`, exposes OTLP/HTTP
on `localhost:4318`, and exposes Jaeger at `http://localhost:16686`. Set the web
build's `VITE_TRACE_BASE_URL` to the Jaeger trace route
(`http://localhost:16686/trace` by default) so the Executions page can link each
traced run directly to its distributed trace.

Each run produces one trace: `run.enqueue` (API, child of the HTTP request
span) → `run.lease` (the worker that picked the entry up) → `run.execute` →
one `node.execute` span per node with `nodyra.node_id`, `nodyra.node_type`,
`nodyra.status`, `nodyra.org_id`, and `nodyra.iteration_path` attributes.
Node spans carry the engine's real start/finish timestamps, including for
nodes executed inside runtime subprocesses — the subprocesses themselves
need no OTel dependencies. Trace context crosses the API→worker boundary on
the durable queue row (`run_queue.trace_context`), so split topologies get
the same single connected trace. FastAPI requests and SQLAlchemy queries are
auto-instrumented in the API process. When disabled, no SDK objects exist
and every hook is a single boolean check. `/ops/runtime-mode` reports
`otel_enabled` so you can confirm what a replica is actually running.

## Security checklist

- Set `SECRET_KEY` to a strong random value. It encrypts credentials and
  signs session tokens.
- Set `AUTH_REQUIRED=true` and create the first admin via `/auth/register`.
- Set `CORS_ORIGINS` to your real frontend origin.
- Set `PUBLIC_API_URL` to the public HTTPS API origin before activating
  provider-managed triggers.
- Put the API behind TLS (Ingress with `tls: true`, or a reverse proxy).
- Code modules and the Code node execute arbitrary Python in the workflow's
  env. Gate Code Library access to Editor/Admin roles.
- `ARTIFACTS_DIR` should be on persistent storage (PVC or host volume) so
  downloads survive restarts. If you set a retention policy, files for
  pruned runs are deleted automatically.
- **Multi-tenancy (`MULTI_TENANCY_ENABLED=true`): the API must connect to
  Postgres as a non-superuser role without `BYPASSRLS`.** Postgres superusers
  skip row-level security entirely, which voids the tenant-isolation backstop
  (see migration `0042_rls` and `tests/test_tenancy_isolation_pg.py`). The
  docker-compose default user is a superuser — fine for single-tenant, not
  for multi-tenant. Create a dedicated app role and grant table privileges
  instead.

## Editions & licensing

Nodyra ships in three editions. With **no license key the instance is
Community** and behaves exactly as an unlicensed self-hosted install, subject to
the Community resource caps below.

| Capability | Community | Pro | Enterprise |
|---|---|---|---|
| Environments | 3 | 10 | unlimited |
| Runner pools | 1 | 5 | unlimited |
| Active deployments | 10 | unlimited | unlimited |
| Seats (users) | 5 | 10 | unlimited |
| All nodes, MCP server, webhooks, scheduling | ✅ | ✅ | ✅ |
| Sandboxed execution (`EXECUTION_SANDBOX`) | ✅ | ✅ | ✅ |
| Observability (`OTEL_ENABLED`) | — | ✅ | ✅ |
| Multi-tenancy / organizations | — | — | ✅ |
| SSO / SAML / OIDC, audit logs, org-KEK/KMS | — | — | ✅ |

`unlimited` is represented internally as `0` (the same convention as the
per-org quota overrides).

### Applying a license

A license is a signed key verified **offline** (no phone-home). Provide it either
way — the env var wins when both are set:

- **Env var:** `NODYRA_LICENSE_KEY=<key>`.
- **UI:** *Settings → License* (admin only), which persists the key to
  `system_settings` (`PUT /system-settings/license`).

`GET /system-settings/license` returns the active edition, customer, expiry, and
limits; `DELETE /system-settings/license` reverts to Community.

### Capability reconciliation at startup

Capability flags are reconciled against the license when the app boots: a flag
set without the matching entitlement is **forced off with a logged warning**
rather than failing to start. For example, `MULTI_TENANCY_ENABLED=true` on a
Community instance boots single-tenant and logs `licensing: multi_tenancy_enabled
requires the Enterprise edition; disabled`. The same applies to
`OTEL_ENABLED` (Pro or higher). Sandboxed execution is available in Community;
so enabling multi-tenancy
(next section) additionally requires an Enterprise license.

### Expiry

An expired key **gracefully downgrades to Community** — running workflows and
data are never touched. Creation of resources already over the Community cap is
blocked (HTTP `402`) until the license is renewed; existing resources keep
working.

### Minting keys (vendor)

Keys are signed offline with `apps/api/tools/mint_license.py`. Generate the
keypair once (`python -m tools.mint_license keygen`), paste the public half into
`_BAKED_PUBLIC_KEY_PEM` in `app/services/licensing.py`, and keep the private key
secret. Mint per-customer keys with `… sign --tier pro --customer "Acme" --days
365`.

## Enabling multi-tenancy

Multi-tenancy ships dormant: with `MULTI_TENANCY_ENABLED=false` (default)
behaviour is identical to single-tenant Nodyra. To enable:

1. Run on **Postgres** (RLS is the DB-enforced isolation backstop; SQLite has
   none) with a **non-superuser app role** — see the security checklist above.
2. Apply migrations (`alembic upgrade head`); existing data lands in the
   `default` organization and every user keeps their role there.
3. Set `MULTI_TENANCY_ENABLED=true` and `AUTH_REQUIRED=true` (anonymous
   requests can only ever reach the default org).
4. Users create organizations from the org switcher in the header; the
   creator becomes that org's owner and gets an org-scoped default
   environment. Members, roles, quotas, and usage live under
   **Manage organization**.
5. Set `EXECUTION_SANDBOX=required` on every process that executes runs
   (the worker, or the API when `DISPATCH_ROLE=inline`) — startup refuses
   unsafe combinations otherwise. See "Sandboxed execution" below for setup.
   `SANDBOX_POLICY_STRICT=false` disables that check for deployments where
   every tenant is trusted (e.g. internal departments); only then does the
   pre-sandbox trust model apply: orgs with untrusted authors must use
   `dedicated_pool` with their own docker/kubernetes runner pool.
6. Per-org quotas (concurrent runs, executions/day, map/loop caps, etc.) are
   owner-editable per org; instance defaults come from Settings. The
   ops dashboard's queue card shows per-org backpressure, including runs
   parked by an org's concurrency quota.

## Sandboxed execution

With `EXECUTION_SANDBOX` enabled, the execution plane runs each workflow in a
disposable hardened container instead of a subprocess: caps dropped,
`no-new-privileges`, read-only rootfs (tmpfs `/tmp`), non-root user, memory/
CPU/pids ceilings, and a dedicated bridge network. Containers are warm-pooled
per `(organization, environment)` — never reused across orgs — and recycled
after `SANDBOX_MAX_RUNS_PER_CONTAINER` runs or `SANDBOX_WARM_TTL_SECONDS`
idle.

Modes (`EXECUTION_SANDBOX`):

- `off` (default) — subprocess runner, single-tenant behaviour unchanged.
- `auto` — use the sandbox when a Docker daemon is reachable, else log a
  warning and fall back to the subprocess runner.
- `required` — refuse to start without a working sandbox. **Enforced at
  startup when `MULTI_TENANCY_ENABLED=true`** (escape hatch:
  `SANDBOX_POLICY_STRICT=false`, trusted tenants only).

Container runtime (`SANDBOX_RUNTIME`): `auto` probes the daemon and picks the
strongest available runtime — `kata` (microVM) > `runsc` (gVisor) > `runc`.
Set it explicitly to fail fast when a specific runtime is mandatory.

| Host | Runtime you get | Isolation |
| --- | --- | --- |
| Docker Desktop (Windows/macOS) | `runc` | container + the Desktop VM boundary |
| Linux / WSL2, gVisor installed | `runsc` | user-space kernel (syscall interception) |
| Linux with Kata containers | `kata` | per-container microVM |

gVisor install (Linux/WSL2): follow
<https://gvisor.dev/docs/user_guide/install/>, add `runsc` to
`/etc/docker/daemon.json` runtimes, restart dockerd. `docker info` should
list `runsc` under Runtimes.

docker-compose: enable sandboxing with the shipped overlay:

```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.sandbox.yml up -d
```

The overlay sets `EXECUTION_SANDBOX=auto` by default; set
`EXECUTION_SANDBOX=required` to refuse worker startup unless Docker and the
selected runtime are available. The worker does **not** mount
`/var/run/docker.sock` directly. The overlay starts a `docker-socket-proxy`
service, mounts the host socket only into that proxy, and points the worker at
`SANDBOX_DOCKER_HOST=tcp://docker-socket-proxy:2375`. The proxy exposes only
the API groups the sandbox needs for environment image builds, network
management, container create/start/stop/log/remove, and daemon info/version.
Denied groups such as `exec`, Swarm/services, volumes, secrets, plugins, and
auth remain disabled.

This is still host-daemon access, not a hard tenant boundary by itself. For
untrusted tenants, prefer a dedicated rootless Docker/Podman daemon or a
Kubernetes runner pool. See `docs/operations/workers.md` for the worker
security tiers and verification checklist.

### Without Docker: rootless Podman

The sandbox talks to any Docker-API-compatible daemon. On a host without
Docker, rootless Podman works daemonlessly and without root:

```bash
systemctl --user enable --now podman.socket
export SANDBOX_DOCKER_HOST=unix:///run/user/$(id -u)/podman/podman.sock
export EXECUTION_SANDBOX=auto
```

Notes: rootless Podman ignores the `runtime` selection for gVisor/Kata (it
runs crun/runc-equivalent containers); resource limits (memory/cpu/pids)
require cgroups v2 delegation, which is enabled on most modern distros.
Verify with Settings -> the sandbox status card, or `GET /ops/sandbox`.

Python-level sandboxing libraries (pysandbox, RestrictedPython, PyPy's
sandbox) are not supported as isolation modes: in-process Python jails are
escapable by design and would be a false security boundary. The supported
tiers are subprocess pool (trusted), containers via Docker/Podman (untrusted
code), and remote runner pools.

Resource ceilings (per run container, overridable per deployment):
`SANDBOX_MEM_LIMIT` (default `1g`), `SANDBOX_CPU_LIMIT` (`1.0`),
`SANDBOX_PIDS_LIMIT` (`256`), `SANDBOX_TMPFS_SIZE` (`256m`). The security
floor (cap-drop, no-new-privileges, read-only rootfs, non-root) is not
overridable. Warm-pool sizing: `SANDBOX_WARM_PER_KEY` (`1`),
`SANDBOX_WARM_TOTAL` (`8`).

Network: run containers attach to the `SANDBOX_NETWORK` bridge
(`nodyra-sandbox`, created on demand). They get outbound internet (HTTP
nodes need it) but sit isolated from the compose service network. Stricter
egress (blocking cloud metadata endpoints, allow-listing destinations) is
operator-supplied: point `SANDBOX_NETWORK` at a network you manage with
firewall rules.

Kubernetes: the DooD socket mount does not translate; the planned K8s path
is a Job per run with `runtimeClassName: gvisor` — a follow-up slice. Until
then, run the worker on a node/VM with Docker for sandboxed execution.
