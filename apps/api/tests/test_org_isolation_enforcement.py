"""X4: dedicated-pool execution isolation enforcement at dispatch time."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app import models
from app.config import settings
from app.services import retention
from app.services.runner import start_run
from app.tenancy import DEFAULT_ORG_ID, current_org_id, run_as_system

GRAPH = {
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


@pytest.fixture
def mt_on(monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    token = current_org_id.set(None)
    yield
    current_org_id.reset(token)


async def _seed(session, *, isolation: str, pool_org: str | None, provider: str):
    session.add_all(
        [
            models.Organization(id=DEFAULT_ORG_ID, name="D", slug="default"),
            models.Organization(
                id="org-x", name="X", slug="x", execution_isolation=isolation
            ),
        ]
    )
    pool_id = None
    if pool_org is not None:
        pool = models.RunnerPool(name="p", provider=provider, org_id=pool_org)
        session.add(pool)
        await session.flush()
        pool_id = pool.id
    token = current_org_id.set("org-x")
    try:
        wf = models.Workflow(
            name="x-wf", draft_graph=GRAPH, default_runner_pool_id=pool_id
        )
        wf.versions.append(models.WorkflowVersion(version=1, graph=GRAPH))
        session.add(wf)
        await session.commit()
        return wf.id
    finally:
        current_org_id.reset(token)


async def test_dedicated_org_without_pool_is_refused(client: AsyncClient, mt_on):
    async with retention.SessionLocal() as session:
        workflow_id = await _seed(
            session, isolation="dedicated_pool", pool_org=None, provider="docker"
        )
    with pytest.raises(ValueError, match="isolated execution"):
        await start_run(workflow_id, GRAPH, 1)


async def test_dedicated_org_with_foreign_pool_is_refused(client: AsyncClient, mt_on):
    async with retention.SessionLocal() as session:
        workflow_id = await _seed(
            session,
            isolation="dedicated_pool",
            pool_org=DEFAULT_ORG_ID,  # someone else's pool
            provider="docker",
        )
    with pytest.raises(ValueError, match="isolated execution"):
        await start_run(workflow_id, GRAPH, 1)


async def test_dedicated_org_with_agent_pool_is_refused(client: AsyncClient, mt_on):
    """An 'agent' pool is a plain VM daemon, not container-per-run — it does
    not satisfy dedicated isolation."""
    async with retention.SessionLocal() as session:
        workflow_id = await _seed(
            session, isolation="dedicated_pool", pool_org="org-x", provider="agent"
        )
    with pytest.raises(ValueError, match="isolated execution"):
        await start_run(workflow_id, GRAPH, 1)


async def test_dedicated_org_with_own_docker_pool_dispatches(
    client: AsyncClient, mt_on
):
    async with retention.SessionLocal() as session:
        workflow_id = await _seed(
            session, isolation="dedicated_pool", pool_org="org-x", provider="docker"
        )
    # Enforcement passes; the run is created (it may then fail later for lack
    # of an actual docker daemon in tests — that's the dispatch layer's
    # problem, not the isolation gate's).
    try:
        await start_run(workflow_id, GRAPH, 1)
    except ValueError as exc:  # pragma: no cover - would mean the gate misfired
        pytest.fail(f"isolation gate refused a valid dedicated pool: {exc}")
    except Exception:
        pass  # downstream dispatch errors are fine here
    async with retention.SessionLocal() as session:
        with run_as_system():
            run = await session.scalar(
                select(models.Run).where(models.Run.workflow_id == workflow_id)
            )
    assert run is not None
    assert run.org_id == "org-x"


async def test_shared_org_unaffected(client: AsyncClient, mt_on):
    async with retention.SessionLocal() as session:
        workflow_id = await _seed(
            session, isolation="shared", pool_org=None, provider="docker"
        )
    run_id = await start_run(workflow_id, GRAPH, 1)
    assert run_id
