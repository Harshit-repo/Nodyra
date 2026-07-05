# One-Click Docker Workers + Autoscaling — Design

**Date:** 2026-07-05 · **Status:** approved by Harry (chat) · **Owner:** Fable 5

## Problem

Adding execution capacity today requires running a command by hand
(`docker compose up -d --scale worker=3`, or copying the agent join command
from the UI). There is no path from "the queue is backing up" to "capacity
appears" without an operator, and no per-worker resource envelope a user can
set from the UI.

## Goals

1. **One-click:** a button in Runner Pools that adds a Docker-backed worker
   with zero copy-paste.
2. **Autoscaling:** worker count follows queue depth within user-set bounds.
3. **Resource limits:** per-container CPU / memory / pids caps, plus a
   max-workers ceiling.
4. **Daemon choice:** each pool targets either the **local** Docker daemon
   (default) or a **remote** daemon via `docker_host`
   (`tcp://…` / `ssh://…`) — a remote machine with Docker becomes worker
   capacity without SSH onboarding.
5. **Sandbox support checkbox:** optionally give spawned runners Docker
   daemon access so they execute each run in a disposable **hardened sibling
   container** (same security floor as the platform sandbox), and advertise
   `{"sandbox": true}` so label routing steers sandbox-required runs to them.

## Non-goals

- Kubernetes autoscaling (the k8s provider + HPA remains the enterprise path).
- Autoscaling the compose `worker` service itself (operator territory).
- Publishing images to a registry (separate roadmap item; everything here
  builds images locally from the repo's wheel cache).

## Part 1 — Quick win: "Enable Docker Workers" preset (per-run containers)

Frontend-only. A card on the Runner Pools page:

- Button **[Enable Docker Workers]** → `POST /runner-pools` (existing
  endpoint) with `{name: "Docker Workers", provider: "docker",
  max_concurrent_runs, provider_config: {docker_host, network, runtime,
  limits: {mem_limit, nano_cpus, pids_limit}}}` from the card's controls
  (max concurrent, CPU, memory, local/remote daemon).
- The existing per-run docker provider does the rest (containers spawn per
  run, hardened, removed after; scale-to-zero inherent).
- Card hides itself when a docker-provider pool already exists; otherwise
  shows dismissible.

## Part 2 — Long-lived Docker agent runners with autoscaling

### Data model (no migration)

All state lives in existing JSON columns:

- `RunnerPool.provider_config` (agent pools) gains:
  ```json
  {
    "docker_host": "",                  // "" = local daemon (from_env)
    "docker_network": "",               // "" = default bridge; compose: "nodyra_default"
    "docker_api_url": "",               // URL runners use to reach the API;
                                        // default resolution below
    "docker_runner": {                  // per-container envelope
      "cpu": 1.0, "memory_mb": 1024, "pids": 512,
      "max_concurrent_runs": 2,
      "sandbox": false                  // the checkbox
    },
    "docker_autoscale": {
      "enabled": false, "min_runners": 0, "max_runners": 4,
      "idle_seconds": 300
    }
  }
  ```
- `Runner.capabilities` for docker-managed runners gains:
  `{"docker_managed": true, "container_name": "nodyra-worker-<id12>",
    "sandbox": <bool>}` (+ existing label semantics apply — `sandbox: true`
  is matchable by `required_labels`).

`docker_api_url` resolution order: explicit value → `settings.public_api_url`
→ `http://host.docker.internal:8000` (local daemon) — the spawn adds
`extra_hosts: {"host.docker.internal": "host-gateway"}` so this works on
Linux too. Validation rejects a blank result.

### New service: `apps/api/app/services/docker_workers.py`

Pure-logic functions are separated from Docker I/O for testability:

- `plan_scaling(pools_state) -> list[Action]` — **pure**; input is a
  snapshot (per pool: queued count, runners with status/current_runs/
  last_seen/idle_since, autoscale config), output is
  `[SpawnRunner(pool_id), RemoveRunner(runner_id), …]`. Rules:
  - scale **up** one runner per pool per tick when
    `queued > 0` and every online docker-managed runner is saturated
    (`current_runs >= max_concurrent_runs`) — or none is online — and
    `count < max_runners`;
  - scale **down** one idle runner per tick (`current_runs == 0`,
    idle ≥ `idle_seconds`, runner is drained or draining) when
    `count > min_runners`;
  - never remove a busy runner; never exceed `max_runners` even if
    min > max (clamp + log).
- `ensure_agent_image(client) -> str` — build
  `nodyra-runner-agent:<IMAGE_SCHEMA_VERSION>` from a tar context containing
  the server's wheel cache (`wheel_index.ensure_wheels()`) + generated
  Dockerfile: `python:3.12-slim`, install `uv`, `pip install nodyra-runner
  --find-links /wheels`, non-root user, entrypoint script that runs
  `nodyra-runner register --api-url $NODYRA_API_URL --token
  $NODYRA_RUNNER_TOKEN --name $NODYRA_RUNNER_NAME && exec nodyra-runner
  start`. Reuses the build pattern of `container_runtime.ensure_base_image`.
- `spawn_docker_runner(session, pool, *, name=None) -> Runner` —
  creates the Runner row + registration token via the shared mint helper
  (factored out of the router, see below), then
  `client.containers.run(image, detach=True, name=…,
  environment={API_URL, TOKEN, NAME}, network=…, mem_limit=…, nano_cpus=…,
  pids_limit=…, restart_policy={"Name": "on-failure", "MaximumRetryCount": 3},
  security_opt=["no-new-privileges:true"], init=True,
  extra_hosts=…, volumes=<socket mount iff sandbox checkbox>)`.
  NOT read-only rootfs (the agent builds venvs); resource-capped, not the
  full sandbox floor — the agent is platform-trust, like the compose worker.
- `remove_docker_runner(session, runner, *, force=False)` — refuse if
  `current_runs > 0` unless force; stop+remove container (ignore
  NotFound), delete the Runner row.
- `reconcile(session, client, pool)` — containers labeled
  `nodyra.pool=<pool_id>` without a Runner row → remove; docker-managed
  Runner rows whose container is gone → delete row (offline detection has
  already requeued their runs).
- `docker_workers_autoscale_loop()` — 30s tick (configurable
  `docker_autoscale_tick_seconds`), runs where a daemon is reachable:
  worker (`worker_main`) and inline-API. Wraps
  snapshot → `plan_scaling` → apply, per agent pool with autoscale enabled
  or ≥1 docker-managed runner (reconcile always). One-shot daemon probe with
  a logged, non-fatal skip when unavailable. All container calls via
  `run_in_executor`.

Container labels: every spawned container carries
`labels={"nodyra.managed": "true", "nodyra.pool": pool_id,
"nodyra.runner": runner_id}` — reconcile keys off labels, never name parsing.

### Shared token mint (small refactor)

Extract the body of `create_registration_token` into
`mint_runner_registration(session, pool, *, name, max_concurrent_runs,
capabilities) -> tuple[Runner, str, datetime]` in
`app/services/runner_tokens.py`; router and `spawn_docker_runner` both call
it. Router behaviour unchanged (regression-covered by existing tests).

### API endpoints (`runner_pools.py`, permission `runner_pool:write`)

- `POST /runner-pools/{pool_id}/docker-runners` → spawn one; body
  `{"name"?: str}`; 400 if pool is not provider `agent`; 502 with a clean
  message when the daemon is unreachable. Returns `RunnerInfo`.
- `DELETE /runner-pools/{pool_id}/docker-runners/{runner_id}?force=` →
  drain-aware remove (409 when busy and not forced).
- Autoscale + envelope settings ride the existing `PATCH
  /runner-pools/{pool_id}` (`provider_config`), with server-side validation:
  `0 <= min <= max <= 32`, `cpu ∈ (0, sandbox_max_cpu]`,
  `memory_mb ∈ [128, sandbox_max_memory_mb]`, `idle_seconds >= 30`,
  `docker_host` must be `""` or start with `tcp://`, `ssh://`,
  `unix://`, `npipe://`.

### Agent-side sandbox path (`packages/runner`)

New module `nodyra_runner_agent/sandbox_exec.py`:

- Port of the hardened sibling-spawn pattern (hardening kwargs + stream
  demuxer are duplicated into the agent package — the agent cannot import
  `app.*`; keep the copies small and cross-referenced in comments).
- `run_workflow_sandboxed(...)` mirrors `run_workflow_subprocess`'s
  interface (same event protocol) but executes in a disposable hardened
  container: cap_drop ALL, no-new-privileges, read-only rootfs, tmpfs /tmp,
  non-root, cpu/mem/pids from the run's env payload sandbox overrides,
  network none by default.
- Selection in `agent.py`: if the assigned run payload carries
  `sandbox_required: true` → sandbox path (error the run with a clear
  message if the agent has no daemon); the API includes that flag for runs
  whose resolved execution mode is sandboxed when dispatching to an agent
  whose capabilities advertise `sandbox: true`. Dispatch-side guard: agent
  provider refuses to assign sandbox-required runs to non-sandbox runners
  (label routing normally prevents this; the guard is defence-in-depth).

### Frontend (RunnerPoolsPage)

- Agent-pool detail: **[Add Docker Runner]** button (spinner + toast on
  daemon errors), runner rows show a `docker` badge and a remove action
  (confirm dialog; force option when busy).
- **Autoscale card:** enable toggle, min/max runners, CPU, memory, idle
  scale-down seconds, daemon choice (Local / Remote + `docker_host` input),
  and the **Sandboxed execution support** checkbox with the DooD trust
  warning inline ("daemon access is root-equivalent on the daemon host;
  runs execute in hardened per-run containers").
- Part 1 card as described above.

### Security

- Endpoints require `runner_pool:write` (same trust as SSH onboarding).
- `docker_host` scheme allowlist (above) — rejects arbitrary URLs.
- Registration tokens: unchanged trust model (signed, runner-bound,
  revocable); the container env carries the token exactly as the manual
  join command does today.
- Sandbox checkbox mounts the socket **into the runner container only**;
  run code executes in hardened siblings. Checkbox copy carries the warning;
  docs updated (workers.md §2).
- Autoscaler never runs untrusted input: pool config is operator-written,
  validated at PATCH time and re-clamped at spawn.

### Testing (all new logic)

- `apps/api/tests/test_docker_workers.py`
  - `plan_scaling`: table-driven cases — scale up on backlog, no-op when
    capacity free, respect max, scale down after idle, respect min, never
    remove busy, clamp min>max, disabled pools untouched, one action per
    pool per tick.
  - spawn/remove/reconcile with a `FakeDockerClient` (records
    containers.run kwargs; asserts resource caps, labels, env injection,
    socket mount present iff sandbox, restart policy).
  - endpoint tests: permissions, 400 non-agent pool, 409 busy without
    force, config validation bounds, daemon-unreachable → 502 not 500.
  - `mint_runner_registration` parity: token decodes, binds runner/pool/org
    (reuses existing token tests as the oracle).
- `packages/runner/tests/test_sandbox_exec.py` — hardening kwargs floor
  (cap_drop ALL, read_only, no-new-privileges, network none), event
  round-trip with a fake container socket, refusal path without daemon.
- Frontend: `RunnerPoolsPage` additions covered by component tests
  (card renders, POST payload shape, checkbox warning text).

### Docs

- `docs/deployment/workers.md`: new "One-click Docker runners" section
  (button, autoscale bounds, local vs remote daemon, sandbox checkbox +
  trust note).

### Rollout / compatibility

- No migrations; all config in JSON columns → forward/backward compatible.
- Feature degrades cleanly: no daemon → button returns a clear 502, the
  autoscale loop idles with one log line.
- `IMAGE_SCHEMA_VERSION` bump strategy shared with the sandbox images (the
  agent image tag embeds its own schema constant so recipe changes rebuild).
