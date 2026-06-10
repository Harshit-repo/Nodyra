"""Remote execution: agent / docker / kubernetes runner pools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.services.executors.base import (
    EventCallback,
    RunExecutionContext,
    RunOutcome,
)


class RemoteExecutor:
    """Adapter over ``remote_dispatch.dispatcher``.

    ``_QueuedError`` (no runner capacity) propagates to the caller —
    ``runner._execute_run`` owns the requeue-with-backoff transition exactly
    as it does today. ``runner_id_for`` resolves the agent a run was pinned
    to; injected so this module needs no DB session of its own (the runner
    module's patched ``SessionLocal`` does the lookup).
    """

    def __init__(self, *, dispatcher: Any,
                 runner_id_for: Callable[[str], Awaitable[str | None]]) -> None:
        self._dispatcher = dispatcher
        self._runner_id_for = runner_id_for

    async def execute(
        self, ctx: RunExecutionContext, on_event: EventCallback
    ) -> RunOutcome:
        status = await self._dispatcher.assign_run(
            ctx["run_id"],
            ctx["runner_pool_id"],
            ctx["env_payload"],
            ctx["graph"],
            ctx["cache"],
            ctx["targets"],
            ctx["workflow_modules"],
            on_event,
            pause_on_approval=ctx["pause_on_approval"],
            agent_action_resume=ctx["agent_action_resume"],
        )
        return RunOutcome(status=str(status))

    async def cancel(self, run_id: str) -> bool:
        runner_id = await self._runner_id_for(run_id)
        if not runner_id:
            return False
        await self._dispatcher.cancel_remote_run(run_id, runner_id)
        return True
