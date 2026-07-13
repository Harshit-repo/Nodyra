# Backup, Restore, And Upgrades

## What to back up

Back up the Postgres `pgdata` volume, the `artifactdata` and `envdata` volumes,
and your `.env` file. **If you lose `SECRET_KEY`, every stored credential is
permanently undecryptable.**

## Backup

```bash
docker compose -f deploy/docker-compose.yml exec postgres \
  pg_dump -U nodyra -Fc nodyra > nodyra-$(date +%F).dump
docker run --rm -v nodyra_artifactdata:/data -v "$PWD:/backup" \
  alpine tar czf /backup/artifacts-$(date +%F).tgz -C /data .
docker run --rm -v nodyra_envdata:/data -v "$PWD:/backup" \
  alpine tar czf /backup/envs-$(date +%F).tgz -C /data .
```

Store `.env` with the dumps in your secret backup system.

### Automated restore verification

Documentation is not restore evidence. The shipped CI creates a synthetic
workflow, terminal run, envelope-encrypted credential, and checksummed S3
artifact; restores PostgreSQL into a clean database and objects into a clean
bucket; then verifies row integrity, strict credential decryptability, and
artifact SHA-256. The redacted JSON evidence is retained as the
`recovery-evidence` CI artifact.

Use the same fixture around your own backup mechanism:

```sh
# Before the snapshot (uses DATABASE_URL and the configured S3 variables):
uv run python scripts/recovery_fixture.py seed --manifest recovery-manifest.json

# After restoring into clean DATABASE_URL / ARTIFACT_S3_BUCKET targets:
uv run python scripts/recovery_fixture.py verify \
  --manifest recovery-manifest.json \
  --evidence recovery-evidence.json \
  --restore-seconds "$RESTORE_SECONDS" \
  --max-rto-seconds 3600
```

`copy-bucket` exists for the disposable reference drill; production object
storage should use versioning, replication, immutable retention where required,
and provider-native inventory/restore tooling. Never upload the database dump,
bucket backup, `.env`, or key material as ordinary CI artifacts—only the
redacted evidence JSON is safe to retain.

## Restore

Create fresh volumes, restore Postgres, extract artifacts and environments, and
start the API last:

```bash
docker compose -f deploy/docker-compose.yml up -d postgres redis minio
cat nodyra-YYYY-MM-DD.dump | docker compose -f deploy/docker-compose.yml exec -T \
  postgres pg_restore -U nodyra -d nodyra --clean
docker run --rm -v nodyra_artifactdata:/data -v "$PWD:/backup" \
  alpine sh -c "cd /data && tar xzf /backup/artifacts-YYYY-MM-DD.tgz"
docker run --rm -v nodyra_envdata:/data -v "$PWD:/backup" \
  alpine sh -c "cd /data && tar xzf /backup/envs-YYYY-MM-DD.tgz"
docker compose -f deploy/docker-compose.yml up -d api worker web
```

## Upgrades

Back up first, then follow the
[zero-downtime upgrade runbook](deployment/upgrade.md). The short compose order
is still: start the API so it runs `alembic upgrade head`, then start workers
and web:

```bash
docker compose -f deploy/docker-compose.yml up -d api
docker compose -f deploy/docker-compose.yml up -d worker web
```

Downgrade by restoring the backup. Do not run `alembic downgrade` against
production data.

## S3 artifact backends

When artifacts live in S3 or a compatible object store, enable bucket
versioning and lifecycle retention instead of tarring the `artifactdata`
volume. Continue backing up Postgres, `envdata`, and `.env`.
