# Troubleshoot

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
