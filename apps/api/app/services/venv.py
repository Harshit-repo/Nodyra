"""Environment venv builder.

Builds a uv-managed virtualenv per environment under ``settings.envs_dir``.
Runs as a background task so the API stays responsive while ``uv`` works.
"""

import asyncio
import shutil
import sys
from pathlib import Path

from app.config import settings
from app.db import SessionLocal
from app.models import Environment


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
    """Create the venv and install the environment's packages."""
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

    async with SessionLocal() as session:
        env = await session.get(Environment, env_id)
        if env is not None:
            env.status = status
            env.status_detail = detail
            await session.commit()


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
