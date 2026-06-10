# Deployment

> **⚠️ Trust boundary — read before exposing Noodle.** Workflow Code nodes and
> uploaded code modules run **arbitrary Python in the worker process on the
> Noodle host**. Deploy Noodle for **single-tenant, trusted authors** only:
> put it behind authentication, restrict edit/deploy access to people you trust
> to run code on the host, and never offer it as a multi-tenant builder to
> untrusted users. Multi-tenant isolation (containers / gVisor / Firecracker per
> run) is a planned capability of the remote-runner seam, not something the warm
> local pools provide today. See [SECURITY.md](../SECURITY.md).

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
API as a control plane (running migrations on startup, `DISPATCH_ROLE=disabled`,
leader-elected scheduler), a dispatch worker (`DISPATCH_ROLE=worker`, executes
runs), and the web dev server. Open <http://localhost:5173> after the API is
healthy.

### Execution topology (`DISPATCH_ROLE`)

| Role | Process | Leases queue entries | Needs |
|------|---------|----------------------|-------|
| `inline` (default) | API | all (local + agent + docker + kubernetes) | SQLite or Postgres |
| `disabled` | API | none — enqueues only | Postgres + Redis |
| `worker` | `python -m app.worker_main` | local + docker | Postgres + Redis |

Recommended production shape: N API replicas with `DISPATCH_ROLE=disabled` +
`SCHEDULER_ROLE=leader`, M workers, one shared Postgres + Redis. Caveat:
agent/kubernetes runner pools need their WebSocket-terminating API replica to
dispatch them — keep one replica with `DISPATCH_ROLE=inline` if you use those
pools. Workers drain gracefully on SIGTERM (stop leasing, wait
`QUEUE_DISPATCH_SHUTDOWN_TIMEOUT_SECONDS`, then cancel); a worker lost
mid-run is recovered by lease expiry, which requeues the entry and resets the
run for another worker.

## Kubernetes (Helm)

```sh
helm install noodle deploy/helm/noodle \
  --set postgres.url=postgresql+asyncpg://noodle:noodle@postgres:5432/noodle \
  --set redis.url=redis://redis:6379/0 \
  --set secret.key=$(openssl rand -hex 32)
```

The chart deploys the API (control plane), web, and the dispatch worker.
Postgres and Redis are expected to be installed separately. Enable the bundled Ingress
with `--set ingress.enabled=true` — it routes `/api` and `/ws` to the API
and everything else to the web app.

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

#### Graceful drain

Production deploys/restarts should drain before terminating:

```sh
# Stop accepting new leases (leased runs continue until done).
curl -X POST -H 'Authorization: Bearer $ADMIN_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"draining": true}' \
  https://noodle.example.com/ops/drain

# Wait for /ops/queue stats to settle (no leased entries), then terminate.
# Re-enable after the new revision is up:
curl -X POST -H 'Authorization: Bearer $ADMIN_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"draining": false}' \
  https://noodle.example.com/ops/drain
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

### External callbacks

| Setting | Default | Purpose |
|---------|---------|---------|
| `PUBLIC_API_URL` | `http://localhost:8000` fallback | Externally reachable API base URL used for provider-managed trigger callbacks such as GitHub repository webhooks. Set this to the public HTTPS API origin in production. |
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
- `GET /runs` (paginated, filterable) and the **Executions** page in the UI
  show every run across the system with per-node logs, timing, and outputs.

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

## Enabling multi-tenancy

Multi-tenancy ships dormant: with `MULTI_TENANCY_ENABLED=false` (default)
behaviour is identical to single-tenant Noodle. To enable:

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
5. Trust model per org: `shared` execution runs workflow code on the host
   warm pool (trusted authors — Tier 1 hygiene only). Orgs with untrusted
   authors must be set to `dedicated_pool` (org owner setting) and given
   their own docker/kubernetes runner pool — runs that don't resolve to one
   are refused. Deep sandbox hardening (egress policy, gVisor) is not
   included; do not market shared-pool tenancy as hard isolation.
6. Per-org quotas (concurrent runs, executions/day, map/loop caps, etc.) are
   owner-editable per org; instance defaults come from Settings. The
   ops dashboard's queue card shows per-org backpressure, including runs
   parked by an org's concurrency quota.
