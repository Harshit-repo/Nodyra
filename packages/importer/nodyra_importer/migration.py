"""Static compatibility analysis for migrations into Nodyra workflows."""

from __future__ import annotations

import ast
import copy
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

from nodyra.models import WorkflowGraph

from .parser import import_module

MigrationStatus = Literal["exact", "transformed", "manual", "unsupported"]
MigrationFormat = Literal[
    "nodyra_module",
    "python_script",
    "n8n",
    "airflow",
    "prefect",
]


@dataclass(frozen=True)
class MigrationFinding:
    source_id: str
    source_type: str
    status: MigrationStatus
    target_type: str | None
    message: str


@dataclass(frozen=True)
class MigrationResult:
    source_format: MigrationFormat
    importable: bool
    partial: bool
    graph: dict[str, Any] | None
    summary: dict[str, int]
    findings: list[MigrationFinding]

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "findings": [asdict(finding) for finding in self.findings],
        }


def _summary(findings: list[MigrationFinding]) -> dict[str, int]:
    return {
        status: sum(finding.status == status for finding in findings)
        for status in ("exact", "transformed", "manual", "unsupported")
    }


def _node(node_id: str, node_type: str, params: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "params": params,
        "position": {"x": (index % 4) * 280, "y": (index // 4) * 180},
    }


def _safe_id(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return normalized[:100] or fallback


def _n8n_mapping(source: dict[str, Any], node_id: str, index: int) -> tuple[dict | None, MigrationFinding]:
    source_type = str(source.get("type") or "unknown")
    params = source.get("parameters") if isinstance(source.get("parameters"), dict) else {}
    source_name = str(source.get("name") or node_id)
    if source_type.endswith(".manualTrigger"):
        return _node(node_id, "manual_trigger", {"data": {}}, index), MigrationFinding(source_name, source_type, "exact", "manual_trigger", "Manual trigger maps exactly.")
    if source_type.endswith(".webhook"):
        mapped = {"path": str(params.get("path") or "imported-webhook"), "response_mode": "On Received"}
        return _node(node_id, "webhook_trigger", mapped, index), MigrationFinding(source_name, source_type, "transformed", "webhook_trigger", "Webhook path was retained; authentication and response policy require review.")
    if source_type.endswith(".httpRequest"):
        mapped = {
            "url": str(params.get("url") or ""),
            "method": str(params.get("method") or "GET").upper(),
            "headers": {},
            "query": {},
            "body": params.get("body") or {},
        }
        return _node(node_id, "http_request", mapped, index), MigrationFinding(source_name, source_type, "transformed", "http_request", "HTTP method and URL were retained; credentials and advanced options require review.")
    if source_type.endswith(".set"):
        fields: dict[str, Any] = {}
        values = params.get("values")
        if isinstance(values, dict):
            for group in values.values():
                if not isinstance(group, list):
                    continue
                for item in group:
                    if isinstance(item, dict) and item.get("name"):
                        fields[str(item["name"])] = item.get("value")
        return _node(node_id, "edit_fields", {"fields": fields, "keep_only_set": bool(params.get("keepOnlySet"))}, index), MigrationFinding(source_name, source_type, "transformed", "edit_fields", "Set fields were converted; expression semantics require review.")
    if source_type.endswith(".slack"):
        mapped = {
            "resource": "message",
            "operation": "send",
            "credentials": "",
            "channel": params.get("channel") or "",
            "text": params.get("text") or params.get("message") or "",
            "blocks": None,
            "thread_ts": "",
        }
        return _node(node_id, "slack", mapped, index), MigrationFinding(source_name, source_type, "transformed", "slack", "Slack message fields were retained; reconnect credentials before publishing.")
    if source_type.endswith(".scheduleTrigger") or source_type.endswith(".cron"):
        return _node(node_id, "schedule_trigger", {"every": 1, "interval": "hours", "cron": "", "timezone": "UTC"}, index), MigrationFinding(source_name, source_type, "manual", "schedule_trigger", "A safe hourly placeholder was created; reproduce and verify the original schedule.")
    if source_type.endswith(".code") or source_type.endswith(".function"):
        return None, MigrationFinding(source_name, source_type, "manual", None, "JavaScript code is not executed or translated. Reimplement it as reviewed Python.")
    return None, MigrationFinding(source_name, source_type, "unsupported", None, "No production-safe mapping is available for this node type.")


def _import_n8n(source: str, allow_partial: bool) -> MigrationResult:
    try:
        document = json.loads(source)
    except json.JSONDecodeError as exc:
        raise ImportError(f"n8n source is not valid JSON: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("nodes"), list):
        raise ImportError("n8n source must contain a nodes array")
    findings: list[MigrationFinding] = []
    nodes: list[dict[str, Any]] = []
    names: dict[str, str] = {}
    for index, raw in enumerate(document["nodes"]):
        if not isinstance(raw, dict):
            continue
        node_id = _safe_id(str(raw.get("id") or raw.get("name") or f"node_{index + 1}"), f"node_{index + 1}")
        mapped, finding = _n8n_mapping(raw, node_id, index)
        findings.append(finding)
        if mapped is not None:
            nodes.append(mapped)
            names[str(raw.get("name") or node_id)] = node_id

    edges: list[dict[str, Any]] = []
    connections = document.get("connections")
    if isinstance(connections, dict):
        for source_name, outputs in connections.items():
            source_id = names.get(str(source_name))
            if not source_id or not isinstance(outputs, dict):
                continue
            for output_groups in outputs.values():
                if not isinstance(output_groups, list):
                    continue
                for group in output_groups:
                    if not isinstance(group, list):
                        continue
                    for target in group:
                        if not isinstance(target, dict):
                            continue
                        target_id = names.get(str(target.get("node") or ""))
                        if not target_id:
                            continue
                        edges.append({
                            "id": f"e_{len(edges) + 1}",
                            "source": source_id,
                            "source_output": "main",
                            "target": target_id,
                            "target_input": "input",
                        })

    blocking = any(finding.status in {"manual", "unsupported"} for finding in findings)
    graph: dict[str, Any] | None = {"nodes": nodes, "edges": edges} if nodes else None
    if graph is not None:
        try:
            graph = WorkflowGraph.model_validate(graph).model_dump(mode="json")
        except Exception as exc:
            raise ImportError(f"Converted n8n graph is invalid: {exc}") from exc
    return MigrationResult("n8n", bool(graph) and (allow_partial or not blocking), blocking, graph, _summary(findings), findings)


def _import_python_script(source: str, allow_partial: bool) -> MigrationResult:
    """Wrap a standalone Python script in an inspectable Code node.

    The source is parsed but never executed during analysis. Imports are
    surfaced as manual environment prerequisites because the importer cannot
    prove that a target environment contains the requested packages.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ImportError(f"Python source has a syntax error: {exc}") from exc

    imports = sorted(
        {
            node.names[0].name.split(".")[0]
            if isinstance(node, ast.Import)
            else (node.module or "").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and (not isinstance(node, ast.ImportFrom) or node.module)
        }
    )
    findings = [
        MigrationFinding(
            "script",
            "python",
            "transformed",
            "code",
            "The script is preserved in a Code node and receives upstream data as `input`.",
        )
    ]
    if imports:
        findings.append(
            MigrationFinding(
                "imports",
                ", ".join(imports),
                "manual",
                None,
                "Review these imports and add third-party packages to the workflow environment before running.",
            )
        )

    script = source.rstrip() + "\n\n# Nodyra migration adapter\noutput = locals().get('output')\n"
    graph = {
        "nodes": [
            _node("manual", "manual_trigger", {"data": {}}, 0),
            _node("python_script", "code", {"code": script}, 1),
        ],
        "edges": [
            {
                "id": "e_1",
                "source": "manual",
                "source_output": "main",
                "target": "python_script",
                "target_input": "input",
            }
        ],
    }
    graph = WorkflowGraph.model_validate(graph).model_dump(mode="json")
    blocking = bool(imports)
    return MigrationResult(
        "python_script",
        allow_partial or not blocking,
        blocking,
        graph,
        _summary(findings),
        findings,
    )


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    return next((item.value for item in call.keywords if item.arg == name), None)


def _literal_string(node: ast.AST | None, default: str = "") -> str:
    return str(node.value) if isinstance(node, ast.Constant) and isinstance(node.value, str) else default


def _runtime_import_source(source: str, tree: ast.Module, excluded: set[str]) -> tuple[str, list[str]]:
    snippets: list[str] = []
    packages: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Import):
            aliases = [
                alias
                for alias in node.names
                if alias.name.split(".")[0] not in excluded
            ]
            if not aliases:
                continue
            packages.extend(alias.name.split(".")[0] for alias in aliases)
            segment = ast.unparse(ast.Import(names=aliases))
        else:
            package = (node.module or "").split(".")[0]
            if not package or package in excluded:
                continue
            packages.append(package)
            segment = ast.get_source_segment(source, node) or ast.unparse(node)
        snippets.append(segment)
    return "\n".join(snippets), sorted(set(packages))


def _function_code(function: ast.FunctionDef, imports: str) -> tuple[str | None, str | None]:
    positional = [*function.args.posonlyargs, *function.args.args]
    if (
        len(positional) > 1
        or function.args.kwonlyargs
        or function.args.vararg
        or function.args.kwarg
    ):
        return None, "Only zero-argument or single-input Python callables can be converted automatically."
    clean = copy.deepcopy(function)
    clean.decorator_list = []
    invocation = "input" if positional else ""
    parts = [imports, ast.unparse(clean), f"output = {function.name}({invocation})"]
    return "\n\n".join(part for part in parts if part), None


def _dependency_edges(nodes: list[dict[str, Any]], dependencies: set[tuple[str, str]]) -> list[dict[str, Any]]:
    node_ids = {str(node["id"]) for node in nodes}
    edges: list[dict[str, Any]] = []
    targets = {target for source, target in dependencies if source in node_ids and target in node_ids}
    for source, target in sorted(dependencies):
        if source not in node_ids or target not in node_ids or source == target:
            continue
        edges.append({
            "id": f"e_{len(edges) + 1}",
            "source": source,
            "source_output": "main",
            "target": target,
            "target_input": "input",
        })
    for node in nodes:
        node_id = str(node["id"])
        if node_id == "manual" or node_id in targets:
            continue
        edges.append({
            "id": f"e_{len(edges) + 1}",
            "source": "manual",
            "source_output": "main",
            "target": node_id,
            "target_input": "input",
        })
    return edges


def _airflow_chain(node: ast.AST, aliases: dict[str, str]) -> list[str]:
    if isinstance(node, ast.Name) and node.id in aliases:
        return [aliases[node.id]]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.RShift):
        return _airflow_chain(node.left, aliases) + _airflow_chain(node.right, aliases)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.LShift):
        return _airflow_chain(node.right, aliases) + _airflow_chain(node.left, aliases)
    return []


def _import_airflow(source: str, allow_partial: bool) -> MigrationResult:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ImportError(f"Airflow source has a syntax error: {exc}") from exc
    imports, packages = _runtime_import_source(source, tree, {"airflow"})
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    findings: list[MigrationFinding] = []
    nodes = [_node("manual", "manual_trigger", {"data": {}}, 0)]
    aliases: dict[str, str] = {}
    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            continue
        if not isinstance(statement.value, ast.Call):
            continue
        operator = _call_name(statement.value)
        if not operator.endswith("Operator"):
            continue
        alias = statement.targets[0].id
        task_id = _literal_string(_keyword(statement.value, "task_id"), alias)
        node_id = _safe_id(alias, f"task_{len(nodes)}")
        aliases[alias] = node_id
        if operator == "PythonOperator":
            callable_node = _keyword(statement.value, "python_callable")
            function = functions.get(callable_node.id) if isinstance(callable_node, ast.Name) else None
            if function is None:
                findings.append(MigrationFinding(task_id, operator, "unsupported", None, "python_callable must reference a top-level function."))
                continue
            code, reason = _function_code(function, imports)
            if code is None:
                findings.append(MigrationFinding(task_id, operator, "manual", None, reason or "Callable requires review."))
                continue
            nodes.append(_node(node_id, "code", {"code": code}, len(nodes)))
            findings.append(MigrationFinding(task_id, operator, "transformed", "code", "Top-level Python callable was converted to an inspectable Code node."))
        elif operator == "BashOperator":
            command = _literal_string(_keyword(statement.value, "bash_command"))
            if not command:
                findings.append(MigrationFinding(task_id, operator, "unsupported", None, "Dynamic bash_command values cannot be converted safely."))
                continue
            nodes.append(_node(node_id, "execute_command", {"command": command, "shell": "auto", "cwd": "", "env_vars": {}, "timeout_seconds": 60, "fail_on_nonzero": True}, len(nodes)))
            findings.append(MigrationFinding(task_id, operator, "manual", "execute_command", "Command was retained; shell, environment, secrets, timeout, and platform behavior require review."))
        elif operator in {"EmptyOperator", "DummyOperator"}:
            nodes.append(_node(node_id, "no_op", {}, len(nodes)))
            findings.append(MigrationFinding(task_id, operator, "exact", "no_op", "Empty task maps to No Operation."))
        else:
            findings.append(MigrationFinding(task_id, operator, "unsupported", None, "No production-safe mapping is available for this operator."))

    dependencies: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, (ast.RShift, ast.LShift)):
            continue
        chain = _airflow_chain(node, aliases)
        dependencies.update(zip(chain, chain[1:], strict=False))
    findings.append(MigrationFinding("dag_activation", "DAG", "manual", "manual_trigger", "DAG schedule, catchup, timezone, concurrency, retries, and backfill policy require an explicit Nodyra deployment review."))
    if packages:
        findings.append(MigrationFinding("imports", ", ".join(packages), "manual", None, "Add and review imported packages in the target workflow environment."))
    if len(nodes) == 1:
        findings.append(MigrationFinding("source", "airflow", "unsupported", None, "No supported Airflow operator assignments were found."))
        return MigrationResult("airflow", False, False, None, _summary(findings), findings)
    edges = _dependency_edges(nodes, dependencies)
    graph = WorkflowGraph.model_validate({"nodes": nodes, "edges": edges}).model_dump(mode="json")
    blocking = any(item.status in {"manual", "unsupported"} for item in findings)
    return MigrationResult("airflow", allow_partial or not blocking, blocking, graph, _summary(findings), findings)


def _decorated(function: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    return any(_call_name(decorator) == name for decorator in function.decorator_list)


def _import_prefect(source: str, allow_partial: bool) -> MigrationResult:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ImportError(f"Prefect source has a syntax error: {exc}") from exc
    imports, packages = _runtime_import_source(source, tree, {"prefect"})
    tasks = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and _decorated(node, "task")
    }
    async_tasks = {
        node.name
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and _decorated(node, "task")
    }
    flow = next(
        (node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _decorated(node, "flow")),
        None,
    )
    findings: list[MigrationFinding] = []
    nodes = [_node("manual", "manual_trigger", {"data": {}}, 0)]
    aliases: dict[str, str] = {}
    dependencies: set[tuple[str, str]] = set()
    body = flow.body if flow is not None else []
    for statement in body:
        if not isinstance(statement, (ast.Assign, ast.Expr)):
            continue
        call = statement.value
        if not isinstance(call, ast.Call):
            continue
        task_name = _call_name(call)
        if task_name in async_tasks:
            findings.append(MigrationFinding(task_name, "Prefect async task", "unsupported", None, "Async Prefect tasks require an explicit async node mapping."))
            continue
        function = tasks.get(task_name)
        if function is None:
            continue
        alias = statement.targets[0].id if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name) else f"{task_name}_{len(nodes)}"
        node_id = _safe_id(alias, f"task_{len(nodes)}")
        code, reason = _function_code(function, imports)
        if code is None:
            findings.append(MigrationFinding(alias, "Prefect task", "manual", None, reason or "Task requires review."))
            continue
        nodes.append(_node(node_id, "code", {"code": code}, len(nodes)))
        aliases[alias] = node_id
        findings.append(MigrationFinding(alias, "Prefect task", "transformed", "code", "Task function was converted to an inspectable Code node."))
        for argument in call.args:
            if isinstance(argument, ast.Name) and argument.id in aliases:
                dependencies.add((aliases[argument.id], node_id))

    findings.append(MigrationFinding("flow_activation", "Prefect flow", "manual", "manual_trigger", "Flow schedules, deployments, retries, caching, task mapping, and state hooks require review."))
    if packages:
        findings.append(MigrationFinding("imports", ", ".join(packages), "manual", None, "Add and review imported packages in the target workflow environment."))
    if len(nodes) == 1:
        findings.append(MigrationFinding("source", "prefect", "unsupported", None, "No supported synchronous @task calls inside a @flow were found."))
        return MigrationResult("prefect", False, False, None, _summary(findings), findings)
    edges = _dependency_edges(nodes, dependencies)
    graph = WorkflowGraph.model_validate({"nodes": nodes, "edges": edges}).model_dump(mode="json")
    blocking = any(item.status in {"manual", "unsupported"} for item in findings)
    return MigrationResult("prefect", allow_partial or not blocking, blocking, graph, _summary(findings), findings)


def analyze_migration(
    source: str,
    source_format: MigrationFormat,
    *,
    allow_partial: bool = False,
) -> MigrationResult:
    """Analyze first; only return importable graphs for semantics we can defend."""
    if source_format == "nodyra_module":
        graph = import_module(source).model_dump(mode="json")
        finding = MigrationFinding("workflow", "nodyra_module", "exact", "WorkflowGraph", "Nodyra module export reconstructed without executing source code.")
        return MigrationResult(source_format, True, False, graph, _summary([finding]), [finding])
    if source_format == "n8n":
        return _import_n8n(source, allow_partial)
    if source_format == "python_script":
        return _import_python_script(source, allow_partial)
    if source_format == "airflow":
        return _import_airflow(source, allow_partial)
    if source_format == "prefect":
        return _import_prefect(source, allow_partial)
    raise ImportError(f"Unsupported migration format: {source_format}")
