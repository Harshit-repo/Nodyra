"""Artifact storage and metadata helpers.

Bytes live in a pluggable :class:`~app.services.artifact_backends.ArtifactBackend`
(local filesystem by default; S3-compatible in production). This module only
owns the metadata side: persisting refs to the ``artifacts`` table, redacting
previews, and orchestrating backend deletes.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import Artifact
from app.services.artifact_backends import (
    LocalBackend,
    get_backend,
)

# Re-exported for app.routers.runner_pools, which imports _artifact_path from here.
from app.services.artifact_backends import (
    _resolve_local_path as _artifact_path,  # noqa: F401
)
from app.services.redaction import load_secret_values, redact_value
from noodle.artifacts import ARTIFACT_MARKER, LocalArtifactStore, is_artifact_ref


def artifact_base_dir() -> Path:
    return Path(settings.artifacts_dir).expanduser().resolve()


def make_artifact_store(
    run_id: str,
    *,
    org_id: str | None = None,
    max_bytes: int | None = None,
    max_count: int | None = None,
) -> LocalArtifactStore:
    return LocalArtifactStore(
        artifact_base_dir(),
        run_id,
        max_bytes=max_bytes if max_bytes is not None else settings.max_artifact_bytes,
        max_count=max_count if max_count is not None else settings.max_artifacts_per_run,
        # Phase F: new artifact keys are namespaced {org_id}/runs/{run_id}/...
        # Existing rows keep their stored storage_key (reads are row-driven),
        # so no rename migration is needed.
        key_prefix=org_id or "",
    )


def collect_artifact_refs(value: Any) -> list[dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}

    def visit(item: Any) -> None:
        if is_artifact_ref(item):
            refs[item["artifact_id"]] = item
            return
        if isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return list(refs.values())


def path_for_artifact(artifact: Artifact) -> Path:
    """Local filesystem path for an artifact.

    Backwards-compatible shim: prefer ``get_backend(artifact.storage_backend)
    .open_download(artifact)`` for any new code path. Only callers that need
    a real ``Path`` (e.g. file writers) should keep using this.
    """
    backend = get_backend(artifact.storage_backend)
    if not isinstance(backend, LocalBackend):
        raise ValueError(
            f"path_for_artifact requires a local backend (got {backend.name!r})"
        )
    return backend.path_for_artifact(artifact)


def _row_from_ref(
    ref: dict[str, Any], run_id: str, secret_values: list[str] | None = None
) -> Artifact:
    artifact_id = str(ref["artifact_id"])
    node_id = str(ref.get("node_id") or "unknown")
    name = str(ref.get("name") or "artifact")
    storage_key = str(
        ref.get("storage_key")
        or f"runs/{run_id}/{node_id}/{artifact_id}-{name}"
    )
    return Artifact(
        id=artifact_id,
        run_id=run_id,
        node_id=node_id,
        name=name,
        kind=str(ref.get("kind") or "binary"),
        content_type=str(ref.get("content_type") or "application/octet-stream"),
        size_bytes=int(ref.get("size_bytes") or 0),
        storage_backend=str(ref.get("storage_backend") or "local"),
        storage_key=storage_key,
        artifact_metadata=redact_value(
            ref.get("metadata") if isinstance(ref.get("metadata"), dict) else {},
            secret_values or [],
        ),
        preview=redact_value(ref.get("preview"), secret_values or []),
    )


async def persist_artifact_refs(run_id: str, refs: Iterable[dict[str, Any]]) -> None:
    unique = {
        str(ref.get("artifact_id")): ref
        for ref in refs
        if ref.get(ARTIFACT_MARKER) is True and ref.get("artifact_id")
    }
    if not unique:
        return

    configured_backend = (settings.artifact_storage_backend or "local").lower()

    async with SessionLocal() as session:
        secret_values = await load_secret_values(session)
        existing = set(
            (
                await session.scalars(
                    select(Artifact.id).where(Artifact.id.in_(unique.keys()))
                )
            ).all()
        )
        for artifact_id, ref in unique.items():
            if artifact_id in existing:
                continue
            row = _row_from_ref(ref, run_id, secret_values)
            local_path: Path | None = None
            if row.storage_backend == "local":
                try:
                    local_path = path_for_artifact(row)
                except ValueError:
                    continue
            # Rehome to the configured backend when it isn't local. Workers
            # always write to the local FS via LocalArtifactStore (no
            # per-worker S3 credentials, warm-pool reuse); the API uploads
            # those bytes here so production deployments keep artifacts in
            # the durable backend instead of local scratch.
            if (
                configured_backend != "local"
                and row.storage_backend == "local"
                and local_path is not None
                and local_path.exists()
            ):
                try:
                    target = get_backend(configured_backend)
                    row.storage_backend = configured_backend
                    target.upload_from_local(row, local_path)
                except Exception:  # noqa: BLE001
                    # Upload failed; keep the local row so the bytes are
                    # still served via the local backend rather than losing
                    # them. The error has already been logged by the backend.
                    row.storage_backend = "local"
                else:
                    # Reclaim local scratch — the durable copy is in the
                    # configured backend now.
                    try:
                        local_path.unlink(missing_ok=True)
                    except OSError:
                        pass
            session.add(row)
        await session.commit()


def delete_artifact_files(rows: Iterable[Artifact]) -> None:
    """Delete bytes via each row's storage backend.

    Groups by ``storage_backend`` so each backend gets one batched call —
    important for object stores where per-object DELETEs are billed.
    """
    by_backend: dict[str, list[Artifact]] = {}
    for row in rows:
        by_backend.setdefault(row.storage_backend or "local", []).append(row)
    for name, group in by_backend.items():
        try:
            backend = get_backend(name)
        except KeyError:
            continue
        backend.delete(group)


async def delete_artifacts_for_run_ids(
    session: AsyncSession, run_ids: list[str]
) -> None:
    if not run_ids:
        return
    rows = list(
        (
            await session.scalars(
                select(Artifact).where(Artifact.run_id.in_(run_ids))
            )
        ).all()
    )
    delete_artifact_files(rows)
    await session.execute(delete(Artifact).where(Artifact.run_id.in_(run_ids)))


def delete_run_artifact_dir(run_id: str, org_id: str | None = None) -> None:
    """Reclaim per-run scratch space across all backends."""
    backends = {"local", (settings.artifact_storage_backend or "local").lower()}
    for backend_name in backends:
        try:
            get_backend(backend_name).delete_run(run_id, org_id=org_id)
        except KeyError:
            continue
