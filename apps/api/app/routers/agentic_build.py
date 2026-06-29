"""SSE router for the agentic build loop (MS4 Slice 4D).

``POST /workflows/{workflow_id}/agentic-build`` opens a Server-Sent Events
stream that drives the autonomous build->execute->diagnose->fix loop.

Rate-limiting
-------------
Per-org iteration count: max 20 agentic build *iterations* per org per hour.
Each ``POST`` starts a new loop; one loop can use up to ``max_iterations``
iterations, so at most 4 concurrent loops per org per hour (with default
max_iterations=5).  The rate key is ``agentic_build_iterations:<org_id>`` and
consumes ``max_iterations`` tokens on start.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.db import get_session
from app.models import Workflow
from app.schemas import AgenticBuildRequest
from app.security import optional_current_user, require_permission
from app.services import rate_limit
from app.services.agentic_builder import run_agentic_build_loop
from app.tenancy import current_org_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workflows", tags=["agentic-build"])

# --- Constants ----------------------------------------------------------------
_AGENTIC_BUILD_RATE_LIMIT = 20  # max iterations per org per hour
_AGENTIC_BUILD_RATE_WINDOW = 3600  # seconds


@router.post(
    "/{workflow_id}/agentic-build",
    dependencies=[Depends(require_permission("workflow:run"))],
)
async def agentic_build(
    workflow_id: str,
    req: AgenticBuildRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Start an agentic build loop and stream progress as SSE events.

    The client opens a long-lived SSE connection.  The server runs the
    build->execute->diagnose->fix loop as a background task and pushes each
    event to the stream.  When the client disconnects the loop is cancelled
    cleanly.
    """
    # --- Validate workflow exists + org scope -----------------------------------
    wf = await session.scalar(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.org_id == org_id)
    )
    if wf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")

    org_id = current_org_id.get() or "default"

    # --- Rate limit -------------------------------------------------------------
    allowed = await rate_limit.allow(
        "agentic_build_iterations",
        org_id,
        limit=_AGENTIC_BUILD_RATE_LIMIT,
        window_seconds=_AGENTIC_BUILD_RATE_WINDOW,
    )
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Org agentic-build iteration limit exceeded (max 20 iterations/hour). "
            "Wait or reduce max_iterations.",
        )

    cancel_event: asyncio.Event = asyncio.Event()
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def event_callback(event: dict) -> None:
        await queue.put(event)

    async def run_loop_and_signal_done() -> None:
        """Background task: run the loop then push a ``None`` sentinel."""
        try:
            await run_agentic_build_loop(
                workflow_id=workflow_id,
                goal=req.goal,
                test_data=req.test_data,
                max_iterations=req.max_iterations,
                org_id=org_id,
                event_callback=event_callback,
                cancel_event=cancel_event,
            )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("agentic build loop crashed unexpectedly")
            await event_callback({
                "type": "error",
                "message": "Internal error during agentic build loop.",
            })
        finally:
            await queue.put(None)  # sentinel -- signals stream() to stop

    async def stream() -> Any:
        """SSE generator: reads events from the queue and yields them."""
        task = asyncio.create_task(run_loop_and_signal_done())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # prevent proxy/browser timeout
                    continue
                if event is None:
                    break
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            # Client disconnected -- signal the loop to stop.
            cancel_event.set()
            task.cancel()
            raise

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )
