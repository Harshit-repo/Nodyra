"""Workspace-wide runtime settings (singleton row), editable by admins."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import SystemSetting, User
from app.schemas import (
    LicenseApply,
    LicenseInfo,
    SystemSettingsInfo,
    SystemSettingsUpdate,
)
from app.security import optional_current_user, require_permission
from app.services import licensing
from app.services.audit import log_audit
from app.services.live_settings import (
    _SINGLETON_ID,
    apply_updates,
    invalidate_live_settings_cache,
)

router = APIRouter(prefix="/system-settings", tags=["system-settings"])

require_read = require_permission("workflow:run")  # any signed-in role can read
require_write = require_permission("environment:write")  # admin


async def _load(session: AsyncSession) -> SystemSetting:
    row = await session.get(SystemSetting, _SINGLETON_ID)
    if row is None:
        row = SystemSetting(id=_SINGLETON_ID)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


@router.get(
    "",
    response_model=SystemSettingsInfo,
    dependencies=[Depends(require_read)],
)
async def get_system_settings(session: AsyncSession = Depends(get_session)):
    return await _load(session)


@router.put(
    "",
    response_model=SystemSettingsInfo,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_write)],
)
async def update_system_settings(
    body: SystemSettingsUpdate,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
):
    row = await _load(session)
    apply_updates(row, body.model_dump(exclude_unset=True))
    await log_audit(
        session,
        "update",
        "system_settings",
        row.id,
        "workspace settings updated",
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
    await session.commit()
    await session.refresh(row)
    invalidate_live_settings_cache()
    return row


def _license_info(lic) -> LicenseInfo:
    return LicenseInfo(
        edition=lic.edition.value,
        customer=lic.customer,
        expires_at=lic.expires_at,
        entitlements=[f.value for f in lic.features],
        limits={
            "environments": lic.limits.environments,
            "runners": lic.limits.runners,
            "deployments": lic.limits.deployments,
            "seats": lic.limits.seats,
        },
        notice=lic.notice,
    )


@router.get(
    "/license",
    response_model=LicenseInfo,
    dependencies=[Depends(require_read)],
)
async def get_license():
    return _license_info(await licensing.current_license())


@router.put(
    "/license",
    response_model=LicenseInfo,
    dependencies=[Depends(require_write)],
)
async def apply_license(
    body: LicenseApply,
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
):
    row = await _load(session)
    row.license_key = body.license_key
    await log_audit(
        session, "update", "license", row.id, "license key applied",
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
    await session.commit()
    licensing.invalidate_license_cache()
    return _license_info(await licensing.current_license())


@router.delete(
    "/license",
    response_model=LicenseInfo,
    dependencies=[Depends(require_write)],
)
async def remove_license(
    session: AsyncSession = Depends(get_session),
    actor: User | None = Depends(optional_current_user),
):
    row = await _load(session)
    row.license_key = None
    await log_audit(
        session, "delete", "license", row.id, "license key removed",
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
    await session.commit()
    licensing.invalidate_license_cache()
    return _license_info(await licensing.current_license())
