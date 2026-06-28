"""Stuck execution detector.

Background loop that finds runs stuck in ``running`` status with no node
completing within a configurable grace window.  This catches engine hangs,
deadlocked subprocess pools, and forgotten remote-runner runs before an
operator notices.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.config import settings
from app.db import SessionLocal
from app.models import NodeRun, Run
from app.services.events import broker

logger = logging.getLogger(__name__)

# Default: a run with no node completing in 30 minutes is considered stuck.
_DEFAULT_STUCK_GRACE_SECONDS: float = 1800.0


def _stuck_grace_seconds() -> float:
    val = getattr(settings, "stuck_run_grace_seconds", None)
    if val is not None and val > 0:
        return float(val)
    return _DEFAULT_STUCK_GRACE_SECONDS


async def detect_stuck_runs() -> int:
    """Find running runs whose most recent node finished before the grace window.

    Returns the number of runs marked as error.
    """
    grace = _stuck_grace_seconds()
    cutoff = datetime.now(UTC) - timedelta(seconds=grace)

    async with SessionLocal() as session:
        # Find running runs that started before the cutoff AND have no node
        # finishing after the cutoff.
        stuck_ids: list[str] = []
        rows = (
            await session.scalars(
                select(Run.id).where(
                    Run.status == "running",
                    # Skip runs dispatched to a remote runner pool — those
                    # are managed by lease expiry, not this detector.
                    Run.runner_pool_id.is_(None),
                )
            )
        ).all()

        for run_id in rows:
            latest_node = await session.scalar(
                select(NodeRun.finished_at)
                .where(NodeRun.run_id == run_id)
                .order_by(NodeRun.finished_at.desc())
                .limit(1)
            )
            if latest_node is None:
                # No nodes have run at all — the run started but never
                # executed anything. Use started_at as the anchor.
                run = await session.get(Run, run_id)
                if run is not None and run.started_at < cutoff:
                    stuck_ids.append(run_id)
            elif latest_node.replace(tzinfo=UTC) < cutoff:
                stuck_ids.append(run_id)

        if not stuck_ids:
            return 0

        now = datetime.now(UTC)
        for run_id in stuck_ids:
            await session.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(status="error", finished_at=now)
            )

        await session.commit()

        for run_id in stuck_ids:
            broker.publish(
                run_id,
                {
                    "type": "run_error",
                    "error": (
                        "Run was stuck: no node completed in the last "
                        f"{int(grace // 60)} minutes"
                    ),
                },
            )
            broker.publish(
                run_id,
                {"type": "run_finished", "run_id": run_id, "status": "error"},
            )
            logger.warning(
                "stuck_run_detector: run_id=%s marked error (no progress for %.0fs)",
                run_id, grace,
            )

    return len(stuck_ids)


async def stuck_run_detector_loop() -> None:
    """Background loop that periodically checks for stuck runs."""
    interval = max(60.0, _stuck_grace_seconds() / 10)
    while True:
        try:
            stuck = await detect_stuck_runs()
            if stuck:
                logger.info(
                    "stuck_run_detector: marked %d run(s) as error", stuck
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never kill the loop
            logger.exception("stuck_run_detector: tick failed")
        await asyncio.sleep(interval)
