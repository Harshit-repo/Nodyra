from datetime import UTC, datetime, timedelta
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import select

from app.config import settings
from app.models import Artifact, Run
from app.services import retention


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


# --- Task 10/11: pluggable artifact backend ----------------------------------

from collections.abc import Iterable

from app.services import artifact_backends
from app.services.artifact_backends import (
    ArtifactDownload,
    LocalBackend,
    get_backend,
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
    # And reports real content.
    (tmp_path / "runs" / "r1" / "n1").mkdir(parents=True)
    (tmp_path / "runs" / "r1" / "n1" / "a-x.bin").write_bytes(b"abcd")
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
        resp = await client.get(
            f"/artifacts/{artifact_id}/download", follow_redirects=False
        )
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


def test_s3_backend_requires_bucket_setting(monkeypatch) -> None:
    monkeypatch.setattr(settings, "artifact_s3_bucket", "")
    from app.services.s3_artifact_backend import S3Backend
    import pytest

    with pytest.raises(RuntimeError, match="ARTIFACT_S3_BUCKET"):
        S3Backend()




# --- S3 write-path: persist_artifact_refs rehomes local bytes to backend ---


async def test_persist_artifact_refs_rehomes_to_configured_backend(
    client: AsyncClient, monkeypatch
) -> None:
    from pathlib import Path as _P
    from app.services import artifacts as artifacts_svc
    from app.services.artifact_backends import register_backend, reset_backends_for_tests

    tmp_path = _P(settings.artifacts_dir)
    monkeypatch.setattr(settings, 'artifact_storage_backend', 'memory')
    reset_backends_for_tests()

    uploads: list[tuple[str, str]] = []

    class _RehomeBackend:
        name = 'memory'

        def delete(self, artifacts): pass
        def open_download(self, artifact): raise FileNotFoundError
        def signed_url(self, artifact, *, expires_in=300): return None
        def stats(self): return {'backend': self.name}
        def delete_run(self, run_id): pass

        def upload_from_local(self, artifact, local_path):
            uploads.append((artifact.id, str(local_path)))

    register_backend(_RehomeBackend())

    # Stage the local scratch file the worker would have written.
    run_id = 'r-rehome'
    node_dir = tmp_path / 'runs' / run_id / 'n1'
    node_dir.mkdir(parents=True)
    storage_key = f'runs/{run_id}/n1/aid-payload.bin'
    local_path = tmp_path / storage_key
    local_path.write_bytes(b'payload-bytes')

    ref = {
        '__noodle_artifact__': True,
        'artifact_id': 'aid',
        'run_id': run_id,
        'node_id': 'n1',
        'name': 'payload.bin',
        'kind': 'binary',
        'content_type': 'application/octet-stream',
        'size_bytes': 13,
        'storage_backend': 'local',
        'storage_key': storage_key,
    }
    await artifacts_svc.persist_artifact_refs(run_id, [ref])

    assert uploads == [('aid', str(local_path))]
    assert not local_path.exists(), 'local scratch should be reclaimed after upload'

    # Row was stored with the new backend.
    async with artifacts_svc.SessionLocal() as session:
        row = await session.get(Artifact, 'aid')
        assert row is not None
        assert row.storage_backend == 'memory'

    reset_backends_for_tests()


async def test_persist_artifact_refs_keeps_local_when_upload_fails(
    client: AsyncClient, monkeypatch
) -> None:
    from pathlib import Path as _P
    from app.services import artifacts as artifacts_svc
    from app.services.artifact_backends import register_backend, reset_backends_for_tests

    tmp_path = _P(settings.artifacts_dir)
    monkeypatch.setattr(settings, 'artifact_storage_backend', 'memory')
    reset_backends_for_tests()

    class _FailingBackend:
        name = 'memory'

        def delete(self, artifacts): pass
        def open_download(self, artifact): raise FileNotFoundError
        def signed_url(self, artifact, *, expires_in=300): return None
        def stats(self): return {'backend': self.name}
        def delete_run(self, run_id): pass
        def upload_from_local(self, artifact, local_path):
            raise RuntimeError('boom')

    register_backend(_FailingBackend())

    run_id = 'r-fail'
    storage_key = f'runs/{run_id}/n1/aid-payload.bin'
    local_path = tmp_path / storage_key
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b'x')

    ref = {
        '__noodle_artifact__': True,
        'artifact_id': 'aid-fail',
        'run_id': run_id,
        'node_id': 'n1',
        'name': 'payload.bin',
        'storage_backend': 'local',
        'storage_key': storage_key,
        'size_bytes': 1,
    }
    await artifacts_svc.persist_artifact_refs(run_id, [ref])

    assert local_path.exists(), 'local bytes must be kept when upload fails'
    async with artifacts_svc.SessionLocal() as session:
        row = await session.get(Artifact, 'aid-fail')
        assert row is not None
        assert row.storage_backend == 'local'

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
    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    results = {node["node_id"]: node for node in run["node_runs"]}
    dataset_ref = results["ds"]["output"]["main"]
    assert dataset_ref["__noodle_dataset__"] is True
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
    assert output["__noodle_dataset__"] is True
    assert output["artifact"]["__noodle_artifact__"] is True
    assert output["artifact"]["artifact_id"]
    assert output["row_count"] == 3
    assert output.get("_truncated") is not True

    resp = await client.post(
        f"/artifacts/{output['artifact']['artifact_id']}/query",
        json={"sql": "SELECT count(*) AS n FROM dataset", "limit": 10},
    )
    assert resp.status_code == 200
    assert resp.json()["rows"] == [{"n": 3}]

