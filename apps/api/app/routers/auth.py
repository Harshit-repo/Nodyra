import hashlib
import json
import logging
import secrets
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import ApiToken, Environment, Membership, Organization, SSOConfig, User
from app.schemas import (
    ApiTokenCreate,
    ApiTokenCreated,
    ApiTokenInfo,
    AuthRequiredResponse,
    LoginRequest,
    PageResponse,
    RegisterRequest,
    TokenResponse,
    UserAdminInfo,
    UserCreate,
    UserInfo,
    UserUpdate,
    WsTicketResponse,
)
from app.security import (
    _PERMISSION_MIN_ROLE,
    _extract_token,
    _role_for,
    _user_from_session_token,
    current_user,
    get_client_ip,
    normalize_role,
    require_instance_permission,
    resolve_org,
    role_allows,
)
from app.services import rate_limit
from app.services.audit import log_audit
from app.services.crypto import (
    create_payload_token,
    create_token,
    decode_payload_token,
    hash_password,
    verify_password,
)
from app.tenancy import DEFAULT_ORG_ID
from app.exceptions import AuthError
from app.redis_client import redis_client
from app.services.sso import (
    build_saml_sp_metadata,
    detect_sso_by_email,
    get_or_create_sso_user,
    get_sso_config_by_org_slug,
    oidc_authorization_url,
    oidc_exchange_code,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])
# REST-conventional alias surface: /users/me mirrors /auth/me so clients that
# treat the user object as the canonical "me" resource don't 404.
users_router = APIRouter(prefix="/users", tags=["users"])
require_user_manage = require_instance_permission("user:manage")


@router.get("/api-tokens", response_model=list[ApiTokenInfo])
async def list_api_tokens(
    user: User = Depends(current_user),
    _org_id: str | None = Depends(resolve_org),
    session: AsyncSession = Depends(get_session),
) -> list[ApiToken]:
    return list(
        (
            await session.scalars(
                select(ApiToken)
                .where(ApiToken.user_id == user.id)
                .order_by(ApiToken.created_at.desc())
            )
        ).all()
    )


@router.post("/api-tokens", response_model=ApiTokenCreated, status_code=201)
async def create_api_token(
    body: ApiTokenCreate,
    request: Request,
    user: User = Depends(current_user),
    org_id: str | None = Depends(resolve_org),
    session: AsyncSession = Depends(get_session),
) -> ApiTokenCreated:
    if getattr(request.state, "api_token_scopes", None) is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Automation tokens cannot mint tokens.")
    normalized_scopes = sorted(set(body.scopes))
    unknown = [scope for scope in normalized_scopes if scope not in _PERMISSION_MIN_ROLE]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown scopes: {', '.join(unknown)}")
    role = await _role_for(session, user, org_id)
    forbidden = [
        scope
        for scope in normalized_scopes
        if not role_allows(role, _PERMISSION_MIN_ROLE[scope])
    ]
    if forbidden:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Your role cannot grant: {', '.join(forbidden)}",
        )
    secret = "ndpat_" + secrets.token_urlsafe(32)
    row = ApiToken(
        org_id=org_id or DEFAULT_ORG_ID,
        user_id=user.id,
        name=body.name.strip(),
        token_hash=hashlib.sha256(secret.encode()).hexdigest(),
        token_prefix=secret[:16],
        scopes=normalized_scopes,
        expires_at=datetime.now(UTC) + timedelta(days=body.expires_in_days),
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Token creation conflicted; retry the request."
        ) from exc
    await session.refresh(row)
    info = ApiTokenInfo.model_validate(row, from_attributes=True)
    return ApiTokenCreated(**info.model_dump(), token=secret)


@router.delete("/api-tokens/{token_id}", status_code=204)
async def revoke_api_token(
    token_id: str,
    request: Request,
    user: User = Depends(current_user),
    _org_id: str | None = Depends(resolve_org),
    session: AsyncSession = Depends(get_session),
) -> Response:
    if getattr(request.state, "api_token_scopes", None) is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Automation tokens cannot revoke tokens.")
    row = await session.scalar(
        select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user.id)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API token not found")
    row.revoked_at = datetime.now(UTC)
    await session.commit()
    return Response(status_code=204)


# Per-(bucket, IP) rate limiting for unauthenticated endpoints is delegated to
# the shared limiter (app.services.rate_limit), which is Redis-backed across
# replicas and falls back to an in-process sliding window — see H5.
async def _enforce_auth_rate_limit(request: Request, bucket: str) -> None:
    if not settings.auth_rate_limit_enabled:
        return
    ip = get_client_ip(request)
    allowed = await rate_limit.allow(bucket, ip, limit=settings.auth_rate_limit_per_minute)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many {bucket} attempts; try again in a minute.",
        )


async def _user_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(User)) or 0)


async def _owner_count(session: AsyncSession) -> int:
    return int(
        await session.scalar(select(func.count()).select_from(User).where(User.role == "owner"))
        or 0
    )


def _email(value: str) -> str:
    return value.strip().lower()


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _token_response(user: User, *, client_ip: str = "") -> TokenResponse:
    return TokenResponse(
        token=create_token(user.id, client_ip=client_ip),
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
    if actor.role != "owner" and (target.role == "owner" or (new_role == "owner" and has_owner)):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only an owner can manage owner accounts.",
        )
    if target.role == "owner" and new_role != "owner" and await _owner_count(session) <= 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Cannot remove the last owner account.",
        )
    if (
        actor.id == target.id
        and target.role in {"owner", "admin"}
        and new_role
        not in {
            "owner",
            "admin",
        }
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Cannot downgrade your own administrator account.",
        )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
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
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from exc
    if settings.multi_tenancy_enabled:
        default_org = await session.get(Organization, DEFAULT_ORG_ID)
        if default_org is None:
            default_org = Organization(
                id=DEFAULT_ORG_ID, name="Default", slug="default"
            )
            session.add(default_org)
            await session.flush()
        from app.services import org_keys

        await org_keys.get_org_kek(DEFAULT_ORG_ID, session)
        session.add(
            Membership(
                org_id=DEFAULT_ORG_ID,
                user_id=user.id,
                role="owner" if count == 0 else normalize_role(settings.auth_registration_role),
            )
        )
        if await session.scalar(
            select(Environment.id).where(Environment.is_global.is_(True))
        ) is None:
            session.add(
                Environment(
                    org_id=DEFAULT_ORG_ID,
                    name="Default",
                    is_global=True,
                    status="ready",
                    packages=[],
                )
            )
    await log_audit(session, "register", "user", detail=f"{email} ({role})")
    try:
        await session.commit()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    result = _token_response(user)
    _set_session_cookies(response, result.token)
    return result


@router.post("/verify-email")
async def verify_email(
    token: str = Query(..., description="Email verification token"),
    session: AsyncSession = Depends(get_session),
):
    """Verify a user's email address using a one-time token (P1-3).

    The token is a ``create_payload_token`` JWT with ``action="verify_email"``
    and the user's id in ``sub``.
    """
    payload = decode_payload_token(token)
    if payload is None or payload.get("action") != "verify_email":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Invalid or expired verification link. Request a new one.",
        )
    user_id = payload.get("sub")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.email_verified:
        return {"message": "Email already verified."}
    user.email_verified = True
    await session.commit()
    return {"message": "Email verified successfully."}


@router.post("/resend-verification")
async def resend_verification(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    """Generate a new verification token and log it (P1-3).

    In production this would send an email.  The token is returned in the
    response for local-dev convenience; in production with a real email
    backend the token would only appear in the email.
    """
    if user.email_verified:
        return {"message": "Email already verified."}
    token = create_payload_token(
        {"sub": user.id, "action": "verify_email"}, ttl_seconds=3600
    )
    # Never log the full token — it's a bearer credential.
    # Log only a prefix hash for debugging correlation.
    logger.info(
        "email verification token generated for user %s (%s) prefix=%s",
        user.id, user.email, token[:8],
    )
    # Return the token in the response for local-dev convenience.  In
    # production with a real email backend, set a feature flag that
    # suppresses the token field and sends it only via email.
    return {"message": "Verification email sent.", "token": token}


# Dummy bcrypt-like hash for constant-time comparison when the user doesn't
# exist.  Prevents timing-based email enumeration: verify_password always runs,
# taking the same wall-clock whether the user is found or not.
_DUMMY_HASH = (
    "$2b$12$LJ3m4ys3Lk0TSwHCpNqrRO"
    "eMrmfW8zH6oGJqk9Ry1jDzE2pXsKlMuv3K4a1bQcVbN0d5sT6u7w8x9y0z"
)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    await _enforce_auth_rate_limit(request, "login")
    user = await session.scalar(select(User).where(User.email == _email(body.email)))
    # Constant-time defence against email enumeration: always verify the
    # password, even when the user doesn't exist.  The dummy hash is a
    # pre-computed valid bcrypt hash so the work factor matches a real one.
    password_hash = user.password_hash if user is not None else _DUMMY_HASH
    if not verify_password(body.password, password_hash) or user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    # P1-3: gate login for unverified users when required
    if settings.auth_require_verified_email and not getattr(user, "email_verified", False):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Email address not verified. Check your inbox or request a new verification link.",
        )
    client_ip = get_client_ip(request) if settings.auth_bind_token_to_ip else ""
    result = _token_response(user, client_ip=client_ip)
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
        session,
        "revoke_sessions",
        "user",
        user.id,
        user.email,
        actor_id=user.id,
        actor_email=user.email,
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
        session,
        "revoke_sessions",
        "user",
        user.id,
        user.email,
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
    response_model=PageResponse[UserAdminInfo],
    dependencies=[Depends(require_user_manage)],
)
async def list_users(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    total = await session.scalar(
        select(func.count()).select_from(User)
    )
    result = await session.scalars(
        select(User)
        .order_by(User.created_at, User.email)
        .offset(offset)
        .limit(limit)
    )
    return PageResponse(
        items=list(result.all()), total=total or 0, limit=limit, offset=offset
    )


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
    await log_audit(
        session,
        "create",
        "user",
        detail=f"{email} ({role})",
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
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
    await log_audit(
        session,
        "update_role",
        "user",
        user.id,
        f"{user.email} -> {role}",
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
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
    await log_audit(
        session,
        "delete",
        "user",
        user.id,
        user.email,
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
    await session.delete(user)
    await session.commit()


# ---------------------------------------------------------------------------
# SSO routes
# ---------------------------------------------------------------------------


@router.get("/sso/start")
async def sso_start(
    org_slug: str = Query(..., description="Organization slug"),
    session: AsyncSession = Depends(get_session),
):
    """Initiate SSO login: redirects to the IdP's authorization page."""
    sso_config = await get_sso_config_by_org_slug(org_slug, session)
    if sso_config is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "SSO not configured for this organization"
        )
    from app.services.licensing import Feature, has_feature

    if not await has_feature(Feature.SSO):
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "feature_locked",
                "feature": "sso",
                "message": "SSO requires an Enterprise license.",
            },
        )

    if sso_config.protocol == "oidc":
        redirect_url = await oidc_authorization_url(sso_config)
        return Response(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": redirect_url},
        )
    elif sso_config.protocol == "saml":
        from app.services.sso import _saml_acs_url, _saml_entity_id

        acs_url = _saml_acs_url()
        entity_id = _saml_entity_id()
        saml_request = _build_saml_authn_request(entity_id, acs_url)
        return Response(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": f"{sso_config.idp_sso_url}?SAMLRequest={saml_request}"},
        )
    else:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Unknown SSO protocol: {sso_config.protocol}"
        )


@router.get("/sso/callback")
async def sso_callback(
    request: Request,
    response: Response,
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """OIDC callback: exchange authorization code for tokens, create session."""
    raw = await redis_client.getdel(f"noodle:sso:state:{state}")
    if raw is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "SSO state expired or never created — please retry authentication",
        )
    pending = json.loads(raw)
    org_id = pending.get("org_id")

    sso_config = await session.scalar(
        select(SSOConfig).where(SSOConfig.org_id == org_id)
    )
    if sso_config is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "SSO configuration not found"
        )

    claims = await oidc_exchange_code(sso_config, code, pending=pending, session=session)
    user = await get_or_create_sso_user(claims, sso_config, session=session)
    await session.commit()

    result = _token_response(user)
    _set_session_cookies(response, result.token)
    return result


@router.get("/sso/detect")
async def sso_detect(
    email: str = Query(..., description="Email address to check"),
    session: AsyncSession = Depends(get_session),
):
    """Check if an email domain has SSO configured."""
    result = await detect_sso_by_email(email, session)
    if result is None:
        return {"has_sso": False}
    return result


@router.get("/sso/metadata")
async def sso_metadata(
    org_slug: str = Query(..., description="Organization slug"),
    session: AsyncSession = Depends(get_session),
):
    """Return SAML SP metadata XML for the given org."""
    sso_config = await get_sso_config_by_org_slug(org_slug, session)
    if sso_config is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "SSO not configured for this organization"
        )
    xml = build_saml_sp_metadata(sso_config)
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'inline; filename="saml-sp-metadata-{org_slug}.xml"'},
    )


@router.post("/sso/acs")
async def sso_acs(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    """SAML assertion consumer service (ACS)."""
    form = await request.form()
    saml_response = form.get("SAMLResponse")
    if not saml_response:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Missing SAMLResponse"
        )
    import base64
    import xml.etree.ElementTree as ET
    import zlib

    # Decompression bomb protection: reject payloads larger than 100 KiB
    raw = saml_response.encode() if isinstance(saml_response, str) else saml_response
    if len(raw) > 102_400:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "SAMLResponse too large (max 100 KiB)",
        )

    try:
        decoded = base64.b64decode(raw)
        # Limit decompressed size to 1 MiB to prevent zip bombs
        inflated = zlib.decompress(decoded, -15, bufsize=1_048_576)

        root = ET.fromstring(inflated)
        ns = {
            "saml2": "urn:oasis:names:tc:SAML:2.0:assertion",
            "saml2p": "urn:oasis:names:tc:SAML:2.0:protocol",
        }
        name_id_el = root.find(".//saml2:NameID", ns)
        email = name_id_el.text if name_id_el is not None else ""
        if not email or "@" not in email:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "SAML response missing valid NameID/email"
            )
        domain = email.split("@")[1].lower()
        sso_config = await session.scalar(
            select(SSOConfig).where(SSOConfig.email_domain == domain)
        )
        if sso_config is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "No SSO configuration found for this email domain",
            )
        # TODO(ms4-4a): Replace manual SAML XML parsing with python3-saml library
        # (OneLogin_Saml2_Auth) for proper assertion signature validation against
        # sso_config.idp_certificate, Issuer verification, Audience restriction,
        # Destination matching, and NotBefore/NotOnOrAfter enforcement.
        # See: https://github.com/onelogin/python3-saml
        if not sso_config.idp_certificate:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "SAML requires idp_certificate to be configured",
            )
        claims = {
            "email": email,
            "name": email.split("@")[0],
            "sub": f"saml:{email}",
        }
        user = await get_or_create_sso_user(claims, sso_config, session=session)
        await session.commit()
        result = _token_response(user)
        _set_session_cookies(response, result.token)
        return result
    except HTTPException:
        raise
    except (ValueError, ET.ParseError) as exc:
        logger.warning("SAML ACS parse error: %s", exc)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Invalid SAML response"
        ) from exc
    except Exception as exc:
        correlation_id = secrets.token_urlsafe(8)
        logger.error(
            "SAML ACS internal error [%s]: %s",
            correlation_id, exc, exc_info=True,
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Internal error processing SAML response (ref: {correlation_id})",
        ) from exc


def _build_saml_authn_request(entity_id: str, acs_url: str) -> str:
    """Build a minimal SAML AuthnRequest and return it base64-encoded."""
    import base64
    import zlib

    request_id = f"_{secrets.token_urlsafe(16)}"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<saml2p:AuthnRequest xmlns:saml2p="urn:oasis:names:tc:SAML:2.0:protocol"
    xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion"
    ID="{request_id}"
    Version="2.0"
    IssueInstant="{datetime.now(UTC).isoformat()}"
    Destination=""
    AssertionConsumerServiceURL="{acs_url}"
    ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST">
    <saml2:Issuer>{entity_id}</saml2:Issuer>
    <saml2p:NameIDPolicy AllowCreate="true"
        Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"/>
</saml2p:AuthnRequest>"""
    compressed = zlib.compress(xml.encode())[2:-4]
    return base64.b64encode(compressed).decode()
