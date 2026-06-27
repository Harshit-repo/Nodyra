"""Tests for long-lived runner tokens (1-year TTL)."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

import app.routers.runner_pools as rp
from app.config import settings
from app.models import Runner
from app.services.crypto import decode_payload_token


@pytest.mark.asyncio
async def test_registration_token_ttl_is_configurable(client: AsyncClient) -> None:
    """Token expiry should reflect settings.runner_token_ttl_days."""
    original = settings.runner_token_ttl_days
    settings.runner_token_ttl_days = 365
    try:
        pool_id = (await client.post("/runner-pools", json={"name": "p"})).json()["id"]
        resp = await client.post(f"/runner-pools/{pool_id}/registration-tokens")
        assert resp.status_code == 200
        body = resp.json()

        payload = decode_payload_token(body["token"])
        assert payload is not None

        # exp in JWT should be ~365 days from now (within a 5-minute window)
        now_ts = datetime.now(UTC).timestamp()
        expected_exp = now_ts + 365 * 86_400
        assert abs(payload["exp"] - expected_exp) < 300

        # expires_at in response should match
        expires_at = datetime.fromisoformat(body["expires_at"])
        assert abs((expires_at - datetime.now(UTC)).total_seconds() - 365 * 86_400) < 300
    finally:
        settings.runner_token_ttl_days = original


@pytest.mark.asyncio
async def test_token_expires_at_persisted_on_runner(client: AsyncClient) -> None:
    """runner.token_expires_at should be saved to the DB after minting."""
    settings_backup = settings.runner_token_ttl_days
    settings.runner_token_ttl_days = 365
    try:
        pool_id = (await client.post("/runner-pools", json={"name": "p"})).json()["id"]
        body = (await client.post(f"/runner-pools/{pool_id}/registration-tokens")).json()
        runner_id = body["runner_id"]

        async with rp.SessionLocal() as session:
            runner = await session.get(Runner, runner_id)
        assert runner is not None
        assert runner.token_expires_at is not None
        token_exp = runner.token_expires_at
        if token_exp.tzinfo is None:
            token_exp = token_exp.replace(tzinfo=UTC)
        delta = (token_exp - datetime.now(UTC)).total_seconds()
        assert abs(delta - 365 * 86_400) < 300
    finally:
        settings.runner_token_ttl_days = settings_backup


@pytest.mark.asyncio
async def test_runner_info_exposes_token_expires_at(client: AsyncClient) -> None:
    """GET /runners should include token_expires_at and ssh_host."""
    pool_id = (await client.post("/runner-pools", json={"name": "p"})).json()["id"]
    body = (await client.post(f"/runner-pools/{pool_id}/registration-tokens")).json()
    runner_id = body["runner_id"]

    runners = (await client.get(f"/runner-pools/{pool_id}/runners")).json()
    assert len(runners) == 1
    runner = runners[0]
    assert runner["id"] == runner_id
    assert "token_expires_at" in runner
    assert runner["token_expires_at"] is not None
    assert runner["ssh_host"] is None  # not SSH-onboarded
