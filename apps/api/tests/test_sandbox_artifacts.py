import base64
import hashlib
from unittest.mock import Mock

import pytest

from app.config import settings
from app.services.sandbox_artifacts import SandboxArtifacts


@pytest.fixture
def transfer(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifacts_dir", str(tmp_path))
    return SandboxArtifacts(Mock(), "run-1", "org-1")


def reference():
    return {
        "__nodyra_artifact__": True,
        "version": 1,
        "artifact_id": "a" * 32,
        "run_id": "run-1",
        "node_id": "export",
        "name": "report.csv",
        "storage_key": "org-1/runs/run-1/export/report.csv",
        "size_bytes": 6,
        "checksum_sha256": hashlib.sha256(b"report").hexdigest(),
    }


def test_sandbox_report_is_copied_and_verified_before_persistence(transfer):
    ref = reference()
    transfer.receive_chunk(
        {"ref": ref, "offset": 0, "chunk": base64.b64encode(b"report").decode(), "final": True}
    )
    transfer.receive({"outputs": {"main": ref}})
    assert transfer.store.path_for_ref(ref).read_bytes() == b"report"
    assert transfer.transferred
    transfer.receive(ref)
    transfer.container.get_archive.assert_not_called()


@pytest.mark.parametrize("problem", ["missing_chunks", "checksum", "tenant", "size"])
def test_unsafe_sandbox_transfer_is_rejected(transfer, problem):
    ref = reference()
    if problem == "checksum":
        ref["checksum_sha256"] = "0" * 64
    elif problem == "tenant":
        ref["storage_key"] = "other-org/runs/run-1/report.csv"
    elif problem == "size":
        ref["size_bytes"] = 1
    with pytest.raises(ValueError):
        transfer.receive_chunk(
            {
                "ref": ref,
                "offset": 3 if problem == "missing_chunks" else 0,
                "chunk": base64.b64encode(b"report").decode(),
                "final": True,
            }
        )
    transfer.cleanup()
    assert not any(p.is_file() for p in transfer.store.base_dir.rglob("*"))


def test_upload_frames_round_trip_without_writing_the_readonly_rootfs(transfer, tmp_path):
    from nodyra.artifacts import LocalArtifactStore
    from nodyra_runtime.artifact_inputs import receive_upload

    path = transfer.store.upload_path("a" * 32) / "customers.csv"
    path.parent.mkdir(parents=True)
    payload = b"name\n" + b"Ada\n" * 200_000
    path.write_bytes(payload)
    messages = list(transfer.upload_messages())
    assert len(messages) > 1
    destination = tmp_path / "runtime-tmpfs"
    for message in messages:
        receive_upload(message, base_dir=destination)
    runtime_store = LocalArtifactStore(destination, "run-1", key_prefix="org-1")
    assert runtime_store.read_upload("a" * 32) == (payload, "customers.csv")
    transfer.container.put_archive.assert_not_called()


def test_upload_frames_reject_missing_chunks_and_bad_checksums(transfer, tmp_path):
    from nodyra_runtime.artifact_inputs import receive_upload

    path = transfer.store.upload_path("a" * 32) / "input.txt"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x" * 300_000)
    messages = list(transfer.upload_messages())
    root = tmp_path / "receiver"
    with pytest.raises(ValueError, match="out of order"):
        receive_upload(messages[-1], base_dir=root)
    receive_upload(messages[0], base_dir=root)
    with pytest.raises(ValueError, match="checksum"):
        receive_upload({**messages[-1], "checksum_sha256": "0" * 64}, base_dir=root)
