.PHONY: demo demo-env demo-down

DEMO_ENV_FILE ?= .tmp/nodyra-demo.env
DEMO_PROJECT ?= nodyra-demo

demo-env:
	uv run python scripts/generate_demo_env.py --output "$(DEMO_ENV_FILE)"

demo: demo-env
	docker compose --env-file "$(DEMO_ENV_FILE)" -p "$(DEMO_PROJECT)" -f deploy/docker-compose.yml up -d --build
	uv run --env-file "$(DEMO_ENV_FILE)" python scripts/wait_for_ready.py --url http://127.0.0.1:8000/health/ready --timeout 180
	uv run --env-file "$(DEMO_ENV_FILE)" python scripts/seed_ai_demo_workflows.py
	uv run --env-file "$(DEMO_ENV_FILE)" python scripts/mcp_smoke.py --no-token

demo-down: demo-env
	docker compose --env-file "$(DEMO_ENV_FILE)" -p "$(DEMO_PROJECT)" -f deploy/docker-compose.yml down --remove-orphans
