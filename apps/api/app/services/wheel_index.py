"""Self-hosted wheel index for the remote runner agent (program A2).

The agent's ``env_manager`` installs ``noodle-core`` / ``noodle-runtime`` /
``noodle-nodes`` into each per-env venv, but those packages are not published to
PyPI — so a runner on a clean machine cannot build any env. This module builds
those three wheels from the monorepo source (the same ``packages/`` tree the API
already installs from for local envs) on first request and caches them, so the
API can serve them as a tiny ``--find-links`` index. The packages' own PyPI
dependencies still resolve from the default index.

Wheels are rebuilt only when missing (or when ``force=True``). The cache lives
under the envs volume so it survives restarts.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.config import settings
from app.services.backends.base import _run, local_noodle_packages

logger = logging.getLogger(__name__)

_lock: asyncio.Lock | None = None
_built = False


def wheels_dir() -> Path:
    path = Path(settings.envs_dir).resolve() / "_runner_wheels"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_wheels() -> list[Path]:
    return sorted(p for p in wheels_dir().glob("*.whl") if p.is_file())


def _get_lock() -> asyncio.Lock:
    # Lazily created so it binds to the running event loop, not import-time.
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


async def ensure_wheels(*, force: bool = False) -> list[Path]:
    """Build the noodle wheels into the cache if absent; return their paths.

    Single-flight via a module lock so concurrent first-run dispatches don't
    race the build. Raises ``RuntimeError`` (with the uv build log tail) if a
    wheel cannot be built.
    """
    global _built
    existing = list_wheels()
    if existing and not force and _built:
        return existing
    async with _get_lock():
        existing = list_wheels()
        if existing and not force and _built:
            return existing
        sources = local_noodle_packages()
        if not sources:
            raise RuntimeError(
                "cannot locate the monorepo packages/ tree to build runner "
                "wheels; the API image must ship the noodle package sources."
            )
        out = wheels_dir()
        if force:
            for stale in out.glob("*.whl"):
                stale.unlink(missing_ok=True)
        for src in sources:
            code, log = await _run(
                "uv", "build", "--wheel", src, "--out-dir", str(out)
            )
            if code != 0:
                raise RuntimeError(
                    f"runner wheel build failed for {src}:\n{log[-2000:]}"
                )
        _built = True
        wheels = list_wheels()
        logger.info("built %d runner wheel(s): %s",
                    len(wheels), [w.name for w in wheels])
        return wheels
