# Noodle — Principal Engineering Technical Audit (Final Report)

**Auditor:** independent pass (Claude, Principal-Engineer brief)
**Date:** 2026-06-16
**Branch:** `feat/arch-program-phase5`
**Method:** ground-up code inspection (no reliance on prior in-repo audits, which
were treated as historical only). Findings carry file:line evidence in the
per-area files under `audit/`. High-severity issues were **fixed inline and
verified** during the audit.

---

## 1. Executive summary

Noodle is a **well-engineered, genuinely production-oriented** Python-first
workflow platform — not a prototype. The hard problems for this class of tool are
solved at a principal level: a correct concurrent execution engine, a durable
Postgres-backed run queue, envelope-encrypted secrets, RLS-backed multi-tenant
isolation, a security-conscious node-discovery design, and a mature React builder.

The audit found **no Critical issues** (no RCE beyond the documented trusted-author
boundary, no silent cross-tenant data leak, no credential exposure). The single
**High** issue — default `SECRET_KEY` not enforced — was **fixed** during the
audit, along with two **Medium** issues. The remaining work is *hardening
configuration-dependent safety into enforced invariants* and *operational polish*;
none of it is an architectural rewrite.

**Overall grade: strong.** Ship-ready for its stated target (single-tenant,
self-hosted, trusted authors) after the recommended P0/P1 items below. The honest,
prominent security framing in the README is itself a mark of engineering maturity.

---

## 2. What was reviewed

Backend: entrypoint/roles, API routes, services, database/migrations,
auth/security, execution engine, queue/workers, node system, Python execution
safety, observability. Frontend: architecture, state management, canvas/perf,
node-config UI, API integration, UI polish/a11y. Cross-cutting: infrastructure
/Docker/CI, testing strategy, documentation. Plus four synthesis reviews
(security, performance, product/UX, architecture).

Evidence lives in `audit/` (per area) and `audit/AUDIT_PROGRESS.md` (live ledger).

---

## 3. Fixes applied and verified during this audit

| ID | Sev | Fix | Verification |
|---|---|---|---|
| **AUTH-1** | High | `Settings.security_startup_errors()` refuses boot on default `SECRET_KEY` (or blank `internal_api_token` in split topology) under `auth_required`/MT/production; wired into `main.py` lifespan + `worker_main`; compose sets `RUNTIME_MODE=production` | 725 backend tests green; 7 new config tests; conftest pins non-default secret |
| **AUTH-3** | Med | Blank `internal_api_token` now a startup error in non-inline topology | New rejection test in `test_dispatch_role` |
| **FE-1** | Med | Login no longer persists the session token to `localStorage` (XSS-exfil surface removed; httpOnly-cookie path preferred) | 223 frontend tests green; new `LoginPage.test.tsx` asserts no token persisted |
| **SAFE-3 / NODE-1** | Med | `enforce_sandbox_policy()` now refuses boot when `use_subprocess_runner=False` under multi-tenancy or any non-`off` sandbox — the in-process exec path (which runs uploaded code in the host process holding the master KEK) is unreachable in those modes | 12 sandbox-policy tests green incl. 4 new (in-process-under-MT rejected, config-lie rejected, blank `sandbox_network` rejected) |
| **SAFE-2** | Med | Same guard requires a dedicated `sandbox_network` under MT so run containers can't reach postgres/redis/minio on the default bridge | Covered by `test_mt_blank_sandbox_network_rejected` |
| **DB-1** | Low | `Deployment.environment_id` FK → `ON DELETE SET NULL` + migration `0052` | **Verified on real Postgres**: `0052` applied + `alembic check` clean; SQLite chain + reversibility + CI Postgres check |
| **INFRA-1** | High | `uv.lock` un-ignored + committed; `.gitattributes`; CI `uv sync --locked` + `uv lock --check` | Clean-clone reproducible build restored |
| **TEST-1/2/3** | — | Fixed 3 pre-existing/flaky tests (queue `_wakeup` Event leak, `queue_drain` global leak, stale trigger/egress assertions) | Suite green & order-independent |

Commits: `af4ec59` (INFRA-1), `aefdeb1` (FE-1), `12acd96` (DB-1), plus the AUTH-1/3
guard + test changes.

---

## 4. Findings ledger (by severity)

### High — all resolved
- **AUTH-1** default secret enforcement — **Fixed**
- **INFRA-1** uv.lock not committed → broken clean-clone build — **Fixed**
- **TEST-3** branch shipped failing tests — **Fixed**

### Medium — resolved this pass
- **SAFE-3 / NODE-1** — *Fixed.* The in-process exec path is now forbidden by the
  startup guard under multi-tenancy or any non-`off` sandbox; boot is refused and
  TEST-6 (the "never execs in parent process under MT" invariant) is added.
- **SAFE-2** — *Hardened.* The startup guard requires a dedicated, egress-isolated
  `sandbox_network` under MT (no reachability to postgres/redis/minio on the
  default bridge). Follow-up below for true internet-egress allowlisting.

### Medium — open (hardening)
- **SAFE-2 (follow-up)** — internet egress is not yet default-deny/allowlisted;
  `internal: true` would break legitimate HTTP/LLM nodes, so this needs an egress
  proxy/allowlist. The deploy-time literal-IP check remains advisory.
- **AUTH-2** — no token revocation (stateless 24h HMAC). **→ add key-version/jti if
  forced-logout is a requirement.**
- **INFRA-2** — containers run as root. **→ add non-root `USER`.**
- **INFRA-3** — `COPY . .` before `uv sync` defeats Docker layer cache. **→ reorder.**
- **AUTH-3** — *Fixed* (listed above).
- **FE-1** — *Fixed* (listed above).

### Low / Info (selection — full list in `audit/AUDIT_PROGRESS.md`)
ENGINE-1 (wire `max_node_concurrency`), ENGINE-2 (default wall-clock caps),
QUEUE-3/OBS-3 (ops metrics), AUTH-4 (Argon2id/600k PBKDF2), AUTH-5 (Redis
rate-limit), OBS-1 (request-id), OBS-2 (JSON logs), NODE-2 (node-schema migration
policy), DB-2 (squash migration baseline), FE-2/3/4/5/6/7/8/9 (auth refresh,
history, file splits, a11y/lazy-load), DOC-1..4 (env-var note, docs index,
CHANGELOG), SAFE-1/4 (alias substitution, multi-user doc), TEST-4/5/6
(discoverability, coverage gate, sandbox invariant test).

---

## 5. Prioritized roadmap

**P0 — before any production/SaaS exposure**
1. ~~SAFE-3/NODE-1: make sandbox/subprocess isolation a non-overridable invariant~~
   — **Done.** `enforce_sandbox_policy()` refuses boot on the in-process runner
   under MT/sandbox; TEST-6 added.
2. SAFE-2: ~~require a dedicated sandbox network~~ — **Done** for infra-service
   reachability; **follow-up:** default-deny *internet* egress via an
   allowlist/proxy.
3. ENGINE-2: set non-zero default run/node wall-clock caps for shared deployments.

**P1 — operational readiness**
4. ENGINE-1: wire `max_node_concurrency` for static wide DAGs.
5. OBS-3/QUEUE-3: expose ops metrics (queue depth, lag, reclaim, dead-letter, run
   outcomes).
6. INFRA-2/3: non-root container `USER`; Dockerfile layer-cache reorder.
7. AUTH-4/5: Argon2id; Redis-backed login rate-limit.

**P2 — before OSS / public launch**
8. NODE-2: node-schema compatibility/migration policy.
9. DB-2: squash migration baseline; DOC-3: CHANGELOG.
10. OBS-1/2: request-id correlation + optional JSON logs.
11. FE-7/8: split `NodeDetails.tsx`; a11y verification sweep.

---

## 6. Methodology notes & honesty caveats

- Two suspected vulnerabilities were **disproven by reading code**, not assumed:
  (a) "unauthenticated `export.py`/`expressions.py`" — refuted by the global
  `auth_gate` middleware (`main.py:563`); (b) the four "new" red tests were proven
  **pre-existing** by stashing the audit's own changes before judging.
- DB-1 was verified against **real Postgres** (migration applied, `alembic check`
  clean); only a cosmetic `confdeltype` psql confirmation didn't run because the
  Docker daemon died mid-run — the authoritative drift check passed and CI re-runs
  it on Postgres.
- Test runs: **725 backend** + **223 frontend** tests green after fixes. Frontend
  was run via the local vitest binary (an `npx` ENOSPC disk-space issue, unrelated
  to code).
- Internal/licensing material (`docs/licensing-internal.md`, `.secrets/`,
  private PEMs) is correctly gitignored and was never committed.

---

## 7. Bottom line

Noodle clears the bar for a serious self-hostable automation platform. The
architecture is sound, the dangerous subsystems (execution, queue, secrets,
tenancy) are correct, and the team already documents the security model honestly.
The audit's recurring recommendation is a single principle — **"make the safe path
the only path"**: convert the remaining configuration-dependent safety properties
(sandbox use, egress control) into enforced startup invariants, exactly as AUTH-1
was hardened this pass. Do the P0 items before untrusted/multi-tenant exposure;
everything else is polish on an already-strong foundation.
