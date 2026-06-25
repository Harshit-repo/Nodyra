"""Run-batch lifecycle reconciliation.

Batch counters are derived from child runs instead of incremented in memory, so
retries, cancellations, worker crashes, and multiple workers cannot double-count.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Run, RunBatch


async def reconcile_batch(session: AsyncSession, batch_id: str | None) -> RunBatch | None:
    if not batch_id:
        return None
    batch = await session.get(RunBatch, batch_id)
    if batch is None:
        return None

    rows = (
        await session.execute(
            select(Run.status, func.count())
            .where(Run.batch_id == batch_id)
            .group_by(Run.status)
        )
    ).all()
    counts = {str(status): int(count) for status, count in rows}
    batch.succeeded_runs = counts.get("success", 0)
    batch.failed_runs = counts.get("error", 0)
    batch.cancelled_runs = counts.get("cancelled", 0)
    terminal = batch.succeeded_runs + batch.failed_runs + batch.cancelled_runs

    if batch.status != "error" and batch.total_runs > 0 and terminal >= batch.total_runs:
        batch.status = (
            "completed"
            if batch.failed_runs == 0 and batch.cancelled_runs == 0
            else "completed_with_errors"
        )
        batch.finished_at = batch.finished_at or datetime.now(UTC)
    return batch
