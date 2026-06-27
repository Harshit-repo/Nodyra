"""Tests for AWS secret encryption in runner pools."""

import pytest
from httpx import AsyncClient

import app.routers.runner_pools as rp
from app.models import RunnerPool
from app.services.crypto import decrypt_data


@pytest.mark.asyncio
async def test_create_pool_extracts_aws_secret(client: AsyncClient) -> None:
    """aws_secret_access_key should be stripped from provider_config and encrypted."""
    resp = await client.post(
        "/runner-pools",
        json={
            "name": "aws-pool",
            "provider": "docker",
            "provider_config": {
                "region": "us-east-1",
                "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
                "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            },
        },
    )
    assert resp.status_code == 201
    pool = resp.json()
    pool_id = pool["id"]

    # Secret should NOT appear in the API response
    assert "aws_secret_access_key" not in pool.get("provider_config", {})
    assert pool["aws_secret_configured"] is True

    # Secret should be stored encrypted in DB
    async with rp.SessionLocal() as session:
        db_pool = await session.get(RunnerPool, pool_id)
    assert db_pool is not None
    assert "aws_secret_access_key" not in (db_pool.provider_config or {})
    assert db_pool.aws_secret_key_enc is not None
    decrypted = decrypt_data(db_pool.aws_secret_key_enc)
    assert decrypted["key"] == "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


@pytest.mark.asyncio
async def test_update_pool_re_encrypts_aws_secret(client: AsyncClient) -> None:
    """Patching provider_config with a new secret should re-encrypt it."""
    pool_id = (
        await client.post("/runner-pools", json={"name": "p", "provider": "docker"})
    ).json()["id"]

    resp = await client.patch(
        f"/runner-pools/{pool_id}",
        json={
            "provider_config": {
                "region": "eu-west-1",
                "aws_secret_access_key": "NEW_SECRET_KEY",
            }
        },
    )
    assert resp.status_code == 200
    assert resp.json()["aws_secret_configured"] is True
    assert "aws_secret_access_key" not in resp.json().get("provider_config", {})

    async with rp.SessionLocal() as session:
        db_pool = await session.get(RunnerPool, pool_id)
    decrypted = decrypt_data(db_pool.aws_secret_key_enc)
    assert decrypted["key"] == "NEW_SECRET_KEY"


@pytest.mark.asyncio
async def test_pool_without_aws_secret_shows_not_configured(client: AsyncClient) -> None:
    resp = await client.post(
        "/runner-pools",
        json={"name": "no-aws", "provider": "agent", "provider_config": {}},
    )
    assert resp.status_code == 201
    assert resp.json()["aws_secret_configured"] is False
