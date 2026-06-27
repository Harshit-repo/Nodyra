"""Tests for ghost runner cleanup."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

import app.routers.runner_pools as rp
from app.config import settings
from app.models import Runner, RunnerPool
from app.services.ghost_cleanup import cleanup_ghost_runners


async def _make_pool(client: AsyncClient, name: str = "p") -> str:
    return (await client.post("/runner-pools", json={"name": name})).json()["id"]


@pytest.mark.asyncio
async def test_ghost_cleanup_deletes_old_never_connected_runners(client: AsyncClient) -> None:
    """cleanup_ghost_runners should delete offline runners with last_seen_at IS NULL older than TTL."""
    pool_id = await _make_pool(client)

    ghost_ttl = settings.runner_ghost_ttl_hours
    cutoff = datetime.now(UTC) - timedelta(hours=ghost_ttl + 1)

    async with rp.SessionLocal() as session:
        ghost = Runner(
            pool_id=pool_id,
            name="ghost",
            status="offline",
            max_concurrent_runs=1,
            capabilities={},
            created_at=cutoff,
        )
        real = Runner(
            pool_id=pool_id,
            name="real",
            status="offline",
            max_concurrent_runs=1,
            capabilities={},
            last_seen_at=datetime.now(UTC),
        )
        session.add_all([ghost, real])
        await session.commit()

    async with rp.SessionLocal() as session:
        deleted = await cleanup_ghost_runners(session, pool_id=pool_id)

    assert deleted == 1

    async with rp.SessionLocal() as session:
        runners = (await session.scalars(select(Runner).where(Runner.pool_id == pool_id))).all()
    names = {r.name for r in runners}
    assert "ghost" not in names
    assert "real" in names


@pytest.mark.asyncio
async def test_ghost_cleanup_respects_ttl(client: AsyncClient) -> None:
    """Runners created after the TTL cutoff should not be deleted."""
    pool_id = await _make_pool(client)

    async with rp.SessionLocal() as session:
        new_ghost = Runner(
            pool_id=pool_id,
            name="new-ghost",
            status="offline",
            max_concurrent_runs=1,
            capabilities={},
        )
        session.add(new_ghost)
        await session.commit()

    async with rp.SessionLocal() as session:
        deleted = await cleanup_ghost_runners(session, pool_id=pool_id)

    assert deleted == 0


@pytest.mark.asyncio
async def test_cleanup_ghosts_endpoint(client: AsyncClient) -> None:
    """POST /{pool_id}/cleanup-ghosts should return deleted count."""
    pool_id = await _make_pool(client)

    cutoff = datetime.now(UTC) - timedelta(hours=settings.runner_ghost_ttl_hours + 1)
    async with rp.SessionLocal() as session:
        ghost = Runner(
            pool_id=pool_id,
            name="g",
            status="offline",
            max_concurrent_runs=1,
            capabilities={},
            created_at=cutoff,
        )
        session.add(ghost)
        await session.commit()

    resp = await client.post(f"/runner-pools/{pool_id}/cleanup-ghosts")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1


@pytest.mark.asyncio
async def test_ghost_count_in_pool_info(client: AsyncClient) -> None:
    """RunnerPoolInfo.ghost_count should reflect offline never-connected runners."""
    pool_id = await _make_pool(client)

    async with rp.SessionLocal() as session:
        ghost = Runner(
            pool_id=pool_id,
            name="g",
            status="offline",
            max_concurrent_runs=1,
            capabilities={},
        )
        session.add(ghost)
        await session.commit()

    pool_info = (await client.get(f"/runner-pools/{pool_id}")).json()
    assert pool_info["ghost_count"] == 1
