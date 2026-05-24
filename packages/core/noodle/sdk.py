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


def register_module_functions(
    module_id: str,
    source: str,
    registry: NodeRegistry,
    *,
    category: str = "Custom",
) -> tuple[list[str], list[tuple[str, str]]]:
    """Exec ``source`` and register each top-level function as a node.

    Reuses :func:`_build_manifest` so user-uploaded functions get the exact
    same manifest treatment as ``@node``-decorated built-ins (type hints →
    input ports / param specs, return → output, async detection). Each
    function ``foo`` is registered under id ``user:<module_id>:<foo>``.

    ``module_id`` should be the DB row id of the uploaded file so the same
    node id is stable across re-registers, and graphs that reference a
    deleted function fall through the engine's existing "unknown node type"
    path.

    Returns ``(registered_names, skipped)`` where ``skipped`` is a list of
    ``(name, reason)`` tuples so the upload preview can tell the user why
    something was ignored (e.g. ``*args``, classes, lambdas).
    """
    module_globals: dict[str, Any] = {
        "__name__": f"user_module_{module_id}",
        "__builtins__": __builtins__,
    }
    pre_keys = set(module_globals.keys())
    exec(source, module_globals)  # noqa: S102 - running user Python is the point
    new_keys = [k for k in module_globals if k not in pre_keys]

    registered: list[str] = []
    skipped: list[tuple[str, str]] = []

    for key in new_keys:
        value = module_globals[key]
        if not inspect.isfunction(value):
            continue
        if value.__module__ != module_globals["__name__"]:
            continue  # imported from elsewhere, not a user function
        try:
            params = inspect.signature(value).parameters
        except (TypeError, ValueError):
            skipped.append((key, "could not inspect signature"))
            continue
        if any(
            p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            for p in params.values()
        ):
            skipped.append((key, "*args / **kwargs are not supported"))
            continue

        # Pick the first parameter as the single wired input port (mirrors how
        # users intuitively wire f(x) → drag a value into x). The remaining
        # parameters become config params edited in the inspector.
        param_names = list(params.keys())
        inputs = [param_names[0]] if param_names else []

        try:
            manifest = _build_manifest(
                value,
                node_id=f"user:{module_id}:{key}",
                name=key,
                category=category,
                version="1.0.0",
                description=(value.__doc__ or "").strip(),
                param_meta={},
                inputs=inputs,
                outputs=["main"],
                icon=None,
            )
        except Exception as exc:  # noqa: BLE001 - surface back to the UI
            skipped.append((key, f"manifest error: {exc}"))
            continue

        node_def = NodeDef(
            func=value,
            manifest=manifest,
            is_async=inspect.iscoroutinefunction(value),
        )
        # Re-register on top: drop any prior entry under the same id so an
        # edited file replaces its previous registration cleanly.
        registry._nodes[manifest.id] = node_def  # noqa: SLF001
        registered.append(key)

    return registered, skipped


def unregister_module(module_id: str, registry: NodeRegistry) -> None:
    """Remove every node id registered for ``module_id`` from the registry."""
    prefix = f"user:{module_id}:"
    for key in [k for k in registry._nodes if k.startswith(prefix)]:  # noqa: SLF001
        del registry._nodes[key]  # noqa: SLF001


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
