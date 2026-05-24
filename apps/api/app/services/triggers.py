"""Trigger dispatch.

Webhook ingress runs workflows whose graph contains a matching webhook node,
and an in-process scheduler fires schedule-trigger workflows. Schedules support
both a simple interval (every N minutes/hours/days) and full cron expressions.
``last_fired`` is persisted in the DB so the scheduler survives a restart
without missing or double-firing. A multi-replica deployment can disable this
loop (``enable_inprocess_scheduler=false``) and drive runs from Celery Beat.
"""

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import SessionLocal
from app.models import Deployment, ScheduleState, Workflow
from app.services.runner import start_run

_INTERVAL_SECONDS = {"minutes": 60, "hours": 3600, "days": 86400}


def _resolve_tz(name: str) -> ZoneInfo:
    """Return the IANA zone for ``name``; fall back to UTC if it's unknown."""
    name = (name or "").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _is_due(params: dict, last: datetime, now: datetime) -> bool:
    """Return True if a schedule with these params is due relative to ``last``."""
    # SQLite returns naive datetimes; treat a stored value as UTC so it can be
    # compared against the timezone-aware ``now``.
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    cron = str(params.get("cron", "") or "").strip()
    if cron:
        # Evaluate the cron in the user-selected timezone so an expression like
        # "0 9 * * *" really means 09:00 *local* (not 09:00 UTC). croniter
        # respects the tzinfo of the base datetime. Resolution chain:
        # node-level ``tz`` → app-wide default (settings.app_timezone) → UTC.
        tz_name = str(params.get("tz", "") or "").strip() or settings.app_timezone
        tz = _resolve_tz(tz_name)
        last_local = last.astimezone(tz)
        try:
            next_time = croniter(cron, last_local).get_next(datetime)
            return next_time <= now
        except (ValueError, KeyError):
            return False  # malformed cron — never fire rather than crash
    interval = params.get("interval", "hours")
    every = max(int(params.get("every", 1) or 1), 1)
    period = _INTERVAL_SECONDS.get(interval, 3600) * every
    return (now - last).total_seconds() >= period


async def _active_workflows() -> list[Workflow]:
    async with SessionLocal() as session:
        result = await session.scalars(
            select(Workflow)
            .where(Workflow.active.is_(True))
            .options(selectinload(Workflow.versions))
        )
        return list(result.all())


async def dispatch_webhook(path: str, request_payload: dict) -> list[str]:
    """Run every active workflow that starts with a matching webhook node."""
    run_ids: list[str] = []
    for workflow in await _active_workflows():
        latest = workflow.versions[-1]
        graph = latest.graph or {}
        for node in graph.get("nodes", []):
            if node.get("type") != "webhook_trigger":
                continue
            node_path = str(node.get("params", {}).get("path", ""))
            if node_path != path:
                continue
            run_id = await start_run(
                workflow.id,
                graph,
                latest.version,
                mode="production",
                trigger_type="webhook",
                cache={node["id"]: {"main": request_payload}},
            )
            run_ids.append(run_id)
    return run_ids


def _deployment_params(deployment: Deployment) -> dict:
    """Build the cron/interval-shaped params dict that ``_is_due`` expects."""
    return {
        "cron": deployment.schedule_cron or "",
        "interval": deployment.schedule_interval or "hours",
        "every": deployment.schedule_every or 1,
        "tz": deployment.schedule_tz or "",
    }


async def _tick() -> None:
    """One pass of the scheduler.

    Precedence: a workflow with *any* active Deployment is scheduled **only**
    by its deployments (deployment is the source of truth). Workflows with
    no active deployment fall back to their in-graph ``schedule_trigger``,
    which preserves the zero-config default for legacy graphs.
    """
    now = datetime.now(UTC)
    due_workflow: list[tuple[str, dict, int]] = []
    due_deployment: list[tuple[str, dict, int, dict]] = []

    async with SessionLocal() as session:
        workflows = (
            await session.scalars(
                select(Workflow).options(selectinload(Workflow.versions))
            )
        ).all()
        deployments = (
            await session.scalars(select(Deployment).where(Deployment.active.is_(True)))
        ).all()
        states = {
            s.workflow_id: s
            for s in (await session.scalars(select(ScheduleState))).all()
        }

        wf_by_id = {wf.id: wf for wf in workflows}
        deployments_by_workflow: dict[str, list[Deployment]] = {}
        for d in deployments:
            deployments_by_workflow.setdefault(d.workflow_id, []).append(d)

        # --- 1. Active deployments take precedence over in-graph schedules.
        for deployment in deployments:
            workflow = wf_by_id.get(deployment.workflow_id)
            if workflow is None:
                continue
            latest = workflow.versions[-1]
            graph = latest.graph or {}
            params = _deployment_params(deployment)
            if deployment.last_fired is None:
                deployment.last_fired = now  # start the clock, no fire
                continue
            if _is_due(params, deployment.last_fired, now):
                deployment.last_fired = now
                due_deployment.append(
                    (workflow.id, graph, latest.version, deployment.default_parameters or {})
                )

        # --- 2. Fallback: workflows that are active and have no deployment
        # use their in-graph schedule_trigger as before.
        for workflow in workflows:
            if not workflow.active:
                continue
            if deployments_by_workflow.get(workflow.id):
                continue  # deployment(s) own this workflow's schedule
            latest = workflow.versions[-1]
            graph = latest.graph or {}
            schedule = next(
                (
                    n
                    for n in graph.get("nodes", [])
                    if n.get("type") == "schedule_trigger"
                ),
                None,
            )
            if schedule is None:
                continue

            params = schedule.get("params", {})
            state = states.get(workflow.id)
            if state is None:
                session.add(ScheduleState(workflow_id=workflow.id, last_fired=now))
                continue
            if _is_due(params, state.last_fired, now):
                state.last_fired = now
                due_workflow.append((workflow.id, graph, latest.version))

        await session.commit()

    # Dispatch outside the state transaction; start_run opens its own session.
    for workflow_id, graph, version in due_workflow:
        await start_run(
            workflow_id,
            graph,
            version,
            mode="production",
            trigger_type="schedule",
        )
    for workflow_id, graph, version, params in due_deployment:
        await start_run(
            workflow_id,
            graph,
            version,
            mode="production",
            trigger_type="deployment",
            parameters=params or None,
        )


async def scheduler_loop() -> None:
    """Background loop that fires schedule triggers. Started from the lifespan."""
    while True:
        try:
            await _tick()
        except Exception:  # noqa: BLE001 - a bad workflow must not kill the loop
            pass
        await asyncio.sleep(30)
