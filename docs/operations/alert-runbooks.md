# Alert runbooks

Use the run ID and trace ID together when available. Before replaying work, verify whether the failed action has external side effects and an idempotency key.

## NodyraApiScrapeDown

Check pod/process health, service discovery, `/health/live`, network policy, and certificate expiry. Drain an unhealthy replica; do not restart every replica simultaneously.

## NodyraHighHttpErrorRate

Split 5xx by route and replica, correlate traces, then inspect PostgreSQL, Redis, object storage, and recent deployments. Roll back the canary if the increase started with the release.

## NodyraHttpLatencyBudgetBurn

Compare route p95, database latency, connection-pool saturation, CPU/memory throttling, and queue queries. Scale only after identifying the constrained tier.

## NodyraQueueBacklog

Inspect oldest queue age, eligible worker labels, lease churn, concurrency caps, and per-workspace fairness. Add capacity or pause noisy workloads after confirming dispatch is healthy.

## NodyraQueueLatencyBudgetBurn

Treat this as user-visible degradation. Confirm workers exist and are eligible, Redis and PostgreSQL are reachable, draining is off, and leases are advancing. Escalate if oldest age continues to rise.

## NodyraDeadLettersPresent

Group dead letters by failure reason and workflow. Repair the dependency first; replay only bounded selections and monitor for duplicate side effects.

## NodyraNoActiveWorkersWithBacklog

Restore at least one compatible worker, verify `DISPATCH_ROLE=worker`, health endpoints, protocol negotiation, environment availability, and required capability labels.

## NodyraLongRunningExecutions

Inspect the slow node, external dependency latency, loop bounds, resource ceilings, and timeout policy. Cancel only after checking whether the node is safely interruptible.

## NodyraRunErrorBudgetBurn

Group failures by workflow, node type, provider, and release version. Pause the offending deployment or roll back the canary; preserve traces and artifact evidence.

## NodyraArtifactOutputStoreDegraded

Check bucket/container availability, credentials, encryption policy, quota, network path, and multipart failures. Run reconciliation in dry-run mode before any repair or orphan deletion.
