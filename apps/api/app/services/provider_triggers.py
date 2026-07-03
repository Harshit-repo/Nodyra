"""Lifecycle-managed provider trigger subscriptions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import nodyra_nodes  # noqa: F401 - ensure built-in provider triggers register
from app.config import settings
from app.db import SessionLocal
from app.models import (
    ProviderTriggerSubscription,
    Run,
    RunEvent,
    Workflow,
    WorkflowVersion,
)
from app.services.audit import log_audit
from app.services.credentials import is_credential_ref, resolve_credential_refs
from app.services.runner import start_run
from nodyra_nodes.integrations_v2.registry import (
    get_registered_provider_trigger,
    is_registered_provider_trigger,
)
from nodyra_nodes.integrations_v2.specs import (
    ProviderTriggerActivationContext,
    ProviderTriggerDeactivationContext,
    ProviderTriggerRequest,
)

logger = logging.getLogger(__name__)

_SECRET_MARKERS = ("secret", "token", "password", "key", "credential")


@dataclass
class ProviderWebhookDispatch:
    """Route-facing result for an inbound provider webhook delivery."""

    status: int = 202
    body: Any = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    run_ids: list[str] = field(default_factory=list)


def _public_base_url() -> str:
    return settings.public_api_url.strip().rstrip("/") or "http://localhost:8000"


def callback_url(subscription_id: str) -> str:
    return f"{_public_base_url()}/provider-webhook/{subscription_id}"


def _graph_nodes(graph: dict | None) -> list[dict[str, Any]]:
    nodes = (graph or {}).get("nodes", [])
    return [node for node in nodes if isinstance(node, dict)]


def _node_params_by_id(graph: dict | None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for node in _graph_nodes(graph):
        node_id = str(node.get("id") or "")
        if node_id:
            params = node.get("params")
            result[node_id] = params if isinstance(params, dict) else {}
    return result


def _redacted_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if is_credential_ref(value):
        return {
            "__nodyra_credential__": True,
            "id": str(value.get("id") or ""),
            "key": str(value.get("key") or ""),
        }
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return "" if value in (None, "") else "[redacted]"
    if isinstance(value, dict):
        return {str(k): _redacted_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redacted_value(key, item) for item in value]
    return value


def _safe_params(params: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _redacted_value(str(key), value) for key, value in params.items()}


def _fingerprint(params: dict[str, Any]) -> str:
    payload = json.dumps(_safe_params(params), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dedupe_key(subscription_id: str, provider_key: str | None) -> str | None:
    if not provider_key:
        return None
    raw = f"provider:{subscription_id}:{provider_key}"
    if len(raw) <= 128:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"provider:{subscription_id}:{digest}"


def _delivery_metadata(
    event_payload: dict[str, Any] | None,
    *,
    response_status: int,
    latency_ms: int,
    dedupe_key: str | None,
    duplicate: bool,
) -> dict[str, Any]:
    payload = event_payload if isinstance(event_payload, dict) else {}
    metadata: dict[str, Any] = {
        "response_status": response_status,
        "latency_ms": latency_ms,
        "duplicate": duplicate,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    if dedupe_key:
        metadata["dedupe_key"] = dedupe_key
    for key in ("provider", "event", "delivery_id"):
        value = payload.get(key)
        if value:
            metadata[key] = str(value)
    repository = payload.get("repository")
    if isinstance(repository, dict) and repository.get("full_name"):
        metadata["repository"] = str(repository["full_name"])
    return metadata


def _record_subscription_delivery(
    row: ProviderTriggerSubscription,
    event_payload: dict[str, Any] | None,
    *,
    response_status: int,
    latency_ms: int,
    dedupe_key: str | None = None,
    duplicate: bool = False,
) -> None:
    config = dict(row.config or {})
    config["last_delivery"] = _delivery_metadata(
        event_payload,
        response_status=response_status,
        latency_ms=latency_ms,
        dedupe_key=dedupe_key,
        duplicate=duplicate,
    )
    row.config = config


async def _resolved_params(
    session: AsyncSession,
    *,
    workflow_id: str,
    environment_id: str | None,
    params: dict[str, Any],
) -> dict[str, Any]:
    resolved = await resolve_credential_refs(
        session,
        params,
        workflow_id=workflow_id,
        environment_id=environment_id,
    )
    return resolved if isinstance(resolved, dict) else {}


async def _activate_subscription(
    row: ProviderTriggerSubscription,
    *,
    workflow: Workflow,
    version: WorkflowVersion,
    params: dict[str, Any],
) -> None:
    registered = get_registered_provider_trigger(row.node_type)
    spec = registered.spec
    if spec.activate is None:
        raise RuntimeError(f"Provider trigger {row.node_type} has no activate hook")
    context = ProviderTriggerActivationContext(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        node_id=row.node_id,
        callback_url=row.callback_url,
        params=params,
    )
    result = await asyncio.to_thread(spec.activate, context)
    row.external_id = result.external_id
    row.expires_at = result.expires_at
    row.status = "active"
    row.error = ""
    row.config = {
        **(row.config or {}),
        "provider": spec.provider,
        "trigger_key": spec.trigger_key,
        "subscription": result.config,
    }


async def _deactivate_subscription(
    row: ProviderTriggerSubscription,
    *,
    workflow_id: str,
    workflow_version_id: str | None,
    params: dict[str, Any],
) -> None:
    registered = get_registered_provider_trigger(row.node_type)
    spec = registered.spec
    if spec.deactivate is not None and row.external_id:
        context = ProviderTriggerDeactivationContext(
            workflow_id=workflow_id,
            workflow_version_id=workflow_version_id,
            node_id=row.node_id,
            external_id=row.external_id,
            params=params,
            config=row.config or {},
        )
        await asyncio.to_thread(spec.deactivate, context)
    row.status = "deleted"
    row.error = ""
    row.external_id = ""


async def _log_subscription_audit(
    session: AsyncSession,
    action: str,
    row: ProviderTriggerSubscription,
    *,
    workflow_id: str,
    actor_id: str | None = None,
    actor_email: str | None = None,
) -> None:
    detail = (
        f"{row.provider} {row.trigger_key} node {row.node_id} "
        f"workflow {workflow_id}"
    )
    await log_audit(
        session,
        action,
        "provider_trigger_subscription",
        row.id,
        detail,
        actor_id=actor_id,
        actor_email=actor_email,
    )


async def sync_workflow_provider_triggers(
    session: AsyncSession,
    workflow: Workflow,
    *,
    actor_id: str | None = None,
    actor_email: str | None = None,
) -> list[ProviderTriggerSubscription]:
    """Create/update/delete provider subscriptions for a workflow's latest version."""
    if not workflow.active:
        await deactivate_workflow_provider_triggers(
            session,
            workflow,
            actor_id=actor_id,
            actor_email=actor_email,
        )
        return []
    if not workflow.versions:
        return []

    version = workflow.versions[-1]
    graph = version.graph or {"nodes": [], "edges": []}
    current_params = _node_params_by_id(graph)
    existing = {
        row.node_id: row
        for row in (
            await session.scalars(
                select(ProviderTriggerSubscription).where(
                    ProviderTriggerSubscription.workflow_id == workflow.id
                )
            )
        ).all()
    }
    active_rows: list[ProviderTriggerSubscription] = []
    desired_node_ids: set[str] = set()

    for node in _graph_nodes(graph):
        node_type = str(node.get("type") or "")
        if not is_registered_provider_trigger(node_type):
            continue
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        desired_node_ids.add(node_id)
        registered = get_registered_provider_trigger(node_type)
        spec = registered.spec
        raw_params = current_params.get(node_id, {})
        fingerprint = _fingerprint(raw_params)
        row = existing.get(node_id)
        if row is None:
            row = ProviderTriggerSubscription(
                id=uuid.uuid4().hex,
                workflow_id=workflow.id,
                workflow_version_id=version.id,
                node_id=node_id,
                node_type=node_type,
                provider=spec.provider,
                trigger_key=spec.trigger_key,
                status="activating",
            )
            session.add(row)
            await session.flush()
        row.workflow_version_id = version.id
        row.node_type = node_type
        row.provider = spec.provider
        row.trigger_key = spec.trigger_key
        row.callback_url = callback_url(row.id)

        config = row.config if isinstance(row.config, dict) else {}
        if row.status == "active" and config.get("fingerprint") == fingerprint:
            active_rows.append(row)
            continue

        if row.external_id:
            stored_old = config.get("provider_params")
            old_params = stored_old if isinstance(stored_old, dict) else raw_params
            resolved_old = await _resolved_params(
                session,
                workflow_id=workflow.id,
                environment_id=workflow.environment_id,
                params=old_params,
            )
            await _deactivate_subscription(
                row,
                workflow_id=workflow.id,
                workflow_version_id=row.workflow_version_id,
                params=resolved_old,
            )
            await _log_subscription_audit(
                session,
                "deactivate",
                row,
                workflow_id=workflow.id,
                actor_id=actor_id,
                actor_email=actor_email,
            )

        row.status = "activating"
        row.config = {
            "fingerprint": fingerprint,
            "provider_params": _safe_params(raw_params),
        }
        resolved = await _resolved_params(
            session,
            workflow_id=workflow.id,
            environment_id=workflow.environment_id,
            params=raw_params,
        )
        try:
            await _activate_subscription(
                row,
                workflow=workflow,
                version=version,
                params=resolved,
            )
        except Exception as exc:
            row.status = "error"
            row.error = str(exc)
            await session.flush()
            raise
        await _log_subscription_audit(
            session,
            "activate",
            row,
            workflow_id=workflow.id,
            actor_id=actor_id,
            actor_email=actor_email,
        )
        active_rows.append(row)

    for node_id, row in existing.items():
        if node_id in desired_node_ids or row.status == "deleted":
            continue
        raw_params = current_params.get(node_id, {})
        if not raw_params and isinstance(row.config, dict):
            stored = row.config.get("provider_params")
            raw_params = stored if isinstance(stored, dict) else {}
        resolved = await _resolved_params(
            session,
            workflow_id=workflow.id,
            environment_id=workflow.environment_id,
            params=raw_params,
        )
        await _deactivate_subscription(
            row,
            workflow_id=workflow.id,
            workflow_version_id=row.workflow_version_id,
            params=resolved,
        )
        await _log_subscription_audit(
            session,
            "deactivate",
            row,
            workflow_id=workflow.id,
            actor_id=actor_id,
            actor_email=actor_email,
        )

    await session.flush()
    return active_rows


async def deactivate_workflow_provider_triggers(
    session: AsyncSession,
    workflow: Workflow,
    *,
    actor_id: str | None = None,
    actor_email: str | None = None,
) -> None:
    """Delete all active provider subscriptions for a workflow."""
    version = workflow.versions[-1] if workflow.versions else None
    current_params = _node_params_by_id((version.graph if version else None) or {})
    rows = (
        await session.scalars(
            select(ProviderTriggerSubscription).where(
                ProviderTriggerSubscription.workflow_id == workflow.id,
                ProviderTriggerSubscription.status != "deleted",
            )
        )
    ).all()
    for row in rows:
        raw_params = current_params.get(row.node_id, {})
        if not raw_params and isinstance(row.config, dict):
            stored = row.config.get("provider_params")
            raw_params = stored if isinstance(stored, dict) else {}
        resolved = await _resolved_params(
            session,
            workflow_id=workflow.id,
            environment_id=workflow.environment_id,
            params=raw_params,
        )
        await _deactivate_subscription(
            row,
            workflow_id=workflow.id,
            workflow_version_id=row.workflow_version_id,
            params=resolved,
        )
        await _log_subscription_audit(
            session,
            "deactivate",
            row,
            workflow_id=workflow.id,
            actor_id=actor_id,
            actor_email=actor_email,
        )
    await session.flush()


async def _load_subscription_context(
    session: AsyncSession,
    subscription_id: str,
) -> tuple[ProviderTriggerSubscription, Workflow, WorkflowVersion]:
    row = await session.get(ProviderTriggerSubscription, subscription_id)
    if row is None or row.status != "active":
        raise KeyError(subscription_id)
    workflow = await session.scalar(
        select(Workflow)
        .where(Workflow.id == row.workflow_id)
        .options(selectinload(Workflow.versions))
    )
    if workflow is None:
        raise KeyError(subscription_id)
    version: WorkflowVersion | None = None
    if row.workflow_version_id:
        version = await session.get(WorkflowVersion, row.workflow_version_id)
    if version is None:
        version = workflow.versions[-1] if workflow.versions else None
    if version is None:
        raise KeyError(subscription_id)
    return row, workflow, version


async def dispatch_provider_webhook(
    subscription_id: str,
    request: ProviderTriggerRequest,
) -> ProviderWebhookDispatch:
    """Verify and dispatch an inbound provider webhook delivery."""
    from app.tenancy import run_as_org, run_as_system

    # Provider callbacks are unauthenticated, so no org context is set on the
    # request. Resolve the subscription's org up front (across all orgs) and
    # scope the whole dispatch to it — otherwise every non-default-org provider
    # trigger is filtered to the default org: the subscription lookup returns
    # None (404, trigger never fires) and the RunEvent timeline rows get
    # mis-stamped to the default org (R-13).
    async with SessionLocal() as session:
        with run_as_system():
            sub_org = await session.scalar(
                select(ProviderTriggerSubscription.org_id).where(
                    ProviderTriggerSubscription.id == subscription_id
                )
            )

    with run_as_org(sub_org):
        async with SessionLocal() as session:
            row, workflow, version = await _load_subscription_context(
                session, subscription_id
            )
            registered = get_registered_provider_trigger(row.node_type)
            spec = registered.spec
            if spec.handle_event is None:
                raise RuntimeError(f"Provider trigger {row.node_type} has no handler")

            graph = version.graph or {"nodes": [], "edges": []}
            node_params = _node_params_by_id(graph).get(row.node_id, {})
            params = await _resolved_params(
                session,
                workflow_id=workflow.id,
                environment_id=workflow.environment_id,
                params=node_params,
            )
            handler_start = time.perf_counter()
            try:
                event = await asyncio.wait_for(
                    asyncio.to_thread(spec.handle_event, request, params),
                    timeout=30.0,  # P1-17: bounded handler to prevent hanging
                )
            except TimeoutError:
                logger.error(
                    "provider trigger handler timed out after 30s "
                    "subscription_id=%s provider=%s",
                    subscription_id, spec.provider_id,
                )
                raise HTTPException(
                    status.HTTP_504_GATEWAY_TIMEOUT,
                    "Provider event handler timed out",
                )
            latency_ms = max(0, int((time.perf_counter() - handler_start) * 1000))
            row.last_event_at = datetime.now(UTC)
            await session.flush()

            if event.payload is None or event.response_status >= 400:
                _record_subscription_delivery(
                    row,
                    event.payload if isinstance(event.payload, dict) else None,
                    response_status=event.response_status,
                    latency_ms=latency_ms,
                )
                await session.commit()
                return ProviderWebhookDispatch(
                    status=event.response_status,
                    body=event.response_body
                    if event.response_body is not None
                    else {"message": "Provider event acknowledged"},
                    headers=event.response_headers,
                )

            dedupe_key = _dedupe_key(row.id, event.dedupe_key)
            if dedupe_key:
                seen = await session.scalar(
                    select(Run.id)
                    .where(Run.workflow_id == workflow.id)
                    .where(Run.deduplication_key == dedupe_key)
                    .limit(1)
                )
                if seen is not None:
                    _record_subscription_delivery(
                        row,
                        event.payload if isinstance(event.payload, dict) else None,
                        response_status=200,
                        latency_ms=latency_ms,
                        dedupe_key=dedupe_key,
                        duplicate=True,
                    )
                    await session.commit()
                    return ProviderWebhookDispatch(
                        status=200,
                        body={
                            "message": "Duplicate provider delivery acknowledged",
                            "runs": [],
                        },
                        headers=event.response_headers,
                    )
            _record_subscription_delivery(
                row,
                event.payload if isinstance(event.payload, dict) else None,
                response_status=event.response_status,
                latency_ms=latency_ms,
                dedupe_key=dedupe_key,
            )
            await session.commit()

        run_id = await start_run(
            workflow.id,
            version.graph or {"nodes": [], "edges": []},
            version.version,
            workflow_version_id=version.id,
            mode="production",
            trigger_type="provider",
            cache={row.node_id: {"main": event.payload}},
            trigger_node_id=row.node_id,
            deduplication_key=dedupe_key,
        )
        await record_provider_trigger_event(
            run_id,
            subscription_id=row.id,
            node_id=row.node_id,
            provider=row.provider,
            trigger_key=row.trigger_key,
            dedupe_key=dedupe_key,
            event_payload=event.payload,
            response_status=event.response_status,
            latency_ms=latency_ms,
        )
        body = event.response_body if event.response_body is not None else {}
        if isinstance(body, dict):
            body = {**body, "runs": [run_id]}
        return ProviderWebhookDispatch(
            status=event.response_status,
            body=body,
            headers=event.response_headers,
            run_ids=[run_id],
        )


async def record_provider_trigger_event(
    run_id: str,
    *,
    subscription_id: str,
    node_id: str,
    provider: str,
    trigger_key: str,
    dedupe_key: str | None,
    event_payload: dict[str, Any],
    response_status: int,
    latency_ms: int,
) -> None:
    """Persist redacted provider-trigger metadata for the run timeline."""
    payload = {
        "type": "provider_trigger_received",
        "subscription_id": subscription_id,
        "node_id": node_id,
        "provider": provider,
        "trigger_key": trigger_key,
        "dedupe_key": dedupe_key or "",
        "response_status": response_status,
        "latency_ms": latency_ms,
    }
    for key in ("event", "delivery_id"):
        value = event_payload.get(key)
        if value:
            payload[key] = str(value)
    repository = event_payload.get("repository")
    if isinstance(repository, dict) and repository.get("full_name"):
        payload["repository"] = str(repository["full_name"])
    async with SessionLocal() as session:
        max_sequence = await session.scalar(
            select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run_id)
        )
        session.add(
            RunEvent(
                run_id=run_id,
                event_type="provider_trigger_received",
                sequence=int(max_sequence or 0) + 1,
                ts=datetime.now(UTC),
                node_id=node_id,
                payload=payload,
            )
        )
        await session.commit()
