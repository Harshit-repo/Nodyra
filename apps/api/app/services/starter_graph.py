"""AST-aware starter-graph generator for uploaded code modules.

Parses the uploaded file and emits a ``WorkflowGraph`` payload by walking
the module body. Under the single-port model:

* Each top-level ``def`` becomes a node with one virtual ``input`` port
  (the upstream data envelope, available as ``$json`` in expressions).
* Every function parameter lives in the inspector.
* For each module-level ``<var> = <call>``, the LHS variable name is
  recorded → caller node id.
* When a call passes a variable reference, the *first* such reference
  becomes the wired edge into the node's ``input`` port, and that
  parameter's value is set to ``{{ $json }}``. Any *additional*
  variable references become cross-node expressions
  ``{{ $node["<id>"].main }}`` in their respective inspector fields.
* Literal arguments become the node's default param values.
"""

import ast
import json
from typing import Any

from noodle.sdk import discover_module_nodes


def _literal_value(node: ast.AST) -> Any:
    """Extract a Python literal from common literal AST nodes; else ``None``."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_literal_value(e) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return [_literal_value(e) for e in node.elts]
    if isinstance(node, ast.Dict):
        out: dict = {}
        for key, value in zip(node.keys, node.values, strict=False):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                out[key.value] = _literal_value(value)
        return out
    return None


def _wired_starter_graph(module_id: str, source: str, include_undecorated: bool) -> dict:
    """Build a graph from ``@node`` decorators and their declared ``wires``.

    Each decorated function becomes a node; ``wires`` entries become edges.
    Node graph ids are ``n_<declared_id>`` so a wire's ``"<source_id>"`` /
    ``"<source_id>.<output>"`` resolves to the matching node.
    """
    discovered, _ = discover_module_nodes(
        module_id, source, include_undecorated=include_undecorated
    )

    nodes: list[dict] = []
    node_id_by_declared: dict[str, str] = {}
    for i, dn in enumerate(discovered):
        nid = f"n_{dn.declared_id}"
        node_id_by_declared[dn.declared_id] = nid
        nodes.append(
            {
                "id": nid,
                "type": dn.manifest.id,
                "params": {},
                "position": {"x": 60.0 + i * 240.0, "y": 120.0},
                "disabled": False,
            }
        )

    edges: list[dict] = []
    edge_seq = 0
    for dn in discovered:
        target_nid = node_id_by_declared[dn.declared_id]
        for input_port, spec in dn.wires.items():
            source_declared, _, output_port = spec.partition(".")
            source_nid = node_id_by_declared.get(source_declared)
            if source_nid is None:
                continue  # wire references an unknown / excluded function
            edges.append(
                {
                    "id": f"e{edge_seq}",
                    "source": source_nid,
                    "source_output": output_port or "main",
                    "target": target_nid,
                    "target_input": input_port,
                }
            )
            edge_seq += 1

    return {"nodes": nodes, "edges": edges}


def build_starter_graph(
    module_id: str, source: str, *, include_undecorated: bool = False
) -> dict:
    """Return a ``{"nodes": [...], "edges": [...]}`` graph payload.

    When the module uses ``@node`` decorators, edges come from each node's
    declared ``wires``. Otherwise edges are inferred from module-level
    ``<var> = <call>`` data flow.
    """
    tree = ast.parse(source)

    # Explicit mode: any top-level function carries an ``@node`` decorator.
    for stmt in tree.body:
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in stmt.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            name = (
                target.id
                if isinstance(target, ast.Name)
                else target.attr
                if isinstance(target, ast.Attribute)
                else None
            )
            if name == "node":
                return _wired_starter_graph(module_id, source, include_undecorated)

    # Discover top-level functions (skip *args/**kwargs).
    function_params: dict[str, list[str]] = {}
    function_order: list[str] = []
    for stmt in tree.body:
        if not isinstance(stmt, ast.FunctionDef):
            continue
        if stmt.args.vararg is not None or stmt.args.kwarg is not None:
            continue
        function_params[stmt.name] = [a.arg for a in stmt.args.args]
        function_order.append(stmt.name)

    if not function_order:
        return {"nodes": [], "edges": []}

    # One node per function, laid out left→right.
    nodes: list[dict] = []
    node_id_by_func: dict[str, str] = {}
    for i, fname in enumerate(function_order):
        nid = f"n_{fname}"
        node_id_by_func[fname] = nid
        nodes.append(
            {
                "id": nid,
                "type": f"user:{module_id}:{fname}",
                "params": {},
                "position": {"x": 60.0 + i * 240.0, "y": 120.0},
                "disabled": False,
            }
        )

    symbol_table: dict[str, str] = {}  # variable name → source node id
    edges: list[dict] = []
    edge_seq = 0
    # Track which target nodes already have their single ``input`` port wired,
    # so subsequent variable references fall back to cross-node expressions.
    wired_targets: set[str] = set()

    def get_node(nid: str) -> dict:
        return next(n for n in nodes if n["id"] == nid)

    def wire_arg(target_node_id: str, port: str, value: ast.AST) -> None:
        """Set one inspector field on ``target_node_id`` based on a call arg."""
        nonlocal edge_seq
        if isinstance(value, ast.Name) and value.id in symbol_table:
            source_node_id = symbol_table[value.id]
            if target_node_id not in wired_targets:
                edges.append(
                    {
                        "id": f"e{edge_seq}",
                        "source": source_node_id,
                        "source_output": "main",
                        "target": target_node_id,
                        "target_input": "input",
                    }
                )
                edge_seq += 1
                wired_targets.add(target_node_id)
                # The wired upstream is available as $json in expressions.
                get_node(target_node_id)["params"][port] = "{{ $json }}"
            else:
                # Already wired to a different upstream — use a cross-node
                # expression to reach this one.
                expr = f'{{{{ $node["{source_node_id}"].main }}}}'
                get_node(target_node_id)["params"][port] = expr
            return
        lit = _literal_value(value)
        if lit is None:
            return
        # JSON-friendly: bool/int/float/str/list/dict are all fine; the engine
        # stores params as JSON anyway.
        try:
            json.dumps(lit)
        except TypeError:
            return
        get_node(target_node_id)["params"][port] = lit

    def handle_call(call: ast.Call) -> str | None:
        if not isinstance(call.func, ast.Name):
            return None
        fname = call.func.id
        if fname not in node_id_by_func:
            return None
        params = function_params[fname]
        nid = node_id_by_func[fname]

        for idx, arg in enumerate(call.args):
            if idx >= len(params):
                break
            wire_arg(nid, params[idx], arg)

        for kw in call.keywords:
            if kw.arg is None or kw.arg not in params:
                continue
            wire_arg(nid, kw.arg, kw.value)

        return nid

    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            if not isinstance(stmt.value, ast.Call):
                continue
            nid = handle_call(stmt.value)
            if nid is None:
                continue
            if len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                symbol_table[stmt.targets[0].id] = nid
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            handle_call(stmt.value)

    return {"nodes": nodes, "edges": edges}
