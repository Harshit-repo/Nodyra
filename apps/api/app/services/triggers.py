"""Trigger dispatch.

Webhook ingress runs workflows whose graph contains a matching webhook node,
and an in-process scheduler fires schedule-trigger workflows on an interval.
A production deployment would move the scheduler to Celery Beat.
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.models import Workflow
from app.services.runner import start_run

_INTERVAL_SECONDS = {"minutes": 60, "hours": 3600, "days": 86400}

# In-memory record of when each workflow's schedule last fired.
_last_fired: dict[str, datetime] = {}


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


async def _tick() -> None:
    now = datetime.now(UTC)
    for workflow in await _active_workflows():
        latest = workflow.versions[-1]
        graph = latest.graph or {}
        schedule = next(
            (n for n in graph.get("nodes", []) if n.get("type") == "schedule_trigger"),
            None,
        )
        if schedule is None:
            continue

        params = schedule.get("params", {})
        interval = params.get("interval", "hours")
        every = max(int(params.get("every", 1) or 1), 1)
        period = _INTERVAL_SECONDS.get(interval, 3600) * every

        last = _last_fired.get(workflow.id)
        if last is None:
            _last_fired[workflow.id] = now  # first sighting — start the clock
            continue
        if (now - last).total_seconds() >= period:
            _last_fired[workflow.id] = now
            await start_run(
                workflow.id,
                graph,
                latest.version,
                mode="production",
                trigger_type="schedule",
            )


async def scheduler_loop() -> None:
    """Background loop that fires schedule triggers. Started from the lifespan."""
    while True:
        try:
            await _tick()
        except Exception:  # noqa: BLE001 - a bad workflow must not kill the loop
            pass
        await asyncio.sleep(30)
