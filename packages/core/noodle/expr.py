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
    ("$fromAI", "_from_ai"),
)
_ALIAS_MAP = dict(_ALIASES)

# A Python string literal (with optional prefix) OR a ``$alias`` token. We must
# rewrite aliases to legal identifiers without touching their occurrences inside
# string literals — a naive ``str.replace`` corrupted any literal that merely
# *contained* an alias substring (H3). f-strings are the deliberate exception:
# their ``{...}`` fields are real code, so aliases there are still rewritten.
_ALIAS_REWRITE_RE = re.compile(
    r"""
    (?P<str>
        (?P<prefix>[rRbBfFuU]{0,3})
        ( \"\"\"(?:\\.|(?!\"\"\").)*\"\"\"
        | '''(?:\\.|(?!''').)*'''
        | "(?:\\.|[^"\\])*"
        | '(?:\\.|[^'\\])*'
        )
    )
    | (?P<alias>\$[A-Za-z_][A-Za-z0-9_]*)
    """,
    re.DOTALL | re.VERBOSE,
)


def _rewrite_aliases(code: str) -> str:
    """Replace ``$alias`` tokens with their legal-identifier targets, leaving
    occurrences inside ordinary string literals untouched (f-string fields are
    still rewritten so interpolated aliases keep working)."""

    def _sub(match: re.Match[str]) -> str:
        literal = match.group("str")
        if literal is not None:
            prefix = (match.group("prefix") or "").lower()
            if "f" in prefix:
                # f-string: rewrite aliases ONLY inside its ``{...}`` expression
                # fields, never the literal text. A blanket replace would corrupt
                # text that merely contains an alias substring, e.g.
                # ``f"price is $now: {x}"`` would turn the words ``$now`` in the
                # output into ``_now``.
                return _rewrite_fstring_fields(literal)
            return literal
        alias = match.group("alias")
        return _ALIAS_MAP.get(alias, alias)

    return _ALIAS_REWRITE_RE.sub(_sub, code)


# ``{{`` / ``}}`` are escaped braces (literal text); ``{...}`` (no nested brace)
# is a real replacement field whose contents are code.
_FSTRING_FIELD_RE = re.compile(r"\{\{|\}\}|\{[^{}]*\}")


def _rewrite_fstring_fields(literal: str) -> str:
    """Rewrite ``$alias`` tokens inside an f-string's ``{...}`` fields only."""

    def _sub_field(match: re.Match[str]) -> str:
        segment = match.group(0)
        if segment in ("{{", "}}"):
            return segment  # escaped brace — literal text, not a field
        for alias, target in _ALIASES:
            segment = segment.replace(alias, target)
        return segment

    return _FSTRING_FIELD_RE.sub(_sub_field, literal)

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
    """Walk AST and reject any disallowed node type or dangerous name.

    SECURITY (EXPR-1): the node-type allowlist must run via ``generic_visit``,
    NOT by overriding ``visit``. Overriding ``visit`` to do the check + call
    ``generic_visit`` bypasses ``NodeVisitor``'s name-based dispatch, so
    ``visit_Name``/``visit_Attribute`` never fire and the ``_BLOCKED_NAMES``
    guard becomes dead code — letting ``x.__class__.__bases__[0].__subclasses__()``
    and ``__import__`` slip through (full sandbox escape). Putting the type check
    in ``generic_visit`` (which the default ``visit`` always reaches) keeps both
    the type allowlist AND the blocked-name dispatch active.
    """

    def generic_visit(self, node: ast.AST) -> None:
        if type(node) not in _ALLOWED_EXPR_NODES:
            raise ValueError(
                f"Expression contains disallowed construct: {type(node).__name__}"
            )
        super().generic_visit(node)

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


class _CodeValidator(ast.NodeVisitor):
    """Validate exec()-mode code: block class defs and blocked names.

    Imports are allowed — users may freely import any installed library.
    Unlike _ExprValidator which allowlists node types, this validator
    blocklists the dangerous constructs so normal control flow (if/for/while/
    try/with/def/import) all work fine.
    """

    def generic_visit(self, node: ast.AST) -> None:
        if type(node) in _BLOCKED_STMT_NODES:
            raise ValueError(
                f"Code node disallows: {type(node).__name__} — "
                "use built-in functions or pass data via the input variable"
            )
        super().generic_visit(node)

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


def _is_dataset_envelope(value: Any) -> bool:
    """Detect a DatasetRef envelope without importing ``noodle.datasets``.

    Kept local to avoid an import cycle (``datasets`` imports from core
    modules). Mirrors the marker contract in :mod:`noodle.datasets`.
    """
    return (
        isinstance(value, dict)
        and value.get("__noodle_dataset__") is True
        and isinstance(value.get("schema"), list)
    )


class _DatasetColumns:
    """Expose a DatasetRef envelope to expressions in a column-friendly way.

    Dataset-input node fields reference columns *by name* (a ``target_column``,
    a chart axis, a feature list, …), so ``{{ $json.species }}`` resolves to the
    column name ``"species"`` when that column exists. This makes drag-and-drop
    of column chips — which insert ``{{ $json.<col> }}`` — work on dataset-backed
    nodes. Unknown keys fall back to the raw envelope metadata (``row_count``,
    ``schema``, …); ``rows``/``records`` return the preview records.
    """

    __slots__ = ("_env", "_columns")

    def __init__(self, env: dict) -> None:
        self._env = env
        self._columns = [
            c.get("name")
            for c in env.get("schema", [])
            if isinstance(c, dict) and c.get("name")
        ]

    def _get(self, key: Any) -> Any:
        if key in self._columns:
            return key
        if key in ("rows", "records"):
            return _wrap(self._env.get("preview") or [])
        if isinstance(key, str) and key in self._env:
            return _wrap(self._env.get(key))
        return None

    def __getattr__(self, key: str) -> Any:
        if key.startswith("_"):
            raise AttributeError(key)
        return self._get(key)

    def __getitem__(self, key: Any) -> Any:
        return self._get(key)

    def __iter__(self):
        return iter(self._columns)

    def __contains__(self, key: Any) -> bool:
        return key in self._columns

    def __len__(self) -> int:
        return len(self._columns)

    def __bool__(self) -> bool:
        return True

    def __repr__(self) -> str:
        return f"<dataset columns={self._columns!r}>"

    def __str__(self) -> str:
        return ", ".join(str(c) for c in self._columns)


def _wrap(value: Any) -> Any:
    if _is_dataset_envelope(value):
        return _DatasetColumns(value)
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


def from_ai_binding(
    *,
    collector: dict[str, dict[str, str]] | None = None,
    ai_args: dict[str, Any] | None = None,
):
    """Build the ``$fromAI(name, description="", type="string", default=None)``
    callable for the expression context.

    * ``collector`` set → *schema-collection mode*: each call records the arg
      and returns ``default`` (so the expression evaluates harmlessly while we
      derive the tool's schema).
    * ``ai_args`` set → *invoke mode*: each call returns the model-supplied
      argument value (falling back to ``default``).

    ``$fromAI`` must be called positionally — the expression validator does not
    permit keyword arguments.
    """

    def _from_ai(
        name: Any,
        description: Any = "",
        type: Any = "string",  # noqa: A002 - mirrors the $fromAI('name','desc','type') shape
        default: Any = None,
    ) -> Any:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("$fromAI requires a non-empty name")
        key = name.strip()
        if collector is not None:
            collector[key] = {
                "name": key,
                "description": str(description or ""),
                "type": str(type or "string"),
            }
            return default
        if ai_args is not None:
            return ai_args.get(key, default)
        return default

    return _from_ai


def _eval_one(expression: str, context: dict[str, Any]) -> Any:
    code = _rewrite_aliases(expression)
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
