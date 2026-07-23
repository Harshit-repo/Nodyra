"""Tests for POST /artifacts/upload — browser file upload endpoint."""

from __future__ import annotations

import io
from pathlib import Path

from httpx import AsyncClient


async def test_upload_artifact_returns_artifact_info(client: AsyncClient) -> None:
    content = b"col1,col2\nval1,val2\n"
    resp = await client.post(
        "/artifacts/upload",
        files={"file": ("test.csv", io.BytesIO(content), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] == "test.csv"
    assert data["content_type"] == "text/csv"
    assert data["size_bytes"] == len(content)
    assert data["kind"] == "upload"
    assert "id" in data


async def test_upload_artifact_missing_file_returns_422(client: AsyncClient) -> None:
    resp = await client.post("/artifacts/upload")
    assert resp.status_code == 422


async def test_upload_artifact_can_be_downloaded(client: AsyncClient) -> None:
    content = b"hello upload"
    upload_resp = await client.post(
        "/artifacts/upload",
        files={"file": ("hello.txt", io.BytesIO(content), "text/plain")},
    )
    assert upload_resp.status_code == 200
    artifact_id = upload_resp.json()["id"]

    download_resp = await client.get(f"/artifacts/{artifact_id}/download")
    assert download_resp.status_code == 200
    assert download_resp.content == content


async def test_upload_artifact_rehomes_to_configured_backend(
    client: AsyncClient, monkeypatch
) -> None:
    from app.config import settings
    from app.models import Artifact
    from app.services import artifacts as artifacts_svc
    from app.services.artifact_backends import register_backend, reset_backends_for_tests

    monkeypatch.setattr(settings, "artifact_storage_backend", "memory")
    reset_backends_for_tests()
    uploads: list[tuple[str, bytes, str]] = []

    class _MemoryBackend:
        name = "memory"

        def delete(self, artifacts):
            return None

        def open_download(self, artifact):
            raise FileNotFoundError

        def signed_url(self, artifact, *, expires_in=300):
            return None

        def stats(self):
            return {"backend": self.name}

        def delete_run(self, run_id, org_id=None):
            return None

        def upload_from_local(self, artifact, local_path):
            uploads.append((artifact.id, Path(local_path).read_bytes(), artifact.storage_key))

    register_backend(_MemoryBackend())

    try:
        content = b"remote upload"
        upload_resp = await client.post(
            "/artifacts/upload",
            files={"file": ("remote.txt", io.BytesIO(content), "text/plain")},
        )
        assert upload_resp.status_code == 200
        artifact_id = upload_resp.json()["id"]

        assert len(uploads) == 1
        uploaded_id, uploaded_content, uploaded_key = uploads[0]
        assert uploaded_id == artifact_id
        assert uploaded_content == content
        async with artifacts_svc.SessionLocal() as session:
            row = await session.get(Artifact, artifact_id)
            assert row is not None
            assert row.storage_backend == "memory"
            assert row.storage_key == uploaded_key
            assert not (Path(settings.artifacts_dir) / row.storage_key).exists()
    finally:
        reset_backends_for_tests()


async def test_artifact_reconcile_is_dry_run_first_and_repairs_explicitly(
    client: AsyncClient,
) -> None:
    from app.config import settings
    from app.models import Artifact
    from app.services import artifacts as artifacts_svc
    from app.services.artifact_backends import reset_backends_for_tests

    reset_backends_for_tests()
    try:
        upload = await client.post(
            "/artifacts/upload",
            files={"file": ("integrity.txt", io.BytesIO(b"trusted"), "text/plain")},
        )
        assert upload.status_code == 200
        artifact_id = upload.json()["id"]

        async with artifacts_svc.SessionLocal() as session:
            artifact = await session.get(Artifact, artifact_id)
            assert artifact is not None
            artifact_path = Path(settings.artifacts_dir) / artifact.storage_key
        artifact_path.unlink()
        orphan_path = Path(settings.artifacts_dir) / "orphan.bin"
        orphan_path.write_bytes(b"orphan")

        dry_run = await client.post(
            "/ops/artifacts/reconcile",
            params={"verify_checksums": "true"},
        )
        assert dry_run.status_code == 200, dry_run.text
        assert dry_run.json()["counts"]["missing"] >= 1
        assert dry_run.json()["counts"]["orphan"] >= 1
        assert dry_run.json()["deleted_orphans"] == 0
        assert orphan_path.exists()

        repair = await client.post(
            "/ops/artifacts/reconcile",
            params={
                "verify_checksums": "true",
                "repair_metadata": "true",
                "delete_orphans": "true",
            },
        )
        assert repair.status_code == 200, repair.text
        assert repair.json()["deleted_orphans"] >= 1
        assert not orphan_path.exists()

        async with artifacts_svc.SessionLocal() as session:
            artifact = await session.get(Artifact, artifact_id)
            assert artifact is not None
            assert artifact.artifact_metadata["integrity"]["status"] == "missing"
    finally:
        reset_backends_for_tests()


async def test_artifact_reconcile_rejects_non_key_prefix(client: AsyncClient) -> None:
    response = await client.post(
        "/ops/artifacts/reconcile",
        params={"prefix": "https://attacker.invalid/object"},
    )
    assert response.status_code == 400
    assert "relative object key prefix" in response.json()["detail"]
