"""Workflow execution service.

Runs a workflow graph in-process with the Nodyra engine, streams per-node
events to the broker for live editor updates, and persists the run.

Sub-workflow semantics (cycle/depth/inline) live in the engine
(``nodyra.engine.subworkflows``); this host supplies the resolver
(``app.services.subworkflows.resolve_subworkflow``) and per-run meta.
"""

import asyncio
import logging
import time
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import nodyra_nodes  # noqa: F401 - importing registers the built-in nodes
from app import tracing
from app.config import settings
from app.db import SessionLocal
from app.exceptions import (
    DedicatedPoolRequired,
    PackageNotInstalled,
    QuotaExceeded,
    SandboxRequired,
    SingleFlightConflict,
    StepNeedsUpstreamTrigger,
    WorkflowNeedsTrigger,
)
from app.models import (
    CodeModule,
    Deployment,
    Environment,
    PinnedData,
    Run,
    RunQueueEntry,
    Workflow,
    WorkflowVersion,
)
from app.services import queue as run_queue
from app.services import (
    run_alerts,
    run_checkpoints,
    run_persistence,
    run_resume,
    sandbox_pool,
)
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
from app.services.executors.sandbox import SandboxExecutor
from app.services.graph_utils import (
    first_trigger_node,
    resolve_trigger_targets,
    targets_have_trigger,
)
from app.services.live_settings import get_live_settings
from app.services.package_preflight import (
    find_missing_packages,
    format_missing,
    format_missing_workflow_requirements,
    missing_workflow_requirements,
)
from app.services.redaction import (
    load_secret_values,
    load_secret_values_for_org,
    redact_value,
)
from app.services.remote_dispatch import (
    _QueuedError,
    build_env_payload,
    dispatcher,
)
from app.services.runtime_pool import _org_run_limits_for, _resolve_run_org
from app.services.runtime_pool import pool as runtime_pool
from app.services.sandbox_policy import resolve_execution_mode, resolve_sandbox_overrides
from app.services.subworkflows import meta_for_root_run, resolve_subworkflow
from nodyra.ai_runtime import AgentActionRequest
from nodyra.context import artifact_store, org_run_limits
from nodyra.engine import DEFAULT_NODE_TIMEOUTS, execute
from nodyra.engine.types import set_call_mcp_tool_impl
from nodyra.models import WorkflowGraph
from nodyra.process_isolation import (
    PooledProcessIsolator,
)
from nodyra.process_isolation import (
    pool_key as engine_pool_key,
)
from nodyra.sdk import (
    register_module_functions,
    unregister_module,
)
from nodyra.sdk import (
    registry as node_registry,
)
from nodyra.serialization import (
    deserialize_value,
    serialize_value,
)

# ContextVar that carries the current run_id into every log record emitted
# while _execute_run is active, without requiring callers to pass it explicitly.
_log_run_id: ContextVar[str] = ContextVar("nodyra_log_run_id", default="")


class _RunIdFilter(logging.Filter):
    """Inject ``run_id`` from the ContextVar into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _log_run_id.get("")  # type: ignore[attr-defined]
        return True


def _install_run_id_filter() -> None:
    root = logging.getLogger("nodyra")
    for log_filter in root.filters:
        if isinstance(log_filter, _RunIdFilter):
            return
    root.addFilter(_RunIdFilter())


logger = logging.getLogger(__name__)

# B4: the API host owns the code-node process pools. One isolator for the
# whole process; pools inside it are still keyed per environment via
# engine_pool_key, exactly as before.
process_isolator = PooledProcessIsolator()

_active_runs: dict[str, asyncio.Task[None]] = {}

# Per-workflow asyncio.Lock for the single-flight gate on SQLite (B-7 fix).
# Postgres uses row-level ``with_for_update``; this dict provides equivalent
# in-process serialisation for SQLite's single-writer model.
_workflow_single_flight_locks: dict[str, asyncio.Lock] = {}
_SINGLE_FLIGHT_LOCKS_MAX = 1024


def _prune_single_flight_locks() -> None:
    if len(_workflow_single_flight_locks) <= _SINGLE_FLIGHT_LOCKS_MAX:
        return
    for wf_id in [key for key, lock in _workflow_single_flight_locks.items() if not lock.locked()]:
        del _workflow_single_flight_locks[wf_id]
        if len(_workflow_single_flight_locks) <= _SINGLE_FLIGHT_LOCKS_MAX:
            break


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

# A2 follow-up: checkpointing moved to run_checkpoints. Aliases keep this
# module's established import surface; patch app.services.run_checkpoints to
# alter checkpoint behaviour in tests (conftest swaps its SessionLocal
# alongside every other service module).
_MAX_CHECKPOINT_BYTES = run_checkpoints._MAX_CHECKPOINT_BYTES
_CheckpointDebouncer = run_checkpoints._CheckpointDebouncer
_serialize_checkpoint_outputs = run_checkpoints._serialize_checkpoint_outputs
_save_checkpoint = run_checkpoints._save_checkpoint


def _agent_pool_sandbox_ok(pool: Any) -> bool:
    """True when an *agent* runner pool is explicitly configured to run
    sandboxed workflows — i.e. its Docker workers were created with the
    "Sandboxed execution support" checkbox on
    (``provider_config.docker_runner.sandbox == True``).

    This is the ONLY way an agent pool may host a sandboxed run: its runners
    advertise ``capabilities.sandbox`` and execute each run in a disposable
    hardened container (``nodyra_runner_agent.sandbox_exec``). A plain agent
    pool (no such config) stays rejected — it would run the code unsandboxed
    on the agent host. See test_execution_mode.test_sandboxed_workflow_409s_
    on_non_container_pool (plain agent pool → still 409).
    """
    if pool is None or getattr(pool, "provider", None) != "agent":
        return False
    cfg = getattr(pool, "provider_config", None) or {}
    return bool((cfg.get("docker_runner") or {}).get("sandbox"))


def _sandboxed_pool_ok(pool: Any) -> bool:
    """A runner pool may host a sandboxed run when it is a container provider
    (docker/kubernetes spawn the hardened container themselves) OR a
    sandbox-configured agent pool (its runners spawn the container)."""
    provider = getattr(pool, "provider", None)
    return provider in ("docker", "kubernetes") or _agent_pool_sandbox_ok(pool)


def _build_ctx(
    *,
    run_id: str,
    workflow_id: str,
    graph: dict,
    cache: dict | None,
    targets: list[str] | None,
    environment_id: str | None,
    runner_pool_id: str | None,
    env_payload: dict | None,
    workflow_modules: list[dict],
    run_timeout: float | None,
    agent_action_resume: dict[str, AgentActionRequest] | None,
    sandbox_spawn_overrides: dict | None = None,
    subworkflow_meta: dict | None = None,
    org_id: str | None = None,
    sandbox_required: bool = False,
) -> RunExecutionContext:
    return {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "org_id": org_id,
        "graph": graph,
        "cache": cache,
        "targets": targets,
        "environment_id": environment_id,
        "runner_pool_id": runner_pool_id,
        "env_payload": env_payload,
        "workflow_modules": workflow_modules,
        "run_timeout": run_timeout,
        "sandbox_spawn_overrides": sandbox_spawn_overrides,
        "sandbox_required": sandbox_required,
        "default_timeouts": _engine_default_timeouts(),
        "pause_on_approval": True,
        "agent_action_resume": (
            {nid: req.model_dump(mode="json") for nid, req in agent_action_resume.items()}
            if agent_action_resume
            else None
        ),
        "subworkflow_meta": subworkflow_meta,
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


# A2: the local executor wraps the warm-subprocess pool path. The resolver
# lives in app.services.subworkflows (A3); _active_runs is shared by
# reference, so conftest's reset (which .clear()s it) covers both.
local_executor = LocalExecutor(
    pool=runtime_pool,
    subworkflow_resolver=resolve_subworkflow,
    active_runs=_active_runs,
)

# Phase D: when sandbox mode is active (init_sandbox configured the pool),
# runs that resolve to NO runner pool execute in disposable hardened
# containers instead of the shared warm-subprocess pool.
sandbox_executor = SandboxExecutor(
    pool=sandbox_pool.pool,
    subworkflow_resolver=resolve_subworkflow,
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
        return build_env_payload(
            env_id, env.python_version, env.packages, runtime_flags=env.runtime_flags
        )


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
    batch_id: str | None = None,
    runner_pool_id: str | None = None,
    execution_mode: str | None = None,
    required_labels: dict | None = None,
) -> str:
    """Public run launcher that restores any caller tenant context.

    ``_start_run_impl`` needs the workflow's org as the ambient context while
    it stamps DB rows and spawns the execution task. The caller may be a request
    for another org, a webhook with default-org context, or a system scheduler
    loop. Always reset the caller's ContextVar token on the way out so one run
    cannot pin a long-lived background loop to its org.
    """
    org_token = None
    if settings.multi_tenancy_enabled:
        from app.models import Workflow as _Workflow
        from app.tenancy import current_org_id, run_as_system

        with run_as_system():
            async with SessionLocal() as _org_session:
                _wf_org = await _org_session.scalar(
                    select(_Workflow.org_id).where(_Workflow.id == workflow_id)
                )
        if _wf_org:
            org_token = current_org_id.set(_wf_org)
    try:
        return await _start_run_impl(
            workflow_id,
            graph,
            version,
            workflow_version_id=workflow_version_id,
            deployment_id=deployment_id,
            triggered_by_error_run_id=triggered_by_error_run_id,
            mode=mode,
            trigger_type=trigger_type,
            targets=targets,
            cache=cache,
            parameters=parameters,
            trigger_node_id=trigger_node_id,
            deduplication_key=deduplication_key,
            pre_run_id=run_id,
            batch_id=batch_id,
            runner_pool_id=runner_pool_id,
            execution_mode=execution_mode,
            required_labels=required_labels,
        )
    finally:
        if org_token is not None:
            current_org_id.reset(org_token)


def _expanded_for_gating(graph: dict) -> dict:
    """Inline transparent metanodes so namespaced step-run targets
    ("<meta_id>/<child>") resolve to real nodes for the trigger-upstream gate.

    Mirrors the engine's run-time expansion (``scheduler`` calls the same
    ``_expand_graph_dict``). Best-effort: falls back to the original graph if
    expansion fails, so gating never crashes a run request.
    """
    try:
        from nodyra.engine.metanodes import _expand_graph_dict

        return _expand_graph_dict(graph)
    except Exception:  # pragma: no cover - defensive
        logger.debug("metanode expansion for gating failed", exc_info=True)
        return graph


async def _start_run_impl(
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
    pre_run_id: str | None = None,
    batch_id: str | None = None,
    runner_pool_id: str | None = None,
    execution_mode: str | None = None,
    required_labels: dict | None = None,
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
                raise WorkflowNeedsTrigger("Workflow needs a trigger to run.")
            trigger_node_id = chosen["id"] if isinstance(chosen, dict) else chosen.id
        targets = resolve_trigger_targets(graph, trigger_node_id, None)
    elif not targets_have_trigger(_expanded_for_gating(graph), targets):
        # Step-run targets inside a transparent metanode are namespaced
        # ("<meta_id>/<child>"); they only gain their upstream trigger once the
        # metanode is inlined (the engine does this at execute time), so gate
        # against the expanded graph too.
        raise StepNeedsUpstreamTrigger("Connect a trigger upstream before running this step.")

    cache = _seed_parameters(graph, cache, parameters, trigger_id=trigger_node_id)

    from app.services.metrics import run_starts_total

    run_starts_total.inc(mode=mode, trigger_type=trigger_type)

    # Per-workflow rate limiting (best-effort, in-process).
    # Production deployments with multiple replicas should use Redis
    # for shared counters; single-process self-hosted is correct as-is.
    from app.services.rate_limit import allow as _rate_allow

    _wf_rate_limit = getattr(settings, "workflow_run_rate_per_minute", 0) or 0
    if _wf_rate_limit > 0 and not _rate_allow(
        "workflow_run",
        workflow_id,
        limit=_wf_rate_limit,
        window_seconds=60,
    ):
        raise QuotaExceeded(
            f"Rate limit exceeded for this workflow. Maximum {_wf_rate_limit} runs per minute."
        )

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
        #   3. The pool bound to the workflow's effective Environment — the
        #      explicit one, or the global env for implicit-Global workflows
        #   4. None -> in-process runtime pool
        if runner_pool_id is None and deployment_id:
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
            env_obj = await session.get(Environment, env_id) if env_id else None
            if env_obj is None:
                # Implicit-Global workflows (no explicit environment_id) still
                # execute in the global env, so they must honour the pool bound
                # to it. Without this fallback the Environments page's "runs here
                # execute on <pool>" binding is silently ignored for them.
                env_obj = await session.scalar(
                    select(Environment).where(Environment.is_global.is_(True))
                )
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
                    pool = await session.get(RunnerPool, runner_pool_id) if runner_pool_id else None
                    if (
                        pool is None
                        or pool.org_id != org.id
                        or pool.provider not in ("docker", "kubernetes")
                    ):
                        raise DedicatedPoolRequired(
                            "This organization requires isolated execution: "
                            "assign one of its docker/kubernetes runner pools "
                            "to the workflow, environment, or deployment."
                        )

        wf_mode = getattr(wf_obj, "execution_mode", "inherit") if wf_obj else "inherit"
        effective_mode = resolve_execution_mode(
            run_override=execution_mode,
            workflow_mode=wf_mode,
        )
        if effective_mode == "sandboxed":
            if runner_pool_id:
                # A run bound to a runner pool executes THERE, never in the
                # container sandbox — so the pool must be able to sandbox it:
                # a docker/kubernetes container provider, OR an agent pool whose
                # Docker workers were created with the sandbox checkbox on
                # (they run each run in a hardened disposable container). A
                # plain agent pool is rejected — it would run the code
                # unsandboxed on the agent host.
                from app.models import RunnerPool as _RunnerPool

                pool = await session.get(_RunnerPool, runner_pool_id)
                if not _sandboxed_pool_ok(pool):
                    raise SandboxRequired(
                        "This run requires sandboxed execution, but its "
                        f"runner pool (provider="
                        f"{getattr(pool, 'provider', None)!r}) cannot sandbox "
                        "it. Assign a docker/kubernetes pool or an agent pool "
                        "with Docker workers that have sandboxed execution "
                        "enabled, or clear the pool so the container sandbox "
                        "runs it."
                    )
            elif settings.execution_sandbox == "off":
                raise SandboxRequired(
                    "This run requires sandboxed execution. Enable "
                    "EXECUTION_SANDBOX=auto|required on the worker (see "
                    "deploy/docker-compose.sandbox.yml), or assign a "
                    "docker/kubernetes runner pool."
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
                    raise QuotaExceeded(
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
                raise PackageNotInstalled(format_missing(missing))
            missing_requirements = missing_workflow_requirements(
                workflow_requirements=list(wf_obj.requirements or []) if wf_obj else [],
                installed=list(preflight_env.packages or []),
            )
            if missing_requirements:
                raise PackageNotInstalled(
                    format_missing_workflow_requirements(missing_requirements)
                )

        if wf_obj is not None and wf_obj.allow_concurrent is False:
            # Single-flight gate — serialize concurrent start_run calls so two
            # requests can't both see no active run and both proceed (TOCTOU).
            #
            # Postgres: ``with_for_update`` row-locks the workflow; the SELECT
            # for active runs below sees any concurrent INSERT that committed
            # after our lock was acquired.
            #
            # SQLite: ``with_for_update`` is a no-op.  Instead we use a
            # per-workflow asyncio.Lock scoped to this process.  SQLite is
            # single-writer anyway, so in-process serialisation + WAL-mode
            # write barrier is correct for single-replica deployments.
            _lock: asyncio.Lock | None = None
            if not settings.database_url.startswith("postgresql"):
                _prune_single_flight_locks()
                _lock = _workflow_single_flight_locks.setdefault(workflow_id, asyncio.Lock())
                await _lock.acquire()
            try:
                if settings.database_url.startswith("postgresql"):
                    await session.get(Workflow, workflow_id, with_for_update=True)
                existing = await session.scalar(
                    select(Run.id)
                    .where(Run.workflow_id == workflow_id)
                    .where(Run.status.in_(("running", "queued", "waiting")))
                    .limit(1)
                )
                if existing is not None:
                    raise SingleFlightConflict(
                        "Workflow is configured single-flight and another run is in progress."
                    )
            finally:
                if _lock is not None:
                    _lock.release()

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
            execution_mode=("sandboxed" if execution_mode == "sandboxed" else None),
            required_labels=run_queue.normalize_required_labels(required_labels),
            deduplication_key=deduplication_key,
            batch_id=batch_id,
        )
        # A caller can pre-generate the run id (webhook raw-body capture writes
        # artifacts under runs/<pre_run_id>/ before the run exists). Leaving it
        # unset lets the model default mint one.  Check for collisions (UUID4 hex
        # is astronomically unlikely to collide, but the DB is the authority).
        if pre_run_id is not None:
            existing = await session.get(Run, pre_run_id)
            if existing is not None:
                logger.warning(
                    "pre_generated run_id %s already exists; falling back to auto-generated id",
                    pre_run_id,
                )
            else:
                run.id = pre_run_id
        # Park the run on the durable queue instead of dispatching inline when
        # (a) this replica is a pure control plane (dispatch_role=disabled —
        # applies to remote-pool runs too; a worker or WS-holding replica
        # leases it), or (b) it's a local run with no immediate admission slot
        # (parked rather than blocking a coroutine on the pool semaphore; the
        # dispatch loop leases it when capacity frees — visible queue depth +
        # restart durability). Synchronous runs (tests) always execute inline.
        # ``control`` parks runs like ``disabled`` (it owns no local/docker
        # execution — a worker does); its dispatch loop then leases only the
        # agent/kubernetes entries whose WebSocket terminates on this replica.
        queue_locally = not settings.run_synchronously and (
            settings.dispatch_role in ("disabled", "control")
            or (
                settings.local_queue_enabled
                and runner_pool_id is None
                and not runtime_pool.has_immediate_capacity()
            )
        )
        if queue_locally:
            run.status = "queued"
        session.add(run)
        await session.flush()
        run_id = run.id
        # Durable queue ledger entry; immediate dispatch happens below so this
        # only adds latency cost when capacity is unavailable (failure path
        # transitions the entry back to ``queued`` for the worker to retry).
        # A5: the enqueue span is the trace root for this run (child of the
        # HTTP request span when FastAPI instrumentation is on). Its carrier
        # rides the queue entry so a worker in another process joins the trace.
        # A parked run executes in another process which rebuilds dispatch
        # state from the DB — the in-memory ``cache`` (trigger payloads: chat
        # message, webhook body) and ``targets`` (partial-run selection) would
        # otherwise be lost, silently executing an empty or full graph instead.
        park_seed: dict | None = None
        if queue_locally and (cache or targets):
            park_seed = {}
            if cache:
                park_seed["cache"] = cache
            if targets:
                park_seed["targets"] = list(targets)
        with tracing.span(
            "run.enqueue",
            attributes={"nodyra.run_id": run_id, "nodyra.workflow_id": workflow_id},
        ):
            trace_carrier = tracing.inject_context()
            entry = await run_queue.enqueue(
                session,
                run_id=run_id,
                workflow_id=workflow_id,
                runner_pool_id=runner_pool_id,
                reason=(
                    (
                        "dispatch_disabled"
                        if settings.dispatch_role in ("disabled", "control")
                        else "local_capacity"
                    )
                    if queue_locally
                    else "start_run"
                ),
                trace_context=trace_carrier,
                replay_seed=park_seed,
                required_labels=run.required_labels,
            )
            if not queue_locally:
                # This process owns immediate execution. Keep the queue ledger
                # for observability/completion bookkeeping, but make it
                # non-leaseable before commit so a dispatch loop woken by a
                # nearby enqueue cannot execute the same run a second time.
                entry.status = "running"
        await session.commit()
        if queue_locally:
            await run_queue.notify_queue_workers()

    # Editor "manual" and "test" (test-URL webhook) runs iterate on the
    # draft; any production trigger (webhook, schedule, deployment, error
    # workflow) must execute the published versions — including for any
    # sub-workflow calls. A "manual" run with a pinned version is a
    # published-graph dispatch (editor use_draft=false, MCP run_workflow
    # use_draft=false, deployment "Run now") and must NOT prefer drafts.
    # "test" runs anchor workflow_version_id to the latest version for
    # history sanity but still execute the draft, hence the mode split.
    prefer_draft = mode == "test" or (mode == "manual" and workflow_version_id is None)

    # Parked for the local durable queue — the dispatch loop owns it now.
    if queue_locally:
        return run_id

    if settings.run_synchronously:
        current = asyncio.current_task()
        if current is not None:
            _active_runs[run_id] = current
        try:
            await _execute_run(
                run_id,
                workflow_id,
                graph,
                targets,
                cache,
                prefer_draft=prefer_draft,
                runner_pool_id=runner_pool_id,
                run_execution_mode=run.execution_mode,
                trace_carrier=trace_carrier,
            )
        finally:
            _active_runs.pop(run_id, None)
    else:
        task = asyncio.create_task(
            _execute_run(
                run_id,
                workflow_id,
                graph,
                targets,
                cache,
                prefer_draft=prefer_draft,
                runner_pool_id=runner_pool_id,
                run_execution_mode=run.execution_mode,
                trace_carrier=trace_carrier,
            )
        )
        _active_runs[run_id] = task
        task.add_done_callback(lambda _task: _active_runs.pop(run_id, None))
    return run_id


async def resume_waiting_run_from_approval(
    run_id: str, approval_id: str, *, approve_all: bool = False
) -> bool:
    """Requeue a waiting run using the stored approved agent action request."""
    resume_event = await run_resume.resume_waiting_run_from_approval(
        SessionLocal, run_id=run_id, approval_id=approval_id, approve_all=approve_all
    )
    if resume_event is None:
        return False
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

    # Sandbox runs: hard-kill the container directly when no awaiting task
    # was found (e.g. a queue-driven worker awaiting pool.dispatch).
    try:
        if await sandbox_pool.pool.cancel(run_id):
            return "cancelling"
    except Exception:  # noqa: BLE001 - container teardown is best-effort
        pass

    from app.tenancy import run_as_system

    async with SessionLocal() as session:
        with run_as_system():
            run = await session.get(Run, run_id)
        if run is None:
            return None
        if run.status in ("running", "queued", "waiting"):
            await run_queue.cancel(session, run_id=run_id)
            await session.flush()
            run.status = "cancelled"
            run.finished_at = datetime.now(UTC)
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
    run_execution_mode: str | None = None,
    agent_action_resume: dict[str, AgentActionRequest] | None = None,
    trace_carrier: dict | None = None,
) -> None:
    """Tracing wrapper: opens the run.execute span (parented on ``trace_carrier``
    when given, else ambient context) around the real executor. A plain
    pass-through when tracing is off."""
    org_id = await _resolve_run_org(run_id)
    org_token = None
    if settings.multi_tenancy_enabled:
        from app.tenancy import current_org_id

        org_token = current_org_id.set(org_id)
    try:
        if not tracing.enabled():
            await _execute_run_impl(
                run_id,
                workflow_id,
                graph_dict,
                targets,
                cache,
                prefer_draft=prefer_draft,
                runner_pool_id=runner_pool_id,
                run_execution_mode=run_execution_mode,
                agent_action_resume=agent_action_resume,
                trace_org=org_id,
                run_org_id=org_id,
            )
            return
        attrs = {"nodyra.run_id": run_id, "nodyra.workflow_id": workflow_id}
        if org_id:
            attrs["nodyra.org_id"] = org_id
        with tracing.span("run.execute", carrier=trace_carrier, attributes=attrs) as sp:
            trace_id = tracing.trace_id_from_span(sp)
            if trace_id:
                await _record_run_trace_id(run_id, trace_id)
            status = await _execute_run_impl(
                run_id,
                workflow_id,
                graph_dict,
                targets,
                cache,
                prefer_draft=prefer_draft,
                runner_pool_id=runner_pool_id,
                run_execution_mode=run_execution_mode,
                agent_action_resume=agent_action_resume,
                trace_org=org_id,
                run_org_id=org_id,
            )
            if sp is not None:
                sp.set_attribute("nodyra.status", status)
    finally:
        if org_token is not None:
            current_org_id.reset(org_token)


async def _record_run_trace_id(run_id: str, trace_id: str) -> None:
    async with SessionLocal() as session:
        run = await session.get(Run, run_id)
        if run is None:
            return
        if run.trace_id == trace_id:
            return
        run.trace_id = trace_id
        await session.commit()


@dataclass
class _PreparedRunContext:
    """Output of ``_prepare_run_context`` — all pre-flight state for a run."""

    graph_dict: dict
    cache: dict[str, dict] | None
    env_id: str | None
    run_timeout: float | None
    workflow_modules: list[dict]
    secret_values: list[str]
    output_cap: int
    execution_mode: str = "inherit"
    sandbox_resources: dict | None = None
    # Live-settings artifact caps; None falls back to boot settings inside
    # make_artifact_store.
    max_artifact_bytes: int | None = None
    max_artifacts_per_run: int | None = None


@dataclass
class _QueuedRunStart:
    """State consumed when a durable-queue entry is promoted to running."""

    replay_seed: dict | None
    trace_carrier: dict | None
    prior_queue_status: str


async def _prepare_run_context(
    run_id: str,
    workflow_id: str,
    graph_dict: dict,
    cache: dict[str, dict] | None,
    run_org_id: str | None,
) -> _PreparedRunContext:
    """Load secrets, resolve credentials, and gather modules for a run.

    Extracted from ``_execute_run_impl`` to keep the hot path readable.
    """
    # Live settings for output/artifact caps (best-effort).
    output_cap = settings.max_output_bytes
    max_artifact_bytes: int | None = None
    max_artifacts_per_run: int | None = None
    try:
        live = await get_live_settings()
        output_cap = live.max_output_bytes
        max_artifact_bytes = live.max_artifact_bytes
        max_artifacts_per_run = live.max_artifacts_per_run
    except Exception:  # noqa: BLE001
        pass

    # Single DB session for secrets, credentials, modules.
    env_id: str | None = None
    run_timeout: float | None = None
    execution_mode = "inherit"
    sandbox_resources: dict | None = None
    secret_values: list[str] = []
    workflow_modules: list[dict] = []

    async with SessionLocal() as session:
        # Secret redaction word-list: best-effort.
        try:
            if run_org_id:
                secret_values = await load_secret_values_for_org(run_org_id, session)
            else:
                secret_values = await load_secret_values(session)
        except Exception:  # noqa: BLE001
            secret_values = []

        # Credential resolution MUST fail loudly (H1).
        graph_dict = await resolve_credential_refs(session, graph_dict, workflow_id=workflow_id)
        if cache is not None:
            cache = await resolve_credential_refs(session, cache, workflow_id=workflow_id)
        await session.commit()

        # Code modules: tolerate legacy DB.
        try:
            workflow = await session.get(Workflow, workflow_id)
            env_id = workflow.environment_id if workflow else None
            run_timeout = workflow.run_timeout_seconds if workflow else None
            execution_mode = workflow.execution_mode if workflow else "inherit"
            sandbox_resources = workflow.sandbox_resources if workflow else None
            stmt = select(CodeModule).where(
                or_(
                    CodeModule.scope == "global",
                    CodeModule.workflow_id == workflow_id,
                    ((CodeModule.scope == "environment") & (CodeModule.environment_id == env_id))
                    if env_id
                    else CodeModule.id.is_(None),
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
        except Exception:  # noqa: BLE001
            workflow_modules = []
            env_id = None
            run_timeout = None
            execution_mode = "inherit"
            sandbox_resources = None

    return _PreparedRunContext(
        graph_dict=graph_dict,
        cache=cache,
        env_id=env_id,
        run_timeout=run_timeout,
        workflow_modules=workflow_modules,
        secret_values=secret_values,
        output_cap=output_cap,
        execution_mode=execution_mode,
        sandbox_resources=sandbox_resources,
        max_artifact_bytes=max_artifact_bytes,
        max_artifacts_per_run=max_artifacts_per_run,
    )


def _lock_for_update_if_supported(session: AsyncSession, stmt):
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        return stmt.with_for_update()
    return stmt


async def _mark_queued_run_started(
    session: AsyncSession,
    *,
    run_id: str,
) -> _QueuedRunStart | None:
    """Atomically promote a queued run and queue entry to running.

    Cancellation can race this transition. Refresh and lock the queue row before
    the run row, then write them in that order, so a stale queue read cannot
    overwrite a cancellation and Postgres does not see inverted row-update
    ordering under load.
    """
    entry_stmt = (
        select(RunQueueEntry)
        .where(RunQueueEntry.run_id == run_id)
        .execution_options(populate_existing=True, skip_org_filter=True)
    )
    entry = await session.scalar(_lock_for_update_if_supported(session, entry_stmt))
    if entry is None or entry.status not in ("queued", "leased"):
        return None

    start_state = _QueuedRunStart(
        replay_seed=dict(entry.replay_seed) if entry.replay_seed else None,
        trace_carrier=dict(entry.trace_context) if entry.trace_context else None,
        prior_queue_status=entry.status,
    )
    run_stmt = select(Run).where(Run.id == run_id).execution_options(populate_existing=True)
    run = await session.scalar(_lock_for_update_if_supported(session, run_stmt))
    if run is None or run.status != "queued":
        return None

    entry.status = "running"
    entry.replay_seed = None
    await session.flush()
    run.status = "running"
    run.finished_at = None
    await session.flush()
    return start_state


async def _execute_run_impl(
    run_id: str,
    workflow_id: str,
    graph_dict: dict,
    targets: list[str] | None,
    cache: dict[str, dict] | None = None,
    *,
    prefer_draft: bool = False,
    runner_pool_id: str | None = None,
    run_execution_mode: str | None = None,
    agent_action_resume: dict[str, AgentActionRequest] | None = None,
    trace_org: str | None = None,
    run_org_id: str | None = None,
) -> str:
    node_events: dict[str, dict] = {}
    # Distinct NodeRun records keyed by (node_id, iteration_path). Non-loop nodes
    # key on an empty path -> one record each; looped body nodes get one per
    # iteration. node_events stays keyed by node_id (last-wins) for back-compat
    # consumers (webhook response, error handlers, guardrails).
    node_run_records: dict[tuple[str, tuple], dict] = {}
    run_events: deque[dict[str, Any]] = deque()
    run_event_sequence = 0
    # Track node IDs that completed successfully — used by checkpoint saves.
    completed_node_ids: set[str] = set()
    # Accumulated serialised checkpoint — we only serialise NEW outputs on
    # each save (O(1) per node instead of O(n²)) but always persist the
    # FULL dict so crash recovery works from a single column read.
    _accumulated_checkpoint: dict[str, dict] = {}
    checkpoint_debouncer = _CheckpointDebouncer()
    last_checkpoint_node_id: str | None = None
    checkpoint_truncated_warned = False
    artifact_refs: list[dict] = []
    secret_values: list[str] = []
    # A3: sub-workflow context (draft preference, depth/chain seed) travels
    # as explicit meta through the engine / run protocol — no ContextVars.
    sub_meta = meta_for_root_run(
        run_id=run_id,
        workflow_id=workflow_id,
        prefer_draft=prefer_draft,
        org_id=run_org_id,
    )
    run_id_token = _log_run_id.set(run_id)
    _install_run_id_filter()
    # A5: node-type lookup for node.execute span attributes; only consulted
    # (and only built) when tracing is enabled.
    trace_node_types: dict[str, str] = (
        {
            str(n.get("id")): str(n.get("type") or "")
            for n in graph_dict.get("nodes", [])
            if isinstance(n, dict)
        }
        if tracing.enabled()
        else {}
    )

    async def on_event(event: dict) -> None:
        nonlocal checkpoint_truncated_warned, last_checkpoint_node_id, run_event_sequence
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
            tracing.record_node_span(clean, node_types=trace_node_types, org_id=trace_org)
            node_events[clean["node_id"]] = clean
            path = clean.get("iteration_path")
            run_key = (clean["node_id"], tuple(path) if isinstance(path, list) else ())
            node_run_records[run_key] = clean
            debug = clean.get("debug")
            guardrail_events = debug.get("guardrail_events") if isinstance(debug, dict) else None
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
        # Track successful node completions for durable execution checkpoints.
        if clean.get("type") == "node_finished" and clean.get("status") == "success":
            completed_node_ids.add(clean["node_id"])
            last_checkpoint_node_id = clean["node_id"]
            try:
                if checkpoint_debouncer.should_persist():
                    was_truncated = await _save_checkpoint(
                        run_id,
                        {nid: ev.get("outputs", {}) for nid, ev in node_events.items()},
                        completed_node_ids,
                        last_node_id=clean["node_id"],
                        _accumulated=_accumulated_checkpoint,
                    )
                    checkpoint_debouncer.mark_persisted()
                    if was_truncated and not checkpoint_truncated_warned:
                        checkpoint_truncated_warned = True
                        broker.publish(
                            run_id,
                            {
                                "type": "checkpoint_truncated",
                                "run_id": run_id,
                                "detail": (
                                    "run state exceeds the 1 MiB checkpoint cap; "
                                    "crash-resume may recompute some nodes"
                                ),
                            },
                        )
            except Exception:  # noqa: BLE001
                checkpoint_debouncer.has_deferred = True
                logger.exception("run_id=%s checkpoint save failed", run_id)
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

    from app.services.metrics import active_runs, run_duration_seconds

    active_runs.inc()
    run_start = time.monotonic()

    broker.publish(run_id, {"type": "run_started", "run_id": run_id})
    status = "success"
    # Boot defaults are kept for the output cap as a safety fallback; the
    # live-settings overlay is loaded inside the try-block below (via
    # _prepare_run_context) so a cancel arriving during the DB read still
    # routes through the outer except and the run reaches a terminal status.
    output_cap = settings.max_output_bytes
    prep: _PreparedRunContext | None = None

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

        # Load secrets, resolve credentials, and gather code modules in one
        # DB session via the extracted helper (keeps _execute_run_impl readable).
        prep = await _prepare_run_context(
            run_id,
            workflow_id,
            graph_dict,
            cache,
            run_org_id,
        )
        graph_dict = prep.graph_dict
        cache = prep.cache
        env_id = prep.env_id
        run_timeout = prep.run_timeout
        workflow_modules = prep.workflow_modules
        secret_values = prep.secret_values
        output_cap = prep.output_cap
        effective_execution_mode = resolve_execution_mode(
            run_override=run_execution_mode,
            workflow_mode=prep.execution_mode,
        )

        if settings.use_subprocess_runner:
            if runner_pool_id:
                # Fail-closed twin of the admission gate: replayed/requeued
                # runs never re-pass admission, so a sandboxed run must be
                # re-verified against its pool's provider before remote
                # dispatch (an agent pool would execute it unsandboxed).
                remote_sandbox_required = False
                if effective_execution_mode == "sandboxed":
                    from app.models import RunnerPool as _RunnerPool

                    async with SessionLocal() as _pool_session:
                        _pool = await _pool_session.get(_RunnerPool, runner_pool_id)
                    if not _sandboxed_pool_ok(_pool):
                        raise SandboxRequired(
                            "run requires sandboxed execution but its runner "
                            f"pool (provider={getattr(_pool, 'provider', None)!r}) "
                            "cannot sandbox it"
                        )
                    # Only an agent pool needs the dispatch flag: docker/k8s
                    # pools spawn the hardened container themselves, whereas an
                    # agent runner must be told to run this in its sandbox path.
                    remote_sandbox_required = _agent_pool_sandbox_ok(_pool)
                # Remote runner path — build env descriptor and dispatch.
                env_payload = await _build_env_payload_for_run(env_id)
                try:
                    outcome = await remote_executor.execute(
                        _build_ctx(
                            run_id=run_id,
                            workflow_id=workflow_id,
                            graph=graph_dict,
                            cache=cache,
                            targets=targets,
                            environment_id=env_id,
                            runner_pool_id=runner_pool_id,
                            env_payload=env_payload,
                            workflow_modules=workflow_modules,
                            run_timeout=run_timeout,
                            agent_action_resume=agent_action_resume,
                            subworkflow_meta=sub_meta.to_payload(),
                            sandbox_required=remote_sandbox_required,
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
                    _log_run_id.reset(run_id_token)
                    return "queued"
            elif effective_execution_mode == "sandboxed":
                if not sandbox_executor.active:
                    raise SandboxRequired(
                        "run requires sandboxed execution but this worker has "
                        "no active sandbox (EXECUTION_SANDBOX=off or Docker "
                        "unreachable)"
                    )
                # Sandbox path: same prepared inputs as local, but the run
                # executes in a disposable hardened container keyed by
                # (org, env). env_payload drives the per-env image.
                env_payload = await _build_env_payload_for_run(env_id)
                sandbox_spawn_overrides = resolve_sandbox_overrides(prep.sandbox_resources)
                outcome = await sandbox_executor.execute(
                    _build_ctx(
                        run_id=run_id,
                        workflow_id=workflow_id,
                        org_id=trace_org,
                        graph=graph_dict,
                        cache=cache,
                        targets=targets,
                        environment_id=env_id,
                        runner_pool_id=None,
                        env_payload=env_payload,
                        workflow_modules=workflow_modules,
                        run_timeout=run_timeout,
                        agent_action_resume=agent_action_resume,
                        sandbox_spawn_overrides=sandbox_spawn_overrides,
                        subworkflow_meta=sub_meta.to_payload(),
                    ),
                    on_event,
                )
                status = outcome.status
            else:
                outcome = await local_executor.execute(
                    _build_ctx(
                        run_id=run_id,
                        workflow_id=workflow_id,
                        graph=graph_dict,
                        cache=cache,
                        targets=targets,
                        environment_id=env_id,
                        runner_pool_id=None,
                        env_payload=None,
                        workflow_modules=workflow_modules,
                        run_timeout=run_timeout,
                        agent_action_resume=agent_action_resume,
                        subworkflow_meta=sub_meta.to_payload(),
                    ),
                    on_event,
                )
                status = outcome.status
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
            pool_key_token = engine_pool_key.set(env_id)
            _run_org = await _resolve_run_org(run_id)
            limits_token = org_run_limits.set(await _org_run_limits_for(_run_org))
            artifact_token = artifact_store.set(
                make_artifact_store(
                    run_id,
                    org_id=_run_org,
                    max_bytes=prep.max_artifact_bytes if prep is not None else None,
                    max_count=prep.max_artifacts_per_run if prep is not None else None,
                )
            )
            try:
                graph = WorkflowGraph.model_validate(graph_dict)
                # Bound top-level in-process runs by the same global ceiling
                # subprocess ``dispatch`` uses, so an in-process deployment
                # can't spawn unbounded concurrent engine runs. Sub-workflows
                # reached via the engine's subworkflow resolver call
                # ``execute`` directly WITHOUT this slot, so a parent waiting
                # on a child never deadlocks (mirrors the subprocess split).
                _eff_timeout = (
                    run_timeout
                    if (run_timeout and run_timeout > 0)
                    else (settings.workflow_run_timeout_seconds or None)
                )

                # Install MCP tool callback so mcp_tool nodes can resolve
                # connections and execute calls through the platform hook.
                def _emit_mcp_event(payload: dict[str, Any]) -> None:
                    nonlocal run_event_sequence
                    clean_payload = redact_value(payload, secret_values)
                    broker.publish(run_id, clean_payload)
                    run_event_sequence += 1
                    if len(run_events) < _MAX_RUN_EVENTS:
                        run_events.append(
                            {
                                "sequence": run_event_sequence,
                                "ts": datetime.now(UTC),
                                "event": _cap_output(clean_payload, output_cap),
                            }
                        )

                async def _mcp_call_impl(
                    connection_id: str,
                    tool_name: str,
                    arguments: dict,
                ) -> Any:
                    from app.db import SessionLocal as _SessionLocal
                    from app.services.mcp_client import (
                        _load_conn_with_secret as _load_conn,
                    )
                    from app.services.mcp_client import (
                        call_tool as _call_tool,
                    )
                    from app.services.mcp_client import ensure_tool_allowed

                    async with _SessionLocal() as _session:
                        conn, secret = await _load_conn(connection_id, _run_org, _session)
                        ensure_tool_allowed(conn, tool_name)
                        started = time.monotonic()
                        _emit_mcp_event(
                            {
                                "type": "mcp_tool_started",
                                "run_id": run_id,
                                "connection_id": connection_id,
                                "connection_name": conn.name,
                                "tool_name": tool_name,
                            }
                        )
                        try:
                            result = await _call_tool(
                                conn,
                                tool_name,
                                arguments,
                                decrypted_secret=secret,
                                audit_session=_session,
                                run_id=run_id,
                            )
                        except Exception as exc:
                            _emit_mcp_event(
                                {
                                    "type": "mcp_tool_finished",
                                    "run_id": run_id,
                                    "connection_id": connection_id,
                                    "connection_name": conn.name,
                                    "tool_name": tool_name,
                                    "status": "error",
                                    "duration_ms": int((time.monotonic() - started) * 1000),
                                    "error": f"{type(exc).__name__}: {exc}",
                                }
                            )
                            raise
                        _emit_mcp_event(
                            {
                                "type": "mcp_tool_finished",
                                "run_id": run_id,
                                "connection_id": connection_id,
                                "connection_name": conn.name,
                                "tool_name": tool_name,
                                "status": "success",
                                "duration_ms": int((time.monotonic() - started) * 1000),
                            }
                        )
                        return result

                set_call_mcp_tool_impl(_mcp_call_impl)
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
                        subworkflow_runner=resolve_subworkflow,
                        subworkflow_meta=sub_meta,
                    )
                    result = await (
                        asyncio.wait_for(coro, timeout=_eff_timeout) if _eff_timeout else coro
                    )
                status = str(result.status)
            finally:
                # Reset the MCP callback so it doesn't leak across runs.
                from nodyra.engine.types import _call_mcp_tool_impl as _mcp_ctxvar

                _mcp_ctxvar.set(None)
                artifact_store.reset(artifact_token)
                org_run_limits.reset(limits_token)
                engine_pool_key.reset(pool_key_token)
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
        error_payload = redact_value(
            {
                "type": "run_error",
                "error": f"{type(exc).__name__}: {exc}",
            },
            secret_values,
        )
        broker.publish(run_id, error_payload)
        # Persist to run_events so callers (MCP get_run, UI) can surface the
        # error message — without this, pre-execution failures (e.g. credential
        # resolution) leave the run with status=error but zero diagnostic info.
        run_event_sequence += 1
        run_events.append(
            {
                "sequence": run_event_sequence,
                "ts": datetime.now(UTC),
                "event": _cap_output(error_payload, output_cap),
            }
        )

    if checkpoint_debouncer.has_deferred and last_checkpoint_node_id is not None:
        try:
            was_truncated = await _save_checkpoint(
                run_id,
                {nid: ev.get("outputs", {}) for nid, ev in node_events.items()},
                completed_node_ids,
                last_node_id=last_checkpoint_node_id,
                _accumulated=_accumulated_checkpoint,
            )
            checkpoint_debouncer.mark_persisted()
            if was_truncated and not checkpoint_truncated_warned:
                checkpoint_truncated_warned = True
                broker.publish(
                    run_id,
                    {
                        "type": "checkpoint_truncated",
                        "run_id": run_id,
                        "detail": (
                            "run state exceeds the 1 MiB checkpoint cap; "
                            "crash-resume may recompute some nodes"
                        ),
                    },
                )
        except Exception:  # noqa: BLE001
            logger.exception("run_id=%s final checkpoint flush failed", run_id)

    # Publish the terminal event BEFORE the DB session so that a DB failure
    # (e.g. a connection reset during the persist below) never leaves the
    # client's WebSocket waiting indefinitely for a run_finished that won't come.
    if status == "waiting":
        broker.publish(run_id, {"type": "run_waiting", "run_id": run_id, "status": status})
    else:
        broker.publish(run_id, {"type": "run_finished", "run_id": run_id, "status": status})

    # SessionLocal / start_run are resolved from this module's globals at call
    # time so the test-suite swaps (conftest, monkeypatch) keep applying.
    await run_persistence.persist_run_outcome(
        SessionLocal,
        run_id=run_id,
        status=status,
        graph_dict=graph_dict,
        node_events=node_events,
        node_run_records=node_run_records,
        run_events=run_events,
        output_cap=output_cap,
    )

    try:
        await persist_artifact_refs(run_id, artifact_refs)
    except Exception:  # noqa: BLE001 - artifact refs are best-effort
        logger.exception("run_id=%s failed to persist artifact refs", run_id)

    if status == "error":
        await run_alerts.dispatch_error_handlers(
            SessionLocal,
            start_run,
            run_id=run_id,
            node_events=node_events,
            secret_values=secret_values,
        )

    from app.services.metrics import active_runs

    active_runs.dec()
    run_duration_seconds.observe(time.monotonic() - run_start, status=status)

    _log_run_id.reset(run_id_token)
    return status


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
        run_execution_mode = run.execution_mode
        workflow_id = run.workflow_id
        mode = run.mode
        wf_version_id = run.workflow_version_id

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

        # Pick the graph the run was originally dispatched against. "test"
        # runs (test-URL webhook, draft chat) execute the editor's DRAFT even
        # though the run row anchors the latest published version id for
        # history sanity — using the version graph for those would replay
        # against the wrong graph (a chat run's seeded trigger id may not
        # even exist in it). "manual" runs ran the draft only when no version
        # is pinned: use_draft=false dispatches (editor, MCP run_workflow,
        # deployment "Run now") pin a version and must replay exactly that
        # graph, never the possibly half-edited draft.
        ran_draft = mode == "test" or (mode == "manual" and not wf_version_id)
        graph_dict: dict | None = None
        if ran_draft:
            graph_dict = workflow.draft_graph or None
        if not graph_dict and wf_version_id:
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

        queue_start = await _mark_queued_run_started(session, run_id=run_id)
        if queue_start is None:
            await session.rollback()
            return
        await session.commit()

    replay_seed = queue_start.replay_seed
    # A5: carrier stamped at enqueue — lets this (possibly different)
    # process join the originating request's trace.
    entry_trace_carrier = queue_start.trace_carrier
    trigger = first_trigger_node(graph_dict)
    trigger_id = (trigger.id if hasattr(trigger, "id") else trigger["id"]) if trigger else None
    targets = resolve_trigger_targets(graph_dict, trigger_id, None) if trigger_id else None
    cache: dict | None = pinned_cache or None

    # Durable execution: load checkpoint (fast path) or reconstruct from
    # NodeRun rows (fallback) so the run resumes from where it left off.
    # The dispatch loop leases the queue entry (status → "leased") BEFORE
    # calling us, and approval-resume keeps it "queued".  Accept both so
    # crash recovery AND approval-resume both benefit from the checkpoint.
    if queue_start.prior_queue_status in ("queued", "leased"):
        cp = run.checkpoint if isinstance(run.checkpoint, dict) else None
        if cp and isinstance(cp.get("node_outputs"), dict) and cp["node_outputs"]:
            node_outputs = cp["node_outputs"]
            merged_new: dict = dict(cache or {})
            for nid, outputs in node_outputs.items():
                if nid not in merged_new and isinstance(outputs, dict):
                    merged_new[nid] = outputs
            cache = merged_new
            logger.info(
                "run_id=%s resumed from checkpoint with %d completed node(s)",
                run_id,
                len(node_outputs),
            )
        else:
            try:
                from app.services.run_resume import build_durable_execution_state

                durable_cache = await build_durable_execution_state(SessionLocal, run_id=run_id)
                if durable_cache:
                    merged_new = dict(cache or {})
                    for nid, outputs in durable_cache.items():
                        if nid not in merged_new:
                            merged_new[nid] = outputs
                    cache = merged_new
                    logger.info(
                        "run_id=%s rebuilt durable state from %d NodeRun rows",
                        run_id,
                        len(durable_cache),
                    )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "run_id=%s durable state reconstruction failed, starting fresh",
                    run_id,
                )

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
            if isinstance(cache, dict):
                explicit_seed_cache_keys = (
                    {str(key) for key in seed_cache.keys()}
                    if isinstance(seed_cache, dict)
                    else set()
                )
                for target_node_id in targets:
                    target_key = str(target_node_id)
                    if target_key not in explicit_seed_cache_keys:
                        cache.pop(target_key, None)
        agent_action_resume = None
        if isinstance(seed_agent_resume, dict):
            agent_action_resume = {
                str(node_id): AgentActionRequest.model_validate(request)
                for node_id, request in seed_agent_resume.items()
                if isinstance(request, dict)
            }
    else:
        agent_action_resume = None

    # A5: run.lease marks the worker-side pickup; run.execute parents on it.
    # The span is brief by design — it records the handoff, not the execution.
    with tracing.span(
        "run.lease",
        carrier=entry_trace_carrier,
        attributes={"nodyra.run_id": run_id, "nodyra.workflow_id": workflow_id},
    ):
        lease_carrier = tracing.inject_context() or entry_trace_carrier

    await _execute_run(
        run_id,
        workflow_id,
        graph_dict,
        targets,
        cache,
        prefer_draft=ran_draft,
        runner_pool_id=runner_pool_id,
        run_execution_mode=run_execution_mode,
        agent_action_resume=agent_action_resume,
        trace_carrier=lease_carrier,
    )
