"""Opt-in mypyc compilation of already-installed node package modules.

Scope: this accelerates *user-supplied node package modules already installed
in the env*. It is opt-in (via ``backend_config.accelerate.mypyc_modules``)
and best-effort — a failed compile never fails the surrounding env build; see
``_do_build`` in ``venv.py`` for how the log/warning is threaded through.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services.backends.base import _run

# Same dotted-module-name shape validated in schemas._validate_backend_config.
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


async def mypyc_compile(python: Path, modules: list[str]) -> tuple[bool, str]:
    """Best-effort mypyc compilation of installed modules, in place.

    Returns (all_ok, log). Never raises. Steps per invocation:
    1. ``uv pip install --python <python> "mypy>=1.10" setuptools`` (build deps).
    2. Resolve each module to its source file inside the env via
       ``importlib.util.find_spec``. Skip (with a log line) modules that
       don't resolve or aren't .py files.
    3. Run ``<python> -m mypyc <origin.py>`` with cwd set to the module's
       site-packages root so the built extension lands next to the source
       (CPython prefers the .so/.pyd over the .py at import time).
    4. Verify: re-run find_spec and log whether origin now points at the
       compiled extension.
    """
    log_lines: list[str] = []
    all_ok = True

    code, install_log = await _run(
        "uv", "pip", "install", "--python", str(python), "mypy>=1.10", "setuptools"
    )
    log_lines.append("mypyc: installing build dependencies (mypy, setuptools)")
    log_lines.append(install_log.strip()[-2000:])
    if code != 0:
        log_lines.append("mypyc: failed to install build dependencies; aborting compilation")
        return False, "\n".join(log_lines)

    for module in modules:
        if not _MODULE_RE.match(module):
            log_lines.append(f"mypyc: skipping {module!r} — not a valid dotted module name")
            all_ok = False
            continue

        code, resolve_out = await _run(
            str(python), "-c",
            "import importlib.util, sys; "
            f"spec = importlib.util.find_spec({module!r}); "
            "print(spec.origin if spec else '')",
        )
        origin = resolve_out.strip().splitlines()[-1] if resolve_out.strip() else ""
        if code != 0 or not origin or not origin.endswith(".py"):
            log_lines.append(
                f"mypyc: skipping {module!r} — could not resolve to a .py source file "
                f"(origin={origin!r})"
            )
            all_ok = False
            continue

        origin_path = Path(origin)
        # mypyc places compiled extensions relative to its cwd, mirroring the
        # relative source path it was given — so invoke it from the
        # site-packages root with a relative path, and the .so/.pyd lands next
        # to the source (where CPython prefers it over the .py at import).
        # site-packages root = origin minus one path level per dotted part
        # ("pkg.sub.mod" → site/pkg/sub/mod.py), plus one more level when the
        # module resolves to a package's __init__.py.
        parts = module.split(".")
        levels = len(parts) if origin_path.name == "__init__.py" else len(parts) - 1
        if levels >= len(origin_path.parents):
            log_lines.append(
                f"mypyc: skipping {module!r} — cannot locate site-packages root "
                f"for {origin_path}"
            )
            all_ok = False
            continue
        site_root = origin_path.parents[levels]
        rel_source = origin_path.relative_to(site_root)

        code, compile_log = await _run(
            str(python), "-m", "mypyc", str(rel_source), cwd=str(site_root)
        )
        log_lines.append(f"mypyc: compiling {module!r} ({origin_path})")
        log_lines.append(compile_log.strip()[-2000:])
        if code != 0:
            log_lines.append(f"mypyc: compilation of {module!r} failed (exit={code})")
            all_ok = False
            continue

        # Verify: re-resolve and check whether the compiled extension now wins.
        _, verify_out = await _run(
            str(python), "-c",
            "import importlib.util, sys; "
            f"spec = importlib.util.find_spec({module!r}); "
            "print(spec.origin if spec else '')",
        )
        new_origin = verify_out.strip().splitlines()[-1] if verify_out.strip() else ""
        if new_origin.endswith((".so", ".pyd")):
            log_lines.append(f"mypyc: {module!r} now loads from compiled extension {new_origin}")
        else:
            log_lines.append(
                f"mypyc: {module!r} compiled but import still resolves to {new_origin!r} "
                "(compiled extension not picked up)"
            )
            all_ok = False

    return all_ok, "\n".join(log_lines)
