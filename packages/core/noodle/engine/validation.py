"""Graph validation: structure checks, port-kind compatibility, and input/output kinds."""

from typing import Any

from noodle.engine.types import GraphError
from noodle.models import GraphNode, PortSpec, WorkflowGraph
from noodle.node_tool import TOOL_MODE_OUTPUT
from noodle.sdk import NodeRegistry

AI_PORT_KINDS: frozenset[str] = frozenset(
    {
        "ai_language_model",
        "ai_embedding_model",
        "ai_memory",
        "ai_tool",
        "ai_output_parser",
        "ai_retriever",
        "ai_vector_store",
        "ai_document_loader",
        "ai_guardrail",
        "ai_subagent",
    }
)


def _port_kind(port: PortSpec | None) -> str:
    return str(getattr(port, "data_kind", "any") or "any")


def _find_port(
    ports: list[PortSpec],
    name: str | None,
    default_name: str,
) -> PortSpec | None:
    wanted = name or default_name
    return next((port for port in ports if port.name == wanted), None) or (
        ports[0] if ports else None
    )


def _kind_label(kind: str) -> str:
    labels = {
        "any": "any data",
        "main": "main data",
        "control": "control",
        "dataset": "DatasetRef",
        "artifact": "ArtifactRef",
        "file": "FileRef",
        "ai_language_model": "AI language model",
        "ai_embedding_model": "AI embedding model",
        "ai_memory": "AI memory",
        "ai_tool": "AI tool",
        "ai_output_parser": "AI output parser",
        "ai_retriever": "AI retriever",
        "ai_vector_store": "AI vector store",
        "ai_document_loader": "AI document loader",
        "ai_guardrail": "AI guardrail",
        "ai_subagent": "AI Sub-Agent",
    }
    return labels.get(kind, kind)


def _connection_kind_error(source_kind: str, target_kind: str) -> str | None:
    if source_kind == target_kind:
        return None

    if source_kind in AI_PORT_KINDS or target_kind in AI_PORT_KINDS:
        return (
            f"{_kind_label(source_kind)} cannot connect to "
            f"{_kind_label(target_kind)}"
        )

    # DatasetRef ports are strict: they may only connect to a port that
    # explicitly accepts them ("dataset") or to the permissive "any" escape
    # hatch (where _auto_expand_dataset_inputs handles runtime expansion).
    if source_kind == "dataset":
        if target_kind in {"dataset", "any"}:
            return None
        return (
            f"{_kind_label(source_kind)} cannot connect to "
            f"{_kind_label(target_kind)}"
        )
    if target_kind == "dataset":
        if source_kind in {"dataset", "any"}:
            return None
        return (
            f"{_kind_label(source_kind)} cannot connect to "
            f"{_kind_label(target_kind)}"
        )

    # `main` is the explicit form of ordinary item/data flow; `any` remains the
    # permissive escape hatch for legacy and generic nodes.
    if source_kind in {"any", "main"} or target_kind in {"any", "main"}:
        return None

    return (
        f"{_kind_label(source_kind)} cannot connect to "
        f"{_kind_label(target_kind)}"
    )


def _validate_connection_kinds(
    graph: WorkflowGraph,
    registry: NodeRegistry,
    needed: set[str] | None = None,
) -> None:
    nodes_by_id = {node.id: node for node in graph.nodes}
    for edge in graph.edges:
        if needed is not None and (
            edge.source not in needed or edge.target not in needed
        ):
            continue
        source_node = nodes_by_id.get(edge.source)
        target_node = nodes_by_id.get(edge.target)
        if source_node is None or target_node is None:
            continue
        try:
            source_def = registry.get(source_node.type)
            target_def = registry.get(target_node.type)
        except KeyError:
            # Preserve existing unknown-node behavior: execution reports the
            # missing node as a node error instead of failing graph validation.
            continue

        source_port = _find_port(
            source_def.manifest.outputs,
            edge.source_output,
            "main",
        )
        target_port = _find_port(
            target_def.manifest.inputs,
            edge.target_input,
            "input",
        )
        source_kind = _port_kind(source_port)
        # A tool-mode node exposes a single `tool` output of kind ai_tool,
        # regardless of its normal (data-flow) manifest outputs.
        if (
            getattr(source_node, "tool_mode", False)
            and edge.source_output == TOOL_MODE_OUTPUT
        ):
            source_kind = "ai_tool"
        target_kind = _port_kind(target_port)
        error = _connection_kind_error(source_kind, target_kind)
        if error:
            raise GraphError(
                "Port kind mismatch on edge "
                f"{edge.source}.{edge.source_output} -> "
                f"{edge.target}.{edge.target_input}: {error}."
            )


def _validate_input_kinds(
    node_def: Any,
    kwargs: dict[str, Any],
    node_id: str,
) -> None:
    """Validate inputs declared as dataset/artifact actually receive that kind."""
    from noodle.artifacts import is_artifact_ref
    from noodle.datasets import is_dataset_ref

    for port in node_def.manifest.inputs:
        kind = getattr(port, "data_kind", "any")
        if kind in ("any", "control") or port.name not in kwargs:
            continue
        value = kwargs[port.name]
        if value is None:
            if kind in AI_PORT_KINDS:
                raise ValueError(
                    f"node '{node_id}' input '{port.name}' expected "
                    f"{_kind_label(kind)}, but the connected upstream node "
                    "produced no value. Enable the upstream node or disconnect "
                    "this AI port."
                )
            continue
        if kind == "dataset" and not is_dataset_ref(value):
            raise ValueError(
                f"node '{node_id}' input '{port.name}' expected a DatasetRef. "
                f"Add a Records To Dataset or CSV Parse node upstream."
            )
        if kind in ("artifact", "file") and not is_artifact_ref(value):
            raise ValueError(
                f"node '{node_id}' input '{port.name}' expected an ArtifactRef."
            )


def _validate_input_schemas(
    node_def: Any,
    kwargs: dict[str, Any],
    node_id: str,
) -> None:
    """Validate wired input values against their port's ``data_schema``.

    When a port declares a JSON Schema, every value arriving on it is
    validated before the node runs — catching type mismatches early with
    a clear message instead of a cryptic downstream crash.
    """
    from jsonschema import ValidationError, validate

    for port in node_def.manifest.inputs:
        schema = getattr(port, "data_schema", None) or None
        if schema is None or port.name not in kwargs:
            continue
        value = kwargs[port.name]
        if value is None:
            continue  # None = optional input; schema validation is skipped
        try:
            validate(instance=value, schema=schema)
        except ValidationError as exc:
            raise ValueError(
                f"node '{node_id}' input '{port.name}' "
                f"failed schema validation: {exc.message}"
            ) from exc


def _validate_output_kinds(
    node_def: Any,
    outputs: dict[str, Any],
    node_id: str,
) -> None:
    from noodle.artifacts import is_artifact_ref
    from noodle.datasets import is_dataset_ref

    for port in node_def.manifest.outputs:
        kind = getattr(port, "data_kind", "any")
        if kind in ("any", "control") or port.name not in outputs:
            continue
        value = outputs[port.name]
        if value is None:
            continue
        if kind == "dataset" and not is_dataset_ref(value):
            raise ValueError(
                f"node '{node_id}' output '{port.name}' was declared as a "
                f"dataset port but produced {type(value).__name__}."
            )
        if kind in ("artifact", "file") and not is_artifact_ref(value):
            raise ValueError(
                f"node '{node_id}' output '{port.name}' was declared as an "
                f"artifact port but produced {type(value).__name__}."
            )


def _validate_graph(
    graph: WorkflowGraph,
    registry: NodeRegistry | None = None,
) -> None:
    """Validate the graph structure before execution.

    Checks that are cheap and catchable early (before topological sort and
    node execution) live here.  Port-kind validation is separate because it
    depends on the ``needed`` set (targeted runs may skip some edges).

    Raises ``GraphError`` on structural problems.
    """
    nodes_by_id: dict[str, GraphNode] = {n.id: n for n in graph.nodes}
    edge_ids: set[str] = set()

    # --- empty graph -------------------------------------------------------
    if not graph.nodes:
        raise GraphError("Workflow graph has no nodes")

    # --- duplicate node ids ------------------------------------------------
    seen_ids: set[str] = set()
    for n in graph.nodes:
        if n.id in seen_ids:
            raise GraphError(f"Duplicate node id '{n.id}'")
        seen_ids.add(n.id)

    # --- edge validation ---------------------------------------------------
    for edge in graph.edges:
        # Self-loops are never valid.
        if edge.source == edge.target:
            raise GraphError(
                f"Self-loop edge from '{edge.source}' to '{edge.target}' "
                "is not allowed"
            )

        # Missing source node.
        if edge.source not in nodes_by_id:
            raise GraphError(
                f"Edge references unknown source node '{edge.source}'"
            )

        # Missing target node.
        if edge.target not in nodes_by_id:
            raise GraphError(
                f"Edge references unknown target node '{edge.target}'"
            )

        # Duplicate edge detection — same (source, source_output, target,
        # target_input) pair.
        edge_key = (
            f"{edge.source}:{edge.source_output}"
            f"->{edge.target}:{edge.target_input}"
        )
        if edge_key in edge_ids:
            raise GraphError(
                f"Duplicate edge from '{edge.source}.{edge.source_output}' "
                f"to '{edge.target}.{edge.target_input}'"
            )
        edge_ids.add(edge_key)

    # --- unknown node types (when registry is supplied) --------------------
    # Engine-internal types (meta_node for transparent/isolated grouping,
    # loop_start/loop_end for iteration, __metanode_input__ for metanode
    # boundary ports) are never registered in the user-visible registry —
    # they are handled by the engine itself. Skip them here.
    _ENGINE_INTERNAL_TYPES: frozenset[str] = frozenset(
        {"meta_node", "loop_start", "loop_end", "__metanode_input__"}
    )
    if registry is not None:
        for n in graph.nodes:
            if n.type in _ENGINE_INTERNAL_TYPES:
                continue
            try:
                registry.get(n.type)
            except KeyError:
                raise GraphError(
                    f"Unknown node type '{n.type}' for node '{n.id}'"
                )
