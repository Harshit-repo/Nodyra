"""write_bytes stamps a sha256 checksum into the artifact ref."""

import hashlib

from nodyra.artifacts import LocalArtifactStore
from nodyra.context import current_node_id


def test_write_bytes_stamps_checksum(tmp_path):
    token = current_node_id.set("node-1")
    try:
        store = LocalArtifactStore(tmp_path, run_id="run-1")
        payload = b"hello artifact"
        ref = store.write_bytes(payload, name="a.txt", content_type="text/plain")
    finally:
        current_node_id.reset(token)
    assert ref["checksum_sha256"] == hashlib.sha256(payload).hexdigest()
