"""Tests for SAML SSO flow."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _patch_redis(monkeypatch):
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


@pytest.fixture
async def _seed_saml(client):
    """Seed an org and SAML SSO config."""
    from uuid import uuid4

    import app.main as main_module
    from app.models import Organization, SSOConfig

    org = Organization(id=uuid4().hex, name="SamlOrg", slug="samlorg")
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
    """POST /auth/sso/acs with a valid SAMLResponse creates a user.

    Generates a self-signed RSA keypair, embeds the IdP cert into the SSO
    config, signs the SAML assertion with ``signxml``, and verifies the full
    ACS flow creates a user with the correct membership.
    """
    import base64
    import zlib
    from datetime import UTC, datetime, timedelta

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509 import (
        BasicConstraints,
        CertificateBuilder,
        Name,
    )
    from lxml import etree
    from signxml import XMLSigner

    # --- Generate a self-signed IdP test cert ---
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = Name.from_rfc4514_string("CN=SAML Test IdP")
    cert = (
        CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(12345)
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(hours=1))
        .add_extension(BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())  # noqa: PLC2701
    )
    idp_cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()

    # --- Patch the SSO config with the real cert and test the real ACS flow ---
    import app.main as main_module
    from app.models import SSOConfig

    async with main_module.SessionLocal() as session:
        config = await session.get(SSOConfig, _seed_saml[1].id)
        config.idp_certificate = idp_cert_pem
        await session.commit()
    _seed_saml[1].idp_certificate = idp_cert_pem

    # --- Build a SAML Response and sign the Assertion ---
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    later_iso = (datetime.now(UTC) + timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    earlier_iso = (datetime.now(UTC) - timedelta(minutes=5)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    assertion_xml = f"""<saml2:Assertion
        xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"
        xmlns:saml2p="urn:oasis:names:tc:SAML:2.0:protocol"
        ID="_test-assertion-id"
        IssueInstant="{now_iso}"
        Version="2.0">
      <saml2:Issuer>urn:example:idp</saml2:Issuer>
      <saml2:Subject>
        <saml2:NameID
            Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">
          samluser@samltest.com
        </saml2:NameID>
      </saml2:Subject>
      <saml2:Conditions
            NotBefore="{earlier_iso}"
            NotOnOrAfter="{later_iso}">
        <saml2:AudienceRestriction>
          <saml2:Audience>/auth/sso/metadata</saml2:Audience>
        </saml2:AudienceRestriction>
      </saml2:Conditions>
      <saml2:AttributeStatement>
        <saml2:Attribute Name="email">
          <saml2:AttributeValue>samluser@samltest.com</saml2:AttributeValue>
        </saml2:Attribute>
      </saml2:AttributeStatement>
    </saml2:Assertion>"""

    response_xml = (
        f"""<saml2p:Response
        xmlns:saml2p="urn:oasis:names:tc:SAML:2.0:protocol"
        xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"
        ID="_test-response-id"
        Version="2.0"
        IssueInstant="{now_iso}"
        Destination="/auth/sso/acs">
      <saml2:Issuer>urn:example:idp</saml2:Issuer>
      {assertion_xml}
    </saml2p:Response>"""
    )

    # Parse with lxml and sign the Assertion element in-place using signxml
    root = etree.fromstring(response_xml.encode())
    nsmap = {
        "saml2": "urn:oasis:names:tc:SAML:2.0:assertion",
        "saml2p": "urn:oasis:names:tc:SAML:2.0:protocol",
    }
    assertion_el = root.find(".//saml2:Assertion", nsmap)
    signed_assertion = XMLSigner(
        c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#",
        signature_algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
        digest_algorithm="http://www.w3.org/2001/04/xmlenc#sha256",
    ).sign(assertion_el, key=key, cert=idp_cert_pem)

    # The signed_assertion returned by sign() is the signed element (already
    # includes the <ds:Signature> child). Replace the unsigned one in-place.
    root.replace(assertion_el, signed_assertion)

    saml_xml_bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
    # SAML uses deflate compression (no header/checksum)
    import zlib as _zlib
    compressed = _zlib.compress(saml_xml_bytes)[2:-4]
    b64_encoded = base64.b64encode(compressed).decode()

    resp = await client.post(
        "/auth/sso/acs",
        data={"SAMLResponse": b64_encoded},
    )
    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}: {resp.text}"
    )
    data = resp.json()
    assert data["user"]["email"] == "samluser@samltest.com"

    # Verify user was created
    from sqlalchemy import select

    from app.models import Membership, User

    async with main_module.SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.email == "samluser@samltest.com")
        )
        assert user is not None
        assert user.email_verified is True
        assert user.password_hash
        membership = await session.scalar(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.org_id == _seed_saml[0].id,
            )
        )
        assert membership is not None
        assert membership.role == "editor"
