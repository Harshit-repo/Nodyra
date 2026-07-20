# Nodyra Operations Guide

This directory is the operator-facing path from a clean clone to a secure,
observable production deployment.

## Runbooks

- [Install](install.md): local, compose, and Helm bootstrap.
- [Configure](configure.md): every supported `.env.example` setting with
  production guidance.
- [Scale](scale.md): dispatch topologies, workers, queue, scheduler, and
  runner pools.
- [Secure](secure.md): auth, secrets, sandboxing, SSRF, audit export, and
  multi-tenancy posture.
- [Observe](observe.md): health checks, metrics, tracing, dashboards, alerts,
  and soak tests.
- [Back Up And Upgrade](backup-upgrade.md): required backups, restore notes,
  and upgrade order.
- [Troubleshoot](troubleshoot.md): common production symptoms and checks.
- [Workers](workers.md): detailed worker and runner-pool operations.
- [Production attestation](production-attestation.md): machine-readable posture
  checks and redacted evidence bundles.
- [Compatibility matrix](compatibility-matrix.md): supported infrastructure,
  browser, and runner protocol combinations.
- [Upgrade and rollback](upgrade-rollback.md): N-1 upgrade, canary, and recovery
  contract.
- [Alert runbooks](alert-runbooks.md): owner and mitigation for every shipped
  Prometheus alert.
- [Release evidence](release-evidence.md): required certification and
  supply-chain attachments.
- [Staged rollout](staged-rollout.md): internal, pilot, canary, broad, and
  automatic halt thresholds.

## Production Baseline

For production, use PostgreSQL, Redis, object storage, authentication, a strong
`SECRET_KEY`, a non-empty `INTERNAL_API_TOKEN` for split dispatch, explicit
`CORS_ORIGINS`, and `EXECUTION_SANDBOX=required` for multi-tenant or untrusted
workflow authors. `/ops/runtime-mode` and the Settings readiness panel should
show zero failures before exposing the instance to users. Warnings that require
operator-owned proof—backup restore, TLS termination, and external KMS—must be
attached to the release evidence rather than ignored.
