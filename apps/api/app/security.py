"""Authentication and RBAC helpers.

Auth can be disabled for local development. When it is enabled, endpoints
using these dependencies require a valid bearer token and enforce the role
minimum for the requested permission.

Dual-mode auth (C3): requests may authenticate via either:
  1. ``Authorization: Bearer <token>`` header (existing, always supported)
  2. ``noodle_session`` httpOnly cookie set by POST /auth/login (SPA mode)

Bearer auth takes precedence when both are present.  Cookie-auth requests
MUST carry a valid ``X-CSRF-Token`` header on state-changing methods; the
CSRF middleware in ``main.py`` enforces this before route handlers run.
"""

from collections.abc import Awaitable, Callable

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from app.config import settings
from app.db import get_session
from app.models import Membership, Organization, User
from app.services.crypto import verify_token
from app.tenancy import DEFAULT_ORG_ID, current_org_id

VALID_ROLES = ("viewer", "editor", "admin", "owner")

_ROLE_RANK = {
    "viewer": 10,
    "editor": 20,
    "admin": 30,
    "owner": 40,
}

_PERMISSION_MIN_ROLE = {
    "workflow:write": "editor",
    "workflow:run": "editor",
    "deployment:write": "editor",
    "deployment:run": "editor",
    "code_module:write": "editor",
    "pinned:write": "editor",
    "artifact:write": "editor",
    "artifact:delete": "editor",
    "credential:read": "editor",
    "credential:test": "editor",
    "credential:write": "admin",
    "environment:write": "admin",
    "runner_pool:write": "admin",
    "audit:read": "admin",
    "user:manage": "admin",
    "ops:drain": "admin",
    "ops:dead-letter:read": "editor",
    "ops:dead-letter:replay": "admin",
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
    """Extract a Noodle session token from the request.

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


async def current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> User:
    token, is_cookie = _extract_token(authorization, request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user_id = verify_token(token)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
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
    user_id = verify_token(token)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
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
    Basic/Bearer/JWT credential, not a Noodle session token. Those must not
    401 here; endpoints that require session auth still depend on the strict
    ``current_user``/``require_role`` chain.

    Also checks the httpOnly session cookie (C3 dual-mode auth) when
    ``request`` is provided.
    """
    token, _ = _extract_token(authorization, request)
    if not token:
        return None
    user_id = verify_token(token)
    if user_id is None:
        return None
    return await session.get(User, user_id)


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
    return await resolve_org_for(x_org_id, user, session)


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


def require_role(
    minimum: str, *, require_authenticated: bool = False
) -> Callable[..., Awaitable[User | None]]:
    minimum = normalize_role(minimum)

    async def dependency(
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
        if not role_allows(role, minimum):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires {minimum} role or higher.",
            )
        return user

    return dependency


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
    )
