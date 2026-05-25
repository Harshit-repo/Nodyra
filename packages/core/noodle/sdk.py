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

import ast
import inspect
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
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

_AST_TYPE_MAP: dict[str, str] = {
    "Any": "any",
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "List": "array",
    "dict": "object",
    "Dict": "object",
}

_MISSING = object()


@dataclass
class NodeDef:
    func: Callable[..., Any]
    manifest: NodeManifest
    is_async: bool
    # Names of parameters the function actually accepts, used by the engine
    # to filter kwargs. Lets user-function nodes expose a virtual "input"
    # port for upstream data without requiring the function to take an
    # ``input`` argument.
    param_names: frozenset[str] = field(default_factory=frozenset)
    # True when the function declares **kwargs — the engine then passes
    # whatever kwargs it has without filtering.
    accepts_var_keyword: bool = False


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


def _signature_info(func: Callable[..., Any]) -> tuple[frozenset[str], bool]:
    """Return (param_names, accepts_var_keyword) for the engine's kwargs filter."""
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return frozenset(), False
    names: set[str] = set()
    has_var_kw = False
    for p in sig.parameters.values():
        if p.kind is inspect.Parameter.VAR_KEYWORD:
            has_var_kw = True
            continue
        if p.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        names.add(p.name)
    return frozenset(names), has_var_kw


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


def _annotation_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _type_label_from_ast(node: ast.AST | None) -> str:
    if node is None:
        return "string"
    name = _annotation_name(node)
    if name in _AST_TYPE_MAP:
        return _AST_TYPE_MAP[name]
    if isinstance(node, ast.Subscript):
        outer = _annotation_name(node.value)
        if outer in ("list", "List", "Sequence", "Iterable", "tuple", "Tuple", "set", "Set"):
            return "array"
        if outer in ("dict", "Dict", "Mapping"):
            return "object"
        if outer in ("Optional", "Union"):
            options = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            for option in options:
                if not (
                    isinstance(option, ast.Constant)
                    and option.value is None
                ) and _annotation_name(option) != "None":
                    return _type_label_from_ast(option)
            return "any"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left_is_none = isinstance(node.left, ast.Constant) and node.left.value is None
        right_is_none = isinstance(node.right, ast.Constant) and node.right.value is None
        if left_is_none:
            return _type_label_from_ast(node.right)
        if right_is_none:
            return _type_label_from_ast(node.left)
        return _type_label_from_ast(node.left)
    if isinstance(node, ast.Constant) and node.value is None:
        return "any"
    return "string"


def _literal_default(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (TypeError, ValueError):
        return _MISSING


def _function_param_specs(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[str, str, bool, Any]]:
    args = [*fn.args.posonlyargs, *fn.args.args]
    defaults: list[ast.AST | None] = [None] * (len(args) - len(fn.args.defaults))
    defaults.extend(fn.args.defaults)

    specs: list[tuple[str, str, bool, Any]] = []
    for arg, default_node in zip(args, defaults, strict=True):
        has_default = default_node is not None
        default = _literal_default(default_node) if default_node is not None else None
        specs.append(
            (
                arg.arg,
                _type_label_from_ast(arg.annotation),
                has_default,
                None if default is _MISSING else default,
            )
        )

    for arg, default_node in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=True):
        has_default = default_node is not None
        default = _literal_default(default_node) if default_node is not None else None
        specs.append(
            (
                arg.arg,
                _type_label_from_ast(arg.annotation),
                has_default,
                None if default is _MISSING else default,
            )
        )

    return specs


def discover_module_function_manifests(
    module_id: str,
    source: str,
    *,
    category: str = "Custom",
) -> tuple[list[NodeManifest], list[tuple[str, str]]]:
    """Statically discover top-level uploaded functions without executing code.

    API preview and palette manifest endpoints must use this helper instead of
    ``register_module_functions`` so top-level side effects in uploaded modules
    cannot run inside the API process. Runtime registration still uses
    ``register_module_functions`` because workflow execution needs callables.
    """
    tree = ast.parse(source)
    manifests: list[NodeManifest] = []
    skipped: list[tuple[str, str]] = []

    for stmt in tree.body:
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if stmt.args.vararg is not None or stmt.args.kwarg is not None:
            skipped.append((stmt.name, "*args / **kwargs are not supported"))
            continue

        param_specs = _function_param_specs(stmt)
        # n8n-style: the node has a single "input" port (the upstream data
        # envelope, available as $json in expressions) and every function
        # parameter shows up in the inspector. Users wire upstream into the
        # one port and reference its fields via {{ $json.field }} in each
        # param — or set literals/expressions directly.
        input_names = ["input"]
        params = [
            ParamSpec(
                name=name,
                type=type_label,
                required=not has_default,
                default=default,
            )
            for name, type_label, has_default, default in param_specs
        ]
        manifests.append(
            NodeManifest(
                id=f"user:{module_id}:{stmt.name}",
                name=stmt.name,
                category=category,
                version="1.0.0",
                description=ast.get_docstring(stmt) or "",
                icon=None,
                inputs=[PortSpec(name=name) for name in input_names],
                params=params,
                outputs=[PortSpec(name="main")],
            )
        )

    return manifests, skipped


def register_module_functions(
    module_id: str,
    source: str,
    registry: NodeRegistry,
    *,
    category: str = "Custom",
) -> tuple[list[str], list[tuple[str, str]]]:
    """Exec ``source`` and register each top-level function as a runtime node.

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

    This executes uploaded Python. Do not call it from API preview or palette
    manifest endpoints; use ``discover_module_function_manifests`` there.
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

        # n8n-style: one virtual "input" port + every function parameter
        # in the inspector. The engine binds the wired upstream value to
        # $json (for expression evaluation) and filters kwargs to the
        # function's actual signature before calling — so the virtual
        # "input" port isn't passed unless the user happened to name a
        # parameter ``input``.
        try:
            hints = get_type_hints(value)
        except Exception:  # noqa: BLE001 - bad annotation strings shouldn't crash registration
            hints = {}
        config_specs: list[ParamSpec] = []
        for pname, param in params.items():
            has_default = param.default is not inspect.Parameter.empty
            config_specs.append(
                ParamSpec(
                    name=pname,
                    type=_type_label(hints.get(pname, str)),
                    required=not has_default,
                    default=param.default if has_default else None,
                )
            )
        manifest = NodeManifest(
            id=f"user:{module_id}:{key}",
            name=key,
            category=category,
            version="1.0.0",
            description=(value.__doc__ or "").strip(),
            icon=None,
            inputs=[PortSpec(name="input")],
            params=config_specs,
            outputs=[PortSpec(name="main")],
        )

        sig_param_names, accepts_var_kw = _signature_info(value)
        node_def = NodeDef(
            func=value,
            manifest=manifest,
            is_async=inspect.iscoroutinefunction(value),
            param_names=sig_param_names,
            accepts_var_keyword=accepts_var_kw,
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
        param_names, has_var_kw = _signature_info(func)
        node_def = NodeDef(
            func=func,
            manifest=manifest,
            is_async=inspect.iscoroutinefunction(func),
            param_names=param_names,
            accepts_var_keyword=has_var_kw,
        )
        registry.register(node_def)
        func.__noodle_node__ = node_def  # type: ignore[attr-defined]
        return func

    return decorator
