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
"""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

WorkflowCaller = Callable[[str, Any], Awaitable[Any]]

workflow_caller: ContextVar[WorkflowCaller | None] = ContextVar(
    "noodle_workflow_caller", default=None
)

call_chain: ContextVar[frozenset[str]] = ContextVar(
    "noodle_call_chain", default=frozenset()
)

node_debug: ContextVar[dict[str, Any] | None] = ContextVar(
    "noodle_node_debug", default=None
)
