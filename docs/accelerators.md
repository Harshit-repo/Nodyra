# Python accelerators

Nodyra environments can opt into a handful of Python-level performance
accelerators: the CPython JIT, lazy imports, free-threaded CPython, PyPy, and
opt-in mypyc compilation of your own node package modules. All of them are
off by default and scoped per-environment — turning one on for an
environment never affects any other environment.

## What each accelerator is

**CPython JIT (`jit`).** CPython's experimental just-in-time compiler
(`PYTHON_JIT=1`). Helps long-running, CPU-bound pure-Python code (tight
loops, numeric code without a C-accelerated library underneath). It does very
little for code that's mostly waiting on I/O (HTTP requests, database
queries) or already delegates the hot loop to a C extension (pandas, numpy).

**Lazy imports (`lazy_imports`).** Sets `PYTHON_LAZY_IMPORTS=1`, deferring
module imports until first use. This is a CPython 3.15+ feature; on earlier
interpreters the environment variable is simply ignored by the interpreter,
so setting the flag today is forward-compatible groundwork rather than an
active optimization. Treat it as experimental until 3.15 is a supported
`python_version`.

**Free-threaded CPython (`interpreter: cpython-ft`).** A PEP 703 build of
CPython (3.13t/3.14t) with the GIL disabled by default, allowing genuine
multi-threaded parallelism for pure-Python code. Extension modules that
haven't been updated for free-threading re-enable the GIL at import time —
the environment still works, it just loses the parallelism. The build log
records a warning when this happens.

**PyPy (`interpreter: pypy`).** An alternative interpreter with its own
JIT that accelerates pure-Python code, often dramatically. PyPy always
JIT-compiles (there's no separate flag to enable), so the `jit` runtime flag
is rejected for PyPy environments. C-extension-heavy workloads (pandas,
numpy) can be *slower* than CPython, because PyPy's C-API compatibility
layer (`cpyext`) adds overhead those packages don't pay on CPython itself —
PyPy is the right choice for pure-Python-heavy workflows, not necessarily
data-science ones.

**mypyc compilation (`backend_config.accelerate.mypyc_modules`).** Best-effort,
opt-in ahead-of-time compilation of your *own* installed node package
modules into CPython C extensions, using [mypyc](https://mypyc.readthedocs.io/).
This only ever touches modules you explicitly list — never third-party
dependencies. It's CPython-only (mypyc emits CPython C-API extensions, so
it's rejected for `interpreter: pypy`) and it's disabled for the whole
"never fail a build" reason described below.

## How to enable

**UI:** the environment create/edit forms have an "Interpreter" select
(CPython / free-threaded CPython / PyPy) and an "Acceleration" section with
JIT and lazy-imports checkboxes. Free-threaded/PyPy interpreters restrict
the backend to venv and filter the Python-version list to what that
interpreter supports.

**API — create an environment with an accelerated interpreter:**

```json
POST /environments
{
  "name": "fast-python",
  "interpreter": "cpython-ft",
  "python_version": "3.14",
  "backend": "venv",
  "runtime_flags": {"jit": true}
}
```

**API — flip runtime flags on an existing environment (no rebuild):**

```json
PATCH /environments/{id}
{
  "runtime_flags": {"jit": true, "lazy_imports": true}
}
```

**API — opt into mypyc compilation of your own node modules:**

```json
POST /environments
{
  "name": "compiled-transforms",
  "backend": "venv",
  "packages": ["my-node-package"],
  "backend_config": {
    "accelerate": {"mypyc_modules": ["my_pkg.transforms", "my_pkg.parsing"]}
  }
}
```

## When each helps and when it hurts

- **JIT**: helps CPU-bound pure-Python loops in Code nodes; negligible or
  even mildly negative for I/O-bound or already-C-accelerated workloads.
- **Free-threaded CPython**: helps workflows that parallelize pure-Python
  work across threads; check the build log's GIL status warning — if an
  installed extension forced the GIL back on, you got the correctness of a
  free-threaded build without the parallelism.
- **PyPy**: excellent for pure-Python-heavy workflows; avoid for
  pandas/numpy-heavy workflows, where CPython is usually faster.
- **mypyc**: only compiles the modules you name, and never fails the
  environment build outright — a failed or incomplete compile logs a
  warning and the environment still reaches `status="ready"` running the
  pure-Python originals. The one case that *does* fail the build is when a
  successful compile breaks the runtime's ability to boot at all; the fix is
  to remove that module from `mypyc_modules` and rebuild (removing a
  compiled artifact safely, in place, isn't attempted).

## Flags apply on the next worker

`runtime_flags` (`jit`, `lazy_imports`) are spawn-time settings, not part of
the environment build. Changing them via `PATCH /environments/{id}` takes
effect the next time the pool spawns a worker for that environment (or the
next disposable per-run container) — running workers keep whatever flags
they were spawned with until they're recycled or idle out.

## Sandbox limitation

Interpreter selection (`cpython-ft`, `pypy`) is not available for sandboxed
(Docker-isolated) runs — sandbox container images are always built from
standard CPython base images (see `container_runtime.ensure_docker_image`),
so a free-threaded or PyPy environment always runs the plain CPython
subprocess pool underneath, never a sandboxed container, if you need actual
interpreter-level isolation for those environments. The two `runtime_flags`
(`jit`, `lazy_imports`) *are* threaded through to sandboxed containers as the
same two environment variables the subprocess pool sets.
