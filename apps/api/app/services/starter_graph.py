"""AST-aware starter-graph generator for uploaded code modules.

Parses the uploaded file and emits a ``WorkflowGraph`` payload by walking
the module body:

* Each top-level ``def`` becomes a node (laid out left→right in declaration
  order). Functions taking ``*args``/``**kwargs`` are skipped to match
  ``register_module_functions``.
* For every module-level ``<var> = <call>``, the LHS variable name is
  recorded → caller node id.
* For every call to one of those functions, positional and keyword arguments
  that reference a known variable wire an **edge** from the variable's
  source node into the calling node's matching input port.
* Literal arguments (``Constant``, ``List``, ``Dict``, ``Tuple``) become the
  node's default param values, so the starter graph looks like the script
  with the same arguments.
"""

import ast
from typing import Any


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


def build_starter_graph(module_id: str, source: str) -> dict:
    """Return a ``{"nodes": [...], "edges": [...]}`` graph payload."""
    tree = ast.parse(source)

    # Discover all user functions and their parameter lists. Mirror
    # register_module_functions: skip *args/**kwargs. Required params
    # (no default) are wired input ports; defaulted ones are config.
    function_params: dict[str, list[str]] = {}
    function_input_ports: dict[str, set[str]] = {}
    function_order: list[str] = []
    for stmt in tree.body:
        if not isinstance(stmt, ast.FunctionDef):
            continue
        if stmt.args.vararg is not None or stmt.args.kwarg is not None:
            continue
        args = stmt.args.args
        defaults_count = len(stmt.args.defaults)
        required_cutoff = len(args) - defaults_count
        param_names = [a.arg for a in args]
        function_params[stmt.name] = param_names
        function_input_ports[stmt.name] = {
            a.arg for a in args[:required_cutoff]
        }
        function_order.append(stmt.name)

    if not function_order:
        return {"nodes": [], "edges": []}

    # Layout: a row of nodes from left to right, 240px apart.
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

    def get_node(nid: str) -> dict:
        return next(n for n in nodes if n["id"] == nid)

    def wire_arg(
        target_node_id: str,
        port: str,
        value: ast.AST,
        wired_ports: set[str],
    ) -> None:
        """Wire one argument: either an edge from a known var, or a default param."""
        nonlocal edge_seq
        if isinstance(value, ast.Name) and value.id in symbol_table:
            edges.append(
                {
                    "id": f"e{edge_seq}",
                    "source": symbol_table[value.id],
                    "source_output": "main",
                    "target": target_node_id,
                    "target_input": port,
                }
            )
            edge_seq += 1
            return
        # Wired (required) ports are data-only — they have no inspector slot,
        # so we can't push a literal there. The user can replace the edge
        # with a Constant node after accepting the starter graph.
        if port in wired_ports:
            return
        lit = _literal_value(value)
        if lit is not None:
            get_node(target_node_id)["params"][port] = lit

    def handle_call(call: ast.Call) -> str | None:
        if not isinstance(call.func, ast.Name):
            return None
        fname = call.func.id
        if fname not in node_id_by_func:
            return None
        params = function_params[fname]
        wired_ports = function_input_ports[fname]
        nid = node_id_by_func[fname]

        for idx, arg in enumerate(call.args):
            if idx >= len(params):
                break
            wire_arg(nid, params[idx], arg, wired_ports)

        for kw in call.keywords:
            if kw.arg is None or kw.arg not in params:
                continue
            wire_arg(nid, kw.arg, kw.value, wired_ports)

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
