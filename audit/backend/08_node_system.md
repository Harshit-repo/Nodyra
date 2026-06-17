# 08 — Node System & Custom-Node Contract

Independent review, 2026-06-16. Files: `packages/core/noodle/sdk.py`,
`packages/core/noodle/models.py` (manifest/port/param specs),
`apps/api/app/routers/code_modules.py`, `apps/api/app/services/runner.py`
(in-process registration path), `packages/runtime/noodle_runtime/server.py`,
`packages/nodes/noodle_nodes/` (86 files, ~34k LOC).

## What is solid (verified)
- **Node = decorated Python function**: `@node` derives the manifest from the
  function signature — type hints → port/param specs, defaults → required-ness,
  `iscoroutinefunction` → async. Low-ceremony, Pythonic, exactly right for a
  "Python-first" tool.
- **Two discovery paths with a clean security split**:
  - **Static AST discovery** (`discover_module_nodes` /
    `discover_module_function_manifests`) builds manifests **without executing
    uploaded code** — used by the API (`code_modules.py`, `starter_graph.py`) for
    palette/preview. Verified: no exec-path caller in the API for manifests.
  - **Runtime registration** (`register_module_functions`) execs the module and
    is called only in the execution plane: the isolated runtime
    (`noodle_runtime/server.py`) and the runner's **in-process branch**
    (`runner.py:1028`, the `else` of the subprocess/sandbox path).
- **Namespacing & isolation**: user node ids are `user:<module_id>:<name>`;
  `_suppress_registration` ContextVar stops a bare `@node` in uploaded code from
  polluting the process-global registry; duplicate ids raise. In-process
  registration is **stripped after each run** (`loaded_module_ids` cleanup) so
  custom nodes don't leak across runs/workflows.
- **Port-kind contract**: typed ports (`data_kind`) with connection validation
  (`engine/validation.py`) — dataset/artifact/AI ports are strict, `any`/`main`
  are the escape hatches. Output kinds are validated at runtime too.
- **Rich UI metadata** without leaving Python: `params` carries choices,
  widgets, `display_when`/`hide_when`, credential selectors, `load_options`,
  validation — so schema-driven inspector forms come straight from the decorator.
- **Tool-capability defaults**: `usable_as_tool` auto-derived (executable,
  non-trigger, non-control-flow) so most nodes are agent-callable without
  per-node wiring; `tool_side_effecting` flags mutation.

## Findings

### NODE-1 — In-process module registration execs uploaded code (MEDIUM — same boundary as SAFE-3)
`runner.py:1019-1034` (the in-process branch) calls `register_module_functions`,
which `exec`s the uploaded module (`_CodeValidator` only blocks
ClassDef/Global/Nonlocal + dunder names — **imports are allowed**, so it's not a
containment boundary). This runs in the **host process** when
`use_subprocess_runner=False` / no sandbox.
- **Impact:** none in the default/production config (subprocess pool or sandbox
  isolates it); but a deployment that flips `use_subprocess_runner=False` for
  "simplicity" would exec uploaded module top-level code in the API/worker
  process that holds the master KEK + DB creds.
- **Fix:** same as SAFE-3 — document that `use_subprocess_runner=False` is
  dev/test only and forbid it under `multi_tenancy_enabled`/production (extend the
  AUTH-1 startup guard or `enforce_sandbox_policy`); add a regression test that
  custom-module execution never happens in the parent process in MT mode.
- **Status:** **Fixed.** `enforce_sandbox_policy()` now rejects
  `use_subprocess_runner=False` under multi-tenancy (and under any non-`off`
  sandbox, as a config-lie guard), so `register_module_functions` cannot run in the
  host process in those modes. Covered by `test_sandbox_policy.py`.

### NODE-2 — No node-manifest versioning / migration story (LOW)
Manifests carry a `version` and `deprecated`/`replacement_id`, which is good, but
there's no visible mechanism to migrate a saved graph's node params when a
built-in node's schema changes (param renamed/removed). Old graphs silently get
defaults or drop params.
- **Recommendation:** define a param-migration hook keyed on
  `node_type_version`, or document the compatibility policy (additive-only param
  changes). Important before a stable plugin/node API for OSS authors.
- **Status:** Needs decision.

### NODE-3 — `__builtins__` passed wholesale to exec'd modules (LOW, by design)
`register_module_functions` execs with `"__builtins__": __builtins__` (full
builtins). Consistent with "code nodes are arbitrary Python", but worth a comment
that this is intentional and that the boundary is the process/container, not the
builtins set.
- **Status:** Reviewed (doc).

## Verdict
The node system is a **genuine product strength** and the static-vs-exec split is
exactly the right security design for a code-first plugin model. The residual
items are the shared SAFE-3 sandbox-policy hardening (NODE-1) and a node-schema
versioning/migration policy (NODE-2) before opening the node API to third-party
authors.
