# Testing Strategy Review

Independent review, 2026-06-16. Scope: `apps/api/tests/` (145 test files,
~40k LOC), `packages/*/tests/`, `apps/web/src/**/*.test.ts(x)` (40 files),
`.github/workflows/ci.yml`, `apps/api/tests/conftest.py`.

## What is solid (verified)
- **Volume and breadth**: 145 backend test modules (~40k LOC of tests) and 40
  frontend test files — a genuinely large suite for a project at this version,
  and roughly 1:1 test-to-source LOC on the API.
- **Critical surfaces are covered** (verified by content, not filename):
  - **Execution engine**: topological sort / cycle detection / loops —
    `test_engine*`, `test_loops`, `test_cross_org_loops` (cycle, toposort refs).
  - **Queue/worker**: `test_run_queue`, `test_ops` cover retry/backoff,
    dead-letter/reclaim, drain, leader election (`test_leader_election`).
  - **Multi-tenant isolation**: `test_org_isolation_enforcement`,
    `test_org_artifacts`, `test_cross_org_loops` — cross-org leakage is explicitly
    tested, not assumed.
  - **Auth/crypto**: `test_crypto` (envelope encryption, token tamper/signature),
    `test_cookie_auth` (dual-mode + CSRF), `test_licensing`.
  - **Sandbox**: 5 `test_*sandbox*` files plus `test_expr*` for the expression AST
    sandbox.
- **CI is multi-lane and now lockfile-enforcing**: `.github/workflows/ci.yml`
  runs the Python lane with `uv sync --locked` + `uv lock --check` (added under
  INFRA-1) and an authoritative **Postgres** `alembic check` — so schema drift and
  dependency drift both fail CI, not just local SQLite.
- **conftest hygiene improved this pass**: deterministic non-default
  `secret_key`, autouse resets for the `queue_drain` global and the module-global
  `queue._wakeup` Event — removing two order-dependent flakes (TEST-1/TEST-2).
- **Tests encode current semantics**: after fixing TEST-3, `test_triggers` reflects
  "publish auto-activates" and `test_deployments` reflects the current egress
  classification — the suite is green and truthful on `feat/arch-program-phase5`.

## Findings

### TEST-4 — Test discoverability by name (LOW)
Several critical behaviors (reaper/lease-reclaim, dead-letter, retry/backoff,
topological ordering, token forgery) are well tested but live in broadly-named
files (`test_ops`, `test_runs`, `test_architecture_fixes`, `test_crypto`). A
newcomer grepping by feature name finds nothing.
- **Recommendation:** either rename/extract focused modules
  (`test_queue_reaper.py`, `test_engine_toposort.py`) or add a one-line
  test-map in `apps/api/tests/README.md` pointing feature → file. Cheap, aids
  contributor onboarding before OSS release.
- **Status:** Reviewed.

### TEST-5 — No visible coverage gate / coverage reporting in CI (LOW — verify)
The suite is large but CI doesn't appear to enforce a coverage floor or publish a
coverage artifact. Without it, coverage can silently erode as nodes are added.
- **Recommendation:** add `pytest --cov` with a non-blocking report first
  (establish the baseline), then a modest floor on the core packages
  (`nodyra/engine`, `app/services/queue`, auth/crypto). Avoid a high global gate
  that punishes the large node-library surface.
- **Status:** Verify.

### TEST-6 — Sandbox tests: confirm a "never executes in parent process" assertion (MEDIUM — ties to SAFE-3/NODE-1)
Sandbox files exist, but the key regression for SAFE-3/NODE-1 is an explicit test
that custom/code-node execution **cannot** happen in the API/worker process when
`multi_tenancy_enabled` (i.e. `use_subprocess_runner=False` is rejected or
force-overridden). 
- **Recommendation:** add a test that asserts the startup guard / runner refuses
  in-process code execution under production/MT mode. This converts the
  documented policy into an enforced invariant.
- **Status:** Recommended (pairs with the SAFE-3/NODE-1 hardening).

## Verdict
Testing is a **strength**, not a gap: large, broad, covers every critical
subsystem including cross-org isolation and token tampering, and CI now enforces
both dependency-lock and Postgres schema integrity. The residual items are
discoverability (naming/test-map), an explicit coverage baseline, and one
high-value sandbox invariant test tied to the SAFE-3/NODE-1 policy — all LOW/MEDIUM.
