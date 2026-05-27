# Noodle

A self-hostable, Python-native workflow automation platform where every node is pure Python.

Build workflows on a drag-and-drop canvas, run them on warm Python worker processes, manage
per-workflow environments, and export any workflow as a standalone `.py` file or Docker image.
The built-in node library includes core data/logic/transform nodes plus first-pass official
integrations for Slack, Discord, SMTP, Google Sheets, Notion, GitHub, SQL databases, S3,
OpenAI, Anthropic, Stripe, and Airtable.

## Repository layout

```
apps/
  web/        React + React Flow editor (Vite)
  api/        FastAPI server
  worker/     Celery workers + env-runner pool
packages/
  core/       execution engine + node SDK + shared models   (dist: noodle-core)
  nodes/      built-in node library                          (dist: noodle-nodes)
  exporter/   workflow -> .py / Docker image                 (dist: noodle-exporter)
  runtime/    env-runner subprocess server                   (dist: noodle-runtime)
deploy/       docker-compose dev stack, Dockerfiles, Helm chart
```

## Development

Requires [uv](https://docs.astral.sh/uv/), Node 20+, and Docker.

```bash
# Start infra + services
docker compose -f deploy/docker-compose.yml up

# Or run the stack locally
uv sync
docker compose -f deploy/docker-compose.yml up postgres redis minio
uv run uvicorn app.main:app --reload --app-dir apps/api      # API
uv run celery -A worker.celery_app worker -l info            # worker
cd apps/web && npm install && npm run dev                    # web
```

API health: `GET http://localhost:8000/health/ready`

See `docs/` and the design plan for architecture details.
