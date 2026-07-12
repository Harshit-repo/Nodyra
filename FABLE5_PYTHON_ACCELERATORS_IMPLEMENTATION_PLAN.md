# Python Accelerators — Production Implementation Plan

Status: READY FOR IMPLEMENTATION
Scope: per-environment interpreter selection (CPython / free-threaded CPython / PyPy),
per-environment runtime flags (CPython JIT, lazy imports), worker cold-start hygiene,
post-build smoke/compat checks, and opt-in mypyc compilation of node modules.
Out of scope: Mojo, PythoC, Cython (documented as future work only).

This plan is written to be executed by an LLM agent with no prior context. Every
change lists the exact file, the anchor to find, and the behavior to implement.
Follow the repo conventions: ruff line-length 100, `target-version py312`, async
SQLAlchemy, fail-closed error handling, and explanatory comments in the existing
style (explain *why*, not *what*).

---

## 0. Ground truth (verified against the codebase)

| Concern | Location |
|---|---|
| Environment model | `apps/api/app/models.py` — `class Environment` (line ~158), `class EnvironmentBuildJob` (line ~210) |
| Env schemas + `SUPPORTED_PYTHON_VERSIONS = ("3.12", "3.13", "3.14")` | `apps/api/app/schemas.py` (constant at line ~10, `EnvironmentCreate` ~217, `EnvironmentUpdate` ~245, `EnvironmentInfo` ~304) |
| Env REST router | `apps/api/app/routers/environments.py` (`create_environment` ~151, `update_environment` ~205, `list_backends` ~257 returns `supported_python_versions`, `_to_info` ~54) |
| venv backend (uv) | `apps/api/app/services/backends/venv.py` — `VenvBackend.build`, `_do_build(env_id, python_version, packages, index_urls)`, `uv venv --python <version>` at line ~63, `_measure_worker_rss` |
| conda / pixi backends | `apps/api/app/services/backends/conda.py`, `pixi.py` |
| Worker spawn + env allowlist | `apps/api/app/services/runtime_pool.py` — `_WORKER_ENV_ALLOWLIST` (~line 132), `_worker_env()` (~167), `_RuntimeProcess.spawn` (~306), `_resolve_pool_sizes` (~52) |
| Build jobs (hash + snapshot) | `apps/api/app/services/environment_builds.py` — `_environment_hash` (~83), `_snapshot_kwargs` (~94), `process_environment_build_job` (~362) |
| Runtime worker entry | `packages/runtime/nodyra_runtime/server.py` — emits `{"type": "ready"}` at line ~302; `packages/runtime/nodyra_runtime/__main__.py` |
| Built-in node registration | `packages/nodes/nodyra_nodes/__init__.py` — imports ~55 modules at import time |
| MCP env tools | `apps/api/app/mcp/tools.py` — `create_environment` et al. |
| Web UI | `apps/web/src/EnvironmentsPage.tsx` (`SUPPORTED_PYTHON_VERSIONS` at line 34, create payload ~346), `apps/web/src/types.ts` (~436), `apps/web/src/api.ts` (~521) |
| Latest alembic migration | `apps/api/alembic/versions/0087_runs_error.py` → new migration is `0088_environment_interpreter_flags.py` |
| Relevant tests | `apps/api/tests/test_environments.py`, `test_venv_service.py`, `test_runtime_pool.py`, `test_worker_env.py`, `test_environment_build_jobs.py` |

Key facts the design relies on:

- Environments are built with `uv venv --python <python_version>`; uv accepts
  free-threaded requests (`3.13t`, `3.14t`) and PyPy requests (`pypy@3.10`,
  `pypy@3.11`) in the same argument position.
- `python_version` is create-only today (`EnvironmentUpdate` has no
  `python_version` field). The new `interpreter` field follows the same rule.
- Worker subprocesses get an allowlisted environment from `_worker_env()`;
  anything not allowlisted is stripped, so `PYTHON_JIT` must be injected
  explicitly at spawn time.
- `nodyra_nodes` modules already keep heavy imports (pandas/boto3/openpyxl)
  at function scope — phase 2 adds a regression guard so it stays that way.

---

## Phase 0 — Shared plumbing (data model, schemas, API, build jobs)

### 0.1 Model columns — `apps/api/app/models.py`

Add to `class Environment` directly under the existing `backend_config` column:

```python
# Interpreter implementation for this environment. "cpython" (default),
# "cpython-ft" (free-threaded, PEP 703 builds: 3.13t/3.14t), or "pypy".
# Create-only, like python_version: changing implementations in place would
# invalidate every installed wheel, so a new environment is the safe unit.
interpreter: Mapped[str] = mapped_column(String(20), default="cpython", nullable=False)
# Per-worker interpreter tuning applied at subprocess spawn (not at build):
# {"jit": bool, "lazy_imports": bool}. Mutable without a rebuild — flags
# take effect for the next spawned worker, same semantics as pool sizing.
runtime_flags: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
```

Add to `class EnvironmentBuildJob` under its `backend_config` column (snapshot
parity — jobs record exactly what they built):

```python
interpreter: Mapped[str] = mapped_column(String(20), nullable=False, default="")
```

### 0.2 Migration — `apps/api/alembic/versions/0088_environment_interpreter_flags.py`

Read `0087_runs_error.py` first and copy its structure (revision ids, imports,
`server_default` conventions). The migration must:

- `add_column("environments", sa.Column("interpreter", sa.String(20), nullable=False, server_default="cpython"))`
- `add_column("environments", sa.Column("runtime_flags", sa.JSON(), nullable=False, server_default="{}"))`
- `add_column("environment_build_jobs", sa.Column("interpreter", sa.String(20), nullable=False, server_default=""))`
- Provide a symmetric `downgrade()` dropping all three columns.

### 0.3 Schemas — `apps/api/app/schemas.py`

Next to `SUPPORTED_PYTHON_VERSIONS` (line ~10) add:

```python
# Interpreter → supported minor versions. cpython-ft (free-threaded) exists
# from 3.13; PyPy tracks its own version stream. Non-cpython interpreters are
# venv/uv-only: conda and pixi resolve their own interpreter builds and do not
# understand uv's "3.14t" / "pypy@3.11" request syntax.
SUPPORTED_INTERPRETERS: dict[str, tuple[str, ...]] = {
    "cpython": SUPPORTED_PYTHON_VERSIONS,
    "cpython-ft": ("3.13", "3.14"),
    "pypy": ("3.10", "3.11"),
}
# Allowlisted runtime flag keys (all boolean). "jit" → PYTHON_JIT=1,
# "lazy_imports" → PYTHON_LAZY_IMPORTS=1 on spawned workers.
SUPPORTED_RUNTIME_FLAGS = ("jit", "lazy_imports")
```

`EnvironmentCreate`:
- Add `interpreter: Literal["cpython", "cpython-ft", "pypy"] = "cpython"` and
  `runtime_flags: dict = Field(default_factory=dict)`.
- Extend the existing `validate_python_version` model validator (keep its name):
  - Resolve `minor_version` as today.
  - Validate `minor_version in SUPPORTED_INTERPRETERS[self.interpreter]`; error
    message must name the interpreter and its supported versions.
  - If `self.interpreter != "cpython"`: require `len(parts) == 2` (minor-only —
    uv patch pinning is not supported for `t`/`pypy` request syntax) and require
    `self.backend == "venv"` with error
    `"free-threaded CPython and PyPy environments require the venv backend"`.
  - Validate `runtime_flags`: every key in `SUPPORTED_RUNTIME_FLAGS`, every
    value a `bool`; reject unknown keys listing the supported ones.
  - If `self.interpreter == "pypy"` and `runtime_flags.get("jit")`: reject with
    `"the 'jit' flag is CPython-only; PyPy always JIT-compiles"`.

`EnvironmentUpdate`:
- Add `runtime_flags: dict | None = None` (interpreter is deliberately absent —
  create-only). Add a model validator applying the same key/value checks when
  not None (extract a module-level `def _validate_runtime_flags(flags: dict) -> None`
  shared by both schemas).

`EnvironmentInfo`: add `interpreter: str = "cpython"` and
`runtime_flags: dict = Field(default_factory=dict)`.

`EnvironmentBuildJobInfo`: add `interpreter: str = ""`.

### 0.4 Router — `apps/api/app/routers/environments.py`

- `create_environment`: persist `interpreter=payload.interpreter` and
  `runtime_flags=dict(payload.runtime_flags)` on the new `Environment` row.
- `update_environment`: when `payload.runtime_flags is not None`, assign
  `env.runtime_flags = dict(payload.runtime_flags)`. No rebuild is enqueued —
  flags are spawn-time. Include the field in the audit log detail like the
  other mutations there.
- `_to_info`: pass through `interpreter=env.interpreter` and
  `runtime_flags=dict(env.runtime_flags or {})`.
- `list_backends` (~257): alongside `"supported_python_versions"` add
  `"supported_interpreters": {k: list(v) for k, v in SUPPORTED_INTERPRETERS.items()}`
  and `"supported_runtime_flags": list(SUPPORTED_RUNTIME_FLAGS)`.

### 0.5 Build jobs — `apps/api/app/services/environment_builds.py`

- `_environment_hash`: add `"interpreter": env.interpreter` to the payload dict
  (changes the hash for all envs once — acceptable; hash equality is only used
  to dedupe queued jobs).
- `_snapshot_kwargs`: add `"interpreter": str(env.interpreter or "cpython")`.

### 0.6 MCP tools — `apps/api/app/mcp/tools.py`

Locate the `create_environment` MCP tool. Add optional `interpreter` and
`runtime_flags` parameters mirroring `EnvironmentCreate` (route them through the
same Pydantic schema so validation stays single-sourced). Update the tool's
docstring/description so agents discover the capability.

### 0.7 Phase 0 tests

- `apps/api/tests/test_environments.py` — add cases:
  - create with `interpreter="cpython-ft", python_version="3.14"` → 201, info
    echoes interpreter.
  - `interpreter="cpython-ft", python_version="3.12"` → 422 (unsupported).
  - `interpreter="pypy", python_version="3.11", backend="conda"` → 422 (venv-only).
  - `interpreter="pypy", python_version="3.14"` → 422.
  - `runtime_flags={"jit": True}` roundtrips; `{"jit": "yes"}` → 422;
    `{"unknown": True}` → 422; PATCH `runtime_flags={"lazy_imports": True}` → 200
    and does NOT change env status (no rebuild).
  - `interpreter="pypy"` + `runtime_flags={"jit": True}` → 422.
  - default create (no new fields) still works → `interpreter == "cpython"`.
- `apps/api/tests/test_environment_build_jobs.py`: assert new snapshots carry
  `interpreter` and that `_environment_hash` differs for two envs identical
  except interpreter.

---

## Phase 1 — Runtime flags reach workers (CPython JIT, lazy imports)

### 1.1 Flag resolution — `apps/api/app/services/runtime_pool.py`

Add near `_resolve_pool_sizes` (same style, same graceful degradation):

```python
async def _resolve_env_runtime_flags(env_id: str | None) -> dict[str, bool]:
    """Per-environment runtime flags ({"jit": bool, "lazy_imports": bool}).

    Read fresh on every worker spawn (spawns are rare and already pay a DB
    round-trip via ensure_environment_ready), so a PATCH takes effect for the
    next worker without an API restart. Any failure degrades to {} — flags are
    accelerators, never a reason to fail a dispatch.
    """
```

Implementation: `env_id is None` → `{}`; else `session.get(Environment, env_id)`
and return `{k: bool(v) for k, v in (env.runtime_flags or {}).items() if v}`
inside the same `try/except Exception` fallback pattern `_resolve_pool_sizes` uses.

### 1.2 Spawn injection — `_RuntimeProcess.spawn` (same file, ~line 306)

After `env = _worker_env()` and before `create_subprocess_exec`:

```python
flags = await _resolve_env_runtime_flags(env_id)
# PYTHON_JIT / PYTHON_LAZY_IMPORTS are read by CPython at startup; unknown
# or unsupported vars are ignored by the interpreter, so passing them to an
# interpreter without the feature is harmless by design.
if flags.get("jit"):
    env["PYTHON_JIT"] = "1"
if flags.get("lazy_imports"):
    env["PYTHON_LAZY_IMPORTS"] = "1"
```

Do NOT add these names to `_WORKER_ENV_ALLOWLIST` — the host process's own
`PYTHON_JIT` must not leak into workers implicitly; the per-env flag is the only
source of truth.

### 1.3 Sandbox parity — `apps/api/app/services/sandbox_pool.py`

Grep for where the sandbox container's environment variables are assembled
(the container runs the same `nodyra_runtime`). Inject the same two variables
from the environment row if env construction is reachable there; if the row is
not available at that call site, plumb the flags through the same way env_id is
plumbed. If after 30 minutes of investigation this is genuinely invasive,
leave sandbox unsupported and add one line to `docs/accelerators.md`:
"runtime flags currently apply to pool workers, not sandboxed runs" — do not
half-wire it.

### 1.4 Phase 1 tests

- `apps/api/tests/test_runtime_pool.py` or `test_worker_env.py` (pick the file
  that already unit-tests spawn env construction; follow its mocking style):
  - env row with `runtime_flags={"jit": True}` → spawned env contains
    `PYTHON_JIT == "1"` and no `PYTHON_LAZY_IMPORTS`.
  - flags `{}` → neither variable present.
  - host process `os.environ["PYTHON_JIT"] = "1"` (monkeypatch) with flags `{}`
    → variable still absent (allowlist strip verified).
  - DB error path → `_resolve_env_runtime_flags` returns `{}` (no raise).

---

## Phase 2 — Cold-start hygiene and observability

### 2.1 Import-hygiene regression guard — new `packages/nodes/tests/test_import_hygiene.py`

A subprocess test (so the parent test process's imports don't pollute the check):

```python
HEAVY = ["pandas", "numpy", "boto3", "openpyxl", "matplotlib", "sklearn", "PIL"]
```

Run `sys.executable -c "import sys, json, nodyra_nodes; print(json.dumps([m for m in <HEAVY> if m in sys.modules]))"`
and assert the list is empty. If any module trips the guard, find the offending
top-level import in `packages/nodes/nodyra_nodes/` and move it to function scope
(this is the actual fix, not weakening the test). Docstring must explain: warm
pool scale-up and sandboxed per-run containers pay `import nodyra_nodes` on
every spawn, so heavy libraries must stay at function scope.

### 2.2 Startup timing in the ready event — `packages/runtime/nodyra_runtime/server.py`

- At module top (immediately after the existing imports): `_PROC_START = time.monotonic()`
  (add `import time`).
- Change line ~302 `_emit({"type": "ready"})` to
  `_emit({"type": "ready", "startup_ms": int((time.monotonic() - _PROC_START) * 1000)})`.
- Host side, `_RuntimeProcess.spawn` in `runtime_pool.py` already parses the
  ready line; after validation add
  `logger.info("runtime worker ready env=%s startup_ms=%s", env_id, ready.get("startup_ms"))`.
  Use `.get` — old workers without the field must keep working (rolling deploys).
- Update the protocol docstring at the top of `server.py` to document the field.

### 2.3 Lazy imports (3.15 forward-compat)

No version gating code: the `lazy_imports` flag already passes
`PYTHON_LAZY_IMPORTS=1` (phase 1), which pre-3.15 interpreters ignore. Document
in `docs/accelerators.md` (phase 6) that the flag becomes meaningful on 3.15+
and is experimental. When 3.15 lands in `SUPPORTED_PYTHON_VERSIONS` (separate
future change), nothing else needs touching. Do NOT add "3.15" now.

### 2.4 Phase 2 tests

- `packages/runtime/tests/test_server.py`: extend the existing ready-event
  assertion to check `startup_ms` is a non-negative int.
- The import-hygiene test itself (2.1).

---

## Phase 3 — Free-threaded CPython (venv backend)

### 3.1 uv request builder — `apps/api/app/services/backends/venv.py`

Add a module-level pure function (unit-testable):

```python
def uv_python_request(interpreter: str, python_version: str) -> str:
    """Translate (interpreter, version) into uv's --python request syntax.

    cpython     → "3.14"        (unchanged, patch pins allowed)
    cpython-ft  → "3.14t"       (uv's free-threaded suffix)
    pypy        → "pypy@3.11"
    Unknown interpreters raise ValueError: builds must fail loudly, not fall
    back to the wrong interpreter.
    """
```

- `VenvBackend.build` passes `env.interpreter` into `_do_build`; `_do_build`
  gains an `interpreter: str = "cpython"` parameter and uses
  `uv_python_request(interpreter, python_version)` at the `uv venv` call.

### 3.2 Post-build smoke check (all interpreters) — same file

New async helper `_smoke_check_runtime(env_id) -> str | None` modeled directly
on `_measure_worker_rss` (reuse its spawn/teardown skeleton): boot
`<env python> -u -m nodyra_runtime`, wait ≤30s for the `ready` line, return
`None` on success or a human-readable error string on failure (include the
first 2000 chars of captured stderr — that is where the import traceback is).

Call it in `_do_build` after a successful install; on failure return
`("error", f"environment built but the Nodyra runtime failed to start: {reason}")`.
This is fail-closed: an env whose worker cannot boot must never reach
`status="ready"`. This matters most for PyPy/free-threaded, but protects
regular envs (e.g. a user package that breaks pydantic) too — intentional.

### 3.3 GIL status report (cpython-ft only) — same file

After a successful smoke check, when `interpreter == "cpython-ft"`, run:

```python
code, out = await _run(
    str(venv_python(env_id)), "-c",
    "import sys; print('gil-disabled' if not sys._is_gil_enabled() else 'gil-enabled')",
)
```

If the output is not `gil-disabled`, append a WARNING line to the build log
(not an error): extension modules without free-threaded support re-enable the
GIL at import; the env still works, just without parallelism. The warning text
must say exactly that so users understand what they got.

### 3.4 Phase 3 tests — `apps/api/tests/test_venv_service.py`

- `uv_python_request` unit tests: all three interpreters, plus
  `ValueError` on `"graalpy"`.
- `_do_build` monkeypatch tests (this file already mocks `_run`): assert the
  `uv venv` argv contains `"3.14t"` for cpython-ft and `"pypy@3.11"` for pypy;
  assert a failing `_smoke_check_runtime` yields `("error", ...)` and a passing
  one yields `("ready", ...)`.

---

## Phase 4 — PyPy (venv backend)

Mechanically, phases 0/3 already did the work (validators, `pypy@X.Y` request,
smoke check). This phase is hardening and honesty:

- 4.1 In `_do_build`, when `interpreter == "pypy"` and the smoke check passed,
  append an informational line to the build log: PyPy accelerates pure-Python
  code; C-extension-heavy workloads (pandas, numpy) may be slower than CPython.
- 4.2 Confirm `local_nodyra_packages()` (core/nodes/runtime/runner) install on
  PyPy — they are pure Python, but the smoke check is the enforcement point;
  no extra code needed beyond 3.2. If the smoke check exposes a hard PyPy
  incompatibility in `nodyra_runtime` imports during your own verification run,
  fix only trivial ones (e.g. a gated import); otherwise record it in the
  review notes — do not attempt a PyPy porting project.
- 4.3 Tests: covered by 0.7 (validation) and 3.4 (request syntax). Add one
  `test_environments.py` case: pypy create with `backend="pixi"` → 422.

---

## Phase 5 — Opt-in mypyc compilation of node modules

Scope guard: this accelerates *user-supplied node package modules already
installed in the env*, opt-in, best-effort. It must never fail a build.

### 5.1 Config surface

`backend_config` gains an optional key (venv backend only, validated in
`_validate_runtime_flags`'s sibling — add `_validate_backend_config(cfg, interpreter)`
called from both env schemas):

```json
{"accelerate": {"mypyc_modules": ["my_pkg.transforms", "my_pkg.parsing"]}}
```

Validation: `accelerate` must be a dict; `mypyc_modules` a list of 1–50 dotted
module names matching `^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$`;
rejected when `interpreter == "pypy"` (mypyc emits CPython C-API extensions).

### 5.2 Compile step — new file `apps/api/app/services/backends/accelerate.py`

```python
async def mypyc_compile(python: Path, modules: list[str]) -> tuple[bool, str]:
    """Best-effort mypyc compilation of installed modules, in place.

    Returns (all_ok, log). Never raises. Steps per invocation:
    1. `uv pip install --python <python> "mypy>=1.10" setuptools` (build deps).
    2. Resolve each module to its source file inside the env:
       `<python> -c "import importlib.util,sys; spec=importlib.util.find_spec(m); print(spec.origin)"`.
       Skip (with a log line) modules that don't resolve or aren't .py files.
    3. Run `<python> -m mypyc <origin.py>` with cwd set to the module's
       site-packages root so the built extension lands next to the source
       (CPython prefers the .so/.pyd over the .py at import time).
    4. Verify: re-run find_spec and log whether origin now points at the
       compiled extension.
    """
```

Wire into `_do_build` after the smoke check: if
`backend_config["accelerate"]["mypyc_modules"]` is non-empty, call it and append
its log. `all_ok=False` appends `"WARNING: mypyc acceleration incomplete"` —
build status stays `"ready"` (the pure-Python modules still work). Then re-run
`_smoke_check_runtime` once more: if compilation broke the runtime, delete the
compiled artifacts is complex — instead fail the build with a clear message
telling the user to remove the module from `mypyc_modules` and rebuild. That is
fail-closed without artifact surgery.

### 5.3 Phase 5 tests — extend `test_venv_service.py` + `test_environments.py`

- Schema: valid `accelerate` accepted on venv+cpython; rejected on pypy;
  malformed module name → 422; >50 modules → 422.
- `mypyc_compile` with a monkeypatched `_run`: asserts install-then-compile
  argv sequence, and that a nonzero compile exit yields `(False, log)` without
  raising.
- `_do_build`: compile failure still returns `("ready", ...)` with the warning;
  post-compile smoke failure returns `("error", ...)`.

---

## Phase 6 — UI + docs

### 6.1 `apps/web/src/types.ts` (~436) and `apps/web/src/api.ts` (~521)

Add `interpreter: string` and `runtime_flags: Record<string, boolean>` to the
environment info type; add both as optional fields to the create payload and
`runtime_flags` to the update payload.

### 6.2 `apps/web/src/EnvironmentsPage.tsx`

- Replace the constant at line 34 with structures mirroring the backend:
  `SUPPORTED_INTERPRETERS: Record<string, string[]>` (same values as schemas.py)
  and keep `SUPPORTED_PYTHON_VERSIONS` as the cpython entry for compatibility.
  (Verified: `apps/web/src/api.ts` line ~518 already fetches
  `/environments/backends` — extend that response type with
  `supported_interpreters` / `supported_runtime_flags` and consume it; keep the
  local constant only as a fallback default with a comment pointing at
  `SUPPORTED_INTERPRETERS` in schemas.py as the source of truth.)
- Create form: an "Interpreter" select (labels: "CPython (default)",
  "CPython free-threaded — experimental", "PyPy — experimental"), which filters
  the Python-version select to the interpreter's supported list and disables
  the conda/pixi backend options for non-cpython interpreters.
- Create + edit forms: two checkboxes under an "Acceleration" heading —
  "Enable CPython JIT (PYTHON_JIT=1)" (hidden for PyPy) and
  "Lazy imports (Python 3.15+, experimental)". Helper text: "Applies to newly
  started workers; running workers are unaffected until recycled."
- Environment card line (~707): when interpreter ≠ cpython append
  ` · free-threaded` or ` · PyPy`; when `runtime_flags.jit` append ` · JIT`.
- Follow the file's existing form-state and styling patterns exactly.

### 6.3 Docs — new `docs/accelerators.md` + README pointer

`docs/accelerators.md` sections: what each accelerator is (one paragraph each,
JIT / lazy imports / free-threaded / PyPy / mypyc); how to enable (UI + API
JSON examples); when each helps and when it hurts (PyPy vs C extensions,
GIL re-enable warning, JIT gains on long-running CPU-bound code nodes);
flags-apply-on-next-worker semantics; sandbox limitation if 1.3 took that path.
Add one line to `README.md`'s "Key Capabilities" area linking to it.

---

## Phase 7 — Verification (the implementing agent MUST run these)

```bash
cd <repo root>
uv run ruff check apps/api packages/nodes packages/runtime
uv run pytest apps/api/tests/test_environments.py apps/api/tests/test_venv_service.py \
  apps/api/tests/test_runtime_pool.py apps/api/tests/test_worker_env.py \
  apps/api/tests/test_environment_build_jobs.py -q --timeout=120
uv run pytest packages/runtime/tests -q --timeout=120
uv run pytest packages/nodes/tests/test_import_hygiene.py -q --timeout=120
uv run alembic -c apps/api/alembic.ini upgrade head   # (verified ini path)
```

Also `cd apps/web && npx tsc --noEmit` if the web workspace typechecks cleanly
before your changes (verify first; if it was already red, don't chase pre-existing
errors — just ensure your files introduce none).

Acceptance criteria:
1. All commands above pass; no test is skipped/weakened to make it pass.
2. A default `EnvironmentCreate` payload (no new fields) behaves byte-identically
   to before (backward compat).
3. `PYTHON_JIT` appears in worker env only via `runtime_flags` — proven by test.
4. Build of an env whose runtime cannot boot ends in `status="error"` — proven
   by test with mocked smoke check.
5. No new dependencies added to `pyproject.toml` dependency groups (mypy is
   installed into target envs at build time, not into the API's env).

## Risks / rollback

- The migration is additive with server defaults — rollback is `downgrade()`.
- All spawn-time behavior is gated behind per-env flags that default to off;
  a bad interaction is contained to environments that opted in.
- The smoke check adds ~1–3s to every env build (one interpreter boot). This is
  accepted: builds are rare, broken-env detection at build time is worth it.
- uv must be able to download interpreter builds (3.14t, pypy) — on air-gapped
  deployments this fails at build time with uv's own error in the build log,
  which is the correct surface. No pre-flight check needed.
