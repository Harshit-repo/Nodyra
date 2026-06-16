# 10 — Logging, Observability & Errors

Independent review, 2026-06-16. Files: `apps/api/app/tracing.py`,
`apps/api/app/services/redaction.py`, `apps/api/app/main.py` (logging/handlers),
`apps/api/app/routers/health.py` / `ops.py`, `apps/api/app/services/metering.py`.

## What is solid (verified)
- **Distributed tracing (OTel)**: spans for requests + SQLAlchemy + synthesized
  `node.execute`; W3C context propagation via `inject_context`/`_context_from`,
  used to **parent worker spans on the enqueueing request's trace**
  (`run_queue.trace_context`). Idempotent setup; genuinely zero-overhead when
  `otel_enabled=False` (a single boolean check). Well done.
- **Secret redaction** (`redaction.py`): known credential values are scrubbed
  (`***REDACTED***`) from logs, expression previews, run events, **and persisted
  run data** — by exact substring match plus sensitive-key detection. This is the
  thing most platforms miss; node outputs/logs can't leak a stored API key. Cache
  is process-local with TTL + explicit invalidation.
- **Structured error responses**: global `Exception` handler returns
  `{"detail","request_id"}` with the stack logged server-side (`logger.exception`),
  never leaked to the client.
- **Health/ops surface**: `/health/live`, `/ops/runtime-mode` (surfaces
  `runtime_warnings`), `/ops/drain`, `/ops/dead-letter` — good operator controls.

## Findings

### OBS-1 — No request-id generation; correlation depends on an upstream proxy (LOW–MEDIUM)
The global exception handler and most logs read `request.headers.get("x-request-id", "")`
— **read-only**. Only the webhook handler generates one
(`x-request-id or uuid4().hex`). So unless a reverse proxy injects `x-request-id`,
logs carry an empty id and can't be correlated across a request's log lines.
- **Fix:** a tiny middleware that sets `request.state.request_id =
  incoming or uuid4().hex`, echoes it in an `X-Request-Id` response header, and is
  used by the exception handler + a logging filter. Small, high-leverage.
- **Status:** Reviewed — easy add.

### OBS-2 — Logs are plain-text, not structured/JSON (LOW–MEDIUM)
Logging is `logging.getLogger("noodle")` with `%`-format human messages and no
JSON formatter / `logging.config`. Production aggregation (Loki/ELK/Datadog,
including on Render/k8s) wants one JSON object per line with stable fields
(level, logger, request_id, org_id, run_id, msg).
- **Fix:** add an opt-in JSON log formatter (env-gated, e.g. `LOG_FORMAT=json`)
  and bind `request_id`/`org_id`/`run_id` via a `LoggerAdapter`/filter. Keep
  human format as the dev default.
- **Status:** Reviewed.

### OBS-3 — No metrics endpoint / ops counters (LOW–MEDIUM) — overlaps QUEUE-3
There's OTel *tracing* and per-org *run metering* (billing), but no Prometheus
`/metrics` or counters for the four operational signals: queue depth by status,
oldest-eligible lag, lease-reclaim count, dead-letter rate (plus run
success/fail/duration). These are what an operator alerts on.
- **Fix:** expose an OTel metrics exporter or a `/metrics` endpoint with those
  counters/gauges (the queue already computes most via its `stats` surface).
- **Status:** Reviewed.

### OBS-4 — Redaction cache staleness across replicas (LOW, already acknowledged)
A credential rotated on replica A may not be redacted on replica B until B's
cache TTL lapses (the code comments call this out). Acceptable; mention in ops
docs that the redaction cache is eventually-consistent.
- **Status:** Reviewed (doc).

## Verdict
Observability **fundamentals are strong** — OTel with cross-process trace
context and real secret redaction are above the bar for this class of tool. The
gaps are production-operability polish: turn on/define request-id correlation
(OBS-1), offer JSON logs (OBS-2), and expose ops metrics (OBS-3). None block a
demo; all three matter for a SaaS/self-host production posture.
