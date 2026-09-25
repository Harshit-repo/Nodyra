import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import select

from app.config import settings
from app.models import Artifact, Run, Workflow
from app.services import retention


async def test_run_success_is_not_visible_until_artifact_metadata_commits(client, monkeypatch):
    """Slow storage must not leave a green run whose download returns 404."""
    from app.services import artifacts as artifacts_svc
    from app.services import runner

    entered = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    persist = artifacts_svc.persist_artifact_refs
    publish = runner.broker.publish

    async def slow_persist(*args, **kwargs):
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=15)
        await persist(*args, **kwargs)

    def record_event(run_id, event):
        if event.get("type") == "run_finished":
            finished.set()
        return publish(run_id, event)

    monkeypatch.setattr(artifacts_svc, "persist_artifact_refs", slow_persist)
    monkeypatch.setattr(runner.broker, "publish", record_event)
    monkeypatch.setattr(settings, "run_synchronously", False)
    wid = (
        await client.post("/templates/csv_clean_dedupe/instantiate", json={"name": "Atomic export"})
    ).json()["id"]
    rid = (await client.post(f"/workflows/{wid}/run", json={})).json()["run_id"]
    try:
        await asyncio.wait_for(entered.wait(), timeout=15)
        assert not finished.is_set(), "Terminal event escaped before artifacts were persisted"
        assert (await client.get(f"/runs/{rid}")).json()["status"] == "running"
    finally:
        release.set()
    await asyncio.wait_for(finished.wait(), timeout=15)
    run = (await client.get(f"/runs/{rid}")).json()
    assert run["status"] == "success"
    artifacts = (await client.get(f"/artifacts?run_id={rid}")).json()["items"]
    exported = next(a for a in artifacts if a["name"] == "cleaned.csv")
    download = await client.get(f"/artifacts/{exported['id']}/download")
    assert download.status_code == 200
    assert b"ada@example.com" in download.content


async def test_retention_prune_deletes_artifact_metadata_and_file(
    client: AsyncClient,
) -> None:
    # Produce a real artifact through the supported dataset path (code nodes no
    # longer write artifacts directly; artifacts flow as refs from dedicated
    # nodes such as records_to_dataset).
    await _make_dataset_run(client)

    async with retention.SessionLocal() as session:
        artifact = (await session.scalars(select(Artifact))).one()
        artifact_path = Path(settings.artifacts_dir) / artifact.storage_key
        assert artifact_path.exists()
        run = (await session.scalars(select(Run))).one()
        run.started_at = datetime.now(UTC) - timedelta(days=10)
        await session.commit()

    previous = settings.run_retention_days
    settings.run_retention_days = 1
    try:
        aged_out, capped_out = await retention.prune_old_runs()
    finally:
        settings.run_retention_days = previous

    assert aged_out == 1
    assert capped_out == 0
    assert not artifact_path.exists()
    async with retention.SessionLocal() as session:
        remaining = (await session.scalars(select(Artifact))).all()
    assert remaining == []


async def test_workflow_artifact_retention_prunes_artifacts_without_run(
    client: AsyncClient,
) -> None:
    workflow_id, artifact_id = await _make_dataset_run(client)

    async with retention.SessionLocal() as session:
        artifact = await session.get(Artifact, artifact_id)
        assert artifact is not None
        artifact_path = Path(settings.artifacts_dir) / artifact.storage_key
        assert artifact_path.exists()
        run_id = artifact.run_id
        assert run_id is not None
        artifact.created_at = datetime.now(UTC) - timedelta(days=10)
        workflow = await session.get(Workflow, workflow_id)
        assert workflow is not None
        workflow.artifact_retention_days = 1
        await session.commit()

    previous = settings.run_retention_days
    settings.run_retention_days = 0
    try:
        aged_out, capped_out = await retention.prune_old_runs()
    finally:
        settings.run_retention_days = previous

    assert aged_out == 0
    assert capped_out == 0
    assert not artifact_path.exists()
    async with retention.SessionLocal() as session:
        assert await session.get(Artifact, artifact_id) is None
        assert await session.get(Run, run_id) is not None


# --- Task 10/11: pluggable artifact backend ----------------------------------

from collections.abc import Iterable  # noqa: E402

from app.services.artifact_backends import (  # noqa: E402
    ArtifactDownload,
    LocalBackend,
    get_backend,
    invalidate_stats_cache,
    register_backend,
    reset_backends_for_tests,
)


def test_local_backend_is_default_and_stats_reports_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "artifacts_dir", str(tmp_path))
    reset_backends_for_tests()
    backend = get_backend()
    assert isinstance(backend, LocalBackend)
    # Stats survives an empty dir.
    stats = backend.stats()
    assert stats["backend"] == "local"
    assert stats["file_count"] == 0
    assert stats["bytes"] == 0
    # And reports real content. stats() is TTL-cached; files written outside
    # the backend's own mutation methods need an explicit invalidation.
    (tmp_path / "runs" / "r1" / "n1").mkdir(parents=True)
    (tmp_path / "runs" / "r1" / "n1" / "a-x.bin").write_bytes(b"abcd")
    invalidate_stats_cache()
    stats = backend.stats()
    assert stats["file_count"] == 1
    assert stats["bytes"] == 4
    reset_backends_for_tests()


def test_local_backend_signed_url_is_none() -> None:
    # Signals to the router that it must stream bytes rather than 307.
    assert LocalBackend().signed_url(object()) is None  # type: ignore[arg-type]


def test_get_backend_raises_for_unknown_name() -> None:
    reset_backends_for_tests()
    import pytest

    with pytest.raises(KeyError):
        get_backend("s3-mystery")
    reset_backends_for_tests()


class _RecordingBackend:
    """Test double that satisfies ``ArtifactBackend`` and records calls."""

    name = "memory"

    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.downloads: list[str] = []
        self.signed: list[tuple[str, int]] = []
        self.runs_deleted: list[str] = []

    def delete(self, artifacts: Iterable[Artifact]) -> None:
        for a in artifacts:
            self.deleted.append(a.id)

    def open_download(self, artifact: Artifact) -> ArtifactDownload:
        self.downloads.append(artifact.id)

        def chunks():
            yield b"hello-from-memory"

        return ArtifactDownload(
            content_type=artifact.content_type,
            filename=artifact.name,
            size_bytes=len(b"hello-from-memory"),
            stream=chunks(),
        )

    def signed_url(self, artifact: Artifact, *, expires_in: int = 300) -> str | None:
        self.signed.append((artifact.id, expires_in))
        return f"https://signed.example.test/{artifact.id}?ttl={expires_in}"

    def stats(self) -> dict:
        return {"backend": self.name, "objects": 0}

    def delete_run(self, run_id: str) -> None:
        self.runs_deleted.append(run_id)


async def test_router_redirects_when_backend_returns_signed_url(
    client: AsyncClient, monkeypatch
) -> None:
    """Backends with ``signed_url`` short-circuit the bytes path with a 307."""
    _workflow_id, artifact_id = await _make_dataset_run(client)

    from app.db import get_session as _get_session

    override = client._transport.app.dependency_overrides[_get_session]

    # Repoint that row's backend to our recorder and register the recorder.
    fake = _RecordingBackend()
    register_backend(fake)
    async for session in override():
        row = await session.get(Artifact, artifact_id)
        row.storage_backend = "memory"
        await session.commit()
        break

    try:
        resp = await client.get(f"/artifacts/{artifact_id}/download", follow_redirects=False)
        assert resp.status_code == 307
        assert "signed.example.test" in resp.headers["location"]
        assert fake.signed and fake.signed[0][0] == artifact_id
    finally:
        reset_backends_for_tests()


async def test_signed_url_endpoint_returns_null_for_local_backend(
    client: AsyncClient,
) -> None:
    """Local backend has no concept of pre-signed URLs; UI falls back to /download."""
    _workflow_id, artifact_id = await _make_dataset_run(client)
    resp = await client.get(f"/artifacts/{artifact_id}/url")
    assert resp.status_code == 200
    assert resp.json() == {"url": None, "expires_in": None}


async def test_download_artifact_supports_byte_ranges(client: AsyncClient) -> None:
    _workflow_id, artifact_id = await _make_dataset_run(client)

    full = await client.get(f"/artifacts/{artifact_id}/download")
    assert full.status_code == 200
    assert len(full.content) > 8

    partial = await client.get(
        f"/artifacts/{artifact_id}/download",
        headers={"Range": "bytes=0-7"},
    )
    assert partial.status_code == 206
    assert partial.headers["accept-ranges"] == "bytes"
    assert partial.headers["content-range"] == f"bytes 0-7/{len(full.content)}"
    assert partial.content == full.content[:8]


async def test_download_artifact_rejects_invalid_byte_range(client: AsyncClient) -> None:
    _workflow_id, artifact_id = await _make_dataset_run(client)

    full = await client.get(f"/artifacts/{artifact_id}/download")
    assert full.status_code == 200

    partial = await client.get(
        f"/artifacts/{artifact_id}/download",
        headers={"Range": f"bytes={len(full.content)}-"},
    )
    assert partial.status_code == 416
    assert partial.headers["content-range"] == f"bytes */{len(full.content)}"


def test_s3_backend_requires_bucket_setting(monkeypatch) -> None:
    monkeypatch.setattr(settings, "artifact_s3_bucket", "")
    import pytest

    from app.services.s3_artifact_backend import S3Backend

    with pytest.raises(RuntimeError, match="ARTIFACT_S3_BUCKET"):
        S3Backend()


# --- S3 write-path: persist_artifact_refs rehomes local bytes to backend ---


async def _seed_artifact_run(session_factory, run_id: str) -> None:
    """Create the production parent rows required by artifact metadata."""
    async with session_factory() as session:
        workflow = Workflow(
            name=f"Artifact parent {run_id}",
            draft_graph={"nodes": [], "edges": []},
        )
        session.add(workflow)
        await session.flush()
        session.add(Run(id=run_id, workflow_id=workflow.id, status="running"))
        await session.commit()


async def test_persist_artifact_refs_rehomes_to_configured_backend(
    client: AsyncClient, monkeypatch
) -> None:
    from pathlib import Path as _P

    from app.services import artifacts as artifacts_svc
    from app.services.artifact_backends import register_backend, reset_backends_for_tests

    tmp_path = _P(settings.artifacts_dir)
    monkeypatch.setattr(settings, "artifact_storage_backend", "memory")
    reset_backends_for_tests()

    uploads: list[tuple[str, str]] = []

    class _RehomeBackend:
        name = "memory"

        def delete(self, artifacts):
            pass

        def open_download(self, artifact):
            raise FileNotFoundError

        def signed_url(self, artifact, *, expires_in=300):
            return None

        def stats(self):
            return {"backend": self.name}

        def delete_run(self, run_id):
            pass

        def upload_from_local(self, artifact, local_path):
            uploads.append((artifact.id, str(local_path)))

    register_backend(_RehomeBackend())

    # Stage the local scratch file the worker would have written.
    run_id = "r-rehome"
    await _seed_artifact_run(artifacts_svc.SessionLocal, run_id)
    node_dir = tmp_path / "runs" / run_id / "n1"
    node_dir.mkdir(parents=True)
    storage_key = f"runs/{run_id}/n1/aid-payload.bin"
    local_path = tmp_path / storage_key
    local_path.write_bytes(b"payload-bytes")

    ref = {
        "__nodyra_artifact__": True,
        "artifact_id": "aid",
        "run_id": run_id,
        "node_id": "n1",
        "name": "payload.bin",
        "kind": "binary",
        "content_type": "application/octet-stream",
        "size_bytes": 13,
        "storage_backend": "local",
        "storage_key": storage_key,
    }
    await artifacts_svc.persist_artifact_refs(run_id, [ref])

    assert uploads == [("aid", str(local_path))]
    assert not local_path.exists(), "local scratch should be reclaimed after upload"

    # Row was stored with the new backend.
    async with artifacts_svc.SessionLocal() as session:
        row = await session.get(Artifact, "aid")
        assert row is not None
        assert row.storage_backend == "memory"

    reset_backends_for_tests()


async def test_persist_artifact_refs_keeps_local_when_upload_fails(
    client: AsyncClient, monkeypatch
) -> None:
    from pathlib import Path as _P

    from app.services import artifacts as artifacts_svc
    from app.services.artifact_backends import register_backend, reset_backends_for_tests

    tmp_path = _P(settings.artifacts_dir)
    monkeypatch.setattr(settings, "artifact_storage_backend", "memory")
    reset_backends_for_tests()

    class _FailingBackend:
        name = "memory"

        def delete(self, artifacts):
            pass

        def open_download(self, artifact):
            raise FileNotFoundError

        def signed_url(self, artifact, *, expires_in=300):
            return None

        def stats(self):
            return {"backend": self.name}

        def delete_run(self, run_id):
            pass

        def upload_from_local(self, artifact, local_path):
            raise RuntimeError("boom")

    register_backend(_FailingBackend())

    run_id = "r-fail"
    await _seed_artifact_run(artifacts_svc.SessionLocal, run_id)
    storage_key = f"runs/{run_id}/n1/aid-payload.bin"
    local_path = tmp_path / storage_key
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"x")

    ref = {
        "__nodyra_artifact__": True,
        "artifact_id": "aid-fail",
        "run_id": run_id,
        "node_id": "n1",
        "name": "payload.bin",
        "storage_backend": "local",
        "storage_key": storage_key,
        "size_bytes": 1,
    }
    await artifacts_svc.persist_artifact_refs(run_id, [ref])

    assert local_path.exists(), "local bytes must be kept when upload fails"
    async with artifacts_svc.SessionLocal() as session:
        row = await session.get(Artifact, "aid-fail")
        assert row is not None
        assert row.storage_backend == "local"

    reset_backends_for_tests()


# --- Dataset SQL explorer ----------------------------------------------------


async def _make_dataset_run(client: AsyncClient) -> tuple[str, str]:
    """Run a workflow that produces a Parquet-backed dataset.

    Returns ``(workflow_id, artifact_id)`` so callers can rerun the same graph
    under alternate output-cap settings.
    """
    workflow_id = (await client.post("/workflows", json={"name": "SQL"})).json()["id"]
    graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "rows",
                "type": "code",
                "params": {
                    "code": (
                        "output = [\n"
                        "  {'city': 'NYC', 'pop': 8},\n"
                        "  {'city': 'LA', 'pop': 4},\n"
                        "  {'city': 'NYC', 'pop': 9},\n"
                        "]"
                    )
                },
                "position": {"x": 200, "y": 0},
            },
            {
                "id": "ds",
                "type": "records_to_dataset",
                "params": {},
                "position": {"x": 400, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e0",
                "source": "t",
                "source_output": "main",
                "target": "rows",
                "target_input": "input",
            },
            {
                "id": "e1",
                "source": "rows",
                "source_output": "main",
                "target": "ds",
                "target_input": "input",
            },
        ],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})
    run_id = (await client.post(f"/workflows/{workflow_id}/run", json={})).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    results = {node["node_id"]: node for node in run["node_runs"]}
    dataset_ref = results["ds"]["output"]["main"]
    assert dataset_ref["__nodyra_dataset__"] is True
    return workflow_id, dataset_ref["artifact"]["artifact_id"]


async def test_dataset_sql_query_returns_rows(client: AsyncClient) -> None:
    _workflow_id, artifact_id = await _make_dataset_run(client)
    resp = await client.post(
        f"/artifacts/{artifact_id}/query",
        json={"sql": "SELECT city, SUM(pop) AS total FROM dataset GROUP BY city ORDER BY city"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["row_count"] == 2
    rows = {r["city"]: r["total"] for r in body["rows"]}
    assert rows == {"LA": 4, "NYC": 17}
    assert any(c["name"] == "total" for c in body["columns"])


async def test_dataset_sql_query_rejects_writes(client: AsyncClient) -> None:
    _workflow_id, artifact_id = await _make_dataset_run(client)
    resp = await client.post(
        f"/artifacts/{artifact_id}/query",
        json={"sql": "DROP TABLE dataset"},
    )
    assert resp.status_code == 400
    assert "SELECT" in resp.json()["detail"] or "disallowed" in resp.json()["detail"]


async def test_dataset_sql_query_rejects_non_dataset_artifact(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Not Dataset"})).json()["id"]
    graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 0, "y": 0},
            }
        ],
        "edges": [],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})
    run_id = (await client.post(f"/workflows/{workflow_id}/run", json={})).json()["run_id"]

    async with retention.SessionLocal() as session:
        artifact = Artifact(
            id="artifact_text_query",
            run_id=run_id,
            node_id="writer",
            name="hello.txt",
            kind="text",
            content_type="text/plain",
            size_bytes=5,
            storage_backend="local",
            storage_key="runs/run_non_dataset_query/writer/artifact_text_query-hello.txt",
            artifact_metadata={},
            preview="hello",
        )
        session.add(artifact)
        await session.commit()

    resp = await client.post(
        "/artifacts/artifact_text_query/query",
        json={"sql": "SELECT * FROM dataset", "limit": 10},
    )

    assert resp.status_code == 400
    assert "Parquet-backed datasets" in resp.json()["detail"]


async def test_dataset_sql_query_missing_artifact_is_404(client: AsyncClient) -> None:
    resp = await client.post(
        "/artifacts/missing-artifact/query",
        json={"sql": "SELECT * FROM dataset", "limit": 10},
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Artifact not found"


async def test_dataset_sql_query_rejects_file_access_functions(client: AsyncClient) -> None:
    _workflow_id, artifact_id = await _make_dataset_run(client)

    resp = await client.post(
        f"/artifacts/{artifact_id}/query",
        json={"sql": "SELECT * FROM read_csv('/etc/passwd')", "limit": 10},
    )

    assert resp.status_code == 400
    assert "disallowed" in resp.json()["detail"].lower()


async def test_dataset_query_blocks_external_file_read(client: AsyncClient) -> None:
    """DSQ-1: the external-access latch blocks file reads even via table

    functions the keyword regex doesn't enumerate (e.g. parquet_metadata).
    """
    _workflow_id, artifact_id = await _make_dataset_run(client)
    resp = await client.post(
        f"/artifacts/{artifact_id}/query",
        json={"sql": "SELECT * FROM parquet_metadata('/etc/hosts')", "limit": 10},
    )
    assert resp.status_code == 400, resp.text
    # Either the regex (defence-in-depth) or the DuckDB permission error from the
    # latch — both are acceptable; the file must never be read.
    assert "TOPSECRET" not in resp.text


async def test_dataset_sql_query_caps_rows(client: AsyncClient) -> None:
    _workflow_id, artifact_id = await _make_dataset_run(client)
    resp = await client.post(
        f"/artifacts/{artifact_id}/query",
        json={"sql": "SELECT * FROM dataset", "limit": 2},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["row_count"] == 2
    assert body["truncated"] is True


async def test_dataset_ref_survives_small_output_cap(client: AsyncClient) -> None:
    workflow_id, _artifact_id = await _make_dataset_run(client)

    previous = settings.max_output_bytes
    settings.max_output_bytes = 512
    try:
        run_id = (await client.post(f"/workflows/{workflow_id}/run", json={})).json()["run_id"]
        run = (await client.get(f"/runs/{run_id}")).json()
    finally:
        settings.max_output_bytes = previous

    output = next(nr for nr in run["node_runs"] if nr["node_id"] == "ds")["output"]["main"]
    assert output["__nodyra_dataset__"] is True
    assert output["artifact"]["__nodyra_artifact__"] is True
    assert output["artifact"]["artifact_id"]
    assert output["row_count"] == 3
    assert output.get("_truncated") is not True

    resp = await client.post(
        f"/artifacts/{output['artifact']['artifact_id']}/query",
        json={"sql": "SELECT count(*) AS n FROM dataset", "limit": 10},
    )
    assert resp.status_code == 200
    assert resp.json()["rows"] == [{"n": 3}]
