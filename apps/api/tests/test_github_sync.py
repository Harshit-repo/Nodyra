"""Tests for GitHub sync DB models and service logic."""
import secrets

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import models  # noqa: F401 — registers ORM models on Base.metadata
from app.db import Base
from app.models import GithubSyncConfig, GithubSyncJob, Workflow
from app.tenancy import DEFAULT_ORG_ID

# ---------------------------------------------------------------------------
# Shared DB fixture for service tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session(tmp_path):
    """Per-test SQLite session with all tables created and a default org row."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'github_sync_test.db'}", poolclass=NullPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add(models.Organization(id=DEFAULT_ORG_ID, name="Default", slug="default"))
        await session.commit()
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Model-structure tests (Task 2)
# ---------------------------------------------------------------------------


def test_github_sync_config_model_exists():
    assert GithubSyncConfig.__tablename__ == "github_sync_configs"


def test_github_sync_job_model_exists():
    assert GithubSyncJob.__tablename__ == "github_sync_jobs"


def test_workflow_has_sync_columns():
    mapper = Workflow.__mapper__
    cols = {c.key for c in mapper.columns}
    assert "github_sync_sha" in cols
    assert "github_sync_status" in cols
    assert "github_sync_conflict_sha" in cols


# ---------------------------------------------------------------------------
# Service tests: enqueue helper (Task 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_github_push_no_config(db_session):
    """enqueue_github_push is a no-op when org has no sync config."""
    from app.services.github_sync import enqueue_github_push

    wf = Workflow(
        id="wf1",
        name="Test",
        org_id="default",
        draft_graph={"nodes": [], "edges": []},
        published_version=1,
    )
    db_session.add(wf)
    await db_session.flush()
    await enqueue_github_push(db_session, wf, "ui")
    result = await db_session.execute(select(GithubSyncJob))
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_enqueue_github_push_creates_job(db_session):
    """enqueue_github_push inserts a GithubSyncJob row when config exists."""
    from app.services.github_sync import enqueue_github_push

    cfg = GithubSyncConfig(
        org_id="default",
        repo="owner/repo",
        base_path="workflows/",
        main_branch="main",
        webhook_secret=secrets.token_hex(20),
    )
    db_session.add(cfg)
    wf = Workflow(
        id="wf2",
        name="My Flow",
        org_id="default",
        draft_graph={"nodes": [], "edges": []},
        published_version=1,
    )
    db_session.add(wf)
    await db_session.flush()

    await enqueue_github_push(db_session, wf, "mcp")
    await db_session.flush()

    jobs = (await db_session.scalars(select(GithubSyncJob))).all()
    assert len(jobs) == 1
    assert jobs[0].job_type == "push_draft"
    assert jobs[0].origin == "mcp"
    assert jobs[0].status == "pending"


# ---------------------------------------------------------------------------
# Endpoint tests: settings CRUD + webhook (Task 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_github_sync_config_no_config(client: AsyncClient) -> None:
    resp = await client.get("/api/github-sync/config")
    assert resp.status_code == 200
    assert resp.json() is None


@pytest.mark.asyncio
async def test_put_github_sync_config(client: AsyncClient) -> None:
    resp = await client.put(
        "/api/github-sync/config",
        json={"repo": "owner/repo", "base_path": "workflows/", "main_branch": "main"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["repo"] == "owner/repo"
    assert "webhook_url" in data
    assert data["webhook_url"].endswith("/webhooks/github-sync/default")


@pytest.mark.asyncio
async def test_delete_github_sync_config(client: AsyncClient) -> None:
    # Create first
    await client.put("/api/github-sync/config", json={"repo": "owner/repo"})
    resp = await client.delete("/api/github-sync/config")
    assert resp.status_code == 204
    get_resp = await client.get("/api/github-sync/config")
    assert get_resp.json() is None


def test_github_sync_imports():
    from app.services.github_sync import enqueue_github_push
    from app.services.github_sync_jobs import notify_sync_workers
    assert callable(enqueue_github_push)
    assert callable(notify_sync_workers)


@pytest.mark.asyncio
async def test_github_webhook_invalid_signature(client: AsyncClient) -> None:
    # First create a config so the org lookup succeeds
    await client.put("/api/github-sync/config", json={"repo": "owner/repo"})
    resp = await client.post(
        "/webhooks/github-sync/default",
        content=b'{"ref":"refs/heads/main"}',
        headers={
            "X-Hub-Signature-256": "sha256=badsig",
            "X-GitHub-Event": "push",
        },
    )
    assert resp.status_code == 401
