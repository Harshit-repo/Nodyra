"""Artifact integrity reconciliation with bounded, dry-run-first repair."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Artifact
from app.services.artifact_backends import ArtifactBackend, ArtifactObjectInfo, get_backend
from app.tenancy import run_as_system

_MAX_ISSUE_SAMPLES = 500


def _validate_prefix(prefix: str) -> None:
    """Reject values that are not portable object-store key prefixes."""
    if len(prefix) > 1_024:
        raise ValueError("Artifact prefix must be at most 1024 characters")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in prefix):
        raise ValueError("Artifact prefix contains invalid control characters")
    if prefix.startswith(("/", "\\")) or "://" in prefix or "\\" in prefix:
        raise ValueError("Artifact prefix must be a relative object key prefix")
    if any(part in {".", ".."} for part in prefix.split("/")):
        raise ValueError("Artifact prefix must not contain traversal segments")


def _collect_objects(
    backend: ArtifactBackend, *, prefix: str, limit: int
) -> tuple[list[ArtifactObjectInfo], bool]:
    objects: list[ArtifactObjectInfo] = []
    truncated = False
    for item in backend.iter_objects(prefix=prefix):
        if len(objects) >= limit:
            truncated = True
            break
        objects.append(item)
    return objects, truncated


async def reconcile_artifacts(
    *,
    backend_name: str | None = None,
    prefix: str = "",
    verify_checksums: bool = False,
    repair_metadata: bool = False,
    delete_orphans: bool = False,
    limit: int = 10_000,
) -> dict[str, Any]:
    """Compare metadata rows with stored objects.

    ``repair_metadata`` only annotates health evidence; it never invents bytes.
    Raw orphan deletion is separately opt-in. Both actions are safe to rerun.
    """

    limit = max(1, min(limit, 100_000))
    _validate_prefix(prefix)
    backend = get_backend(backend_name or None)
    async with SessionLocal() as session:
        with run_as_system():
            statement = select(Artifact).where(Artifact.storage_backend == backend.name)
            if prefix:
                statement = statement.where(Artifact.storage_key.startswith(prefix))
            rows = list((await session.scalars(statement.limit(limit + 1))).all())
            rows_truncated = len(rows) > limit
            rows = rows[:limit]

            issues: list[dict[str, Any]] = []
            counts: Counter[str] = Counter()
            checked_at = datetime.now(UTC).isoformat()

            async def inspect(
                row: Artifact,
            ) -> tuple[Artifact, ArtifactObjectInfo | None, str | None]:
                try:
                    info = await asyncio.to_thread(
                        backend.inspect,
                        row,
                        verify_checksum=verify_checksums,
                    )
                    return row, info, None
                except FileNotFoundError:
                    return row, None, "missing"
                except Exception:  # noqa: BLE001 - report backend failures without aborting sweep
                    return row, None, "inspect_error"

            for offset in range(0, len(rows), 16):
                inspected = await asyncio.gather(
                    *(inspect(row) for row in rows[offset : offset + 16])
                )
                for row, info, error in inspected:
                    issue: str | None = error
                    if info is not None and info.size_bytes != row.size_bytes:
                        issue = "size_mismatch"
                    if (
                        info is not None
                        and verify_checksums
                        and row.checksum_sha256
                        and info.checksum_sha256 != row.checksum_sha256
                    ):
                        issue = "checksum_mismatch"
                    health = "healthy" if issue is None else issue
                    counts[health] += 1
                    if issue and len(issues) < _MAX_ISSUE_SAMPLES:
                        issues.append(
                            {
                                "kind": issue,
                                "artifact_id": row.id,
                                "storage_key": row.storage_key,
                            }
                        )
                    if repair_metadata:
                        row.artifact_metadata = {
                            **(row.artifact_metadata or {}),
                            "integrity": {
                                "status": health,
                                "verified_at": checked_at,
                                "checksum_verified": bool(verify_checksums and row.checksum_sha256),
                            },
                        }

            known_keys = {row.storage_key for row in rows}
            if rows_truncated:
                # Never label or delete an object as orphaned when the metadata
                # side of the comparison was intentionally truncated.
                objects = []
                objects_truncated = True
                orphan_keys: list[str] = []
            else:
                objects, objects_truncated = await asyncio.to_thread(
                    _collect_objects,
                    backend,
                    prefix=prefix,
                    limit=limit,
                )
                orphan_keys = [item.key for item in objects if item.key not in known_keys]
            counts["orphan"] = len(orphan_keys)
            for key in orphan_keys[: max(0, _MAX_ISSUE_SAMPLES - len(issues))]:
                issues.append({"kind": "orphan", "storage_key": key})

            deleted_orphans = 0
            if delete_orphans and orphan_keys:
                await asyncio.to_thread(backend.delete_keys, orphan_keys)
                deleted_orphans = len(orphan_keys)
            if repair_metadata:
                await session.commit()

    return {
        "backend": backend.name,
        "prefix": prefix,
        "checked_at": checked_at,
        "verify_checksums": verify_checksums,
        "repair_metadata": repair_metadata,
        "delete_orphans": delete_orphans,
        "rows_scanned": len(rows),
        "objects_scanned": len(objects),
        "rows_truncated": rows_truncated,
        "objects_truncated": objects_truncated,
        "counts": dict(counts),
        "deleted_orphans": deleted_orphans,
        "issues": issues,
        "issues_truncated": sum(count for key, count in counts.items() if key != "healthy")
        > len(issues),
    }
