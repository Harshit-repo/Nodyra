"""Artifact storage and metadata helpers.

Bytes live in a pluggable :class:`~app.services.artifact_backends.ArtifactBackend`
(local filesystem by default; S3-compatible in production). This module only
owns the metadata side: persisting refs to the ``artifacts`` table, redacting
previews, and orchestrating backend deletes.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from collections.abc import Iterable
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.exceptions import ArtifactRefInvalid
from app.models import Artifact, Run
from app.services.artifact_backends import (
    LocalBackend,
    get_backend,
)

# Re-exported for app.routers.runner_pools, which imports _artifact_path from here.
from app.services.artifact_backends import (
    _resolve_local_path as _artifact_path,  # noqa: F401
)
from app.services.redaction import (
    load_secret_values,
    load_secret_values_for_org,
    redact_value,
)
from app.tenancy import DEFAULT_ORG_ID, run_as_system
from nodyra.artifacts import (
    ARTIFACT_MARKER,
    LocalArtifactStore,
    is_artifact_ref,
    sanitize_name,
)


def artifact_base_dir() -> Path:
    return Path(settings.artifacts_dir).expanduser().resolve()


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Atomically replace a local artifact without exposing partial bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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


def _stage_upload(
    row: Artifact, store: LocalArtifactStore, destination: Path | None = None
) -> None:
    """Stream verified bytes into run scratch space without exposing partial files."""
    destination = destination or store.upload_path(row.id) / sanitize_name(row.name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{uuid.uuid4().hex}.tmp")
    download = get_backend(row.storage_backend).open_download(row)
    source = download.path.open("rb") if download.path is not None else None
    chunks = iter(lambda: source.read(64 * 1024), b"") if source else download.stream
    if chunks is None:
        raise ValueError("Upload storage backend must provide a file or byte stream")
    digest = hashlib.sha256()
    total = 0
    try:
        with temporary.open("wb") as target:
            for chunk in chunks:
                total += len(chunk)
                if total > row.size_bytes or (store.max_bytes and total > store.max_bytes):
                    raise ValueError(
                        "Uploaded file exceeds its declared size or the run's file limit"
                    )
                digest.update(chunk)
                target.write(chunk)
        if total != row.size_bytes or (
            row.checksum_sha256 and digest.hexdigest() != row.checksum_sha256
        ):
            raise ValueError("Uploaded file failed its size or checksum integrity check")
        os.replace(temporary, destination)
    finally:
        if source is not None:
            source.close()
        close = getattr(chunks, "close", None)
        if close:
            close()
        temporary.unlink(missing_ok=True)


async def prepare_uploaded_files(
    session: AsyncSession, graph: dict, *, run_id: str, org_id: str | None
) -> None:
    """Authorize file-widget inputs before staging them for the run's runtime.

    Works with local and S3 storage, including historical storage keys. The
    explicit organization predicate remains enforced in system worker contexts.
    Run scratch files are reclaimed by the existing run retention policy.
    """
    from nodyra.sdk import registry

    upload_ids: set[str] = set()
    for node in graph.get("nodes", []):
        if node.get("type") not in registry:
            continue
        for param in registry.get(node["type"]).manifest.params:
            value = node.get("params", {}).get(param.name)
            if param.widget == "file_upload" and isinstance(value, str) and value:
                upload_ids.add(value)
    if not upload_ids:
        return
    run_org = org_id or DEFAULT_ORG_ID
    store = make_artifact_store(run_id, org_id=run_org)
    for upload_id in sorted(upload_ids):
        store.upload_path(upload_id)  # Reject malformed ids before any I/O.
        row = await session.scalar(
            select(Artifact).where(
                Artifact.id == upload_id,
                Artifact.org_id == run_org,
                Artifact.kind == "upload",
                Artifact.run_id.is_(None),
            )
        )
        if row is None:
            raise ValueError("Uploaded file is unavailable in this organization; select it again")
        await asyncio.to_thread(_stage_upload, row, store)


async def prepare_artifact_inputs(
    session: AsyncSession, value: Any, *, run_id: str, org_id: str | None
) -> None:
    """Rehydrate cached/pinned artifact refs into authorized run-local input files."""
    run_org = org_id or DEFAULT_ORG_ID
    store = make_artifact_store(run_id, org_id=run_org)
    for ref in collect_artifact_refs(value):
        # Everything about the ref itself is caller-supplied, so a malformed
        # artifact id is a client error too — the store raises a plain
        # ValueError for it, which escaped as a 500 like the checks below did.
        try:
            destination = store.input_path(ref)
        except ValueError as exc:
            raise ArtifactRefInvalid(f"Invalid artifact reference: {exc}") from exc
        row = await session.scalar(
            select(Artifact).where(
                Artifact.id == ref["artifact_id"],
                Artifact.org_id == run_org,
            )
        )
        if row is None:
            # A recovered checkpoint can refer to an output streamed to durable
            # worker scratch before the previous attempt committed its outcome.
            if ref.get("run_id") != run_id:
                raise ArtifactRefInvalid(
                    "Referenced artifact is unavailable in this organization"
                )
            row = _row_from_ref(ref, run_id, [], org_id=run_org)
        for name in ("name", "run_id", "size_bytes", "checksum_sha256"):
            if ref.get(name) != getattr(row, name):
                raise ArtifactRefInvalid(
                    "Referenced artifact metadata does not match its stored file"
                )
        # storage_key only when the caller actually has one. The API strips it
        # from every ref it serves ("implementation details that must not leak
        # through the public API" — NodeRunInfo._redact_storage_internals), so
        # requiring it made any ref read back from a run unusable as cache or
        # pinned data: the caller cannot echo a field they were never given.
        # Nothing reads it from the ref either — input_path derives the path
        # from artifact_id and name, and staging copies from the row — so this
        # was friction, not a check. Engine-internal refs (recovered
        # checkpoints) do carry it, and those are still compared.
        if ref.get("storage_key") is not None and ref["storage_key"] != row.storage_key:
            raise ArtifactRefInvalid(
                "Referenced artifact metadata does not match its stored file"
            )
        await asyncio.to_thread(_stage_upload, row, store, destination)


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
        raise ValueError(f"path_for_artifact requires a local backend (got {backend.name!r})")
    return backend.path_for_artifact(artifact)


def canonical_storage_keys(
    *, run_id: str, node_id: str, artifact_id: str, name: str, org_id: str | None
) -> list[str]:
    """Every storage key a ref for this run is allowed to carry.

    Mirrors ``LocalArtifactStore._storage_key``. Two forms are legal because the
    org prefix was introduced later and a store built without an org id (single
    tenant, or a runtime that had no org context) still writes the unprefixed
    key. Both address bytes inside this run's own directory, which is the whole
    point of the check.
    """
    suffix = f"runs/{run_id}/{sanitize_name(node_id)}/{artifact_id}-{sanitize_name(name)}"
    cleaned = str(org_id or "").strip("/")
    return [f"{cleaned}/{suffix}", suffix] if cleaned else [suffix]


def _row_from_ref(
    ref: dict[str, Any],
    run_id: str,
    secret_values: list[str] | None = None,
    *,
    org_id: str | None = None,
) -> Artifact:
    artifact_id = str(ref["artifact_id"])
    node_id = str(ref.get("node_id") or "unknown")
    name = str(ref.get("name") or "artifact")
    # SECURITY (F-06): the ref is produced by the run's own Python — user code.
    # A crafted ``storage_key`` stays inside the artifact root (the backend's
    # traversal guard sees to that) but could otherwise address ANOTHER run's,
    # and therefore another tenant's, bytes; the row would then be stamped with
    # the attacker's org and served by the normal download route. Accept the
    # supplied key only when it is one this run could legitimately have
    # written, else fall back to the canonical key.
    allowed = canonical_storage_keys(
        run_id=run_id,
        node_id=node_id,
        artifact_id=artifact_id,
        name=name,
        org_id=org_id,
    )
    supplied = str(ref.get("storage_key") or "").strip()
    storage_key = supplied if supplied in allowed else allowed[0]
    return Artifact(
        id=artifact_id,
        run_id=run_id,
        # Stamp the owning org explicitly: artifact persistence runs in a
        # background worker with no request org context, so the before_flush
        # stamp hook would otherwise file the row under the default org.
        org_id=org_id,
        node_id=node_id,
        name=name,
        kind=str(ref.get("kind") or "binary"),
        content_type=str(ref.get("content_type") or "application/octet-stream"),
        size_bytes=int(ref.get("size_bytes") or 0),
        checksum_sha256=(str(ref["checksum_sha256"]) if ref.get("checksum_sha256") else None),
        storage_backend=str(ref.get("storage_backend") or "local"),
        storage_key=storage_key,
        artifact_metadata=redact_value(
            ref.get("metadata") if isinstance(ref.get("metadata"), dict) else {},
            secret_values or [],
        ),
        preview=redact_value(ref.get("preview"), secret_values or []),
    )


async def persist_artifact_refs(
    run_id: str,
    refs: Iterable[dict[str, Any]],
    *,
    session: AsyncSession | None = None,
    cleanup_paths: list[Path] | None = None,
) -> None:
    """Persist refs, optionally within the run outcome's fenced transaction.

    The caller of a shared transaction removes cleanup_paths only after commit.
    """
    unique = {
        str(ref.get("artifact_id")): ref
        for ref in refs
        if ref.get(ARTIFACT_MARKER) is True and ref.get("artifact_id")
    }
    if not unique:
        return

    configured_backend = (settings.artifact_storage_backend or "local").lower()

    owns_session = session is None
    pending_cleanup = cleanup_paths if cleanup_paths is not None else []
    async with SessionLocal() if owns_session else nullcontext(session) as session:
        # Artifact persistence runs in workers/background tasks with no request
        # tenant context. In multi-tenant mode an unset context deliberately
        # falls back to the default org, so all reads here must opt out and then
        # stamp rows from the owning Run explicitly.
        with run_as_system():
            # Resolve the owning org FIRST so redaction only ever decrypts this
            # tenant's credentials. Loading the all-orgs list here used to pull
            # every organization's plaintext secrets into one process cache for
            # a single run's redaction (F-02).
            run_org_id = await session.scalar(select(Run.org_id).where(Run.id == run_id))
            secret_values = (
                await load_secret_values_for_org(run_org_id, session)
                if run_org_id
                else await load_secret_values(session)
            )
            existing = set(
                (
                    await session.scalars(select(Artifact.id).where(Artifact.id.in_(unique.keys())))
                ).all()
            )
            for artifact_id, ref in unique.items():
                if artifact_id in existing:
                    continue
                row = _row_from_ref(ref, run_id, secret_values, org_id=run_org_id)
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
                        await asyncio.to_thread(target.upload_from_local, row, local_path)
                    except Exception:  # noqa: BLE001
                        # Upload failed; keep the local row so the bytes are
                        # still served via the local backend rather than losing
                        # them. The error has already been logged by the backend.
                        row.storage_backend = "local"
                    else:
                        pending_cleanup.append(local_path)
                session.add(row)
            if owns_session:
                await session.commit()
                for path in pending_cleanup:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
            else:
                await session.flush()


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


async def delete_artifacts_for_run_ids(session: AsyncSession, run_ids: list[str]) -> None:
    if not run_ids:
        return
    rows = list((await session.scalars(select(Artifact).where(Artifact.run_id.in_(run_ids)))).all())
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
