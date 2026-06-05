"""PixiBackend: pixi-managed environments with conda + PyPI package mix."""
from __future__ import annotations

import platform
import re
import shutil
import sys
import tomllib
from pathlib import Path

from app.services.backends.base import _run, local_noodle_packages, venv_dir
from app.services.backends.tools import ensure_tool

_DEFAULT_CHANNELS = ["conda-forge", "defaults"]

# Matches the bare package name from a PEP 508 / conda spec, stopping before
# any version operator or extras bracket.
_NAME_RE = re.compile(r"^([A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?)")


def _pkg_name(spec: str) -> str:
    """Extract the normalised package name from a spec like 'numpy>=2.0'."""
    m = _NAME_RE.match(spec.strip())
    return m.group(1).lower().replace("-", "_") if m else spec.strip().lower()


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


def _incremental_additions(
    toml_path: Path, env,
) -> tuple[list[str], list[str]] | None:
    """
    Compare desired env.packages against the installed pixi.toml.

    Returns (new_conda, new_pypi) if only additions are needed,
    or None if a full rebuild is required (removals, channel change, parse error).
    """
    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return None

    installed_conda = {
        k.lower().replace("-", "_")
        for k in data.get("dependencies", {}).keys()
        if k != "python"
    }
    installed_pypi = {
        k.lower().replace("-", "_")
        for k in data.get("pypi-dependencies", {}).keys()
    }
    installed_channels = list(data.get("project", {}).get("channels", []))
    desired_channels = (env.backend_config or {}).get("channels", _DEFAULT_CHANNELS)
    if installed_channels != desired_channels:
        return None  # channel change → full rebuild

    desired_conda, desired_pypi = _split_packages(list(env.packages))
    desired_conda_names = {_pkg_name(p) for p in desired_conda}
    desired_pypi_names = {_pkg_name(p) for p in desired_pypi}

    if (installed_conda - desired_conda_names) or (installed_pypi - desired_pypi_names):
        return None  # removals → full rebuild

    new_conda = [p for p in desired_conda if _pkg_name(p) not in installed_conda]
    new_pypi = [p for p in desired_pypi if _pkg_name(p) not in installed_pypi]
    return new_conda, new_pypi


class PixiBackend:
    async def build(self, env) -> tuple[str, str]:
        pixi = await ensure_tool("pixi")
        env_dir = venv_dir(env.id)
        env_dir.mkdir(parents=True, exist_ok=True)
        toml_path = env_dir / "pixi.toml"

        # Fast path: incremental add when env is already built
        if toml_path.exists() and self.python_path(env.id).exists():
            additions = _incremental_additions(toml_path, env)
            if additions is not None:
                new_conda, new_pypi = additions
                if not new_conda and not new_pypi:
                    return "ready", "Nothing to add — already up to date."
                code, log = await self._pixi_add(pixi, toml_path, new_conda, new_pypi)
                if code == 0:
                    return "ready", log[-4000:]
                # pixi add failed — fall through to full rebuild

        # Full rebuild path
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

    async def _pixi_add(
        self,
        pixi: Path,
        toml_path: Path,
        conda_pkgs: list[str],
        pypi_pkgs: list[str],
    ) -> tuple[int, str]:
        """Run pixi add for conda and/or pypi packages incrementally."""
        combined_log = ""
        if conda_pkgs:
            code, log = await _run(
                str(pixi), "add",
                "--manifest-path", str(toml_path),
                *conda_pkgs,
            )
            combined_log += log
            if code != 0:
                return code, combined_log
        if pypi_pkgs:
            code, log = await _run(
                str(pixi), "add",
                "--manifest-path", str(toml_path),
                "--pypi",
                *pypi_pkgs,
            )
            combined_log += f"\n{log}"
            return code, combined_log
        return 0, combined_log

    def python_path(self, env_id: str) -> Path:
        base = venv_dir(env_id)
        if sys.platform == "win32":
            return base / ".pixi" / "envs" / "default" / "python.exe"
        return base / ".pixi" / "envs" / "default" / "bin" / "python"

    async def destroy(self, env_id: str) -> None:
        shutil.rmtree(venv_dir(env_id), ignore_errors=True)
