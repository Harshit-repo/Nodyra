.PHONY: demo

DEMO_POSTGRES_PASSWORD ?= nodyra
DEMO_MINIO_ROOT_PASSWORD ?= nodyra-demo-minio
DEMO_INTERNAL_API_TOKEN ?= nodyra-demo-internal-token
DEMO_SECRET_KEY ?= nodyra-demo-secret-key-at-least-32-bytes
DEMO_DATABASE_URL ?= postgresql+asyncpg://nodyra:$(DEMO_POSTGRES_PASSWORD)@localhost:5432/nodyra

demo:
	POSTGRES_PASSWORD="$(DEMO_POSTGRES_PASSWORD)" \
	MINIO_ROOT_PASSWORD="$(DEMO_MINIO_ROOT_PASSWORD)" \
	INTERNAL_API_TOKEN="$(DEMO_INTERNAL_API_TOKEN)" \
	NODYRA_SECRET_KEY="$(DEMO_SECRET_KEY)" \
	AUTH_REQUIRED=false \
	docker compose -f deploy/docker-compose.yml up -d --build
	uv run python scripts/wait_for_ready.py --url http://localhost:8000/health/ready --timeout 180
	DATABASE_URL="$(DEMO_DATABASE_URL)" \
	SECRET_KEY="$(DEMO_SECRET_KEY)" \
	AUTH_REQUIRED=false \
	uv run python scripts/seed_ai_demo_workflows.py
	NODYRA_MCP_URL=http://localhost:8000/mcp uv run python scripts/mcp_smoke.py --no-token
