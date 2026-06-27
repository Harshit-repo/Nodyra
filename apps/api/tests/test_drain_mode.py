"""Tests for runner drain mode."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

import app.routers.runner_pools as rp
from app.models import Runner


async def _make_pool(client: AsyncClient, name: str = "p") -> str:
    return (await client.post("/runner-pools", json={"name": name})).json()["id"]


async def _make_runner(client: AsyncClient, pool_id: str) -> str:
    body = (await client.post(f"/runner-pools/{pool_id}/registration-tokens")).json()
    return body["runner_id"]


@pytest.mark.asyncio
async def test_drain_sets_status_to_draining(client: AsyncClient) -> None:
    pool_id = await _make_pool(client)
    runner_id = await _make_runner(client, pool_id)

    # Simulate runner as online so drain makes sense
    async with rp.SessionLocal() as session:
        runner = await session.get(Runner, runner_id)
        runner.status = "online"
        runner.last_seen_at = datetime.now(UTC)
        await session.commit()

    resp = await client.post(
        f"/runner-pools/{pool_id}/runners/{runner_id}/drain",
        json={"draining": True},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "draining"


@pytest.mark.asyncio
async def test_undrain_restores_online_status(client: AsyncClient) -> None:
    pool_id = await _make_pool(client)
    runner_id = await _make_runner(client, pool_id)

    async with rp.SessionLocal() as session:
        runner = await session.get(Runner, runner_id)
        runner.status = "draining"
        runner.last_seen_at = datetime.now(UTC)
        await session.commit()

    resp = await client.post(
        f"/runner-pools/{pool_id}/runners/{runner_id}/drain",
        json={"draining": False},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "online"


@pytest.mark.asyncio
async def test_undrain_never_connected_stays_offline(client: AsyncClient) -> None:
    """Un-draining a runner that was never seen should go to offline, not online."""
    pool_id = await _make_pool(client)
    runner_id = await _make_runner(client, pool_id)

    async with rp.SessionLocal() as session:
        runner = await session.get(Runner, runner_id)
        runner.status = "draining"
        runner.last_seen_at = None
        await session.commit()

    resp = await client.post(
        f"/runner-pools/{pool_id}/runners/{runner_id}/drain",
        json={"draining": False},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "offline"


@pytest.mark.asyncio
async def test_drain_wrong_pool_returns_404(client: AsyncClient) -> None:
    pool_a = await _make_pool(client, "a")
    pool_b = await _make_pool(client, "b")
    runner_id = await _make_runner(client, pool_a)

    resp = await client.post(
        f"/runner-pools/{pool_b}/runners/{runner_id}/drain",
        json={"draining": True},
    )
    assert resp.status_code == 404
