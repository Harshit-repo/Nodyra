# 09 — Python Code Execution Safety (CRITICAL area)

Independent review, 2026-06-16. Files read: `packages/core/nodyra/expr.py`,
`apps/api/app/services/unsafe_nodes.py`, `apps/api/app/services/isolation.py`,
`apps/api/app/services/sandbox_policy.py`,
`apps/api/app/services/executors/sandbox.py`,
`apps/api/app/services/container_runtime.py`, and the dispatch gate in
`apps/api/app/services/runner.py` (start_run).

## Threat model (as built)

Nodyra runs **arbitrary user Python** in three places:
1. **Code nodes / DuckDB / Polars** — explicitly arbitrary Python/SQL.
2. **Expressions** (`{{ … }}`) — restricted mini-language.
3. **Per-env package installs** — admin-controlled image/venv builds.

The product correctly treats the **expression evaluator as a sandbox** and the
**process/container boundary as the real isolation** for code nodes.

## What is solid (verified, not just claimed)

- **Expression sandbox (`expr.py`)** — AST allowlist via `generic_visit` plus
  name/attribute blocklist dispatch. The EXPR-1 regression (overriding `visit`
  disabling `visit_Name`/`visit_Attribute`) is fixed and documented at
  `expr.py:88-99`. Builtins restricted to a safe map; context exposes only
  wrapped JSON + `datetime`. The classic `().__class__.__bases__[0].__subclasses__()`
  and `__import__` escapes are blocked by `_BLOCKED_NAMES`. **Assessment: sound.**
- **Container hardening (`container_runtime.hardening_kwargs`)** —
  `cap_drop:["ALL"]`, `no-new-privileges:true`, `read_only:True`, tmpfs-only
  writable surface, `mem_limit`/`nano_cpus`/`pids_limit`, pluggable `runtime`
  (gVisor/Kata/runc). The **security floor is non-overridable** (only resource
  ceilings + network are); this is the correct split. **Assessment: strong.**
- **Build-time injection (RD-2)** — `_validate_packages` / `_validate_python_version`
  hard-reject shell metacharacters before they reach the generated Dockerfile's
  `RUN uv pip install`. **Assessment: correct defence-in-depth.**
- **Multi-tenant policy interlock (`sandbox_policy.enforce_sandbox_policy`)** —
  `multi_tenancy_enabled=true` + `sandbox_policy_strict` forces
  `execution_sandbox=required`; enforced in both API lifespan and `worker_main`
  before any dispatch. **Assessment: correct fail-closed default.**
- **Dispatch chokepoint (`runner.start_run`, X4)** — a `dedicated_pool` org is
  refused on the shared warm pool at the single dispatch chokepoint, not just at
  assignment time (`isolation.validate_pool_assignment` is the friendly pre-check).
  **Assessment: defence in the right place.**
- **Deploy-time risk classifier (`unsafe_nodes.classify`)** — thorough,
  data-driven catalogue (code/command/ssh/filesystem/private-IP/SSRF/SQL-injection)
  with a 4-rung policy ladder (allow/warn/require_approval/block). **Assessment:
  a genuine product strength.**

## Findings

### SAFE-1 — Expression alias substitution is a textual `.replace()` (Low, correctness)
`expr.py:344-347` rewrites `$json`→`_json` etc. via `str.replace()` on the raw
expression *before* parsing. A literal string inside the expression that contains
`$json` (e.g. `{{ "$json is the input" }}`) is corrupted to `"_json is the input"`.
- **Impact:** wrong output, not a security hole. Edge case, but silent.
- **Fix:** tokenize/parse first, or restrict replacement to identifier
  boundaries (`\b\$json\b`), or expose the bindings under their real `$` names by
  pre-binding them in the eval namespace instead of textual rewriting.
- **Test:** `{{ "$json literal" + " x" }}` should evaluate to `"$json literal x"`.
- **Status:** Reviewed.

### SAFE-2 — `unsafe_nodes` private-IP check is literal-only; ad-hoc runs are ungated (Medium, by-design but document)
`_is_private_host` intentionally does **not** resolve DNS (`expr`… see
`unsafe_nodes.py:183-210`), and the whole classifier only runs at **deployment
activation**, not on manual/editor runs.
- **Impact:** SSRF to internal services via a DNS name (`http://internal.corp/`)
  or a manual run is not caught by this layer. This is an accepted limitation —
  the comment is explicit — but it means the *network boundary* (sandbox
  `network` mode / egress policy), not this classifier, must be the real SSRF
  control in multi-tenant mode.
- **Recommendation:** (a) document that `EXECUTION_SANDBOX=required` with a
  locked-down `network` is the SSRF control, not `unsafe_nodes`; (b) consider an
  opt-in egress allowlist for sandbox containers; (c) confirm manual editor runs
  in multi-tenant mode still route through the sandbox (they should via the
  dispatch gate — verify in a dedicated test).
- **Status:** **Hardened.** `enforce_sandbox_policy()` now requires a dedicated,
  non-empty `sandbox_network` under multi-tenancy (so run containers cannot reach
  postgres/redis/minio on the default bridge) — making the network boundary, not
  the deploy-time classifier, the enforced SSRF control for infra services.
  Internet-egress allowlisting (item b) remains the follow-up.

### SAFE-3 — Code-node AST validator is cosmetic; rely only on the process boundary (Informational/Medium)
`_CodeValidator` (`expr.py:129-154`) blocks only `ClassDef`/`Global`/`Nonlocal`
and a dunder name list, **but allows `import`** — so `import os; os.system(...)`
runs. This is intended ("users may freely import any installed library"), but the
blocklist gives a false impression of containment.
- **Impact:** None *if* every code-node execution path is process/container
  isolated. The risk is a future code path that runs a code node **in-process**
  (e.g. a preview, a "test node" shortcut, an export evaluator) bypassing the
  subprocess pool — that would be RCE on the API host.
- **Recommendation:** add a regression test asserting code nodes **never** execute
  in the API/worker parent process (assert the subprocess/sandbox executor is
  invoked), and a comment at the `_CodeValidator` declaration that it is *not* a
  security boundary.
- **Status:** **Fixed at the configuration boundary.** `enforce_sandbox_policy()`
  now refuses to boot when `use_subprocess_runner=False` under multi-tenancy or any
  non-`off` sandbox — the runner's in-process branch (which checks
  `use_subprocess_runner` before the sandbox) is unreachable in those modes, so a
  code node cannot run in the parent process. `test_sandbox_policy.py` asserts the
  invariant (TEST-6). The `_CodeValidator`-is-not-a-boundary comment is still worth
  adding inline.

### SAFE-4 — Single-tenant default shares the host kernel (Informational)
With `multi_tenancy_enabled=false` (the default), code nodes run in warm
**subprocess** pools on the host kernel, not containers. Correct and acceptable
for trusted single-tenant self-hosting; just ensure the README/security docs
state plainly: *multi-user/untrusted = set `EXECUTION_SANDBOX=required`.*
- **Status:** Reviewed — doc item.

## Tests to add
- Expression: literal-`$`-string regression (SAFE-1); attribute/`__class__`
  escape attempts return `[expr error: …]` not a value.
- Sandbox routing: code node in `multi_tenancy_enabled=true` asserts
  SandboxExecutor path; `enforce_sandbox_policy` raises when misconfigured.
- Code node never runs in parent process (SAFE-3).

## Verdict
Execution-safety architecture is **above average for this class of tool** and
clearly the product's most carefully engineered area. No critical escape found in
this pass. Residual risk is operational/documentation (SAFE-2/-4) plus one
correctness bug (SAFE-1). Recommend the regression tests above before SaaS launch.
