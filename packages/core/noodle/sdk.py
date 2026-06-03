"""Node SDK: the ``@node`` decorator, manifest generation, and the node registry.

A node is a plain Python function with:

* **input ports** — wired data connections. Function parameters whose names are
  listed in ``@node(inputs=[...])`` are input ports (default ``["input"]``;
  triggers pass ``inputs=[]``).
* **config parameters** — every other function parameter. These are edited in
  the inspector, never wired.
* **outputs** — declared with ``@node(outputs=[...])`` (default ``["main"]``);
  a multi-output node returns a dict keyed by those names.

Per-parameter UI metadata (choices, multiline, placeholder, description,
credential selectors) is supplied via the decorator's ``params`` argument.
"""

import ast
import contextvars
import inspect
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, get_args, get_origin, get_type_hints

from noodle.models import CredentialSpec, NodeManifest, ParamSpec, PortSpec

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


def _dict_meta(meta: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = meta.get(key)
    return value if isinstance(value, dict) else None


def _list_meta(meta: dict[str, Any], key: str) -> list[str]:
    value = meta.get(key)
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return [str(value)]


def _param_meta_kwargs(meta: dict[str, Any]) -> dict[str, Any]:
    """Return rich ParamSpec kwargs shared by runtime and AST discovery paths."""
    return {
        "group": meta.get("group") or None,
        "display_name": str(meta.get("display_name") or ""),
        "display_when": _dict_meta(meta, "display_when"),
        "hide_when": _dict_meta(meta, "hide_when"),
        "widget": str(meta.get("widget") or ""),
        "depends_on": _list_meta(meta, "depends_on"),
        "load_options": meta.get("load_options") or None,
        "resource_mapper": _dict_meta(meta, "resource_mapper"),
        "fixed_collection": _dict_meta(meta, "fixed_collection"),
        "credential_type": meta.get("credential_type") or None,
        "required_scopes": _list_meta(meta, "required_scopes"),
        "advanced": bool(meta.get("advanced", False)),
        "documentation_url": str(meta.get("documentation_url") or ""),
        "validation": _dict_meta(meta, "validation"),
    }


def _apply_param_groups(
    params: dict[str, dict[str, Any]] | None,
    param_groups: dict[str, list[str]] | None,
) -> dict[str, dict[str, Any]]:
    """Apply ``param_groups`` shorthand while preserving explicit per-param group."""
    param_meta = {
        key: dict(value) if isinstance(value, dict) else {}
        for key, value in (params or {}).items()
    }
    for group_name, names in (param_groups or {}).items():
        names_iter = [names] if isinstance(names, str) else names
        for pname in names_iter:
            meta = param_meta.setdefault(str(pname), {})
            meta.setdefault("group", group_name)
    return param_meta


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
    # Downstream-declared wiring: maps this node's input-port name to the
    # source it should be fed from, ``"<source_id>"`` or
    # ``"<source_id>.<output_port>"``. Used at graph-build time, not by the
    # engine. Only populated for ``@node``-decorated functions that pass
    # ``wires=...``.
    wires: dict[str, str] = field(default_factory=dict)
    # The id declared on the decorator (or the function name). For user
    # modules the registry id is namespaced ``user:<module_id>:<declared_id>``
    # while ``wires`` references use this bare declared id.
    declared_id: str = ""


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

# When truthy, the ``@node`` decorator only attaches metadata to the function
# and does NOT register into a global registry. ``register_module_functions``
# sets this while exec'ing user modules so a bare ``@node`` in user code can't
# pollute the process-wide registry or crash on a duplicate / non-namespaced
# id; the function's ``__noodle_node__`` metadata is read back afterwards and
# registered under a namespaced id instead.
_suppress_registration: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "noodle_suppress_node_registration", default=False
)


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
    role: str,
    hidden: bool,
    deprecated: bool,
    replacement_id: str | None,
    param_meta: dict[str, dict[str, Any]],
    inputs: list[str],
    outputs: list[str],
    icon: str | None,
    input_kinds: dict[str, str] | None = None,
    output_kinds: dict[str, str] | None = None,
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
        credential_meta = meta.get("credential")
        credential = (
            CredentialSpec.model_validate(credential_meta)
            if isinstance(credential_meta, dict)
            else None
        )
        params.append(
            ParamSpec(
                name=pname,
                type="credential" if credential else _type_label(hints.get(pname, str)),
                required=not has_default,
                default=param.default if has_default else None,
                description=meta.get("description", ""),
                placeholder=meta.get("placeholder", ""),
                choices=meta.get("choices"),
                multiline=bool(meta.get("multiline", False)),
                key_value=bool(meta.get("key_value", False)),
                credential=credential,
                **_param_meta_kwargs(meta),
            )
        )

    in_kinds = input_kinds or {}
    out_kinds = output_kinds or {}
    return NodeManifest(
        id=node_id,
        name=name,
        category=category,
        version=version,
        description=description,
        icon=icon,
        role=role,
        hidden=hidden,
        deprecated=deprecated,
        replacement_id=replacement_id,
        inputs=[
            PortSpec(name=n, data_kind=in_kinds.get(n, "any"))
            for n in inputs
        ],
        params=params,
        outputs=[
            PortSpec(name=o, data_kind=out_kinds.get(o, "any"))
            for o in outputs
        ],
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


@dataclass
class DiscoveredNode:
    """A node found by static discovery, plus its declared wiring.

    ``manifest`` carries the namespaced id (``user:<module_id>:<declared_id>``).
    ``declared_id`` is the bare id ``wires`` entries reference. ``wires`` maps an
    input-port name to ``\"<source_id>\"`` / ``\"<source_id>.<output>\"``.
    """

    manifest: NodeManifest
    declared_id: str
    wires: dict[str, str] = field(default_factory=dict)
    decorated: bool = False


def _find_node_decorator(
    stmt: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.expr | None:
    """Return the ``@node`` decorator node (call or bare name), else ``None``."""
    for dec in stmt.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if _annotation_name(target) == "node":
            return dec
    return None


def _decorator_kwargs(dec: ast.expr) -> dict[str, Any]:
    """Statically evaluate the literal keyword args of an ``@node(...)`` call.

    Non-literal arguments (references, f-strings, calls) are skipped rather than
    failing the whole discovery — they just won't contribute UI metadata.
    """
    if not isinstance(dec, ast.Call):
        return {}
    out: dict[str, Any] = {}
    for kw in dec.keywords:
        if kw.arg is None:
            continue
        try:
            out[kw.arg] = ast.literal_eval(kw.value)
        except (ValueError, TypeError, SyntaxError):
            continue
    return out


def _decorated_node_from_ast(
    module_id: str,
    stmt: ast.FunctionDef | ast.AsyncFunctionDef,
    kwargs: dict[str, Any],
    default_category: str,
) -> DiscoveredNode:
    """Build a :class:`DiscoveredNode` from a function's ``@node`` metadata."""
    declared_id = str(kwargs.get("id") or stmt.name)
    name = str(kwargs.get("name") or stmt.name)
    category = str(kwargs.get("category") or default_category)
    version = str(kwargs.get("version") or "1.0.0")
    description = str(kwargs.get("description") or ast.get_docstring(stmt) or "")
    role = str(kwargs.get("role") or "executable")
    hidden = bool(kwargs.get("hidden", False))
    deprecated = bool(kwargs.get("deprecated", False))
    replacement_id = kwargs.get("replacement_id")
    if replacement_id is not None:
        replacement_id = str(replacement_id)
    icon = kwargs.get("icon")
    inputs = kwargs.get("inputs")
    inputs = ["input"] if inputs is None else list(inputs)
    outputs = list(kwargs.get("outputs") or ["main"])
    input_kinds = kwargs.get("input_kinds") or {}
    output_kinds = kwargs.get("output_kinds") or {}
    raw_param_meta = kwargs.get("params") or {}
    if not isinstance(raw_param_meta, dict):
        raw_param_meta = {}
    raw_param_groups = kwargs.get("param_groups") or {}
    if not isinstance(raw_param_groups, dict):
        raw_param_groups = {}
    param_meta = _apply_param_groups(raw_param_meta, raw_param_groups)

    input_set = set(inputs)
    params: list[ParamSpec] = []
    for pname, type_label, has_default, default in _function_param_specs(stmt):
        if pname in input_set:
            continue  # wired input port, not a config parameter
        meta = param_meta.get(pname, {})
        if not isinstance(meta, dict):
            meta = {}
        credential_meta = meta.get("credential")
        credential = (
            CredentialSpec.model_validate(credential_meta)
            if isinstance(credential_meta, dict)
            else None
        )
        params.append(
            ParamSpec(
                name=pname,
                type="credential" if credential else type_label,
                required=not has_default,
                default=default,
                description=str(meta.get("description", "")),
                placeholder=str(meta.get("placeholder", "")),
                choices=meta.get("choices"),
                multiline=bool(meta.get("multiline", False)),
                key_value=bool(meta.get("key_value", False)),
                credential=credential,
                **_param_meta_kwargs(meta),
            )
        )

    manifest = NodeManifest(
        id=f"user:{module_id}:{declared_id}",
        name=name,
        category=category,
        version=version,
        description=description,
        icon=icon,
        role=role,
        hidden=hidden,
        deprecated=deprecated,
        replacement_id=replacement_id,
        inputs=[
            PortSpec(name=n, data_kind=input_kinds.get(n, "any")) for n in inputs
        ],
        params=params,
        outputs=[
            PortSpec(name=o, data_kind=output_kinds.get(o, "any")) for o in outputs
        ],
    )
    raw_wires = kwargs.get("wires") or {}
    wires = (
        {str(k): str(v) for k, v in raw_wires.items()}
        if isinstance(raw_wires, dict)
        else {}
    )
    return DiscoveredNode(
        manifest=manifest, declared_id=declared_id, wires=wires, decorated=True
    )


def _auto_node_from_ast(
    module_id: str,
    stmt: ast.FunctionDef | ast.AsyncFunctionDef,
    category: str,
) -> DiscoveredNode:
    """Build a single-port auto node for an undecorated function."""
    params = [
        ParamSpec(name=name, type=type_label, required=not has_default, default=default)
        for name, type_label, has_default, default in _function_param_specs(stmt)
    ]
    manifest = NodeManifest(
        id=f"user:{module_id}:{stmt.name}",
        name=stmt.name,
        category=category,
        version="1.0.0",
        description=ast.get_docstring(stmt) or "",
        icon=None,
        inputs=[PortSpec(name="input")],
        params=params,
        outputs=[PortSpec(name="main")],
    )
    return DiscoveredNode(manifest=manifest, declared_id=stmt.name, wires={})


def discover_module_nodes(
    module_id: str,
    source: str,
    *,
    category: str = "Custom",
    include_undecorated: bool = False,
) -> tuple[list[DiscoveredNode], list[tuple[str, str]]]:
    """Statically discover the nodes a module exposes without executing code.

    If any top-level function carries an ``@node`` decorator the module is in
    *explicit mode*: only decorated functions become nodes, and undecorated
    functions are treated as helpers (callable at runtime but not shown) unless
    ``include_undecorated`` is set, in which case they are also auto-converted.
    A module with no decorators keeps the original behaviour: every top-level
    function becomes a single-port node.
    """
    tree = ast.parse(source)
    functions = [
        s for s in tree.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    decorators = {s.name: _find_node_decorator(s) for s in functions}
    explicit_mode = any(d is not None for d in decorators.values())

    discovered: list[DiscoveredNode] = []
    skipped: list[tuple[str, str]] = []

    for stmt in functions:
        dec = decorators[stmt.name]
        if dec is not None:
            discovered.append(
                _decorated_node_from_ast(
                    module_id, stmt, _decorator_kwargs(dec), category
                )
            )
            continue
        # Undecorated function.
        if explicit_mode and not include_undecorated:
            continue  # helper — callable at runtime, not surfaced as a node
        if stmt.args.vararg is not None or stmt.args.kwarg is not None:
            skipped.append((stmt.name, "*args / **kwargs are not supported"))
            continue
        discovered.append(_auto_node_from_ast(module_id, stmt, category))

    return discovered, skipped


def discover_module_function_manifests(
    module_id: str,
    source: str,
    *,
    category: str = "Custom",
    include_undecorated: bool = False,
) -> tuple[list[NodeManifest], list[tuple[str, str]]]:
    """Statically discover top-level uploaded functions without executing code.

    API preview and palette manifest endpoints must use this helper instead of
    ``register_module_functions`` so top-level side effects in uploaded modules
    cannot run inside the API process. Runtime registration still uses
    ``register_module_functions`` because workflow execution needs callables.
    """
    discovered, skipped = discover_module_nodes(
        module_id, source, category=category, include_undecorated=include_undecorated
    )
    return [d.manifest for d in discovered], skipped


def register_module_functions(
    module_id: str,
    source: str,
    registry: NodeRegistry,
    *,
    category: str = "Custom",
    include_undecorated: bool = False,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Exec ``source`` and register each top-level function as a runtime node.

    Reuses :func:`_build_manifest` so user-uploaded functions get the exact
    same manifest treatment as ``@node``-decorated built-ins (type hints →
    input ports / param specs, return → output, async detection). Each
    function ``foo`` is registered under id ``user:<module_id>:<foo>``.

    If any top-level function uses the ``@node`` decorator the module is in
    *explicit mode*: only decorated functions are registered as nodes (their
    decorator metadata — ports, params, wiring — is honoured), and undecorated
    functions stay callable helpers unless ``include_undecorated`` is set. A
    module with no decorators registers every top-level function as before.

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
    # Suppress global registration so a bare ``@node`` in user code only
    # attaches ``__noodle_node__`` metadata; we register it ourselves below
    # under a namespaced id.
    token = _suppress_registration.set(True)
    try:
        exec(source, module_globals)  # noqa: S102 - running user Python is the point
    finally:
        _suppress_registration.reset(token)
    new_keys = [k for k in module_globals if k not in pre_keys]

    user_functions = [
        module_globals[k]
        for k in new_keys
        if inspect.isfunction(module_globals[k])
        and module_globals[k].__module__ == module_globals["__name__"]
    ]
    explicit_mode = any(
        getattr(fn, "__noodle_node__", None) is not None for fn in user_functions
    )

    registered: list[str] = []
    skipped: list[tuple[str, str]] = []

    for key in new_keys:
        value = module_globals[key]
        if not inspect.isfunction(value):
            continue
        if value.__module__ != module_globals["__name__"]:
            continue  # imported from elsewhere, not a user function

        decorator_def: NodeDef | None = getattr(value, "__noodle_node__", None)
        if decorator_def is not None:
            # Explicit ``@node`` — honour the decorator's manifest, re-id'd
            # into the module namespace, and carry its declared wiring.
            namespaced_id = f"user:{module_id}:{decorator_def.declared_id}"
            manifest = decorator_def.manifest.model_copy(
                update={"id": namespaced_id}
            )
            registry._nodes[namespaced_id] = NodeDef(  # noqa: SLF001
                func=decorator_def.func,
                manifest=manifest,
                is_async=decorator_def.is_async,
                param_names=decorator_def.param_names,
                accepts_var_keyword=decorator_def.accepts_var_keyword,
                wires=decorator_def.wires,
                declared_id=decorator_def.declared_id,
            )
            registered.append(key)
            continue

        # Undecorated function.
        if explicit_mode and not include_undecorated:
            continue  # helper — callable from nodes at runtime, not a node

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

        # Single-port model: one virtual "input" port + every function
        # parameter in the inspector. The engine binds the wired upstream
        # value to $json (for expression evaluation) and filters kwargs to
        # the function's actual signature before calling — so the virtual
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
            declared_id=key,
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
    role: str = "executable",
    hidden: bool = False,
    deprecated: bool = False,
    replacement_id: str | None = None,
    params: dict[str, dict[str, Any]] | None = None,
    param_groups: dict[str, list[str]] | None = None,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    input_kinds: dict[str, str] | None = None,
    output_kinds: dict[str, str] | None = None,
    icon: str | None = None,
    wires: dict[str, str] | None = None,
    registry: NodeRegistry = registry,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a function as a Noodle node.

    ``inputs`` lists the wired input ports (default ``["input"]``; pass ``[]``
    for triggers). ``params`` maps a config parameter name to UI metadata, e.g.
    ``{"method": {"choices": ["GET", "POST"]}}``. ``outputs`` declares named
    output ports; a node with more than one output must return a dict keyed by
    those names (omit a key to leave that branch untaken).

    ``wires`` declares incoming edges for user-module graphs: it maps one of
    this node's input-port names to the upstream it should be fed from,
    ``"<source_id>"`` (the source's first/``main`` output) or
    ``"<source_id>.<output_port>"``. It is read when generating a starter
    graph and ignored for built-in nodes.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        node_id = id or func.__name__
        # ``param_groups={"Options": ["a", "b"]}`` is a shorthand for setting
        # ``"group"`` on many optional params at once; an explicit per-param
        # ``group`` in ``params`` still wins.
        param_meta = _apply_param_groups(params, param_groups)
        manifest = _build_manifest(
            func,
            node_id=node_id,
            name=name,
            category=category,
            version=version,
            description=description or (func.__doc__ or "").strip(),
            role=role,
            hidden=hidden,
            deprecated=deprecated,
            replacement_id=replacement_id,
            param_meta=param_meta,
            inputs=["input"] if inputs is None else inputs,
            outputs=outputs or ["main"],
            icon=icon,
            input_kinds=input_kinds,
            output_kinds=output_kinds,
        )
        param_names, has_var_kw = _signature_info(func)
        node_def = NodeDef(
            func=func,
            manifest=manifest,
            is_async=inspect.iscoroutinefunction(func),
            param_names=param_names,
            accepts_var_keyword=has_var_kw,
            wires=dict(wires or {}),
            declared_id=node_id,
        )
        func.__noodle_node__ = node_def  # type: ignore[attr-defined]
        if not _suppress_registration.get():
            registry.register(node_def)
        return func

    return decorator
