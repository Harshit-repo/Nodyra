"""Receive bounded upload frames through the sandbox's existing stdin channel."""

import base64
import hashlib
import os
import re
from pathlib import Path

from nodyra.artifacts import LocalArtifactStore, sanitize_name

ARTIFACTS_DIR = "/tmp/nodyra-artifacts"
MAX_FRAME_BYTES = 256 * 1024


def receive_upload(message: dict, *, base_dir: str | Path = ARTIFACTS_DIR) -> None:
    run_id = str(message.get("request_id") or "")
    prefix = str(message.get("artifact_key_prefix") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,128}", prefix
    ):
        raise ValueError("Invalid upload run or organization id")
    size = int(message["size_bytes"])
    offset = int(message["offset"])
    limit = int(os.environ.get("NODYRA_MAX_INPUT_BYTES", str(50 * 1024 * 1024)))
    if size < 0 or (limit > 0 and size > limit) or offset < 0:
        raise ValueError("Uploaded file exceeds the runtime file limit")
    payload = base64.b64decode(message["chunk"], validate=True)
    if len(payload) > MAX_FRAME_BYTES or offset + len(payload) > size:
        raise ValueError("Uploaded file chunk exceeds its declared size")
    store = LocalArtifactStore(base_dir, run_id, key_prefix=prefix)
    if message.get("input_kind") == "artifact":
        destination = store.input_path(
            {"artifact_id": message["artifact_id"], "name": message["name"]}
        )
    else:
        destination = store.upload_path(message["artifact_id"]) / sanitize_name(message["name"])
    temporary = destination.with_name(f".{destination.name}.input")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if offset and (not temporary.is_file() or temporary.stat().st_size != offset):
        raise ValueError("Uploaded file chunks arrived out of order")
    with temporary.open("ab" if offset else "wb") as target:
        target.write(payload)
    if message.get("final"):
        with temporary.open("rb") as source:
            checksum = hashlib.file_digest(source, "sha256").hexdigest()
        if temporary.stat().st_size != size or checksum != message.get("checksum_sha256"):
            temporary.unlink(missing_ok=True)
            raise ValueError("Uploaded file checksum or size does not match")
        os.replace(temporary, destination)
