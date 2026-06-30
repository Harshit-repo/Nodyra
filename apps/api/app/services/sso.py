"""SSO authentication service: OIDC + SAML flows.

OIDC implements the authorization-code flow: authorization URL builder with
Redis-backed state storage, code exchange, and ID token validation.
SAML stubs are provided for the POST-based ACS endpoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import urllib.parse

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import AuthError
from app.models import Membership, Organization, SSOConfig, User
from app.redis_client import redis_client
from app.services.crypto import hash_password

logger = logging.getLogger(__name__)

_discovery_cache: dict[str, tuple[float, dict]] = {}
_discovery_lock = asyncio.Lock()
_jwks_cache: dict[str, tuple[float, dict]] = {}
_jwks_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# OIDC Flow
# ---------------------------------------------------------------------------


async def oidc_authorization_url(sso_config: SSOConfig) -> str:
    """Build the OIDC authorization redirect URL.

    Persists state + nonce in Redis with 600s TTL for callback validation.
    Returns the redirect URL only.
    """
    discovery = await _fetch_oidc_discovery(sso_config.discovery_url)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    await redis_client.set(
        f"noodle:sso:state:{state}",
        json.dumps({"nonce": nonce, "org_id": sso_config.org_id}),
        ex=600,
    )
    redirect_uri = _sso_redirect_uri()
    params = {
        "client_id": sso_config.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
    }
    return f"{discovery['authorization_endpoint']}?{urllib.parse.urlencode(params)}"


async def oidc_exchange_code(
    sso_config: SSOConfig, code: str, *, pending: dict, session: AsyncSession
) -> dict:
    """Exchange authorization code for tokens, validate nonce, return user claims.

    *pending* is the dict previously stored in Redis under the state key
    (already parsed by the caller).  It must contain ``org_id`` and ``nonce``.
    """
    if pending.get("org_id") != sso_config.org_id:
        raise AuthError("SSO state org mismatch")
    nonce = pending["nonce"]

    discovery = await _fetch_oidc_discovery(sso_config.discovery_url)
    client_secret = await _decrypt_client_secret(sso_config, session)
    tokens = await _token_exchange(discovery, sso_config, code, client_secret)
    claims = await _validate_id_token(tokens["id_token"], discovery, nonce=nonce, client_id=sso_config.client_id or "")
    return {
        "email": claims["email"],
        "name": claims.get("name") or claims.get("email"),
        "sub": claims["sub"],
    }


# ---------------------------------------------------------------------------
# JIT Provisioning
# ---------------------------------------------------------------------------


async def get_or_create_sso_user(
    claims: dict, sso_config: SSOConfig, session: AsyncSession
) -> User:
    """Find or create a user from SSO claims, ensuring org membership."""
    email = claims["email"].lower()
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        if not sso_config.jit_provisioning:
            raise AuthError("JIT provisioning disabled; user must be pre-invited")
        user = User(
            email=email,
            name=claims.get("name", email),
            password_hash=hash_password(secrets.token_urlsafe(48)),
            email_verified=True,
            sso_subject=claims["sub"],
        )
        session.add(user)
        await session.flush()

    # Always ensure org membership exists
    existing_membership = await session.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.org_id == sso_config.org_id,
        )
    )
    if existing_membership is None:
        if not sso_config.jit_provisioning:
            raise AuthError("JIT provisioning disabled; user must be pre-invited")
        session.add(
            Membership(
                user_id=user.id, org_id=sso_config.org_id, role="editor"
            )
        )
        await session.flush()
    return user


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


async def get_sso_config_by_org_slug(
    slug: str, session: AsyncSession
) -> SSOConfig | None:
    """Resolve ``org_slug`` -> ``Organization`` -> ``SSOConfig``."""
    org = await session.scalar(
        select(Organization).where(Organization.slug == slug)
    )
    if org is None:
        return None
    return await session.scalar(
        select(SSOConfig).where(SSOConfig.org_id == org.id)
    )


async def detect_sso_by_email(
    email: str, session: AsyncSession
) -> dict | None:
    """Check if an email domain has SSO configured.

    Returns ``{"has_sso": True, "org_slug": "..."}`` or ``None``.

    Timing-safe: adds a small random delay when no SSO is found to prevent
    enumeration of configured domains via response-time analysis.
    """
    import random

    domain = email.strip().lower().split("@")[-1] if "@" in email else None
    if not domain:
        return None
    config = await session.scalar(
        select(SSOConfig).where(SSOConfig.email_domain == domain)
    )
    if config is None:
        # Mitigate timing enumeration: add jitter so "no config" takes ~same
        # time as the org lookup that follows a successful config match.
        await asyncio.sleep(random.uniform(0.01, 0.05))
        return None
    org = await session.get(Organization, config.org_id)
    if org is None:
        return None
    return {"has_sso": True, "org_slug": org.slug}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sso_redirect_uri() -> str:
    base = settings.oauth_redirect_base_url or settings.public_api_url or ""
    if base:
        return f"{base.rstrip('/')}/auth/sso/callback"
    return "/auth/sso/callback"


async def _fetch_oidc_discovery(discovery_url: str) -> dict:
    """Fetch and cache the OIDC discovery document (1h TTL)."""
    now = time.monotonic()
    if discovery_url in _discovery_cache:
        cached_at, doc = _discovery_cache[discovery_url]
        if now - cached_at < 3600:
            return doc
    from noodle_nodes.http_security import assert_public_http_url

    assert_public_http_url(discovery_url, context="OIDC discovery")
    async with _discovery_lock:
        if discovery_url in _discovery_cache:
            cached_at, doc = _discovery_cache[discovery_url]
            if now - cached_at < 3600:
                return doc
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            resp = await client.get(discovery_url)
            resp.raise_for_status()
            doc = resp.json()
        # Validate JWKS and token endpoints from discovery doc before caching
        for key in ("jwks_uri", "token_endpoint", "authorization_endpoint"):
            if key in doc and isinstance(doc[key], str):
                assert_public_http_url(doc[key], context=f"OIDC discovery {key}")
        _discovery_cache[discovery_url] = (time.monotonic(), doc)
        return doc


async def _decrypt_client_secret(
    sso_config: SSOConfig, session: AsyncSession
) -> str | None:
    """Decrypt single-field Fernet-encrypted client_secret using org KEK."""
    if not sso_config.client_secret:
        return None
    from app.services.org_keys import get_org_kek

    org_kek = await get_org_kek(sso_config.org_id, session)
    if org_kek is None:
        raise AuthError("Cannot decrypt client_secret: org KEK not available")
    return Fernet(org_kek).decrypt(sso_config.client_secret.encode()).decode()


async def _token_exchange(
    discovery: dict,
    sso_config: SSOConfig,
    code: str,
    client_secret: str | None,
) -> dict:
    """POST authorization code to token endpoint."""
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        resp = await client.post(
            discovery["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": sso_config.client_id,
                "client_secret": client_secret,
                "redirect_uri": _sso_redirect_uri(),
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _validate_id_token(
    id_token: str, discovery: dict, *, nonce: str, client_id: str
) -> dict:
    """Validate OIDC ID token using authlib."""
    from authlib.jose import JsonWebKey, JsonWebToken

    jwks_uri = discovery["jwks_uri"]
    now = time.monotonic()
    async with _jwks_lock:
        if jwks_uri not in _jwks_cache or now - _jwks_cache[jwks_uri][0] >= 3600:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                resp = await client.get(jwks_uri)
                resp.raise_for_status()
            _jwks_cache[jwks_uri] = (time.monotonic(), resp.json())
    jwks_data = _jwks_cache[jwks_uri][1]
    jwt = JsonWebToken(["RS256", "ES256"])
    claims = jwt.decode(id_token, JsonWebKey.import_key_set(jwks_data))
    claims.validate()
    # Validate issuer against discovery document (OIDC Core 3.1.3.7.2)
    expected_iss = discovery.get("issuer", "")
    if expected_iss and claims.get("iss") != expected_iss:
        raise AuthError("ID token issuer mismatch")
    if claims.get("nonce") != nonce:
        raise AuthError("ID token nonce mismatch - replay attack suspected")
    if claims.get("aud") != client_id:
        raise AuthError("ID token audience mismatch — not issued for this client")
    if "email" not in claims:
        raise AuthError("ID token missing email claim")
    return dict(claims)


# ---------------------------------------------------------------------------
# SAML stubs
# ---------------------------------------------------------------------------


def build_saml_sp_metadata(sso_config: SSOConfig) -> str:
    """Generate SP metadata XML for SAML configuration using proper XML construction
    (avoids injection via f-string interpolation of entity_id / acs_url)."""
    import xml.etree.ElementTree as ET

    entity_id = _saml_entity_id()
    acs_url = _saml_acs_url()

    # Build the XML tree programmatically so entity_id and acs_url are
    # properly escaped by ElementTree rather than interpolated raw.
    root = ET.Element(
        "{urn:oasis:names:tc:SAML:2.0:metadata}EntityDescriptor",
        attrib={"entityID": entity_id},
    )
    sp = ET.SubElement(
        root,
        "{urn:oasis:names:tc:SAML:2.0:metadata}SPSSODescriptor",
        attrib={
            "protocolSupportEnumeration": "urn:oasis:names:tc:SAML:2.0:protocol",
        },
    )
    ET.SubElement(
        sp,
        "{urn:oasis:names:tc:SAML:2.0:metadata}AssertionConsumerService",
        attrib={
            "Binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            "Location": acs_url,
            "index": "0",
        },
    )
    # Use minidom or simple serialisation for a clean XML declaration
    xml_bytes = ET.tostring(root, encoding="unicode", xml_declaration=True)
    return xml_bytes


def _saml_entity_id() -> str:
    base = settings.oauth_redirect_base_url or settings.public_api_url or ""
    if base:
        return f"{base.rstrip('/')}/auth/sso/metadata"
    return "/auth/sso/metadata"


def _saml_acs_url() -> str:
    base = settings.oauth_redirect_base_url or settings.public_api_url or ""
    if base:
        return f"{base.rstrip('/')}/auth/sso/acs"
    return "/auth/sso/acs"
