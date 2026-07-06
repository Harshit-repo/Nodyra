"""RunExecutor seam (program review A2).

An executor takes a fully-prepared run (credentials already resolved into the
graph, env payload built, modules gathered) and produces a terminal status.
It owns NO persistence and NO DB sessions — ``runner._execute_run`` remains
the single place that loads context and persists outcomes, so the executor
can be swapped (local pool today, remote pool, future RPC worker) without
touching run bookkeeping.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict, runtime_checkable

EventCallback = Callable[[dict], Awaitable[None]]


class RunExecutionContext(TypedDict):
    run_id: str
    workflow_id: str
    org_id: str | None               # sandbox pool key; None single-tenant
    graph: dict                      # credential refs already resolved
    cache: dict | None
    targets: list[str] | None
    environment_id: str | None
    runner_pool_id: str | None
    env_payload: dict | None         # remote runs only; built by the host
    workflow_modules: list[dict]
    run_timeout: float | None
    sandbox_spawn_overrides: dict | None
    # True only for a sandboxed run dispatched to a sandbox-configured AGENT
    # pool: the agent runs it in a hardened disposable container. docker/
    # kubernetes pools ignore this (they spawn the container themselves).
    sandbox_required: bool
    default_timeouts: dict[str, float]
    pause_on_approval: bool
    agent_action_resume: dict[str, Any] | None  # node_id -> serialized request
    subworkflow_meta: dict | None    # SubworkflowMeta.to_payload() for the run protocol


@dataclass(frozen=True)
class RunOutcome:
    status: str  # success | error | waiting | cancelled


@runtime_checkable
class RunExecutor(Protocol):
    async def execute(
        self, ctx: RunExecutionContext, on_event: EventCallback
    ) -> RunOutcome: ...

    async def cancel(self, run_id: str) -> bool: ...
