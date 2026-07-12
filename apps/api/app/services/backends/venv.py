"""VenvBackend: uv-managed virtual environments."""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

from app.services.backends.base import _run, local_nodyra_packages, venv_dir


def venv_python(env_id: str) -> Path:
    base = venv_dir(env_id)
    if sys.platform == "win32":
        return base / "Scripts" / "python.exe"
    return base / "bin" / "python"


class VenvBackend:
    async def build(self, env) -> tuple[str, str]:
        index_urls = list((env.backend_config or {}).get("index_urls", []))
        # Some tests construct env stand-ins without the new column; degrade
        # to plain cpython rather than raising.
        interpreter = getattr(env, "interpreter", "cpython") or "cpython"
        return await _do_build(
            env.id,
            env.python_version,
            list(env.packages),
            index_urls,
            interpreter=interpreter,
            backend_config=dict(env.backend_config or {}),
        )

    def python_path(self, env_id: str) -> Path:
        return venv_python(env_id)

    async def destroy(self, env_id: str) -> None:
        target = venv_dir(env_id)
        if target.exists():
            shutil.rmtree(target)


def uv_python_request(interpreter: str, python_version: str) -> str:
    """Translate (interpreter, version) into uv's --python request syntax.

    cpython     → "3.14"        (unchanged, patch pins allowed)
    cpython-ft  → "3.14t"       (uv's free-threaded suffix)
    pypy        → "pypy@3.11"
    Unknown interpreters raise ValueError: builds must fail loudly, not fall
    back to the wrong interpreter.
    """
    if interpreter == "cpython":
        return python_version
    if interpreter == "cpython-ft":
        return f"{python_version}t"
    if interpreter == "pypy":
        return f"pypy@{python_version}"
    raise ValueError(f"unknown interpreter {interpreter!r}")


async def _smoke_check_runtime(env_id: str) -> str | None:
    """Boot the env's runtime and confirm it emits a ``ready`` event.

    Modeled on ``_measure_worker_rss``'s spawn/teardown skeleton. Returns
    ``None`` on success, or a human-readable error (including recent stderr)
    on failure. Fail-closed: an env whose worker cannot boot must never reach
    status="ready" — this matters most for PyPy/free-threaded builds, but
    also protects a regular env broken by an incompatible user package.
    """
    python = venv_python(env_id)
    if not python.exists():
        return f"expected interpreter at {python} but it does not exist"

    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            str(python), "-u", "-m", "nodyra_runtime",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process.stdout is None:
            return "runtime subprocess stdout was not opened"
        try:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=30)
        except TimeoutError:
            stderr_tail = await _drain_stderr(process)
            return f"timed out waiting for ready event (30s). stderr: {stderr_tail}"
        if not line:
            stderr_tail = await _drain_stderr(process)
            return f"runtime did not emit a ready event. stderr: {stderr_tail}"
        try:
            ready = json.loads(line)
        except json.JSONDecodeError:
            return f"first line was not valid JSON: {line[:2000]!r}"
        if ready.get("type") != "ready":
            return f"unexpected first event: {ready}"
        return None
    except (OSError, RuntimeError) as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        if process is not None and process.returncode is None:
            try:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            except ProcessLookupError:
                pass


async def _drain_stderr(process: asyncio.subprocess.Process) -> str:
    """Best-effort read of whatever stderr the process has produced so far
    (import tracebacks land here) — used only for error messages."""
    if process.stderr is None:
        return ""
    try:
        data = await asyncio.wait_for(process.stderr.read(4096), timeout=2)
        return data.decode("utf-8", "replace")[-2000:]
    except (TimeoutError, OSError):
        return ""


async def _do_build(
    env_id: str,
    python_version: str,
    packages: list[str],
    index_urls: list[str] | None = None,
    *,
    interpreter: str = "cpython",
    backend_config: dict | None = None,
) -> tuple[str, str]:
    # Validate index_urls through the SSRF guard before passing them to uv.
    # A malicious or misconfigured index URL could exfiltrate internal metadata
    # (e.g. http://169.254.169.254/pypi redirecting to the EC2 metadata service).
    if index_urls:
        from nodyra_nodes.http_security import assert_public_http_url
        for url in index_urls:
            try:
                assert_public_http_url(url, context="package index URL")
            except ValueError as exc:
                return "error", (
                    f"Package index URL is not allowed: {url!r} — {exc}. "
                    "Private network addresses are blocked to prevent SSRF. "
                    "Set NODYRA_ALLOW_PRIVATE_EGRESS=1 to allow private registries."
                )

    target = venv_dir(env_id)
    try:
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            python_request = uv_python_request(interpreter, python_version)
        except ValueError as exc:
            return "error", str(exc)

        code, log = await _run("uv", "venv", "--python", python_request, str(target))
        if code != 0:
            return "error", log.strip()[-4000:]

        to_install = [*local_nodyra_packages(), *packages]
        if to_install:
            extra_index_args: list[str] = []
            for url in (index_urls or []):
                extra_index_args += ["--extra-index-url", url]
            code, install_log = await _run(
                "uv", "pip", "install",
                "--python", str(venv_python(env_id)),
                *extra_index_args,
                *to_install,
            )
            log = f"{log}\n{install_log}"

        if code != 0:
            return "error", log.strip()[-4000:]

        # Fail-closed smoke check: an env whose runtime cannot boot must never
        # reach status="ready". Costs ~1-3s per build (one interpreter boot);
        # accepted because builds are rare and this catches broken envs
        # (bad PyPy/free-threaded wheels, or a plain package that breaks
        # pydantic) before a run ever tries to use them.
        smoke_failure = await _smoke_check_runtime(env_id)
        if smoke_failure is not None:
            return "error", (
                f"environment built but the Nodyra runtime failed to start: {smoke_failure}"
            )

        if interpreter == "cpython-ft":
            gil_code, gil_out = await _run(
                str(venv_python(env_id)), "-c",
                "import sys; print('gil-disabled' if not sys._is_gil_enabled() else "
                "'gil-enabled')",
            )
            if gil_code == 0 and "gil-disabled" not in gil_out:
                log = (
                    f"{log}\nWARNING: free-threaded build re-enabled the GIL at import "
                    "— an installed extension module without free-threaded support "
                    "forced it back on. The environment still works, just without "
                    "the parallelism free-threading would otherwise give you."
                )

        if interpreter == "pypy":
            log = (
                f"{log}\nINFO: PyPy accelerates pure-Python code via its JIT; "
                "C-extension-heavy workloads (pandas, numpy) may be slower than "
                "on CPython since PyPy's C-API compatibility layer (cpyext) adds "
                "overhead those packages don't pay on CPython."
            )

        mypyc_modules = list(((backend_config or {}).get("accelerate") or {}).get(
            "mypyc_modules", []
        ))
        if mypyc_modules:
            from app.services.backends.accelerate import mypyc_compile

            mypyc_ok, mypyc_log = await mypyc_compile(venv_python(env_id), mypyc_modules)
            log = f"{log}\n{mypyc_log}"
            if not mypyc_ok:
                log = f"{log}\nWARNING: mypyc acceleration incomplete"
            # Re-check the runtime still boots — mypyc replaces a .py with a
            # compiled extension in place, which can break an import. Fixing
            # this without artifact surgery isn't safe, so fail the build and
            # tell the user which module to drop instead.
            post_compile_failure = await _smoke_check_runtime(env_id)
            if post_compile_failure is not None:
                return "error", (
                    f"{log}\nmypyc compilation broke the runtime: {post_compile_failure}. "
                    "Remove the offending module from backend_config.accelerate."
                    "mypyc_modules and rebuild."
                )

        return "ready", log.strip()[-4000:]
    except Exception as exc:  # noqa: BLE001
        return "error", f"{type(exc).__name__}: {exc}"


async def _measure_worker_rss(env_id: str) -> int | None:
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        return None

    python = venv_python(env_id)
    if not python.exists():
        return None

    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            str(python), "-u", "-m", "nodyra_runtime",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process.stdout is None:
            return None
        try:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=30)
        except TimeoutError:
            return None
        if not line:
            return None
        try:
            ready = json.loads(line)
        except json.JSONDecodeError:
            return None
        if ready.get("type") != "ready":
            return None
        try:
            rss = int(psutil.Process(process.pid).memory_info().rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
            return None
        return rss if rss > 0 else None
    except (OSError, RuntimeError):
        return None
    finally:
        if process is not None and process.returncode is None:
            try:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            except ProcessLookupError:
                pass
