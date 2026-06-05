"""CondaBackend: micromamba/mamba/conda-managed environments."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from app.services.backends.base import _run, venv_dir
from app.services.backends.tools import ensure_tool


class CondaBackend:
    async def build(self, env) -> tuple[str, str]:
        solver = await self._solver_cmd()
        channels = (env.backend_config or {}).get("channels", ["conda-forge", "defaults"])
        channel_args = [arg for c in channels for arg in ("-c", c)]
        env_dir = venv_dir(env.id)
        if env_dir.exists():
            shutil.rmtree(env_dir)
        code, log = await _run(
            str(solver), "create", "--yes",
            "--prefix", str(env_dir),
            f"python={env.python_version}",
            *channel_args,
            *list(env.packages),
        )
        return ("ready" if code == 0 else "error"), log[-4000:]

    def python_path(self, env_id: str) -> Path:
        base = venv_dir(env_id)
        if sys.platform == "win32":
            return base / "Scripts" / "python.exe"
        return base / "bin" / "python"

    async def destroy(self, env_id: str) -> None:
        shutil.rmtree(venv_dir(env_id), ignore_errors=True)

    async def _solver_cmd(self) -> Path:
        managed = await ensure_tool("micromamba")
        if managed.exists():
            return managed
        for cmd in ("mamba", "conda"):
            found = shutil.which(cmd)
            if found:
                return Path(found)
        raise RuntimeError(
            "micromamba download failed and no system mamba/conda found. "
            "Check your network connection and try rebuilding."
        )
