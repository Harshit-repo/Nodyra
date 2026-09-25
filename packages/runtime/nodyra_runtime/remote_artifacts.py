"""Artifact store for remote runners.

A remote runner can't write to the API's filesystem, so artifact bytes are
POSTed to ``/runner-pools/artifact-upload`` with the runner token. We subclass
``LocalArtifactStore`` so the bytes are also written to a local temp dir —
that lets a *downstream* node on the same runner read an upstream artifact
during the same run — and additionally upload each write to the API, which is
the durable home for the bytes and the source for UI downloads.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import httpx

from nodyra.artifacts import LocalArtifactStore, sanitize_name


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
        key_prefix: str = "",
    ) -> None:
        local_base = base_dir or Path(tempfile.gettempdir()) / "nodyra-runner-artifacts"
        super().__init__(
            local_base,
            run_id,
            max_bytes=max_bytes,
            max_count=max_count,
            key_prefix=key_prefix,
        )
        self._upload_url = upload_url
        self._runner_token = runner_token
        self._uploaded: set[str] = set()

    def cleanup(self) -> None:
        """Remove this completed run's local files; uploaded bytes live at the API."""
        runs = (self.base_dir / self.key_prefix / "runs").resolve()
        directory = (runs / self.run_id).resolve()
        if directory.parent != runs:
            raise ValueError("Refusing to clean a directory outside this run's scratch space")
        if directory.is_dir():
            shutil.rmtree(directory)

    def _fetch_input(self, artifact_id: str, kind: str) -> Path:
        directory = (
            self.upload_path(artifact_id)
            if kind == "upload"
            else self.input_path({"artifact_id": artifact_id}).parent
        )
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / f".{uuid.uuid4().hex}.download"
        url = self._upload_url.rsplit("/", 1)[0] + "/artifacts/input"
        try:
            with httpx.stream(
                "GET",
                url,
                headers={"Authorization": f"Bearer {self._runner_token}"},
                params={"run_id": self.run_id, "artifact_id": artifact_id, "input_kind": kind},
                timeout=60,
            ) as response:
                response.raise_for_status()
                name = sanitize_name(response.headers.get("X-Nodyra-Artifact-Name"))
                expected = response.headers.get("X-Nodyra-Checksum-SHA256")
                digest = hashlib.sha256()
                size = 0
                with temporary.open("wb") as output:
                    for chunk in response.iter_bytes(64 * 1024):
                        size += len(chunk)
                        if self.max_bytes and size > self.max_bytes:
                            raise ValueError("Downloaded input exceeds the run file limit")
                        digest.update(chunk)
                        output.write(chunk)
                if not expected or digest.hexdigest() != expected:
                    raise ValueError("Downloaded input checksum does not match")
                destination = directory / name
                os.replace(temporary, destination)
                return destination
        finally:
            temporary.unlink(missing_ok=True)

    def read_upload(self, artifact_id: str) -> tuple[bytes, str]:
        try:
            return super().read_upload(artifact_id)
        except FileNotFoundError:
            path = self._fetch_input(artifact_id, "upload")
            return path.read_bytes(), path.name

    def path_for_ref(self, ref: dict[str, Any]) -> Path:
        path = (
            super().path_for_ref(ref) if ref.get("run_id") == self.run_id else self.input_path(ref)
        )
        if not path.is_file():
            path = self._fetch_input(ref["artifact_id"], "artifact")
        return path

    def ensure_uploaded(self, ref: dict[str, Any]) -> None:
        if ref.get("run_id") != self.run_id or ref["artifact_id"] in self._uploaded:
            return
        with super().path_for_ref(ref).open("rb") as source:
            httpx.post(
                self._upload_url,
                headers={"Authorization": f"Bearer {self._runner_token}"},
                params={
                    name: ref[name]
                    for name in (
                        "run_id",
                        "node_id",
                        "artifact_id",
                        "name",
                        "storage_key",
                        "size_bytes",
                        "content_type",
                        "kind",
                    )
                },
                files={"data": (ref["name"], source, ref["content_type"])},
                timeout=60,
            ).raise_for_status()
        self._uploaded.add(ref["artifact_id"])

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
            self._uploaded.add(ref["artifact_id"])
        except Exception as exc:  # noqa: BLE001 - surface upload failure to the node
            raise RuntimeError(f"artifact upload failed for {ref['name']!r}: {exc}") from exc
        return ref
