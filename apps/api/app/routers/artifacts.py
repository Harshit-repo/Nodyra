from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Artifact, Run
from app.schemas import ArtifactInfo
from app.security import require_permission
from app.services.artifacts import delete_artifact_files, path_for_artifact

router = APIRouter(tags=["artifacts"])


def _info(row: Artifact) -> ArtifactInfo:
    return ArtifactInfo(
        id=row.id,
        run_id=row.run_id,
        node_id=row.node_id,
        name=row.name,
        kind=row.kind,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        metadata=row.artifact_metadata or {},
        preview=row.preview,
        created_at=row.created_at,
    )


async def _get_artifact(session: AsyncSession, artifact_id: str) -> Artifact:
    row = await session.get(Artifact, artifact_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
    return row


@router.get("/runs/{run_id}/artifacts", response_model=list[ArtifactInfo])
async def list_run_artifacts(
    run_id: str, session: AsyncSession = Depends(get_session)
) -> list[ArtifactInfo]:
    if await session.get(Run, run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    rows = (
        await session.scalars(
            select(Artifact)
            .where(Artifact.run_id == run_id)
            .order_by(Artifact.created_at, Artifact.name)
        )
    ).all()
    return [_info(row) for row in rows]


@router.get("/artifacts/{artifact_id}", response_model=ArtifactInfo)
async def get_artifact(
    artifact_id: str, session: AsyncSession = Depends(get_session)
) -> ArtifactInfo:
    return _info(await _get_artifact(session, artifact_id))


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: str, session: AsyncSession = Depends(get_session)
) -> FileResponse:
    row = await _get_artifact(session, artifact_id)
    try:
        path = path_for_artifact(row)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact file not found")
    return FileResponse(
        path,
        media_type=row.content_type,
        filename=row.name,
    )


@router.delete(
    "/artifacts/{artifact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("artifact:delete"))],
)
async def delete_artifact(
    artifact_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    row = await _get_artifact(session, artifact_id)
    delete_artifact_files([row])
    await session.delete(row)
    await session.commit()
