import hashlib
from contextlib import contextmanager

import httpx
import pytest

from nodyra_runtime.remote_artifacts import RemoteArtifactStore


def store(tmp_path):
    return RemoteArtifactStore(
        "https://api.example/runner-pools/artifact-upload",
        "synthetic-token",
        "run-a",
        base_dir=tmp_path,
        key_prefix="default",
        max_bytes=1024,
    )


def download_response(monkeypatch, payload=b"name\nAda", *, checksum=None):
    calls = []

    @contextmanager
    def stream(method, url, **kwargs):
        calls.append((method, url, kwargs))
        yield httpx.Response(
            200,
            content=payload,
            request=httpx.Request(method, url),
            headers={
                "X-Nodyra-Artifact-Name": "customers.csv",
                "X-Nodyra-Checksum-SHA256": checksum or hashlib.sha256(payload).hexdigest(),
            },
        )

    monkeypatch.setattr(httpx, "stream", stream)
    return calls


def test_remote_upload_input_is_downloaded_once_and_verified(tmp_path, monkeypatch):
    calls = download_response(monkeypatch)
    remote = store(tmp_path)
    assert remote.read_upload("a" * 32) == (b"name\nAda", "customers.csv")
    assert remote.read_upload("a" * 32) == (b"name\nAda", "customers.csv")
    assert len(calls) == 1
    assert calls[0][1] == "https://api.example/runner-pools/artifacts/input"
    assert calls[0][2]["params"]["run_id"] == "run-a"


def test_remote_cached_artifact_is_rehydrated_for_this_run(tmp_path, monkeypatch):
    calls = download_response(monkeypatch)
    remote = store(tmp_path)
    ref = {"artifact_id": "b" * 32, "run_id": "previous-run", "name": "customers.csv"}
    assert remote.path_for_ref(ref).read_bytes() == b"name\nAda"
    assert calls[0][2]["params"]["input_kind"] == "artifact"
    assert "/run-a/_inputs/" in remote.path_for_ref(ref).as_posix()


@pytest.mark.parametrize("problem", ["checksum", "size"])
def test_remote_corrupt_or_oversized_input_is_not_published(tmp_path, monkeypatch, problem):
    download_response(
        monkeypatch, b"x" * 2048 if problem == "size" else b"corrupt", checksum="0" * 64
    )
    with pytest.raises(ValueError):
        store(tmp_path).read_upload("a" * 32)
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_file_backed_outputs_are_uploaded_before_their_node_event(tmp_path, monkeypatch):
    remote = store(tmp_path)
    posted = []

    def post(url, **kwargs):
        source = kwargs["files"]["data"][1]
        posted.append(source.read())
        return httpx.Response(201, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    ref = {
        "artifact_id": "a" * 32,
        "run_id": "run-a",
        "node_id": "export",
        "name": "data.parquet",
        "storage_key": "default/runs/run-a/export/data.parquet",
        "size_bytes": 6,
        "content_type": "application/octet-stream",
        "kind": "dataset",
    }
    path = remote._path_for_key(ref["storage_key"])
    path.parent.mkdir(parents=True)
    path.write_bytes(b"report")
    remote.ensure_uploaded(ref)
    remote.ensure_uploaded(ref)
    assert posted == [b"report"]


def test_remote_cleanup_removes_only_the_completed_run(tmp_path):
    remote = store(tmp_path)
    own = remote.upload_path("a" * 32) / "input.txt"
    own.parent.mkdir(parents=True)
    own.write_bytes(b"own")
    other = tmp_path / "default/runs/other-run/keep.txt"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"other")
    remote.cleanup()
    assert not own.exists()
    assert other.read_bytes() == b"other"
