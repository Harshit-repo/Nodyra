# Audit Progress

Independent audit started 2026-06-16 on `feat/arch-program-phase5`. Prior in-repo
audits (`docs/production-*`) are treated as historical only; all findings below are
re-derived from direct code inspection.

| Area | Status | Notes |
|---|---|---|
| Repository map | Done | `NOODLE_REPOSITORY_MAP.md` |
| Audit scope | Done | `NOODLE_TECHNICAL_AUDIT_SCOPE.md` |
| Backend entrypoint | Not started | `main.py` lifespan, role flags, CORS, error handlers |
| API routes | Reviewed (pass 1) | `audit/backend/02_…` — auth posture VERIFIED CLEAN; API-1..3 minor |
| Services | Not started | ~50 service modules |
| Database | Reviewed (pass 1) | `audit/backend/04_…` — production-grade; DB-1 (FK ondelete) LOW, DB-2/3 |
| Auth/security | Reviewed (pass 1) | `audit/backend/05_…` — AUTH-1 HIGH fixed, AUTH-2..5 |
| Execution engine | Reviewed (pass 1) | `audit/backend/06_…` — correct & robust; ENGINE-1..3 |
| Queue/workers | Reviewed (pass 1) | `audit/backend/07_…` — production-grade; KEEP custom queue; QUEUE-1..3 |
| Node system | Reviewed (pass 1) | `audit/backend/08_…` — strong (static/exec split); NODE-1 (SAFE-3 dup), NODE-2/3 |
| Python execution safety | Reviewed (pass 1) | `audit/backend/09_…` — sound; SAFE-1..4. No critical escape found |
| Frontend architecture | Reviewed (pass 1) | `audit/frontend/01_…` — sliced store, atomic selectors, dual save-guards; FE-3/4 LOW |
| Workflow canvas | Reviewed (pass 1) | `audit/frontend/02_…` — module-scope nodeTypes correct, memoized; FE-5/6 LOW |
| Node UI/config | Reviewed (pass 1) | `audit/frontend/03_…` — schema-driven inspector; FE-7 split file, FE-8/9 verify |
| API integration | Reviewed (pass 1) | `audit/frontend/04_…` — strong client; FE-1 (localStorage token) MEDIUM **Fixed**, FE-2 |
| State management | Reviewed (pass 1) | `audit/frontend/01_…` — bounded history, structural dirty tracking |
| UI polish | Reviewed (pass 1) | `audit/frontend/03_…` — ErrorBoundary/Toast/Confirm/a11y infra strong |
| Infrastructure | Reviewed (pass 1) | `audit/infrastructure/01_…` — INFRA-1 HIGH (uv.lock), INFRA-2..5 |
| Testing | Reviewed (pass 1) | `audit/testing/01_…` — 145 BE + 40 FE test files; strong; TEST-4/5/6 |
| Documentation | Reviewed (pass 1) | `audit/documentation/01_…` — README complete+honest; DOC-1..4 LOW |
| Security review | Done | `audit/security/SECURITY_REVIEW.md` — no Critical; AUTH-1/3, FE-1 fixed; SAFE-2/3 open |
| Performance review | Done | `audit/performance/PERFORMANCE_REVIEW.md` — keep queue; wire ENGINE-1; add metrics |
| Product/UX review | Done | `audit/product/PRODUCT_UX_REVIEW.md` — mature builder; NODE-2 policy key |
| Architecture review | Done | `audit/architecture/ARCHITECTURE_REVIEW.md` — sound; "make safe path the only path" |
| Final report | Done | `NOODLE_AUDIT_FINAL_REPORT.md` |

## Running findings ledger (independent pass)

| ID | Area | Severity | Summary | Status |
|---|---|---|---|---|
| INFRA-1 | Deploy | High | `uv.lock` gitignored but Dockerfile uses `uv sync --locked` → clean-clone build fails, non-reproducible | **Fixed** (af4ec59) |
| INFRA-2 | Deploy | Medium | API/worker container runs as root (no `USER`) | Reviewed |
| INFRA-3 | Deploy | Medium | `COPY . .` before `uv sync` defeats Docker layer cache | Reviewed |
| INFRA-4 | Deploy | Medium | `.dockerignore` may ship `apps/api/envs/*` venvs into image | Verify |
| INFRA-5 | Deploy | Low-Med | No healthcheck for api/worker in compose/Helm | Reviewed |
| SAFE-1 | Exec safety | Low | Expression alias substitution is textual `.replace()`, corrupts literal `$json` strings | Reviewed |
| SAFE-2 | Exec safety | Medium | `unsafe_nodes` SSRF check is literal-IP only + deploy-time only; sandbox network must be the real control | **Hardened** — startup guard requires dedicated `sandbox_network` under MT (containers off the infra bridge) |
| SAFE-3 | Exec safety | Medium | Code-node AST validator is cosmetic; add test that code nodes never run in parent process | **Fixed** — `enforce_sandbox_policy` rejects in-process runner under MT/sandbox; boot refused (4 new tests) |
| SAFE-4 | Exec safety | Info | Single-tenant default shares host kernel — document multi-user requirement | Doc |
| AUTH-1 | Auth/secrets | High | Default SECRET_KEY not enforced (warning only, suppressed in shipped compose) → token forgery + credential decryption | **Fixed** (guard + compose + tests) |
| AUTH-2 | Auth/secrets | Medium | No token revocation / session invalidation (stateless HMAC, 24h TTL) | Needs decision |
| AUTH-3 | Auth/secrets | Medium | Blank `internal_api_token` left `/internal/*` unauthenticated in split topology | **Fixed** (folded into startup guard) |
| AUTH-4 | Auth/secrets | Low | PBKDF2 200k < OWASP 600k; consider Argon2id | Reviewed |
| AUTH-5 | Auth/secrets | Low-Med | Login/register rate limit is in-process only (N× behind replicas) | Reviewed |
| TEST-1 | Testing | Low | `test_health::test_root_metadata` flake — module-global `queue._wakeup` Event leaks across TestClient loops | **Fixed** (conftest resets `_wakeup`) |
| TEST-2 | Testing | Medium | `test_ops::test_drain_status_default_false` order-dependent — `settings.queue_drain` global leaks | **Fixed** (conftest reset fixture) |
| TEST-3 | Testing | High | Branch had failing tests: `unsafe_nodes` classify drift (postgres egress) + stale `test_triggers` (publish now auto-activates) | **Fixed** (tests updated to current semantics) |
| ENGINE-1 | Exec engine | Low-Med | Static wide DAGs run unbounded node concurrency (`max_node_concurrency` never wired); dynamic fan-out IS bounded | Recommend wiring |
| ENGINE-2 | Exec engine | Info | Default run/node wall-clock cap is 0 (unlimited) — set non-zero for SaaS | Doc/decision |
| ENGINE-3 | Exec engine | Info | Failure = skip-downstream (continue-on-fail), not fail-branch — confirm + document | Decision |
| QUEUE-1 | Queue | Low | Fair pre-pass caps at 5 orgs/lease — possible ~1s wasted tick under heavy MT contention | Reviewed |
| QUEUE-3 | Queue | Low-Med | Verify queue depth/lag/dead-letter metrics exist (OTel off by default) | Verify |
| API-1 | API routes | Low | Verify list `total` (`func.count`) is org-scoped (likely fine via RLS) | Verify w/ test |
| API-3 | API routes | Info | Reads authn-gated but not authz-gated within an org (by design) — document | Doc |
| FE-1 | Frontend | Medium | Session token in `localStorage` (Bearer) → XSS-exfiltratable; httpOnly-cookie path already exists in backend | **Fixed** (aefdeb1; login no longer persists token) |
| FE-2 | Frontend | Low | No token refresh / silent re-auth (24h fixed TTL) | Reviewed |
| FE-3 | Frontend | Low | Undo history stores full node/edge snapshots (bounded at 50) — fine today, revisit for huge graphs | Reviewed |
| FE-4 | Frontend | Low | `store/index.ts` action layer 2428 LOC — keep migrating bodies into slices | Reviewed |
| FE-5 | Frontend | Low | No React Flow virtualization — correct for current scale, don't add speculatively | Reviewed |
| FE-6 | Frontend | Low | `NodeCard` not `React.memo`'d — verify RF internal memoization, else wrap | Verify |
| FE-7 | Frontend | Low | `NodeDetails.tsx` 3880 LOC — split decomposed sub-components into a folder | Reviewed |
| FE-8 | Frontend | Low | Verify all schema-driven controls/icon-buttons have labels (jest-axe sweep) | Verify |
| FE-9 | Frontend | Low | Verify heavy code/expression editors are lazily mounted | Verify |
| DB-1 | Database | Low | `Deployment.environment_id` FK lacks `ondelete` (only 1 of 46) → env delete errors | **Fixed** (12acd96; verified on real Postgres: `0052` applied + `alembic check` clean) |
| DB-2 | Database | Low | 51 migrations on 0.0.1 — squash baseline before OSS release | Reviewed |
| DB-3 | Database | Low | Verify destructive migrations are reversible/data-safe | Verify |
| NODE-1 | Node system | Medium | In-process module registration execs uploaded code (use_subprocess_runner=False) — fold into SAFE-3 sandbox policy | **Fixed** — same guard as SAFE-3 (in-process forbidden under MT/sandbox) |
| NODE-2 | Node system | Low | No node-manifest param migration story for schema changes | Decision |
| OBS-1 | Observability | Low-Med | No request-id generation (read-only) — correlation needs upstream proxy | Easy add |
| OBS-2 | Observability | Low-Med | Plain-text logs, no JSON/structured option | Reviewed |
| OBS-3 | Observability | Low-Med | No metrics endpoint / ops counters (overlaps QUEUE-3) | Reviewed |
| TEST-4 | Testing | Low | Critical behaviors tested but in broadly-named files — add test-map/rename | Reviewed |
| TEST-5 | Testing | Low | No coverage gate/report in CI — establish baseline floor on core pkgs | Verify |
| TEST-6 | Testing | Medium | Add explicit "code never execs in parent process under MT" invariant test (SAFE-3/NODE-1) | **Done** — `test_sandbox_policy` asserts MT/sandbox refuses in-process boot |
| DOC-1 | Docs | Low | Local-dev secret var is `SECRET_KEY` not `NOODLE_SECRET_KEY` (compose-only rename) | Reviewed |
| DOC-2 | Docs | Low | `docs/` mixes living refs with historical audit snapshots — add index/`history/` | Reviewed |
| DOC-3 | Docs | Low | No CHANGELOG before public launch (ties to NODE-2 schema migrations) | Reviewed |
| DOC-4 | Docs | Info | Internal/licensing docs correctly gitignored — verified clean | Verified |

## Review order (per §13 fixing policy)
1. Python execution safety + multi-tenant isolation (critical surface)
2. Execution engine correctness
3. Auth/z + secrets
4. Queue/worker robustness
5. Backend API validation/error handling
6. Frontend broken states + canvas
7. Infra/deploy + CI
8. Tests, docs, polish
