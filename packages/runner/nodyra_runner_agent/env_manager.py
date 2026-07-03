"""Per-env venv builder + cache for the remote runner.

Each env is identified by ``(env_id, packages_hash)``. The first run for a
given hash builds a uv venv under ``~/.nodyra-runner/envs/{env_id}-{hash}``
and installs ``nodyra-runtime nodyra-nodes nodyra-core`` plus the env's
packages. Subsequent runs with the same hash reuse the cached venv — so a
package change (new hash) builds a fresh venv and leaves the old one intact.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from nodyra_runner_agent.config import data_dir


def envs_dir() -> Path:
    path = data_dir() / "envs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def env_cache_path(env_id: str, packages_hash: str) -> Path:
    return envs_dir() / f"{env_id}-{packages_hash}"


def _venv_python(venv_root: Path) -> Path:
    if sys.platform == "win32":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def list_cached_env_ids() -> list[str]:
    """Return ``{env_id}-{hash}`` keys for every cached venv on disk."""
    base = envs_dir()
    if not base.exists():
        return []
    return [
        p.name
        for p in base.iterdir()
        if p.is_dir() and _venv_python(p).exists()
    ]


async def _run(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "replace")


async def build_env(
    env_id: str,
    python_version: str,
    packages: list[str],
    packages_hash: str,
    wheel_index_url: str | None = None,
) -> Path:
    """Ensure the venv for this env exists; return its python interpreter path.

    Cache hit (venv python already present) returns immediately. Cache miss
    builds with ``uv venv`` + ``uv pip install``. The nodyra-* packages are not
    on PyPI, so ``wheel_index_url`` (the API's ``--find-links`` page) is added
    when provided; their PyPI dependencies still resolve from the default index.
    Raises ``RuntimeError`` with the combined uv output on a build failure.
    """
    cache_path = env_cache_path(env_id, packages_hash)
    python = _venv_python(cache_path)
    if python.exists():
        return python

    code, out = await _run(
        "uv", "venv", "--python", python_version, str(cache_path)
    )
    if code != 0:
        raise RuntimeError(f"uv venv failed for env {env_id}: {out}")

    find_links = ["--find-links", wheel_index_url] if wheel_index_url else []
    install_args = [
        "uv", "pip", "install",
        "--python", str(python),
        *find_links,
        "nodyra-runtime", "nodyra-nodes", "nodyra-core",
        *packages,
    ]
    code, out = await _run(*install_args)
    if code != 0:
        raise RuntimeError(f"uv pip install failed for env {env_id}: {out}")

    if not python.exists():
        raise RuntimeError(
            f"env {env_id} venv python not found at {python} after build"
        )
    return python
