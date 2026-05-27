"""Environment venv builder.

Builds a uv-managed virtualenv per environment under ``settings.envs_dir``.
Runs as a background task so the API stays responsive while ``uv`` works.
"""

import asyncio
import json
import shutil
import sys
from pathlib import Path

from app.config import settings
from app.db import SessionLocal
from app.models import Environment

_build_locks: dict[str, asyncio.Lock] = {}
_build_locks_lock = asyncio.Lock()


def venv_dir(env_id: str) -> Path:
    return Path(settings.envs_dir).resolve() / env_id


def venv_python(env_id: str) -> Path:
    base = venv_dir(env_id)
    if sys.platform == "win32":
        return base / "Scripts" / "python.exe"
    return base / "bin" / "python"


async def _run(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "replace")


async def build_environment(env_id: str) -> None:
    """Create the venv and install the environment's packages.

    Serialized per-env via ``_build_lock`` so that back-to-back rebuild
    requests (multiple ``add package`` clicks, or create+rebuild within a
    moment) don't race on the same target directory. Without this lock the
    second task's ``shutil.rmtree(target)`` deletes files the first task is
    still extracting, surfacing as ``uv`` rename failures of the form
    ``failed to rename file from .../scipy/integrate/.tmpXXX/_vode.so:
    No such file or directory``.
    """
    lock = await _build_lock(env_id)
    async with lock:
        await _build_environment_locked(env_id)


async def _build_environment_locked(env_id: str) -> None:
    """Inner builder. Caller must already hold the per-env build lock."""
    async with SessionLocal() as session:
        env = await session.get(Environment, env_id)
        if env is None:
            return
        env.status = "building"
        env.status_detail = ""
        await session.commit()
        packages = list(env.packages)
        python_version = env.python_version

    if not settings.enable_venv_builds:
        status, detail = "ready", "Venv builds are disabled in this environment."
    else:
        status, detail = await _do_build(env_id, python_version, packages)

    rss_estimate: int | None = None
    if status == "ready" and settings.enable_venv_builds:
        rss_estimate = await _measure_worker_rss(env_id)

    async with SessionLocal() as session:
        env = await session.get(Environment, env_id)
        if env is not None:
            env.status = status
            env.status_detail = detail
            if rss_estimate is not None:
                env.worker_rss_estimate_bytes = rss_estimate
            await session.commit()


async def _build_lock(env_id: str) -> asyncio.Lock:
    async with _build_locks_lock:
        lock = _build_locks.get(env_id)
        if lock is None:
            lock = asyncio.Lock()
            _build_locks[env_id] = lock
        return lock


async def ensure_environment_ready(env_id: str) -> Path:
    """Return the env Python, rebuilding if the recorded venv is missing.

    Docker/container restarts can lose ``./envs`` unless it is mounted as a
    volume, while the database still says the environment is ready. The runner
    must never silently fall back to the API interpreter in that case because
    user packages such as pandas would disappear at execution time.
    """
    lock = await _build_lock(env_id)
    async with lock:
        candidate = venv_python(env_id)
        async with SessionLocal() as session:
            env = await session.get(Environment, env_id)
            if env is None:
                raise RuntimeError(f"environment '{env_id}' not found")
            status = str(env.status or "")
            detail = str(env.status_detail or "")

        if candidate.exists() and status == "ready":
            return candidate

        if not settings.enable_venv_builds:
            raise RuntimeError(
                f"environment '{env_id}' is not available and venv builds are disabled"
            )

        # We already hold the per-env build lock; call the unlocked builder
        # directly so we don't deadlock on the same asyncio.Lock.
        await _build_environment_locked(env_id)

        async with SessionLocal() as session:
            env = await session.get(Environment, env_id)
            status = str(env.status or "") if env is not None else "missing"
            detail = str(env.status_detail or "") if env is not None else ""

        if not candidate.exists() or status != "ready":
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(
                f"environment '{env_id}' is not ready after rebuild{suffix}"
            )
        return candidate


def _workspace_root() -> Path | None:
    """Walk up from this file to find the noodle monorepo root."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "packages").is_dir() and (parent / "pyproject.toml").is_file():
            return parent
    return None


def _local_noodle_packages() -> list[str]:
    """Local paths to noodle workspace packages, installed into each env."""
    root = _workspace_root()
    if root is None:
        return []
    paths: list[str] = []
    for name in ("core", "nodes", "runtime"):
        candidate = root / "packages" / name
        if candidate.is_dir():
            paths.append(str(candidate))
    return paths


async def _do_build(
    env_id: str, python_version: str, packages: list[str]
) -> tuple[str, str]:
    target = venv_dir(env_id)
    try:
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)

        code, log = await _run("uv", "venv", "--python", python_version, str(target))
        if code != 0:
            return "error", log.strip()[-4000:]

        # Always install the noodle workspace packages so the runtime
        # subprocess can import the engine + the built-in node library.
        to_install = [*_local_noodle_packages(), *packages]
        if to_install:
            code, install_log = await _run(
                "uv",
                "pip",
                "install",
                "--python",
                str(venv_python(env_id)),
                *to_install,
            )
            log = f"{log}\n{install_log}"

        status = "ready" if code == 0 else "error"
        return status, log.strip()[-4000:]
    except Exception as exc:  # noqa: BLE001 - surface any build failure to the UI
        return "error", f"{type(exc).__name__}: {exc}"


async def _measure_worker_rss(env_id: str) -> int | None:
    """Sample the RSS of a freshly-warm runtime subprocess for this env.

    Best-effort: any failure (psutil missing, process death, permission
    denied) is swallowed and ``None`` is returned, leaving
    ``Environment.worker_rss_estimate_bytes`` unchanged. The number is
    purely advisory — the UI uses it to show "estimated max RAM at burst"
    and to drive a soft warning when the chosen ``max`` would exceed the
    workspace memory budget.
    """
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
            str(python),
            "-u",
            "-m",
            "noodle_runtime",
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
