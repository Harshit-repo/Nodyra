"""The stuck-run detector: the safety net for a run that never finishes.

It had no test coverage, and it marked a run ``error`` without recording why
— publishing the reason only to the live event broker. A run killed here has
no node-level error to fall back on, so once the stream was gone the run
showed a failure with no explanation anywhere. ADR-0003 puts exactly this
case (a terminal failure no node owns) on ``runs.error``.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app import models
from app.services import stuck_run_detector
from app.services.stuck_run_detector import detect_stuck_runs


@pytest.fixture
def short_grace(monkeypatch):
    """Shrink the 30-minute window so tests do not have to time-travel far."""
    from app.config import settings

    monkeypatch.setattr(settings, "stuck_run_grace_seconds", 60, raising=False)


async def _make_run(*, started_minutes_ago: float, status: str = "running") -> str:
    async with stuck_run_detector.SessionLocal() as session:
        workflow = models.Workflow(name="Stuck probe")
        session.add(workflow)
        await session.flush()
        run = models.Run(
            workflow_id=workflow.id,
            workflow_version=1,
            mode="production",
            trigger_type="manual",
            status=status,
            started_at=datetime.now(UTC) - timedelta(minutes=started_minutes_ago),
        )
        session.add(run)
        await session.commit()
        return run.id


async def _load(run_id: str) -> models.Run:
    async with stuck_run_detector.SessionLocal() as session:
        return await session.scalar(select(models.Run).where(models.Run.id == run_id))


async def test_a_run_with_no_progress_is_marked_error(client: AsyncClient, short_grace) -> None:
    run_id = await _make_run(started_minutes_ago=10)

    marked = await detect_stuck_runs()

    assert marked >= 1
    assert (await _load(run_id)).status == "error"


async def test_the_reason_is_recorded_on_the_run(client: AsyncClient, short_grace) -> None:
    """Not only on the event broker — that is gone by the time anyone looks."""
    run_id = await _make_run(started_minutes_ago=10)

    await detect_stuck_runs()

    run = await _load(run_id)
    assert run.error, "a stuck run must say why it failed"
    assert "stuck" in run.error.lower()
    assert "no node completed" in run.error


async def test_a_recent_run_is_left_alone(client: AsyncClient, short_grace) -> None:
    """Inside the grace window a slow run is just slow."""
    run_id = await _make_run(started_minutes_ago=0)

    await detect_stuck_runs()

    assert (await _load(run_id)).status == "running"


async def test_a_finished_run_is_not_touched(client: AsyncClient, short_grace) -> None:
    run_id = await _make_run(started_minutes_ago=10, status="success")

    await detect_stuck_runs()

    run = await _load(run_id)
    assert run.status == "success"
    assert run.error is None


async def test_runs_on_a_remote_pool_are_left_to_lease_expiry(client: AsyncClient, short_grace) -> None:
    """Remote-pool runs are managed by their lease, not by this detector."""
    async with stuck_run_detector.SessionLocal() as session:
        workflow = models.Workflow(name="Remote probe")
        pool = models.RunnerPool(name="pool-1", provider="agent")
        session.add_all([workflow, pool])
        await session.flush()
        run = models.Run(
            workflow_id=workflow.id,
            workflow_version=1,
            mode="production",
            trigger_type="manual",
            status="running",
            runner_pool_id=pool.id,
            started_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    await detect_stuck_runs()

    assert (await _load(run_id)).status == "running"
