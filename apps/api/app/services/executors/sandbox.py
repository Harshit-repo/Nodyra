"""Sandbox execution: disposable hardened container via the SandboxPool."""

from __future__ import annotations

from typing import Any

from app.services.executors.base import (
    EventCallback,
    RunExecutionContext,
    RunOutcome,
)


class SandboxExecutor:
    """Adapter over ``sandbox_pool.pool`` — the per-(org, env) warm
    container pool. No DB access, mirroring LocalExecutor."""

    def __init__(self, *, pool: Any, subworkflow_resolver: Any) -> None:
        self._pool = pool
        self._subworkflow_resolver = subworkflow_resolver

    @property
    def active(self) -> bool:
        return bool(self._pool.enabled)

    async def execute(
        self, ctx: RunExecutionContext, on_event: EventCallback
    ) -> RunOutcome:
        status = await self._pool.dispatch(
            ctx["run_id"],
            org_id=ctx.get("org_id"),
            env_id=ctx["environment_id"],
            env_payload=ctx["env_payload"] or {},
            graph=ctx["graph"],
            cache=ctx["cache"],
            targets=ctx["targets"],
            workflow_modules=ctx["workflow_modules"],
            on_event=on_event,
            subworkflow_resolver=self._subworkflow_resolver,
            subworkflow_meta=ctx.get("subworkflow_meta"),
            run_timeout=ctx["run_timeout"],
            pause_on_approval=ctx["pause_on_approval"],
            agent_action_resume=ctx["agent_action_resume"],
        )
        return RunOutcome(status=str(status))

    async def cancel(self, run_id: str) -> bool:
        return await self._pool.cancel(run_id)
