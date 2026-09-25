"""Browser uploads must be readable by real runs, including S3 and tenant gates."""

import hashlib

import pytest
from sqlalchemy import select

from app.models import Artifact
from app.services import artifacts
from app.services.artifact_backends import ArtifactDownload
from app.tenancy import run_as_system


def uploaded_csv_graph(upload_id):
    return {
        "nodes": [
            {"id": "start", "type": "manual_trigger", "params": {}},
            {
                "id": "read",
                "type": "read_csv_file",
                "params": {
                    "file": upload_id,
                    "output_as_dataset": False,
                },
            },
        ],
        "edges": [
            {"source": "start", "source_port": "main", "target": "read", "target_port": "input"}
        ],
    }


class MemoryObjectStore:
    name = "s3"

    def __init__(self):
        self.objects = {}

    def upload_from_local(self, row, path):
        self.objects[row.storage_key] = path.read_bytes()

    def open_download(self, row):
        content = self.objects[row.storage_key]
        return ArtifactDownload(
            content_type=row.content_type,
            filename=row.name,
            stream=iter([content[:5], content[5:]]),
            size_bytes=len(content),
        )


@pytest.mark.parametrize("remote_storage", [False, True])
async def test_upload_then_run_csv_reader(client, monkeypatch, remote_storage):
    if remote_storage:
        from app.routers import artifacts as router

        backend = MemoryObjectStore()
        get_backend = artifacts.get_backend
        monkeypatch.setattr(router, "get_backend", lambda: backend)
        monkeypatch.setattr(
            artifacts,
            "get_backend",
            lambda name=None: backend if name == "s3" else get_backend(name),
        )
    uploaded = await client.post(
        "/artifacts/upload",
        files={
            "file": (
                "customers.csv",
                b"name,total\nAda,120\nGrace,250\n",
                "text/csv",
            )
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    upload = uploaded.json()
    if remote_storage:
        async with artifacts.SessionLocal() as session:
            row = await session.get(Artifact, upload["id"])
            assert not (artifacts.artifact_base_dir() / row.storage_key).exists()
    workflow = (await client.post("/workflows", json={"name": "Read customer upload"})).json()
    saved = await client.put(
        f"/workflows/{workflow['id']}", json={"graph": uploaded_csv_graph(upload["id"])}
    )
    assert saved.status_code == 200, saved.text
    started = await client.post(f"/workflows/{workflow['id']}/run", json={})
    assert started.status_code == 202, started.text
    run = (await client.get(f"/runs/{started.json()['run_id']}")).json()
    assert run["status"] == "success", run
    output = next(n for n in run["node_runs"] if n["node_id"] == "read")["output"]["main"]
    assert output["rows"] == [{"name": "Ada", "total": "120"}, {"name": "Grace", "total": "250"}]


async def test_upload_cannot_cross_organizations_even_in_system_context(client):
    upload = (
        await client.post(
            "/artifacts/upload", files={"file": ("secret.csv", b"value\n1", "text/csv")}
        )
    ).json()
    with run_as_system():
        async with artifacts.SessionLocal() as session:
            with pytest.raises(ValueError, match="unavailable in this organization"):
                await artifacts.prepare_uploaded_files(
                    session,
                    uploaded_csv_graph(upload["id"]),
                    run_id="foreign-run",
                    org_id="other-org",
                )
    assert not (artifacts.artifact_base_dir() / "other-org").exists()


async def test_corrupt_object_upload_is_rejected_before_execution(client, monkeypatch):
    upload = (
        await client.post(
            "/artifacts/upload", files={"file": ("metrics.csv", b"value\n1", "text/csv")}
        )
    ).json()
    async with artifacts.SessionLocal() as session:
        row = await session.scalar(select(Artifact).where(Artifact.id == upload["id"]))
        row.checksum_sha256 = hashlib.sha256(b"different").hexdigest()
        await session.commit()
        with pytest.raises(ValueError, match="integrity check"):
            await artifacts.prepare_uploaded_files(
                session, uploaded_csv_graph(upload["id"]), run_id="corrupt-run", org_id="default"
            )
    staged = artifacts.artifact_base_dir() / "default/runs/corrupt-run/_uploads"
    assert not any(p.is_file() for p in staged.rglob("*"))


async def test_deleted_upload_is_not_reused_from_previous_run(client):
    upload = (
        await client.post(
            "/artifacts/upload", files={"file": ("metrics.csv", b"value\n1", "text/csv")}
        )
    ).json()
    graph = uploaded_csv_graph(upload["id"])
    async with artifacts.SessionLocal() as session:
        await artifacts.prepare_uploaded_files(session, graph, run_id="old-run", org_id="default")
    assert (await client.delete(f"/artifacts/{upload['id']}")).status_code == 204
    async with artifacts.SessionLocal() as session:
        with pytest.raises(ValueError, match="unavailable"):
            await artifacts.prepare_uploaded_files(
                session, graph, run_id="new-run", org_id="default"
            )


async def test_cached_dataset_is_rehydrated_from_s3_for_retry(client, monkeypatch):

    backend = MemoryObjectStore()
    original_backend = artifacts.get_backend
    monkeypatch.setattr(
        artifacts,
        "get_backend",
        lambda name=None: backend if name == "s3" else original_backend(name),
    )
    from app.config import settings

    monkeypatch.setattr(settings, "artifact_storage_backend", "s3")
    graph = {
        "nodes": [
            {"id": "start", "type": "manual_trigger", "params": {"data": [{"name": "Ada"}]}},
            {"id": "dataset", "type": "records_to_dataset", "params": {}},
            {"id": "fail", "type": "code", "params": {"code": "raise ValueError('needs review')"}},
            {"id": "records", "type": "dataset_to_records", "params": {}},
        ],
        "edges": [
            {"source": a, "source_output": "main", "target": b, "target_input": "input"}
            for a, b in [("start", "dataset"), ("dataset", "fail"), ("fail", "records")]
        ],
    }
    wid = (await client.post("/workflows", json={"name": "Dataset retry"})).json()["id"]
    await client.put(f"/workflows/{wid}", json={"graph": graph})
    failed = (await client.post(f"/workflows/{wid}/run", json={})).json()["run_id"]
    assert (await client.get(f"/runs/{failed}")).json()["status"] == "error"
    assert backend.objects, "First attempt must have persisted the dataset to object storage"
    graph["nodes"][2]["params"]["code"] = "output = input"
    await client.put(f"/workflows/{wid}", json={"graph": graph})
    retried = await client.post(f"/runs/{failed}/retry", json={})
    assert retried.is_success, retried.text
    run = (await client.get(f"/runs/{retried.json()['run_id']}")).json()
    assert run["status"] == "success", run
    output = next(n for n in run["node_runs"] if n["node_id"] == "records")["output"]["main"]
    assert output == [{"name": "Ada"}]
