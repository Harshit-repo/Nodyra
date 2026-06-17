"""A3: org_id on every tenant-owned table + automatic stamping on flush."""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import models
from app.config import settings
from app.db import Base
from app.tenancy import DEFAULT_ORG_ID, current_org_id, install_org_filter

# The decided Phase A list: every top-level tenant-owned table. Child tables
# (node_runs, run_events, run_approvals) inherit tenancy via their parent FK
# and deliberately have no org_id. Artifact and Runner carry their own org_id
# because they are queried directly (not always through a parent) and the ORM
# hook only filters models that have the column.
ORG_SCOPED = [
    models.Workflow,
    models.WorkflowVersion,
    models.Credential,
    models.Environment,
    models.Deployment,
    models.CodeModule,
    models.PinnedData,
    models.RunnerPool,
    models.Run,
    models.RunBatch,
    models.RunQueueEntry,
    models.ProviderTriggerSubscription,
    models.ScheduleState,
    models.AuditEvent,
    models.Artifact,
    models.Runner,
]

CHILD_TABLES = [
    models.NodeRun,
    models.RunEvent,
    models.RunApproval,
]


@pytest_asyncio.fixture
async def session(tmp_path):
    install_org_filter()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'orgcols.db'}", poolclass=NullPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add_all(
            [
                models.Organization(id=DEFAULT_ORG_ID, name="Default", slug="default"),
                models.Organization(id="org-x", name="X", slug="x"),
            ]
        )
        await session.commit()
        yield session
    await engine.dispose()


def test_all_tenant_tables_have_org_id():
    for model in ORG_SCOPED:
        assert "org_id" in model.__table__.c, f"{model.__name__} is missing org_id"
        column = model.__table__.c.org_id
        assert not column.nullable, f"{model.__name__}.org_id must be NOT NULL"
        assert column.index, f"{model.__name__}.org_id must be indexed"


def test_child_tables_have_no_org_id():
    for model in CHILD_TABLES:
        assert "org_id" not in model.__table__.c, (
            f"{model.__name__} should inherit tenancy via its parent, not org_id"
        )


@pytest.mark.asyncio
async def test_new_rows_stamped_with_default_org_when_flag_off(session, monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", False)
    workflow = models.Workflow(name="wf", draft_graph={"nodes": [], "edges": []})
    workflow.versions.append(models.WorkflowVersion(version=1, graph={}))
    session.add(workflow)
    await session.commit()
    assert workflow.org_id == DEFAULT_ORG_ID
    assert workflow.versions[0].org_id == DEFAULT_ORG_ID


@pytest.mark.asyncio
async def test_new_rows_stamped_with_request_org_when_flag_on(session, monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    token = current_org_id.set("org-x")
    try:
        session.add(models.AuditEvent(action="create", target_type="workflow"))
        await session.commit()
    finally:
        current_org_id.reset(token)
    event = await session.scalar(select(models.AuditEvent).execution_options())
    # we are outside the org-x context now; with the flag still on, the
    # default-org scope hides org-x rows
    assert event is None


@pytest.mark.asyncio
async def test_workflows_invisible_across_orgs(session, monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    token = current_org_id.set("org-x")
    try:
        workflow = models.Workflow(name="x-wf", draft_graph={"nodes": [], "edges": []})
        workflow.versions.append(models.WorkflowVersion(version=1, graph={}))
        session.add(workflow)
        await session.commit()
        assert workflow.org_id == "org-x"
        mine = (await session.scalars(select(models.Workflow))).all()
        assert [w.name for w in mine] == ["x-wf"]
    finally:
        current_org_id.reset(token)
    current_default = current_org_id.set(DEFAULT_ORG_ID)
    try:
        theirs = (await session.scalars(select(models.Workflow))).all()
        assert theirs == []
    finally:
        current_org_id.reset(current_default)
