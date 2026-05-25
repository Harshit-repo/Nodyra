"""Noodle core: execution engine, node SDK, and shared models."""

from noodle.engine import GraphError, execute, run
from noodle.models import (
    Edge,
    GraphNode,
    NodeManifest,
    NodeRunResult,
    NodeStatus,
    ParamSpec,
    PortSpec,
    Position,
    RunResult,
    RunStatus,
    WorkflowGraph,
)
from noodle.sdk import NodeDef, NodeRegistry, node, registry
from noodle.serialization import deserialize_value, serialize_value

__version__ = "0.0.1"

__all__ = [
    "Edge",
    "GraphError",
    "GraphNode",
    "NodeDef",
    "NodeManifest",
    "NodeRegistry",
    "NodeRunResult",
    "NodeStatus",
    "ParamSpec",
    "PortSpec",
    "Position",
    "RunResult",
    "RunStatus",
    "WorkflowGraph",
    "execute",
    "node",
    "registry",
    "run",
    "deserialize_value",
    "serialize_value",
]
