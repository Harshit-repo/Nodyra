# Noodle Backend Production Audit

*Independent production-readiness audit — fresh pass, 2026-06-16. Auditor role: Principal Backend Engineer / Staff Architect. This audit was conducted by reading the source directly; it does not reuse or defer to the prior `NOODLE_AUDIT_FINAL_REPORT.md` or the in-tree audit trackers.*

---

## Audit Checklist (scope actually inspected)

Depth legend: **D** = read in full / line-by-line; **S** = sampled the security/reliability-critical paths; **N** = not inspected (flagged for follow-up).

| Area | Depth | Key files read |
|---|---|---|
| App bootstrap / middleware / topology | D | `apps/api/app/main.py`, `config.py` |
| Auth & RBAC | D | `app/security.py`, `app/routers/auth.py` |
| Crypto / secrets / tokens | D | `app/services/crypto.py` |
| Multi-tenancy enforcement | D | `app/tenancy.py`, `app/db.py` |
| Durable run queue | D | `app/services/queue.py` |
| Workflow engine / scheduler | D | `packages/core/noodle/engine/scheduler.py`, `node_exec.py` |
| Expression sandbox | D | `packages/core/noodle/expr.py` |
| Process isolation | D | `packages/core/noodle/process_isolation.py` |
| Code-node execution | S | `packages/nodes/noodle_nodes/builtin.py` |
| SSRF / HTTP node safety | D | `packages/nodes/noodle_nodes/http_security.py`, `builtin.py:http_request` |
| Webhook ingress | D | `app/routers/webhooks.py` |
| Sandbox executor + pool | S | `app/services/executors/sandbox.py`, `sandbox_pool.py` |
| Runner / credential injection / redaction | S | `app/services/runner.py` (grep + spot reads) |
| DB models / indexes / cascades | S | `app/models.py` (constraint survey) |
| Artifact storage / path safety | D | `app/services/artifact_backends.py` |
| Health / readiness | D | `app/routers/health.py` |
| Internal API auth | D | `app/routers/internal.py` |
| API design / pagination | S | `app/routers/runs.py` |
| Deployment | D | `deploy/docker-compose.yml`, `Dockerfile.python`, `.github/workflows/ci.yml` |
| Node library (49k LOC) | N | only `builtin.py`, `http_security.py` sampled |
| AI/LLM nodes | N | `llm.py` (2k LOC), `ai_builder.py`, `ai_runtime.py` not deep-read |
| OAuth / remote-runner WS / MCP server auth | N | `oauth.py`, `remote_dispatch.py`, `mcp/` not deep-read |
| Providers (docker/k8s/agent) | N | `services/providers/*` not deep-read |

---

## Executive Summary

**Overall production-readiness score: 7.5 / 10** for a self-hosted, single-tenant or trusted-tenant product; **6 / 10** for an untrusted multi-tenant SaaS.

This is, frankly, one of the more mature "prototype-grade" codebases I have audited. It is well past prototype. The architecture is deliberate, the failure modes are reasoned about in comments (not just code), and the hardest production problems — durable queueing with SKIP-LOCKED leasing, lease-expiry recovery, graceful drain, dependency-counting DAG execution with cooperative cancellation, fail-closed startup guards, envelope-encrypted credentials, and a two-layer multi-tenancy enforcement model — are already solved competently. The test suite is substantial (~1,854 test functions across 143 files) and CI exercises both SQLite and a real Postgres+Redis backend plus an Alembic schema-drift gate.

**Biggest strengths**
- **Fail-closed security posture at startup** (`config.security_startup_errors`, `dispatch_topology_errors`): a default `SECRET_KEY` or a blank internal token under an enforced boundary *aborts boot*. This is the right instinct and rare to see.
- **Durable run queue** (`services/queue.py`) is genuinely production-grade: SKIP LOCKED, exponential backoff, dead-lettering, org-fair scheduling, drain mode, cross-process wakeup via Redis pub/sub.
- **Engine** (`engine/scheduler.py`) is clean: deterministic topological order, cycle detection, dependency-counting concurrency, and correct cancellation teardown of in-flight tasks.
- **Multi-tenancy** is enforced in two layers (ORM `with_loader_criteria` + Postgres RLS GUC via `SET LOCAL`) with a fail-closed default-org fallback.
- **Envelope encryption** for credentials (data ← DEK ← org-KEK ← master-KEK) with per-credential keys.

**Biggest risks**
1. **Stateless session tokens with no revocation** — logout/compromise cannot invalidate a token before TTL (24h default). For a product that vaults third-party credentials, this is the most material auth gap.
2. **SSRF guard is bypassable** — `assert_public_http_url` validates once but `requests` follows redirects unchecked and re-resolves DNS (rebinding TOCTOU). Real exposure when URLs are attacker-influenced or in deployments that disable the Code node.
3. **The code-node "sandbox" is not a security boundary** — by design. Real isolation only exists when `execution_sandbox` is enabled (containers). This is correctly gated for multi-tenancy but the in-code import blocklist invites false confidence.
4. **Observability gap** — no structured/JSON logging or request-correlated log records in the API process; only `/metrics` and ad-hoc `logging`.
5. **No rate limiting on public webhook ingress** — auth/credential endpoints are rate-limited; the public `/webhook/{path}` is not.

**Is it ready for real users?** Yes, for **self-hosted single-tenant / trusted-team** use — that is clearly the primary target and it is close to launch-ready. For **untrusted multi-tenant SaaS**, the sandbox-required path exists but the surrounding controls (token revocation, SSRF hardening, per-tenant rate limiting, audit completeness) need a focused hardening pass first.

**Must fix before a public launch:** token revocation/session store (#C1), SSRF redirect+rebinding hardening (#C2), structured logging (#H4), webhook rate limiting (#H5), non-root container user (#H6), and removing the developer-specific bind mount from the shipped compose (#L-misc).

---

## Critical Issues

### C1 — Session tokens cannot be revoked (stateless HMAC, no jti / no denylist)
- **Severity:** Critical · **Category:** Security / Reliability
- **Files:** `app/services/crypto.py` (`create_token`, `verify_token`), `app/security.py`, `app/routers/auth.py` (`logout`, `_clear_session_cookies`)
- **What is wrong:** Session tokens are self-contained HMAC blobs: `{sub, exp, typ}.sig`. Verification is purely cryptographic + expiry. There is no `jti`, no server-side session table, and no denylist. Logout only deletes the browser cookie (`_clear_session_cookies`); the token string remains valid until `exp` (default `auth_token_ttl_seconds = 86_400`, 24h).
- **Why it matters:** You cannot force-logout, cannot revoke a leaked/stolen token, cannot invalidate sessions on password change or role downgrade, and cannot kill a deactivated user's access until expiry. For a platform that stores customer OAuth tokens and credentials, "we can't revoke a stolen session for 24h" is a serious incident-response gap. Role changes also don't take effect until re-login because the role is read live from `User`/`Membership` (that part is fine), but a *deleted* user is only caught by `session.get(User, user_id)` returning None — good — yet a token minted before a password reset still authenticates.
- **Recommended fix:** Introduce a minimal session/revocation store:
  - Add a `jti` (random) and `iat` to the token payload.
  - Maintain either (a) a `sessions` table keyed by `jti` with `revoked_at`, checked in `verify_token`'s caller (`current_user`), or (b) a per-user `tokens_valid_after` timestamp column; reject tokens with `iat < tokens_valid_after`. Option (b) is the cheapest "revoke all sessions on password change/role change" lever.
  - On logout, write the `jti` to a Redis denylist with TTL = remaining token life.
- **Implementation steps:** (1) migration adding `User.sessions_valid_after` (nullable timestamp); (2) include `iat`/`jti` in `create_token`; (3) in `current_user`, after loading the user, reject if `payload.iat < user.sessions_valid_after`; (4) set `sessions_valid_after = now()` in password-reset, deactivate-user, and a new "revoke sessions" admin action; (5) optional Redis `jti` denylist for single-session logout.

### C2 — SSRF guard bypass via HTTP redirects and DNS rebinding
- **Severity:** Critical (in untrusted-input / multi-tenant context) · High (trusted single-tenant) · **Category:** Security
- **Files:** `packages/nodes/noodle_nodes/http_security.py` (`assert_public_http_url`), `packages/nodes/noodle_nodes/builtin.py` (`http_request` ~L1166-1179, `graphql_request` ~L1232)
- **What is wrong:** `assert_public_http_url` resolves DNS once and rejects private targets, but:
  1. `requests.request(...)` is called with **default `allow_redirects=True`**, and the redirect *target* is never re-validated. A public host returning `302 -> http://169.254.169.254/latest/meta-data/...` or `-> http://10.0.0.5/...` is followed straight to the internal resource.
  2. The check resolves DNS, then `requests` resolves DNS again at connect time — a **DNS-rebinding** attacker (low-TTL record flipping public→private between the two lookups) defeats the pre-check.
- **Why it matters:** Cloud-metadata theft (IAM creds on AWS/GCP/Azure), internal service enumeration, and reaching `postgres`/`redis`/`minio` on the compose network. In multi-tenant mode the dedicated `sandbox_network` mitigates reachability *if* `execution_sandbox` is on and the run container is actually on that isolated network — but the HTTP node runs in non-sandbox deployments too, and the guard is the only control there. Note: a trusted workflow author already has the Code node (full RCE) so SSRF adds little *for that author*; the real exposure is **attacker-controlled URL values** (e.g. a webhook body interpolated into the `url` param via `{{ $json.callback }}`).
- **Recommended fix:**
  - Set `allow_redirects=False` and resolve redirects manually, calling `assert_public_http_url` on every hop (bounded redirect count).
  - Close the rebinding window by resolving the host yourself, pinning the validated IP, and connecting to the IP with the original `Host` header (or use a custom `requests` adapter / `HTTPConnection` that pins the resolved address). At minimum, document that the guard is best-effort and that multi-tenant deployments must rely on `sandbox_network` egress isolation.
  - Apply the same guard to every node that opens a socket to a user-supplied host (the `unsafe_nodes.py` inventory already enumerates these — SSH, exec, SQL, filesystem).
- **Implementation steps:** factor a `safe_request()` helper in `http_security.py` that (1) resolves+validates, (2) pins the IP, (3) disables auto-redirect and re-validates each hop; route `http_request`/`graphql_request`/integration nodes through it.

> Honest caveat: given the Code node is RCE-by-design in non-sandbox mode, I considered downgrading C2 to High. I kept it Critical because (a) the SSRF guard is an *explicit security control the product advertises and relies on*, and (b) the attacker-controlled-URL path is reachable without authoring a workflow.

---

## High Priority Issues

### H1 — `decrypt_credential` / `decrypt_data` silently return `{}` on failure
- **Severity:** High · **Category:** Reliability / Security
- **Files:** `app/services/crypto.py` (`decrypt_data`, `decrypt_credential`, `decrypt_with_dek`)
- **What is wrong:** Every decrypt failure (wrong key after rotation, corruption, truncated ciphertext) is swallowed and returns an empty dict, with at most a `warning` log in one path and *no* log in `decrypt_data`/`decrypt_with_dek`.
- **Why it matters:** A botched master-key rotation or org-KEK migration turns every credential into "no fields" silently. Nodes then run *without* auth headers/keys and may hit production APIs unauthenticated, or behave unpredictably, instead of failing loudly. It also masks tampering. Silent `{}` is the worst failure mode for a secrets vault.
- **Recommended fix:** Distinguish "no such credential" from "decryption failed." Raise a typed `CredentialDecryptError` on failure and let the runner surface it as a node/run error; never return `{}` for a credential that exists. Keep the lenient `{}` only for genuinely optional/legacy reads, and always log at WARNING with the credential id (never the plaintext).

### H2 — Code-node import blocklist is security theater (bypassable; invites false confidence)
- **Severity:** High · **Category:** Security / DX
- **Files:** `packages/nodes/noodle_nodes/builtin.py` (`_CODE_NODE_BLOCKED_IMPORTS`, `_make_sandboxed_import`, `_build_safe_builtins`, `_run_code_isolated`), `packages/core/noodle/expr.py` (`_CodeValidator`)
- **What is wrong:** `_build_safe_builtins()` copies **all** builtins and only overrides `__import__`; `open`, `eval`, `exec`, `compile` remain present (the `_CodeValidator` blocks them only as *identifiers/attributes*, which is trivially sidesteppable). The import blocklist is a root-module string check on `__import__` and is bypassable in numerous ways (transitive imports, `importlib` if not blocked, builtins still holding references). The code comment honestly says "this is a guard, not a sandbox" — but the layering (AST validator + blocked names + sandboxed import) reads like a sandbox and will be trusted as one by operators.
- **Why it matters:** Anyone who relies on "Code node is sandboxed" in a non-container deployment is wrong. The only real boundary is `execution_sandbox` (containers + `sandbox_network` + non-privileged runtime). This is correctly enforced for multi-tenancy via `sandbox_policy_strict`, but the in-code guard muddies the threat model.
- **Recommended fix:** Either (a) drop the pretense — keep the footgun-import warning but document plainly that Code nodes are arbitrary RCE and isolation = containers only; or (b) if you want a true in-process restriction, that path is a dead end in CPython — invest in the container sandbox instead and make it the documented requirement for any untrusted-author deployment. Add a config flag to *disable the Code node entirely* for deployments that accept untrusted input but can't run containers.

### H3 — Expression alias substitution corrupts string literals
- **Severity:** High (correctness) → realistically Medium · **Category:** Bug
- **Files:** `packages/core/noodle/expr.py` (`_eval_one`, `_ALIASES`)
- **What is wrong:** `_eval_one` rewrites `$json`→`_json` etc. with naive `str.replace` over the *entire* expression source, including inside string literals. `{{ "$json.foo" }}` becomes `{{ "_json.foo" }}`; `{{ x if y else "$now" }}` mangles the literal. Also any user data containing `$json` as a substring in a templated literal is altered.
- **Why it matters:** Silent wrong results in expressions — the hardest class of bug for users to diagnose, in a feature (expressions) that's used everywhere.
- **Recommended fix:** Replace the string-level alias swap with an AST `NodeTransformer` that renames only `ast.Name` nodes whose `id` is an alias, or bind the `$`-prefixed names directly in the eval namespace by mapping `$json`→a key the parser accepts. Cleanest: tokenize and rename only identifier tokens, or pre-bind `_json` and rewrite via AST after parse.

### H4 — No structured logging / request correlation in the API process
- **Severity:** High · **Category:** Observability
- **Files:** entire `apps/api/app` (only `app/worker_main.py:133` calls `logging.basicConfig`); `app/main.py` `_security_headers` generates `X-Request-ID` but never injects it into log records.
- **What is wrong:** The API relies on uvicorn's default text logging. There is no `dictConfig`, no JSON formatter, no per-request logger with `request_id`/`org_id`/`user_id` bound. `X-Request-ID` is returned to the client but not attached to the server-side log line, so you cannot correlate a user's failed request to its log/trace.
- **Why it matters:** First production incident, you'll be grepping unstructured multi-line tracebacks across replicas with no correlation key. This is the difference between a 5-minute and a 5-hour incident.
- **Recommended fix:** Add a logging `dictConfig` at startup with a JSON formatter (e.g. `python-json-logger`); add a contextvar-backed `LoggingFilter` that injects `request_id`, `org_id`, and `user_id` (set in the existing `_tenant_context_scope`/auth middleware) into every record. OTel tracing already exists (`app/tracing.py`) — also emit the `trace_id` into logs so logs↔traces join.

### H5 — Public webhook ingress has no rate limiting
- **Severity:** High · **Category:** Reliability / Security (DoS)
- **Files:** `app/routers/webhooks.py` (`trigger_webhook`), `app/services/triggers.py` (`dispatch_webhook`)
- **What is wrong:** `/auth/login` and `/auth/register` are rate-limited (`auth.py:_enforce_auth_rate_limit`, Redis-backed), but the public `/webhook/{path}` endpoint is not. Each hit can enqueue a run (subject to dedup/queue backpressure, but enqueue + DB writes + matching queries happen first).
- **Why it matters:** A discovered webhook URL can be flooded to fill the run queue, exhaust per-org quotas, and generate DB/compute load. There's good *downstream* backpressure (org caps, queue depth) but no *ingress* throttle.
- **Recommended fix:** Reuse the Redis sliding-window limiter from `auth.py` (factor it into a shared `services/rate_limit.py`) keyed by `(webhook_path, client_ip)` and/or per-org. Add a global config `webhook_rate_limit_per_minute`. Also consider a max body already covered by the 10 MiB middleware — good.

### H6 — Containers run as root; root + docker.sock = host root
- **Severity:** High · **Category:** Security / DevOps
- **Files:** `deploy/Dockerfile.python` (no `USER`), `deploy/docker-compose.yml` (worker `docker.sock` mount, commented but documented as the sandbox path)
- **What is wrong:** The image has no non-root `USER`. The worker, when running `execution_sandbox`, mounts `/var/run/docker.sock`. Root inside a container with the host docker socket is effectively host root. Even without the socket, running app processes as root is a needless escalation surface.
- **Recommended fix:** Add a dedicated non-root user in the Dockerfile and `USER appuser`. For the sandbox worker, prefer a rootless/socket-proxy approach (e.g. a constrained docker-socket-proxy that only permits container create/run on the `noodle-sandbox` network) rather than the raw socket. Document that the worker host is part of the trust boundary.

### H7 — `/health/ready` hard-fails on Redis even when Redis isn't required
- **Severity:** High (operational) → Medium · **Category:** Reliability / DevOps
- **Files:** `app/routers/health.py` (`ready`)
- **What is wrong:** `ready()` always pings Redis and marks the service `degraded` (503) if it fails, regardless of `queue_backend`/`dispatch_role`. In a legitimate single-process `local` deployment (no Redis), readiness is permanently 503.
- **Why it matters:** Behind a load balancer / k8s readiness probe, a valid local-mode deployment never becomes ready. Operators will disable the probe and lose the signal.
- **Recommended fix:** Only treat Redis as readiness-critical when `queue_backend == "redis"` or `dispatch_role != "inline"`; otherwise report it as informational (like the sandbox check already does).

---

## Medium Priority Issues

### M1 — CORS wildcard + `allow_credentials=True` is only warned, not blocked
- **Severity:** Medium · **Category:** Security
- **Files:** `app/main.py` (CORS middleware), `config.py` (`runtime_warnings`)
- **What:** `allow_credentials=True` with a `*` origin makes Starlette reflect any Origin and allow credentials — effectively "any site can make authenticated requests." It's only a production-mode *warning*.
- **Fix:** Refuse to start (or strip credentials) when `*` is combined with `allow_credentials=True` and `auth_required`. Promote to `security_startup_errors`.

### M2 — `_org_fair_order` issues N `effective_limits` queries per lease tick
- **Severity:** Medium · **Category:** Performance / Scalability
- **Files:** `app/services/queue.py` (`_org_fair_order`, called from `lease`)
- **What:** With many tenants, every dispatch tick runs one `effective_limits(session, org_id)` query per eligible org before leasing. The two grouped queries are fine; the per-org limits lookup isn't batched/cached.
- **Fix:** Cache `effective_limits` per org for a few seconds (it changes rarely), or batch-load all eligible orgs' limits in one query.

### M3 — No static type checking in CI (type hints present, unverified)
- **Severity:** Medium · **Category:** Code quality / DX
- **Files:** `pyproject.toml` (ruff only), `.github/workflows/ci.yml`
- **What:** The codebase is heavily type-hinted but there's no mypy/pyright gate, so the hints can and will drift from reality.
- **Fix:** Add `mypy` (or `pyright`) to dev deps and a CI step, even if initially scoped to `app/` and `noodle/engine/` with a baseline.

### M4 — Stateless CSRF cookie is not bound to the session (cookie-fixation class)
- **Severity:** Medium · **Category:** Security
- **Files:** `app/routers/auth.py` (`_set_session_cookies`), `app/main.py` (`_csrf_gate`)
- **What:** Double-submit CSRF compares `noodle_csrf` cookie to `X-CSRF-Token` header. The CSRF value is random but not cryptographically bound to the session token. A network/subdomain attacker who can set cookies could fix both. This is the known limitation of stateless double-submit and is acceptable, but worth tightening.
- **Fix:** Derive/sign the CSRF token from the session (HMAC of the session jti) so the server can verify binding, or set `__Host-` cookie prefix + `Secure` to harden against subdomain cookie injection.

### M5 — No dependency vulnerability scanning in CI
- **Severity:** Medium · **Category:** Security / DevOps
- **Files:** `.github/workflows/ci.yml`
- **What:** CI lints, type-checks (ruff), runs tests, checks lockfile + migration drift — but no `pip-audit`/`uv pip audit`/Dependabot and no SAST (`bandit`/`semgrep`).
- **Fix:** Add `uv pip audit` (or `pip-audit`) and a `bandit` pass scoped to `apps/` and `packages/`.

### M6 — Interrupted-run recovery only runs on the `inline` role
- **Severity:** Medium · **Category:** Reliability (needs deeper investigation)
- **Files:** `app/main.py` (`_mark_interrupted_runs`, gated on `dispatch_inline`)
- **What:** On restart, only an `inline` process reconciles orphaned `running`/`waiting` rows; split topologies rely on lease expiry. That's the right call, but the lease-expiry path (`requeue_expired_leases`) only resets `Run.status` for rows still `running` — a run stuck in `waiting` (approval) with a dead worker isn't covered the same way. Verify approval-waiting runs recover correctly in `worker`/`control` topologies after a crash.
- **Fix:** Add a test for "worker crashes while a run is `waiting` on approval" in split topology and confirm recovery; document the guarantee.

### M7 — `unsafe_node_policy` defaults to `warn` (risky nodes ship-on by default)
- **Severity:** Medium · **Category:** Security (posture)
- **Files:** `config.py` (`unsafe_node_policy = "warn"`), `app/services/unsafe_nodes.py`
- **What:** Deployments activating workflows with Code/SSH/exec/private-IP HTTP nodes are only *warned* by default. Good inventory exists; default posture is permissive.
- **Fix:** For multi-tenant / untrusted-input deployments, default to `require_approval` or `block`. Tie the default to `multi_tenancy_enabled`.

### M8 — Webhook IP allowlist / HMAC verified, but no replay protection beyond dedup
- **Severity:** Medium · **Category:** Security (needs deeper investigation)
- **Files:** `app/services/triggers.py` (`dispatch_webhook`, HMAC path), `webhooks.py`
- **What:** HMAC signature verification exists and there's a `deduplication_key`, but I did not confirm timestamp/nonce-window replay protection (a captured signed request could be replayed within the dedup window or after dedup state ages out).
- **Fix:** Verify provider signatures include and enforce a timestamp tolerance window (Stripe/GitHub style); reject stale signed payloads.

---

## Low Priority / Polish Improvements

- **L1 — Developer-specific bind mount shipped in tracked compose.** `deploy/docker-compose.yml:125` mounts `D:/output_grainbrokers_parquet:/data/grainbrokers:ro`. This is a local artifact of the author's machine and should not be in the shipped file. *(Bug/Polish.)* Remove it.
- **L2 — Weak default infra creds in compose** (`postgres noodle/noodle`, `minio noodle/noodle123`) with ports published to host. Fine for local; add a clear "change these / not for production" banner and an override file. *(DevOps.)*
- **L3 — `master KEK` derived from `SECRET_KEY` via plain SHA-256** (`crypto._fernet`). Acceptable because `SECRET_KEY` should be high-entropy, but if an operator sets a weak secret it's directly hashable. Consider HKDF with a fixed salt/info, and document the entropy requirement. *(Security/polish.)*
- **L4 — PBKDF2-HMAC-SHA256 @ 200k rounds** (`crypto.hash_password`) is OK but Argon2id is the modern default. Consider migrating with a transparent rehash-on-login. *(Security/polish.)*
- **L5 — Broad `except Exception` swallowing** is pervasive (often justified with comments). A few hide useful signal silently (e.g. `notify_queue_workers` swallows all Redis errors with no debug log on the publish path). Audit for at least DEBUG logging. *(Code quality.)*
- **L6 — `_unhandled_exception_handler` returns generic 500** — good — but ensure FastAPI `HTTPException` and validation errors also carry the `request_id` for client correlation. *(Polish.)*
- **L7 — `/metrics` is a custom Prometheus text endpoint** (`ops.py`) rather than `prometheus_client` exposition — fine, but verify label cardinality (don't label by run_id/org_id unbounded). *(Observability, needs check.)*

---

## Architecture Review

The backend is a clean monorepo with the right seams:
- `packages/core/noodle` — the pure execution engine (graph model, scheduler, expr, datasets, agent runtime). It has **no dependency on the API/DB**, which is exactly right: the same engine runs in-process, in env-runner subprocesses, in sandbox containers, and in exported scripts. This is the single best architectural decision in the codebase.
- `packages/nodes/noodle_nodes` — the node library, registered via a decorator/registry (`sdk.NodeRegistry`).
- `apps/api/app` — FastAPI app, split into `routers/` (HTTP surface) and `services/` (domain logic). Service layer is real, not anemic; routers stay thin.

**Execution topology** is a genuine strength. `dispatch_role` (inline/worker/control/disabled), `scheduler_role` (inline/leader/disabled), and `webhook_role` (inline/ingress/disabled) compose into coherent deployment shapes, and misconfigurations that would silently lose runs are converted into hard startup errors (`dispatch_topology_errors`). The control/worker split correctly reasons about *where a WebSocket terminates* (agent/k8s pools must be dispatched by the replica holding the WS).

**Dependency direction** is sound: `db.py` imports `tenancy` (late) to install ORM hooks; `tenancy` reads `Base` lazily to avoid a cycle; engine submodules use lazy imports to break the loops/metanodes/subworkflows cycle. These are managed deliberately.

**Concerns / improvements:**
- The `services/` directory is large (50+ modules) and a few are doing a lot (`runner.py` 1,294 LOC, `triggers.py` 1,223 LOC). These are the natural next refactor targets — `runner.py` mixes admission control, credential resolution, redaction, execution dispatch, and lifecycle. Splitting into `admission`, `dispatch`, and `lifecycle` submodules would help.
- State management is mostly DB-backed (good), but several correctness-relevant pieces are **in-process dicts** with Redis fallback: webhook listen sessions, webhook capture buffer, auth rate-limit buckets, the queue wakeup event. All have documented degradation modes, which is responsible, but it means "Redis down" silently changes semantics (rate limits become per-replica, listen-gate becomes per-replica). That's acceptable for self-host; document it as a known multi-replica caveat.
- Error propagation from nodes → run → API is well modeled (`NodeRunResult`, `RunStatus` severity ranking). Good.

**Will it scale beyond a prototype?** Yes — the queue + worker split + Postgres SKIP LOCKED is a textbook horizontally-scalable execution plane. The ceilings are operational (connection pool sizing, the per-org limits query in the lease loop, in-process fallbacks) rather than architectural.

---

## API Review

- **Pagination:** present and bounded (`runs.py` uses `Query(50, ge=1, le=500)` + `offset`, returns a `PageResponse` with `total`). Good. Verify the *list* endpoints across all routers are consistently paginated (I confirmed runs; spot-check workflows/credentials/audit).
- **Validation:** Pydantic schemas in `schemas.py` (1,148 LOC) — comprehensive. Role validation is centralized (`normalize_role`).
- **Status codes / errors:** consistent JSON error bodies; `_unhandled_exception_handler` guarantees parseable 500s with a `request_id`. CORS is outermost so error responses keep ACAO headers — a thoughtful detail.
- **Versioning:** API is `version 0.0.1`, no `/v1` prefix. Before public launch, decide on a versioning strategy (URL prefix or header) so you can evolve without breaking embedded SDKs / MCP clients.
- **OpenAPI:** auto-generated; docs/redoc exempted from auth with a relaxed CSP. Consider gating `/docs` behind auth in production (it currently leaks the full API shape to anonymous users — arguably fine for an OSS product).
- **Consistency:** `/users/me` aliases `/auth/me` deliberately — good ergonomics.

**Gaps:** no documented idempotency-key support on run-creation endpoints for external callers (the queue has dedup keys internally; expose it). No cursor pagination (offset pagination degrades on deep pages, but limits cap it).

---

## Auth & Security Review

**Strong:**
- Fail-closed startup (`security_startup_errors`): default secret or blank internal token under an enforced boundary aborts boot. Excellent.
- Dual-mode auth (Bearer + httpOnly cookie) with CSRF double-submit for cookie sessions; Bearer is correctly CSRF-exempt.
- Token *type* discrimination (`typ == "session"`) prevents replaying purpose tokens (runner/oauth/k8s) as session tokens (the TOK-1 fix) — verified in `verify_token`.
- Constant-time comparisons for tokens, internal secret, and password digests.
- RBAC is role-rank based with a permission→min-role map and an explicit "must be authenticated even when auth disabled" set for `user:manage`.
- Per-credential DEK + per-org KEK envelope encryption.
- WS auth via short-lived one-time tickets (avoids tokens in access logs).

**Weak / to fix:** C1 (no revocation), H1 (silent decrypt failure), H2 (code-node not a sandbox), M1 (CORS), M4 (CSRF binding), M7 (unsafe-node default). Also:
- **Password policy:** I saw no complexity/length enforcement on register (rate-limited but no minimum strength). Add a minimum length + breached-password check or zxcvbn-style scoring.
- **Account lockout:** only IP rate-limiting, no per-account lockout after N failures — acceptable with rate limiting but consider it.

---

## Database Review

- **Models** (`models.py`, 998 LOC): well-structured, fully typed `Mapped[...]`, sensible column sizes.
- **Indexes:** composite indexes on hot paths (`ix_runs_workflow_id_started_at`, `ix_runs_status_started_at`, `ix_node_runs_run_id_node_id`), plus FK-column indexes. Queue lease indexes added (migration 0030). This is better than most.
- **Constraints:** real `UniqueConstraint`s (`uq_membership_org_user`, `uq_run_meters_org_day`), `nullable=False` discipline, server defaults for booleans (so Postgres + SQLite agree).
- **Cascades:** thoughtfully chosen — `CASCADE` for owned children (memberships, node_runs, run_queue), `SET NULL` for soft references (deployment→environment, run→workflow_version). The recent `0052_deployment_env_fk_ondelete` shows active care here.
- **Migrations:** 52 Alembic revisions, linear, with a CI **schema-drift gate** against Postgres (authoritative) — this is exactly right and rare.
- **Tenancy at the DB:** Postgres RLS via `SET LOCAL app.current_org` (transaction-scoped, pool-safe) backing the ORM filter. Strong.

**Concerns:**
- **SQLite in production is only a warning.** RLS doesn't exist on SQLite, so the ORM `with_loader_criteria` filter is the *only* tenancy layer there, and any raw-SQL path bypasses it. Multi-tenant + SQLite would be unsafe; it's warned but not blocked. Consider promoting "multi_tenancy_enabled + sqlite" to a hard error.
- **N+1 risk** in the lease loop (M2) and potentially in run/node-run listing (verify eager-loading on the executions page query).
- **JSON columns** (`packages`, `provider_config`, `attempts_log`, `replay_seed`, node outputs) are convenient but unqueryable/unindexable and the code already works around SQLAlchemy's lack of in-place mutation detection (`_append_attempt` rebinds). Fine, just know the trade-off.

---

## Workflow Engine Review

This is the heart of the system and it's well built (`engine/scheduler.py`, `node_exec.py`):
- **Topological execution** with deterministic ordering driven only by edges + insertion order (canvas position explicitly ignored — documented contract). Cycle detection raises `GraphError`.
- **Dependency-counting scheduler** (`_execute_nodes`): each node starts the instant its in-set predecessors finish; `node_sem` bounds concurrency; loop/metanode *drivers* deliberately don't hold a semaphore slot so nested regions can't deadlock a capped run. This is subtle and correct.
- **Cancellation** tears down in-flight tasks on any `BaseException` (`REL-2` class) — verified.
- **Branching/skip** semantics, **partial execution** via `cache`, **targeted runs** via `targets`+`_needed_nodes`, **loops** (each/while/until) and **subworkflows** with cycle + depth guards (`max_subworkflow_depth`).
- **Per-node:** retries with exponential backoff + jitter, per-node + per-workflow timeouts, output-size capping with a cheap upper-bound fast path (`_encoded_upper_bound`) before measuring, context-local stdout/stderr capture (safe under concurrency), token/chunk streaming with bounded cross-thread emit.
- **Approval pause/resume** (`AgentApprovalRequired` → `waiting` status + `replay_seed`) is integrated end-to-end with the queue.

**Risks / gaps:**
- **Idempotency of node side effects on retry/replay:** retries re-invoke the node function; replay re-runs from a cache seed. Nodes with external side effects (HTTP POST, DB writes) are not idempotent and can double-fire on lease-expiry requeue or retry. There's no per-node "exactly-once" guard. Document this and consider an opt-in idempotency key for side-effecting nodes.
- **Large payloads between nodes:** outputs are capped per-node (`max_output_bytes`, default 256 KiB) and big data goes to artifacts/datasets — good — but intermediate in-memory `node_outputs` for a wide graph still lives in the worker process. Bounded by the cap, acceptable.
- **`run` (sync wrapper) uses `asyncio.run`** — fine for scripts/exports, not used on the hot server path.

---

## Node System Review

- **Registry/decorator** model (`@node(...)`, `NodeRegistry`) with manifests (inputs/outputs/params, versions, data-kinds). Input/output *kind* validation runs in the engine (`_validate_input_kinds`/`_validate_output_kinds`). Node type versions are tracked on results (good for observability/migration).
- **Breadth:** the library is large (~49k LOC: LLM, ML, RAG, statistical analysis, integrations v2 with per-provider operation modules, browser automation, geospatial, document intelligence, etc.). This is a real product surface, not a toy.
- **Code node:** RCE-by-design, process-isolated for *crash/timeout* containment (`process_isolation.py`) but **not** security-isolated unless `execution_sandbox` is on (see H2).
- **HTTP nodes:** SSRF guard exists but bypassable (C2).

**Not audited (flagged):** I did not deep-read the LLM nodes (`llm.py` 2k LOC), `ai_builder.py`, the integration providers, or `ai_runtime.py`. For a production pass these need review for: provider API-key handling and redaction, prompt-injection surface in the agent/tool loop, retry/cost controls, and timeout defaults (only `ai_agent_v2: 300s` and `http_request: 45s` are in `DEFAULT_NODE_TIMEOUTS`). **Needs deeper investigation.**

---

## Background Jobs / Worker Review

`services/queue.py` + the dispatch loop is the standout module.
- **Durable DB queue** is the correctness baseline; Redis is an optimization/notify channel, not a parallel dispatch path (explicitly stated). Right call.
- **Leasing:** `SELECT ... FOR UPDATE SKIP LOCKED` on Postgres; documented SQLite fallback (no row locking → dev only).
- **Recovery:** `requeue_expired_leases` resets dead-worker leases and the user-facing `Run` row; dead-letter after `max_attempts`.
- **Backpressure:** per-org fairness + concurrency caps, `org_quota_exceeded` reason surfaced to the UI, local-slot budgeting so the loop doesn't lease more local runs than the pool can take.
- **Drain:** `queue_drain` stops new leases while finishing in-flight; lifespan shutdown drains with a bounded timeout. Good.
- **Cross-process cancellation:** `_cancel_reconcile` turns a control-plane DELETE into a real worker-side `task.cancel()`.

**Concerns:** M2 (per-org limits query in lease loop), M6 (approval-waiting recovery in split topology), and the worker entrypoint (`worker_main.py`) wasn't deep-read — verify it wires the same drain/shutdown semantics as the API lifespan. Duplicate-execution prevention rests on the single `RunQueueEntry` per `run_id` + lease; that's sound, but combined with non-idempotent nodes, lease-expiry requeue can re-execute side effects (see engine review).

---

## Performance Review

- **Async discipline:** sync node functions are always offloaded with `asyncio.to_thread` (or process pool for Code) — the event loop is never blocked by node work. Verified in `node_exec.invoke_node`.
- **DB pool:** tunable (`db_pool_size`/`max_overflow`/`recycle`, `pool_pre_ping`), NullPool for SQLite. Sensible.
- **Output sizing:** cheap upper-bound before full JSON measurement avoids serializing huge outputs twice.
- **Caching opportunities:** `effective_limits` (M2); `org_scoped_models()` recomputes the model list on *every* SELECT in the ORM hook (`tenancy._scope_selects_to_org`) — cache it once after mappers are configured. Minor but it's on the hottest path in the codebase (every query, multi-tenant on).
- **Redis pub/sub wakeup** eliminates poll latency on new work — good.

**Recommended quick perf win:** memoize `org_scoped_models()` (it iterates all mappers per query) and `effective_limits` per org.

---

## Testing Review

- **Volume:** ~1,854 test functions / 143 files. Strong.
- **Breadth:** auth, cookie auth, tenancy isolation (incl. a Postgres-specific `test_tenancy_isolation_pg`), queue fairness, leader election, sandbox policy + container runtime, crypto, licensing, RBAC, org isolation enforcement, run queue, executors, retention, expr-preview isolation. This maps closely to the risk surface — the tests were written by someone thinking about failure modes.
- **CI:** two lanes (SQLite fast lane + Postgres/Redis real-backend lane), lockfile drift check, **Alembic schema-drift gate against Postgres** (authoritative). `pytest-timeout` guards hangs.

**Gaps:**
- No **load/soak** tests (queue under thousands of entries, many orgs, worker churn).
- No **security regression** tests for the SSRF redirect/rebinding case (C2) or for "code node cannot escape the container" (an integration test against a real sandbox).
- **Static typing** not enforced (M3).
- Approval-waiting recovery under split-topology worker crash (M6) — add a test.
- The big node library has tests for many modules but I did not verify LLM/agent prompt-injection or provider-key-redaction coverage.

---

## DevOps / Deployment Review

- **Dockerfile** (`deploy/Dockerfile.python`): single shared image for API + worker, `uv sync --locked --no-dev --all-packages`, `UV_LINK_MODE=copy`. Reproducible build with a locked file gate in CI. **Runs as root (H6)** and has **no HEALTHCHECK**.
- **compose:** Postgres/Redis healthchecked; API runs `alembic upgrade head` on boot then uvicorn; worker is `python -m app.worker_main`; sandbox via optional docker.sock mount documented. `INTERNAL_API_TOKEN` is *required* via `${VAR:?}` — good. `SECRET_KEY` defaults to the placeholder, but with `AUTH_REQUIRED=true` the startup guard will (correctly) abort, so the compose fails closed. **L1 dev bind-mount must be removed.** `web` service runs the Vite dev server, not a production build — fine for the dev compose but make sure the Helm/production path serves a built bundle.
- **Helm chart** present (`deploy/helm/noodle`) with separate api/web/worker deployments + ingress — not deep-read; verify it sets a non-default SECRET_KEY via Secret, resource limits, readiness/liveness probes wired to `/health/*`, and the worker's drain on `SIGTERM`.
- **No backup/restore documentation** for Postgres + artifact store surfaced in what I read. For a product holding workflows + credentials + run history, document a backup/restore runbook (incl. that artifacts in `local` mode aren't in the DB).

---

## Missing Features and Gaps

Most of the "production workflow platform" checklist is **already present**: workflow versioning (`published_version`, `WorkflowVersion`), run history + retention, execution logs/events, credentials vault (envelope-encrypted), org/workspace + RBAC + memberships, triggers (manual/schedule/webhook/provider), scheduling (leader-elected), retry policies + dead-letter, export/import, environment separation, audit log, usage metering (`RunMeter`), org limits, MCP server, deployments, runner pools (agent/k8s/docker).

**Genuinely missing or thin:**
1. **Session revocation / token management** (C1).
2. **Per-tenant API rate limiting** beyond auth + (missing) webhook ingress (H5).
3. **Notifications** beyond run alerts — confirm email/Slack/webhook on run failure exists end-to-end (`run_alerts.py` present but not deep-read).
4. **Idempotency keys** for side-effecting nodes / external run-trigger API.
5. **Backup/restore + DR runbook.**
6. **Secret rotation tooling** — KEK rotation helpers exist in crypto, but an operator-facing "rotate master key" workflow + the silent-decrypt-failure fix (H1) are needed before rotation is safe.
7. **Structured logging + log/trace correlation** (H4).
8. **Worker autoscaling signal** — queue depth/oldest-age metrics exist (`stats`); document an HPA recipe (scale workers on `oldest_queued_age_seconds`).
9. **API versioning strategy** before public SDK/MCP consumers lock in.

---

## Recommended Roadmap

### Phase 1 — Must fix before production (public launch)
1. **C1** Session revocation (`sessions_valid_after` + optional Redis denylist).
2. **C2** SSRF: no-auto-redirect + per-hop revalidation + IP pinning in a shared `safe_request`.
3. **H1** Stop silently returning `{}` on credential decrypt failure.
4. **H4** Structured JSON logging + request/org/user/trace correlation.
5. **H5** Rate-limit public webhook ingress.
6. **H6** Non-root container user; harden the sandbox docker-socket access.
7. **L1** Remove the developer bind-mount from the shipped compose.
8. **M1** Block CORS `*` + credentials under an enforced boundary.

### Phase 2 — Should fix before beta
1. **H2** Decide the Code-node story; add "disable Code node" config; document isolation = containers.
2. **H3** AST-based expression alias rewriting (fix literal corruption).
3. **H7** Redis-conditional readiness probe.
4. **M3** mypy/pyright in CI; **M5** dependency + SAST scanning.
5. **M7** Default `unsafe_node_policy` to `require_approval`/`block` when multi-tenant.
6. **M6/M8** Tests + fixes for approval-waiting recovery and webhook replay window.
7. Password policy + (optional) account lockout.
8. Backup/restore runbook; KEK rotation procedure.

### Phase 3 — Scalability and polish
1. **M2** Cache `effective_limits`; memoize `org_scoped_models()`.
2. Load/soak tests for the queue; HPA recipe on queue depth.
3. Refactor `runner.py`/`triggers.py` into focused submodules.
4. API versioning (`/v1`), idempotency keys, cursor pagination for deep lists.
5. Promote "multi_tenancy + SQLite" to a hard startup error.

### Phase 4 — Advanced platform features
1. Argon2id password hashing with rehash-on-login.
2. External KMS provider for the master KEK (the envelope design already anticipates this).
3. Node marketplace/signing; per-node cost/quota controls for LLM nodes.
4. Full prompt-injection hardening + tooling-permission model for the agent runtime.

---

## Quick Wins (high impact / low effort)
- Memoize `org_scoped_models()` (hottest path, multi-tenant) and cache `effective_limits` (M2).
- Make `/health/ready` Redis check conditional on `queue_backend`/`dispatch_role` (H7).
- Add `USER appuser` to the Dockerfile (H6) and a `HEALTHCHECK`.
- Remove the `D:/output_grainbrokers_parquet` mount (L1).
- Set `allow_redirects=False` on the HTTP node and re-validate the target — closes the easy half of C2 in a few lines.
- Add a `dictConfig` JSON logger + request-id filter (H4).
- Promote CORS `*`+credentials to a startup error (M1).

## Suggested Refactors (deeper, long-term maintainability)
- Split `services/runner.py` (admission / credential-resolution+redaction / dispatch / lifecycle) and `services/triggers.py` (webhook / schedule / provider).
- Extract a shared `services/rate_limit.py` (used by auth, webhooks, and future per-tenant API limits).
- Centralize logging/observability setup (`app/logging.py`) used by both API and `worker_main`.
- Introduce a typed credential-decrypt result instead of `dict | {}`.

---

## Final Recommendation

**Ship it to self-hosted / single-tenant users after Phase 1.** The core is genuinely production-grade and the team has already solved the problems most projects get wrong (durable queueing, execution topology, tenancy enforcement, fail-closed startup). The remaining Phase-1 items are concentrated, well-scoped, and don't require architectural change.

**Do not position it as an untrusted multi-tenant SaaS until Phase 2 is complete** — specifically token revocation (C1), SSRF hardening (C2), the Code-node isolation story (H2), and per-tenant rate limiting (H5). The multi-tenant *plumbing* (RLS, org KEKs, sandbox-required policy, org fairness/limits) is impressively complete; the gap is in the perimeter controls and incident-response levers, not the data model.

**Single highest-leverage next action:** implement session revocation (C1). It's the one gap that turns a routine credential-leak incident into an unbounded one, and everything else (rotation, deactivation, role changes) depends on being able to invalidate a session.

---

### Areas explicitly NOT audited (recommend a follow-up pass)
- LLM / agent runtime (`llm.py`, `ai_runtime.py`, `ai_builder.py`, `engine/agent.py`): prompt injection, tool-permission model, API-key redaction, cost/timeout controls.
- OAuth flow (`services/oauth.py`): state HMAC, PKCE, token storage/refresh.
- Remote-runner WebSocket protocol (`remote_dispatch.py`, `runner_pools.py`): registration-token security, message authz, replay.
- MCP server (`app/mcp/`, `routers/mcp.py`): auth scoping of the workflow-run/builder tools.
- Providers (`services/providers/docker.py`, `k8s.py`, `agent.py`): container/pod security context, image trust.
- The bulk of the 49k-LOC node library beyond `builtin.py`.
