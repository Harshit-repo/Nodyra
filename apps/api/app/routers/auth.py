import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import User
from app.schemas import (
    AuthRequiredResponse,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserAdminInfo,
    UserCreate,
    UserInfo,
    UserUpdate,
    WsTicketResponse,
)
from app.security import (
    _extract_token,
    _user_from_session_token,
    current_user,
    get_client_ip,
    normalize_role,
    require_permission,
)
from app.services import rate_limit
from app.services.audit import log_audit
from app.services.crypto import (
    create_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])
# REST-conventional alias surface: /users/me mirrors /auth/me so clients that
# treat the user object as the canonical "me" resource don't 404.
users_router = APIRouter(prefix="/users", tags=["users"])
require_user_manage = require_permission("user:manage")


# Per-(bucket, IP) rate limiting for unauthenticated endpoints is delegated to
# the shared limiter (app.services.rate_limit), which is Redis-backed across
# replicas and falls back to an in-process sliding window — see H5.
async def _enforce_auth_rate_limit(request: Request, bucket: str) -> None:
    if not settings.auth_rate_limit_enabled:
        return
    ip = get_client_ip(request)
    allowed = await rate_limit.allow(
        bucket, ip, limit=settings.auth_rate_limit_per_minute
    )
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many {bucket} attempts; try again in a minute.",
        )


async def _user_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(User)) or 0)


async def _owner_count(session: AsyncSession) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(User).where(User.role == "owner")
        )
        or 0
    )


def _email(value: str) -> str:
    return value.strip().lower()


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _token_response(user: User) -> TokenResponse:
    return TokenResponse(
        token=create_token(user.id),
        user=UserInfo(
            id=user.id,
            email=user.email,
            name=user.name,
            company=user.company,
            role=user.role,
        ),
    )


def _set_session_cookies(response: Response, token: str) -> None:
    """Attach httpOnly session cookie + non-httpOnly CSRF cookie to the response.

    The CSRF cookie carries a same-value token the SPA must echo in the
    ``X-CSRF-Token`` header.  It is NOT httpOnly so JS can read it.
    The session cookie IS httpOnly so JS cannot access the bearer token.
    """
    import secrets

    csrf_value = secrets.token_urlsafe(32)
    ttl = settings.auth_token_ttl_seconds
    samesite = settings.session_cookie_samesite

    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=ttl,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=samesite,
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_value,
        max_age=ttl,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite=samesite,
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
    )
    response.delete_cookie(
        key=settings.csrf_cookie_name,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
    )


async def _assert_role_change_allowed(
    session: AsyncSession,
    actor: User | None,
    target: User,
    new_role: str,
) -> None:
    if actor is None:
        return
    has_owner = await _owner_count(session) > 0
    if actor.role != "owner" and (
        target.role == "owner" or (new_role == "owner" and has_owner)
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only an owner can manage owner accounts.",
        )
    if (
        target.role == "owner"
        and new_role != "owner"
        and await _owner_count(session) <= 1
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Cannot remove the last owner account.",
        )
    if actor.id == target.id and target.role in {"owner", "admin"} and new_role not in {
        "owner",
        "admin",
    }:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Cannot downgrade your own administrator account.",
        )


@router.post(
    "/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED
)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    await _enforce_auth_rate_limit(request, "register")
    email = _email(body.email)
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    count = await _user_count(session)
    if count > 0 and not settings.auth_allow_registration:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Registration is closed. Ask an admin to create an account.",
        )
    role = "owner" if count == 0 else normalize_role(settings.auth_registration_role)

    user = User(
        email=email,
        name=_clean(body.name),
        company=_clean(body.company),
        password_hash=hash_password(body.password),
        role=role,
    )
    session.add(user)
    await log_audit(session, "register", "user", detail=f"{email} ({role})")
    try:
        await session.commit()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    result = _token_response(user)
    _set_session_cookies(response, result.token)
    return result


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    await _enforce_auth_rate_limit(request, "login")
    user = await session.scalar(select(User).where(User.email == _email(body.email)))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid email or password"
        )
    result = _token_response(user)
    _set_session_cookies(response, result.token)
    return result


def _revoke_sessions(user: User) -> None:
    """Stamp the per-user revocation cutoff (C1).

    Every outstanding token has ``iat`` < now (it was minted earlier), so the
    cutoff invalidates them all. A re-login moments later mints a token with a
    strictly-larger ``iat`` (sub-second resolution) and survives.
    """
    user.sessions_valid_after = time.time()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    """Clear the httpOnly session cookie and CSRF cookie.

    Safe to call when not signed in (idempotent cookie deletion).
    """
    _clear_session_cookies(response)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    response: Response,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Revoke every session for the current user ("log out everywhere").

    Invalidates all outstanding tokens (this device included) by advancing the
    user's revocation cutoff, then clears this response's cookies.
    """
    _revoke_sessions(user)
    await log_audit(
        session, "revoke_sessions", "user", user.id, user.email,
        actor_id=user.id, actor_email=user.email,
    )
    await session.commit()
    _clear_session_cookies(response)


@router.post(
    "/users/{user_id}/revoke-sessions",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_user_manage)],
)
async def revoke_user_sessions(
    user_id: str,
    actor: User | None = Depends(require_user_manage),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Admin lockout: revoke all of a target user's sessions (C1)."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    _revoke_sessions(user)
    await log_audit(
        session, "revoke_sessions", "user", user.id, user.email,
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
    await session.commit()


@router.post("/ws-ticket", response_model=WsTicketResponse)
async def ws_ticket(user: User = Depends(current_user)) -> WsTicketResponse:
    """Mint a single-use WebSocket authentication ticket.

    Browsers can't send custom headers on WS upgrades, so the SPA calls this
    endpoint first to get a short-lived ticket and passes it as ``?ticket=``
    on the WS URL.  The ticket is consumed on first use.
    """
    from app.services.ws_ticket import create_ticket

    ticket = await create_ticket(user.id)
    return WsTicketResponse(ticket=ticket)


@router.get("/me", response_model=UserInfo)
async def me(user: User = Depends(current_user)):
    return user


@users_router.get("/me", response_model=UserInfo)
async def users_me(user: User = Depends(current_user)):
    """REST-conventional alias for ``GET /auth/me``."""
    return user


@router.get("/required", response_model=AuthRequiredResponse)
async def auth_required(
    request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Frontend bootstrap — tells the UI whether to show a login screen."""
    user: User | None = None
    token, _ = _extract_token(authorization, request)
    if token:
        user = await _user_from_session_token(token, session)
    count = await _user_count(session)
    from app.services.licensing import current_license

    lic = await current_license()
    return AuthRequiredResponse(
        auth_required=settings.auth_required,
        signed_in=user is not None,
        registration_open=count == 0 or settings.auth_allow_registration,
        multi_tenancy=settings.multi_tenancy_enabled,
        edition=lic.edition.value,
        entitlements=[f.value for f in lic.features],
        limits={
            "environments": lic.limits.environments,
            "runners": lic.limits.runners,
            "deployments": lic.limits.deployments,
            "seats": lic.limits.seats,
        },
        license_notice=lic.notice,
        user=UserInfo.model_validate(user) if user is not None else None,
    )


@router.get(
    "/users",
    response_model=list[UserAdminInfo],
    dependencies=[Depends(require_user_manage)],
)
async def list_users(session: AsyncSession = Depends(get_session)):
    result = await session.scalars(select(User).order_by(User.created_at, User.email))
    return list(result.all())


@router.post(
    "/users",
    response_model=UserAdminInfo,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    body: UserCreate,
    actor: User | None = Depends(require_user_manage),
    session: AsyncSession = Depends(get_session),
):
    role = normalize_role(body.role)
    if (
        actor is not None
        and actor.role != "owner"
        and role == "owner"
        and await _owner_count(session) > 0
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only an owner can create another owner.",
        )
    email = _email(body.email)
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    from app.services.licensing import enforce_resource_cap
    await enforce_resource_cap(session, "seats")
    user = User(
        email=email,
        name=_clean(body.name),
        company=_clean(body.company),
        password_hash=hash_password(body.password),
        role=role,
    )
    session.add(user)
    await log_audit(session, "create", "user", detail=f"{email} ({role})",
                    actor_id=actor.id if actor else None,
                    actor_email=actor.email if actor else None)
    await session.commit()
    await session.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserAdminInfo)
async def update_user(
    user_id: str,
    body: UserUpdate,
    actor: User | None = Depends(require_user_manage),
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    role = normalize_role(body.role)
    await _assert_role_change_allowed(session, actor, user, role)
    user.role = role
    await log_audit(session, "update_role", "user", user.id, f"{user.email} -> {role}",
                    actor_id=actor.id if actor else None,
                    actor_email=actor.email if actor else None)
    await session.commit()
    await session.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    actor: User | None = Depends(require_user_manage),
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if actor is not None and actor.id == user.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "You cannot delete your own account while signed in.",
        )
    if user.role == "owner":
        if actor is not None and actor.role != "owner":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only an owner can delete another owner.",
            )
        if await _owner_count(session) <= 1:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Cannot delete the last owner account.",
            )
    await log_audit(session, "delete", "user", user.id, user.email,
                    actor_id=actor.id if actor else None,
                    actor_email=actor.email if actor else None)
    await session.delete(user)
    await session.commit()
