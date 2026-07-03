"""Authentication and RBAC helpers.

Auth can be disabled for local development. When it is enabled, endpoints
using these dependencies require a valid bearer token and enforce the role
minimum for the requested permission.

Dual-mode auth (C3): requests may authenticate via either:
  1. ``Authorization: Bearer <token>`` header (existing, always supported)
  2. ``nodyra_session`` httpOnly cookie set by POST /auth/login (SPA mode)

Bearer auth takes precedence when both are present.  Cookie-auth requests
MUST carry a valid ``X-CSRF-Token`` header on state-changing methods; the
CSRF middleware in ``main.py`` enforces this before route handlers run.
"""

import hashlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from app.config import settings
from app.db import get_session
from app.models import ApiToken, CustomRole, Membership, Organization, User
from app.services.crypto import decode_session_token
from app.tenancy import DEFAULT_ORG_ID, current_org_id, run_as_system

# ---------------------------------------------------------------------------
# OAuth introspection cache + circuit breaker (B-07)
# ---------------------------------------------------------------------------
# Cache valid introspection results for 60 s to avoid hammering the auth server
# on every MCP request. Keyed by BLAKE2b hash of the token.
_INTROSPECTION_CACHE_TTL = 60.0  # seconds
_INTROSPECTION_CACHE: dict[str, tuple[dict[str, Any], float]] = {}

# Circuit breaker: open when consecutive failures exceed threshold.
_CB_FAILURE_THRESHOLD = 5
_CB_RESET_AFTER = 30.0  # seconds
_cb_failures = 0
_cb_open_since: float | None = None


def _introspection_cache_key(token: str) -> str:
    return hashlib.blake2b(token.encode(), digest_size=16).hexdigest()


def _introspection_cache_get(token_hash: str) -> dict[str, Any] | None:
    entry = _INTROSPECTION_CACHE.get(token_hash)
    if entry is None:
        return None
    payload, expires_at = entry
    if time.monotonic() > expires_at:
        _INTROSPECTION_CACHE.pop(token_hash, None)
        return None
    return payload


def _introspection_cache_set(token_hash: str, payload: dict[str, Any]) -> None:
    _INTROSPECTION_CACHE[token_hash] = (payload, time.monotonic() + _INTROSPECTION_CACHE_TTL)
    # Evict stale entries if cache grows too large (> 2000 tokens)
    if len(_INTROSPECTION_CACHE) > 2000:
        now = time.monotonic()
        stale = [k for k, (_, exp) in _INTROSPECTION_CACHE.items() if now > exp]
        for k in stale:
            _INTROSPECTION_CACHE.pop(k, None)


def _cb_is_open() -> bool:
    global _cb_open_since
    if _cb_open_since is None:
        return False
    if time.monotonic() - _cb_open_since >= _CB_RESET_AFTER:
        _cb_open_since = None
        return False
    return True


def _cb_record_failure() -> None:
    global _cb_failures, _cb_open_since
    _cb_failures += 1
    if _cb_failures >= _CB_FAILURE_THRESHOLD:
        _cb_open_since = time.monotonic()
        _cb_failures = 0


def _cb_record_success() -> None:
    global _cb_failures
    _cb_failures = 0

VALID_ROLES = ("viewer", "editor", "admin", "owner")

# Permissions assignable to custom roles — operational permissions only.
# Admin/billing/SSO permissions are EXCLUDED: they remain locked to built-in roles.
CUSTOM_ROLE_PERMISSION_REGISTRY: frozenset[str] = frozenset({
    "workflow:read", "workflow:write", "workflow:run", "workflow:delete",
    "workflow:publish",
    "run:cancel_others",
    "credential:read_names",
    "credential:create",
    "audit:read",
    "mcp_connection:manage",
})


@dataclass(frozen=True)
class ExternalTokenGrant:
    org_id: str
    scopes: frozenset[str]


def get_client_ip(request: Request) -> str:
    """Return the real client IP, respecting ``trusted_proxy_count``.

    When ``trusted_proxy_count > 0`` the X-Forwarded-For header is read and
    the entry at position ``-(trusted_proxy_count)`` from the right is used
    (the rightmost entries are added by our own trusted proxies; the first
    entry we don't control is the client).  Falls back to the direct
    connection address when the header is absent or has too few entries.
    """
    n = settings.trusted_proxy_count
    if n > 0:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            parts = [p.strip() for p in xff.split(",")]
            idx = max(0, len(parts) - n)
            return parts[idx]
    if request.client:
        return request.client.host
    return "anon"

_ROLE_RANK = {
    "viewer": 10,
    "editor": 20,
    "admin": 30,
    "owner": 40,
}

_PERMISSION_MIN_ROLE = {
    "workflow:read": "viewer",
    "workflow:write": "editor",
    "workflow:run": "editor",
    "deployment:write": "editor",
    "deployment:run": "editor",
    "code_module:write": "editor",
    "pinned:write": "editor",
    "artifact:write": "editor",
    "artifact:delete": "editor",
    "credential:read": "editor",
    "credential:read_values": "admin",  # P1-24: decrypt credential values
    "credential:test": "editor",
    "credential:write": "admin",
    "environment:write": "admin",
    "runner_pool:write": "admin",
    "audit:read": "admin",
    "mcp_connection:manage": "admin",
    "workflow:delete": "admin",
    "workflow:publish": "editor",
    "run:cancel_others": "admin",
    "credential:read_names": "editor",
    "credential:create": "editor",
    "admin:users": "owner",
    "admin:sso": "owner",
    "admin:billing": "owner",
    "node_registry:install": "admin",
    "user:manage": "admin",
    "ops:drain": "admin",
    "ops:dead-letter:read": "editor",
    "ops:dead-letter:replay": "admin",
    "ops:pool:read": "editor",
    "ops:pool:resize": "admin",
    "ops:replicas:read": "editor",
}


def normalize_role(role: str | None) -> str:
    value = (role or "viewer").strip().lower()
    if value not in _ROLE_RANK:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported role '{role}'. Expected one of: {', '.join(VALID_ROLES)}.",
        )
    return value


def role_allows(role: str, minimum: str) -> bool:
    return _ROLE_RANK.get(role, 0) >= _ROLE_RANK[minimum]


def _extract_token(
    authorization: str | None,
    request: HTTPConnection | None,
) -> tuple[str | None, bool]:
    """Extract a Nodyra session token from the request.

    Returns ``(token_str, is_cookie_auth)``.  Bearer takes precedence over
    cookie so existing API consumers are unaffected.  ``is_cookie_auth`` lets
    callers (and the CSRF middleware) know which auth mode was used.
    """
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer "), False
    if request is not None:
        cookie = request.cookies.get(settings.session_cookie_name)
        if cookie:
            return cookie, True
    return None, False


def _token_is_revoked(user: User, payload: dict) -> bool:
    """C1: reject session tokens minted before the user's revocation cutoff."""
    cutoff = getattr(user, "sessions_valid_after", None)
    if cutoff is None:
        return False
    return float(payload.get("iat", 0)) < float(cutoff)


async def _user_from_session_token(
    token: str, session: AsyncSession, *, client_ip: str = ""
) -> User | None:
    """Resolve + validate a session token to a live, non-revoked user, or None.

    Returns ``None`` for any failure (bad/expired token, missing user, or a
    token revoked by ``sessions_valid_after``). Callers decide whether ``None``
    means 401 or anonymous.

    P1-6: when ``client_ip`` is provided and the token carries an ``ip`` claim,
    they must match.
    """
    payload = decode_session_token(token, client_ip=client_ip)
    if payload is None:
        return None
    user = await session.get(User, payload.get("sub"))
    if user is None:
        return None
    if _token_is_revoked(user, payload):
        return None
    # P1-3: gate unverified users when the setting is on
    if (
        settings.auth_require_verified_email
        and not getattr(user, "email_verified", False)
    ):
        return None
    return user


async def _api_token_principal(
    token: str, session: AsyncSession
) -> tuple[User, ApiToken] | None:
    if not token.startswith("ndpat_"):
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    # Token lookup is the one cross-org bootstrap read: possession of the
    # high-entropy secret resolves its tenant before normal org scoping begins.
    with run_as_system():
        row = await session.scalar(
            select(ApiToken).where(ApiToken.token_hash == digest)
        )
        if row is None or row.revoked_at is not None:
            return None
        expires_at = row.expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= datetime.now(UTC):
                return None
        user = await session.get(User, row.user_id)
        last_used = row.last_used_at
        if last_used is not None and last_used.tzinfo is None:
            last_used = last_used.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        if last_used is None or now - last_used >= timedelta(minutes=5):
            row.last_used_at = now
            await session.flush()  # flush into the open transaction; caller commits
    if user is None:
        return None
    return user, row


async def _external_oauth_principal(
    token: str, session: AsyncSession
) -> tuple[User, ExternalTokenGrant] | None:
    if not settings.mcp_oauth_introspection_url:
        return None

    token_hash = _introspection_cache_key(token)
    cached_payload = _introspection_cache_get(token_hash)

    if cached_payload is None:
        if _cb_is_open():
            return None

        import httpx

        auth = None
        if settings.mcp_oauth_client_id:
            auth = (settings.mcp_oauth_client_id, settings.mcp_oauth_client_secret)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    settings.mcp_oauth_introspection_url,
                    data={"token": token, "token_type_hint": "access_token"},
                    auth=auth,
                )
            response.raise_for_status()
            payload = response.json()
            _cb_record_success()
        except (httpx.HTTPError, ValueError):
            _cb_record_failure()
            return None

        if payload.get("active") is not True:
            return None
        _introspection_cache_set(token_hash, payload)
        cached_payload = payload

    subject = str(cached_payload.get("sub") or "").strip()
    org_id = str(cached_payload.get("org_id") or "").strip()
    if not subject or not org_id:
        return None
    raw_scopes = cached_payload.get("scope", cached_payload.get("scopes", []))
    scopes = (
        raw_scopes.split()
        if isinstance(raw_scopes, str)
        else [str(item) for item in raw_scopes]
    )
    with run_as_system():
        user = await session.scalar(
            select(User).where(or_(User.id == subject, User.email == subject.lower()))
        )
    if user is None:
        return None
    return user, ExternalTokenGrant(org_id=org_id, scopes=frozenset(scopes))


async def _principal_from_token(
    token: str, session: AsyncSession
) -> tuple[User, ApiToken | ExternalTokenGrant | None] | None:
    user = await _user_from_session_token(token, session)
    if user is not None:
        return user, None
    api_token = await _api_token_principal(token, session)
    if api_token is not None:
        return api_token
    if token.startswith("ndpat_"):
        return None
    return await _external_oauth_principal(token, session)


async def _principal_for_request(
    token: str, session: AsyncSession, request: HTTPConnection | None
) -> tuple[User, ApiToken | ExternalTokenGrant | None] | None:
    # Provider webhooks frequently carry third-party Bearer credentials. Never
    # forward those to the configured OAuth introspection endpoint; Nodyra PAT
    # and external OAuth grants are deliberately limited to /mcp.
    if request is None or request.url.path != "/mcp":
        user = await _user_from_session_token(token, session)
        return (user, None) if user is not None else None
    return await _principal_from_token(token, session)


def _set_principal_state(
    request: HTTPConnection, grant: ApiToken | ExternalTokenGrant | None
) -> None:
    if grant is None:
        request.state.api_token_scopes = None
        request.state.api_token_org_id = None
        return
    request.state.api_token_scopes = frozenset(str(s) for s in grant.scopes)
    request.state.api_token_org_id = grant.org_id


async def current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> User:
    token, is_cookie = _extract_token(authorization, request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    principal = await _principal_for_request(token, session, request)
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user, api_token = principal
    _set_principal_state(request, api_token)
    # Surface cookie-auth mode so the CSRF middleware can check it.
    request.state.cookie_auth = is_cookie
    return user


async def optional_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> User | None:
    token, is_cookie = _extract_token(authorization, request)
    if not token:
        if settings.auth_required:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
        return None
    principal = await _principal_for_request(token, session, request)
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user, api_token = principal
    _set_principal_state(request, api_token)
    request.state.cookie_auth = is_cookie
    return user


async def _lenient_session_user(
    authorization: str | None,
    session: AsyncSession,
    request: HTTPConnection | None = None,
) -> User | None:
    """The session user, or None — never raises.

    The global org-resolution dependency runs on EVERY request, including
    webhook ingress where the Authorization header carries the *webhook's*
    Basic/Bearer/JWT credential, not a Nodyra session token. Those must not
    401 here; endpoints that require session auth still depend on the strict
    ``current_user``/``require_role`` chain.

    Also checks the httpOnly session cookie (C3 dual-mode auth) when
    ``request`` is provided.
    """
    token, _ = _extract_token(authorization, request)
    if not token:
        return None
    principal = await _principal_for_request(token, session, request)
    if principal is None:
        return None
    user, api_token = principal
    if request is not None:
        _set_principal_state(request, api_token)
    return user


async def resolve_org(
    request: HTTPConnection,
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> str | None:
    """Resolve and validate the request's organization (multi-tenancy A4/A6).

    Registered as a global app dependency so *every* request sets the
    request-scoped org context both enforcement layers read. Flag off ->
    ``None``, nothing is set, single-tenant behaviour unchanged.
    """
    if not settings.multi_tenancy_enabled:
        return None
    user = await _lenient_session_user(authorization, session, request)
    token_org_id = getattr(request.state, "api_token_org_id", None)
    if token_org_id and x_org_id and x_org_id.strip() != token_org_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This automation token is bound to a different organization.",
        )
    return await resolve_org_for(x_org_id or token_org_id, user, session)


async def resolve_org_for(
    x_org_id: str | None,
    user: User | None,
    session: AsyncSession,
) -> str | None:
    """Validate org access for an already-resolved user; sets the ContextVar.

    The ContextVar is set *before* the membership query: ``Membership``
    carries ``org_id``, so the session filter scopes that query to the
    requested org — set-after would scope it to the previous/default org and
    always refuse.
    """
    if not settings.multi_tenancy_enabled:
        return None
    org_id = (x_org_id or DEFAULT_ORG_ID).strip() or DEFAULT_ORG_ID
    current_org_id.set(org_id)
    # Authentication usually starts the transaction before the org header is
    # resolved. Refresh the transaction-local Postgres GUC now; relying only
    # on Session.after_begin would leave it pinned to the default org for the
    # rest of a non-default-org request.
    connection = await session.connection()
    if connection.dialect.name == "postgresql":
        from sqlalchemy import text

        await connection.execute(
            text("SELECT set_config('app.current_org', :org, true)"),
            {"org": org_id},
        )
    # Mirror the resolved org into the logging context (H4) so log lines emitted
    # for the rest of this request carry ``org_id`` for correlation.
    from app.logging import _org_id as _log_org_id

    _log_org_id.set(org_id)
    if user is None:
        # Anonymous (auth disabled or public endpoint): only the default org
        # is reachable without an identity to check membership against.
        if org_id != DEFAULT_ORG_ID:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Sign-in required to access this organization.",
            )
        return org_id
    if await session.get(Organization, org_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    member = await session.scalar(
        select(Membership.id).where(
            Membership.org_id == org_id, Membership.user_id == user.id
        )
    )
    if member is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Not a member of this organization."
        )
    return org_id


async def _role_for(
    session: AsyncSession, user: User, org_id: str | None
) -> str:
    """The role governing this request: the user's membership role within the
    request org when multi-tenancy is on, else the global ``User.role``."""
    if not settings.multi_tenancy_enabled or org_id is None:
        return user.role
    membership_role = await session.scalar(
        select(Membership.role).where(
            Membership.org_id == org_id, Membership.user_id == user.id
        )
    )
    if membership_role is None:
        # resolve_org already refused non-members; belt and braces.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Not a member of this organization."
        )
    return membership_role


async def _membership_for(
    session: AsyncSession, user: User, org_id: str | None
) -> Membership | None:
    """Return the user's membership within the request org, or None."""
    if not settings.multi_tenancy_enabled or org_id is None:
        return None
    return await session.scalar(
        select(Membership).where(
            Membership.org_id == org_id, Membership.user_id == user.id
        )
    )


def require_role(
    minimum: str,
    *,
    require_authenticated: bool = False,
    permission: str | None = None,
) -> Callable[..., Awaitable[User | None]]:
    minimum = normalize_role(minimum)

    async def dependency(
        request: Request,
        user: User | None = Depends(optional_current_user),
        org_id: str | None = Depends(resolve_org),
        session: AsyncSession = Depends(get_session),
    ) -> User | None:
        if user is None:
            if require_authenticated:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED,
                    "Sign-in required for this operation.",
                )
            return None
        role = await _role_for(session, user, org_id)
        # C4: Custom role check — if the user has a custom_role_id, use its
        # permissions exclusively (replaces, not augments, the built-in role).
        # admin:* and node_registry:install are never in custom roles.
        if permission:
            # Resolve membership to check for custom_role_id.
            membership = await _membership_for(session, user, org_id)
            if membership and membership.custom_role_id:
                custom_role = await session.get(CustomRole, membership.custom_role_id)
                if custom_role is not None:
                    perms = frozenset(custom_role.permissions or [])
                    if permission not in perms:
                        raise HTTPException(
                            status.HTTP_403_FORBIDDEN,
                            f"Custom role '{custom_role.name}' does not grant '{permission}'.",
                        )
                    # Custom role granted the permission — skip built-in role check.
                    token_scopes = getattr(request.state, "api_token_scopes", None)
                    if token_scopes is not None and permission not in token_scopes and "*" not in token_scopes:
                        raise HTTPException(
                            status.HTTP_403_FORBIDDEN,
                            f"Automation token does not grant {permission}.",
                        )
                    return user

        if not role_allows(role, minimum):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires {minimum} role or higher.",
            )
        token_scopes = getattr(request.state, "api_token_scopes", None)
        if (
            permission
            and token_scopes is not None
            and permission not in token_scopes
            and "*" not in token_scopes
        ):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Automation token does not grant {permission}.",
            )
        return user

    return dependency


def validate_custom_role_permissions(permissions: list[str]) -> None:
    """Validate that all permission strings are in the registry. Raises 422 on unknown strings."""
    for p in permissions:
        if p not in CUSTOM_ROLE_PERMISSION_REGISTRY:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Unknown permission '{p}'. "
                    f"Valid permissions: {', '.join(sorted(CUSTOM_ROLE_PERMISSION_REGISTRY))}."
                ),
            )


# Permissions that MUST have an authenticated actor even when ``auth_required``
# is globally off. Without this, a local-dev instance with auth disabled would
# let an anonymous request create or delete users, including owners — and the
# "only owner can manage owners" guard short-circuits because the actor is
# None. Anything that modifies the user/role surface itself goes here.
_REQUIRES_AUTHENTICATED = frozenset({"user:manage"})


def require_permission(permission: str) -> Callable[..., Awaitable[User | None]]:
    minimum = _PERMISSION_MIN_ROLE.get(permission)
    if minimum is None:
        raise ValueError(f"Unknown RBAC permission: {permission}")
    return require_role(
        minimum,
        require_authenticated=permission in _REQUIRES_AUTHENTICATED,
        permission=permission,
    )


def require_instance_permission(
    permission: str,
) -> Callable[..., Awaitable[User | None]]:
    """Authorize an installation-global operation from ``User.role`` only.

    Workspace membership must never grant access to global users, singleton
    runtime settings, or the installation license in multi-tenant mode.
    """
    minimum = _PERMISSION_MIN_ROLE.get(permission)
    if minimum is None:
        raise ValueError(f"Unknown RBAC permission: {permission}")
    require_authenticated = permission in _REQUIRES_AUTHENTICATED

    async def dependency(
        request: Request,
        user: User | None = Depends(optional_current_user),
    ) -> User | None:
        if user is None:
            if require_authenticated:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED,
                    "Sign-in required for this operation.",
                )
            return None
        if getattr(request.state, "api_token_scopes", None) is not None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Organization-scoped automation tokens cannot access instance settings.",
            )
        if not role_allows(user.role, minimum):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires instance {minimum} role or higher.",
            )
        return user

    return dependency
