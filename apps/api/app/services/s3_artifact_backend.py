"""S3-compatible artifact backend.

Targets any S3-API-compatible object store (AWS S3, MinIO, Cloudflare R2,
Backblaze B2 with the S3 API). ``boto3`` is imported lazily because it's a
heavy dependency and shouldn't be required for local development.

Configuration via ``Settings``:
- ``artifact_storage_backend = "s3"``
- ``artifact_s3_bucket`` (required when backend is s3)
- ``artifact_s3_region`` (optional; AWS region or empty for non-AWS endpoints)
- ``artifact_s3_endpoint`` (optional; for MinIO/R2/B2 set to their endpoint URL)
- Credentials come from the standard boto3 chain (env vars, instance role,
  ``~/.aws/credentials``). The API never stores access keys itself.

Registration: ``register_s3_backend()`` is called from ``app.main`` on
startup when ``settings.artifact_storage_backend == "s3"``.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config import settings
from app.services.artifact_backends import (
    ArtifactDownload,
    ArtifactObjectInfo,
    register_backend,
)

if TYPE_CHECKING:
    from app.models import Artifact

log = logging.getLogger(__name__)


def browser_endpoint(cfg: Any = None) -> str:
    """The endpoint a presigned URL should point at for a browser.

    Falls back to the signing endpoint, so deployments whose S3 is already
    reachable from the browser are unaffected.
    """
    cfg = cfg if cfg is not None else settings
    public = (getattr(cfg, "artifact_s3_public_endpoint", "") or "").strip()
    if public:
        return public
    return (getattr(cfg, "artifact_s3_endpoint", "") or "").strip()


def _client(*, endpoint_override: str = "") -> Any:
    """Lazy boto3 client. Raises a friendly error when the dep is missing."""
    try:
        import boto3  # type: ignore[import-not-found]
        from botocore.config import Config  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - tested via skip
        raise RuntimeError(
            "boto3 is required for the S3 artifact backend. "
            "Install with: pip install boto3"
        ) from exc

    kwargs: dict[str, Any] = {}
    endpoint = (endpoint_override or settings.artifact_s3_endpoint or "").strip()
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    region = (settings.artifact_s3_region or "").strip()
    if region:
        kwargs["region_name"] = region
    # ``s3v4`` is required by most S3-compatible stores (MinIO, R2). AWS
    # accepts it too, so it's a safe default.
    kwargs["config"] = Config(signature_version="s3v4")
    return boto3.client("s3", **kwargs)


class S3Backend:
    name = "s3"

    def __init__(self, *, bucket: str | None = None) -> None:
        self.bucket = bucket or settings.artifact_s3_bucket
        if not self.bucket:
            raise RuntimeError(
                "ARTIFACT_S3_BUCKET must be set when ARTIFACT_STORAGE_BACKEND=s3"
            )
        self._client_cache: Any | None = None

    @property
    def client(self) -> Any:
        if self._client_cache is None:
            self._client_cache = _client()
        return self._client_cache

    def _key(self, artifact: Artifact) -> str:
        # ``storage_key`` is already namespaced under ``runs/{run_id}/...``
        # by the artifact store; we use it verbatim as the object key.
        return artifact.storage_key

    def delete(self, artifacts: Iterable[Artifact]) -> None:
        # S3 supports batched DELETE up to 1000 objects per request.
        keys: list[dict[str, str]] = []
        for row in artifacts:
            if row.storage_backend != self.name:
                continue
            keys.append({"Key": self._key(row)})
        if not keys:
            return
        try:
            for i in range(0, len(keys), 1000):
                chunk = keys[i : i + 1000]
                self.client.delete_objects(
                    Bucket=self.bucket, Delete={"Objects": chunk, "Quiet": True}
                )
        except Exception:  # noqa: BLE001
            log.exception("S3 artifact delete failed (%d keys)", len(keys))

    def open_download(self, artifact: Artifact) -> ArtifactDownload:
        key = self._key(artifact)
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.NoSuchKey as exc:  # type: ignore[attr-defined]
            raise FileNotFoundError(key) from exc
        body = obj["Body"]

        def chunks() -> Iterator[bytes]:
            try:
                while True:
                    chunk = body.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                body.close()

        return ArtifactDownload(
            content_type=artifact.content_type,
            filename=artifact.name,
            size_bytes=artifact.size_bytes,
            stream=chunks(),
        )

    def signed_url(self, artifact: Artifact, *, expires_in: int = 300) -> str | None:
        # Sign against the address the *browser* will use. Signing is scoped to
        # the host, so presigning with the internal endpoint and rewriting the
        # string afterwards would invalidate the signature — the client has to
        # be built against the public endpoint instead.
        public = (settings.artifact_s3_public_endpoint or "").strip()
        client = _client(endpoint_override=public) if public else self.client
        try:
            return client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": self._key(artifact),
                    "ResponseContentDisposition": f'attachment; filename="{artifact.name}"',
                    "ResponseContentType": artifact.content_type,
                },
                ExpiresIn=int(expires_in),
            )
        except Exception:  # noqa: BLE001
            log.exception("S3 presigned url failed for %s", artifact.id)
            return None

    def stats(self) -> dict[str, Any]:
        # Object count + total size requires a full bucket scan; deliberately
        # not computed here (would be expensive). Surface the bucket so the
        # ops dashboard can link out to the cloud console.
        return {
            "backend": self.name,
            "bucket": self.bucket,
            "endpoint": settings.artifact_s3_endpoint or None,
        }

    def delete_run(self, run_id: str, org_id: str | None = None) -> None:
        prefixes = [f"runs/{run_id}/"]
        if org_id:
            prefixes.append(f"{org_id}/runs/{run_id}/")
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            for prefix in prefixes:
                for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                    contents = page.get("Contents") or []
                    if not contents:
                        continue
                    keys = [{"Key": item["Key"]} for item in contents]
                    self.client.delete_objects(
                        Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True}
                    )
        except Exception:  # noqa: BLE001
            log.exception("S3 delete_run failed for %s", run_id)

    def upload_from_local(self, artifact: Artifact, local_path: Path) -> None:
        """Upload bytes written by the worker (local FS) to the S3 bucket.

        Workers cannot write directly to S3 (no per-worker credentials, warm
        pool reuse) so the API rehomes bytes here. Caller flips
        ``artifact.storage_backend = "s3"`` and may then delete the local
        scratch file.
        """
        key = self._key(artifact)
        try:
            from boto3.s3.transfer import TransferConfig  # type: ignore[import-not-found]

            transfer = TransferConfig(
                multipart_threshold=8 * 1024 * 1024,
                multipart_chunksize=8 * 1024 * 1024,
                max_concurrency=4,
                use_threads=True,
            )
            extra_args: dict[str, Any] = {"ContentType": artifact.content_type}
            if artifact.checksum_sha256:
                extra_args["Metadata"] = {"sha256": artifact.checksum_sha256}
            self.client.upload_file(
                str(local_path),
                self.bucket,
                key,
                ExtraArgs=extra_args,
                Config=transfer,
            )
        except Exception:  # noqa: BLE001
            log.exception("S3 upload failed for %s -> %s", local_path, key)
            raise

    def inspect(
        self, artifact: Artifact, *, verify_checksum: bool = False
    ) -> ArtifactObjectInfo:
        key = self._key(artifact)
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:  # noqa: BLE001
            response = getattr(exc, "response", {})
            code = str((response.get("Error") or {}).get("Code") or "")
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise FileNotFoundError(key) from exc
            raise
        checksum = (head.get("Metadata") or {}).get("sha256")
        if verify_checksum:
            download = self.open_download(artifact)
            digest = hashlib.sha256()
            for chunk in download.stream or ():
                digest.update(chunk)
            checksum = digest.hexdigest()
        modified = head.get("LastModified")
        return ArtifactObjectInfo(
            key=key,
            size_bytes=int(head.get("ContentLength") or 0),
            checksum_sha256=checksum,
            last_modified=modified.isoformat() if modified else None,
        )

    def iter_objects(self, *, prefix: str = "") -> Iterator[ArtifactObjectInfo]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents") or []:
                modified = item.get("LastModified")
                yield ArtifactObjectInfo(
                    key=str(item.get("Key") or ""),
                    size_bytes=int(item.get("Size") or 0),
                    last_modified=modified.isoformat() if modified else None,
                )

    def delete_keys(self, keys: Iterable[str]) -> None:
        pending = [{"Key": key} for key in keys if key]
        for index in range(0, len(pending), 1000):
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": pending[index : index + 1000], "Quiet": True},
            )


def register_s3_backend() -> S3Backend:
    """Construct and register the S3 backend (idempotent)."""
    backend = S3Backend()
    register_backend(backend)
    return backend


__all__ = ["S3Backend", "register_s3_backend"]
