"""Phase F: org-namespaced artifact storage.

New artifact bytes land under ``{org_id}/...`` so per-org quotas, retention,
and (later) per-org buckets have a stable namespace. Old rows keep their
stored ``storage_key`` and stay readable — reads are row-driven, so no
rename migration is needed.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import pytest_asyncio

from app import models
from app.config import settings
from app.db import Base
from app.tenancy import DEFAULT_ORG_ID, current_org_id, install_org_filter
from noodle.artifacts import LocalArtifactStore
from noodle.context import current_node_id


def test_store_key_prefix_namespaces_writes(tmp_path):
    store = LocalArtifactStore(tmp_path, "run1", key_prefix="org-a")
    token = current_node_id.set("n1")
    try:
        ref = store.write_bytes(b"hello", name="out.txt")
    finally:
        current_node_id.reset(token)
    assert ref["storage_key"].startswith("org-a/runs/run1/")
    assert (tmp_path / ref["storage_key"]).read_bytes() == b"hello"
    # the ref reads back through the same store
    assert store.read_bytes(ref) == b"hello"


def test_store_without_prefix_keeps_legacy_layout(tmp_path):
    store = LocalArtifactStore(tmp_path, "run1")
    token = current_node_id.set("n1")
    try:
        ref = store.write_bytes(b"x", name="out.bin")
    finally:
        current_node_id.reset(token)
    assert ref["storage_key"].startswith("runs/run1/")


def test_make_artifact_store_uses_org_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifacts_dir", str(tmp_path))
    from app.services.artifacts import make_artifact_store

    store = make_artifact_store("run9", org_id="org-z")
    token = current_node_id.set("n1")
    try:
        ref = store.write_bytes(b"z", name="z.bin")
    finally:
        current_node_id.reset(token)
    assert ref["storage_key"].startswith("org-z/runs/run9/")


def test_local_backend_delete_run_reclaims_both_layouts(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "artifacts_dir", str(tmp_path))
    from app.services.artifact_backends import LocalBackend

    legacy = tmp_path / "runs" / "r1" / "n"
    legacy.mkdir(parents=True)
    (legacy / "a.bin").write_bytes(b"1")
    namespaced = tmp_path / "org-a" / "runs" / "r1" / "n"
    namespaced.mkdir(parents=True)
    (namespaced / "b.bin").write_bytes(b"2")

    LocalBackend().delete_run("r1", org_id="org-a")
    assert not (tmp_path / "runs" / "r1").exists()
    assert not (tmp_path / "org-a" / "runs" / "r1").exists()


@pytest_asyncio.fixture
async def session(tmp_path):
    install_org_filter()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'art.db'}", poolclass=NullPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def test_runner_upload_must_match_run_org_namespace(client, monkeypatch):
    """Polish-2: a runner token can only write under its run's org prefix —
    and the run lookup is org-blind (runners send no X-Org-Id)."""
    import io

    from app.services import retention
    from app.services.crypto import create_payload_token

    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    async with retention.SessionLocal() as db:
        db.add_all(
            [
                models.Organization(id=DEFAULT_ORG_ID, name="D", slug="default"),
                models.Organization(id="org-x", name="X", slug="x"),
            ]
        )
        pool = models.RunnerPool(name="p", provider="agent", org_id="org-x")
        db.add(pool)
        await db.flush()
        runner = models.Runner(pool_id=pool.id, name="r1")
        db.add(runner)
        token = current_org_id.set("org-x")
        try:
            wf = models.Workflow(name="wf", draft_graph={"nodes": [], "edges": []})
            wf.versions.append(models.WorkflowVersion(version=1, graph={}))
            db.add(wf)
            await db.flush()
            run = models.Run(workflow_id=wf.id)
            db.add(run)
            await db.commit()
            run_id, runner_id = run.id, runner.id
            assert run.org_id == "org-x"
        finally:
            current_org_id.reset(token)

    bearer = create_payload_token(
        {"sub": runner_id, "kind": "runner_registration"}, ttl_seconds=3600
    )

    def _post(storage_key: str):
        return client.post(
            "/runner-pools/artifact-upload",
            params={
                "run_id": run_id,
                "node_id": "n1",
                "artifact_id": "art1",
                "name": "out.bin",
                "storage_key": storage_key,
            },
            headers={"Authorization": f"Bearer {bearer}"},
            files={"data": ("out.bin", io.BytesIO(b"x"), "application/octet-stream")},
        )

    wrong = await _post("default/runs/x/n1/art1-out.bin")
    assert wrong.status_code == 400, wrong.text

    unprefixed = await _post("runs/x/n1/art1-out.bin")
    assert unprefixed.status_code == 400

    right = await _post(f"org-x/runs/{run_id}/n1/art1-out.bin")
    assert right.status_code == 201, right.text


async def test_artifact_of_foreign_org_run_is_404(session, monkeypatch):
    """The artifacts table has no org_id; the guard goes through the parent
    run, which IS org-scoped. An org-a request must not see org-b's bytes."""
    from app.routers.artifacts import _get_artifact

    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    token = current_org_id.set("org-b")
    try:
        session.add_all(
            [
                models.Organization(id=DEFAULT_ORG_ID, name="D", slug="default"),
                models.Organization(id="org-a", name="A", slug="a"),
                models.Organization(id="org-b", name="B", slug="b"),
            ]
        )
        wf = models.Workflow(name="wf", draft_graph={"nodes": [], "edges": []})
        wf.versions.append(models.WorkflowVersion(version=1, graph={}))
        session.add(wf)
        await session.flush()
        run = models.Run(workflow_id=wf.id)
        session.add(run)
        await session.flush()
        session.add(
            models.Artifact(
                id="artb", run_id=run.id, node_id="n", name="a.bin",
                storage_key="org-b/runs/x/n/a.bin",
            )
        )
        await session.commit()
        assert run.org_id == "org-b"
    finally:
        current_org_id.reset(token)

    token = current_org_id.set("org-a")
    try:
        with pytest.raises(HTTPException) as exc:
            await _get_artifact(session, "artb")
        assert exc.value.status_code == 404
    finally:
        current_org_id.reset(token)

    token = current_org_id.set("org-b")
    try:
        row = await _get_artifact(session, "artb")
        assert row.id == "artb"
    finally:
        current_org_id.reset(token)
