# Remote Runner, Deployments & Environments — Production-Grade Redesign

**Date:** 2026-06-13
**Branch:** `feat/arch-program-phase5`
**Status:** Approved direction (Runner Pools mockup signed off); implementing.

## Problem

The remote runner + deployments surfaces are functional but not production-grade.
A live hands-on test (registered a real `noodle-runner` agent against the local
Docker stack, created deployments, ran them) surfaced three architectural
blockers, several bugs, and pervasive UX gaps.

### Architectural blockers
1. **Agent/k8s pools can't dispatch in the split topology.** The API runs
   `DISPATCH_ROLE=disabled`; the worker leases only `{local, docker}`. Agent
   runs require the leasing process to hold the runner's in-memory WebSocket
   (`remote_dispatch._agents`), which terminates on the API. Result: agent runs
   sit `queued` forever with `queue_reason=dispatch_disabled`, no warning.
2. **`noodle-runner` cannot build envs on a clean machine.**
   `env_manager.build_env` runs `uv pip install noodle-runtime noodle-nodes
   noodle-core` — none are on PyPI, so every remote env build fails outside the
   monorepo.
3. **env→pool resolution ignores implicit-Global workflows.** `runner.start_run`
   only consults the env's `runner_pool_id` when `workflow.environment_id` is
   explicitly set; workflows on the implicit Global env never route to the bound
   pool, contradicting the Environments UI copy.

### Bugs
- Install snippet uses `window.location.origin` (web origin) as `--api-url`;
  wrong for any split web/API deployment.
- Toast notifications overlay modal footer buttons and swallow clicks (no
  request fired, no error) — observed repeatedly during testing.
- Deployments can pin and activate versions whose graph is empty / trigger-less;
  the only symptom is a fire-time 400. A scheduled deployment fails silently
  every interval.
- `noodle-runner` agent crashes on Windows (`cp1252` UnicodeEncodeError on uv's
  box-drawing output), masking the real env-build error.

### UX gaps
- No live status: pools show stale "online/never seen"; deployments show no
  next-fire / last-run / health; remote run node-rows appear only on completion.
- Version-pin and error-workflow are raw UUID paste-boxes.
- Deployments list has no run history (API exists), no health, no env/pool
  override, no `error_alerts` surface.
- Runner rows are cramped one-liners; no per-runner history, no pool queue
  depth, no "what's blocking dispatch" signal.
- Multi-tenancy is enforced in the DB but invisible in the UI (no org ownership,
  no per-org fairness on shared pools).

## Architecture decision — agent/k8s dispatch

**Agent/k8s dispatch becomes a capability of the control-plane API**, co-located
with the WebSocket it already terminates. Not the worker (stateless N replicas
can't own a pinned socket), not a new container (WS-forwarding is ~0 CPU).

- Worker keeps `{local, docker}` — heavy, horizontally scalable.
- API gains a dispatch capability for `{agent, kubernetes}` only, driven by
  `DISPATCH_ROLE`. WS-terminating providers lease where their socket lives.
- **Surface it:** a pool with runners online but no process leasing its provider
  shows "⚠ No dispatcher reachable — runs will queue."
- Documented future escape hatch for multi-replica WS scale: Redis-broker the
  `_agents` registry (publish assignment to `runner:{id}` channel; the
  socket-holding replica forwards). Out of scope now.

## Slices

Each slice is its own spec→plan→build cycle. Visual language (fleet summary
bar, per-card health strip, status dots/pills, recent-run sparklines,
dispatcher-reachability signal) is shared across all three.

### Slice 1 — Remote Runner (this implementation)
**Backend**
- `A1` `DISPATCH_ROLE` gains an agent/k8s dispatch capability runnable on the
  API process; queue `providers` filter extended so the WS-terminating process
  leases `{agent, kubernetes}` entries. New role value (e.g. `control` =
  control-plane + agent/k8s dispatch) so the default split stack works with
  agent pools out of the box.
- `A2` Self-hosted wheel index: API serves `noodle-core`, `noodle-runtime`,
  `noodle-nodes` wheels (built from the monorepo) at a PEP-503-ish endpoint;
  `env_manager` installs with `--find-links`/`--index-url` pointing at the API.
- `A3` env→pool resolution consults the env's pool for implicit-Global
  workflows too (resolve the effective env even when `environment_id` is null).
- `A4` Registration/install snippet uses the API's public URL
  (`settings.public_api_url` / request base), not the web origin.
- `A5` Agent: force UTF-8 safe logging on Windows; capture and report the real
  env-build error back to the run (`run_error` with build log tail) instead of
  crashing the log handler.
- `A6` Dispatcher-reachability signal: API exposes, per pool, whether a process
  is currently leasing its provider (+ queue depth, oldest-queued, capacity
  used, 24h success). Feeds the pool health strip.

**Frontend (matches approved mockup)**
- Fleet summary bar (runners online, queue depth, in-flight, dispatchers).
- Pool card health strip (capacity used, pool queue depth, 24h success,
  dispatcher reachable + latency) and amber "no dispatcher" banner with fix.
- Runner table: status dot + last-seen, label pills, capacity bar, cached-env
  size, recent-run sparkline, per-runner actions.
- Org ownership badge; live polling (reuse env-page polling pattern) or WS.
- Correct install snippet; "mint token" no longer pre-creates a phantom
  online-counting runner row.

### Slice 2 — Deployments
Version-pin + error-workflow dropdowns (v1/v2/latest + unpublished-drift
indicator), per-deployment run history + health sparkline + next-fire/last-run,
env/pool override, `error_alerts` UI, create/activate validation (empty /
trigger-less graph), fix toast click-swallow (global), redesigned list with the
shared visual language.

### Slice 3 — Environments
Live + truthful execution-target with dispatcher health, build status as
first-class (live tail / error+retry, surfaced real build error), RAM budget
bar, org/global ownership + usage count, packages drawer install-source +
`packages_hash`.

## Testing
- Backend: pytest for A1 (role leases correct providers), A3 (resolution for
  implicit-Global), A4 (URL), A6 (health endpoint shape). A2 covered by an
  end-to-end "register runner on clean data dir → build env from API index →
  run succeeds" integration check where feasible.
- Frontend: vitest for the new status/health components; typecheck + build.
- Manual: re-run the live test (fresh `NOODLE_RUNNER_DATA`) and confirm a remote
  run completes without hand-seeding the venv, with the default split stack.

## Out of scope (now)
Redis-brokered multi-replica agent WS; label-aware dispatch; cross-region
runner affinity.
