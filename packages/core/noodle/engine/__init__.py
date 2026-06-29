"""Workflow execution engine (package facade).

Public API: ``execute``, ``run``, ``GraphError``. The underscore names are
re-exported because tests and hosts imported them from ``noodle.engine``
before the monolith split; keep them working."""

from noodle.engine.agent import (
    _MAX_AGENT_LOOP_ITERATIONS,
    _dispatch_agent_action_request,
)
from noodle.engine.datasets import (
    AUTO_PROMOTE_NODE_TYPES,
    DATASET_AUTO_EXPAND_CAP,
    DATASET_PASSTHROUGH_NODE_TYPES,
    _auto_expand_dataset_inputs,
    _auto_promote_outputs,
)
from noodle.engine.loops import (
    LoopRegion,
    _loop_items,
    _loop_regions,
    _validate_loop_regions,
)
from noodle.engine.metanodes import _expand_metanodes
from noodle.engine.node_exec import (
    DEFAULT_NODE_TIMEOUTS,
    PROCESS_ISOLATED_NODE_TYPES,
    _approx_encoded_length,
    _install_capture,
    _sanitize_schema,
    _validate_node_input_schema,
    _validate_node_output_schema,
)
from noodle.engine.scheduler import (
    _build_plan,
    _execute_nodes,
    _needed_nodes,
    _topo_order,
    _worse_status,
    execute,
    run,
)
from noodle.engine.subworkflows import (
    InlineSubworkflow,
    SubworkflowCall,
    SubworkflowMeta,
    SubworkflowRunner,
    extract_leaf_value,
    make_workflow_caller,
)
from noodle.engine.types import EventCallback, GraphError, NodeError, NodeValidationError, ValidationWarning
from noodle.engine.validation import (
    AI_PORT_KINDS,
    _validate_connection_kinds,
    _validate_graph,
    _validate_input_kinds,
    _validate_input_schemas,
    _validate_output_kinds,
    validate_graph,
)

__all__ = [
    "AI_PORT_KINDS",
    "AUTO_PROMOTE_NODE_TYPES",
    "DATASET_AUTO_EXPAND_CAP",
    "DATASET_PASSTHROUGH_NODE_TYPES",
    "DEFAULT_NODE_TIMEOUTS",
    "EventCallback",
    "GraphError",
    "InlineSubworkflow",
    "NodeError",
    "NodeValidationError",
    "ValidationWarning",
    "LoopRegion",
    "PROCESS_ISOLATED_NODE_TYPES",
    "SubworkflowCall",
    "SubworkflowMeta",
    "SubworkflowRunner",
    "execute",
    "extract_leaf_value",
    "make_workflow_caller",
    "run",
    "validate_graph",
    # test-consumed internals (compat with pre-split import paths)
    "_MAX_AGENT_LOOP_ITERATIONS",
    "_approx_encoded_length",
    "_auto_expand_dataset_inputs",
    "_build_plan",
    "_auto_promote_outputs",
    "_dispatch_agent_action_request",
    "_execute_nodes",
    "_expand_metanodes",
    "_install_capture",
    "_loop_items",
    "_loop_regions",
    "_needed_nodes",
    "_topo_order",
    "_validate_connection_kinds",
    "_sanitize_schema",
    "_validate_graph",
    "_validate_input_kinds",
    "_validate_input_schemas",
    "_validate_loop_regions",
    "_validate_node_input_schema",
    "_validate_node_output_schema",
    "_validate_output_kinds",
    "_worse_status",
]
