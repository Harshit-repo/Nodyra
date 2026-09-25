"""Run history retention.

Keeps the DB bounded as runs accumulate by pruning old rows on a background
tick. Two configurable rules — applied independently per tick:

- ``run_retention_days``: drop runs whose ``started_at`` is older than N days.
- ``run_retention_max_per_workflow``: keep only the most recent N runs per
  workflow.
- ``workflow.artifact_retention_days``: optionally delete old artifact files
  for a workflow while retaining the run history rows.

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
from app.models import (
    Artifact,
    AuditEvent,
    MCPCommandApproval,
    MCPGatewayInvocation,
    NodeRun,
    Run,
    RunApproval,
    RunEvent,
    RunQueueEntry,
    Workflow,
)
from app.services.artifacts import delete_artifact_files, delete_artifacts_for_run_ids
from app.services.live_settings import get_live_settings
from app.services.output_store import delete_outputs_for_run_ids

logger = logging.getLogger(__name__)

# Maximum IDs fetched and deleted per prune tick to avoid loading millions of
# UUIDs into a Python list and risking a DB timeout on the bulk DELETE.
_PRUNE_BATCH_SIZE = 10_000
_TERMINAL_STATUSES = ("success", "error", "timed_out", "cancelled")


async def prune_workflow_artifacts(now: datetime | None = None) -> int:
    """Delete artifacts whose workflow-specific retention window has elapsed.

    This intentionally removes only artifact bytes and metadata. Run, NodeRun,
    and RunEvent rows remain available for history/debugging until the global
    run retention rules delete the run itself.
    """
    now = now or datetime.now(UTC)
    pruned = 0
    async with SessionLocal() as session:
        policies = (
            await session.execute(
                select(Workflow.id, Workflow.artifact_retention_days).where(
                    Workflow.artifact_retention_days.is_not(None),
                    Workflow.artifact_retention_days > 0,
                )
            )
        ).all()

        for workflow_id, days in policies:
            cutoff = now - timedelta(days=max(0, int(days or 0)))
            while True:
                rows = list(
                    (
                        await session.scalars(
                            select(Artifact)
                            .join(Run, Artifact.run_id == Run.id)
                            .where(
                                Run.workflow_id == workflow_id,
                                Run.status.in_(_TERMINAL_STATUSES),
                                Artifact.created_at < cutoff,
                            )
                            .limit(_PRUNE_BATCH_SIZE)
                        )
                    ).all()
                )
                if not rows:
                    break
                delete_artifact_files(rows)
                ids = [row.id for row in rows]
                result = await session.execute(
                    delete(Artifact).where(Artifact.id.in_(ids))
                )
                pruned += int(result.rowcount or 0)
                await session.commit()

        await session.commit()
    return pruned


async def prune_old_runs(now: datetime | None = None) -> tuple[int, int]:
    """Run retention once. Returns (aged_out, capped_out) run counts."""
    now = now or datetime.now(UTC)
    aged_out = 0
    capped_out = 0

    async def _delete_runs(session, ids: list[str]) -> int:
        if not ids:
            return 0
        await delete_artifacts_for_run_ids(session, ids)
        # Phase 3.1: reclaim offloaded-output files (data/outputs/<run_id>/) —
        # these live on disk independent of the NodeRun rows deleted below, so
        # without this every offloaded run would leave an orphaned directory.
        delete_outputs_for_run_ids(ids)
        await session.execute(delete(RunApproval).where(RunApproval.run_id.in_(ids)))
        await session.execute(delete(RunEvent).where(RunEvent.run_id.in_(ids)))
        await session.execute(delete(NodeRun).where(NodeRun.run_id.in_(ids)))
        await session.execute(delete(RunQueueEntry).where(RunQueueEntry.run_id.in_(ids)))
        result = await session.execute(delete(Run).where(Run.id.in_(ids)))
        return int(result.rowcount or 0)

    live = await get_live_settings()
    async with SessionLocal() as session:
        # Never prune runs that are still actively executing — only terminal states.
        _terminal = Run.status.in_(_TERMINAL_STATUSES)

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

    await prune_workflow_artifacts(now)
    return aged_out, capped_out


_prune_lock = asyncio.Lock()


async def prune_audit_logs(now: datetime | None = None) -> int:
    """Delete audit log rows older than ``audit_log_retention_days``.

    Returns the number of deleted rows. No-op when the retention setting is 0.
    """
    now = now or datetime.now(UTC)
    days = settings.audit_log_retention_days
    if days <= 0:
        return 0
    cutoff = now - timedelta(days=days)
    async with SessionLocal() as session:
        result = await session.execute(
            delete(AuditEvent).where(AuditEvent.created_at < cutoff)
        )
        await session.execute(
            delete(MCPCommandApproval).where(MCPCommandApproval.created_at < cutoff)
        )
        await session.execute(
            delete(MCPGatewayInvocation).where(MCPGatewayInvocation.created_at < cutoff)
        )
        await session.commit()
    return int(result.rowcount or 0)


_audit_prune_lock = asyncio.Lock()


async def retention_loop() -> None:
    """Background loop. Bounded by graceful shutdown via task cancellation.

    Uses an ``asyncio.Lock`` to prevent overlapping prunes when a tick takes
    longer than ``run_retention_tick_seconds`` (e.g. a very large DB). If a
    prune is still running when the next tick fires, the new tick is skipped
    harmlessly rather than piling on and risking connection exhaustion.

    Also runs the nightly audit log purge on the same interval (which is short
    enough to be practical for audit retention too).
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

        if not _audit_prune_lock.locked():
            async with _audit_prune_lock:
                try:
                    purged = await prune_audit_logs()
                    if purged:
                        logger.info("audit retention: purged %d rows", purged)
                except Exception:
                    logger.exception("audit retention tick failed")
        else:
            logger.debug("audit retention: skipping tick — previous prune still in progress")

        await asyncio.sleep(interval)
