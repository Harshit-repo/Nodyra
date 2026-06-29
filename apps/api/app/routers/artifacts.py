import asyncio
import contextlib
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Artifact, Run
from app.schemas import ArtifactInfo, DatasetQueryRequest, DatasetQueryResult
from app.security import require_permission
from app.services.artifact_backends import (
    _resolve_local_path,
    get_backend,
)
from app.services.artifacts import atomic_write_bytes, delete_artifact_files
from app.services.datasets_query import DatasetQueryError, run_dataset_query
from app.tenancy import DEFAULT_ORG_ID, active_org_id

router = APIRouter(tags=["artifacts"])
logger = logging.getLogger(__name__)


def _runless_artifact_visible(row: Artifact) -> bool:
    """Run-less uploads have no org_id column; their storage key carries it."""
    if not settings.multi_tenancy_enabled:
        return True
    org_id = active_org_id() or DEFAULT_ORG_ID
    storage_key = str(row.storage_key or "")
    if storage_key.startswith(f"{org_id}/"):
        return True
    # Legacy pre-namespace uploads were stored as uploads/{artifact_id}/...
    # and belong to the default org after MT is enabled.
    return org_id == DEFAULT_ORG_ID and storage_key.startswith("uploads/")


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
    # Tenancy guard: artifacts carry no org_id; their org is the parent
    # run's. The Run lookup goes through the org-scoped ORM filter (and RLS
    # on Postgres), so a foreign org's run resolves to None — answer 404, not
    # 403, to avoid existence leaks. Run-less rows (browser uploads) are
    # instance-level until Phase B scopes uploads.
    # populate_existing forces a real SELECT — an identity-map hit from
    # earlier in the session would skip the org filter entirely.
    if row.run_id is not None and (
        await session.get(Run, row.run_id, populate_existing=True) is None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
    if row.run_id is None and not _runless_artifact_visible(row):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
    return row


@router.get("/runs/{run_id}/artifacts", response_model=list[ArtifactInfo])
async def list_run_artifacts(
    run_id: str,
    node_id: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ArtifactInfo]:
    # B-01: Use select() to trigger do_orm_execute org filter.
    if await session.scalar(select(Run).where(Run.id == run_id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    q = select(Artifact).where(Artifact.run_id == run_id)
    if node_id is not None:
        q = q.where(Artifact.node_id == node_id)
    # Fetch newest-first so the dedup dict keeps the latest version of each name.
    rows = (await session.scalars(q.order_by(Artifact.created_at.desc()))).all()
    seen: set[tuple[str | None, str]] = set()
    unique: list[Artifact] = []
    for row in rows:
        key = (row.node_id, row.name)
        if key not in seen:
            seen.add(key)
            unique.append(row)
    unique.sort(key=lambda r: r.name)
    return [_info(row) for row in unique]


@router.get("/artifacts/{artifact_id}", response_model=ArtifactInfo)
async def get_artifact(
    artifact_id: str, session: AsyncSession = Depends(get_session)
) -> ArtifactInfo:
    return _info(await _get_artifact(session, artifact_id))


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    inline: bool = False,
    session: AsyncSession = Depends(get_session),
):
    row = await _get_artifact(session, artifact_id)
    try:
        backend = get_backend(row.storage_backend)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    # Backends that produce time-limited URLs (S3) skip the API process for
    # the actual bytes — saves bandwidth and keeps long downloads off the
    # event loop.
    redirect = backend.signed_url(row)
    if redirect:
        return RedirectResponse(redirect, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    try:
        download = backend.open_download(row)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact file not found") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    # ``inline`` lets the browser render images/PDFs/media in-page instead of
    # forcing a download (used by the artifact preview in the editor).
    disposition = "inline" if inline else "attachment"
    if download.path is not None:
        return FileResponse(
            download.path,
            media_type=download.content_type,
            filename=download.filename,
            content_disposition_type=disposition,
        )
    if download.stream is not None:
        return StreamingResponse(
            download.stream,
            media_type=download.content_type,
            headers={
                "Content-Disposition": f'{disposition}; filename="{download.filename}"',
            },
        )
    raise HTTPException(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "Backend produced no download payload",
    )


@router.post(
    "/artifacts/{artifact_id}/query",
    response_model=DatasetQueryResult,
)
async def query_artifact(
    artifact_id: str,
    payload: DatasetQueryRequest,
    session: AsyncSession = Depends(get_session),
) -> DatasetQueryResult:
    """Run a read-only DuckDB query against a Parquet-backed dataset artifact.

    The Parquet file is exposed as the ``dataset`` and ``input`` views.
    """
    import asyncio

    row = await _get_artifact(session, artifact_id)
    is_parquet = (
        row.kind == "dataset"
        or "parquet" in (row.content_type or "").lower()
        or row.name.lower().endswith(".parquet")
    )
    if not is_parquet:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "SQL query is only supported for Parquet-backed datasets",
        )
    try:
        result = await asyncio.to_thread(run_dataset_query, row, payload.sql, payload.limit)
    except DatasetQueryError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return DatasetQueryResult(**result)


@router.get("/artifacts/{artifact_id}/url")
async def get_artifact_signed_url(
    artifact_id: str,
    expires_in: int = 300,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str | int | None]:
    """Return a time-limited URL for object-store backends (or ``null`` locally)."""
    row = await _get_artifact(session, artifact_id)
    try:
        backend = get_backend(row.storage_backend)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    url = backend.signed_url(row, expires_in=max(1, int(expires_in)))
    return {"url": url, "expires_in": expires_in if url else None}


@router.post("/artifacts/upload", response_model=ArtifactInfo)
async def upload_artifact(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_permission("artifact:write")),
) -> ArtifactInfo:
    """Upload a file from the browser and store it as a run-less artifact."""
    max_bytes = settings.max_artifact_bytes
    content = await file.read(max_bytes + 1 if max_bytes > 0 else -1)
    if max_bytes > 0 and len(content) > max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds maximum upload size of {max_bytes} bytes",
        )

    # SECURITY: the uploaded filename is attacker-controlled. Reduce it to a
    # bare basename so directory components and ``..`` segments can't escape the
    # per-upload folder, then resolve through the same containment guard the
    # read/delete paths use as defence-in-depth (ART-1).
    filename = Path(file.filename or "upload").name or "upload"
    content_type = file.content_type or "application/octet-stream"
    artifact_id = uuid.uuid4().hex

    # Phase F: namespace new uploads under the request org (default org when
    # multi-tenancy is off) so storage quotas/retention can group by prefix.
    org_segment = active_org_id() or DEFAULT_ORG_ID
    storage_key = f"{org_segment}/uploads/{artifact_id}/{filename}"
    artifact_path = _resolve_local_path(storage_key)
    await asyncio.to_thread(atomic_write_bytes, artifact_path, content)

    backend = get_backend()
    storage_backend = "local"
    if backend.name != "local":
        upload_row = Artifact(
            id=artifact_id,
            run_id=None,
            node_id=None,
            name=filename,
            kind="upload",
            content_type=content_type,
            size_bytes=len(content),
            storage_backend=backend.name,
            storage_key=storage_key,
        )
        try:
            backend.upload_from_local(upload_row, artifact_path)
        except Exception:  # noqa: BLE001 - keep the local file as a fallback
            logger.exception(
                "upload_artifact: failed to rehome upload %s to backend %s; keeping local",
                artifact_id,
                backend.name,
            )
        else:
            storage_backend = backend.name
            with contextlib.suppress(OSError):
                artifact_path.unlink(missing_ok=True)

    row = Artifact(
        id=artifact_id,
        run_id=None,
        node_id=None,
        name=filename,
        kind="upload",
        content_type=content_type,
        size_bytes=len(content),
        storage_backend=storage_backend,
        storage_key=storage_key,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _info(row)


@router.delete(
    "/artifacts/{artifact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("artifact:delete"))],
)
async def delete_artifact(artifact_id: str, session: AsyncSession = Depends(get_session)) -> None:
    row = await _get_artifact(session, artifact_id)
    delete_artifact_files([row])
    await session.delete(row)
    await session.commit()
