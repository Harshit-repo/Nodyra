"""Pluggable artifact backend interface.

The API persists ``Artifact`` rows in the metadata DB regardless of where the
bytes live; this module abstracts ``where``. ``LocalBackend`` keeps bytes on
the API host's filesystem (default for ``runtime_mode=local``); ``S3Backend``
(see ``s3_backend.py``) targets any S3-compatible object store and is the
recommended production deployment.

Routers and services should only depend on the protocol — never reach into a
specific backend's internals. ``get_backend(name)`` is the single factory.
"""

from __future__ import annotations

import hashlib
import shutil
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from app.config import settings

if TYPE_CHECKING:
    from app.models import Artifact


@dataclass(slots=True)
class ArtifactDownload:
    """How to deliver an artifact's bytes to a HTTP client.

    Exactly one of ``path`` (zero-copy file) or ``stream`` (chunk iterator)
    must be set. ``redirect_url`` is an alternative for object stores that
    can produce time-limited URLs — the router will issue a 307 redirect
    instead of streaming bytes through the API process.
    """

    content_type: str
    filename: str
    size_bytes: int | None = None
    path: Path | None = None
    stream: Iterator[bytes] | None = None
    redirect_url: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactObjectInfo:
    key: str
    size_bytes: int
    checksum_sha256: str | None = None
    last_modified: str | None = None


class ArtifactBackend(Protocol):
    """Storage operations a backend must support.

    All methods receive the persisted ``Artifact`` row (the metadata source
    of truth). Backends own the bytes, not the row — they must not write
    to or delete the row themselves.
    """

    name: str

    def delete(self, artifacts: Iterable[Artifact]) -> None:
        """Remove the underlying bytes for each artifact. Must be idempotent.

        Failures must be logged but never raised — bulk retention sweeps
        rely on this not aborting halfway through.
        """

    def open_download(self, artifact: Artifact) -> ArtifactDownload:
        """Return how to deliver the artifact's bytes.

        Raises ``FileNotFoundError`` when the row's storage_key has no
        backing bytes (orphan row from a failed write).
        """

    def signed_url(self, artifact: Artifact, *, expires_in: int = 300) -> str | None:
        """Optional time-limited URL bypassing the API process.

        Local backends should return ``None``; clients then fall back to
        ``GET /artifacts/{id}/download``.
        """

    def stats(self) -> dict[str, Any]:
        """Backend-specific telemetry surfaced by ``/ops/queue``."""

    def delete_run(self, run_id: str, org_id: str | None = None) -> None:
        """Best-effort cleanup of any per-run scratch space (e.g. empty dirs).

        ``org_id`` covers the Phase F namespaced layout
        ({org_id}/runs/{run_id}); the legacy layout (runs/{run_id}) is always
        swept too. Independent of ``delete`` because some backends (S3) have
        no concept of an empty 'directory' to reclaim.
        """

    def upload_from_local(self, artifact: Artifact, local_path: Path) -> None:
        """Persist bytes for ``artifact`` from a local file.

        The worker always writes to the host filesystem via
        ``LocalArtifactStore`` (warm-pool subprocess can't talk to S3 directly
        without per-worker credentials). The API rehomes those bytes to the
        configured backend in ``persist_artifact_refs`` by calling this.
        For ``LocalBackend`` this is a no-op when the file is already in the
        artifacts dir; remote backends upload and the caller flips
        ``storage_backend`` on the row.
        """

    def inspect(self, artifact: Artifact, *, verify_checksum: bool = False) -> ArtifactObjectInfo:
        """Read bounded object metadata and optionally verify its full checksum."""

    def iter_objects(self, *, prefix: str = "") -> Iterator[ArtifactObjectInfo]:
        """Iterate stored objects for integrity reconciliation."""

    def delete_keys(self, keys: Iterable[str]) -> None:
        """Idempotently remove raw orphan keys selected by reconciliation."""


# --- Local filesystem backend ------------------------------------------------


def _artifact_base_dir() -> Path:
    return Path(settings.artifacts_dir).expanduser().resolve()


def _resolve_local_path(storage_key: str) -> Path:
    """Resolve a storage key under the artifacts dir, guarding against escape.

    Trusted-author model doesn't make this redundant — storage_key can come
    from a runner via the artifact ref and we never let bytes write outside
    the configured root.
    """
    base = _artifact_base_dir()
    path = (base / storage_key).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise ValueError("artifact storage key escapes the artifact directory") from exc
    return path


class LocalBackend:
    name = "local"

    def _path(self, artifact: Artifact) -> Path:
        if artifact.storage_backend != self.name:
            raise ValueError(
                f"local backend cannot handle storage_backend={artifact.storage_backend!r}"
            )
        return _resolve_local_path(artifact.storage_key)

    def path_for_artifact(self, artifact: Artifact) -> Path:
        """Public path accessor used by writers that still need a Path."""
        return self._path(artifact)

    def delete(self, artifacts: Iterable[Artifact]) -> None:
        base = _artifact_base_dir()
        for row in artifacts:
            if row.storage_backend != self.name:
                continue
            try:
                path = self._path(row)
            except ValueError:
                continue
            try:
                path.unlink(missing_ok=True)
                # Opportunistically reclaim now-empty artifact-id/node/run dirs.
                for parent in (
                    path.parent,
                    path.parent.parent,
                    path.parent.parent.parent,
                ):
                    if parent == base or not parent.exists():
                        break
                    try:
                        parent.rmdir()
                    except OSError:
                        break
            except OSError:
                pass
        invalidate_stats_cache()

    def open_download(self, artifact: Artifact) -> ArtifactDownload:
        path = self._path(artifact)
        if not path.exists():
            raise FileNotFoundError(path)
        return ArtifactDownload(
            content_type=artifact.content_type,
            filename=artifact.name,
            size_bytes=artifact.size_bytes,
            path=path,
        )

    def signed_url(self, artifact: Artifact, *, expires_in: int = 300) -> str | None:
        return None

    def stats(self) -> dict[str, Any]:
        global _stats_cache
        now = time.monotonic()
        if _stats_cache is not None and now - _stats_cache[0] < _STATS_TTL_SECONDS:
            return dict(_stats_cache[1])
        result = self._stats_uncached()
        _stats_cache = (now, dict(result))
        return result

    def _stats_uncached(self) -> dict[str, Any]:
        base = _artifact_base_dir()
        if not base.exists():
            return {"backend": self.name, "base_dir": str(base), "file_count": 0, "bytes": 0}
        total_bytes = 0
        file_count = 0
        for p in base.rglob("*"):
            if p.is_file():
                try:
                    total_bytes += p.stat().st_size
                except OSError:
                    continue
                file_count += 1
        return {
            "backend": self.name,
            "base_dir": str(base),
            "file_count": file_count,
            "bytes": total_bytes,
        }

    def delete_run(self, run_id: str, org_id: str | None = None) -> None:
        base = _artifact_base_dir()
        candidates = [base / "runs" / run_id]
        if org_id:
            candidates.append(base / org_id / "runs" / run_id)
        for path in candidates:
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        invalidate_stats_cache()

    def upload_from_local(self, artifact: Artifact, local_path: Path) -> None:
        # Bytes are already on the local FS; nothing to do.
        return None

    def inspect(self, artifact: Artifact, *, verify_checksum: bool = False) -> ArtifactObjectInfo:
        path = self._path(artifact)
        if not path.is_file():
            raise FileNotFoundError(path)
        checksum: str | None = None
        if verify_checksum:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            checksum = digest.hexdigest()
        stat = path.stat()
        return ArtifactObjectInfo(
            key=artifact.storage_key,
            size_bytes=stat.st_size,
            checksum_sha256=checksum,
            last_modified=str(stat.st_mtime_ns),
        )

    def iter_objects(self, *, prefix: str = "") -> Iterator[ArtifactObjectInfo]:
        base = _artifact_base_dir()
        root = _resolve_local_path(prefix) if prefix else base
        if not root.exists():
            return
        candidates = [root] if root.is_file() else root.rglob("*")
        for path in candidates:
            if not path.is_file():
                continue
            stat = path.stat()
            yield ArtifactObjectInfo(
                key=path.relative_to(base).as_posix(),
                size_bytes=stat.st_size,
                last_modified=str(stat.st_mtime_ns),
            )

    def delete_keys(self, keys: Iterable[str]) -> None:
        for key in keys:
            try:
                _resolve_local_path(key).unlink(missing_ok=True)
            except (OSError, ValueError):
                continue
        invalidate_stats_cache()


# --- Registry ----------------------------------------------------------------

_STATS_TTL_SECONDS = 60.0
_stats_cache: tuple[float, dict[str, Any]] | None = None


def invalidate_stats_cache() -> None:
    """Drop the cached stats() snapshot.

    Called after backend mutations (delete / delete_run) so ops surfaces see
    deletions promptly. Writers outside this module (LocalArtifactStore in the
    engine) don't invalidate — new files may take up to the TTL to appear.
    """
    global _stats_cache
    _stats_cache = None


_BACKENDS: dict[str, ArtifactBackend] = {}


def register_backend(backend: ArtifactBackend) -> None:
    """Register a backend; subsequent ``get_backend(backend.name)`` returns it.

    Backends are singletons — the API process holds one instance per name
    for the lifetime of the process.
    """
    _BACKENDS[backend.name] = backend


def get_backend(name: str | None = None) -> ArtifactBackend:
    """Return the named backend, defaulting to the configured one.

    Lazy-registers ``LocalBackend`` so callers don't need a startup hook
    just to use the default.
    """
    chosen = (name or settings.artifact_storage_backend or "local").lower()
    if chosen not in _BACKENDS:
        if chosen == "local":
            register_backend(LocalBackend())
        else:
            raise KeyError(
                f"artifact backend {chosen!r} is not registered; "
                "call register_backend() at startup or set "
                "ARTIFACT_STORAGE_BACKEND=local"
            )
    return _BACKENDS[chosen]


def reset_backends_for_tests() -> None:
    """Drop the cached singletons. Tests that monkeypatch settings call this."""
    global _stats_cache
    _stats_cache = None
    _BACKENDS.clear()


__all__ = [
    "ArtifactBackend",
    "ArtifactDownload",
    "ArtifactObjectInfo",
    "LocalBackend",
    "get_backend",
    "register_backend",
    "reset_backends_for_tests",
]
