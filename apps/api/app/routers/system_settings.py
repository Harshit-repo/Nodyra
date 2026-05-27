"""Workspace-wide runtime settings (singleton row), editable by admins."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import SystemSetting
from app.schemas import SystemSettingsInfo, SystemSettingsUpdate
from app.security import require_permission
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
):
    row = await _load(session)
    apply_updates(row, body.model_dump(exclude_unset=True))
    await log_audit(
        session,
        "update",
        "system_settings",
        row.id,
        "workspace settings updated",
    )
    await session.commit()
    await session.refresh(row)
    invalidate_live_settings_cache()
    return row
