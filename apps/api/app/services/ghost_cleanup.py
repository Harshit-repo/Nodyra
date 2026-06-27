"""Periodic cleanup of ghost runner rows.

A ghost runner is a Runner row whose agent never connected — the operator
minted a registration token but the machine never came online.  These rows
have ``last_seen_at IS NULL`` and ``status == 'offline'``.  Left
indefinitely they inflate pool counts and confuse the health dashboard.

The cleanup loop runs hourly and deletes rows older than
``settings.runner_ghost_ttl_hours`` (default 48 h).
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import settings
from app.models import Runner

log = logging.getLogger(__name__)


async def cleanup_ghost_runners(session, pool_id: str | None = None) -> int:
    """Delete ghost runner rows and return the number deleted.

    Safe to call directly from endpoint handlers or from the background loop.
    Does NOT call ``run_as_system`` — callers are responsible for org context
    (the background loop wraps this via ``_as_system`` in main.py; the
    endpoint handler runs inside the request's org context which is fine for
    operator-scoped cleanup).
    """
    cutoff = datetime.now(UTC) - timedelta(hours=settings.runner_ghost_ttl_hours)
    q = select(Runner).where(
        Runner.last_seen_at.is_(None),
        Runner.status == "offline",
        Runner.created_at < cutoff,
    )
    if pool_id is not None:
        q = q.where(Runner.pool_id == pool_id)

    ghosts = (await session.scalars(q)).all()
    for ghost in ghosts:
        await session.delete(ghost)
    if ghosts:
        await session.commit()
    log.info("ghost_cleanup: deleted %d ghost runner(s)", len(ghosts))
    return len(ghosts)


async def ghost_cleanup_loop() -> None:
    """Background loop: clean up ghost runners every hour."""
    while True:
        await asyncio.sleep(3600)
        try:
            from app.db import SessionLocal

            async with SessionLocal() as session:
                await cleanup_ghost_runners(session)
        except Exception:
            log.exception("ghost_cleanup_loop: unhandled error")
