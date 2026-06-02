"""Trigger dispatch.

Webhook ingress runs workflows whose graph contains a matching webhook node,
and an in-process scheduler fires schedule-trigger workflows. Schedules support
both a simple interval (every N minutes/hours/days) and full cron expressions.
``last_fired`` is persisted in the DB so the scheduler survives a restart
without missing or double-firing. A multi-replica deployment can disable this
loop (``enable_inprocess_scheduler=false``) and drive runs from Celery Beat.
"""

import asyncio
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import SessionLocal
from app.models import Deployment, ScheduleState, Workflow, WorkflowVersion
from app.services.graph_utils import first_trigger_node
from app.services.runner import start_run

logger = logging.getLogger(__name__)

_INTERVAL_SECONDS = {"minutes": 60, "hours": 3600, "days": 86400}

# Don't log the same unknown-timezone string every tick — flood control.
_logged_bad_tz: set[str] = set()


def _resolve_tz(name: str) -> ZoneInfo | None:
    """Return the IANA zone for ``name``, or ``None`` if it's unknown.

    Returning ``None`` lets callers decide what "invalid" means in their
    context — for ``_is_due`` it means "skip this tick" (don't fall back to
    UTC, which would silently fire crons at the wrong absolute time).
    Blank/unset is *not* invalid — it's interpreted as UTC.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(cleaned)
    except ZoneInfoNotFoundError:
        return None


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
        raw_tz_name = (
            str(params.get("tz", "") or "").strip() or settings.app_timezone
        )
        tz = _resolve_tz(raw_tz_name)
        if tz is None:
            # Unknown IANA name — refuse to fire rather than silently use UTC.
            # Log once per name so the loop stays quiet.
            if raw_tz_name not in _logged_bad_tz:
                _logged_bad_tz.add(raw_tz_name)
                logger.warning(
                    "schedule trigger has unknown timezone %r; not firing",
                    raw_tz_name,
                )
            return False
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


async def _resolve_node_auth(
    params: dict, workflow_id: str, environment_id: str | None
) -> dict:
    """Pre-resolve credential refs inside the webhook node's auth params.

    Returns a copy of ``params`` with any ``{"__noodle_credential__": True}``
    references replaced by their decrypted values. Auth comparison happens
    in :func:`dispatch_webhook` against the resolved strings, never against
    the stored references.
    """
    from app.services.credentials import resolve_credential_refs

    # New workflows store the entire auth config inside auth_credentials.
    # Legacy workflows pinned individual credential references per field;
    # keep those keys so old graphs continue to validate.
    auth_keys = (
        "auth_credentials",
        "auth_username",
        "auth_password",
        "auth_header_value",
        "auth_query_value",
    )
    snapshot = {key: params.get(key) for key in auth_keys}
    async with SessionLocal() as session:
        resolved = await resolve_credential_refs(
            session,
            snapshot,
            workflow_id=workflow_id,
            environment_id=environment_id,
        )
    return resolved


def _matches_basic_auth(
    auth_header: str | None, expected_user: str, expected_pass: str
) -> bool:
    if not auth_header or not auth_header.lower().startswith("basic "):
        return False
    import base64

    try:
        decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    if ":" not in decoded:
        return False
    user, _, password = decoded.partition(":")
    return user == expected_user and password == expected_pass


def _webhook_auth_passes(
    node_params: dict, resolved: dict, headers: dict, query: dict
) -> bool:
    """Return True if the incoming request satisfies the node's auth_type."""
    auth_type = str(node_params.get("auth_type") or "none").lower()
    if auth_type == "none":
        return True
    # Header keys arrive lower-cased from FastAPI's CIMultiDict; normalise.
    lower_headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    auth_credentials = resolved.get("auth_credentials")
    creds = auth_credentials if isinstance(auth_credentials, dict) else None

    if auth_type == "basic":
        if creds:
            username = str(creds.get("username") or "")
            password = str(creds.get("password") or "")
        else:
            # Legacy: per-field credential references on old workflows.
            username = str(resolved.get("auth_username") or "")
            password = str(resolved.get("auth_password") or "")
        return _matches_basic_auth(lower_headers.get("authorization"), username, password)

    if auth_type == "header":
        if creds:
            name = str(creds.get("name") or "X-API-Key").lower()
            expected = str(creds.get("value") or "")
        else:
            # Legacy fallback for workflows that pinned name on the node.
            name = str(node_params.get("auth_header_name") or "X-API-Key").lower()
            expected = str(resolved.get("auth_header_value") or "")
        return bool(expected) and lower_headers.get(name) == expected

    if auth_type == "query":
        if creds:
            name = str(creds.get("name") or "token")
            expected = str(creds.get("value") or "")
        else:
            name = str(node_params.get("auth_query_name") or "token")
            expected = str(resolved.get("auth_query_value") or "")
        return bool(expected) and str((query or {}).get(name) or "") == expected

    return False


async def dispatch_webhook(
    path: str, request_payload: dict, *, prefer_draft: bool = False
) -> tuple[list[str], bool]:
    """Run every active workflow that starts with a matching webhook node.

    When ``prefer_draft`` is True the dispatcher uses each workflow's
    in-editor draft graph instead of the latest published version. This is
    what the editor's test URL (``/webhook-test/{path}``) should use so the
    user can iterate on auth + flow without publishing first. Production
    URL (``/webhook/{path}``) always uses the published snapshot.

    Returns ``(run_ids, any_path_matched)``. The caller uses
    ``any_path_matched`` to distinguish "no workflow at this path" (404)
    from "matched but auth rejected every candidate" (401).
    """
    run_ids: list[str] = []
    any_match = False
    headers = request_payload.get("headers") or {}
    query = request_payload.get("query") or {}
    workflows = (
        await _all_workflows() if prefer_draft else await _active_workflows()
    )
    for workflow in workflows:
        graph: dict | None = None
        version_number: int = 1
        version_id: str | None = None
        if prefer_draft and getattr(workflow, "draft_graph", None):
            graph = workflow.draft_graph
            # Anchor the run to the latest known version for history sanity,
            # but we never bump the version — drafts are not snapshots.
            if workflow.versions:
                version_number = workflow.versions[-1].version
                version_id = workflow.versions[-1].id
        else:
            if not workflow.versions:
                continue
            latest = workflow.versions[-1]
            graph = latest.graph or {}
            version_number = latest.version
            version_id = latest.id
        if not graph:
            continue
        for node in graph.get("nodes", []):
            if node.get("type") != "webhook_trigger":
                continue
            node_params = node.get("params") or {}
            node_path = str(node_params.get("path", ""))
            if node_path != path:
                continue
            any_match = True
            resolved_auth = await _resolve_node_auth(
                node_params, workflow.id, workflow.environment_id
            )
            if not _webhook_auth_passes(node_params, resolved_auth, headers, query):
                continue
            run_id = await start_run(
                workflow.id,
                graph,
                version_number,
                workflow_version_id=version_id,
                mode="test" if prefer_draft else "production",
                trigger_type="webhook",
                cache={node["id"]: {"main": request_payload}},
                trigger_node_id=node["id"],
            )
            run_ids.append(run_id)
    return run_ids, any_match


async def _all_workflows() -> list[Workflow]:
    """Used by the editor test URL — draft-mode dispatch ignores `active`."""
    async with SessionLocal() as session:
        result = await session.scalars(
            select(Workflow).options(selectinload(Workflow.versions))
        )
        return list(result.all())


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
    due_workflow: list[tuple[str, dict, int, str | None, str]] = []
    due_deployment: list[tuple[str, dict, int, str | None, str, dict, str | None]] = []

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

        # Batch-load the pinned versions referenced by active deployments so the
        # loop below doesn't issue one ``session.get(WorkflowVersion)`` per
        # deployment every tick.
        dep_version_ids = {
            d.workflow_version_id for d in deployments if d.workflow_version_id
        }
        versions_by_id: dict[str, WorkflowVersion] = {}
        if dep_version_ids:
            versions_by_id = {
                v.id: v
                for v in (
                    await session.scalars(
                        select(WorkflowVersion).where(
                            WorkflowVersion.id.in_(dep_version_ids)
                        )
                    )
                ).all()
            }

        # --- 1. Active deployments take precedence over in-graph schedules.
        for deployment in deployments:
            workflow = wf_by_id.get(deployment.workflow_id)
            if workflow is None:
                continue
            version: WorkflowVersion | None = None
            if deployment.workflow_version_id:
                version = versions_by_id.get(deployment.workflow_version_id)
            if version is None:
                version = workflow.versions[-1]
            graph = version.graph or {}
            params = _deployment_params(deployment)
            if deployment.last_fired is None:
                deployment.last_fired = now  # start the clock, no fire
                continue
            if _is_due(params, deployment.last_fired, now):
                deployment.last_fired = now
                chosen_trigger = first_trigger_node(graph)
                trigger_id = (
                    chosen_trigger["id"]
                    if isinstance(chosen_trigger, dict)
                    else getattr(chosen_trigger, "id", None)
                )
                due_deployment.append(
                    (
                        workflow.id,
                        graph,
                        version.version,
                        version.id,
                        deployment.id,
                        deployment.default_parameters or {},
                        trigger_id,
                    )
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
                due_workflow.append(
                    (
                        workflow.id,
                        graph,
                        latest.version,
                        latest.id,
                        schedule["id"],
                    )
                )

        await session.commit()

    if due_workflow or due_deployment:
        logger.info(
            "scheduler tick: %d workflow schedule(s), %d deployment(s) due",
            len(due_workflow),
            len(due_deployment),
        )

    # Dispatch outside the state transaction; start_run opens its own session.
    for workflow_id, graph, version, version_id, trigger_id in due_workflow:
        await start_run(
            workflow_id,
            graph,
            version,
            workflow_version_id=version_id,
            mode="production",
            trigger_type="schedule",
            trigger_node_id=trigger_id,
        )
    for (
        workflow_id,
        graph,
        version,
        version_id,
        deployment_id,
        params,
        trigger_id,
    ) in due_deployment:
        await start_run(
            workflow_id,
            graph,
            version,
            workflow_version_id=version_id,
            deployment_id=deployment_id,
            mode="production",
            trigger_type="deployment",
            parameters=params or None,
            trigger_node_id=trigger_id,
        )


async def scheduler_loop() -> None:
    """Background loop that fires schedule triggers. Started from the lifespan."""
    while True:
        try:
            await _tick()
        except Exception:  # noqa: BLE001 - a bad workflow must not kill the loop
            pass
        await asyncio.sleep(30)
