"""Authentication and RBAC helpers.

Auth can be disabled for local development. When it is enabled, endpoints
using these dependencies require a valid bearer token and enforce the role
minimum for the requested permission.
"""

from collections.abc import Awaitable, Callable

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import User
from app.services.crypto import verify_token

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


async def current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user_id = verify_token(authorization.removeprefix("Bearer "))
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user


async def optional_current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> User | None:
    if not authorization:
        if settings.auth_required:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
        return None
    return await current_user(authorization=authorization, session=session)


def require_role(
    minimum: str, *, require_authenticated: bool = False
) -> Callable[..., Awaitable[User | None]]:
    minimum = normalize_role(minimum)

    async def dependency(
        user: User | None = Depends(optional_current_user),
    ) -> User | None:
        if user is None:
            if require_authenticated:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED,
                    "Sign-in required for this operation.",
                )
            return None
        if not role_allows(user.role, minimum):
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
