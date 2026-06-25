"""C3: per-org run metering + executions/day quota."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app import models
from app.config import settings
from app.exceptions import QuotaExceeded
from app.services import metering, org_limits, retention
from app.services.runner import start_run
from app.tenancy import DEFAULT_ORG_ID, current_org_id

GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual_trigger", "params": {}, "position": {"x": 0, "y": 0}}
    ],
    "edges": [],
}


@pytest.fixture
def mt_on(monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    token = current_org_id.set(None)
    yield
    org_limits.invalidate_limits_cache()
    current_org_id.reset(token)


async def _seed_org_workflow(session, *, executions_per_day: int | None) -> str:
    session.add_all(
        [
            models.Organization(id=DEFAULT_ORG_ID, name="D", slug="default"),
            models.Organization(id="org-x", name="X", slug="x"),
        ]
    )
    if executions_per_day is not None:
        session.add(
            models.OrgSettings(org_id="org-x", executions_per_day=executions_per_day)
        )
    token = current_org_id.set("org-x")
    try:
        wf = models.Workflow(name="metered", draft_graph=GRAPH)
        wf.versions.append(models.WorkflowVersion(version=1, graph=GRAPH))
        session.add(wf)
        await session.commit()
        return wf.id
    finally:
        current_org_id.reset(token)


async def test_run_counts_and_quota_end_to_end(client: AsyncClient, mt_on):
    async with retention.SessionLocal() as session:
        workflow_id = await _seed_org_workflow(session, executions_per_day=2)
        org_limits.invalidate_limits_cache()

    # Two runs pass (run_synchronously executes them inline)...
    await start_run(workflow_id, GRAPH, 1)
    await start_run(workflow_id, GRAPH, 1)

    # ...the third hits the daily ceiling.
    with pytest.raises(QuotaExceeded, match="Daily execution quota"):
        await start_run(workflow_id, GRAPH, 1)

    async with retention.SessionLocal() as session:
        meter = await session.scalar(
            select(models.RunMeter)
            .where(models.RunMeter.org_id == "org-x")
            .execution_options(skip_org_filter=True)
        )
        assert meter is not None
        assert meter.runs == 2
        # Completion accounting must mirror the persisted NodeRun rows
        # exactly (a trigger-only graph may legitimately produce zero).
        from sqlalchemy import func

        actual_node_runs = int(
            await session.scalar(
                select(func.count())
                .select_from(models.NodeRun)
                .join(models.Run, models.Run.id == models.NodeRun.run_id)
                .where(models.Run.workflow_id == workflow_id)
                .execution_options(skip_org_filter=True)
            )
            or 0
        )
        assert meter.node_runs == actual_node_runs
        assert meter.compute_seconds >= 0.0


async def test_no_quota_means_unlimited_but_still_metered(client: AsyncClient, mt_on):
    async with retention.SessionLocal() as session:
        workflow_id = await _seed_org_workflow(session, executions_per_day=None)
        org_limits.invalidate_limits_cache()
    await start_run(workflow_id, GRAPH, 1)
    async with retention.SessionLocal() as session:
        meter = await session.scalar(
            select(models.RunMeter)
            .where(models.RunMeter.org_id == "org-x")
            .execution_options(skip_org_filter=True)
        )
        assert meter is not None and meter.runs == 1


async def test_flag_off_records_nothing(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", False)
    async with retention.SessionLocal() as session:
        workflow_id = await _seed_org_workflow(session, executions_per_day=1)
    await start_run(workflow_id, GRAPH, 1)
    await start_run(workflow_id, GRAPH, 1)  # quota ignored with the flag off
    async with retention.SessionLocal() as session:
        meters = (
            await session.scalars(
                select(models.RunMeter).execution_options(skip_org_filter=True)
            )
        ).all()
        assert meters == []


async def test_runs_today_helper(client: AsyncClient, mt_on):
    async with retention.SessionLocal() as session:
        await _seed_org_workflow(session, executions_per_day=None)
        assert await metering.runs_today(session, "org-x") == 0
        await metering.record_run_started(session, "org-x")
        await metering.record_run_started(session, "org-x")
        await session.commit()
        assert await metering.runs_today(session, "org-x") == 2
