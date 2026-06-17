# Docker Environment Backend + MT Sandbox Scope

> **Date:** 2026-06-13  
> **Status:** scoped for implementation  
> **Related specs:** `docs/superpowers/specs/2026-06-05-multi-backend-environments-design.md`, `docs/superpowers/specs/2026-06-12-sandboxed-run-execution-design.md`

## Goal

Add `backend="docker"` as a first-class Environment backend now that the
multi-tenant sandbox exists. Docker envs let workflows run in isolated
containers with system dependencies and custom images, while MT keeps the
control-plane/data-plane boundary intact across tenants.

## Current State

Already in place:

- `Environment.backend` and `backend_config` columns exist.
- `venv`, `conda`, and `pixi` backends are wired through
  `app.services.backends.get_backend`.
- The Environment UI already shows a Docker badge, but creation is capped at
  `venv | conda | pixi` and displays "Docker backend coming soon".
- Shared container machinery exists in `app/services/container_runtime.py`:
  image tags, package/Python validation, base image build, Docker stream
  demuxing, runtime probing, sandbox network creation, and hardened spawn args.
- `SandboxExecutor` + `SandboxPool` already execute runs in warm containers
  keyed by `(org_id, environment_id)`.
- MT startup policy requires `EXECUTION_SANDBOX=required` when
  `MULTI_TENANCY_ENABLED=true` unless the operator explicitly disables strict
  policy.
- Each new org gets its own default Environment row, so env IDs are not shared
  across orgs.

Missing:

- No `apps/api/app/services/backends/docker.py`.
- `get_backend()` rejects `backend="docker"`.
- `ensure_environment_ready()` assumes every backend returns a host Python path.
  Docker envs do not.
- `build_env_payload()` only carries Python version and package list; it does
  not include backend/mode/image metadata.
- `container_runtime.ensure_docker_image()` only supports managed
  Python+packages images, not custom Dockerfile or prebuilt image modes.
- The runner selection path does not force Docker envs onto a container
  executor when the sandbox is off.
- `/environments/backends` currently checks `shutil.which("docker")`; execution
  uses the Docker SDK/daemon, so availability should be a daemon ping.
- Frontend create/edit screens have no Docker configuration fields.

## Key Decision

A Docker env is not a local Python environment. It cannot safely fall through to
`LocalExecutor` / `RuntimePool`, because there is no host-side `python_path`.

Execution rule:

1. If a run resolves to a remote runner pool, dispatch there as today.
2. Else if `SandboxExecutor` is active, run through the platform sandbox.
3. Else if the selected environment has `backend="docker"`, fail with a clear
   error: Docker environments require `EXECUTION_SANDBOX=auto|required` or a
   Docker/Kubernetes runner pool.
4. Else use the local subprocess executor.

This means Docker envs work for single-tenant installs by turning on
`EXECUTION_SANDBOX=auto|required`, and work for MT automatically because MT
already requires `required`.

## MT Model

Do not create one shared Docker environment row used by every tenant.

The safe shape is shared Docker infrastructure, with tenant-scoped environment
rows and tenant-scoped warm containers:

- Env rows stay org-scoped.
- Warm containers stay keyed by `(org_id, environment_id)`.
- Image tags should keep `env_id` and a content hash; adding `org_id` as a
  label is useful for cleanup/audit, but env IDs are already globally unique.
- No Docker socket is mounted into run containers. Only the trusted worker has
  Docker daemon access.
- Secrets continue to travel only in the run message and are never baked into
  images.
- Resource limits start with the global sandbox settings
  (`SANDBOX_MEM_LIMIT`, `SANDBOX_CPU_LIMIT`, `SANDBOX_PIDS_LIMIT`) and later
  move to per-org overrides via `OrgSettings`.

"Provide across all tenants" should mean every tenant can create/use a Docker
env in their org, backed by the same platform sandbox, not that tenants share a
mutable env or warm runtime.

## Backend Config

Keep the already-planned three Docker modes, but ship them in two slices.

Slice 1: managed Docker envs.

```json
{
  "mode": "managed",
  "base_image": "python:3.12-slim"
}
```

`packages` is active in managed mode. Noodle builds a runtime image from the
workspace packages plus the env's packages. This is closest to current
`ensure_docker_image()` and should ship first.

Slice 2: custom image modes.

```json
{
  "mode": "dockerfile",
  "dockerfile": "FROM python:3.12-slim\nRUN apt-get update && apt-get install -y ghostscript\n..."
}
```

```json
{
  "mode": "image",
  "image_name": "registry.example.com/team/data-env:v3",
  "pull_policy": "if_missing"
}
```

For `dockerfile` and `image`, the image contract must be explicit: the final
image must run `python -u -m noodle_runtime`, include `noodle_core`,
`noodle_nodes`, and `noodle_runtime`, and emit the initial `ready` event. The
build step should validate that contract by starting the image and waiting for
`{"type":"ready"}` before marking the Environment `ready`.

## Required Code Changes

### API backend

- Create `app/services/backends/docker.py`.
  - `build(env)` builds/pulls/validates the Docker image.
  - `python_path(env_id)` returns `None`.
  - `destroy(env_id)` removes Noodle-owned images for that env when safe.
- Wire Docker into `app/services/backends/__init__.py`.
- Validate `EnvironmentCreate.backend` / `EnvironmentUpdate.backend_config`:
  - accepted backends: `venv | conda | pixi | docker`;
  - accepted Docker modes: `managed | dockerfile | image`;
  - reject missing `dockerfile` or `image_name`;
  - cap Dockerfile size.
- Make `/environments/backends` probe Docker through the SDK daemon path, not
  just CLI presence.

### Runtime/container layer

- Extend `build_env_payload()` to include:
  - `backend`;
  - sanitized `backend_config`;
  - `org_id`;
  - a content hash that includes packages, Python version, backend mode, and
    Dockerfile/image config where relevant.
- Update `_build_env_payload_for_run()` to load `backend` and `backend_config`.
- Update `image_tag_for()` / `ensure_docker_image()`:
  - managed mode: current base+packages build path;
  - dockerfile mode: build supplied Dockerfile under a content-hash tag;
  - image mode: pull/check configured image and use it as the runtime tag.
- Add Docker labels to Noodle-created images/containers:
  - `noodle.env_id`;
  - `noodle.org_id`;
  - `noodle.backend=docker|managed`;
  - `noodle.schema_version`.

### Runner selection

- Load the selected Environment backend before choosing local vs sandbox.
- If backend is Docker and there is no remote pool and sandbox is inactive,
  fail the run before dispatch with a user-facing error.
- If backend is Docker and sandbox is active, dispatch to `SandboxExecutor`.
- Keep dedicated-pool org behavior: an org with
  `execution_isolation="dedicated_pool"` still requires its own Docker/K8s
  runner pool. Shared-isolation orgs can use the platform sandbox.

### Frontend

- Change `BackendTab` to `venv | conda | pixi | docker`.
- Replace "Docker backend coming soon" with a real Docker tab.
- Managed mode fields:
  - base image;
  - package list remains editable.
- Dockerfile mode fields:
  - Dockerfile editor/textarea;
  - package drawer disabled or marked "managed by Dockerfile".
- Image mode fields:
  - image name;
  - optional pull policy.
- Use `api.listBackends()` to disable Docker with a daemon-specific reason.
- Adjust env cards:
  - show "container sandbox" instead of "local (in-process)" when a Docker env
    has no runner pool but the platform sandbox is active;
  - hide RAM estimate for Docker envs until container-level telemetry exists.

### Deployment/docs

- Document that Docker envs need Docker daemon access in the worker process:
  either `EXECUTION_SANDBOX=auto|required` with docker.sock mounted, or a
  Docker/Kubernetes runner pool.
- Clarify the socket trust boundary: worker is trusted control plane; tenant
  code executes in sibling hardened containers with no socket mount.
- Add Helm values for the future K8s runtime-class path; compose can use the
  existing commented sandbox section.

## Test Plan

Backend/unit:

- `get_backend("docker")` returns `DockerBackend`.
- DockerBackend managed mode calls `ensure_docker_image()` and returns ready.
- DockerBackend image mode validates missing `image_name`.
- DockerBackend dockerfile mode hashes Dockerfile contents into the image tag.
- `python_path()` returns `None`.
- `ensure_environment_ready()` produces a clear error if called for a Docker env.

Container runtime:

- managed mode still produces the existing base-image Dockerfile.
- dockerfile mode builds the supplied Dockerfile under a Noodle tag.
- image mode pulls/checks image without rebuilding.
- content hash changes when Dockerfile/config changes.
- Noodle labels are passed to image/container creation.

Runner/MT:

- Docker env + sandbox active dispatches to `SandboxExecutor`.
- Docker env + no sandbox + no remote pool fails before local subprocess.
- MT + shared org + Docker env uses platform sandbox.
- MT + dedicated_pool org still refuses platform fallback unless an org-owned
  Docker/K8s pool is assigned.
- Warm reuse never crosses orgs for Docker envs.

API/UI:

- Create Docker managed env.
- Reject invalid Docker config.
- `/environments/backends` reports Docker unavailable when SDK ping fails.
- Frontend typecheck/build with Docker tab enabled.

Integration, env-gated:

- With `NOODLE_SANDBOX_IT=1`, create a managed Docker env and run a workflow in
  a real container.
- Rebuild after package change uses a new image hash and does not reuse stale
  warm containers.

## Recommended Implementation Order

- [ ] **Task 1:** Backend shape and validation: `DockerBackend` managed mode,
  dispatcher wiring, schema validation, unit tests.
- [ ] **Task 2:** Env payload/runtime changes: backend-aware content hash,
  labels, Docker SDK daemon availability, managed-image reuse.
- [ ] **Task 3:** Runner selection safety: force Docker envs to sandbox/remote
  and add MT/dedicated-pool tests.
- [ ] **Task 4:** Frontend Docker managed-mode UI.
- [ ] **Task 5:** Real-daemon managed Docker env integration test.
- [ ] **Task 6:** Custom Dockerfile mode.
- [ ] **Task 7:** Prebuilt image mode and pull policy.
- [ ] **Task 8:** Docs and operator cleanup guidance for stale images.

## Open Questions

- Do we want org owners to create Dockerfile/image-mode envs in hosted MT, or
  restrict those modes to admins until image scanning/signing exists?
- Should image mode require immutable digests in hosted MT, with mutable tags
  allowed only in self-hosted?
- Do we need per-org sandbox CPU/memory overrides before launch, or are global
  defaults acceptable for the first Docker-env release?
- Should the default org template include a Docker managed env automatically
  when the daemon is available, or should Docker remain opt-in per org?
