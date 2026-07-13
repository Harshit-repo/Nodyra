# Observe

## Health

- `/health/live`: process is up.
- `/health/ready`: database, Redis when required, and sandbox readiness.
- Worker health uses the same readiness logic when `WORKER_METRICS_PORT` is
  enabled.

## Metrics

`/metrics` exports Prometheus text including:

- `nodyra_http_requests_total`
- `nodyra_http_request_duration_seconds`
- `nodyra_run_duration_seconds`
- `nodyra_queue_depth`
- `nodyra_queue_leased`
- `nodyra_active_runs`
- `nodyra_code_validation_blocked_total`

Import `deploy/observability/grafana-dashboard.json` for queue depth, active
runs, run duration p50/p95, and per-route HTTP request p50/p95. Load
`deploy/observability/prometheus-alerts.yml` for baseline API, queue, and run
duration alerts.

## Traces

Run the bundled collector and Jaeger:

```bash
OTEL_ENABLED=true docker compose -f deploy/docker-compose.yml \
  --profile observability up -d
```

Set `VITE_TRACE_BASE_URL` so the Executions page links traced runs to Jaeger.

## Soak Tests

Use [soak testing](../soak-testing.md) for queue and sandbox durability. The
nightly CI soak kills a worker mid-run, asserts the queue drains, and enforces a
p95 run-completion threshold.
