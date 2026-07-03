# Durable queue soak testing

Bring up the compose stack, mint an API token, then run the soak profiles
against the live API:

```bash
docker compose -f deploy/docker-compose.yml up -d
NODYRA_TOKEN=ndpat_YOUR_TOKEN uv run python scripts/soak_test.py \
  --base-url http://localhost:8000 --runs 50 --cancel-ratio 0
NODYRA_TOKEN=ndpat_YOUR_TOKEN uv run python scripts/soak_test.py \
  --base-url http://localhost:8000 --runs 100 --cancel-ratio 0.3
NODYRA_TOKEN=ndpat_YOUR_TOKEN uv run python scripts/soak_test.py \
  --base-url http://localhost:8000 --runs 100 --cancel-ratio 0.2 \
  --kill-container nodyra-worker-1
```

The script asserts three invariants: every started run reaches a terminal
status before `--settle-seconds`, the durable queue has no leased/running
entries after settling, and killing then restarting a worker loses zero runs.

Paste the script output into any PR that claims soak evidence. If Docker is
unavailable in the environment running the PR checks, state that explicitly.
