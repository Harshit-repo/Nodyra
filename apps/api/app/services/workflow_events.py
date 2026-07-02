from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Workflow, WorkflowRevision
from app.services.events import workflow_broker

WORKFLOW_CREATED = "workflow_created"
WORKFLOW_DELETED = "workflow_deleted"
WORKFLOW_GRAPH_CHANGED = "workflow_graph_changed"
WORKFLOW_UPDATED = "workflow_updated"
WORKFLOW_PUBLISHED = "workflow_published"


def bump_graph_revision(workflow: Workflow) -> int:
    workflow.graph_revision = int(workflow.graph_revision or 0) + 1
    return workflow.graph_revision


def node_patch_snapshot(node: dict[str, Any]) -> dict[str, Any]:
    """Sanitized node metadata for event/history payloads.

    Node params can contain secrets, payload examples, or generated code, so
    live-change events expose structure and param names, not raw values.
    """
    params = node.get("params")
    snapshot: dict[str, Any] = {
        "id": node.get("id"),
        "type": node.get("type"),
    }
    if node.get("label") is not None:
        snapshot["label"] = node.get("label")
    if node.get("position") is not None:
        snapshot["position"] = node.get("position")
    if isinstance(params, dict):
        snapshot["param_keys"] = sorted(str(key) for key in params)
    return {key: value for key, value in snapshot.items() if value is not None}


def edge_patch_snapshot(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        key: edge.get(key)
        for key in ("source", "source_output", "target", "target_input")
        if edge.get(key) is not None
    }


def summarize_patch(operation: str, patch: dict[str, Any] | None = None) -> str:
    if not patch:
        return operation.replace("_", " ")
    patch_type = str(patch.get("type") or operation)
    node_id = patch.get("node_id") or (patch.get("node") or {}).get("id")
    if node_id:
        return f"{patch_type.replace('_', ' ')}: {node_id}"
    source = patch.get("source") or (patch.get("edge") or {}).get("source")
    target = patch.get("target") or (patch.get("edge") or {}).get("target")
    if source and target:
        return f"{patch_type.replace('_', ' ')}: {source} -> {target}"
    return patch_type.replace("_", " ")


def record_workflow_revision(
    session: AsyncSession,
    workflow: Workflow,
    *,
    origin: str,
    operation: str,
    actor: User | None = None,
    patch: dict[str, Any] | None = None,
    summary: str | None = None,
) -> WorkflowRevision:
    revision = WorkflowRevision(
        org_id=workflow.org_id,
        workflow_id=workflow.id,
        graph_revision=int(workflow.graph_revision or 0),
        origin=origin,
        operation=operation,
        summary=summary or summarize_patch(operation, patch),
        patch=patch,
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
    )
    session.add(revision)
    return revision


def publish_workflow_event(
    workflow: Workflow,
    event_type: str,
    *,
    origin: str,
    operation: str,
    actor: User | None = None,
    **extra: Any,
) -> None:
    payload: dict[str, Any] = {
        "type": event_type,
        "workflow_id": workflow.id,
        "org_id": workflow.org_id,
        "workflow_name": workflow.name,
        "graph_revision": int(workflow.graph_revision or 0),
        "published_version": workflow.published_version,
        "origin": origin,
        "operation": operation,
        "ts": datetime.now(UTC).isoformat(),
    }
    if actor is not None:
        payload["actor_id"] = actor.id
        payload["actor_email"] = actor.email
    payload.update({key: value for key, value in extra.items() if value is not None})
    workflow_broker.publish(workflow.id, payload)


def publish_workflow_graph_changed(
    workflow: Workflow,
    *,
    origin: str,
    operation: str,
    actor: User | None = None,
    **extra: Any,
) -> None:
    publish_workflow_event(
        workflow,
        WORKFLOW_GRAPH_CHANGED,
        origin=origin,
        operation=operation,
        actor=actor,
        **extra,
    )
