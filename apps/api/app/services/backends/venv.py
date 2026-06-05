"""VenvBackend stub — full implementation in Task 3."""
from __future__ import annotations
from pathlib import Path


class VenvBackend:
    async def build(self, env) -> tuple[str, str]:
        raise NotImplementedError

    def python_path(self, env_id: str) -> Path | None:
        raise NotImplementedError

    async def destroy(self, env_id: str) -> None:
        raise NotImplementedError
