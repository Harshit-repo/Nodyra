# Sandboxed Run Execution (MT Phase D, slice 1) — Design

**Date:** 2026-06-12
**Status:** approved direction, pending spec review
**Relates to:** `docs/multi-tenancy-plan.md` Phase D, `docs/production-readiness-audit.md` SEC-1b

## 1. Problem

In the standard Noodle deployment (`deploy/docker-compose.yml`), the single
`worker` service executes every workflow run as a **subprocess inside its own
container** (`USE_SUBPROCESS_RUNNER=true` → `runtime_pool.py` warm per-env
subprocess pool). All tenants share one kernel namespace; the only boundary
between two tenants' arbitrary Python code is a Unix process. That is
acceptable for single-tenant self-hosting (users run their own code on their
own machine) and unacceptable for a hosted multi-tenant cloud.

Runner pools (`RunnerPool.provider = docker|kubernetes|agent`) already provide
container-per-run execution, but they are **opt-in, per-org configuration** —
not the platform's default path. This design makes disposable, hardened,
per-run containers the platform-level execution mode, reusing the runner-pool
container machinery.

## 2. Goals

1. A **sandbox execution mode** in which the worker runs each workflow run in
   a short-lived, hardened container instead of a subprocess.
2. **Isolation tiers selected by host capability**, one codepath:
   `runc` (hardened container, works everywhere Docker runs, incl. Docker
   Desktop on Windows/macOS) → `runsc` (gVisor, Linux hosts incl. WSL2) →
   `kata` (Kata Containers + microVM, Linux+KVM hosts, future).
3. **Warm container pool per `(org, environment)`** so steady traffic rarely
   pays container start-up latency. Reuse within one tenant only, never across.
4. **Multi-tenancy requires the sandbox**: when `multi_tenancy_enabled=true`,
   runs that would fall through to the shared subprocess pool are refused.
5. Single-tenant self-hosting is unaffected: subprocess mode remains the
   default; sandbox mode is opt-in (`auto`) or enforced (`required`).

## 3. Non-goals

- **Direct Firecracker orchestration** (VM pool manager, rootfs images,
  vsock). Kata Containers delivers microVMs later as a `runtime=` value
  through the same Docker API; no bespoke VMM code.
- **gVisor on Windows/macOS desktops.** runsc is Linux-only. Docker Desktop
  already runs containers inside a Linux VM, which is a stronger host boundary
  than gVisor adds for a single-user machine.
- **License-gating multi-tenancy.** That belongs to the licensing project
  (`docs/licensing-plan.md`); when it lands, MT becomes
  `has_feature("multi_tenancy")`. Nothing here blocks it.
- **Replacing runner pools.** Per-org dedicated pools (X4) keep working and
  satisfy `dedicated_pool` isolation as today.
- **Per-node sandboxing.** The unit of isolation is the run (plus its
  sub-workflows and loop iterations, which already execute inside the parent
  run's runtime by construction).

## 4. Architecture

### 4.1 Where it plugs in

The executor seam already exists: `app/services/executors/base.py` defines
`RunExecutionContext → RunOutcome`, with `LocalExecutor` (warm subprocess
pool) and `RemoteExecutor` (runner pools) implementations. We add:

- **`app/services/executors/sandbox.py` — `SandboxExecutor`**: same interface
  as `LocalExecutor`, but dispatches to a per-`(org, env)` warm container
  pool instead of the subprocess pool.
- **`app/services/container_runtime.py`** (extracted from
  `providers/docker.py`): the shared container driver — `ensure_docker_image`
  (per-env image build, RD-2 validation) and the attach-socket
  `noodle_runtime` JSON protocol loop. Both the runner-pool docker provider
  and `SandboxExecutor` consume this module; no duplicated protocol code.
- **`app/services/sandbox_pool.py`**: warm container pool, mirroring
  `runtime_pool.py`'s shape but holding containers keyed by
  `(org_id, environment_id)`.

Executor selection happens where `LocalExecutor` is chosen today (the
`runner.py` dispatch path): if sandbox mode is active and the run resolved to
no runner pool, use `SandboxExecutor`; otherwise unchanged.

### 4.2 Run lifecycle in sandbox mode

```
run queued → worker picks it up
  → warm container for (org_id, env_id) available?
      yes → send run message over its stdin (≈0ms overhead)
      no  → docker run noodle-env:{env}-{hash}
            (runtime per tier probe, hardened flags)  ≈100–500ms
  → stream noodle_runtime events over stdout → run finishes
  → container healthy and pool not full? → return to warm pool
    else → remove container
```

The container image is the existing `noodle-env:{env_id}-{packages_hash}`
built by `ensure_docker_image`; image builds stay off the hot path (rebuilt
only when an environment's packages change). Credentials and run payload
travel through the run message on stdin, exactly as the docker runner-pool
provider does today — nothing secret is baked into images.

### 4.3 Isolation tier probe

`container_runtime.detect_runtime()` runs once at worker startup:

1. If `sandbox_runtime` setting is an explicit value (`runc|runsc|kata`), use
   it; fail fast at startup if the Docker daemon does not list that runtime.
2. On `auto`: query `docker info` runtimes; prefer `kata` > `runsc` > `runc`.

The chosen tier is logged at startup and exposed on the existing health
endpoint so operators can verify what they are actually getting.

### 4.4 Container hardening (applies to every spawned run container)

These flags go into the shared `container_runtime` spawn call, so the
runner-pool docker provider inherits them too:

| Flag | Value | Why |
|---|---|---|
| `cap_drop` | `ALL` | workflow code needs no Linux capabilities |
| `security_opt` | `no-new-privileges` | blocks setuid escalation |
| `read_only` rootfs | true, + tmpfs `/tmp` (size-capped) | no persistence inside the sandbox |
| `mem_limit` | default 1g | configurable; per-org override via `org_limits` later |
| `nano_cpus` | default 1.0 CPU | same |
| `pids_limit` | default 256 | fork-bomb containment |
| `network` | dedicated bridge `noodle-sandbox` | see 4.5 |
| user | non-root (add `USER` to the generated env image) | container breakout surface reduction |

Defaults live in settings (`sandbox_mem_limit`, `sandbox_cpu_limit`,
`sandbox_pids_limit`, `sandbox_tmpfs_size`); the existing run timeout
(`workflow_run_timeout_seconds`) already bounds the protocol read loop, and
the container is force-removed in the `finally` path as today.

### 4.5 Networking

Sandbox containers attach to a dedicated bridge network
(`noodle-sandbox`, created on demand), **not** the compose project network.
Workflow code must be able to reach the internet (HTTP nodes, AI providers)
but must not reach `postgres`, `redis`, or `minio`, which it can today from
the worker's subprocess. The run protocol needs no inbound networking —
events travel over the attach socket. Operators who need stricter egress
(e.g. blocking cloud metadata endpoints, RFC1918 ranges) can point
`sandbox_network` at their own pre-configured network; shipping an iptables
egress filter is out of scope for this slice and noted in the rollout doc.

### 4.6 Deployment topologies

**Compose / self-hosted (this slice):** Docker-out-of-Docker. The `worker`
service mounts the host Docker socket (`/var/run/docker.sock`) when sandbox
mode is enabled; run containers are siblings on the host daemon. The compose
file gains a commented-out variant documenting this, including the honest
caveat: socket access is root-equivalent on the host, which is acceptable
because the worker is trusted control-plane code and tenant code no longer
executes inside it — moving tenant code out of the socket-holding process is
the entire point. On Windows/macOS this works unchanged against Docker
Desktop's daemon (tier: hardened `runc`, already VM-isolated from the host).
A Linux host (or a WSL2 distro) with gVisor installed gets `runsc`
automatically via the probe.

**Kubernetes / cloud (follow-up slice):** the worker creates per-run **Jobs
with `runtimeClassName: gvisor`** (or `kata`) instead of talking to a Docker
socket. The executor seam and protocol are identical; only the spawn/attach
mechanics differ. Designed for, not built in, this slice.

### 4.7 Mode selection and the MT gate

New setting `execution_sandbox: off | auto | required` (default `off`):

- `off` — today's behaviour (subprocess pool).
- `auto` — sandbox if a usable Docker daemon is detected, otherwise fall back
  to subprocess **with a startup warning**. Only legal when
  `multi_tenancy_enabled=false`.
- `required` — refuse to start the worker if no usable daemon/runtime.

Enforcement: when `multi_tenancy_enabled=true`, the effective mode is forced
to `required` (config validation rejects `off`/`auto` at startup). The
existing X4 chokepoint in `runner.start_run` keeps handling per-org
`dedicated_pool` routing; the sandbox replaces the *shared* fallback path
(precedence chain step 4: "None → in-process runtime pool" becomes "None →
sandbox pool").

### 4.8 Warm pool semantics

- Keyed by `(org_id, environment_id)`; max idle containers per key
  (`sandbox_warm_per_key`, default 1) and a global cap
  (`sandbox_warm_total`, default 8), LRU-evicted.
- A container is returned to the pool only after a clean `result` event;
  any protocol error, timeout, or non-zero exit removes it.
- Idle TTL (`sandbox_warm_ttl_seconds`, default 300) so quiet tenants don't
  pin memory.
- Recycle after N runs (`sandbox_max_runs_per_container`, default 50) to
  bound state accumulation in the runtime process.
- In single-tenant deployments `org_id` is the single default org, so the
  pool degrades gracefully to "warm pool per environment" — same shape as
  today's subprocess pool.

## 5. Error handling

- **Docker daemon unreachable:** `required` → worker exits at startup with a
  clear message; `auto` → warn and use subprocess; mid-flight daemon loss →
  the run fails with `run_error` (same contract as the docker provider today)
  and the warm pool is flushed.
- **Container start failure** (bad runtime, missing image build deps): run
  fails with the daemon's error surfaced in `run_error`; never silently
  downgraded to subprocess.
- **Run timeout:** existing `workflow_run_timeout_seconds` bounds the read
  loop; on timeout the container is force-removed — this finally gives MT a
  real per-run kill, resolving the audit note that "real per-run kill needs
  OS-level isolation (SEC-1b)".
- **Cancel:** `SandboxExecutor.cancel()` force-removes the run's container,
  which is a harder guarantee than the subprocess path's task-cancel.

## 6. Testing

- **Unit (no Docker):** `SandboxExecutor` and `sandbox_pool` against a fake
  Docker client (the protocol loop is already exercisable in-memory); tier
  probe with mocked `docker info`; config validation (MT forces `required`,
  `auto` rejected under MT); hardening flags asserted on the spawn call;
  warm-pool reuse/eviction/recycle rules; cross-org reuse is impossible by
  construction (keying) — asserted anyway.
- **Integration (requires Docker, env-gated like existing docker tests):**
  one end-to-end run through a real container in sandbox mode; cancel
  force-removes; dirty exit not returned to pool.
- **Existing suites:** default stays `execution_sandbox=off`, so the current
  test matrix is untouched.

## 7. Phasing

1. **This slice:** extract `container_runtime.py`, hardening flags (shared
   with the docker runner-pool provider), tier probe, `SandboxExecutor` +
   warm pool, mode setting + MT enforcement, compose documentation.
2. **Next:** Kubernetes Jobs + RuntimeClass executor for the cloud
   deployment (helm chart values for `runtimeClassName`).
3. **Later, evidence-driven:** Kata/Firecracker tier on KVM hosts; egress
   filtering hardening; per-org resource limits sourced from `org_limits`.
