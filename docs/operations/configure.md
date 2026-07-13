# Configure

Every setting below appears in `.env.example`. Defaults are safe for local
development unless noted; production guidance is explicit.

## Storage And Broker

| Variable | Production guidance |
| --- | --- |
| `DATABASE_URL` | Use PostgreSQL. SQLite is local-only and should make readiness amber/red. |
| `REDIS_URL` | Required for shared rate limits, queueing, event fanout, and split dispatch. |

## HTTP, Public URLs, And MCP OAuth

| Variable | Production guidance |
| --- | --- |
| `CORS_ORIGINS` | Set exact HTTPS browser origins. Do not use `*` with auth. |
| `PUBLIC_API_URL` | Public HTTPS API origin for remote runners and provider callbacks. |
| `MCP_AUTHORIZATION_SERVER_URL` | OAuth authorization-server issuer for remote MCP clients. |
| `MCP_OAUTH_INTROSPECTION_URL` | Token introspection endpoint when MCP OAuth is enabled. |
| `MCP_OAUTH_CLIENT_ID` | OAuth client id used for introspection. |
| `MCP_OAUTH_CLIENT_SECRET` | Store in a secret manager; never commit it. |

## Python Environments And Execution

| Variable | Production guidance |
| --- | --- |
| `ENVS_DIR` | Persistent volume for uv-managed environments. Back it up. |
| `ENABLE_VENV_BUILDS` | Set `false` only in locked-down environments where builds are pre-provisioned. |
| `USE_SUBPROCESS_RUNNER` | Keep `true`; it isolates workflow packages from the API process. |
| `RUNNER_POOL_SIZE` | Raise deliberately with memory capacity planning. |
| `MAX_CONCURRENT_RUNS` | Set to the number of top-level runs the host can sustain. |
| `DOCKER_AUTOSCALE_TICK_SECONDS` | Tune only when using Docker-managed runner pools. |
| `RUN_SYNCHRONOUSLY` | Test-only; keep `false` in production. |
| `CODE_NODE_TIMEOUT_SECONDS` | Keep finite for trusted pools; sandbox still needs resource limits. |
| `WORKER_METRICS_PORT` | Enable when scraping standalone workers directly. |

## Observability

| Variable | Production guidance |
| --- | --- |
| `OTEL_ENABLED` | Set `true` when an OTLP collector is reachable. |
| `OTEL_EXPORTER_PROTOCOL` | Use `http` for the bundled collector or configure your collector protocol. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Point API and workers at the collector trace endpoint. |
| `VITE_TRACE_BASE_URL` | Web trace URL prefix, for example `https://jaeger.example.com/trace`. |

## Sandboxed Execution

| Variable | Production guidance |
| --- | --- |
| `EXECUTION_SANDBOX` | Use `required` for multi-tenant or untrusted code. |
| `SANDBOX_RUNTIME` | `auto` prefers Kata, then gVisor/runsc, then runc. |
| `SANDBOX_DOCKER_HOST` | Point to a socket proxy or rootless daemon; avoid raw host socket access. |
| `SANDBOX_NETWORK` | Dedicated bridge for run containers, isolated from database/cache networks. |
| `SANDBOX_MEM_LIMIT` | Set per-run memory ceiling. |
| `SANDBOX_CPU_LIMIT` | Set per-run CPU ceiling. |
| `SANDBOX_PIDS_LIMIT` | Keep bounded to stop fork amplification. |
| `SANDBOX_TMPFS_SIZE` | Keep temporary filesystem bounded. |
| `SANDBOX_WARM_PER_KEY` | Warm idle containers per org/environment key. |
| `SANDBOX_WARM_TOTAL` | Global warm-container ceiling. |
| `SANDBOX_WARM_TTL_SECONDS` | Idle warm-container TTL. |
| `SANDBOX_MAX_RUNS_PER_CONTAINER` | Recycle warm containers after this many runs. |

## Scheduler, Retention, And Outputs

| Variable | Production guidance |
| --- | --- |
| `ENABLE_INPROCESS_SCHEDULER` | Enable on one leader or use `SCHEDULER_ROLE=leader`. |
| `SCHEDULER_ROLE` | Use one leader in multi-replica deployments. |
| `APP_TIMEZONE` | Pin explicitly to avoid host timezone drift. |
| `RUN_RETENTION_DAYS` | Set to match compliance and database capacity. |
| `RUN_RETENTION_MAX_PER_WORKFLOW` | Add a count cap for high-frequency workflows. |
| `RUN_RETENTION_TICK_SECONDS` | Keep at least 60 seconds; hourly is typical. |
| `MAX_OUTPUT_BYTES` | Keep finite; large values should become artifacts or DatasetRefs. |

## Audit / SIEM Export

| Variable | Production guidance |
| --- | --- |
| `AUDIT_WEBHOOK_URL` | SIEM ingestion endpoint. Leave blank to disable outbound export. |
| `AUDIT_WEBHOOK_SECRET` | Random shared secret used for HMAC signatures. |
| `AUDIT_WEBHOOK_TIMEOUT_SECONDS` | Keep short so audit export never stalls requests. |
| `AUDIT_WEBHOOK_BATCH_SIZE` | Tune to SIEM throughput. |
| `AUDIT_WEBHOOK_QUEUE_SIZE` | Size for expected bursts; watch dropped-event logs. |

## Split Dispatch Topology

| Variable | Production guidance |
| --- | --- |
| `DISPATCH_ROLE` | `control`/`disabled` for API replicas, `worker` for workers, `inline` for single process. |
| `QUEUE_BACKEND` | Use `redis` for split dispatch and shared queue state. |
| `INTERNAL_API_TOKEN` | Strong shared secret for API-worker internal calls. Required in split topologies. |

## Auth, Egress, And Registry

| Variable | Production guidance |
| --- | --- |
| `AUTH_REQUIRED` | Set `true` before exposing an instance. |
| `SECRET_KEY` | Long random secret. Rotating it invalidates sessions and can affect encrypted data. |
| `NODYRA_ALLOW_PRIVATE_EGRESS` | Keep blocked in multi-tenant deployments; allow only for trusted internal automation. |
| `ALLOW_REGISTRY` | Set `false` in air-gapped or maximum-security deployments. |
| `REGISTRY_INDEX_URL` | Pin to an approved registry mirror when allowing community nodes. |
