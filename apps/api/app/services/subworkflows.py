"""Host-side sub-workflow resolver (A3).

Implements ``SubworkflowRunner`` for the API: looks up the child graph,
resolves credentials, seeds the trigger, creates a child Run row, and runs
the child on the right substrate. Cycle detection and depth limits live in
the ENGINE adapter (``nodyra.engine.subworkflows``) — not here.

Concurrency invariants preserved from the pre-A3 code (HANDOFF.md §7):

* Child runs NEVER acquire the global ``max_concurrent_runs`` slot — the
  parent already holds one; waiting would deadlock at the cap.
  ``runtime_pool.dispatch_subworkflow`` (subprocess) and the direct
  ``execute`` call below (in-process) both bypass it.
* Org sub-workflow caps (``max_inflight_subworkflows``) are enforced by the
  soft ``subworkflow_slot`` throttle inside ``dispatch_subworkflow`` —
  unchanged by this refactor.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import SessionLocal
from app.models import PinnedData, Run, Workflow
from app.services.credentials import resolve_credential_refs
from app.services.graph_utils import first_trigger_node, resolve_trigger_targets
from nodyra.engine import execute
from nodyra.engine.subworkflows import (
    InlineSubworkflow,
    SubworkflowCall,
    SubworkflowMeta,
    extract_leaf_value,
)
from nodyra.models import WorkflowGraph
from nodyra.serialization import deserialize_value

logger = logging.getLogger(__name__)


def meta_for_root_run(
    *, run_id: str, workflow_id: str, prefer_draft: bool, org_id: str | None = None
) -> SubworkflowMeta:
    """The SubworkflowMeta a root run hands to the engine / run protocol."""
    return SubworkflowMeta(
        use_published=not prefer_draft,
        parent_run_id=run_id,
        depth=0,
        call_chain=frozenset({workflow_id}),
        max_depth=settings.max_subworkflow_depth,
        org_id=org_id,
    )


async def _load_workflow_graph(
    session: AsyncSession, workflow_id: str, *, use_published: bool
) -> tuple[dict, dict[str, dict]]:
    """(graph_dict, pinned_cache) for a child workflow.

    Production runs execute the most recently published version (Slice 11
    contract); manual editor runs (``use_published=False``) propagate "use
    draft" so iteration works without publishing every dependent workflow.
    """
    workflow = await session.scalar(
        select(Workflow)
        .where(Workflow.id == workflow_id)
        .options(selectinload(Workflow.versions))
    )
    if workflow is None:
        raise ValueError(f"workflow '{workflow_id}' not found")
    if not workflow.versions:
        raise ValueError(f"workflow '{workflow_id}' has no published versions")
    latest = workflow.versions[-1]
    pinned_rows = await session.scalars(
        select(PinnedData).where(PinnedData.workflow_id == workflow_id)
    )
    pinned = {row.node_id: row.payload for row in pinned_rows.all()}
    published = latest.graph or {"nodes": [], "edges": []}
    if not use_published and workflow.draft_graph:
        return workflow.draft_graph, pinned
    return published, pinned


async def _create_child_run(call: SubworkflowCall, workflow_id: str) -> str:
    """Persist a Run row for a spawned/in-process child.

    Gives the child a real identity: ``_resolve_run_org`` keys artifact
    prefixes and org limits off the run row, so children inherit the
    parent's org instead of falling back to the default org (pre-A3 bug for
    multi-tenant deployments). Inline children skip this — they execute
    inside the parent's process under the parent's run.

    The child's ``org_id`` is taken from ``call.org_id`` (propagated through
    the engine's SubworkflowCall/Meta protocol) rather than derived from the
    parent Run — the parent may have already been GC'd by retention or the
    tenancy context is explicit.
    """
    from app.tenancy import run_as_system

    child_run_id = uuid.uuid4().hex[:32]
    with run_as_system():
        async with SessionLocal() as session:
            org_id = call.org_id or "default"
            session.add(
                Run(
                    id=child_run_id,
                    org_id=org_id,
                    workflow_id=workflow_id,
                    parent_run_id=call.parent_run_id,
                    mode="subworkflow",
                    trigger_type="subworkflow",
                    status="running",
                )
            )
            await session.commit()
    return child_run_id


async def _finalize_child_run(child_run_id: str, status: str) -> None:
    from app.tenancy import run_as_system

    try:
        with run_as_system():
            async with SessionLocal() as session:
                run = await session.get(Run, child_run_id)
                if run is not None:
                    run.status = status
                    run.finished_at = datetime.now(UTC)
                    await session.commit()
    except Exception:  # noqa: BLE001 - bookkeeping must not mask the result
        logger.exception("child run finalize failed run_id=%s", child_run_id)


async def resolve_subworkflow(
    call: SubworkflowCall, *, parent_env_id: str | None = None
) -> Any:
    """``SubworkflowRunner`` for the API host.

    Execution paths (the engine adapter already did cycle/depth checks):

    * **Inline directive** — subprocess mode + parent and child share an
      env: return the prepared graph; the parent's engine runs it itself.
      Zero subprocess spawns. (The pre-A3 "no nested workflow calls"
      restriction is gone: chain/depth travel in the protocol now.)
    * **Spawn-fresh subprocess** — subprocess mode otherwise; goes through
      ``dispatch_subworkflow`` (global-cap bypass + org-aware soft throttle).
    * **In-process** — tests / ``use_subprocess_runner=False``.

    The caller's org context is propagated through ``call.org_id`` so the
    child workflow lookup, credential resolution, and Run creation all
    happen in the correct tenant scope.
    """
    from app.tenancy import current_org_id

    resolved_org = call.org_id
    org_token = current_org_id.set(resolved_org) if resolved_org else None
    try:
        async with SessionLocal() as session:
            workflow = await session.get(Workflow, call.workflow_id)
            sub_env_id = workflow.environment_id if workflow else None
            graph_dict, pinned_cache = await _load_workflow_graph(
                session, call.workflow_id, use_published=call.use_published
            )
            graph_dict = await resolve_credential_refs(
                session, graph_dict, workflow_id=call.workflow_id
            )
            pinned_cache = await resolve_credential_refs(
                session, pinned_cache, workflow_id=call.workflow_id
            )
            await session.commit()

        graph = WorkflowGraph.model_validate(graph_dict)
        sources = {edge.source for edge in graph.edges}

        cache: dict[str, dict] = deserialize_value(dict(pinned_cache))
        trigger = first_trigger_node(graph)
        if trigger is not None and trigger.id not in cache:
            cache[trigger.id] = {
                "main": call.parameters if call.parameters is not None else {}
            }
        sub_targets = (
            resolve_trigger_targets(graph_dict, trigger.id, None)
            if trigger is not None
            else None
        )

        if (
            settings.use_subprocess_runner
            and parent_env_id is not None
            and parent_env_id == sub_env_id
        ):
            logger.info(
                "sub-workflow inline workflow_id=%s env_id=%s depth=%s",
                call.workflow_id, sub_env_id, call.depth,
            )
            return InlineSubworkflow(
                graph=graph_dict,
                cache=cache or None,
                targets=sub_targets,
                sources=tuple(sorted(sources)),
            )

        child_run_id = await _create_child_run(call, call.workflow_id)
        child_meta = SubworkflowMeta(
            use_published=call.use_published,
            parent_run_id=child_run_id,
            depth=call.depth,
            call_chain=call.call_chain,
            max_depth=settings.max_subworkflow_depth,
            org_id=resolved_org,
        )
        status = "error"
        try:
            if settings.use_subprocess_runner:
                from app.services.runtime_pool import pool as runtime_pool

                node_status: dict[str, str] = {}
                node_outputs: dict[str, dict] = {}

                async def collect(event: dict) -> None:
                    if event.get("type") != "node_finished":
                        return
                    nid = event.get("node_id")
                    if not isinstance(nid, str):
                        return
                    node_status[nid] = str(event.get("status") or "")
                    outputs = deserialize_value(event.get("outputs"))
                    if isinstance(outputs, dict):
                        node_outputs[nid] = outputs

                logger.info(
                    "sub-workflow spawn workflow_id=%s env_id=%s parent_env_id=%s",
                    call.workflow_id, sub_env_id, parent_env_id,
                )
                status = await runtime_pool.dispatch_subworkflow(
                    child_run_id,
                    sub_env_id,
                    graph_dict,
                    cache or None,
                    sub_targets,
                    collect,
                    subworkflow_resolver=resolve_subworkflow,
                    subworkflow_meta=child_meta,
                )
                return extract_leaf_value(sources, node_status, node_outputs)

            from app.services import runner as _runner

            result = await execute(
                graph,
                _runner.node_registry,
                cache=cache or None,
                targets=sub_targets,
                default_timeouts=_runner._engine_default_timeouts(),
                process_isolator=_runner.process_isolator,
                subworkflow_runner=resolve_subworkflow,
                subworkflow_meta=child_meta,
            )
            status = str(result.status)
            node_status = {nid: str(r.status) for nid, r in result.nodes.items()}
            node_outputs = {nid: dict(r.outputs) for nid, r in result.nodes.items()}
            return extract_leaf_value(sources, node_status, node_outputs)
        finally:
            await _finalize_child_run(child_run_id, status)
    finally:
        if org_token is not None:
            current_org_id.reset(org_token)
