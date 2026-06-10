"""C2: org-fair queue leasing + per-org concurrency caps."""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import models
from app.config import settings
from app.db import Base
from app.services import org_limits
from app.services import queue as run_queue
from app.tenancy import DEFAULT_ORG_ID, current_org_id, install_org_filter

GRAPH = {"nodes": [], "edges": []}


@pytest_asyncio.fixture
async def session(tmp_path):
    install_org_filter()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'fair.db'}", poolclass=NullPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add_all(
            [
                models.Organization(id=DEFAULT_ORG_ID, name="D", slug="default"),
                models.Organization(id="org-a", name="A", slug="a"),
                models.Organization(id="org-b", name="B", slug="b"),
            ]
        )
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clear_caches():
    org_limits.invalidate_limits_cache()
    yield
    org_limits.invalidate_limits_cache()


@pytest.fixture
def mt_on(monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    token = current_org_id.set(None)
    yield
    current_org_id.reset(token)


async def _enqueue_run(session, org_id: str, name: str) -> str:
    """Workflow + run + queue entry for the given org; returns run_id."""
    token = current_org_id.set(org_id)
    try:
        wf = models.Workflow(name=name, draft_graph=GRAPH)
        wf.versions.append(models.WorkflowVersion(version=1, graph=GRAPH))
        session.add(wf)
        await session.flush()
        run = models.Run(workflow_id=wf.id, status="queued")
        session.add(run)
        await session.flush()
        await run_queue.enqueue(
            session, run_id=run.id, workflow_id=wf.id, reason="test"
        )
        await session.commit()
        return run.id
    finally:
        current_org_id.reset(token)


async def _entry_org(session, entry) -> str:
    return entry.org_id


async def test_flooding_org_does_not_starve_others(session, mt_on):
    # org A floods five entries first (older available_at), org B adds one.
    for i in range(5):
        await _enqueue_run(session, "org-a", f"a{i}")
    b_run = await _enqueue_run(session, "org-b", "b0")

    first = await run_queue.lease(session, worker_id="w1")
    assert first.org_id == "org-a"  # both idle; A has the oldest entry
    second = await run_queue.lease(session, worker_id="w1")
    # A now has one in flight, B has zero -> fairness picks B despite A's
    # older backlog.
    assert second.org_id == "org-b"
    assert second.run_id == b_run


async def test_org_at_cap_is_parked_with_reason(session, mt_on):
    session.add(models.OrgSettings(org_id="org-a", max_concurrent_runs=1))
    await session.commit()
    org_limits.invalidate_limits_cache()

    await _enqueue_run(session, "org-a", "a0")
    await _enqueue_run(session, "org-a", "a1")

    first = await run_queue.lease(session, worker_id="w1")
    assert first is not None and first.org_id == "org-a"
    # A is now at its cap of 1; its remaining entry must NOT lease.
    assert await run_queue.lease(session, worker_id="w1") is None

    parked = await session.scalar(
        select(models.RunQueueEntry)
        .where(models.RunQueueEntry.status == "queued")
        .execution_options(skip_org_filter=True)
    )
    assert parked.queue_reason == "org_quota_exceeded"

    # Capacity frees -> the parked entry leases again.
    await run_queue.complete(session, run_id=first.run_id)
    await session.commit()
    resumed = await run_queue.lease(session, worker_id="w1")
    assert resumed is not None and resumed.org_id == "org-a"


async def test_flag_off_keeps_global_fifo(session, monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", False)
    # Flag off: stamping pins everything to the default org and leasing is
    # plain oldest-first with no org logic at all.
    first_run = await _enqueue_run(session, "org-a", "a0")
    second_run = await _enqueue_run(session, "org-b", "b0")
    first = await run_queue.lease(session, worker_id="w1")
    second = await run_queue.lease(session, worker_id="w1")
    assert first.run_id == first_run
    assert second.run_id == second_run
    assert first.org_id == second.org_id == DEFAULT_ORG_ID
