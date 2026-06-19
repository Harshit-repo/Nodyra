"""X4: dedicated-pool execution isolation enforcement at dispatch time."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app import models
from app.config import settings
from app.services import retention
from app.exceptions import DedicatedPoolRequired
from app.services.runner import start_run
from app.tenancy import DEFAULT_ORG_ID, active_org_id, current_org_id, run_as_system

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
            models.Organization(id="org-x", name="X", slug="x", execution_isolation=isolation),
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
        wf = models.Workflow(name="x-wf", draft_graph=GRAPH, default_runner_pool_id=pool_id)
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
    with pytest.raises(DedicatedPoolRequired, match="isolated execution"):
        await start_run(workflow_id, GRAPH, 1)


async def test_dedicated_org_with_foreign_pool_is_refused(client: AsyncClient, mt_on):
    async with retention.SessionLocal() as session:
        workflow_id = await _seed(
            session,
            isolation="dedicated_pool",
            pool_org=DEFAULT_ORG_ID,  # someone else's pool
            provider="docker",
        )
    with pytest.raises(DedicatedPoolRequired, match="isolated execution"):
        await start_run(workflow_id, GRAPH, 1)


async def test_dedicated_org_with_agent_pool_is_refused(client: AsyncClient, mt_on):
    """An 'agent' pool is a plain VM daemon, not container-per-run — it does
    not satisfy dedicated isolation."""
    async with retention.SessionLocal() as session:
        workflow_id = await _seed(
            session, isolation="dedicated_pool", pool_org="org-x", provider="agent"
        )
    with pytest.raises(DedicatedPoolRequired, match="isolated execution"):
        await start_run(workflow_id, GRAPH, 1)


async def test_dedicated_org_with_own_docker_pool_dispatches(client: AsyncClient, mt_on):
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


async def test_write_time_pool_validation(client: AsyncClient, mt_on):
    """Polish-1: assigning a non-qualifying pool to a dedicated org's
    environment fails up front with 422 (the dispatch gate would refuse the
    run anyway; this is the early, actionable error)."""
    from fastapi import HTTPException

    from app.services.isolation import validate_pool_assignment

    async with retention.SessionLocal() as session:
        session.add_all(
            [
                models.Organization(id=DEFAULT_ORG_ID, name="D", slug="default"),
                models.Organization(
                    id="org-x",
                    name="X",
                    slug="x",
                    execution_isolation="dedicated_pool",
                ),
                models.Organization(id="org-s", name="S", slug="s"),
            ]
        )
        own_docker = models.RunnerPool(name="ok", provider="docker", org_id="org-x")
        own_agent = models.RunnerPool(name="vm", provider="agent", org_id="org-x")
        foreign = models.RunnerPool(name="theirs", provider="docker", org_id=DEFAULT_ORG_ID)
        session.add_all([own_docker, own_agent, foreign])
        await session.commit()

        await validate_pool_assignment(session, "org-x", own_docker.id)  # ok
        await validate_pool_assignment(session, "org-x", None)  # clearing ok
        await validate_pool_assignment(session, "org-s", own_agent.id)  # shared org ok
        for bad in (own_agent.id, foreign.id, "missing"):
            with pytest.raises(HTTPException) as exc:
                await validate_pool_assignment(session, "org-x", bad)
            assert exc.value.status_code == 422


async def test_environment_create_validates_dedicated_pool_assignment(
    client: AsyncClient, mt_on
):
    """Creating an environment must enforce the same X4 assignment rule as PATCH."""
    from fastapi import BackgroundTasks, HTTPException

    from app.routers.environments import create_environment
    from app.schemas import EnvironmentCreate

    async with retention.SessionLocal() as session:
        session.add_all(
            [
                models.Organization(id=DEFAULT_ORG_ID, name="D", slug="default"),
                models.Organization(
                    id="org-x",
                    name="X",
                    slug="x",
                    execution_isolation="dedicated_pool",
                ),
            ]
        )
        own_agent = models.RunnerPool(name="vm", provider="agent", org_id="org-x")
        own_docker = models.RunnerPool(name="ok", provider="docker", org_id="org-x")
        session.add_all([own_agent, own_docker])
        await session.commit()

        token = current_org_id.set("org-x")
        try:
            with pytest.raises(HTTPException) as exc:
                await create_environment(
                    EnvironmentCreate(name="bad", runner_pool_id=own_agent.id),
                    BackgroundTasks(),
                    session,
                    actor=None,
                )
            assert exc.value.status_code == 422

            created = await create_environment(
                EnvironmentCreate(name="ok", runner_pool_id=own_docker.id),
                BackgroundTasks(),
                session,
                actor=None,
            )
            assert created.runner_pool_id == own_docker.id
        finally:
            current_org_id.reset(token)


async def test_shared_org_unaffected(client: AsyncClient, mt_on):
    async with retention.SessionLocal() as session:
        workflow_id = await _seed(session, isolation="shared", pool_org=None, provider="docker")
    run_id = await start_run(workflow_id, GRAPH, 1)
    assert run_id


async def test_start_run_restores_system_context(client: AsyncClient, mt_on):
    """Scheduled/system loops must stay unscoped after launching an org run."""
    async with retention.SessionLocal() as session:
        workflow_id = await _seed(session, isolation="shared", pool_org=None, provider="docker")

    with run_as_system():
        assert active_org_id() is None
        await start_run(workflow_id, GRAPH, 1)
        assert active_org_id() is None


async def test_global_credentials_are_org_local_for_system_resolution(
    client: AsyncClient, mt_on
) -> None:
    """A system-scoped webhook matcher must not turn global creds cross-tenant."""
    from app.services.credentials import credential_ref, resolve_credential_refs

    async with retention.SessionLocal() as session:
        session.add_all(
            [
                models.Organization(id="org-a", name="A", slug="a"),
                models.Organization(id="org-b", name="B", slug="b"),
            ]
        )
        cred = models.Credential(
            id="cred-a",
            org_id="org-a",
            name="A global",
            type="generic",
            scope="global",
            encrypted_data="not-used",
        )
        workflow = models.Workflow(
            org_id="org-b",
            name="B workflow",
            draft_graph=GRAPH,
        )
        session.add_all([cred, workflow])
        await session.commit()

        with run_as_system():
            with pytest.raises(RuntimeError, match="not visible"):
                await resolve_credential_refs(
                    session,
                    {"token": credential_ref(cred.id, "token")},
                    workflow_id=workflow.id,
                )
