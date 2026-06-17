"""Noodle core: execution engine, node SDK, and shared models."""

from noodle.context import emit_chunk
from noodle.engine import GraphError, execute, run
from noodle.models import (
    BinaryRef,
    Edge,
    GraphNode,
    ItemMeta,
    NodeManifest,
    NodeRunResult,
    NodeStatus,
    NoodleItem,
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
    "BinaryRef",
    "Edge",
    "GraphError",
    "GraphNode",
    "ItemMeta",
    "NodeDef",
    "NodeManifest",
    "NodeRegistry",
    "NodeRunResult",
    "NodeStatus",
    "NoodleItem",
    "ParamSpec",
    "PortSpec",
    "Position",
    "RunResult",
    "RunStatus",
    "WorkflowGraph",
    "emit_chunk",
    "execute",
    "node",
    "registry",
    "run",
    "deserialize_value",
    "serialize_value",
]
