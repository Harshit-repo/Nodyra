"""Local execution: warm per-env subprocess via the host's RuntimePool."""

from __future__ import annotations

import asyncio
from typing import Any

from app.services.executors.base import (
    EventCallback,
    RunExecutionContext,
    RunOutcome,
)


class LocalExecutor:
    """Adapter over ``runtime_pool.pool`` (the warm subprocess pool).

    No DB access. ``active_runs`` is runner.py's run-id → asyncio.Task
    registry; cancel() works by cancelling the awaiting task, exactly like
    ``cancel_run`` does today.
    """

    def __init__(self, *, pool: Any, sub_workflow_caller: Any,
                 active_runs: dict[str, asyncio.Task]) -> None:
        self._pool = pool
        self._sub_workflow_caller = sub_workflow_caller
        self._active_runs = active_runs

    async def execute(
        self, ctx: RunExecutionContext, on_event: EventCallback
    ) -> RunOutcome:
        status = await self._pool.dispatch(
            ctx["run_id"],
            ctx["environment_id"],
            ctx["graph"],
            ctx["cache"],
            ctx["targets"],
            on_event,
            sub_workflow_caller=self._sub_workflow_caller,
            workflow_modules=ctx["workflow_modules"],
            run_timeout=ctx["run_timeout"],
            pause_on_approval=ctx["pause_on_approval"],
            agent_action_resume=ctx["agent_action_resume"],
        )
        return RunOutcome(status=str(status))

    async def cancel(self, run_id: str) -> bool:
        task = self._active_runs.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            return True
        return False
