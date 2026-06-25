from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Folder, Workflow
from app.schemas import FolderCreate, FolderInfo, FolderRename
from app.security import require_permission

router = APIRouter(prefix="/folders", tags=["folders"])


async def _load(session: AsyncSession, folder_id: str) -> Folder:
    # The do_orm_execute hook in tenancy.py appends org_id filtering to every
    # Folder SELECT, so a cross-tenant folder_id returns None → 404.
    folder = await session.scalar(select(Folder).where(Folder.id == folder_id))
    if folder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
    return folder


async def _count_workflows(session: AsyncSession, folder_id: str) -> int:
    return await session.scalar(
        select(func.count()).select_from(Workflow).where(Workflow.folder_id == folder_id)
    ) or 0


@router.get("", response_model=list[FolderInfo])
async def list_folders(session: AsyncSession = Depends(get_session)):
    folders = (
        await session.scalars(select(Folder).order_by(Folder.name))
    ).all()
    if not folders:
        return []
    counts_result = await session.execute(
        select(Workflow.folder_id, func.count().label("n"))
        .where(Workflow.folder_id.in_([f.id for f in folders]))
        .group_by(Workflow.folder_id)
    )
    counts = {row.folder_id: row.n for row in counts_result}
    return [
        FolderInfo(
            id=f.id,
            name=f.name,
            color=f.color,
            workflow_count=counts.get(f.id, 0),
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in folders
    ]


@router.post(
    "",
    response_model=FolderInfo,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def create_folder(
    body: FolderCreate, session: AsyncSession = Depends(get_session)
):
    folder = Folder(name=body.name, color=body.color)
    session.add(folder)
    await session.commit()
    await session.refresh(folder)
    return FolderInfo(
        id=folder.id,
        name=folder.name,
        color=folder.color,
        workflow_count=0,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


@router.put(
    "/{folder_id}",
    response_model=FolderInfo,
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def rename_folder(
    folder_id: str,
    body: FolderRename,
    session: AsyncSession = Depends(get_session),
):
    folder = await _load(session, folder_id)
    if body.name is not None:
        folder.name = body.name
    if "color" in body.model_fields_set:
        folder.color = body.color
    await session.commit()
    await session.refresh(folder)
    return FolderInfo(
        id=folder.id,
        name=folder.name,
        color=folder.color,
        workflow_count=await _count_workflows(session, folder_id),
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


@router.delete(
    "/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def delete_folder(
    folder_id: str, session: AsyncSession = Depends(get_session)
):
    folder = await _load(session, folder_id)
    # Manually clear folder_id before delete — SQLite doesn't enforce FK cascades
    # by default; on Postgres the FK constraint handles this automatically.
    await session.execute(
        update(Workflow).where(Workflow.folder_id == folder_id).values(folder_id=None)
    )
    await session.delete(folder)
    await session.commit()
