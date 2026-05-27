"""Artifact storage and metadata helpers."""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.models import Artifact
from app.services.redaction import load_secret_values, redact_value
from noodle.artifacts import ARTIFACT_MARKER, LocalArtifactStore, is_artifact_ref


def artifact_base_dir() -> Path:
    return Path(settings.artifacts_dir).expanduser().resolve()


def make_artifact_store(
    run_id: str,
    *,
    max_bytes: int | None = None,
    max_count: int | None = None,
) -> LocalArtifactStore:
    return LocalArtifactStore(
        artifact_base_dir(),
        run_id,
        max_bytes=max_bytes if max_bytes is not None else settings.max_artifact_bytes,
        max_count=max_count if max_count is not None else settings.max_artifacts_per_run,
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


def _artifact_path(storage_key: str) -> Path:
    base = artifact_base_dir()
    path = (base / storage_key).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise ValueError("artifact storage key escapes the artifact directory") from exc
    return path


def path_for_artifact(artifact: Artifact) -> Path:
    if artifact.storage_backend != "local":
        raise ValueError(f"unsupported artifact backend {artifact.storage_backend!r}")
    return _artifact_path(artifact.storage_key)


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
            if row.storage_backend == "local":
                try:
                    path_for_artifact(row)
                except ValueError:
                    continue
            session.add(row)
        await session.commit()


def delete_artifact_files(rows: Iterable[Artifact]) -> None:
    for row in rows:
        if row.storage_backend != "local":
            continue
        try:
            path = path_for_artifact(row)
        except ValueError:
            continue
        try:
            path.unlink(missing_ok=True)
            # Remove now-empty artifact-id/node/run directories opportunistically.
            for parent in (path.parent, path.parent.parent, path.parent.parent.parent):
                if parent == artifact_base_dir() or not parent.exists():
                    break
                try:
                    parent.rmdir()
                except OSError:
                    break
        except OSError:
            pass


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


def delete_run_artifact_dir(run_id: str) -> None:
    path = artifact_base_dir() / "runs" / run_id
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass
