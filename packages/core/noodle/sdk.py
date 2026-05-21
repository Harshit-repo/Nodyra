"""Node SDK: the ``@node`` decorator, manifest generation, and the node registry.

A node is a plain Python function. Following n8n's model, a node has:

* **input ports** — wired data connections. Function parameters whose names are
  listed in ``@node(inputs=[...])`` are input ports (default ``["input"]``;
  triggers pass ``inputs=[]``).
* **config parameters** — every other function parameter. These are edited in
  the inspector, never wired.
* **outputs** — declared with ``@node(outputs=[...])`` (default ``["main"]``);
  a multi-output node returns a dict keyed by those names.

Per-parameter UI metadata (choices, multiline, placeholder, description) is
supplied via the decorator's ``params`` argument.
"""

import inspect
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_args, get_origin, get_type_hints

from noodle.models import NodeManifest, ParamSpec, PortSpec

_TYPE_MAP: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


@dataclass
class NodeDef:
    func: Callable[..., Any]
    manifest: NodeManifest
    is_async: bool


class NodeRegistry:
    """Holds node definitions keyed by manifest id."""

    def __init__(self) -> None:
        self._nodes: dict[str, NodeDef] = {}

    def register(self, node_def: NodeDef) -> None:
        node_id = node_def.manifest.id
        if node_id in self._nodes:
            raise ValueError(f"Duplicate node id: {node_id}")
        self._nodes[node_id] = node_def

    def get(self, node_id: str) -> NodeDef:
        if node_id not in self._nodes:
            raise KeyError(f"Unknown node type: {node_id}")
        return self._nodes[node_id]

    def manifests(self) -> list[NodeManifest]:
        return [d.manifest for d in self._nodes.values()]

    def __contains__(self, node_id: object) -> bool:
        return node_id in self._nodes


# The default registry that built-in nodes register into.
registry = NodeRegistry()


def _type_label(annotation: Any) -> str:
    if annotation is Any:
        return "any"
    origin = get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if non_none:
            return _type_label(non_none[0])
    if origin is list:
        return "array"
    if origin is dict:
        return "object"
    return _TYPE_MAP.get(annotation, "string")


def _build_manifest(
    func: Callable[..., Any],
    *,
    node_id: str,
    name: str,
    category: str,
    version: str,
    description: str,
    param_meta: dict[str, dict[str, Any]],
    inputs: list[str],
    outputs: list[str],
    icon: str | None,
) -> NodeManifest:
    hints = get_type_hints(func)
    signature = inspect.signature(func)
    input_names = set(inputs)
    params: list[ParamSpec] = []

    for pname, param in signature.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if pname in input_names:
            continue  # wired input port, not a config parameter

        meta = param_meta.get(pname, {})
        has_default = param.default is not inspect.Parameter.empty
        params.append(
            ParamSpec(
                name=pname,
                type=_type_label(hints.get(pname, str)),
                required=not has_default,
                default=param.default if has_default else None,
                description=meta.get("description", ""),
                placeholder=meta.get("placeholder", ""),
                choices=meta.get("choices"),
                multiline=bool(meta.get("multiline", False)),
                key_value=bool(meta.get("key_value", False)),
            )
        )

    return NodeManifest(
        id=node_id,
        name=name,
        category=category,
        version=version,
        description=description,
        icon=icon,
        inputs=[PortSpec(name=n) for n in inputs],
        params=params,
        outputs=[PortSpec(name=o) for o in outputs],
    )


def node(
    *,
    name: str,
    id: str | None = None,
    category: str = "General",
    version: str = "1.0.0",
    description: str = "",
    params: dict[str, dict[str, Any]] | None = None,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    icon: str | None = None,
    registry: NodeRegistry = registry,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a function as a Noodle node.

    ``inputs`` lists the wired input ports (default ``["input"]``; pass ``[]``
    for triggers). ``params`` maps a config parameter name to UI metadata, e.g.
    ``{"method": {"choices": ["GET", "POST"]}}``. ``outputs`` declares named
    output ports; a node with more than one output must return a dict keyed by
    those names (omit a key to leave that branch untaken).
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        node_id = id or func.__name__
        manifest = _build_manifest(
            func,
            node_id=node_id,
            name=name,
            category=category,
            version=version,
            description=description or (func.__doc__ or "").strip(),
            param_meta=params or {},
            inputs=["input"] if inputs is None else inputs,
            outputs=outputs or ["main"],
            icon=icon,
        )
        node_def = NodeDef(
            func=func,
            manifest=manifest,
            is_async=inspect.iscoroutinefunction(func),
        )
        registry.register(node_def)
        func.__noodle_node__ = node_def  # type: ignore[attr-defined]
        return func

    return decorator
