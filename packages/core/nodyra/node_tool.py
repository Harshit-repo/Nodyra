"""Run a normal node as an AI Agent tool ("tool mode").

A graph node flagged ``tool_mode`` does not execute in the data flow. The engine
turns it into a :class:`NodeToolAdapter` whose schema is derived from the node's
``$fromAI(...)`` parameter bindings; the node's own function runs only when the
agent calls the tool.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from nodyra.ai_runtime import ToolAdapter, ToolParameterSchema, ToolSchema
from nodyra.expr import build_context, evaluate, from_ai_binding

TOOL_MODE_OUTPUT = "tool"


def _is_blank(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip() == ""
    return value is None


def _schema_type(spec_type: str | None) -> str:
    if spec_type in {"number", "integer", "float"}:
        return "number"
    if spec_type == "boolean":
        return "boolean"
    return "string"


def _infer_blank_core_args(node_def: Any, graph_node: Any) -> dict[str, dict[str, str]]:
    inferred: dict[str, dict[str, str]] = {}
    params = dict(getattr(graph_node, "params", {}) or {})
    for spec in getattr(node_def.manifest, "params", []):
        if (
            getattr(spec, "credential", None)
            or getattr(spec, "group", None)
            or getattr(spec, "choices", None)
        ):
            continue
        spec_type = str(getattr(spec, "type", "string") or "string")
        if spec_type in {"object", "array"}:
            continue
        value = params.get(spec.name, getattr(spec, "default", None))
        if not _is_blank(value):
            continue
        inferred[spec.name] = {
            "description": (
                getattr(spec, "description", "")
                or getattr(spec, "placeholder", "")
                or spec.name
            ),
            "type": _schema_type(spec_type),
        }
    return inferred


def _build_schema(node_def: Any, graph_node: Any) -> tuple[ToolSchema, set[str]]:
    collector: dict[str, dict[str, str]] = {}
    ctx = build_context()
    ctx["_from_ai"] = from_ai_binding(collector=collector)
    evaluate(dict(graph_node.params), ctx)  # populates collector via $fromAI calls
    inferred_names: set[str] = set()
    if not collector:
        collector.update(_infer_blank_core_args(node_def, graph_node))
        inferred_names = set(collector)
    properties: dict[str, Any] = {}
    for arg_name, spec in collector.items():
        prop: dict[str, Any] = {"type": spec["type"] or "string"}
        if spec["description"]:
            prop["description"] = spec["description"]
        properties[arg_name] = prop
    manifest = node_def.manifest
    name = (graph_node.tool_name or manifest.id).strip()
    description = (
        graph_node.tool_description or manifest.description or manifest.name
    ).strip()
    return (
        ToolSchema(
            name=name,
            description=description,
            parameters=ToolParameterSchema(
                properties=properties, required=list(collector.keys())
            ),
        ),
        inferred_names,
    )


def _as_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)


class NodeToolAdapter(ToolAdapter):
    """Adapter that runs a tool-mode node's function on demand."""

    def __init__(
        self,
        *,
        node_def: Any,
        params: dict[str, Any],
        schema: ToolSchema,
        side_effecting: bool,
        inferred_arg_names: set[str] | None = None,
    ) -> None:
        self._node_def = node_def
        self._params = dict(params)
        self._schema = schema
        self._side_effecting = side_effecting
        self._inferred_arg_names = set(inferred_arg_names or ())

    @property
    def schema(self) -> ToolSchema:
        return self._schema

    @property
    def side_effecting(self) -> bool:
        return self._side_effecting

    def _resolved_kwargs(self, arguments: dict[str, Any]) -> dict[str, Any]:
        ctx = build_context()
        ctx["_from_ai"] = from_ai_binding(ai_args=arguments)
        resolved = evaluate(self._params, ctx)
        for name in self._inferred_arg_names:
            if name in arguments and _is_blank(resolved.get(name)):
                resolved[name] = arguments[name]
        nd = self._node_def
        if nd.accepts_var_keyword or not nd.param_names:
            return dict(resolved)
        return {k: v for k, v in resolved.items() if k in nd.param_names}

    def invoke(self, arguments: dict[str, Any]) -> str:
        if self._node_def.is_async:
            raise RuntimeError(
                f"{self._schema.name}: async tool node requires invoke_async"
            )
        return _as_text(self._node_def.func(**self._resolved_kwargs(arguments)))

    async def invoke_async(self, arguments: dict[str, Any]) -> str:
        kwargs = self._resolved_kwargs(arguments)
        nd = self._node_def
        if nd.is_async:
            return _as_text(await nd.func(**kwargs))
        return _as_text(await asyncio.to_thread(nd.func, **kwargs))


def build_node_tool_adapter(node_def: Any, graph_node: Any) -> NodeToolAdapter:
    """Construct the adapter for a ``tool_mode`` graph node."""
    schema, inferred_arg_names = _build_schema(node_def, graph_node)
    side_effecting = bool(getattr(node_def.manifest, "tool_side_effecting", True))
    return NodeToolAdapter(
        node_def=node_def,
        params=graph_node.params,
        schema=schema,
        side_effecting=side_effecting,
        inferred_arg_names=inferred_arg_names,
    )
