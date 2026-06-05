"""Shared base types and utilities for all environment backends."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.config import settings


def venv_dir(env_id: str) -> Path:
    """Stable directory for a given environment id. Shared by all backends."""
    return Path(settings.envs_dir).resolve() / env_id


async def _run(*args: str) -> tuple[int, str]:
    """Run a subprocess, return (returncode, combined stdout+stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "replace")


@runtime_checkable
class EnvironmentBackend(Protocol):
    async def build(self, env: object) -> tuple[str, str]: ...
    def python_path(self, env_id: str) -> Path | None: ...
    async def destroy(self, env_id: str) -> None: ...
