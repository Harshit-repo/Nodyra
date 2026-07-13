# Install

## Local Development

1. Install Python 3.12+, Node 20+, Docker, and uv.
2. Copy `.env.example` to `.env`.
3. Start dependencies:

   ```bash
   docker compose -f deploy/docker-compose.yml up -d postgres redis minio
   ```

4. Install and migrate:

   ```bash
   uv sync --locked --all-packages
   cd apps/api && uv run alembic upgrade head
   ```

5. Start the API and web app:

   ```bash
   uv run uvicorn app.main:app --app-dir apps/api --reload
   cd apps/web && npm install && npm run dev
   ```

## Docker Compose

Set production secrets in `deploy/.env` or the shell, then boot the stack:

```bash
POSTGRES_PASSWORD=<strong-db-password> \
MINIO_ROOT_PASSWORD=<strong-minio-password> \
INTERNAL_API_TOKEN=<strong-shared-secret> \
NODYRA_SECRET_KEY=<strong-jwt-secret> \
docker compose -f deploy/docker-compose.yml up -d --build
```

Use the sandbox overlay when workflow code is untrusted:

```bash
EXECUTION_SANDBOX=required \
docker compose -f deploy/docker-compose.yml \
  -f deploy/docker-compose.sandbox.yml up -d --build
```

## Helm

The Helm chart runs migrations with a `pre-install,pre-upgrade` job and uses
readiness probes for rolling updates:

```bash
helm lint deploy/helm/nodyra
helm upgrade --install nodyra deploy/helm/nodyra --namespace nodyra --create-namespace
```

Set image tags, secrets, ingress, and storage in values or external secret
managers. See [Back Up And Upgrade](backup-upgrade.md) before the first
production rollout.
