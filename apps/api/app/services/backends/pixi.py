"""PixiBackend: pixi-managed environments with conda + PyPI package mix."""
from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path

from app.services.backends.base import _run, local_noodle_packages, venv_dir
from app.services.backends.tools import ensure_tool

_DEFAULT_CHANNELS = ["conda-forge", "defaults"]


def _split_packages(packages: list[str]) -> tuple[list[str], list[str]]:
    """Return (conda_pkgs, pypi_pkgs) splitting on '@ pypi' marker."""
    conda: list[str] = []
    pypi: list[str] = []
    for pkg in packages:
        if pkg.replace(" ", "").endswith("@pypi"):
            pypi.append(pkg.split("@")[0].strip())
        else:
            conda.append(pkg)
    return conda, pypi


def _current_platform() -> str:
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "win-64"
    if sys.platform == "darwin":
        return "osx-arm64" if machine in ("arm64", "aarch64") else "osx-64"
    return "linux-aarch64" if machine in ("arm64", "aarch64") else "linux-64"


def _write_pixi_toml(toml_path: Path, env) -> None:
    channels = (env.backend_config or {}).get("channels", _DEFAULT_CHANNELS)
    conda_pkgs, pypi_pkgs = _split_packages(list(env.packages))
    channel_str = ", ".join(f'"{c}"' for c in channels)

    lines = [
        "[project]",
        f'name = "noodle-env-{env.id}"',
        f"channels = [{channel_str}]",
        f'platforms = ["{_current_platform()}"]',
        "",
        "[dependencies]",
        f'python = "{env.python_version}.*"',
    ]
    for pkg in conda_pkgs:
        lines.append(f'{pkg} = "*"')

    if pypi_pkgs:
        lines.append("")
        lines.append("[pypi-dependencies]")
        for pkg in pypi_pkgs:
            lines.append(f'{pkg} = "*"')

    lines.append("")
    toml_path.write_text("\n".join(lines))


class PixiBackend:
    async def build(self, env) -> tuple[str, str]:
        pixi = await ensure_tool("pixi")
        env_dir = venv_dir(env.id)
        env_dir.mkdir(parents=True, exist_ok=True)
        toml_path = env_dir / "pixi.toml"
        _write_pixi_toml(toml_path, env)
        code, log = await _run(
            str(pixi), "install",
            "--manifest-path", str(toml_path),
        )
        if code != 0:
            return "error", log[-4000:]

        local_pkgs = local_noodle_packages()
        if local_pkgs:
            pip_code, pip_log = await _run(
                str(self.python_path(env.id)), "-m", "pip", "install", *local_pkgs,
            )
            log = f"{log}\n{pip_log}"
            if pip_code != 0:
                return "error", log[-4000:]

        return "ready", log[-4000:]

    def python_path(self, env_id: str) -> Path:
        base = venv_dir(env_id)
        if sys.platform == "win32":
            return base / ".pixi" / "envs" / "default" / "python.exe"
        return base / ".pixi" / "envs" / "default" / "bin" / "python"

    async def destroy(self, env_id: str) -> None:
        shutil.rmtree(venv_dir(env_id), ignore_errors=True)
