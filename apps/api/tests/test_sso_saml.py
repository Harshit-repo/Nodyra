"""Tests for SAML SSO flow."""

from __future__ import annotations

from unittest.mock import ANY, AsyncMock, patch

import pytest
from httpx import AsyncClient


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _patch_redis(monkeypatch):
    mock_redis = AsyncMock()
    monkeypatch.setattr(
        "app.redis_client.redis_client",
        mock_redis,
    )
    # Also patch the module-level reference in sso service
    import app.services.sso as sso_mod
    monkeypatch.setattr(sso_mod, "redis_client", mock_redis)


@pytest.fixture
async def _seed_saml(client):
    """Seed an org and SAML SSO config."""
    from app.models import Organization, SSOConfig
    import app.main as main_module

    org = Organization(name="SamlOrg", slug="samlorg")
    config = SSOConfig(
        org_id=org.id,
        protocol="saml",
        idp_entity_id="urn:example:idp",
        idp_sso_url="https://idp.example.com/saml/sso",
        idp_certificate="MIID... (test cert)",
        email_domain="samltest.com",
    )
    async with main_module.SessionLocal() as session:
        session.add(org)
        session.add(config)
        await session.commit()
    return org, config


async def test_saml_metadata_returns_xml(client: AsyncClient, _seed_saml):
    """GET /auth/sso/metadata should return SP metadata XML."""
    resp = await client.get("/auth/sso/metadata?org_slug=samlorg")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/xml"
    text = resp.text
    assert "EntityDescriptor" in text
    assert "SPSSODescriptor" in text
    assert "AssertionConsumerService" in text


async def test_saml_metadata_not_found(client: AsyncClient):
    """SAML metadata for non-existent org returns 404."""
    resp = await client.get("/auth/sso/metadata?org_slug=nonexistent")
    assert resp.status_code == 404


async def test_saml_acs_missing_response_returns_400(client: AsyncClient):
    """POST /auth/sso/acs without SAMLResponse returns 400."""
    resp = await client.post("/auth/sso/acs")
    assert resp.status_code == 400


async def test_saml_acs_empty_response_returns_400(client: AsyncClient):
    """POST /auth/sso/acs with empty SAMLResponse returns 400."""
    resp = await client.post("/auth/sso/acs", data={"SAMLResponse": ""})
    assert resp.status_code == 400


async def test_saml_start_redirects(client: AsyncClient, _seed_saml):
    """GET /auth/sso/start for SAML config should redirect to IdP."""
    resp = await client.get("/auth/sso/start?org_slug=samlorg")
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "SAMLRequest=" in location
    assert "idp.example.com" in location


async def test_saml_acs_with_valid_response_creates_user(
    client: AsyncClient, _seed_saml
):
    """POST /auth/sso/acs with a valid SAMLResponse creates a user."""
    # Build a minimal valid SAML response
    import base64
    import zlib

    saml_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<saml2p:Response xmlns:saml2p="urn:oasis:names:tc:SAML:2.0:protocol"
    xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"
    ID="_test-response-id"
    Version="2.0"
    IssueInstant="2026-06-30T00:00:00Z"
    Destination="http://localhost/auth/sso/acs">
  <saml2:Assertion ID="_test-assertion-id"
      IssueInstant="2026-06-30T00:00:00Z"
      Version="2.0">
    <saml2:Subject>
      <saml2:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">
        samluser@samltest.com
      </saml2:NameID>
    </saml2:Subject>
    <saml2:AttributeStatement>
      <saml2:Attribute Name="email">
        <saml2:AttributeValue>samluser@samltest.com</saml2:AttributeValue>
      </saml2:Attribute>
    </saml2:AttributeStatement>
  </saml2:Assertion>
</saml2p:Response>"""
    compressed = zlib.compress(saml_xml.encode())[2:-4]
    b64_encoded = base64.b64encode(compressed).decode()

    resp = await client.post(
        "/auth/sso/acs",
        data={"SAMLResponse": b64_encoded},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["email"] == "samluser@samltest.com"

    # Verify user was created
    import app.main as main_module
    from app.models import User

    async with main_module.SessionLocal() as session:
        user = await session.scalar(
            User.__table__.select().where(User.email == "samluser@samltest.com")
        )
        assert user is not None
        assert user.email_verified is True
