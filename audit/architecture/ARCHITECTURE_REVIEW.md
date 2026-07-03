# Architecture Review (Synthesis)

Independent audit, 2026-06-16. Synthesizes all backend/frontend/infra area reviews
plus `NODYRA_REPOSITORY_MAP.md`.

## System shape
- **Monorepo, uv workspace**: `packages/core` (engine + SDK + models),
  `packages/nodes` (~86 modules / 34k LOC node library), `packages/runtime`
  (isolated exec server), `packages/runner` (remote-runner seam), `apps/api`
  (FastAPI control plane), `apps/web` (React/Vite builder).
- **Control plane / execution plane split**: API can run `DISPATCH_ROLE` =
  inline/worker/control/disabled; scheduler is leader-elected; runs flow through a
  durable Postgres queue to standalone dispatch workers; code executes in a warm
  subprocess pool or the isolated runtime, with a remote-runner seam for future
  per-pool sandboxing.

## Architectural strengths (verified)
- **Clean plane separation** with explicit role flags and a topology validator
  (`dispatch_topology_errors`) — you can scale workers independently of the API,
  and misconfigured topologies fail fast at startup.
- **Static-vs-exec node discovery split** (08): the single best architectural
  decision — API builds manifests via AST without executing uploaded code; exec is
  confined to the execution plane. This is what makes a "code-first" plugin model
  safe to expose over an API.
- **Durable queue over Postgres** (07): no extra broker dependency; `SKIP LOCKED`
  leasing, fairness, dead-letter/replay, reaper. Correct build-vs-buy call.
- **Engine correctness** (06): Kahn toposort + cycle detection, bounded dynamic
  fan-out, cancellation hygiene — the hard concurrency problems are handled.
- **Security architecture** (05): envelope encryption, middleware auth/CSRF gates,
  RLS-backed multi-tenancy — cross-cutting concerns live in middleware/session
  layers, not sprinkled per-route.
- **Frontend** (01): sliced Zustand store, server/client state separation
  (TanStack Query vs Zustand), atomic selectors — scales as a single large SPA.
- **Observability seam** (10): OTel with cross-process W3C trace context parenting
  worker spans on the enqueueing request — zero-overhead when disabled.

## Architectural findings / tensions
| ID | Severity | Tension | Direction |
|---|---|---|---|
| SAFE-3/NODE-1 | Medium | In-process exec path co-existed with the sandbox model; safety depended on a config flag | **Fixed** — startup guard makes sandbox/subprocess isolation a non-overridable invariant under MT/sandbox |
| SAFE-2 | Medium | SSRF/egress control is at deploy-time, not in the run sandbox | **Partly fixed** — dedicated `sandbox_network` now required under MT; internet-egress allowlist is the follow-up |
| ENGINE-1 | Low-Med | Concurrency bound exists for dynamic fan-out but not static DAG width | Wire `max_node_concurrency` uniformly |
| INFRA-2/3 | Medium | Containers run as root; `COPY . .` before `uv sync` defeats layer cache | Add non-root `USER`; reorder Dockerfile for cache |
| DB-2 | Low | 51 migrations at 0.0.1 | Squash baseline pre-OSS |
| NODE-2 | Low | No node-schema/version migration story | Define before 3rd-party node API |
| AUTH-2 | Medium | Stateless tokens → no revocation | Add key-version/jti if forced-logout needed |

## Cross-cutting recommendation
The architecture is sound; the recurring theme across the Medium findings was
**"make the safe path the only path"**: several safety properties (sandbox use,
egress control, secret enforcement) depended on configuration being set correctly.
AUTH-1 was converted from a warning into a hard startup guard, and this pass the
**same pattern was applied to SAFE-3/NODE-1 and SAFE-2** (`enforce_sandbox_policy`
now refuses boot on the in-process runner under MT/sandbox and requires a dedicated
sandbox network). A production deployment can no longer silently run tenant code in
the host process or on the infra network. The remaining instance of this theme is
internet-egress allowlisting (SAFE-2 follow-up).

## Verdict
This is a **well-architected system**, not a prototype: the plane split, the
static/exec node discovery boundary, the durable queue, and the correctness of the
engine are all principal-level decisions. There is no recommended rewrite. The
work ahead is hardening configuration-dependent safety into enforced invariants
and operational polish (metrics, non-root containers, migration squash) — evolution
within the existing design, not a redesign.
