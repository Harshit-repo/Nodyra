"""PixiBackend stub — full implementation in Task 6."""
from __future__ import annotations
from pathlib import Path


class PixiBackend:
    async def build(self, env) -> tuple[str, str]:
        raise NotImplementedError

    def python_path(self, env_id: str) -> Path | None:
        raise NotImplementedError

    async def destroy(self, env_id: str) -> None:
        raise NotImplementedError
