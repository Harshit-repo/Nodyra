"""Bounded file transfer across the sandbox's isolated filesystem boundary."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import PurePosixPath
from typing import Any

from app.services.artifacts import collect_artifact_refs, make_artifact_store

CONTAINER_ARTIFACTS_DIR = "/tmp/nodyra-artifacts"


class SandboxArtifacts:
    def __init__(self, container: Any, run_id: str, org_id: str):
        self.container = container
        self.store = make_artifact_store(run_id, org_id=org_id)
        self.transferred = False
        self._received: dict[str, dict] = {}
        self._pending: dict[str, dict] = {}

    def upload_messages(self):
        root = self.store._path_for_key(f"{self.store.key_prefix}runs/{self.store.run_id}")
        if not root.is_dir():
            return
        paths = (
            (kind, path)
            for kind in ("_uploads", "_inputs")
            for path in sorted((root / kind).rglob("*"))
        )
        for kind, path in paths:
            if not path.is_file() or path.is_symlink() or path.name.startswith("."):
                continue
            size = path.stat().st_size
            digest = hashlib.sha256()
            offset = 0
            with path.open("rb") as source:
                while True:
                    chunk = source.read(256 * 1024)
                    if not chunk and offset < size:
                        raise RuntimeError("Uploaded file changed during sandbox transfer")
                    digest.update(chunk)
                    final = offset + len(chunk) == size
                    yield {
                        "type": "artifact_input",
                        "input_kind": "artifact" if kind == "_inputs" else "upload",
                        "request_id": self.store.run_id,
                        "artifact_key_prefix": self.store.key_prefix.strip("/"),
                        "artifact_id": path.parent.name,
                        "name": path.name,
                        "offset": offset,
                        "size_bytes": size,
                        "chunk": base64.b64encode(chunk).decode("ascii"),
                        "final": final,
                        "checksum_sha256": digest.hexdigest() if final else None,
                    }
                    offset += len(chunk)
                    self.transferred = True
                    if final:
                        break

    def receive(self, value: Any) -> None:
        for ref in collect_artifact_refs(value):
            if ref.get("run_id") != self.store.run_id:
                continue
            artifact_id = str(ref.get("artifact_id") or "")
            if artifact_id in self._received:
                if self._received[artifact_id] != ref:
                    raise ValueError("Sandbox changed completed artifact metadata")
                continue
            if self.store.max_count and len(self._received) >= self.store.max_count:
                raise ValueError("Sandbox exceeded the run artifact count limit")
            key = str(ref.get("storage_key") or "")
            prefix = f"{self.store.key_prefix}runs/{self.store.run_id}/"
            if not key.startswith(prefix) or ".." in PurePosixPath(key).parts or "\\" in key:
                raise ValueError("Sandbox artifact key must belong to its assigned run")
            size = int(ref.get("size_bytes") or 0)
            if size < 0 or (self.store.max_bytes and size > self.store.max_bytes):
                raise ValueError("Sandbox artifact exceeds the run file limit")
            raise ValueError("Sandbox reported an artifact before its file transfer completed")

    def receive_chunk(self, message: dict) -> None:
        ref = message["ref"]
        artifact_id = str(ref.get("artifact_id") or "")
        key = str(ref.get("storage_key") or "")
        prefix = f"{self.store.key_prefix}runs/{self.store.run_id}/"
        if (
            ref.get("run_id") != self.store.run_id
            or not key.startswith(prefix)
            or ".." in PurePosixPath(key).parts
            or "\\" in key
        ):
            raise ValueError("Sandbox artifact key must belong to its assigned run")
        if artifact_id in self._received:
            raise ValueError("Sandbox sent a duplicate completed artifact")
        if artifact_id not in self._pending:
            if (
                self.store.max_count
                and len(self._received) + len(self._pending) >= self.store.max_count
            ):
                raise ValueError("Sandbox exceeded the run artifact count limit")
            self._pending[artifact_id] = ref
        elif self._pending[artifact_id] != ref:
            raise ValueError("Sandbox changed artifact metadata during transfer")
        size = int(ref.get("size_bytes") or 0)
        offset = int(message["offset"])
        chunk = base64.b64decode(message["chunk"], validate=True)
        if (
            size < 0
            or offset < 0
            or len(chunk) > 256 * 1024
            or offset + len(chunk) > size
            or (self.store.max_bytes and size > self.store.max_bytes)
        ):
            raise ValueError("Sandbox artifact exceeds the run file limit")
        destination = self.store.path_for_ref(ref)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.transfer")
        if offset and (not temporary.is_file() or temporary.stat().st_size != offset):
            raise ValueError("Sandbox artifact chunks arrived out of order")
        with temporary.open("ab" if offset else "wb") as target:
            target.write(chunk)
        self.transferred = True
        if message.get("final"):
            with temporary.open("rb") as source:
                checksum = hashlib.file_digest(source, "sha256").hexdigest()
            if temporary.stat().st_size != size or checksum != ref.get("checksum_sha256"):
                temporary.unlink(missing_ok=True)
                raise ValueError("Sandbox artifact checksum does not match")
            os.replace(temporary, destination)
            self._received[artifact_id] = ref
            self._pending.pop(artifact_id)

    def cleanup(self) -> None:
        for ref in self._pending.values():
            destination = self.store.path_for_ref(ref)
            destination.with_name(f".{destination.name}.transfer").unlink(missing_ok=True)
