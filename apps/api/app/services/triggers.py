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
from app.models import ScheduleState, Workflow
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


async def _tick() -> None:
    now = datetime.now(UTC)
    due: list[tuple[str, dict, int]] = []

    async with SessionLocal() as session:
        workflows = (
            await session.scalars(
                select(Workflow)
                .where(Workflow.active.is_(True))
                .options(selectinload(Workflow.versions))
            )
        ).all()
        states = {
            s.workflow_id: s
            for s in (await session.scalars(select(ScheduleState))).all()
        }

        for workflow in workflows:
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
                # First sighting — start the clock without firing, so a
                # restart doesn't trigger an immediate run.
                session.add(ScheduleState(workflow_id=workflow.id, last_fired=now))
                continue

            if _is_due(params, state.last_fired, now):
                state.last_fired = now
                due.append((workflow.id, graph, latest.version))

        await session.commit()

    # Dispatch outside the state transaction; start_run opens its own session.
    for workflow_id, graph, version in due:
        await start_run(
            workflow_id,
            graph,
            version,
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
