# Back Up And Upgrade

## Required Backups

Back up:

- PostgreSQL database.
- Artifact storage, unless using a versioned object store with retention.
- Environment storage (`ENVS_DIR`).
- `.env` or Kubernetes secrets.

See [Backup, Restore, And Upgrades](../backup-restore.md) for commands.

## Upgrade Order

Use [Zero-Downtime Upgrade Runbook](../deployment/upgrade.md). The production
order is:

1. Verify image signatures and SBOM attestations.
2. Run additive migrations.
3. Roll workers.
4. Roll API.
5. Roll web.
6. Run REST and MCP smoke tests.

Do not use `alembic downgrade` as a production rollback path. Restore from a
verified backup after destructive migrations.
