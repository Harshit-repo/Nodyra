# Troubleshoot

## Decision tree

1. Is `/health/live` down? Inspect process/container crash logs and resource
   limits before any application-level diagnosis.
2. Is live up but `/health/ready` down? Follow the failing dependency from the
   readiness payload: database → migrations/connection; Redis → queue endpoint;
   sandbox → daemon/image/policy; artifact storage → credentials/bucket/network.
3. Is readiness green but a run is queued? Check `/ops/queue`, drain state,
   worker presence, required labels, per-pool capacity, and lease age.
4. Is the run active but making no progress? Inspect timeline/heartbeat, node
   deadline, approval wait, provider rate limit, and worker churn. Preserve the
   run ID and trace before cancellation.
5. Did the run finish but data is missing? Follow node output → artifact
   metadata → checksum/backend health → retention/reconcile evidence.
6. Is only the browser failing? Compare direct API response, session/CORS/TLS,
   browser console, route error boundary, and the supported browser matrix.

If the cause is still unclear, generate the redacted support bundle and attach
the alert/runbook plus recent deployment digest. Do not attach credentials,
workflow payloads, or raw artifacts by default.

## API Not Ready

```bash
curl -fsS http://localhost:8000/health/ready
docker compose -f deploy/docker-compose.yml logs --tail=200 api
```

Check `DATABASE_URL`, `REDIS_URL`, migration status, and sandbox readiness when
`EXECUTION_SANDBOX=required`.

## Runs Stay Queued

Check queue state and workers:

```bash
curl -fsS http://localhost:8000/ops/queue
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs --tail=200 worker
```

Common causes are missing Redis, mismatched `DISPATCH_ROLE`, worker labels that
do not satisfy a run, or an empty `INTERNAL_API_TOKEN` in a split topology.

## Readiness Panel Shows Warnings

Open Settings -> Readiness or call `/ops/runtime-mode`. Treat production
warnings as deployment blockers unless the risk is intentionally accepted and
documented.

## Artifacts Fail To Download

Check `ARTIFACT_STORAGE_BACKEND`, MinIO/S3 credentials, bucket existence, and
artifact retention settings. For compose, confirm `minio-init` completed.

## Traces Missing

Confirm `OTEL_ENABLED=true`, `OTEL_EXPORTER_OTLP_ENDPOINT` points to the
collector, the collector is reachable, and `VITE_TRACE_BASE_URL` points to the
trace UI.

## MCP Smoke Fails

Run:

```bash
NODYRA_MCP_URL=http://localhost:8000/mcp python scripts/mcp_smoke.py --no-token
```

If auth is required, set `NODYRA_MCP_TOKEN` to a token with workflow
permissions. Tool write calls must include approval; the smoke script already
sends `approved_by_user=true`.
