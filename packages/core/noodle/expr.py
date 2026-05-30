"""Expression evaluation for node parameters.

Inside any string parameter, ``{{ ... }}`` blocks are evaluated against a small
context before the node runs. Available bindings:

* ``$json``  — the value flowing into this node (the first wired input).
* ``$input`` — every wired input value, keyed by input port name.
* ``$node``  — every upstream node's outputs, keyed by node id.
* ``$now``   — the current ``datetime`` in UTC.

Dotted access mirrors subscript access (``$json.status`` == ``$json["status"]``).
Missing keys return ``None`` rather than raising so expressions stay forgiving.
"""

import ast
import re
from datetime import UTC, datetime
from typing import Any

_EXPR_RE = re.compile(r"\{\{\s*(.+?)\s*\}\}", re.DOTALL)

# Map the ``$`` aliases to legal Python identifiers used inside eval.
_ALIASES = (
    ("$json", "_json"),
    ("$input", "_input"),
    ("$node", "_node"),
    ("$now", "_now"),
)

_SAFE_BUILTINS: dict[str, Any] = {
    "True": True,
    "False": False,
    "None": None,
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "range": range,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
    "zip": zip,
}

# AST node types that are safe in expressions.
_ALLOWED_EXPR_NODES = frozenset({
    ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp,
    ast.IfExp, ast.Compare, ast.Call, ast.Constant,
    ast.Attribute, ast.Subscript, ast.Index, ast.Slice,
    ast.List, ast.Tuple, ast.Dict, ast.Set,
    ast.Name, ast.Load, ast.Store, ast.Del,
    ast.And, ast.Or, ast.Not,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
    ast.FloorDiv, ast.Pow, ast.LShift, ast.RShift,
    ast.BitAnd, ast.BitOr, ast.BitXor, ast.Invert,
    ast.UAdd, ast.USub,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Is, ast.IsNot, ast.In, ast.NotIn,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
    ast.comprehension, ast.Starred,
    ast.JoinedStr, ast.FormattedValue,  # f-strings
})

_BLOCKED_NAMES = frozenset({
    "__class__", "__bases__", "__subclasses__", "__mro__",
    "__globals__", "__builtins__", "__import__", "__loader__",
    "exec", "eval", "compile", "open", "__code__",
    "__reduce__", "__reduce_ex__", "__init_subclass__",
})


class _ExprValidator(ast.NodeVisitor):
    """Walk AST and reject any disallowed node type or dangerous name."""

    def visit(self, node: ast.AST) -> None:
        if type(node) not in _ALLOWED_EXPR_NODES:
            raise ValueError(
                f"Expression contains disallowed construct: {type(node).__name__}"
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _BLOCKED_NAMES:
            raise ValueError(f"Expression references blocked name: {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _BLOCKED_NAMES:
            raise ValueError(f"Expression accesses blocked attribute: {node.attr}")
        self.generic_visit(node)


# AST node types that are explicitly blocked in code node exec() mode.
# Everything not blocked is allowed — this is more permissive than the
# expression validator but still prevents the worst escapes.
_BLOCKED_STMT_NODES = frozenset({
    ast.Global,
    ast.Nonlocal,
    ast.ClassDef,
})

# Modules that may be imported from inside a Code node. Anything else
# (os, sys, subprocess, socket, importlib, pathlib, ctypes, etc.) is rejected.
CODE_NODE_ALLOWED_IMPORTS = frozenset({
    "pandas",
    "numpy",
    "json",
    "math",
    "re",
    "datetime",
    "statistics",
    "collections",
    "itertools",
    "functools",
    "decimal",
    "fractions",
    "random",
    "uuid",
    "base64",
    "hashlib",
    "string",
    "textwrap",
    "csv",
    "io",
})


def _root_module(name: str) -> str:
    return name.split(".", 1)[0] if name else ""


class _CodeValidator(ast.NodeVisitor):
    """Validate exec()-mode code: block class defs, blocked names, and
    imports outside the safe allowlist.

    Unlike _ExprValidator which allowlists node types, this validator
    blocklists the dangerous constructs so normal control flow (if/for/while/
    try/with/def) all work fine.
    """

    def generic_visit(self, node: ast.AST) -> None:
        if type(node) in _BLOCKED_STMT_NODES:
            raise ValueError(
                f"Code node disallows: {type(node).__name__} — "
                "use built-in functions or pass data via the input variable"
            )
        super().generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = _root_module(alias.name)
            if root not in CODE_NODE_ALLOWED_IMPORTS:
                raise ValueError(
                    f"Code node disallows import of '{alias.name}' — "
                    f"allowed modules: {', '.join(sorted(CODE_NODE_ALLOWED_IMPORTS))}"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        root = _root_module(node.module or "")
        if not root or root not in CODE_NODE_ALLOWED_IMPORTS:
            raise ValueError(
                f"Code node disallows import from '{node.module}' — "
                f"allowed modules: {', '.join(sorted(CODE_NODE_ALLOWED_IMPORTS))}"
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _BLOCKED_NAMES:
            raise ValueError(f"Code references blocked name: {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _BLOCKED_NAMES:
            raise ValueError(f"Code accesses blocked attribute: {node.attr}")
        self.generic_visit(node)


class _Attrible:
    """Wrap a JSON-ish value so attribute access mirrors subscript access."""

    __slots__ = ("_data",)

    def __init__(self, data: Any) -> None:
        self._data = data

    def __getattr__(self, key: str) -> Any:
        if key.startswith("_"):
            raise AttributeError(key)
        if isinstance(self._data, dict):
            return _wrap(self._data.get(key))
        return None

    def __getitem__(self, key: Any) -> Any:
        if isinstance(self._data, dict):
            return _wrap(self._data.get(key))
        if isinstance(self._data, list):
            try:
                return _wrap(self._data[int(key)])
            except (ValueError, IndexError, TypeError):
                return None
        return None

    def __iter__(self):
        if hasattr(self._data, "__iter__"):
            return iter(self._data)
        return iter([])

    def __len__(self) -> int:
        if hasattr(self._data, "__len__"):
            return len(self._data)
        return 0

    def __bool__(self) -> bool:
        return bool(self._data)

    def __repr__(self) -> str:
        return repr(self._data)

    def __str__(self) -> str:
        return str(self._data)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _Attrible):
            return self._data == other._data
        return self._data == other

    def __hash__(self) -> int:
        return hash(repr(self._data))


def _wrap(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return _Attrible(value)
    return value


def build_context(
    *,
    first_input: Any = None,
    inputs: dict[str, Any] | None = None,
    node_outputs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the eval locals dict for a node about to execute."""
    return {
        "_json": _wrap(first_input),
        "_input": _wrap(inputs or {}),
        "_node": {nid: _wrap(outs) for nid, outs in (node_outputs or {}).items()},
        "_now": datetime.now(UTC),
    }


def _eval_one(expression: str, context: dict[str, Any]) -> Any:
    code = expression
    for alias, target in _ALIASES:
        code = code.replace(alias, target)
    try:
        tree = ast.parse(code, mode="eval")
    except SyntaxError as exc:
        return f"[expr error: SyntaxError: {exc}]"
    try:
        _ExprValidator().visit(tree)
    except ValueError as exc:
        return f"[expr error: {exc}]"
    try:
        return eval(  # noqa: S307 - AST-validated above
            compile(tree, "<expression>", "eval"),
            {"__builtins__": _SAFE_BUILTINS},
            context,
        )
    except Exception as exc:  # noqa: BLE001 - any failure becomes a friendly error
        return f"[expr error: {type(exc).__name__}: {exc}]"


def evaluate(value: Any, context: dict[str, Any]) -> Any:
    """Walk ``value`` recursively and evaluate every ``{{ ... }}`` block."""
    if isinstance(value, str):
        matches = list(_EXPR_RE.finditer(value))
        if not matches:
            return value
        if len(matches) == 1 and matches[0].group(0) == value.strip():
            # Whole string is a single expression — return the raw value.
            return _eval_one(matches[0].group(1), context)
        return _EXPR_RE.sub(
            lambda m: str(_eval_one(m.group(1), context)), value
        )
    if isinstance(value, dict):
        return {key: evaluate(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [evaluate(item, context) for item in value]
    return value


def contains_expression(value: Any) -> bool:
    """Quick test used by the editor to surface an "expression" hint."""
    if isinstance(value, str):
        return bool(_EXPR_RE.search(value))
    if isinstance(value, dict):
        return any(contains_expression(v) for v in value.values())
    if isinstance(value, list):
        return any(contains_expression(v) for v in value)
    return False


def evaluate_parts(value: str, context: dict[str, Any]) -> list[dict[str, Any]]:
    """Split ``value`` into literal/expression parts with each expression
    individually evaluated. Used by the editor preview so each ``{{ }}`` block
    can be colorized with its resolved value while the surrounding literal text
    stays plain.

    Each part is one of:
      * ``{"kind": "text",  "value": <literal>}``
      * ``{"kind": "expr",  "raw": "{{ ... }}", "value": <resolved>}``
      * ``{"kind": "error", "raw": "{{ ... }}", "error": <message>}``
    """
    parts: list[dict[str, Any]] = []
    cursor = 0
    for match in _EXPR_RE.finditer(value):
        if match.start() > cursor:
            parts.append({"kind": "text", "value": value[cursor:match.start()]})
        raw = match.group(0)
        result = _eval_one(match.group(1), context)
        if isinstance(result, str) and result.startswith("[expr error:"):
            parts.append({"kind": "error", "raw": raw, "error": result.strip("[]")})
        else:
            parts.append({"kind": "expr", "raw": raw, "value": result})
        cursor = match.end()
    if cursor < len(value):
        parts.append({"kind": "text", "value": value[cursor:]})
    return parts
