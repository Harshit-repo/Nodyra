"""Workflow execution service.

Runs a workflow graph in-process with the Noodle engine, streams per-node
events to the broker for live editor updates, and persists the run.

Sets up the runtime context (``noodle.context.workflow_caller`` and
``call_chain``) so Execute-Workflow nodes can invoke sub-workflows by id.
"""

import asyncio
import logging
import uuid
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# ContextVar that carries the current run_id into every log record emitted
# while _execute_run is active, without requiring callers to pass it explicitly.
_log_run_id: ContextVar[str] = ContextVar("noodle_log_run_id", default="")


class _RunIdFilter(logging.Filter):
    """Inject ``run_id`` from the ContextVar into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _log_run_id.get("")  # type: ignore[attr-defined]
        return True


def _install_run_id_filter() -> None:
    root = logging.getLogger("noodle")
    for f in root.filters:
        if isinstance(f, _RunIdFilter):
            return
    root.addFilter(_RunIdFilter())

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import noodle_nodes  # noqa: F401 - importing registers the built-in nodes
from app.config import settings
from app.db import SessionLocal
from app.models import (
    CodeModule,
    Deployment,
    Environment,
    NodeRun,
    PinnedData,
    Run,
    RunApproval,
    RunEvent,
    RunQueueEntry,
    Workflow,
    WorkflowVersion,
)
from app.services import queue as run_queue
from app.services import run_alerts, run_persistence
from app.services.artifacts import (
    collect_artifact_refs,
    make_artifact_store,
    persist_artifact_refs,
)
from app.services.credentials import resolve_credential_refs
from app.services.events import broker
from app.services.executors.base import RunExecutionContext
from app.services.executors.local import LocalExecutor
from app.services.executors.remote import RemoteExecutor
from app.services.graph_utils import (
    first_trigger_node,
    forward_descendants,
    resolve_trigger_targets,
    targets_have_trigger,
)
from app.services.live_settings import get_live_settings
from app.services.package_preflight import find_missing_packages, format_missing
from app.services.redaction import load_secret_values, redact_value
from app.services.remote_dispatch import (
    _QueuedError,
    build_env_payload,
    dispatcher,
)
from app.services.runtime_pool import _org_run_limits_for, _resolve_run_org
from app.services.runtime_pool import pool as runtime_pool
from noodle.ai_runtime import AgentActionRequest
from noodle.context import artifact_store, call_chain, org_run_limits, workflow_caller
from noodle.engine import DEFAULT_NODE_TIMEOUTS, execute
from noodle.models import WorkflowGraph
from noodle.process_isolation import (
    PooledProcessIsolator,
)
from noodle.process_isolation import (
    pool_key as engine_pool_key,
)
from noodle.sdk import (
    register_module_functions,
    unregister_module,
)
from noodle.sdk import (
    registry as node_registry,
)
from noodle.serialization import (
    deserialize_value,
    serialize_value,
)

logger = logging.getLogger(__name__)

# B4: the API host owns the code-node process pools. One isolator for the
# whole process; pools inside it are still keyed per environment via
# engine_pool_key, exactly as before.
process_isolator = PooledProcessIsolator()

_active_runs: dict[str, asyncio.Task[None]] = {}

# Back-compat aliases — these moved to run_persistence (A2 split) but are part
# of this module's established surface (on_event closure, resume path, lazy
# importers, tests).
_MAX_RUN_EVENTS = run_persistence._MAX_RUN_EVENTS
AGENT_EVENT_TYPES = run_persistence.AGENT_EVENT_TYPES
GUARDRAIL_EVENT_TYPES = run_persistence.GUARDRAIL_EVENT_TYPES
_approval_key = run_persistence._approval_key
_upsert_run_approval = run_persistence._upsert_run_approval
_maybe_truncate = run_persistence._maybe_truncate
_cap_output = run_persistence._cap_output
_cap_logs = run_persistence._cap_logs
_contains_unrestorable_object = run_persistence._contains_unrestorable_object
_graph_node_types = run_persistence._graph_node_types
_extract_webhook_response = run_persistence._extract_webhook_response


def _build_ctx(
    *, run_id: str, workflow_id: str, graph: dict, cache: dict | None,
    targets: list[str] | None, environment_id: str | None,
    runner_pool_id: str | None, env_payload: dict | None,
    workflow_modules: list[dict], run_timeout: float | None,
    agent_action_resume: dict[str, AgentActionRequest] | None,
) -> RunExecutionContext:
    return {
        "run_id": run_id, "workflow_id": workflow_id, "graph": graph,
        "cache": cache, "targets": targets, "environment_id": environment_id,
        "runner_pool_id": runner_pool_id, "env_payload": env_payload,
        "workflow_modules": workflow_modules, "run_timeout": run_timeout,
        "default_timeouts": _engine_default_timeouts(),
        "pause_on_approval": True,
        "agent_action_resume": (
            {nid: req.model_dump(mode="json")
             for nid, req in agent_action_resume.items()}
            if agent_action_resume else None
        ),
    }


def _engine_default_timeouts() -> dict[str, float]:
    """Per-node default timeouts for the in-process engine.

    Starts from the engine's built-ins and layers on a configurable ``code``
    cap. ``code_node_timeout_seconds <= 0`` leaves code uncapped so a
    long-running Python node isn't cancelled mid-flight.
    """
    timeouts = dict(DEFAULT_NODE_TIMEOUTS)
    code_timeout = settings.code_node_timeout_seconds
    if code_timeout and code_timeout > 0:
        timeouts["code"] = code_timeout
    return timeouts

# Set by ``_execute_run`` before invoking the engine. ``_call_sub_workflow``
# reads this to decide whether sub-workflows should run their editable draft
# (when the root run is a manual editor iteration) or their latest published
# version (any production run). Default ``False`` is the safe choice — a
# missing context defaults to "published only".
_prefer_draft_graphs: ContextVar[bool] = ContextVar(
    "noodle_prefer_draft_graphs", default=False
)


async def _load_workflow_graph(
    session: AsyncSession, workflow_id: str
) -> tuple[dict, dict[str, dict]]:
    """Return (graph_dict, pinned_cache) for a workflow id.

    Used only by ``_call_sub_workflow``. The graph picked depends on the
    root run's context:

    * Production runs (scheduled/webhook/deployment/error workflow) execute
      the most recently published version. This is the Slice 11 contract —
      production must never pick up unpublished changes via a sub-workflow.
    * Manual editor runs (where the user clicked Run on a draft) propagate
      "use draft" to sub-workflows so iteration works without publishing
      every dependent workflow first.

    The choice is read from ``_prefer_draft_graphs`` which ``_execute_run``
    sets based on the root run's ``mode``.
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
    if _prefer_draft_graphs.get() and workflow.draft_graph:
        return workflow.draft_graph, pinned
    return published, pinned


@dataclass(frozen=True)
class InlineSubWorkflow:
    """Sentinel returned by the sub-workflow caller asking the parent's
    runtime to execute the sub in-process — no fresh subprocess spawn.

    Carried by ``_handle_call_workflow`` from the host back to the
    parent's subprocess via the existing ``call_workflow_response``
    message (with ``inline_*`` fields). The runtime runs the engine on
    ``graph`` and completes the awaiting callback with the leaf result,
    saving one subprocess spawn + the credential resolution and graph
    load are already done here on the host.
    """

    graph: dict
    cache: dict | None
    targets: list[str] | None
    sources: list[str]


def _has_nested_workflow_call(graph: dict) -> bool:
    """True if the graph has any ``execute_workflow`` node.

    Inline execution is only safe when the sub doesn't fan out into more
    sub-workflows: the host's ``call_chain`` ContextVar wouldn't be
    extended with this sub's id (we never enter the chain-set block on
    the host for inline subs), so deep cycle detection beyond one level
    of inlining would break. Falling back to the spawn-fresh path keeps
    chain tracking correct via the existing mechanism.
    """
    nodes = graph.get("nodes") or [] if isinstance(graph, dict) else []
    return any(
        isinstance(n, dict) and n.get("type") == "execute_workflow" for n in nodes
    )


def _extract_sub_leaf(
    sources: set[str],
    node_status: dict[str, str],
    node_outputs: dict[str, dict],
) -> Any:
    """Extract the leaf-node output(s) from a sub-workflow run.

    Same selection rule used by both execution paths: the "leaf" is any
    successful node that no edge originates from (i.e. it has no
    downstream consumers in this graph). One leaf → return its ``main``
    output; multiple leaves → dict keyed by node id; none → ``None``.
    """
    leaves = [
        nid
        for nid, status in node_status.items()
        if status == "success" and nid not in sources
    ]
    if len(leaves) == 1:
        return (node_outputs.get(leaves[0]) or {}).get("main")
    if leaves:
        return {nid: (node_outputs.get(nid) or {}).get("main") for nid in leaves}
    return None


async def _call_sub_workflow(
    workflow_id: str,
    input_value: Any,
    *,
    parent_env_id: str | None = None,
) -> Any:
    """Implementation of ``noodle.context.workflow_caller`` for the API.

    Loads the target workflow's graph, seeds its trigger node with the
    supplied input, runs it through the engine, and returns the output of
    its leaf node (or a dict keyed by leaf id when there are several).

    Three execution paths:

    * **Inline-in-parent** (subprocess mode + parent and sub share an env
      + sub has no nested ``execute_workflow`` nodes) — returns an
      ``InlineSubWorkflow`` sentinel. The pool's callback handler forwards
      the prepared graph/cache/targets back to the parent's subprocess,
      which runs the engine inline and completes the awaiting callback.
      Zero subprocess spawns, zero round-trips through the host runtime
      pool. Fastest path for the common pattern (parent → sub on same env).
    * **Spawn-fresh subprocess** (subprocess mode + different env, OR sub
      contains ``execute_workflow`` nodes) — dispatches through
      ``runtime_pool.dispatch_subworkflow`` which spawns a short-lived
      ``_RuntimeProcess`` for the sub's env outside both pool caps.
    * **In-process** (tests / ``use_subprocess_runner=False``) — runs on
      the host's engine + ``node_registry``.

    Cycle detection: the host-side ``call_chain`` ContextVar is
    inherited by the asyncio task that handles ``call_workflow``
    callbacks from the subprocess. Detection works for the spawn-fresh
    and in-process paths because we enter the ``chain_token`` block
    before invoking the engine. The inline path is restricted to subs
    with no nested workflow calls (see ``_has_nested_workflow_call``),
    so chain depth never exceeds one level past where we set it.

    ``parent_env_id`` is supplied by the pool's callback handler so we
    can detect the inline opportunity; in-process callers (host engine
    invocations via the ``workflow_caller`` ContextVar) leave it ``None``
    and always fall through to the spawn-fresh or in-process branches.
    """
    chain = call_chain.get()
    if workflow_id in chain:
        raise RuntimeError(
            f"sub-workflow cycle detected — '{workflow_id}' is already running"
        )

    async with SessionLocal() as session:
        workflow = await session.get(Workflow, workflow_id)
        sub_env_id = workflow.environment_id if workflow else None
        graph_dict, pinned_cache = await _load_workflow_graph(session, workflow_id)
        graph_dict = await resolve_credential_refs(
            session, graph_dict, workflow_id=workflow_id
        )
        pinned_cache = await resolve_credential_refs(
            session, pinned_cache, workflow_id=workflow_id
        )
        await session.commit()

    graph = WorkflowGraph.model_validate(graph_dict)
    sources = {edge.source for edge in graph.edges}

    cache: dict[str, dict] = deserialize_value(dict(pinned_cache))
    trigger = first_trigger_node(graph)
    if trigger is not None and trigger.id not in cache:
        cache[trigger.id] = {"main": input_value if input_value is not None else {}}

    # Gate the sub-workflow's execution to the trigger we just seeded, so
    # sibling triggers in the same sub-graph don't fire on every call.
    sub_targets = (
        resolve_trigger_targets(graph_dict, trigger.id, None)
        if trigger is not None
        else None
    )

    # Inline opportunity — see docstring. Returned BEFORE the chain_token
    # block because the parent's runtime will execute this sub itself; the
    # restriction to subs without nested workflow calls keeps us from
    # needing to thread chain state into the subprocess.
    inline_eligible = (
        settings.use_subprocess_runner
        and parent_env_id is not None
        and parent_env_id == sub_env_id
        and not _has_nested_workflow_call(graph_dict)
    )
    if inline_eligible:
        logger.info(
            "sub-workflow inline workflow_id=%s env_id=%s",
            workflow_id,
            sub_env_id,
        )
        return InlineSubWorkflow(
            graph=graph_dict,
            cache=cache or None,
            targets=sub_targets,
            sources=sorted(sources),
        )

    chain_token = call_chain.set(chain | {workflow_id})
    try:
        if settings.use_subprocess_runner:
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

            sub_run_id = f"sub_{workflow_id}_{uuid.uuid4().hex[:8]}"
            logger.info(
                "sub-workflow spawn workflow_id=%s env_id=%s parent_env_id=%s",
                workflow_id, sub_env_id, parent_env_id,
            )
            await runtime_pool.dispatch_subworkflow(
                sub_run_id,
                sub_env_id,
                graph_dict,
                cache or None,
                sub_targets,
                collect,
                sub_workflow_caller=_call_sub_workflow,
            )
            return _extract_sub_leaf(sources, node_status, node_outputs)

        # In-process fallback for tests / dev. Same leaf rule as above.
        result = await execute(
            graph,
            node_registry,
            cache=cache or None,
            targets=sub_targets,
            default_timeouts=_engine_default_timeouts(),
            process_isolator=process_isolator,
        )
        node_status = {nid: str(r.status) for nid, r in result.nodes.items()}
        node_outputs = {nid: dict(r.outputs) for nid, r in result.nodes.items()}
        return _extract_sub_leaf(sources, node_status, node_outputs)
    finally:
        call_chain.reset(chain_token)


# A2: the local executor wraps the warm-subprocess pool path. Constructed
# after _call_sub_workflow so the caller reference binds; _active_runs is
# shared by reference, so conftest's reset (which .clear()s it) covers both.
local_executor = LocalExecutor(
    pool=runtime_pool,
    sub_workflow_caller=_call_sub_workflow,
    active_runs=_active_runs,
)


async def _runner_id_for(run_id: str) -> str | None:
    async with SessionLocal() as session:
        run = await session.get(Run, run_id)
        return run.runner_id if run else None


remote_executor = RemoteExecutor(dispatcher=dispatcher, runner_id_for=_runner_id_for)


def _seed_parameters(
    graph: dict,
    cache: dict[str, dict] | None,
    parameters: dict | None,
    *,
    trigger_id: str | None,
) -> dict[str, dict] | None:
    """Seed run parameters into ``trigger_id``'s ``main`` input port.

    An explicit cache entry for that trigger always wins (so a webhook
    payload is never overwritten by deployment defaults). When the caller
    didn't pick a trigger, fall back to the first trigger in graph order so
    legacy callers and the no-trigger path keep working.
    """
    if not parameters:
        return cache
    if trigger_id is None:
        chosen = first_trigger_node(graph)
        if chosen is None:
            return cache
        trigger_id = chosen["id"] if isinstance(chosen, dict) else chosen.id
    next_cache = dict(cache or {})
    if trigger_id not in next_cache:
        next_cache[trigger_id] = {"main": parameters}
    return next_cache


async def _build_env_payload_for_run(env_id: str | None) -> dict:
    """Build the env descriptor sent to a remote runner."""
    from app.models import Environment  # noqa: PLC0415
    if env_id is None:
        return build_env_payload("default", "3.12", [])
    async with SessionLocal() as session:
        env = await session.get(Environment, env_id)
        if env is None:
            return build_env_payload(env_id, "3.12", [])
        return build_env_payload(env_id, env.python_version, env.packages)


async def start_run(
    workflow_id: str,
    graph: dict,
    version: int,
    *,
    workflow_version_id: str | None = None,
    deployment_id: str | None = None,
    triggered_by_error_run_id: str | None = None,
    mode: str = "manual",
    trigger_type: str = "manual",
    targets: list[str] | None = None,
    cache: dict[str, dict] | None = None,
    parameters: dict | None = None,
    trigger_node_id: str | None = None,
    deduplication_key: str | None = None,
    run_id: str | None = None,
) -> str:
    """Create a run record and launch execution in the background.

    Gating rule: when the caller doesn't supply explicit ``targets`` (the
    retry/rerun/"Run this step" paths), execution is restricted to the
    chosen trigger plus its forward descendants — so sibling triggers in
    the same graph don't fire. If ``trigger_node_id`` is None the dispatcher
    picks one deterministically (manual_trigger first, else the first
    trigger in graph order). A graph with no trigger raises ``ValueError``,
    which the router turns into a 400.
    """
    if not targets:
        if trigger_node_id is None:
            chosen = first_trigger_node(graph, prefer_manual=True)
            if chosen is None:
                raise ValueError("Workflow needs a trigger to run.")
            trigger_node_id = (
                chosen["id"] if isinstance(chosen, dict) else chosen.id
            )
        targets = resolve_trigger_targets(graph, trigger_node_id, None)
    elif not targets_have_trigger(graph, targets):
        raise ValueError(
            "Connect a trigger upstream before running this step."
        )

    cache = _seed_parameters(
        graph, cache, parameters, trigger_id=trigger_node_id
    )

    # Multi-tenancy: pin the request/task context to the WORKFLOW's org for
    # everything that follows (Run row stamping, credential resolution, the
    # background execution task — which inherits this context). Callers reach
    # here from every trigger path: user requests (context already matches),
    # webhook ingress (context is the default org), and system loops
    # (scheduler/queue, context unscoped) — the workflow row is the one
    # source of truth for which tenant a run belongs to.
    if settings.multi_tenancy_enabled:
        from app.models import Workflow as _Workflow
        from app.tenancy import current_org_id, run_as_system

        with run_as_system():
            async with SessionLocal() as _org_session:
                _wf_org = await _org_session.scalar(
                    select(_Workflow.org_id).where(_Workflow.id == workflow_id)
                )
        if _wf_org:
            current_org_id.set(_wf_org)

    logger.info(
        "dispatch workflow_id=%s mode=%s trigger_type=%s trigger_node_id=%s "
        "targets=%d cache_keys=%s deployment_id=%s",
        workflow_id,
        mode,
        trigger_type,
        trigger_node_id,
        len(targets) if targets else 0,
        list(cache.keys()) if cache else [],
        deployment_id,
    )

    async with SessionLocal() as session:
        # Resolve runner pool with a clear precedence chain:
        #   1. Deployment override (most specific)
        #   2. Workflow default pool
        #   3. The pool bound to the workflow's Environment
        #   4. None -> in-process runtime pool
        runner_pool_id: str | None = None
        if deployment_id:
            dep = await session.get(Deployment, deployment_id)
            if dep:
                runner_pool_id = dep.runner_pool_id
        wf_obj: Workflow | None = None
        if not runner_pool_id:
            wf_obj = await session.get(Workflow, workflow_id)
            if wf_obj:
                runner_pool_id = wf_obj.default_runner_pool_id
        if not runner_pool_id:
            if wf_obj is None:
                wf_obj = await session.get(Workflow, workflow_id)
            env_id = wf_obj.environment_id if wf_obj else None
            if env_id:
                env_obj = await session.get(Environment, env_id)
                if env_obj is not None:
                    runner_pool_id = env_obj.runner_pool_id
        if wf_obj is None:
            wf_obj = await session.get(Workflow, workflow_id)

        # X4 execution isolation: an org marked dedicated_pool must NEVER run
        # on the shared host warm pool. The run is refused outright (rather
        # than silently degraded) unless it resolves to the org's OWN
        # container-per-run pool. Enforced here — the single chokepoint every
        # trigger path (manual, webhook, schedule, queue, deployment, error
        # workflow, batch) funnels through.
        if settings.multi_tenancy_enabled and wf_obj is not None:
            from app.models import Organization, RunnerPool
            from app.tenancy import run_as_system

            with run_as_system():
                org = await session.get(Organization, wf_obj.org_id)
                if org is not None and org.execution_isolation == "dedicated_pool":
                    pool = (
                        await session.get(RunnerPool, runner_pool_id)
                        if runner_pool_id
                        else None
                    )
                    if (
                        pool is None
                        or pool.org_id != org.id
                        or pool.provider not in ("docker", "kubernetes")
                    ):
                        raise ValueError(
                            "This organization requires isolated execution: "
                            "assign one of its docker/kubernetes runner pools "
                            "to the workflow, environment, or deployment."
                        )

        # C3: executions/day quota — checked and counted at ADMISSION so the
        # ceiling is hard (a burst of starts can't outrun completion-time
        # accounting). Day boundary is UTC.
        if settings.multi_tenancy_enabled and wf_obj is not None:
            from app.services import metering
            from app.services.org_limits import effective_limits

            limits = await effective_limits(session, wf_obj.org_id)
            if limits.executions_per_day:
                used = await metering.runs_today(session, wf_obj.org_id)
                if used >= limits.executions_per_day:
                    raise ValueError(
                        "Daily execution quota reached for this organization "
                        f"({used}/{limits.executions_per_day}). Runs resume "
                        "at midnight UTC, or an owner can raise the quota."
                    )
            await metering.record_run_started(session, wf_obj.org_id)

        # Preflight: block the run if a node needs a package the env lacks.
        preflight_env = None
        if wf_obj and wf_obj.environment_id:
            preflight_env = await session.get(Environment, wf_obj.environment_id)
        if preflight_env is None:
            preflight_env = await session.scalar(
                select(Environment).where(Environment.is_global.is_(True))
            )
        if preflight_env is not None:
            missing = find_missing_packages(graph, list(preflight_env.packages))
            if missing:
                raise ValueError(format_missing(missing))

        if wf_obj is not None and wf_obj.allow_concurrent is False:
            # Single-flight gate — lock the workflow row to serialize concurrent
            # start_run calls; prevents two requests both seeing no active run
            # and both proceeding (TOCTOU). with_for_update is a no-op on SQLite.
            await session.get(Workflow, workflow_id, with_for_update=True)
            existing = await session.scalar(
                select(Run.id)
                .where(Run.workflow_id == workflow_id)
                .where(Run.status.in_(("running", "queued", "waiting")))
                .limit(1)
            )
            if existing is not None:
                raise RuntimeError(
                    "Workflow is configured single-flight and another run is in progress."
                )

        run = Run(
            workflow_id=workflow_id,
            workflow_version=version,
            workflow_version_id=workflow_version_id,
            deployment_id=deployment_id,
            triggered_by_error_run_id=triggered_by_error_run_id,
            mode=mode,
            trigger_type=trigger_type,
            status="running",
            runner_pool_id=runner_pool_id,
            deduplication_key=deduplication_key,
        )
        # A caller can pre-generate the run id (webhook raw-body capture writes
        # artifacts under runs/<run_id>/ before the run exists). Leaving it unset
        # lets the model default mint one.
        if run_id is not None:
            run.id = run_id
        # Local durable queue: a LOCAL run (no remote runner pool) that can't
        # grab an admission slot right now is parked as a durable ``queued``
        # entry instead of blocking a coroutine on the pool semaphore. The
        # dispatch loop leases it when capacity frees — giving visible queue
        # depth and restart durability. Only for async dispatch; synchronous
        # runs (tests) always execute inline.
        queue_locally = (
            settings.local_queue_enabled
            and not settings.run_synchronously
            and runner_pool_id is None
            and not runtime_pool.has_immediate_capacity()
        )
        if queue_locally:
            run.status = "queued"
        session.add(run)
        await session.flush()
        run_id = run.id
        # Durable queue ledger entry; immediate dispatch happens below so this
        # only adds latency cost when capacity is unavailable (failure path
        # transitions the entry back to ``queued`` for the worker to retry).
        await run_queue.enqueue(
            session,
            run_id=run_id,
            workflow_id=workflow_id,
            runner_pool_id=runner_pool_id,
            reason="local_capacity" if queue_locally else "start_run",
        )
        await session.commit()

    # Editor "manual" and "test" (test-URL webhook) runs iterate on the
    # draft; any production trigger (webhook, schedule, deployment, error
    # workflow) must execute the published versions — including for any
    # sub-workflow calls.
    prefer_draft = mode in ("manual", "test")

    # Parked for the local durable queue — the dispatch loop owns it now.
    if queue_locally:
        return run_id

    if settings.run_synchronously:
        current = asyncio.current_task()
        if current is not None:
            _active_runs[run_id] = current
        try:
            await _execute_run(
                run_id, workflow_id, graph, targets, cache,
                prefer_draft=prefer_draft, runner_pool_id=runner_pool_id,
            )
        finally:
            _active_runs.pop(run_id, None)
    else:
        task = asyncio.create_task(
            _execute_run(
                run_id, workflow_id, graph, targets, cache,
                prefer_draft=prefer_draft, runner_pool_id=runner_pool_id,
            )
        )
        _active_runs[run_id] = task
        task.add_done_callback(lambda _task: _active_runs.pop(run_id, None))
    return run_id


async def resume_waiting_run_from_approval(run_id: str, approval_id: str) -> bool:
    """Requeue a waiting run using the stored approved agent action request."""
    async with SessionLocal() as session:
        approval = await session.scalar(
            select(RunApproval).where(
                RunApproval.run_id == run_id,
                RunApproval.id == approval_id,
            )
        )
        run = await session.get(Run, run_id)
        if (
            approval is None
            or run is None
            or approval.status not in {"approved", "rejected"}
            or run.status != "waiting"
            or not isinstance(approval.resume_state, dict)
        ):
            return False

        agent_node_id = str(
            approval.resume_state.get("agent_node_id")
            or approval.agent_node_id
            or approval.node_id
            or ""
        )
        request_state = approval.resume_state.get("request")
        if not agent_node_id or not isinstance(request_state, dict):
            return False

        request = AgentActionRequest.model_validate(request_state)
        if approval.status == "approved":
            approved_ids = set(request.approved_tool_call_ids or [])
            approved_ids.add(approval.tool_call_id)
            request.approved_tool_call_ids = sorted(approved_ids)
        else:
            rejected_ids = set(request.rejected_tool_call_ids or [])
            rejected_ids.add(approval.tool_call_id)
            request.rejected_tool_call_ids = sorted(rejected_ids)

        workflow = await session.scalar(
            select(Workflow)
            .where(Workflow.id == run.workflow_id)
            .options(selectinload(Workflow.versions))
        )
        if workflow is None or not workflow.versions:
            return False

        graph_dict: dict | None = None
        if run.workflow_version_id:
            version_row = await session.scalar(
                select(WorkflowVersion).where(
                    WorkflowVersion.id == run.workflow_version_id
                )
            )
            if version_row is not None:
                graph_dict = version_row.graph
        if not graph_dict:
            graph_dict = workflow.draft_graph or workflow.versions[-1].graph
        if not graph_dict:
            return False

        cache: dict[str, dict] = {}
        skipped_cache_nodes: list[dict[str, Any]] = []
        node_types = _graph_node_types(graph_dict)
        node_runs = (
            await session.scalars(
                select(NodeRun).where(
                    NodeRun.run_id == run_id,
                    NodeRun.status == "success",
                )
            )
        ).all()
        for node_run in node_runs:
            if node_run.node_id == agent_node_id:
                continue
            output = node_run.output
            if not isinstance(output, dict):
                continue
            if _contains_unrestorable_object(output):
                skipped_cache_nodes.append(
                    {
                        "node_id": node_run.node_id,
                        "node_type": node_types.get(node_run.node_id, ""),
                        "reason": "unrestorable_output",
                        "output_ports": sorted(str(port) for port in output.keys()),
                    }
                )
                continue
            cache[node_run.node_id] = output

        resume_targets = sorted(forward_descendants(graph_dict, {agent_node_id}))
        replay_seed = {
            "cache": cache,
            "targets": resume_targets,
            "skipped_cache_nodes": skipped_cache_nodes,
            "agent_action_resume": {
                agent_node_id: request.model_dump(mode="json"),
            },
        }
        max_sequence = await session.scalar(
            select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run_id)
        )
        resume_event = {
            "type": "agent_resume_prepared",
            "approval_id": approval.id,
            "approval_key": approval.approval_key,
            "agent_node_id": agent_node_id,
            "tool_call_id": approval.tool_call_id,
            "tool_name": approval.tool_name,
            "cached_node_ids": sorted(cache.keys()),
            "skipped_cache_nodes": skipped_cache_nodes,
            "targets": resume_targets,
        }
        session.add(
            RunEvent(
                run_id=run_id,
                event_type="agent_resume_prepared",
                sequence=int(max_sequence or 0) + 1,
                ts=datetime.now(UTC),
                node_id=approval.node_id,
                agent_node_id=agent_node_id,
                payload=resume_event,
            )
        )
        entry = await run_queue.resume_waiting(
            session,
            run_id=run_id,
            replay_seed=replay_seed,
        )
        if entry is None:
            return False
        run.status = "queued"
        run.finished_at = None
        await session.commit()

    broker.publish(run_id, resume_event)
    if settings.run_synchronously:
        await _execute_queued_entry(run_id)
    return True


async def cancel_run(run_id: str) -> str | None:
    """Cancel an active run, or mark a stale running record as cancelled.

    For runs dispatched to a remote runner, first tell the agent to stop its
    subprocess (otherwise it keeps executing and later resolves a dead future),
    then cancel the local awaiting task.
    """
    try:
        await remote_executor.cancel(run_id)  # no-op when the run has no runner
    except Exception:  # noqa: BLE001 - notifying the agent is best-effort
        pass

    task = _active_runs.get(run_id)
    if task is not None and not task.done():
        task.cancel()
        return "cancelling"

    async with SessionLocal() as session:
        run = await session.get(Run, run_id)
        if run is None:
            return None
        if run.status in ("running", "queued", "waiting"):
            run.status = "cancelled"
            run.finished_at = datetime.now(UTC)
            await run_queue.cancel(session, run_id=run_id)
            await session.commit()
            broker.publish(
                run_id,
                {
                    "type": "run_cancelled",
                    "run_id": run_id,
                    "error": "Run cancelled",
                },
            )
            broker.publish(
                run_id,
                {"type": "run_finished", "run_id": run_id, "status": "cancelled"},
            )
        return run.status


async def drain_active_runs(timeout: float) -> int:
    """Wait for in-flight runs to finish naturally up to ``timeout`` seconds.

    Used by the lifespan shutdown to honor graceful drain: callers should
    flip ``settings.queue_drain`` first so the dispatch loop stops issuing
    new leases, then call this. Returns the number of runs still active
    when the timeout expired (0 means everything drained cleanly).
    """
    if timeout <= 0:
        return sum(1 for task in _active_runs.values() if not task.done())
    tasks = [task for task in _active_runs.values() if not task.done()]
    if not tasks:
        return 0
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    return len(pending)


async def shutdown_active_runs(timeout: float = 5.0) -> None:
    tasks = [task for task in _active_runs.values() if not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=timeout,
        )


async def _execute_run(
    run_id: str,
    workflow_id: str,
    graph_dict: dict,
    targets: list[str] | None,
    cache: dict[str, dict] | None = None,
    *,
    prefer_draft: bool = False,
    runner_pool_id: str | None = None,
    agent_action_resume: dict[str, AgentActionRequest] | None = None,
) -> None:
    node_events: dict[str, dict] = {}
    # Distinct NodeRun records keyed by (node_id, iteration_path). Non-loop nodes
    # key on an empty path -> one record each; looped body nodes get one per
    # iteration. node_events stays keyed by node_id (last-wins) for back-compat
    # consumers (webhook response, error handlers, guardrails).
    node_run_records: dict[tuple[str, tuple], dict] = {}
    run_events: deque[dict[str, Any]] = deque()
    run_event_sequence = 0
    artifact_refs: list[dict] = []
    secret_values: list[str] = []
    prefer_draft_token = _prefer_draft_graphs.set(prefer_draft)
    run_id_token = _log_run_id.set(run_id)
    _install_run_id_filter()

    async def on_event(event: dict) -> None:
        nonlocal run_event_sequence
        clean = dict(event)
        if "outputs" in clean:
            clean["outputs"] = serialize_value(clean["outputs"])
        if "debug" in clean:
            clean["debug"] = serialize_value(clean["debug"])
        clean = redact_value(clean, secret_values)
        # Artifact refs travel as plain dicts (marker key + JSON fields) and must
        # NOT be wrapped in a typed envelope by serialize_value, or this walk
        # won't see them. serialize_value preserves plain dicts as-is today.
        artifact_refs.extend(collect_artifact_refs(clean))
        broker.publish(run_id, clean)
        if clean.get("type") == "node_finished":
            node_events[clean["node_id"]] = clean
            path = clean.get("iteration_path")
            run_key = (clean["node_id"], tuple(path) if isinstance(path, list) else ())
            node_run_records[run_key] = clean
            debug = clean.get("debug")
            guardrail_events = (
                debug.get("guardrail_events") if isinstance(debug, dict) else None
            )
            if isinstance(guardrail_events, list):
                for raw_guardrail_event in guardrail_events:
                    if not isinstance(raw_guardrail_event, dict):
                        continue
                    event_type = str(raw_guardrail_event.get("type") or "")
                    if event_type not in GUARDRAIL_EVENT_TYPES:
                        continue
                    payload = {
                        **raw_guardrail_event,
                        "node_id": clean.get("node_id"),
                        "node_status": clean.get("status"),
                    }
                    run_event_sequence += 1
                    if len(run_events) < _MAX_RUN_EVENTS:
                        run_events.append(
                            {
                                "sequence": run_event_sequence,
                                "ts": datetime.now(UTC),
                                "event": _cap_output(payload, output_cap),
                            }
                        )
                    broker.publish(run_id, payload)
        if clean.get("type") in AGENT_EVENT_TYPES:
            run_event_sequence += 1
            if len(run_events) < _MAX_RUN_EVENTS:
                run_events.append(
                    {
                        "sequence": run_event_sequence,
                        "ts": datetime.now(UTC),
                        "event": _cap_output(clean, output_cap),
                    }
                )

    broker.publish(run_id, {"type": "run_started", "run_id": run_id})
    status = "success"
    # ``live`` is read inside the cancellation try-block below so a cancel
    # arriving during the DB read still routes through the outer except and
    # the run row reaches its terminal status. Boot defaults are kept for the
    # output cap as a safety fallback.
    output_cap = settings.max_output_bytes
    live: Any = None

    workflow_modules: list[dict] = []
    try:
        # Mark the durable queue entry as running. Inside the outer try so a
        # cancel arriving here still routes through the terminal state writer.
        try:
            async with SessionLocal() as session:
                await run_queue.mark_running(session, run_id=run_id)
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - queue ledger must never block execution
            logger.exception("queue mark_running failed run_id=%s", run_id)

        try:
            live = await get_live_settings()
            output_cap = live.max_output_bytes
        except Exception:  # noqa: BLE001 - never let settings load block a run
            pass

        try:
            async with SessionLocal() as session:
                secret_values = await load_secret_values(session)
        except Exception:  # noqa: BLE001 - redaction should never block execution
            secret_values = []

        async with SessionLocal() as session:
            graph_dict = await resolve_credential_refs(
                session, graph_dict, workflow_id=workflow_id
            )
            if cache is not None:
                cache = await resolve_credential_refs(
                    session, cache, workflow_id=workflow_id
                )
            await session.commit()

        # Gather user code modules visible to this workflow:
        # global + this workflow's env + this workflow. Lives INSIDE the
        # cancellation try block so a cancel during this DB read still
        # routes through the outer except and marks the run cancelled.
        env_id: str | None = None
        run_timeout: float | None = None
        try:
            async with SessionLocal() as session:
                workflow = await session.get(Workflow, workflow_id)
                env_id = workflow.environment_id if workflow else None
                run_timeout = workflow.run_timeout_seconds if workflow else None
                stmt = select(CodeModule).where(
                    or_(
                        CodeModule.scope == "global",
                        CodeModule.workflow_id == workflow_id,
                        (
                            (CodeModule.scope == "environment")
                            & (CodeModule.environment_id == env_id)
                        )
                        if env_id
                        else CodeModule.id.is_(None),  # noop predicate
                    )
                )
                rows = (await session.scalars(stmt)).all()
                workflow_modules = [
                    {
                        "id": m.id,
                        "name": m.name,
                        "contents": m.contents,
                        "include_undecorated": m.include_undecorated,
                    }
                    for m in rows
                ]
        except Exception:  # noqa: BLE001 - missing table on legacy DB is fine
            workflow_modules = []

        if settings.use_subprocess_runner:
            chain_token = call_chain.set(frozenset({workflow_id}))
            caller_token = workflow_caller.set(_call_sub_workflow)
            try:
                if runner_pool_id:
                    # Remote runner path — build env descriptor and dispatch.
                    env_payload = await _build_env_payload_for_run(env_id)
                    try:
                        outcome = await remote_executor.execute(
                            _build_ctx(
                                run_id=run_id, workflow_id=workflow_id,
                                graph=graph_dict, cache=cache, targets=targets,
                                environment_id=env_id,
                                runner_pool_id=runner_pool_id,
                                env_payload=env_payload,
                                workflow_modules=workflow_modules,
                                run_timeout=run_timeout,
                                agent_action_resume=agent_action_resume,
                            ),
                            on_event,
                        )
                        status = outcome.status
                    except _QueuedError as queued_exc:
                        # No runner capacity right now. Reset both ledgers
                        # to "queued" so the durable queue's dispatch loop
                        # retries with backoff when capacity frees. If the
                        # queue has exhausted its retry budget the entry is
                        # dead-lettered; mirror that onto Run.status="error"
                        # so the run doesn't appear queued forever.
                        async with SessionLocal() as session:
                            entry = await run_queue.fail(
                                session,
                                run_id=run_id,
                                retryable=True,
                                error=str(queued_exc) or "no runner capacity",
                            )
                            run = await session.get(Run, run_id)
                            if run is not None:
                                if entry is not None and entry.status == "queued":
                                    run.status = "queued"
                                    run.finished_at = None
                                else:
                                    run.status = "error"
                                    run.finished_at = datetime.now(UTC)
                            await session.commit()
                        _prefer_draft_graphs.reset(prefer_draft_token)
                        _log_run_id.reset(run_id_token)
                        return
                else:
                    outcome = await local_executor.execute(
                        _build_ctx(
                            run_id=run_id, workflow_id=workflow_id,
                            graph=graph_dict, cache=cache, targets=targets,
                            environment_id=env_id, runner_pool_id=None,
                            env_payload=None, workflow_modules=workflow_modules,
                            run_timeout=run_timeout,
                            agent_action_resume=agent_action_resume,
                        ),
                        on_event,
                    )
                    status = outcome.status
            finally:
                workflow_caller.reset(caller_token)
                call_chain.reset(chain_token)
        else:
            # In-process path: register modules into the host's registry for
            # the duration of the run, then strip them on the way out so we
            # don't leak custom nodes across runs / workflows.
            loaded_module_ids: list[str] = []
            for module in workflow_modules:
                if not module.get("contents", "").strip():
                    continue
                try:
                    register_module_functions(
                        module["id"],
                        module["contents"],
                        node_registry,
                        include_undecorated=bool(module.get("include_undecorated")),
                    )
                    loaded_module_ids.append(module["id"])
                except Exception as exc:  # noqa: BLE001 - bad code surfaces in the run
                    broker.publish(
                        run_id,
                        redact_value(
                            {
                                "type": "module_error",
                                "module_id": module["id"],
                                "error": f"{type(exc).__name__}: {exc}",
                            },
                            secret_values,
                        ),
                    )
            chain_token = call_chain.set(frozenset({workflow_id}))
            caller_token = workflow_caller.set(_call_sub_workflow)
            pool_key_token = engine_pool_key.set(env_id)
            _run_org = await _resolve_run_org(run_id)
            limits_token = org_run_limits.set(
                await _org_run_limits_for(_run_org)
            )
            artifact_token = artifact_store.set(
                make_artifact_store(
                    run_id,
                    org_id=_run_org,
                    max_bytes=live.max_artifact_bytes if live is not None else None,
                    max_count=live.max_artifacts_per_run if live is not None else None,
                )
            )
            try:
                graph = WorkflowGraph.model_validate(graph_dict)
                # Bound top-level in-process runs by the same global ceiling
                # subprocess ``dispatch`` uses, so an in-process deployment
                # can't spawn unbounded concurrent engine runs. Sub-workflows
                # reached via ``workflow_caller``/``_call_sub_workflow`` call
                # ``execute`` directly WITHOUT this slot, so a parent waiting
                # on a child never deadlocks (mirrors the subprocess split).
                _eff_timeout = run_timeout if (run_timeout and run_timeout > 0) else (
                    settings.workflow_run_timeout_seconds or None
                )
                async with runtime_pool.global_slot():
                    coro = execute(
                        graph,
                        node_registry,
                        cache=deserialize_value(cache),
                        targets=targets,
                        on_event=on_event,
                        default_timeouts=_engine_default_timeouts(),
                        pause_on_approval=True,
                        agent_action_resume=agent_action_resume,
                        process_isolator=process_isolator,
                    )
                    result = await (
                        asyncio.wait_for(coro, timeout=_eff_timeout)
                        if _eff_timeout
                        else coro
                    )
                status = str(result.status)
            finally:
                artifact_store.reset(artifact_token)
                org_run_limits.reset(limits_token)
                engine_pool_key.reset(pool_key_token)
                workflow_caller.reset(caller_token)
                call_chain.reset(chain_token)
                for module_id in loaded_module_ids:
                    unregister_module(module_id, node_registry)
    except asyncio.CancelledError:
        status = "cancelled"
        broker.publish(
            run_id,
            redact_value(
                {"type": "run_cancelled", "run_id": run_id, "error": "Run cancelled"},
                secret_values,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - report any execution failure
        status = "error"
        logger.exception("run_id=%s execution failed: %s", run_id, exc)
        broker.publish(
            run_id,
            redact_value(
                {
                    "type": "run_error",
                    "error": f"{type(exc).__name__}: {exc}",
                },
                secret_values,
            ),
        )

    # Publish the terminal event BEFORE the DB session so that a DB failure
    # (e.g. a connection reset during the persist below) never leaves the
    # client's WebSocket waiting indefinitely for a run_finished that won't come.
    if status == "waiting":
        broker.publish(
            run_id, {"type": "run_waiting", "run_id": run_id, "status": status}
        )
    else:
        broker.publish(
            run_id, {"type": "run_finished", "run_id": run_id, "status": status}
        )

    # SessionLocal / start_run are resolved from this module's globals at call
    # time so the test-suite swaps (conftest, monkeypatch) keep applying.
    await run_persistence.persist_run_outcome(
        SessionLocal,
        run_id=run_id, status=status, graph_dict=graph_dict,
        node_events=node_events, node_run_records=node_run_records,
        run_events=run_events, output_cap=output_cap,
    )

    try:
        await persist_artifact_refs(run_id, artifact_refs)
    except Exception:  # noqa: BLE001 - artifact refs are best-effort
        logger.exception("run_id=%s failed to persist artifact refs", run_id)

    if status == "error":
        await run_alerts.dispatch_error_handlers(
            SessionLocal, start_run,
            run_id=run_id, node_events=node_events, secret_values=secret_values,
        )

    _prefer_draft_graphs.reset(prefer_draft_token)
    _log_run_id.reset(run_id_token)


async def _execute_queued_entry(run_id: str) -> None:
    """Re-attempt dispatch of a queued run after a queue worker leases its entry.

    Reloads the run row, recomputes targets from the workflow graph (using
    the same trigger-selection logic as ``start_run``), and delegates to
    ``_execute_run``. ``_execute_run`` handles queue lifecycle transitions
    (mark_running / complete / fail / cancel) end-to-end.
    """
    async with SessionLocal() as session:
        run = await session.get(Run, run_id)
        if run is None or run.status != "queued":
            return
        runner_pool_id = run.runner_pool_id
        workflow_id = run.workflow_id
        mode = run.mode
        wf_version_id = run.workflow_version_id

        # Pick up any replay-from-failure seed left by the replay endpoint
        # before we transition the entry to running.
        queue_entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
        )
        replay_seed: dict | None = (
            dict(queue_entry.replay_seed) if queue_entry and queue_entry.replay_seed else None
        )
        if queue_entry is not None and queue_entry.replay_seed:
            queue_entry.replay_seed = None  # consumed; don't re-apply on later retries

        workflow = await session.scalar(
            select(Workflow)
            .where(Workflow.id == workflow_id)
            .options(selectinload(Workflow.versions))
        )
        if workflow is None or not workflow.versions:
            run.status = "error"
            run.finished_at = datetime.now(UTC)
            await session.commit()
            return

        # Pick the version that was originally dispatched if available, else
        # the workflow's draft graph (the editor's working copy) so manual /
        # test runs replay correctly. Falling back to the latest *published*
        # version would replay against the wrong graph entirely.
        graph_dict: dict | None = None
        if wf_version_id:
            version_row = await session.scalar(
                select(WorkflowVersion).where(WorkflowVersion.id == wf_version_id)
            )
            if version_row:
                graph_dict = version_row.graph
        if not graph_dict:
            graph_dict = workflow.draft_graph or (
                workflow.versions[-1].graph if workflow.versions else None
            )

        if not graph_dict:
            run.status = "error"
            run.finished_at = datetime.now(UTC)
            await session.commit()
            return

        pinned_rows = await session.scalars(
            select(PinnedData).where(PinnedData.workflow_id == workflow_id)
        )
        pinned_cache: dict = {row.node_id: row.payload for row in pinned_rows.all()}

        run.status = "running"
        await session.commit()

    trigger = first_trigger_node(graph_dict)
    trigger_id = (trigger.id if hasattr(trigger, "id") else trigger["id"]) if trigger else None
    targets = resolve_trigger_targets(graph_dict, trigger_id, None) if trigger_id else None
    cache: dict | None = pinned_cache or None

    if replay_seed:
        seed_cache = replay_seed.get("cache")
        seed_targets = replay_seed.get("targets")
        seed_agent_resume = replay_seed.get("agent_action_resume")
        if isinstance(seed_cache, dict) and seed_cache:
            merged: dict = dict(cache or {})
            merged.update(seed_cache)
            cache = merged
        if isinstance(seed_targets, list) and seed_targets:
            targets = list(seed_targets)
        agent_action_resume = None
        if isinstance(seed_agent_resume, dict):
            agent_action_resume = {
                str(node_id): AgentActionRequest.model_validate(request)
                for node_id, request in seed_agent_resume.items()
                if isinstance(request, dict)
            }
    else:
        agent_action_resume = None

    await _execute_run(
        run_id, workflow_id, graph_dict, targets, cache,
        prefer_draft=(mode in ("manual", "test")),
        runner_pool_id=runner_pool_id,
        agent_action_resume=agent_action_resume,
    )


