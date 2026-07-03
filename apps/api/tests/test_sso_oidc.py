"""Tests for OIDC SSO flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _patch_redis(monkeypatch):
    """Patch Redis to avoid needing a running Redis server."""
    mock_redis = AsyncMock()
    store: dict[str, str] = {}

    async def _set(key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG001
        store[key] = value

    async def _getdel(key: str) -> str | None:
        return store.pop(key, None)

    mock_redis.set.side_effect = _set
    mock_redis.getdel.side_effect = _getdel
    monkeypatch.setattr(
        "app.redis_client.redis_client",
        mock_redis,
    )
    # Also patch the module-level reference in sso service
    import app.routers.auth as auth_mod
    import app.services.sso as sso_mod
    monkeypatch.setattr(sso_mod, "redis_client", mock_redis)
    monkeypatch.setattr(auth_mod, "redis_client", mock_redis)


_OIDC_DISCOVERY = {
    "issuer": "https://accounts.example.com",
    "authorization_endpoint": "https://accounts.example.com/o/oauth2/v2/auth",
    "token_endpoint": "https://oauth2.example.com/token",
    "jwks_uri": "https://www.exampleapi.com/oauth2/v3/certs",
    "userinfo_endpoint": "https://www.exampleapi.com/oauth2/v3/userinfo",
}


@pytest.fixture
async def _seed(client):
    """Seed an org and SSO config."""
    from uuid import uuid4

    import app.main as main_module
    from app.models import Organization, SSOConfig

    org = Organization(id=uuid4().hex, name="TestOrg", slug="testorg")
    config = SSOConfig(
        org_id=org.id,
        protocol="oidc",
        client_id="test-client-id",
        client_secret=None,
        discovery_url="https://accounts.example.com/.well-known/openid-configuration",
        email_domain="example.com",
    )
    # Use the patched session factory from main module
    async with main_module.SessionLocal() as session:
        session.add(org)
        session.add(config)
        await session.commit()
    return org, config


async def test_sso_start_redirects_to_idp(client: AsyncClient, _seed):
    """GET /auth/sso/start should redirect to the IdP authorization URL."""
    from app.services.sso import _discovery_cache

    _discovery_cache["https://accounts.example.com/.well-known/openid-configuration"] = (
        999999.0,
        _OIDC_DISCOVERY,
    )

    resp = await client.get("/auth/sso/start?org_slug=testorg")
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith(_OIDC_DISCOVERY["authorization_endpoint"])
    assert "client_id=test-client-id" in location
    assert "response_type=code" in location
    assert "state=" in location
    assert "nonce=" in location


async def test_sso_detect_finds_sso(client: AsyncClient, _seed):
    """GET /auth/sso/detect should find SSO for a matching email domain."""
    resp = await client.get("/auth/sso/detect?email=user@example.com")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_sso"] is True
    assert data["org_slug"] == "testorg"


async def test_sso_detect_no_sso(client: AsyncClient):
    """GET /auth/sso/detect should return false for unknown domains."""
    resp = await client.get("/auth/sso/detect?email=user@unknown.com")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_sso"] is False


async def test_sso_detect_invalid_email(client: AsyncClient):
    """GET /auth/sso/detect should handle invalid email gracefully."""
    resp = await client.get("/auth/sso/detect?email=notanemail")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_sso"] is False


async def test_sso_start_without_config_returns_404(client: AsyncClient):
    """GET /auth/sso/start for org without SSO config should 404."""
    resp = await client.get("/auth/sso/start?org_slug=nonexistent")
    assert resp.status_code == 404


async def test_sso_start_missing_slug(client: AsyncClient):
    """GET /auth/sso/start without org_slug should 422."""
    resp = await client.get("/auth/sso/start")
    assert resp.status_code == 422


@patch("app.services.sso._validate_id_token")
@patch("app.services.sso._token_exchange")
async def test_oidc_callback_creates_new_user_jit(
    mock_exchange: AsyncMock,
    mock_validate: AsyncMock,
    client: AsyncClient,
    _seed,
):
    """OIDC callback with valid code and state creates a new user via JIT."""
    # Pre-seed Redis with a valid state
    import json

    from app.redis_client import redis_client

    state = "test-valid-state"
    nonce = "test-nonce"
    await redis_client.set(
        f"nodyra:sso:state:{state}",
        json.dumps({"nonce": nonce, "org_id": _seed[0].id}),
        ex=600,
    )

    mock_exchange.return_value = {"id_token": "test-id-token"}
    mock_validate.return_value = {
        "email": "newuser@example.com",
        "name": "New User",
        "sub": "oidc-sub-12345",
        "nonce": nonce,
    }

    resp = await client.get(
        f"/auth/sso/callback?code=test-code&state={state}"
    )
    # Should succeed and set cookies
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["email"] == "newuser@example.com"
    assert data["user"]["name"] == "New User"

    # Verify user was created in DB
    from sqlalchemy import select

    import app.main as main_module
    from app.models import Membership, User

    async with main_module.SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.email == "newuser@example.com")
        )
        assert user is not None
        assert user.sso_subject == "oidc-sub-12345"
        assert user.email_verified is True
        assert user.password_hash
        membership = await session.scalar(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.org_id == _seed[0].id,
            )
        )
        assert membership is not None
        assert membership.role == "editor"


@patch("app.services.sso._validate_id_token")
@patch("app.services.sso._token_exchange")
async def test_oidc_callback_reuses_existing_user(
    mock_exchange: AsyncMock,
    mock_validate: AsyncMock,
    client: AsyncClient,
    _seed,
):
    """OIDC callback with existing email returns existing user."""
    import app.main as main_module
    from app.models import User
    from app.services.crypto import hash_password

    # Create existing user
    async with main_module.SessionLocal() as session:
        existing = User(
            email="existing@example.com",
            name="Existing User",
            password_hash=hash_password("local-password"),
            sso_subject="oidc-sub-old",
        )
        session.add(existing)
        await session.commit()

    import json

    from app.redis_client import redis_client

    state = "test-valid-state-2"
    nonce = "test-nonce-2"
    await redis_client.set(
        f"nodyra:sso:state:{state}",
        json.dumps({"nonce": nonce, "org_id": _seed[0].id}),
        ex=600,
    )

    mock_exchange.return_value = {"id_token": "test-id-token"}
    mock_validate.return_value = {
        "email": "existing@example.com",
        "name": "Existing User",
        "sub": "oidc-sub-new",
        "nonce": nonce,
    }

    resp = await client.get(
        f"/auth/sso/callback?code=test-code&state={state}"
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == "existing@example.com"


async def test_oidc_callback_invalid_state_returns_400(
    client: AsyncClient,
):
    """OIDC callback with invalid state returns 400."""
    resp = await client.get(
        "/auth/sso/callback?code=test-code&state=invalid-state"
    )
    assert resp.status_code == 400
