"""Shared engine types with no internal dependencies."""

import contextvars
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]

# Default per-node output cap (10 MiB).  Applied inside the engine when the
# caller doesn't explicitly set ``max_node_output_bytes``.  Pass 0 to disable.
DEFAULT_MAX_NODE_OUTPUT_BYTES: int = 10 * 1024 * 1024


class GraphError(Exception):
    """Raised when a workflow graph is structurally invalid (e.g. has a cycle)."""


class NodeError(Exception):
    """Base exception for node-level execution errors."""


class NodeValidationError(NodeError):
    """Raised when a node's input or output fails schema validation.

    Attributes:
        node_id: The id of the node that failed validation.
        message: Human-readable description of the validation failure.
    """

    def __init__(self, message: str, node_id: str) -> None:
        self.node_id = node_id
        super().__init__(message)


@dataclass
class ValidationWarning:
    """A non-blocking warning emitted during graph validation.

    Warnings are logged by the runner but do not prevent execution — they
    inform the user about potential issues (e.g. schema mismatches) without
    raising an error.
    """

    node_id: str
    message: str
    severity: Literal["warning"] = "warning"


if TYPE_CHECKING:
    from nodyra.engine.subworkflows import SubworkflowMeta, SubworkflowRunner
    from nodyra.process_isolation import ProcessIsolator
    from nodyra.sdk import NodeRegistry


@dataclass
class RuntimeContext:
    """Immutable execution parameters bundled explicitly instead of scattered
    across ContextVars and TypedDicts.

    Set once before ``engine.execute()`` and never mutated during the run.
    ContextVars remain for per-node mutable state (``iteration_path``,
    ``current_node_id``, ``cancel_event``, ``node_emitter``, log capture).
    """

    run_id: str
    workflow_id: str
    org_id: str | None = None

    # --- node execution ---------------------------------------------------
    registry: "NodeRegistry | None" = None
    default_timeouts: dict[str, float] = field(default_factory=dict)
    max_node_output_bytes: int | None = None
    max_node_concurrency: int | None = None
    run_timeout_seconds: float | None = None
    pause_on_approval: bool = False

    # --- isolation --------------------------------------------------------
    process_isolator: "ProcessIsolator | None" = None

    # --- sub-workflows ----------------------------------------------------
    subworkflow_runner: "SubworkflowRunner | None" = None
    subworkflow_meta: "SubworkflowMeta | None" = None

    # --- agent resume state -----------------------------------------------
    agent_action_resume: dict[str, Any] | None = None

    # --- per-node state (set by engine before each node call) -------------
    node_params: dict[str, Any] = field(default_factory=dict)
    """The current node's resolved parameter values (from graph node params)."""

    node_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    """The current node's resolved input port values (upstream outputs)."""

    # ------------------------------------------------------------------
    # Platform hooks — overridden by the runner with real implementations.
    # ------------------------------------------------------------------

    async def call_mcp_tool(
        self, connection_id: str, tool_name: str, arguments: dict
    ) -> Any:
        """Platform hook: resolve MCP connection and execute tool call.

        The runner installs a real implementation via ``set_call_mcp_tool_impl``.
        By default this raises ``NotImplementedError``.
        """
        fn = _call_mcp_tool_impl.get()
        if fn is not None:
            return await fn(connection_id, tool_name, arguments)
        raise NotImplementedError(
            "call_mcp_tool is not implemented in this context"
        )


# Module-level hook: the API runner installs a real implementation so nodes
# in package ``nodyra_nodes`` (which cannot import ``app.services``) can
# dispatch MCP tool calls through the platform.  A ContextVar, rather than a
# module global, ensures each async task sees the correct hook when the engine
# runs a workflow — in particular after a subprocess-pool dispatch resets.
_call_mcp_tool_impl: contextvars.ContextVar[
    Callable[[str, str, dict], Awaitable[Any]] | None
] = contextvars.ContextVar("_call_mcp_tool_impl", default=None)


def set_call_mcp_tool_impl(
    fn: Callable[[str, str, dict], Awaitable[Any]],
) -> None:
    """Install the platform hook for ``RuntimeContext.call_mcp_tool``.

    Called by the API runner (``app.services.runner``) before each workflow
    execution so that ``mcp_tool`` nodes can resolve and call MCP connections.
    """
    _call_mcp_tool_impl.set(fn)
