"""Shared engine types with no internal dependencies."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]

# Default per-node output cap (10 MiB).  Applied inside the engine when the
# caller doesn't explicitly set ``max_node_output_bytes``.  Pass 0 to disable.
DEFAULT_MAX_NODE_OUTPUT_BYTES: int = 10 * 1024 * 1024


class GraphError(Exception):
    """Raised when a workflow graph is structurally invalid (e.g. has a cycle)."""


if TYPE_CHECKING:
    from noodle.engine.subworkflows import SubworkflowMeta, SubworkflowRunner
    from noodle.process_isolation import ProcessIsolator
    from noodle.sdk import NodeRegistry


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
