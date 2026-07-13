# Zero-Downtime Upgrade Runbook

This runbook is for production upgrades where the database, workers, API, and
web UI stay available during the rollout. It assumes backward-compatible
migrations: add nullable columns/tables first, deploy code that reads and writes
both old and new shapes, then remove old shapes in a later release.

## Preconditions

- A verified backup exists for Postgres, artifacts, environments, and the
  deployment `.env` or Kubernetes secrets. See
  [Backup, Restore, And Upgrades](../backup-restore.md).
- The target image tag has passed CI, including the compose smoke, migration
  drift check, queue/chaos lane, security lane, and frontend build.
- Release images have a valid cosign signature and SBOM attestation.
- Operators have a rollback tag ready. Rollbacks after a destructive migration
  require restoring the backup; do not run `alembic downgrade` against
  production data.

## Kubernetes / Helm

1. Verify the new image and SBOM:

   ```bash
   cosign verify ghcr.io/<owner>/nodyra-api@sha256:<digest>
   cosign verify-attestation --type https://spdx.dev/Document \
     ghcr.io/<owner>/nodyra-api@sha256:<digest>
   ```

2. Run the Helm upgrade. The chart's `pre-upgrade` migration job runs
   `alembic upgrade head` before workload pods roll.

   ```bash
   helm upgrade nodyra deploy/helm/nodyra \
     --namespace nodyra \
     --set image.repository=ghcr.io/<owner>/nodyra-api \
     --set image.tag=<version>
   ```

3. Wait for the migration job to complete:

   ```bash
   kubectl -n nodyra wait --for=condition=complete \
     job/nodyra-migrate --timeout=300s
   ```

4. Roll workers first so new run executors understand the newest queue,
   artifact, and sandbox protocols:

   ```bash
   kubectl -n nodyra rollout status deployment/nodyra-worker
   ```

5. Roll the API. The chart uses `maxUnavailable: 0`, readiness probes on
   `/health/ready`, and a 60 second termination grace period so traffic only
   moves to ready pods:

   ```bash
   kubectl -n nodyra rollout status deployment/nodyra-api
   ```

6. Roll the web UI after the API is ready:

   ```bash
   kubectl -n nodyra rollout status deployment/nodyra-web
   ```

7. Run post-upgrade checks:

   ```bash
   curl -fsS https://<host>/health/ready
   curl -fsS https://<host>/metrics >/tmp/nodyra-metrics.txt
   python scripts/mcp_smoke.py
   ```

## Docker Compose

Compose is host-local and has no load balancer, so "zero downtime" means keeping
the database and queue online while replacing application containers in the
least disruptive order.

1. Back up Postgres, artifact storage, environment storage, and `.env`.
2. Pull/build the target source or images.
3. Apply migrations by starting one API container:

   ```bash
   docker compose -f deploy/docker-compose.yml up -d --build api
   docker compose -f deploy/docker-compose.yml logs --tail=100 api
   curl -fsS http://localhost:8000/health/ready
   ```

4. Roll workers, then web:

   ```bash
   docker compose -f deploy/docker-compose.yml up -d --build worker
   docker compose -f deploy/docker-compose.yml up -d --build web
   ```

5. Run smoke checks:

   ```bash
   curl -fsS http://localhost:8000/templates
   NODYRA_MCP_URL=http://localhost:8000/mcp python scripts/mcp_smoke.py --no-token
   ```

## Rollback

- Before migrations complete: stop the upgrade and redeploy the previous tag.
- After additive migrations complete: redeploy the previous application tag if
  that version is compatible with the expanded schema.
- After destructive migrations or failed data migration: restore the backup to a
  clean database and redeploy the previous tag. Do not rely on downgrade scripts
  for production rollback.

## Compatibility Rules For New Releases

- First release: add schema, code reads old and new data, background jobs can
  backfill.
- Second release: code requires the new schema only after the previous release
  has been fully rolled out.
- Third release or later: remove old columns/tables only when no supported
  version writes them.
- Workers must be protocol-compatible with the API for at least one release in
  both directions.
- Public API and frontend fields added during upgrades must be optional until
  all clients can consume them.
