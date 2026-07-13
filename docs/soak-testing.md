# Durable queue soak testing

Bring up the compose stack, then run the soak profiles against the live API.
For local no-auth smoke tests, set `AUTH_REQUIRED=false` before `docker compose
up` and omit `NODYRA_TOKEN`. For authenticated environments, `NODYRA_TOKEN`
must be a session bearer token with workflow and ops permissions.

```bash
docker compose -f deploy/docker-compose.yml up -d
uv run python scripts/soak_test.py \
  --base-url http://localhost:8000 --runs 50 --cancel-ratio 0
uv run python scripts/soak_test.py \
  --base-url http://localhost:8000 --runs 100 --cancel-ratio 0.3
uv run python scripts/soak_test.py \
  --base-url http://localhost:8000 --runs 100 --cancel-ratio 0.2 \
  --kill-container nodyra-worker-1
```

The sandbox-required profile exercises the split execution plane with the
compose sandbox overlay and the Docker socket proxy. It submits the workflow
and each run with sandboxed execution enabled, checks that the API reports the
configured sandbox mode, kills the worker container mid-storm, and then asserts
the queue drains. This profile requires an active Pro-or-higher license because
sandbox execution is license-gated at startup:

```bash
AUTH_REQUIRED=false EXECUTION_SANDBOX=required SANDBOX_RUNTIME=runc \
MINIO_ROOT_PASSWORD=nodyra-ci-minio-password \
INTERNAL_API_TOKEN=ci-internal-token \
NODYRA_SECRET_KEY=ci-secret-key-at-least-32-bytes-long \
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.sandbox.yml \
  up -d --build api worker

uv run python scripts/soak_test.py \
  --base-url http://localhost:8000 \
  --runs 50 \
  --cancel-ratio 0.2 \
  --kill-container nodyra-worker-1 \
  --sandbox \
  --expect-sandbox-mode required \
  --settle-seconds 240
```

The script asserts three invariants: every started run reaches a terminal
status before `--settle-seconds`, the durable queue has no leased/running
entries after settling, and killing then restarting a worker loses zero runs.
When `--sandbox` is set it also forces `execution_mode=sandboxed` on the
workflow and per-run request.

CI includes a `sandbox-soak` job that runs on the nightly schedule and via
manual `workflow_dispatch`. It mints a short-lived test Pro license using the
existing test signing helper, starts the base compose stack plus
`docker-compose.sandbox.yml`, runs:

```bash
uv run python scripts/soak_test.py --base-url http://localhost:8000 \
  --runs 50 --cancel-ratio 0.2 --kill-container nodyra-worker-1 \
  --sandbox --expect-sandbox-mode required --settle-seconds 240
```

and always prints API, worker, and socket-proxy logs before tearing the stack
down. Paste the script output into any PR that claims manual soak evidence. If
Docker or a sandbox-capable license is unavailable in the environment running
the checks, state that explicitly.
