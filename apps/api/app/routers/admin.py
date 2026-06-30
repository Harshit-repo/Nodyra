"""Admin routes: custom role CRUD, audit log viewer, KMS health, and SSO config.

Authorization:
- Custom role CRUD requires ``admin:users`` (owner/admin only).
- Audit log viewer requires ``audit:read``.
- The ``Feature.ADVANCED_RBAC`` gate protects custom role creation/update.
- The ``Feature.AUDIT_LOGS`` gate protects the audit log endpoint.
- The ``Feature.EXTERNAL_KMS`` gate protects the KMS health endpoint.
- SSO config management requires ``user:manage`` via ``require_admin``.
"""

import csv
import io
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import AuditEvent, CustomRole, Organization, SSOConfig, User
from app.schemas import (
    AuditEventInfo,
    CustomRoleCreate,
    CustomRoleInfo,
    CustomRoleUpdate,
    PageResponse,
)
from app.security import (
    CUSTOM_ROLE_PERMISSION_REGISTRY,
    current_user,
    require_instance_permission,
    require_permission,
    validate_custom_role_permissions,
)
from app.services.audit import log_audit
from app.services.licensing import Feature, require_feature
from app.services.org_keys import get_org_kek
from app.tenancy import active_org_id

router = APIRouter(tags=["admin"])


# ---------------------------------------------------------------------------
# Custom role CRUD
# ---------------------------------------------------------------------------


@router.get(
    "/admin/custom-roles",
    response_model=list[CustomRoleInfo],
    dependencies=[
        Depends(require_permission("admin:users")),
        Depends(require_feature(Feature.ADVANCED_RBAC)),
    ],
)
async def list_custom_roles(
    session: AsyncSession = Depends(get_session),
):
    org_id = active_org_id() or "default"
    rows = (
        await session.scalars(
            select(CustomRole)
            .where(CustomRole.org_id == org_id)
            .order_by(CustomRole.created_at.desc())
        )
    ).all()
    return rows


@router.get(
    "/admin/custom-roles/{role_id}",
    response_model=CustomRoleInfo,
    dependencies=[
        Depends(require_permission("admin:users")),
        Depends(require_feature(Feature.ADVANCED_RBAC)),
    ],
)
async def get_custom_role(
    role_id: str,
    session: AsyncSession = Depends(get_session),
):
    org_id = active_org_id() or "default"
    role = await session.scalar(
        select(CustomRole).where(
            CustomRole.id == role_id, CustomRole.org_id == org_id
        )
    )
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Custom role not found")
    return role


@router.post(
    "/admin/custom-roles",
    response_model=CustomRoleInfo,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_permission("admin:users")),
        Depends(require_feature(Feature.ADVANCED_RBAC)),
    ],
)
async def create_custom_role(
    payload: CustomRoleCreate,
    session: AsyncSession = Depends(get_session),
):
    org_id = active_org_id() or "default"
    validate_custom_role_permissions(payload.permissions)

    # Check for duplicate name within org
    existing = await session.scalar(
        select(CustomRole).where(
            CustomRole.org_id == org_id, CustomRole.name == payload.name
        )
    )
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A custom role named '{payload.name}' already exists in this organization.",
        )

    role = CustomRole(
        org_id=org_id,
        name=payload.name,
        permissions=payload.permissions,
    )
    session.add(role)
    await session.commit()
    await session.refresh(role)
    return role


@router.patch(
    "/admin/custom-roles/{role_id}",
    response_model=CustomRoleInfo,
    dependencies=[
        Depends(require_permission("admin:users")),
        Depends(require_feature(Feature.ADVANCED_RBAC)),
    ],
)
async def update_custom_role(
    role_id: str,
    payload: CustomRoleUpdate,
    session: AsyncSession = Depends(get_session),
):
    org_id = active_org_id() or "default"
    role = await session.scalar(
        select(CustomRole).where(
            CustomRole.id == role_id, CustomRole.org_id == org_id
        )
    )
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Custom role not found")

    if payload.name is not None:
        # Check for duplicate name (excluding self)
        duplicate = await session.scalar(
            select(CustomRole).where(
                CustomRole.org_id == org_id,
                CustomRole.name == payload.name,
                CustomRole.id != role_id,
            )
        )
        if duplicate:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"A custom role named '{payload.name}' already exists in this organization.",
            )
        role.name = payload.name

    if payload.permissions is not None:
        validate_custom_role_permissions(payload.permissions)
        role.permissions = payload.permissions

    await session.commit()
    await session.refresh(role)
    return role


@router.delete(
    "/admin/custom-roles/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[
        Depends(require_permission("admin:users")),
        Depends(require_feature(Feature.ADVANCED_RBAC)),
    ],
)
async def delete_custom_role(
    role_id: str,
    session: AsyncSession = Depends(get_session),
):
    org_id = active_org_id() or "default"
    role = await session.scalar(
        select(CustomRole).where(
            CustomRole.id == role_id, CustomRole.org_id == org_id
        )
    )
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Custom role not found")
    # ON DELETE SET NULL on memberships.custom_role_id handles the cascade
    await session.delete(role)
    await session.commit()


# ---------------------------------------------------------------------------
# Custom role permission registry (read-only, for UI checkboxes)
# ---------------------------------------------------------------------------


@router.get(
    "/admin/permissions",
    dependencies=[Depends(require_permission("admin:users"))],
)
async def list_permissions():
    """Return the set of all assignable custom role permissions."""
    return sorted(CUSTOM_ROLE_PERMISSION_REGISTRY)


# ---------------------------------------------------------------------------
# Audit log viewer
# ---------------------------------------------------------------------------


@router.get(
    "/admin/audit-logs",
    response_model=PageResponse[AuditEventInfo],
    dependencies=[
        Depends(require_permission("audit:read")),
        Depends(require_feature(Feature.AUDIT_LOGS)),
    ],
)
async def list_audit_logs(
    user_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    org_id = active_org_id() or "default"
    stmt = select(AuditEvent).where(AuditEvent.org_id == org_id)
    count_stmt = select(func.count()).select_from(AuditEvent).where(AuditEvent.org_id == org_id)

    if user_id is not None:
        stmt = stmt.where(AuditEvent.actor_id == user_id)
        count_stmt = count_stmt.where(AuditEvent.actor_id == user_id)
    if action is not None:
        stmt = stmt.where(AuditEvent.action == action)
        count_stmt = count_stmt.where(AuditEvent.action == action)
    if resource_type is not None:
        stmt = stmt.where(AuditEvent.target_type == resource_type)
        count_stmt = count_stmt.where(AuditEvent.target_type == resource_type)
    if from_ is not None:
        stmt = stmt.where(AuditEvent.created_at >= from_)
        count_stmt = count_stmt.where(AuditEvent.created_at >= from_)
    if to is not None:
        stmt = stmt.where(AuditEvent.created_at <= to)
        count_stmt = count_stmt.where(AuditEvent.created_at <= to)

    stmt = stmt.order_by(AuditEvent.created_at.desc())
    total = await session.scalar(count_stmt)
    result = await session.scalars(stmt.offset(offset).limit(limit))
    return PageResponse(
        items=list(result.all()),
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/admin/kms/health",
    dependencies=[
        Depends(require_permission("admin:users")),
        Depends(require_feature(Feature.EXTERNAL_KMS)),
    ],
)
async def kms_health():
    """Probe the configured KMS provider.

    Gated behind ``Feature.EXTERNAL_KMS`` (Enterprise).  Returns the provider
    status and provider name.  ``status`` is ``"ok"`` when ``health_check()``
    returns ``True``; ``"unhealthy"`` otherwise (caller should check logs).
    """
    from app.config import settings
    from app.services.kms import get_kms_provider

    provider = get_kms_provider()
    healthy = await provider.health_check()
    return {
        "status": "ok" if healthy else "unhealthy",
        "provider": settings.kms_provider,
    }


@router.get(
    "/admin/audit-logs/export",
    dependencies=[
        Depends(require_permission("audit:read")),
        Depends(require_feature(Feature.AUDIT_LOGS)),
    ],
)
async def export_audit_logs_csv(
    user_id: str | None = Query(default=None),
    action: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Export audit logs as CSV (all matching rows, no pagination)."""
    org_id = active_org_id() or "default"
    stmt = (
        select(AuditEvent)
        .where(AuditEvent.org_id == org_id)
        .order_by(AuditEvent.created_at.desc())
    )
    if user_id is not None:
        stmt = stmt.where(AuditEvent.actor_id == user_id)
    if action is not None:
        stmt = stmt.where(AuditEvent.action == action)
    if resource_type is not None:
        stmt = stmt.where(AuditEvent.target_type == resource_type)
    if from_ is not None:
        stmt = stmt.where(AuditEvent.created_at >= from_)
    if to is not None:
        stmt = stmt.where(AuditEvent.created_at <= to)

    _CSV_EXPORT_MAX_ROWS = 10_000
    rows = (await session.scalars(stmt.limit(_CSV_EXPORT_MAX_ROWS + 1))).all()

    truncated = len(rows) > _CSV_EXPORT_MAX_ROWS
    if truncated:
        rows = rows[:_CSV_EXPORT_MAX_ROWS]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "created_at", "action", "target_type", "target_id",
        "detail", "actor_id", "actor_email", "session_id", "actor_type",
    ])
    for r in rows:
        writer.writerow([
            r.id, r.created_at.isoformat() if r.created_at else "",
            r.action, r.target_type, r.target_id,
            r.detail, r.actor_id or "", r.actor_email or "",
            r.session_id or "", r.actor_type,
        ])

    csv_bytes = output.getvalue().encode("utf-8")
    headers = {"Content-Disposition": "attachment; filename=audit-log.csv"}
    if truncated:
        headers["X-Audit-Export-Truncated"] = "true"
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# SSO config management
# ---------------------------------------------------------------------------

require_admin = require_instance_permission("user:manage")


@router.get("/admin/sso")
async def get_sso_config(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(current_user),
    _: None = Depends(require_admin),
    __: None = Depends(require_feature(Feature.SSO)),
):
    """Get the SSO config for the current user's org."""
    from app.tenancy import DEFAULT_ORG_ID

    org_id = active_org_id() or DEFAULT_ORG_ID

    config = await session.scalar(
        select(SSOConfig).where(SSOConfig.org_id == org_id)
    )
    if config is None:
        return None

    org = await session.get(Organization, org_id)
    return {
        "id": config.id,
        "org_id": config.org_id,
        "org_slug": org.slug if org else "",
        "protocol": config.protocol,
        "client_id": config.client_id,
        "client_secret": _mask_secret(config.client_secret),
        "discovery_url": config.discovery_url,
        "idp_entity_id": config.idp_entity_id,
        "idp_sso_url": config.idp_sso_url,
        "idp_certificate": _mask_secret(config.idp_certificate),
        "email_domain": config.email_domain,
        "attribute_map": config.attribute_map,
        "jit_provisioning": config.jit_provisioning,
        "created_at": config.created_at.isoformat() if config.created_at else None,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


@router.post("/admin/sso", status_code=status.HTTP_200_OK)
async def upsert_sso_config(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(current_user),
    _: None = Depends(require_admin),
):
    """Create or update SSO configuration for the org."""
    from app.tenancy import DEFAULT_ORG_ID

    org_id = active_org_id() or DEFAULT_ORG_ID

    from app.services.licensing import has_feature

    if not await has_feature(Feature.SSO):
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "feature_locked",
                "feature": "sso",
                "message": "SSO requires an Enterprise license.",
            },
        )

    existing = await session.scalar(
        select(SSOConfig).where(SSOConfig.org_id == org_id)
    )

    protocol = body.get("protocol", "oidc")
    if protocol not in ("oidc", "saml"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Protocol must be 'oidc' or 'saml'",
        )

    # Validate SSO URLs before storing them
    from noodle_nodes.http_security import assert_public_http_url

    discovery_url = body.get("discovery_url")
    if discovery_url:
        assert_public_http_url(discovery_url, context="SSO discovery URL")
    idp_sso_url = body.get("idp_sso_url")
    if idp_sso_url:
        assert_public_http_url(idp_sso_url, context="SSO IdP SSO URL")

    # Encrypt client_secret if provided (new or changed)
    client_secret_raw = body.get("client_secret")
    client_secret_stored = existing.client_secret if existing else None
    if client_secret_raw:
        org_kek = await get_org_kek(org_id, session)
        if org_kek is None:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Cannot encrypt client_secret: org KEK not available",
            )
        client_secret_stored = Fernet(org_kek).encrypt(
            client_secret_raw.encode()
        ).decode()

    vals = {
        "protocol": protocol,
        "client_id": body.get("client_id"),
        "client_secret": client_secret_stored,
        "discovery_url": body.get("discovery_url"),
        "idp_entity_id": body.get("idp_entity_id"),
        "idp_sso_url": body.get("idp_sso_url"),
        "idp_certificate": body.get("idp_certificate"),
        "email_domain": body.get("email_domain"),
        "attribute_map": body.get("attribute_map", {}),
        "jit_provisioning": body.get("jit_provisioning", True),
    }

    if existing:
        for key, value in vals.items():
            setattr(existing, key, value)
        await log_audit(
            session,
            "update",
            "sso_config",
            existing.id,
            f"protocol={protocol}",
            actor_id=current_user.id,
            actor_email=current_user.email,
        )
    else:
        config = SSOConfig(org_id=org_id, **vals)
        session.add(config)
        await session.flush()
        await log_audit(
            session,
            "create",
            "sso_config",
            config.id,
            f"protocol={protocol}",
            actor_id=current_user.id,
            actor_email=current_user.email,
        )

    await session.commit()
    return {"status": "ok", "protocol": protocol, "org_id": org_id}


@router.delete("/admin/sso", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sso_config(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(current_user),
    _: None = Depends(require_admin),
    __: None = Depends(require_feature(Feature.SSO)),
):
    """Remove the SSO configuration for the org."""
    from app.tenancy import DEFAULT_ORG_ID

    org_id = active_org_id() or DEFAULT_ORG_ID

    config = await session.scalar(
        select(SSOConfig).where(SSOConfig.org_id == org_id)
    )
    if config is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "SSO not configured for this organization"
        )

    await log_audit(
        session,
        "delete",
        "sso_config",
        config.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
    )
    await session.delete(config)
    await session.commit()


@router.post("/admin/sso/test")
async def test_sso_connection(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(current_user),
    _: None = Depends(require_admin),
    __: None = Depends(require_feature(Feature.SSO)),
):
    """Test SSO connection by checking provider reachability."""
    protocol = body.get("protocol", "oidc")

    if protocol == "oidc":
        discovery_url = body.get("discovery_url")
        if not discovery_url:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "discovery_url is required for OIDC"
            )
        from noodle_nodes.http_security import assert_public_http_url

        assert_public_http_url(discovery_url, context="SSO test")
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                resp = await client.get(discovery_url)
                resp.raise_for_status()
                doc = resp.json()
            endpoints_found = []
            for key in (
                "authorization_endpoint",
                "token_endpoint",
                "jwks_uri",
                "userinfo_endpoint",
            ):
                if key in doc:
                    endpoints_found.append(key)
            return {
                "status": "ok",
                "detail": f"Discovery document fetched successfully. Found endpoints: {', '.join(endpoints_found)}",
            }
        except httpx.HTTPError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Cannot reach IdP: {exc}",
            ) from exc

    elif protocol == "saml":
        idp_sso_url = body.get("idp_sso_url")
        if not idp_sso_url:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "idp_sso_url is required for SAML",
            )
        from noodle_nodes.http_security import assert_public_http_url

        assert_public_http_url(idp_sso_url, context="SSO test")
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                resp = await client.get(idp_sso_url)
                resp.raise_for_status()
            return {"status": "ok", "detail": "IdP SSO URL reachable"}
        except httpx.HTTPError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Cannot reach IdP SSO URL: {exc}",
            ) from exc
    else:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Unknown protocol: {protocol}"
        )


def _mask_secret(value: str | None) -> str | None:
    """Mask a secret for display (show first 4 + last 4 chars)."""
    if not value:
        return None
    if len(value) <= 8:
        return "********"
    return value[:4] + "****" + value[-4:]
