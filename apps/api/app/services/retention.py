"""Run history retention.

Keeps the DB bounded as runs accumulate by pruning old rows on a background
tick. Two configurable rules — applied independently per tick:

- ``run_retention_days``: drop runs whose ``started_at`` is older than N days.
- ``run_retention_max_per_workflow``: keep only the most recent N runs per
  workflow.

We delete the child ``node_runs`` rows explicitly because SQLite doesn't
enforce ``ON DELETE CASCADE`` unless ``PRAGMA foreign_keys=ON`` is set, and
SQLAlchemy's ORM cascades only fire on ORM-level deletes (not bulk
``delete().where(...)``). Doing it ourselves is portable across DBs and
clear at the call site.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select

from app.config import settings
from app.db import SessionLocal
from app.models import NodeRun, Run, RunApproval, RunEvent, RunQueueEntry
from app.services.artifacts import delete_artifacts_for_run_ids
from app.services.live_settings import get_live_settings

logger = logging.getLogger(__name__)

# Maximum IDs fetched and deleted per prune tick to avoid loading millions of
# UUIDs into a Python list and risking a DB timeout on the bulk DELETE.
_PRUNE_BATCH_SIZE = 10_000


async def prune_old_runs(now: datetime | None = None) -> tuple[int, int]:
    """Run both prune rules once. Returns (aged_out, capped_out) counts."""
    now = now or datetime.now(UTC)
    aged_out = 0
    capped_out = 0

    async def _delete_runs(session, ids: list[str]) -> int:
        if not ids:
            return 0
        await delete_artifacts_for_run_ids(session, ids)
        await session.execute(delete(RunApproval).where(RunApproval.run_id.in_(ids)))
        await session.execute(delete(RunEvent).where(RunEvent.run_id.in_(ids)))
        await session.execute(delete(NodeRun).where(NodeRun.run_id.in_(ids)))
        await session.execute(delete(RunQueueEntry).where(RunQueueEntry.run_id.in_(ids)))
        result = await session.execute(delete(Run).where(Run.id.in_(ids)))
        return int(result.rowcount or 0)

    live = await get_live_settings()
    async with SessionLocal() as session:
        # Never prune runs that are still actively executing — only terminal states.
        _terminal = Run.status.in_(("success", "error", "cancelled"))

        days = max(0, live.run_retention_days)
        if days > 0:
            cutoff = now - timedelta(days=days)
            # Fetch and delete in batches to avoid loading millions of UUIDs
            # into memory at once and risking a DB timeout on a huge IN clause.
            while True:
                ids = list(
                    (
                        await session.scalars(
                            select(Run.id)
                            .where(Run.started_at < cutoff, _terminal)
                            .limit(_PRUNE_BATCH_SIZE)
                        )
                    ).all()
                )
                if not ids:
                    break
                aged_out += await _delete_runs(session, ids)
                await session.commit()

        keep = max(0, live.run_retention_max_per_workflow)
        if keep > 0:
            # One query using a window function: rank each terminal run within
            # its workflow newest-first; delete rows ranked beyond `keep`.
            # Avoids one SELECT per workflow (N+1) when many workflows exist.
            rn = func.row_number().over(
                partition_by=Run.workflow_id,
                order_by=Run.started_at.desc(),
            ).label("rn")
            subq = (
                select(Run.id.label("id"), rn)
                .where(_terminal)
                .subquery()
            )
            # Process in batches to cap peak memory usage.
            while True:
                old_ids = list(
                    (
                        await session.scalars(
                            select(subq.c.id)
                            .where(subq.c.rn > keep)
                            .limit(_PRUNE_BATCH_SIZE)
                        )
                    ).all()
                )
                if not old_ids:
                    break
                capped_out += await _delete_runs(session, old_ids)
                await session.commit()

        await session.commit()

    return aged_out, capped_out


_prune_lock = asyncio.Lock()


async def retention_loop() -> None:
    """Background loop. Bounded by graceful shutdown via task cancellation.

    Uses an ``asyncio.Lock`` to prevent overlapping prunes when a tick takes
    longer than ``run_retention_tick_seconds`` (e.g. a very large DB). If a
    prune is still running when the next tick fires, the new tick is skipped
    harmlessly rather than piling on and risking connection exhaustion.
    """
    interval = max(60, settings.run_retention_tick_seconds)
    while True:
        if not _prune_lock.locked():
            async with _prune_lock:
                try:
                    await prune_old_runs()
                except Exception:
                    logger.exception("retention tick failed")
        else:
            logger.debug("retention: skipping tick — previous prune still in progress")
        await asyncio.sleep(interval)
