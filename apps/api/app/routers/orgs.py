"""Organization CRUD + membership management (multi-tenancy Phase B).

Authorization here is membership-based and inline (not the global
``require_permission`` map): creating an org needs only an authenticated
user; everything else checks the actor's role *within the target org*.
Owner-guard semantics mirror the instance-level user management in
``routers/auth.py`` (last owner can't be demoted/removed; only owners manage
owner roles).
"""

import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import (
    Environment,
    Membership,
    Organization,
    OrgSettings,
    RunMeter,
    User,
)
from app.schemas import (
    OrgCreate,
    OrgInfo,
    OrgMemberAdd,
    OrgMemberInfo,
    OrgMemberUpdate,
    OrgSettingsInfo,
    OrgSettingsUpdate,
    OrgUpdate,
    OrgUsageDay,
)
from app.security import current_user, normalize_role
from app.services import org_keys
from app.services.audit import log_audit
from app.services.org_limits import effective_limits, invalidate_limits_cache
from app.tenancy import DEFAULT_ORG_ID, active_org_id

router = APIRouter(tags=["orgs"])

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")


def _slugify(value: str) -> str:
    slug = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")
    return slug[:80]


def _active_org_or_400() -> str:
    org_id = active_org_id()
    if org_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Organization management requires multi_tenancy_enabled.",
        )
    return org_id


async def _role_in(session: AsyncSession, user_id: str, org_id: str) -> str | None:
    return await session.scalar(
        select(Membership.role)
        .where(Membership.org_id == org_id, Membership.user_id == user_id)
        .execution_options(skip_org_filter=True)
    )


async def _require_org_role(session: AsyncSession, user: User, org_id: str, minimum: str) -> str:
    from app.security import role_allows

    role = await _role_in(session, user.id, org_id)
    if role is None or not role_allows(role, minimum):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Requires {minimum} role in this organization.",
        )
    return role


async def _org_owner_count(session: AsyncSession, org_id: str) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.org_id == org_id, Membership.role == "owner")
            .execution_options(skip_org_filter=True)
        )
        or 0
    )


@router.get("/me/orgs", response_model=list[OrgInfo])
async def list_my_orgs(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[OrgInfo]:
    """Every org the caller belongs to, with their role — feeds the org
    switcher. Crosses org boundaries by design, hence the filter escape."""
    rows = (
        await session.execute(
            select(Organization, Membership.role)
            .join(Membership, Membership.org_id == Organization.id)
            .where(Membership.user_id == user.id)
            .order_by(Organization.created_at)
            .execution_options(skip_org_filter=True)
        )
    ).all()
    return [
        OrgInfo(id=org.id, name=org.name, slug=org.slug, status=org.status, role=role)
        for org, role in rows
    ]


@router.post("/orgs", response_model=OrgInfo, status_code=status.HTTP_201_CREATED)
async def create_org(
    body: OrgCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> OrgInfo:
    slug = body.slug.strip().lower() or _slugify(body.name)
    if not _SLUG_RE.match(slug):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Slug must be 2-80 lowercase letters, digits, or hyphens.",
        )
    if await session.scalar(select(Organization.id).where(Organization.slug == slug)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Slug is already taken.")

    org = Organization(name=body.name.strip(), slug=slug)
    session.add(org)
    await session.flush()
    session.add(Membership(org_id=org.id, user_id=user.id, role="owner"))
    # X2: each org gets its own default environment so warm worker processes
    # (keyed by environment id) are never shared across orgs.
    session.add(
        Environment(
            org_id=org.id,
            name="Default",
            is_global=True,
            status="ready",
            packages=[],
        )
    )
    # Phase E: mint the org's KEK up front so the first credential write
    # never races two replicas through the lazy-mint path.
    await org_keys.get_org_kek(org.id, session)
    await log_audit(
        session,
        "create",
        "organization",
        org.id,
        f"{org.name} ({slug})",
        actor_id=user.id,
        actor_email=user.email,
    )
    try:
        await session.commit()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Slug is already taken.")
    return OrgInfo(id=org.id, name=org.name, slug=org.slug, status=org.status, role="owner")


@router.patch("/orgs/{org_id}", response_model=OrgInfo)
async def update_org(
    org_id: str,
    body: OrgUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> OrgInfo:
    role = await _require_org_role(session, user, org_id, "admin")
    org = await session.get(Organization, org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    if body.name is not None:
        org.name = body.name.strip()
    if body.execution_isolation is not None:
        if body.execution_isolation not in ("shared", "dedicated_pool"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "execution_isolation must be 'shared' or 'dedicated_pool'.",
            )
        if role != "owner":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only an owner can change execution isolation.",
            )
        org.execution_isolation = body.execution_isolation
    await log_audit(
        session,
        "update",
        "organization",
        org.id,
        org.name,
        actor_id=user.id,
        actor_email=user.email,
    )
    await session.commit()
    return OrgInfo(id=org.id, name=org.name, slug=org.slug, status=org.status, role=role)


@router.delete("/orgs/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org(
    org_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    if org_id == DEFAULT_ORG_ID:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "The default organization cannot be deleted.",
        )
    await _require_org_role(session, user, org_id, "owner")
    org = await session.get(Organization, org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    await log_audit(
        session,
        "delete",
        "organization",
        org.id,
        org.name,
        actor_id=user.id,
        actor_email=user.email,
    )
    # FK ondelete=CASCADE wipes the org's workflows, runs, credentials, etc.
    await session.delete(org)
    await session.commit()


_QUOTA_FIELDS = (
    "max_concurrent_runs",
    "executions_per_day",
    "max_map_width",
    "max_loop_iterations",
    "max_inflight_subworkflows",
    "storage_quota_bytes",
)


async def _settings_info(session: AsyncSession, org_id: str) -> OrgSettingsInfo:
    limits = await effective_limits(session, org_id)
    row = await session.scalar(
        select(OrgSettings)
        .where(OrgSettings.org_id == org_id)
        .execution_options(skip_org_filter=True)
    )
    overridden = [
        field for field in _QUOTA_FIELDS if row is not None and getattr(row, field) is not None
    ]
    return OrgSettingsInfo(
        org_id=org_id,
        overridden=overridden,
        **{field: getattr(limits, field) for field in _QUOTA_FIELDS},
    )


@router.get("/orgs/{org_id}/settings", response_model=OrgSettingsInfo)
async def get_org_settings(
    org_id: str,
    actor: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> OrgSettingsInfo:
    await _require_org_role(session, actor, org_id, "admin")
    return await _settings_info(session, org_id)


@router.put("/orgs/{org_id}/settings", response_model=OrgSettingsInfo)
async def update_org_settings(
    org_id: str,
    body: OrgSettingsUpdate,
    actor: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> OrgSettingsInfo:
    role = await _require_org_role(session, actor, org_id, "admin")
    if role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an owner can change org quotas.")
    row = await session.scalar(
        select(OrgSettings)
        .where(OrgSettings.org_id == org_id)
        .execution_options(skip_org_filter=True)
    )
    if row is None:
        row = OrgSettings(org_id=org_id)
        session.add(row)
    for field in _QUOTA_FIELDS:
        value = getattr(body, field)
        if value is None:
            continue
        # -1 clears the override back to "inherit instance default".
        setattr(row, field, None if value == -1 else value)
    await log_audit(
        session,
        "update",
        "org_settings",
        org_id,
        ", ".join(f for f in _QUOTA_FIELDS if getattr(body, f) is not None),
        actor_id=actor.id,
        actor_email=actor.email,
    )
    await session.commit()
    invalidate_limits_cache()
    return await _settings_info(session, org_id)


@router.get("/orgs/{org_id}/usage", response_model=list[OrgUsageDay])
async def get_org_usage(
    org_id: str,
    days: int = 30,
    actor: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[OrgUsageDay]:
    """Daily run/compute usage for the org (billing + abuse visibility)."""
    await _require_org_role(session, actor, org_id, "admin")
    rows = (
        await session.scalars(
            select(RunMeter)
            .where(RunMeter.org_id == org_id)
            .order_by(RunMeter.day.desc())
            .limit(max(1, min(days, 365)))
            .execution_options(skip_org_filter=True)
        )
    ).all()
    return [OrgUsageDay.model_validate(row) for row in rows]


@router.get("/orgs/current/members", response_model=list[OrgMemberInfo])
async def list_members(
    actor: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[OrgMemberInfo]:
    org_id = _active_org_or_400()
    # resolve_org already refuses non-members, but that gate passes anonymous
    # callers for the default org (auth_required=false installs) — an email
    # enumeration hole. Require an authenticated member explicitly, matching
    # the add/update/remove siblings.
    await _require_org_role(session, actor, org_id, "viewer")
    rows = (
        await session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id == org_id)
            .order_by(Membership.created_at)
            .execution_options(skip_org_filter=True)
        )
    ).all()
    return [
        OrgMemberInfo(user_id=user.id, email=user.email, name=user.name, role=member.role)
        for member, user in rows
    ]


@router.post(
    "/orgs/current/members",
    response_model=OrgMemberInfo,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    body: OrgMemberAdd,
    actor: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> OrgMemberInfo:
    org_id = _active_org_or_400()
    actor_role = await _require_org_role(session, actor, org_id, "admin")
    role = normalize_role(body.role)
    if role == "owner" and actor_role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an owner can add another owner.")
    user = await session.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No account with that email. Create the user first.",
        )
    if await _role_in(session, user.id, org_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already a member.")
    session.add(Membership(org_id=org_id, user_id=user.id, role=role))
    await log_audit(
        session,
        "member_add",
        "organization",
        org_id,
        f"{user.email} ({role})",
        actor_id=actor.id,
        actor_email=actor.email,
    )
    await session.commit()
    return OrgMemberInfo(user_id=user.id, email=user.email, name=user.name, role=role)


@router.patch("/orgs/current/members/{user_id}", response_model=OrgMemberInfo)
async def update_member(
    user_id: str,
    body: OrgMemberUpdate,
    actor: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> OrgMemberInfo:
    org_id = _active_org_or_400()
    actor_role = await _require_org_role(session, actor, org_id, "admin")
    role = normalize_role(body.role)
    member = await session.scalar(
        select(Membership)
        .where(Membership.org_id == org_id, Membership.user_id == user_id)
        .execution_options(skip_org_filter=True)
    )
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a member.")
    if (member.role == "owner" or role == "owner") and actor_role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an owner can manage owner roles.")
    if member.role == "owner" and role != "owner" and await _org_owner_count(session, org_id) <= 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Cannot demote the organization's last owner.",
        )
    member.role = role
    user = await session.get(User, user_id)
    await log_audit(
        session,
        "member_role",
        "organization",
        org_id,
        f"{user.email if user else user_id} -> {role}",
        actor_id=actor.id,
        actor_email=actor.email,
    )
    await session.commit()
    return OrgMemberInfo(
        user_id=user_id,
        email=user.email if user else "",
        name=user.name if user else "",
        role=role,
    )


@router.delete("/orgs/current/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    user_id: str,
    actor: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    org_id = _active_org_or_400()
    actor_role = await _require_org_role(session, actor, org_id, "admin")
    member = await session.scalar(
        select(Membership)
        .where(Membership.org_id == org_id, Membership.user_id == user_id)
        .execution_options(skip_org_filter=True)
    )
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a member.")
    if member.role == "owner":
        if actor_role != "owner":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an owner can remove an owner.")
        if await _org_owner_count(session, org_id) <= 1:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Cannot remove the organization's last owner.",
            )
    user = await session.get(User, user_id)
    await log_audit(
        session,
        "member_remove",
        "organization",
        org_id,
        user.email if user else user_id,
        actor_id=actor.id,
        actor_email=actor.email,
    )
    await session.delete(member)
    await session.commit()
