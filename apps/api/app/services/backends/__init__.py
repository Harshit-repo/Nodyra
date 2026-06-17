"""Backend dispatcher: routes build/destroy to the correct backend class."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.config import settings
from app.db import SessionLocal
from app.models import Environment

logger = logging.getLogger(__name__)

_build_locks: dict[str, asyncio.Lock] = {}
_build_locks_lock = asyncio.Lock()


def get_backend(env: Environment):
    from app.services.backends.conda import CondaBackend
    from app.services.backends.pixi import PixiBackend
    from app.services.backends.venv import VenvBackend

    match env.backend:
        case "venv":
            return VenvBackend()
        case "conda":
            return CondaBackend()
        case "pixi":
            return PixiBackend()
        case other:
            raise ValueError(f"Unknown backend: {other!r}")


async def _build_lock(env_id: str) -> asyncio.Lock:
    async with _build_locks_lock:
        lock = _build_locks.get(env_id)
        if lock is None:
            lock = asyncio.Lock()
            _build_locks[env_id] = lock
        return lock


async def build_environment(env_id: str) -> None:
    """Build or rebuild the environment, serialised per-env."""
    lock = await _build_lock(env_id)
    async with lock:
        await _build_environment_locked(env_id)


async def _build_environment_locked(env_id: str) -> None:
    """Inner builder. Caller must already hold the per-env build lock."""
    _env: Environment | None = None
    async with SessionLocal() as session:
        env = await session.get(Environment, env_id)
        if env is None:
            return
        env.status = "building"
        env.status_detail = ""
        await session.commit()
        _env = env

    if _env.backend == "venv" and not settings.enable_venv_builds:
        status, detail = "ready", "Venv builds are disabled in this environment."
    else:
        backend = get_backend(_env)
        try:
            status, detail = await backend.build(_env)
        except Exception as exc:  # noqa: BLE001
            status, detail = "error", f"{type(exc).__name__}: {exc}"

    rss_estimate: int | None = None
    if _env.backend == "venv" and status == "ready" and settings.enable_venv_builds:
        from app.services.backends.venv import _measure_worker_rss
        rss_estimate = await _measure_worker_rss(env_id)

    async with SessionLocal() as session:
        env = await session.get(Environment, env_id)
        if env is not None:
            env.status = status
            env.status_detail = detail
            if rss_estimate is not None:
                env.worker_rss_estimate_bytes = rss_estimate
            await session.commit()


async def ensure_environment_ready(env_id: str) -> Path:
    """Return the Python binary path for env_id, rebuilding if absent."""
    lock = await _build_lock(env_id)
    async with lock:
        _env: Environment | None = None
        async with SessionLocal() as session:
            env = await session.get(Environment, env_id)
            if env is None:
                raise RuntimeError(f"environment '{env_id}' not found")
            backend = get_backend(env)
            status = str(env.status or "")
            detail = str(env.status_detail or "")
            _env = env

        candidate = backend.python_path(env_id)

        if candidate is not None and candidate.exists() and status == "ready":
            return candidate

        if _env.backend == "venv" and not settings.enable_venv_builds:
            raise RuntimeError(
                f"environment '{env_id}' is not available and venv builds are disabled"
            )

        await _build_environment_locked(env_id)

        async with SessionLocal() as session:
            env = await session.get(Environment, env_id)
            if env is not None:
                backend = get_backend(env)
                status = str(env.status or "")
                detail = str(env.status_detail or "")
            else:
                status = "missing"
                detail = ""

        candidate = backend.python_path(env_id)
        if candidate is None or not candidate.exists() or status != "ready":
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(
                f"environment '{env_id}' is not ready after rebuild{suffix}"
            )
        return candidate
