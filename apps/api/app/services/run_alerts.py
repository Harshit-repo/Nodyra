"""Error-workflow + alert-webhook dispatch for failed runs (split from runner.py).

``dispatch_error_handlers`` receives its session factory and ``start_run``
callable from the runner module at call time so test monkeypatching of
``runner.SessionLocal`` / ``runner.start_run`` keeps applying.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import Deployment, Run, Workflow, WorkflowVersion
from app.services.redaction import redact_value
from app.tenancy import run_as_system


def _first_failed_event(node_events: dict[str, dict]) -> dict | None:
    for event in node_events.values():
        if event.get("status") == "error":
            return event
    return None


def _webhook_urls(alerts: dict | None) -> list[str]:
    if not isinstance(alerts, dict):
        return []
    urls: list[str] = []
    value = alerts.get("webhook_url")
    if isinstance(value, str) and value.strip():
        urls.append(value.strip())
    values = alerts.get("webhook_urls")
    if isinstance(values, list):
        urls.extend(str(item).strip() for item in values if str(item).strip())
    return urls


async def _post_error_webhooks(alerts: dict | None, payload: dict) -> None:
    urls = _webhook_urls(alerts)
    if not urls:
        return
    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        for url in urls:
            try:
                await client.post(url, json=payload)
            except Exception:  # noqa: BLE001 - alerts must not fail the run
                continue


async def dispatch_error_handlers(
    session_factory,
    start_run_fn,
    *,
    run_id: str,
    node_events: dict[str, dict],
    secret_values: list[str],
) -> None:
    async with session_factory() as session:
        with run_as_system():
            run = await session.get(Run, run_id)
        if (
            run is None
            or run.status != "error"
            or run.triggered_by_error_run_id is not None
        ):
            return
        with run_as_system():
            workflow = await session.scalar(
                select(Workflow)
                .where(Workflow.id == run.workflow_id)
                .options(selectinload(Workflow.versions))
            )
        if workflow is None:
            return
        with run_as_system():
            deployment = (
                await session.get(Deployment, run.deployment_id)
                if run.deployment_id
                else None
            )
        error_workflow_id = (
            deployment.error_workflow_id if deployment else None
        ) or workflow.error_workflow_id
        alerts = (deployment.error_alerts if deployment else None) or workflow.error_alerts
        failed = _first_failed_event(node_events)
        payload = redact_value(
            {
                "workflow_id": run.workflow_id,
                "workflow_name": workflow.name,
                "run_id": run.id,
                "status": run.status,
                "trigger_type": run.trigger_type,
                "deployment_id": run.deployment_id,
                "workflow_version": run.workflow_version,
                "workflow_version_id": run.workflow_version_id,
                "failed_node_id": failed.get("node_id") if failed else None,
                "error": failed.get("error") if failed else None,
                "logs": failed.get("logs") if failed else [],
                "retry_path": f"/executions?run={run.id}",
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": (
                    run.finished_at.isoformat() if run.finished_at else None
                ),
            },
            secret_values,
        )

        error_graph: dict | None = None
        error_version: int | None = None
        error_version_id: str | None = None
        if error_workflow_id and error_workflow_id != run.workflow_id:
            with run_as_system():
                error_workflow = await session.scalar(
                    select(Workflow)
                    .where(Workflow.id == error_workflow_id)
                    .options(selectinload(Workflow.versions))
                )
            if error_workflow is not None and error_workflow.versions:
                version: WorkflowVersion = error_workflow.versions[-1]
                error_graph = version.graph or {"nodes": [], "edges": []}
                error_version = version.version
                error_version_id = version.id

    await _post_error_webhooks(alerts, payload)
    if error_graph is not None and error_version is not None:
        await start_run_fn(
            error_workflow_id,
            error_graph,
            error_version,
            workflow_version_id=error_version_id,
            triggered_by_error_run_id=run_id,
            mode="production",
            trigger_type="error",
            parameters=payload,
        )
