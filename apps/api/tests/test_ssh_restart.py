"""Tests for SSH runner restart endpoint."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

import app.routers.runner_pools as rp
from app.models import Runner
from app.services.crypto import encrypt_data
from app.services.ssh_onboard import _restart_script


async def _make_pool(client: AsyncClient, name: str = "p") -> str:
    return (await client.post("/runner-pools", json={"name": name})).json()["id"]


async def _make_runner_with_ssh_creds(client: AsyncClient, pool_id: str) -> str:
    body = (await client.post(f"/runner-pools/{pool_id}/registration-tokens")).json()
    runner_id = body["runner_id"]

    creds = {
        "host": "10.0.0.1",
        "port": 22,
        "username": "ubuntu",
        "auth_method": "password",
        "password": "secret",
        "private_key": None,
        "passphrase": None,
        "use_systemd": True,
    }
    async with rp.SessionLocal() as session:
        runner = await session.get(Runner, runner_id)
        runner.ssh_credentials = encrypt_data(creds)
        runner.ssh_host = "ubuntu@10.0.0.1:22"
        await session.commit()
    return runner_id


@pytest.mark.asyncio
async def test_restart_no_creds_returns_400(client: AsyncClient) -> None:
    pool_id = await _make_pool(client)
    body = (await client.post(f"/runner-pools/{pool_id}/registration-tokens")).json()
    runner_id = body["runner_id"]

    resp = await client.post(f"/runner-pools/{pool_id}/runners/{runner_id}/restart")
    assert resp.status_code == 400
    assert "No SSH credentials" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_restart_calls_onboard_restart(client: AsyncClient) -> None:
    pool_id = await _make_pool(client)
    runner_id = await _make_runner_with_ssh_creds(client, pool_id)

    with patch("app.services.ssh_onboard.onboard_restart", new_callable=AsyncMock) as mock_restart:
        mock_restart.return_value = "[noodle] restarted via systemd"
        resp = await client.post(f"/runner-pools/{pool_id}/runners/{runner_id}/restart")

    assert resp.status_code == 200
    assert resp.json()["runner_id"] == runner_id
    assert "restarted" in resp.json()["log"]
    mock_restart.assert_called_once()


@pytest.mark.asyncio
async def test_restart_rate_limit(client: AsyncClient) -> None:
    """After 3 successful restarts, the 4th should be rate-limited."""
    pool_id = await _make_pool(client)
    runner_id = await _make_runner_with_ssh_creds(client, pool_id)

    # Clear any prior state
    import app.routers.runner_pools as rp_module
    rp_module._restart_attempts.pop(runner_id, None)

    with patch("app.services.ssh_onboard.onboard_restart", new_callable=AsyncMock) as mock_restart:
        mock_restart.return_value = "ok"
        for _ in range(3):
            resp = await client.post(f"/runner-pools/{pool_id}/runners/{runner_id}/restart")
            assert resp.status_code == 200

        resp = await client.post(f"/runner-pools/{pool_id}/runners/{runner_id}/restart")
        assert resp.status_code == 429


def test_restart_script_contains_systemd_path() -> None:
    creds = {"use_systemd": True}
    script = _restart_script(creds)
    assert "systemctl" in script
    assert "nohup" in script  # fallback branch present
