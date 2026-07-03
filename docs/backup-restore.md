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

Back up first, then pull the new image/source. Start the API so it runs
`alembic upgrade head`, then start workers and web:

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
