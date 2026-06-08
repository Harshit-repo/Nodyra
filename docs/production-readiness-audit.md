# Production Readiness Audit

A living, resumable audit of the Noodle codebase ahead of release. Every finding
is tracked here with a stable ID, severity, evidence (`file:line`), and status.

**Goal:** ship with no known correctness, security, or resource-safety bugs.

---

## 🤝 Handoff context (read this first if you're continuing the audit)

This audit is being done incrementally across sessions. This section is the
operating manual so a fresh agent can continue without re-deriving context.

### Environment & how to run tests
- **OS:** Windows 11. Shell: PowerShell (but a Bash tool is available).
- **Python venv:** repo-root `D:\noodle\.venv`. Run tests with the venv python:
  - `.venv/Scripts/python.exe -m pytest <paths> -q`
  - System `python` does **not** have pytest — always use the venv.
- **Test layout:**
  - API: `apps/api/tests/` — `conftest.py` provides an async `client` fixture
    (ASGITransport), forces the **in-process** engine
    (`run_synchronously=True`, `use_subprocess_runner=False`), and gives each
    test a fresh SQLite DB. Set `NOODLE_TEST_DATABASE_URL` to a Postgres async
    URL to exercise the `SKIP LOCKED` lease path.
  - Core engine/SDK/expr: `packages/core/tests/`
  - Nodes: `packages/nodes/tests/`
  - `pytest` runs in `asyncio_mode=auto`, so `async def test_*` works without a
    marker (the audit tests still add `@pytest.mark.asyncio` explicitly — both
    are fine).
- **conftest gotcha:** the `client` fixture monkeypatches `SessionLocal` on a
  fixed list of modules. If you add a new service module that calls
  `SessionLocal()` and needs the test DB, add it to that list in
  `apps/api/tests/conftest.py`.
- **Optional deps NOT installed in this venv:** `statsmodels`, `pulp`,
  `scipy`/`sklearn` (full), causing some `test_statistical_analysis.py` failures
  — see TEST-1. `duckdb` 1.5.3 IS installed.

### Verification baselines (green as of 2026-06-08, Wave 3)
- Full API suite: **468 passed** (`.venv/Scripts/python.exe -m pytest apps/api/tests -q`, ~3 min)
- Core + nodes: **1012 passed, 0 failures** (TEST-1 fully resolved; some optional-
  dep tests `skip` when statsmodels/pulp/sklearn aren't installed).
- After ANY change, re-run the affected suite + (for core changes like
  `expr.py`) the full API suite, since the API depends on core.

### Working conventions / policy (follow these)
- **TDD:** write a failing test that encodes the bug first, then fix, then
  re-run. Every fixed finding has a named test (see the Status column).
- **Audit tests live in:** `apps/api/tests/test_audit_wave1.py`,
  `apps/api/tests/test_audit_wave2.py`, plus additions to `test_artifacts.py`,
  and core `packages/core/tests/test_expr.py`,
  `packages/nodes/tests/test_statistical_analysis.py`. Add new findings' tests
  to a `test_audit_wave3.py` (or the most relevant existing file).
- **Do NOT commit.** The feature branch has substantial pre-existing uncommitted
  work; all audit changes are left **unstaged** in the working tree for the owner
  (Harry) to review/commit. Don't create partial commits mixing audit + branch
  work.
- **Match surrounding style.** This codebase is mature and heavily commented with
  *why* notes — keep that density. Add a short comment citing the finding ID at
  each fix.
- **Don't over-block / over-engineer.** See SEC-1: the right call was to NOT
  break legitimate functionality (e.g. blocking `os` in code nodes) for
  illusory security. Calibrate to the documented trust model.

### Wave 3 additions (this session) — deferred Lows + remaining modules
All deferred findings closed and the remaining high-value modules reviewed.
Fixed with tests: **ENG-1** (loop orphan), **RUN-1** (approval-decision authz),
**DEP-1** (active-deployment version-repoint re-gate), **RD-2** (Dockerfile
package/python-version injection), **RP-1** (runner token now revocable via
runner deletion), **RP-2** (artifact upload bound to the run's runner), **EVT-1**
(broker reaps abandoned non-finished/`waiting` runs), **DSQ-2** (duckdb_sql
external-access latch + duckdb_sql/polars_transform now gated by
`unsafe_node_policy`), **MINOR** get_event_loop (engine/remote_dispatch/queue/
triggers), and **TEST-1** (whole suite green). Tests:
`apps/api/tests/test_audit_wave3.py`, plus additions to
`packages/core/tests/test_loops.py`, `packages/nodes/tests/test_datasets.py`,
and fixes in `test_statistical_analysis.py` / `test_map_nodes.py`.

### Wave 2/early additions
- **NodeVisitor grep (EXPR-1 follow-up TODO): DONE.** Grepped `packages/**` and
  `apps/**` for `NodeVisitor`/`NodeTransformer`. Only two subclasses exist, both
  in `packages/core/noodle/expr.py` (`_ExprValidator`, `_CodeValidator`) and both
  now correctly put the type check in `generic_visit` with name checks in
  `visit_Name`/`visit_Attribute`. No other validators override `visit`. The
  exporter/sdk do not subclass NodeVisitor. **This TODO is closed.**
- **engine.py reviewed** (full 2092-line pass). Found ENG-1; fixed it and the
  engine-local `get_event_loop()` MINOR. See Wave 3 findings below.

### Files changed so far (all unstaged)
- `apps/api/app/routers/internal.py` (SEC-2)
- `apps/api/app/main.py` (SEC-3, REL-1)
- `apps/api/app/services/crypto.py` (PERF-2, TOK-1)
- `apps/api/app/services/runtime_pool.py` (REL-2, REL-3)
- `apps/api/app/routers/credentials.py` (PERF-1)
- `apps/api/app/routers/artifacts.py` (ART-1)
- `apps/api/app/services/datasets_query.py` (DSQ-1)
- `apps/api/app/services/remote_dispatch.py` (RD-1)
- `packages/nodes/noodle_nodes/builtin.py` (SEC-1 doc/comment)
- `packages/nodes/noodle_nodes/statistical_analysis.py` (SA-1)
- `packages/core/noodle/expr.py` (**EXPR-1**)
- `packages/core/noodle/engine.py` (**ENG-1**, MINOR get_event_loop)
- Tests: `apps/api/tests/test_audit_wave1.py`, `test_audit_wave2.py`,
  additions to `apps/api/tests/test_artifacts.py`,
  `packages/core/tests/test_expr.py`,
  `packages/nodes/tests/test_statistical_analysis.py`,
  `packages/core/tests/test_loops.py` (**ENG-1**)
- This doc.

### Key architecture / trust-model notes (learned during the audit)
- **Trust model.** Code nodes, `{{ }}` expressions, and user code modules are
  "arbitrary code authored by a trusted-ish editor." The intended security
  boundaries are: (1) **process isolation** (subprocess/ProcessPoolExecutor per
  env), (2) the deployment-time **`unsafe_node_policy`** gate
  (`app/services/unsafe_nodes.py` flags code/command/ssh/sql-with-expr), and
  (3) the **AST validators** in `packages/core/noodle/expr.py`
  (`_ExprValidator` for `{{ }}`, `_CodeValidator` for code nodes). EXPR-1 was a
  hole in (3). NOTE: expressions are **not** covered by `unsafe_node_policy`, so
  the expr validator is the only gate on that path — treat it as security-
  critical.
- **`ast.NodeVisitor` gotcha (root cause of EXPR-1).** If you subclass
  `NodeVisitor` and override `visit()`, you disable the built-in name-based
  dispatch to `visit_<NodeType>` methods. Put per-node-type checks in
  `generic_visit` instead (as `_CodeValidator` correctly does). **TODO for next
  agent:** grep for other `NodeVisitor` subclasses and verify none override
  `visit` (`packages/**/*.py`, e.g. exporter, sdk).
- **Token model (post TOK-1).** `crypto.create_token` mints user **session**
  tokens (`typ="session"`); `crypto.create_payload_token` mints **purpose**
  tokens (`kind="runner_registration"|"k8s_run"`, or OAuth `state`).
  `verify_token` now accepts session tokens ONLY; purpose tokens must be read
  via `decode_payload_token` and their handlers check `kind`. Don't route a
  purpose token through `verify_token`/`current_user`.
- **Middleware order (`main.py`).** Starlette `add_middleware` prepends, so the
  LAST added is outermost. `CORSMiddleware` is intentionally added last so CORS
  headers appear even on short-circuit error responses (REL-1). Keep it last.
- **Artifact path containment.** Any code that writes/reads artifact bytes by
  `storage_key` must go through `artifact_backends._resolve_local_path` (or
  `artifacts._artifact_path`), which rejects escapes from the artifacts root.
  ART-1 was a write path that bypassed it.
- **A prior audit already happened** (`apps/api/tests/test_production_fixes.py`):
  webhook_response null-guard, scheduler empty-versions guard, retention batched
  deletes, `get_running_loop().create_future()`, auth rate-limit bucket sweep,
  and `run_events` cap are ALREADY fixed — don't redo them.

### Prioritized next steps (recommended order)
1. ~~**`packages/core/noodle/engine.py`**~~ — DONE this session. Reviewed the
   full execution core; fixed ENG-1 (orphaned loop iterations on
   `on_error=fail`) + the engine-local `get_event_loop()` MINOR; closed the
   NodeVisitor grep TODO. Remaining engine notes (not bugs): process-pool
   `wait_for` cannot truly kill a running subprocess on timeout (Python
   limitation, eviction is best-effort); loop per-node results in `results` are
   overwritten across iterations (by design — events carry `iteration_path`).
2. **`apps/api/app/routers/runs.py` + `workflows.py`** — authz/IDOR, input
   validation, pagination, draft-vs-published handling.
3. **DSQ-2** — `packages/nodes/noodle_nodes/datasets.py`: `polars_transform`
   `exec` at ~:717 (is it AST-validated like the code node? if not, apply the
   same `_CodeValidator` path) and the DuckDB f-string paths at ~:251/:665
   (apply the `enable_external_access=false` latch from DSQ-1).
4. ~~**Deferred Lows:** RP-1, RP-2, RD-2, EVT-1, get_event_loop~~ — DONE.
5. ~~**TEST-1 triage**~~ — DONE (suite is fully green now).
6. **Deeper frontend pass** — error/401 handling flows, a11y, perf, the large
   `EditorPage`/`NodeDetails` components (security basics already done). REMAINING.
7. **SEC-1b** — real per-run isolation (container/seccomp/gVisor) for hostile
   multi-tenant use. Larger project; accepted for launch with the documented
   mitigations (process isolation per env + `unsafe_node_policy` deploy gate +
   the AST validators). REMAINING for hostile multi-tenant.
8. **Residual MINOR:** `get_event_loop().create_future()` in the *runner agent*
   (`packages/runner/.../agent.py`) and *runtime server*
   (`packages/runtime/.../server.py`) — separate subprocess entrypoints, left as
   a deprecation-only nit to avoid untested changes to deployable units.

---

## Status summary (updated 2026-06-08)

| ID | Title | Severity | Status |
|---|---|---|---|
| EXPR-1 | `{{ }}` expression sandbox escape → RCE | **Critical** | ✅ fixed + test |
| ART-1 | Artifact upload path traversal (file write) | High | ✅ fixed + test |
| SA-1 | Monte Carlo node raw `eval()` → RCE | High | ✅ fixed + test |
| TOK-1 | Token-type confusion → auth gate bypass | Medium/High | ✅ fixed + test |
| RD-1 | Cross-run event injection from a runner | Medium | ✅ fixed + test |
| DSQ-1 | DuckDB explorer file read (LFI) | Medium | ✅ fixed + test |
| SEC-2 | Internal token timing-unsafe compare | Medium | ✅ fixed + test |
| SEC-3 | OAuth callback blocked under auth_required | Medium | ✅ fixed + test |
| REL-1 | CORS not outermost → no headers on errors | Medium | ✅ fixed + test |
| PERF-1 | Credential list decrypts on event loop | Medium | ✅ fixed + test |
| ENG-1 | Orphaned loop iterations on `on_error=fail` (concurrency>1) | Low/Med | ✅ fixed + test |
| REL-2 | Orphaned sub-workflow callbacks on error | Low | ✅ fixed + test |
| REL-3 | `idle_since` undeclared attribute | Low | ✅ fixed |
| PERF-2 | `_fernet()` re-derived each call | Low | ✅ fixed + test |
| SEC-1 | Code-node blocklist not a boundary | Low | ✅ documented |
| SEC-4 | SSRF TOCTOU/DNS-rebind | Low | accepted/documented |
| FE-1 | Session token in localStorage | Low | accepted/documented |
| RUN-1 | Approval decision missing `workflow:run` (viewer escalation) | Medium | ✅ fixed + test |
| DEP-1 | Active deployment version-repoint skips unsafe-node gate | Medium | ✅ fixed + test |
| RD-2 | Dockerfile cmd injection via package names | Low | ✅ fixed + test |
| RP-1 | Runner registration token multi-use (now revocable) | Low/Med | ✅ fixed + test |
| RP-2 | Runner artifact-upload not run-bound | Low | ✅ fixed + test |
| EVT-1 | Broker buffer not reaped for `waiting` runs | Low | ✅ fixed + test |
| DSQ-2 | datasets.py duckdb_sql LFI + deploy-gate the data-code nodes | Medium | ✅ fixed + test |
| MINOR | `get_event_loop()` deprecations | Low | ✅ fixed (engine/remote_dispatch/queue/triggers; runtime+agent pkgs noted) |
| TEST-1 | Pre-existing failing tests (wave3 stats) | — | ✅ fixed |
| SEC-1b | Real per-run isolation (container/seccomp) | (project) | accepted for launch/documented |

Verified: full API suite **468 passed**; core+nodes **1012 passed, 0 failures**
(all 11 prior TEST-1 failures now pass). Run with
`.venv/Scripts/python.exe -m pytest`.

### Severity legend
**Critical** (data loss / RCE / auth bypass), **High** (security or correctness
bug likely to hit prod), **Medium** (real bug, narrower blast radius), **Low**
(hardening / polish), **Perf** (efficiency). Status flow: `todo` → `in-progress`
→ `fixed` (+ test) → `verified`.

---

## Module review ledger
Single source of truth for what's had a line-level pass. Resumable: pick the
next `⏳ pending` row.

| Module | Reviewed | Notes |
|---|---|---|
| apps/api/app/config.py | ✅ | clean |
| apps/api/app/security.py | ✅ | clean (RBAC/role logic sound) |
| apps/api/app/main.py | ✅ | REL-1, SEC-3 (middleware order, auth gate) |
| apps/api/app/services/crypto.py | ✅ | PERF-2, TOK-1 |
| apps/api/app/routers/internal.py | ✅ | SEC-2 |
| apps/api/app/routers/webhooks.py | ✅ | clean (capture-buffer bounded) |
| apps/api/app/routers/credentials.py | ✅ | PERF-1 |
| apps/api/app/routers/auth.py | ✅ | clean; login user-enum timing (Low, also exposed via /register) |
| apps/api/app/routers/artifacts.py | ✅ | ART-1 |
| apps/api/app/routers/runner_pools.py | ✅ | TOK-1, RP-1, RP-2 |
| apps/api/app/services/artifact_backends.py | ✅ | clean (containment guard ok) |
| apps/api/app/services/datasets_query.py | ✅ | DSQ-1 |
| apps/api/app/services/events.py | ✅ | EVT-1 |
| apps/api/app/services/runtime_pool.py | ✅ | REL-2, REL-3 |
| apps/api/app/services/runner.py | ✅ | clean (prior audit fixed webhook/run_events) |
| apps/api/app/services/unsafe_nodes.py | ✅ | informs SEC-1; DSQ-2 (gates duckdb_sql/polars_transform) |
| apps/api/app/services/queue.py | ✅ | clean (SKIP LOCKED ok); MINOR get_event_loop fixed |
| apps/api/app/services/remote_dispatch.py | ✅ | RD-1, RD-2; MINOR get_event_loop fixed |
| apps/api/app/services/triggers.py (webhook auth) | ✅ | clean (HMAC + HS256 JWT correct) |
| apps/api/app/services/oauth.py | ✅ | clean (signed state via TOK-1 path) |
| packages/nodes/noodle_nodes/http_security.py | ✅ | SEC-4 (accepted) |
| packages/nodes/noodle_nodes/builtin.py (code/http nodes) | ⚠️ partial | SEC-1 |
| packages/nodes/noodle_nodes/statistical_analysis.py | ✅ | SA-1; TEST-1 (validate-before-optional-import, t-SNE perplexity clamp) |
| packages/core/noodle/expr.py | ✅ | **EXPR-1** |
| apps/web (security basics: XSS/postMessage/token) | ✅ partial | FE-1; sinks clean |
| apps/api/app/services/triggers.py (scheduler/cron) | ✅ | clean (single-replica by design; MINOR get_event_loop fixed) |
| apps/api/app/routers/runs.py | ✅ | RUN-1 (approval-decision authz); reads gated by global auth |
| apps/api/app/routers/workflows.py | ✅ | clean (perms consistent, batched queries) |
| apps/api/app/routers/deployments.py | ✅ | DEP-1 (re-gate on active version repoint) |
| apps/api/app/routers/** (authz sweep) | ✅ | all mutating routes guarded or intentionally public/token-authed |
| packages/core/noodle/engine.py | ✅ | ENG-1 + MINOR get_event_loop; NodeVisitor grep closed |
| **packages/core/noodle/sdk.py** | ⏳ partial | exec of user modules (by-design); NodeVisitor OK |
| packages/core/noodle/serialization.py | ✅ | clean (no pickle; safe typed envelopes only) |
| packages/nodes/noodle_nodes/datasets.py | ✅ | DSQ-2 (duckdb_sql latch; polars_transform already AST-validated) |
| **packages/nodes/noodle_nodes/** (other node modules ~40) | ⏳ pending | sampled only |
| **apps/web/src/** (deep correctness/UX/a11y) | ⏳ pending | beyond security basics |
| **apps/api/alembic/versions/** | ⏳ pending | migration safety/idempotency |

---

## Findings — Wave 1 (initial critical-path pass)

### Security

#### SEC-1 — Code-node sandbox is not a containment boundary
- **Severity:** Low (after analysis; consistent with documented threat model)
- **Evidence:** `packages/nodes/noodle_nodes/builtin.py` (~:958 blocklist) blocks
  only `subprocess/pty/ctypes/cffi/multiprocessing`; `_SAFE_BUILTINS` is *all*
  builtins. `import os; os.system(...)`, `os.popen`, `open`, and `getattr`-based
  attribute reach all work.
- **Decision:** Do NOT over-block (`os`/`sys`/`socket` are needed by legitimate
  data scripts; blocking them gives false security while breaking real
  workflows). Reframed the blocklist as a *footgun guard* (comment corrected).
  Real boundaries are process isolation + `unsafe_node_policy`. True isolation
  tracked as **SEC-1b**.
- **Status:** `documented` — `builtin.py` comment corrected.

#### SEC-1b — Real per-run isolation (container/seccomp/gVisor)
- **Severity:** project-level (needed for hostile multi-tenant)
- **Note:** Code nodes / expressions / user modules execute arbitrary author
  code on the runner host. For untrusted multi-tenant use, add OS-level
  sandboxing per run. Large effort; out of scope for the quick-fix passes.
- **Status:** `todo`

#### SEC-2 — Timing-unsafe internal API token comparison
- **Severity:** Medium
- **Evidence:** `apps/api/app/routers/internal.py` — `headers.get(_HEADER) != expected`.
- **Fix:** `hmac.compare_digest`.
- **Status:** `fixed` — `test_internal_token_constant_time_and_enforced`, `test_internal_uses_compare_digest`

#### SEC-3 — OAuth callback unreachable under `auth_required=True`
- **Severity:** Medium
- **Evidence:** `main.py` `_AUTH_EXEMPT_PREFIXES` omitted
  `/credentials/oauth/callback`; provider redirect carries no bearer token → 401.
- **Fix:** Exempt the signed-state callback path (state is HMAC-signed).
- **Status:** `fixed` — `test_oauth_callback_auth_exempt`

#### SEC-4 — SSRF check is TOCTOU / DNS-rebind-able
- **Severity:** Low (documented best-effort)
- **Evidence:** `http_security.py` resolves once; `requests` re-resolves.
- **Decision:** Accepted; real fix needs a pinned-IP transport.
- **Status:** `accepted`

### Reliability / correctness

#### REL-1 — CORS middleware innermost; error responses lack CORS headers
- **Severity:** Medium
- **Evidence:** CORS was added first → innermost; `auth_gate` (401)/
  `_body_size_limit` (413) short-circuits bypassed CORS → browser shows opaque
  error, SPA can't detect session expiry.
- **Fix:** Register `CORSMiddleware` last (outermost).
- **Status:** `fixed` — `test_cors_headers_on_401`

#### REL-2 — Orphaned sub-workflow callback tasks on runtime error
- **Severity:** Low
- **Evidence:** `runtime_pool.py` `_RuntimeProcess.run()` only cancelled
  `callbacks` on `CancelledError`; a raised `error`/EOF left callback tasks
  running while the proc could be recycled → stdio corruption for the next run.
- **Fix:** Cancel + drain pending callbacks in a `finally`.
- **Status:** `fixed` — `test_runtime_run_cancels_callbacks_on_error`

#### REL-3 — `idle_since` undeclared dynamic attribute
- **Severity:** Low
- **Fix:** Declare `idle_since` in `_RuntimeProcess.__init__`.
- **Status:** `fixed`

### Performance

#### PERF-1 — `list_credentials` decrypts every secret to return key names
- **Severity:** Perf / Medium
- **Evidence:** `credentials.py` `_info` runs synchronous Fernet decrypt for up
  to 500 creds on the event loop just to list key names.
- **Fix:** Offload list building to a thread (`asyncio.to_thread`). Longer-term:
  store key-names/oauth metadata as non-secret columns.
- **Status:** `fixed` — `test_list_credentials` covered via `test_credentials_v2` + audit roundtrip tests

#### PERF-2 — `_fernet()` re-derives the key every call
- **Severity:** Perf / Low
- **Fix:** Cache derived `Fernet`, keyed on the secret (so tests that
  monkeypatch `secret_key` rebuild it).
- **Status:** `fixed` — `test_fernet_cached_and_rekeys`

---

## Findings — Wave 2 (systematic sweep)

### EXPR-1 — Expression `{{ }}` sandbox escape → RCE (CRITICAL)
- **Severity:** Critical (authoring/remote code execution on the runner host)
- **Evidence:** `packages/core/noodle/expr.py` — `_ExprValidator` overrode
  `NodeVisitor.visit()` to do the node-type allowlist check + `generic_visit`,
  which **bypasses NodeVisitor's name-based dispatch**, so `visit_Name` /
  `visit_Attribute` (the `_BLOCKED_NAMES` enforcement) never ran. Only the
  node-type allowlist applied, and `Attribute`/`Name`/`Subscript`/`Call`/
  comprehensions are all allowed. Verified: `x.__class__`,
  `().__class__.__bases__[0].__subclasses__()`, and `__import__` all PASSED
  validation, and `evaluate("{{ ().__class__ }}")` executed. From there the
  `__init__.__globals__['__builtins__']['__import__']('os').system(...)` walk
  reaches arbitrary code using zero builtins (restricted `_SAFE_BUILTINS`
  doesn't help).
- **Blast radius:** every `{{ }}` expression (Set nodes, HTTP url/headers, SQL
  params, webhook dedup/response expressions, …) — and expressions are NOT
  covered by `unsafe_node_policy`, so it's an ungated path.
- **Fix:** Move the node-type check into `generic_visit` (mirrors the working
  `_CodeValidator`) so default `visit` keeps dispatching to
  `visit_Name`/`visit_Attribute`.
- **Behavior change:** attribute access to a blocked name (e.g. `input.open`
  where `open` is an OHLC field) is now correctly rejected; use `input['open']`.
- **Status:** `fixed` — `test_expression_validator_blocks_sandbox_escape`

### ART-1 — Path-traversal arbitrary file write in artifact upload (High)
- **Severity:** High (arbitrary file write, potential RCE)
- **Evidence:** `apps/api/app/routers/artifacts.py` `upload_artifact` —
  `filename = file.filename` (attacker-controlled) → `storage_key =
  f"uploads/{artifact_id}/{filename}"` → `write_bytes` with no sanitization. A
  `filename` of `../../../../etc/cron.d/x` escapes the artifacts root. Read/
  delete paths use `_resolve_local_path`; the **write** path didn't.
- **Fix:** `Path(filename).name` (basename) + route through `_resolve_local_path`.
- **Status:** `fixed` — `test_upload_artifact_rejects_path_traversal`

### SA-1 — Monte Carlo node `eval()` with no AST validation → RCE (High)
- **Severity:** High (code execution; ungated like EXPR-1)
- **Evidence:** `packages/nodes/noodle_nodes/statistical_analysis.py`
  `monte_carlo_simulate` ran `eval(expression, {"__builtins__": {}}, locals)`
  per iteration. Empty builtins doesn't sandbox; also recompiled every iteration.
- **Fix:** Validate via the (fixed) `_ExprValidator` + `_SAFE_BUILTINS`, and
  `compile()` once before the loop (also a perf win).
- **Status:** `fixed` — `test_monte_carlo_simulate_blocks_sandbox_escape`

### TOK-1 — Token-type confusion: non-session tokens pass the auth gate (Medium/High)
- **Severity:** Medium/High (authentication-gate bypass)
- **Evidence:** `crypto.create_token` (session) and `create_payload_token`
  (runner registration / OAuth state / k8s) all sign `{sub, exp, …}` with the
  same key; `verify_token` only checked signature + exp. Verified
  `verify_token(<runner token>)` returns the runner id. The global `auth_gate`
  authorizes on `verify_token` alone → any signed token (OAuth `state` is
  browser-exposed) passes the gate for gate-only endpoints. Routes using
  `current_user`/`require_permission` still fail (User lookup), but gate-only
  GETs were exposed.
- **Fix:** `create_token` stamps `typ="session"`; `verify_token` requires it.
  Purpose tokens flow via `decode_payload_token` (handlers check `kind`).
  Pre-release cost: existing sessions invalidated → re-login once.
- **Status:** `fixed` — `test_verify_token_rejects_non_session_tokens`,
  `test_runner_token_cannot_pass_auth_gate`

### RD-1 — Cross-run event injection from a connected runner (Medium)
- **Severity:** Medium (integrity; semi-trusted runner boundary)
- **Evidence:** `remote_dispatch._handle_agent_message` — `run_event` and
  `env_building` routed by the runner-supplied `run_id` through the **global**
  `_run_callbacks`, not scoped to the connection, so runner A could push events
  (logs, outputs, artifact refs that `on_event` persists) into run B's stream.
  `run_finished`/`env_error` were already scoped. K8s handler trusted the
  message `run_id` over the connection's authenticated run.
- **Fix:** Deliver `run_event`/`env_building` only when `run_id in
  conn.active_runs`; K8s handler accepts only `rid == <authenticated run_id>`.
- **Status:** `fixed` — `test_remote_dispatch_rejects_cross_run_events`

### DSQ-1 — DuckDB dataset explorer keyword filter bypassable → server-side LFI (Medium)
- **Severity:** Medium (auth-gated; any authenticated user can read server files)
- **Evidence:** `datasets_query.py` `_FORBIDDEN` regex missed DuckDB aliases
  (`read_csv_auto`, `parquet_scan`, `read_ndjson`, `csv_scan`, `parquet_metadata`,
  …). `SELECT * FROM read_csv_auto('/path')` reads arbitrary server files.
- **Fix:** Eager-materialize the dataset into an in-memory TABLE, then latch
  `SET enable_external_access=false` before the user query (one-way in DuckDB) —
  blocks all file/network access regardless of function name. Regex extended as
  defence-in-depth. Verified the latch blocks `parquet_metadata` (not in regex).
- **Status:** `fixed` — `test_dataset_query_blocks_external_file_read`

### EVT-1 — In-process broker buffer not reaped for `waiting` runs (Low)
- **Severity:** Low (in-process transport only; prod uses Redis TTL)
- **Evidence:** `events.py` `_publish_inprocess` records `_finished` only on
  `run_finished`; a run ending `waiting` (agent approval) emits `run_waiting`, so
  its buffer is never reaped until it later finishes. Abandoned waiting runs leak
  until restart.
- **Status:** `todo`

### RP-1 — Runner registration token is multi-use, not one-time (Low/Medium)
- **Evidence:** `runner_pools.create_registration_token` docstring claims
  one-time, but `runner_ws` only decodes the signed token (24h) — no single-use
  tracking/revocation. A leaked token reconnects for 24h.
- **Fix (deferred):** track first-use / revocation on the Runner row.
- **Status:** `todo`

### RP-2 — Runner artifact-upload not bound to the run/runner (Low)
- **Evidence:** `runner_pools.upload_artifact` accepts ANY valid
  `runner_registration` token to write an artifact for an arbitrary
  `run_id`/`artifact_id`. `storage_key` is containment-guarded, but no check the
  token's runner owns the run.
- **Fix (deferred):** bind token's runner to the run's assigned runner.
- **Status:** `todo`

### RD-2 — Dockerfile command injection via env package names (Low)
- **Evidence:** `remote_dispatch._ensure_docker_image` interpolates
  `' '.join(packages)` into a `RUN uv pip install …` line; shell metacharacters
  in a package name run at build time. Admin-only (`environment:write`).
- **Fix (deferred):** validate package tokens (PEP 508) before interpolation.
- **Status:** `todo`

### DSQ-2 — Other custom `eval`/`exec` sites (review)
- `packages/nodes/noodle_nodes/datasets.py:~717` (`polars_transform` exec) — is
  it AST-validated like the code node? If not, route through `_CodeValidator`.
- `datasets.py:~251/~665` (DuckDB f-string with internal artifact path) — apply
  the DSQ-1 external-access latch if user SQL can reach these connections.
- **Status:** `todo`

### MINOR — `asyncio.get_event_loop()` deprecations
- `remote_dispatch.py` (docker/cloud `run_in_executor`) and `queue._worker_id()`
  use `get_event_loop()`; prefer `get_running_loop()` (all inside a running loop).
- **Status:** `todo`

### Frontend security-basics pass (apps/web/src) — mostly clean
- **XSS sink:** `editor/ChatPanel.tsx` injects markdown via
  `dangerouslySetInnerHTML`, but it's `DOMPurify.sanitize(marked.parse(...))` —
  correct. No other raw-HTML sinks (`innerHTML`/`eval`/`new Function` absent).
- **OAuth popup:** `CredentialsPage.tsx` checks `event.origin ===
  window.location.origin` before trusting the message — correct.
- **FE-1 (Low, accepted):** session token in `localStorage`
  (`api.ts`, `editor/artifactValues.ts`, `editor/datasetValues.ts`,
  `editor/NDVPanels.tsx`) — XSS-stealable. Standard SPA tradeoff; mitigated by
  the above. Defence-in-depth: httpOnly cookie + CSRF, strict CSP on the app
  shell host.
- **Deferred:** broad FE correctness/UX/perf/a11y, error & 401 flows,
  `EditorPage`/`NodeDetails`.

---

## Findings — Wave 3 (engine.py execution core)

### ENG-1 — Orphaned loop iterations on `on_error="fail"` with concurrency>1 (Low/Med)
- **Severity:** Low/Medium (resource leak + late events for a failed run; the
  REL-2 class of bug, but in the loop driver)
- **Evidence:** `packages/core/noodle/engine.py` `_run_loop` — the concurrency>1
  branch ran `await asyncio.gather(*[_one_iteration(...) for ...])`. When one
  iteration raises `_LoopRowError` (a row failed and `on_error="fail"`), bare
  `gather` propagates that exception to the caller **but leaves the other
  in-flight iteration coroutines running detached**. Those orphaned iterations
  keep executing body nodes — calling `finish()`/`emit()` (events for an
  already-failed run), burning compute, and mutating shared `collected`/`errors`
  — after the loop has returned `RunStatus.error`. Verified by
  `test_loop_fail_cancels_inflight_iterations_eng1`: with 3 concurrent rows where
  the middle one fails fast, the two slow rows completed *after* the loop
  reported error.
- **Fix:** Drive iterations as explicit `asyncio.ensure_future` tasks; on
  `_LoopRowError`, cancel any not-done tasks and drain them
  (`gather(..., return_exceptions=True)`) before returning the error. Mirrors the
  REL-2 cancel-and-drain pattern. Note: synchronous (thread-pool) and code
  (process-pool) body nodes still can't be killed mid-call — cancellation stops
  the *await*, not a running thread/subprocess (Python limitation) — but async
  body nodes (and any further iterations not yet started) are now stopped.
- **Status:** `fixed` — `test_loop_fail_cancels_inflight_iterations_eng1`

### MINOR (engine.py portion) — `get_event_loop()` → `get_running_loop()`
- `engine.py` `invoke_node` used `asyncio.get_event_loop()` to schedule the
  process-pool executor; it always runs inside the running loop, so switched to
  `get_running_loop()`. `remote_dispatch.py` / `queue._worker_id()` still todo.
- **Status:** engine.py done; rest `todo`.

### Engine review notes (reviewed, NOT bugs)
- **Process-pool timeout can't kill a running child.** `invoke_node`'s
  `asyncio.wait_for(fut, timeout)` on a `ProcessPoolExecutor` future + `_evict_pool`
  is best-effort: `shutdown(wait=False, cancel_futures=True)` cancels only
  *pending* futures; a child already executing runs to completion. Python
  limitation; real per-run kill needs OS-level isolation (tracked as SEC-1b).
- **Loop per-node `results` overwrite.** With N iterations, each body node id is
  written to `results` N times (last wins). This is by design — per-iteration
  fidelity is carried by `node_finished` events tagged with `iteration_path`,
  not by the flat `results` map. Confirmed by existing
  `test_loop_events_are_iteration_tagged`.
- **contextvar isolation under concurrent iterations is correct.** `asyncio.gather`
  wraps each `_one_iteration`/`_one` in a Task that copies the context, so
  `iteration_path`/`_log_capture`/`current_node_id` don't bleed across
  concurrent iterations. Verified by `test_nested_loop_events_carry_full_path`.
- **`run_status` mutation across gathered `_one` tasks is safe** — single-threaded
  cooperative scheduling, no `await` between the read and write.

## Findings — Wave 3 (deferred Lows + remaining modules)

### RUN-1 — Approval decision endpoint missing `workflow:run` (Medium)
- **Evidence:** `routers/runs.py` `decide_run_approval` had no `require_permission`
  dependency, while every other run-mutating route (cancel/replay/rerun/retry)
  requires `workflow:run` (editor). Approving a side-effecting AI tool call AND
  resuming the run is at least as sensitive, so a `viewer` could authorize tool
  execution — a privilege escalation under the RBAC ladder.
- **Fix:** add `dependencies=[Depends(require_permission("workflow:run"))]`.
- **Status:** `fixed` — `test_approval_decision_requires_run_permission`.

### DEP-1 — Active deployment version-repoint bypasses the unsafe-node gate (Medium)
- **Evidence:** `routers/deployments.py` `update_deployment` only re-ran
  `_enforce_unsafe_node_policy` on an inactive→active flip. Repointing an
  already-active deployment's `workflow_version_id` to a risky version skipped
  the gate, so risky nodes could go live without acknowledgement.
- **Fix:** enforce when the resulting deployment is active AND (becoming active
  OR the version changed).
- **Status:** `fixed` — `test_active_deployment_version_repoint_re_enforces_unsafe_policy`.

### RD-2 — Dockerfile injection via package/python-version (Low)
- **Evidence:** `remote_dispatch._ensure_docker_image` interpolated `packages`
  and `python_version` into the `RUN uv pip install` / `FROM python:` lines.
- **Fix:** `_validate_packages` (PEP 508 name/extras/version, no shell
  metacharacters) and `_validate_python_version` (`X[.Y[.Z]]`). Admin-only path,
  validated as defence-in-depth.
- **Status:** `fixed` — `test_validate_packages_*`, `test_validate_python_version`.

### RP-1 / RP-2 — Runner artifact upload now revocable + run-bound (Low/Med)
- **Evidence:** `runner_pools.upload_artifact` accepted any valid
  `runner_registration` token for any `run_id`. The token was effectively
  irrevocable before its 24h expiry.
- **Fix:** RP-1 — reject when the runner row no longer exists (deleting a runner
  revokes its token everywhere, mirroring the WS path). RP-2 — when the run is
  already assigned (`run.runner_id` set), only that runner's token may upload.
  Docstring corrected (token is a *reusable, revocable* runner credential, not
  one-time — SSH-onboarded agents reuse it across restarts).
- **Status:** `fixed` — `test_artifact_upload_revoked_when_runner_deleted`,
  `test_artifact_upload_rejects_cross_run_runner`,
  `test_artifact_upload_allows_assigned_runner`.

### EVT-1 — In-process broker reaps abandoned non-finished runs (Low)
- **Evidence:** `events.py` recorded `_finished` only on `run_finished`; a run
  ending `waiting` (agent approval) and then abandoned leaked its buffer until
  restart (in-process transport only; Redis uses TTL).
- **Fix:** track `_last_activity` per run; `reap()` now also drops buffers with no
  new events for the TTL and no live subscriber.
- **Status:** `fixed` — `test_broker_reaps_abandoned_waiting_run`,
  `test_broker_keeps_fresh_waiting_run`.

### DSQ-2 — duckdb_sql LFI latch + deploy-gate the data-code nodes (Medium)
- **Evidence:** `duckdb_sql` (free-form author SQL) was ungated and could read
  arbitrary server files (`SELECT * FROM read_csv_auto('/etc/passwd')`) — the
  DSQ-1 class, but as a workflow node not covered by `unsafe_node_policy`.
  `polars_transform` was already AST-validated via `_CodeValidator`.
- **Fix:** (1) `duckdb_sql` now materializes the input into an in-memory TABLE,
  latches `SET enable_external_access=false` before the author query, then writes
  the Arrow result out via a fresh connection (mirrors DSQ-1). (2) Added
  `duckdb_sql` and `polars_transform` to `unsafe_nodes.classify` (kind `code`) so
  the deploy-time policy gate covers them.
- **Status:** `fixed` — `test_duckdb_sql_blocks_server_file_read`,
  `test_classify_flags_duckdb_and_polars_nodes`.

## TEST-1 — Pre-existing failing tests — RESOLVED
All 11 now pass:
- `default_registry` → `registry` + `.list()` → `.manifests()` in
  `test_statistical_analysis.py` (test bug).
- Optional-dep "raises_without_X" tests: the nodes now **validate inputs before
  importing the optional dependency** (`regression_analysis`,
  `optimization_solve`, `time_series_decompose`) — better UX (clear "X is
  required" instead of "install statsmodels") and the tests pass deps-free.
- `dimensionality_reduce` t-SNE: clamp `perplexity` to `< n_samples` so small
  datasets work.
- `test_import_does_not_import_optional_packages`: rewritten to check in a clean
  subprocess (the same-process `sys.modules` snapshot was polluted by sibling
  test modules).
- `test_map_items_workflow_id_required`: converted to an async test (was using
  `asyncio.get_event_loop().run_until_complete`, which raises on 3.12).
- **Status:** `fixed`.
