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
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal
from app.models import NodeRun, Run
from app.services.artifacts import delete_artifacts_for_run_ids


async def prune_old_runs(now: datetime | None = None) -> tuple[int, int]:
    """Run both prune rules once. Returns (aged_out, capped_out) counts."""
    now = now or datetime.now(UTC)
    aged_out = 0
    capped_out = 0

    async def _delete_runs(session, ids: list[str]) -> int:
        if not ids:
            return 0
        await delete_artifacts_for_run_ids(session, ids)
        await session.execute(delete(NodeRun).where(NodeRun.run_id.in_(ids)))
        result = await session.execute(delete(Run).where(Run.id.in_(ids)))
        return int(result.rowcount or 0)

    async with SessionLocal() as session:
        days = max(0, settings.run_retention_days)
        if days > 0:
            cutoff = now - timedelta(days=days)
            ids = list(
                (
                    await session.scalars(
                        select(Run.id).where(Run.started_at < cutoff)
                    )
                ).all()
            )
            aged_out = await _delete_runs(session, ids)

        keep = max(0, settings.run_retention_max_per_workflow)
        if keep > 0:
            workflow_ids = (
                await session.scalars(select(Run.workflow_id).distinct())
            ).all()
            for workflow_id in workflow_ids:
                # Find the runs to discard: every row beyond the most recent
                # ``keep`` for this workflow. OFFSET on a sorted SELECT works
                # uniformly across SQLite + Postgres.
                old_ids = list(
                    (
                        await session.scalars(
                            select(Run.id)
                            .where(Run.workflow_id == workflow_id)
                            .order_by(Run.started_at.desc())
                            .offset(keep)
                        )
                    ).all()
                )
                capped_out += await _delete_runs(session, old_ids)

        await session.commit()

    return aged_out, capped_out


async def retention_loop() -> None:
    """Background loop. Bounded by graceful shutdown via task cancellation."""
    interval = max(60, settings.run_retention_tick_seconds)
    while True:
        try:
            await prune_old_runs()
        except Exception:  # noqa: BLE001 - a bad row must not kill the loop
            pass
        await asyncio.sleep(interval)
