"""Tests for run history and recent-runs endpoints."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

import app.routers.runner_pools as rp
from app.models import Run, Workflow


async def _make_pool(client: AsyncClient, name: str = "p") -> str:
    return (await client.post("/runner-pools", json={"name": name})).json()["id"]


async def _make_workflow(session) -> str:
    wf = Workflow(name="test-wf")
    session.add(wf)
    await session.commit()
    await session.refresh(wf)
    return wf.id


@pytest.mark.asyncio
async def test_run_history_empty_pool(client: AsyncClient) -> None:
    pool_id = await _make_pool(client)
    resp = await client.get(f"/runner-pools/{pool_id}/run-history?hours=24&buckets=24")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 24
    for bucket in data:
        assert bucket["success"] == 0
        assert bucket["error"] == 0
        assert bucket["total"] == 0
        assert bucket["avg_duration_seconds"] is None


@pytest.mark.asyncio
async def test_run_history_counts_runs_in_correct_buckets(client: AsyncClient) -> None:
    pool_id = await _make_pool(client)

    now = datetime.now(UTC)
    async with rp.SessionLocal() as session:
        wf_id = await _make_workflow(session)
        # Run finished 2 hours ago → should land in bucket ~22 of 24 (near end)
        r1 = Run(
            workflow_id=wf_id,
            runner_pool_id=pool_id,
            status="success",
            started_at=now - timedelta(hours=2, minutes=5),
            finished_at=now - timedelta(hours=2),
        )
        # Run finished 12 hours ago → should land in bucket ~12
        r2 = Run(
            workflow_id=wf_id,
            runner_pool_id=pool_id,
            status="error",
            started_at=now - timedelta(hours=12, minutes=1),
            finished_at=now - timedelta(hours=12),
        )
        # Run older than window → should not appear
        r3 = Run(
            workflow_id=wf_id,
            runner_pool_id=pool_id,
            status="success",
            started_at=now - timedelta(hours=25),
            finished_at=now - timedelta(hours=25),
        )
        session.add_all([r1, r2, r3])
        await session.commit()

    resp = await client.get(f"/runner-pools/{pool_id}/run-history?hours=24&buckets=24")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 24

    total_success = sum(b["success"] for b in data)
    total_error = sum(b["error"] for b in data)
    assert total_success == 1
    assert total_error == 1


@pytest.mark.asyncio
async def test_run_history_avg_duration(client: AsyncClient) -> None:
    pool_id = await _make_pool(client)

    now = datetime.now(UTC)
    async with rp.SessionLocal() as session:
        wf_id = await _make_workflow(session)
        run = Run(
            workflow_id=wf_id,
            runner_pool_id=pool_id,
            status="success",
            started_at=now - timedelta(hours=1, minutes=5),
            finished_at=now - timedelta(hours=1),
        )
        session.add(run)
        await session.commit()

    resp = await client.get(f"/runner-pools/{pool_id}/run-history?hours=2&buckets=2")
    assert resp.status_code == 200
    data = resp.json()
    populated = [b for b in data if b["total"] > 0]
    assert len(populated) == 1
    assert populated[0]["avg_duration_seconds"] == pytest.approx(300.0, abs=5)


@pytest.mark.asyncio
async def test_recent_runs_returns_latest_first(client: AsyncClient) -> None:
    pool_id = await _make_pool(client)

    now = datetime.now(UTC)
    async with rp.SessionLocal() as session:
        wf_id = await _make_workflow(session)
        for i in range(3):
            session.add(
                Run(
                    workflow_id=wf_id,
                    runner_pool_id=pool_id,
                    status="success",
                    started_at=now - timedelta(hours=i),
                )
            )
        await session.commit()

    resp = await client.get(f"/runner-pools/{pool_id}/recent-runs?limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    # Most recent first
    t0 = datetime.fromisoformat(data[0]["started_at"])
    t1 = datetime.fromisoformat(data[1]["started_at"])
    assert t0 >= t1


@pytest.mark.asyncio
async def test_recent_runs_limit_enforced(client: AsyncClient) -> None:
    pool_id = await _make_pool(client)

    now = datetime.now(UTC)
    async with rp.SessionLocal() as session:
        wf_id = await _make_workflow(session)
        for i in range(10):
            session.add(
                Run(
                    workflow_id=wf_id,
                    runner_pool_id=pool_id,
                    status="success",
                    started_at=now - timedelta(minutes=i),
                )
            )
        await session.commit()

    resp = await client.get(f"/runner-pools/{pool_id}/recent-runs?limit=5")
    assert resp.status_code == 200
    assert len(resp.json()) == 5
