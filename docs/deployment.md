# Deployment

## Local development

```sh
uv sync --all-packages
docker compose -f deploy/docker-compose.yml up postgres redis minio

# api
DATABASE_URL=sqlite+aiosqlite:///./dev.db \
  uv run alembic upgrade head --config apps/api/alembic.ini
DATABASE_URL=sqlite+aiosqlite:///./dev.db \
  uv run uvicorn app.main:app --app-dir apps/api --reload --port 8000

# web
cd apps/web && npm install && npm run dev
```

For a full Postgres-backed dev stack, swap the `DATABASE_URL` for the compose
Postgres URL and run `alembic upgrade head` against it.

## docker-compose

`deploy/docker-compose.yml` brings up the full stack — Postgres, Redis,
MinIO, the API (running migrations on startup), Celery worker and beat, and
the web dev server. Open <http://localhost:5173> after the API is healthy.

## Kubernetes (Helm)

```sh
helm install noodle deploy/helm/noodle \
  --set postgres.url=postgresql+asyncpg://noodle:noodle@postgres:5432/noodle \
  --set redis.url=redis://redis:6379/0 \
  --set secret.key=$(openssl rand -hex 32)
```

The chart deploys the API, web, worker, and a beat replica. Postgres and
Redis are expected to be installed separately (Bitnami charts or a managed
service). Enable the bundled Ingress with `--set ingress.enabled=true` and
point a hostname at the cluster — it routes `/api` and `/ws` to the API and
everything else to the web app.

## Observability

- `GET /health/live` — liveness probe (no dependencies).
- `GET /health/ready` — readiness probe (Postgres + Redis).
- `GET /system/status` — JSON status with version, uptime, counts.
- `GET /metrics` — Prometheus text format.

Point Prometheus at the API service and add `/metrics` as a scrape target.

## Security checklist

- Set `SECRET_KEY` to a strong random value. It encrypts credentials and
  signs session tokens.
- Set `CORS_ORIGINS` to your real frontend origin.
- Put the API behind TLS (Ingress with `tls: true`, or a reverse proxy).
- Rotate the encrypted-data key by re-encrypting credentials if it ever leaks.
