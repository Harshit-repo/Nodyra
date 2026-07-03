"""Runtime context for nodes that need to call back into the host.

The runner sets these before invoking the engine. Nodes can read them via
``contextvars.get`` but should never set them themselves.

* :data:`workflow_caller` — when set, an async callable that runs another
  workflow by id and returns its node outputs. The Execute Workflow node uses
  this to invoke sub-workflows without depending on the API or its DB layer.
* :data:`call_chain` — the chain of workflow ids currently executing, used to
  short-circuit cycles. The runner seeds this with the root workflow id; each
  sub-workflow caller pushes a new id and checks for re-entry.
* :data:`node_debug` — a per-node scratch dict. Nodes can attach optional
  debugger metadata here; the engine emits it with the node result.
* :data:`current_node_id` and :data:`artifact_store` — used by
  ``nodyra.artifacts`` so user Python can create files tied to the active run
  without coupling the core engine to the API database.
"""

import threading
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any, Protocol

WorkflowCaller = Callable[[str, Any], Awaitable[Any]]

workflow_caller: ContextVar[WorkflowCaller | None] = ContextVar(
    "nodyra_workflow_caller", default=None
)

call_chain: ContextVar[frozenset[str]] = ContextVar(
    "nodyra_call_chain", default=frozenset()
)

node_debug: ContextVar[dict[str, Any] | None] = ContextVar(
    "nodyra_node_debug", default=None
)

current_node_id: ContextVar[str | None] = ContextVar(
    "nodyra_current_node_id", default=None
)

artifact_store: ContextVar[Any | None] = ContextVar(
    "nodyra_artifact_store", default=None
)

# Loop iteration coordinates for the currently executing node, outermost first.
# Empty tuple = not inside any loop. Set by the engine's loop driver so emitted
# events and persisted node runs can be attributed to a specific iteration.
iteration_path: ContextVar[tuple[int, ...]] = ContextVar(
    "nodyra_iteration_path", default=()
)

# Live output streaming hook for the currently executing node. The engine sets
# this to a callback that forwards incremental chunks as ``node_chunk`` run
# events (thread-safe — a node running in a worker thread can call it). Unset
# when no run is listening, so :func:`emit_chunk` is a no-op outside a live run.
NodeEmitter = Callable[..., None]

node_emitter: ContextVar[NodeEmitter | None] = ContextVar(
    "nodyra_node_emitter", default=None
)


def emit_chunk(delta: str, *, channel: str = "output") -> None:
    """Stream an incremental output chunk from inside a running node.

    When the host is rendering this run live (e.g. the editor canvas), the chunk
    is forwarded as a ``node_chunk`` event so partial output — LLM tokens, long
    log lines — appears as it is produced instead of only when the node finishes.
    It is a no-op when nothing is listening (a headless run, an exported script,
    or a process-isolated node), so node code can call it unconditionally.

    ``channel`` lets a node separate distinct streams (default ``"output"``);
    consumers accumulate chunks per ``(node, channel)``.
    """
    cb = node_emitter.get()
    if cb is None:
        return
    text = str(delta)
    if not text:
        return
    cb(text, channel=channel)


# Cancellation signal for synchronous nodes running in worker threads (E-03).
# The engine sets this to a threading.Event before invoking a sync node via
# asyncio.to_thread. The event is set when the run is cancelled so long-running
# sync nodes that check it can exit early. Nodes access it via:
#   from nodyra.context import cancel_event; event = cancel_event.get()
_DEFAULT_CANCEL_EVENT = threading.Event()
cancel_event: ContextVar[threading.Event] = ContextVar(
    "nodyra_cancel_event", default=_DEFAULT_CANCEL_EVENT
)

# Monotonic timestamp after which no node may keep running. Set by the engine
# for the duration of a run with run_timeout_seconds.
run_deadline: ContextVar[float | None] = ContextVar(
    "nodyra_run_deadline", default=None
)

# Per-organization amplification caps for the current run (multi-tenancy C5).
# Keys: "max_map_width" (rows a map node may fan out into child workflows)
# and "max_loop_iterations" (units a single loop may drive). 0/absent =
# uncapped. Set by the host (runner / runtime server) before invoking the
# engine; empty for single-tenant deployments.
org_run_limits: ContextVar[dict[str, int] | None] = ContextVar(
    "nodyra_org_run_limits", default=None
)


class WebSocketConnection(Protocol):
    """Minimal interface a node uses to read/write a managed WebSocket."""

    async def send(self, data: bytes | str) -> None: ...
    async def recv(self) -> bytes | str: ...
    async def close(self) -> None: ...


# Injected by the runtime when a node needs a managed WebSocket connection.
# The runtime opens and tracks connections so they are cleaned up when the
# node run finishes or times out. Default None = not inside a live run.
NodeWsConnectFn = Callable[[str, dict], Awaitable["WebSocketConnection"]]

node_ws_connect: ContextVar[NodeWsConnectFn | None] = ContextVar(
    "nodyra_node_ws_connect", default=None
)
