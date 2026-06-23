"""AST-based importer: Noodle .module.py → WorkflowGraph.

Parses the structured output of noodle_exporter.workflow_to_module() without
executing any code. Uses ast.literal_eval() for module-level dicts/lists and
ast.parse() to walk the function definitions.
"""
from __future__ import annotations

import ast
from typing import Any

from noodle.models import WorkflowGraph

_MODULE_VARS = {"_DELEGATES", "_NODE_SETTINGS", "_PARAM_OVERRIDES", "_EXTRA_NODES", "_EXTRA_EDGES"}


def _extract_module_vars(tree: ast.Module) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name) and target.id in _MODULE_VARS:
                try:
                    out[target.id] = ast.literal_eval(stmt.value)
                except (ValueError, TypeError):
                    pass
    return out


def _node_decorator(func_def: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.Call | None:
    for dec in func_def.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        f = dec.func
        if (isinstance(f, ast.Name) and f.id == "node") or (
            isinstance(f, ast.Attribute) and f.attr == "node"
        ):
            return dec
    return None


def _kw_literal(call: ast.Call, name: str) -> Any:
    for kw in call.keywords:
        if kw.arg == name:
            try:
                return ast.literal_eval(kw.value)
            except (ValueError, TypeError):
                return None
    return None


def _signature_params(
    func_def: ast.FunctionDef | ast.AsyncFunctionDef,
    input_ports: list[str],
) -> dict[str, Any]:
    args = func_def.args.args
    defaults = func_def.args.defaults
    offset = len(args) - len(defaults)
    params: dict[str, Any] = {}
    for i, arg in enumerate(args):
        if arg.arg in input_ports or i < offset:
            continue
        try:
            params[arg.arg] = ast.literal_eval(defaults[i - offset])
        except (ValueError, TypeError):
            pass
    return params


def import_module(source: str) -> WorkflowGraph:
    """Parse a Noodle .module.py source string into a WorkflowGraph.

    Raises ImportError with a descriptive message if the source is not a
    valid Noodle module export. Never executes the source code.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ImportError(f"Syntax error in module source: {exc}") from exc

    mv = _extract_module_vars(tree)
    delegates: dict[str, str] = mv.get("_DELEGATES", {})
    node_settings: dict[str, dict] = mv.get("_NODE_SETTINGS", {})
    param_overrides: dict[str, dict] = mv.get("_PARAM_OVERRIDES", {})
    extra_nodes: list[dict] = mv.get("_EXTRA_NODES", [])
    extra_edges: list[dict] = mv.get("_EXTRA_EDGES", [])

    nodes: list[dict] = []
    edges: list[dict] = []

    for stmt in ast.walk(tree):
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        dec = _node_decorator(stmt)
        if dec is None:
            continue

        node_id: str | None = _kw_literal(dec, "id")
        if not node_id:
            continue

        wires: dict[str, str] = _kw_literal(dec, "wires") or {}
        input_ports: list[str] = _kw_literal(dec, "inputs") or []

        params = _signature_params(stmt, input_ports)
        params.update(param_overrides.get(node_id, {}))

        node_type = delegates.get(node_id, stmt.name)
        spec: dict[str, Any] = {
            "id": node_id,
            "type": node_type,
            "params": params,
        }
        spec.update(node_settings.get(node_id, {}))
        nodes.append(spec)

        for port, wire in wires.items():
            src, _, out = wire.partition(".")
            edges.append({
                "id": f"w_{node_id}_{port}",
                "source": src,
                "source_output": out or "main",
                "target": node_id,
                "target_input": port,
            })

    nodes.extend(extra_nodes)
    edges.extend(extra_edges)

    if not nodes:
        raise ImportError(
            "No @node decorated functions found in module source. "
            "The file does not look like a Noodle .module.py export."
        )

    try:
        return WorkflowGraph.model_validate({"nodes": nodes, "edges": edges})
    except Exception as exc:
        raise ImportError(f"Reconstructed graph failed validation: {exc}") from exc
