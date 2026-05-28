"""Artifact store for remote runners.

A remote runner can't write to the API's filesystem, so artifact bytes are
POSTed to ``/runner-pools/artifact-upload`` with the runner token. We subclass
``LocalArtifactStore`` so the bytes are also written to a local temp dir —
that lets a *downstream* node on the same runner read an upstream artifact
during the same run — and additionally upload each write to the API, which is
the durable home for the bytes and the source for UI downloads.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import httpx

from noodle.artifacts import LocalArtifactStore


class RemoteArtifactStore(LocalArtifactStore):
    def __init__(
        self,
        upload_url: str,
        runner_token: str,
        run_id: str,
        *,
        max_bytes: int = 0,
        max_count: int = 0,
        base_dir: str | Path | None = None,
    ) -> None:
        local_base = base_dir or Path(tempfile.gettempdir()) / "noodle-runner-artifacts"
        super().__init__(local_base, run_id, max_bytes=max_bytes, max_count=max_count)
        self._upload_url = upload_url
        self._runner_token = runner_token

    def write_bytes(
        self,
        data: bytes | bytearray | memoryview,
        *,
        name: str,
        content_type: str = "application/octet-stream",
        kind: str = "binary",
        metadata: dict[str, Any] | None = None,
        preview: Any = None,
    ) -> dict[str, Any]:
        # Write locally first to build the ref (storage_key, ids, preview).
        ref = super().write_bytes(
            data,
            name=name,
            content_type=content_type,
            kind=kind,
            metadata=metadata,
            preview=preview,
        )
        # Upload the same bytes to the API so they survive past this runner.
        try:
            httpx.post(
                self._upload_url,
                headers={"Authorization": f"Bearer {self._runner_token}"},
                params={
                    "run_id": ref["run_id"],
                    "node_id": ref["node_id"],
                    "artifact_id": ref["artifact_id"],
                    "name": ref["name"],
                    "content_type": content_type,
                    "kind": kind,
                    "storage_key": ref["storage_key"],
                    "size_bytes": ref["size_bytes"],
                },
                files={"data": (ref["name"], bytes(data), content_type)},
                timeout=60,
            ).raise_for_status()
        except Exception as exc:  # noqa: BLE001 - surface upload failure to the node
            raise RuntimeError(
                f"artifact upload failed for {ref['name']!r}: {exc}"
            ) from exc
        return ref
