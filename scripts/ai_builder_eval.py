"""Local quality harness for the AI workflow builder.

Default mode is deterministic and does not call external LLMs. It scores the
same graph-building surface the product falls back to when no planner model is
configured, so the command is suitable for local development and advisory CI:

    python scripts/ai_builder_eval.py --n 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import nodyra_nodes  # noqa: E402,F401  # registers bundled node manifests
from app.db import SessionLocal  # noqa: E402
from app.schemas import AiWorkflowDraftRequest  # noqa: E402
from app.services.ai_builder import (  # noqa: E402
    _fallback_draft,
    allowed_node_types,
    build_workflow_draft,
)
from nodyra.models import WorkflowGraph  # noqa: E402

_SECRETISH = re.compile(
    r"(?i)(sk-[a-z0-9_-]{12,}|xox[baprs]-[a-z0-9-]{10,}|api[_-]?key\s*[:=])"
)


@dataclass(frozen=True)
class EvalCase:
    name: str
    prompt: str
    expected_types: tuple[str, ...]
    min_nodes: int = 2


@dataclass
class EvalResult:
    case: EvalCase
    passed: bool
    score: float
    planner: str
    node_types: list[str]
    failures: list[str]


CASES: tuple[EvalCase, ...] = (
    EvalCase(
        "webhook_openai_slack",
        "When a GitHub issue is opened, summarize it with OpenAI and post to Slack.",
        ("webhook_trigger", "ai_chat", "slack"),
        3,
    ),
    EvalCase(
        "scheduled_http_branch",
        "Every morning fetch an HTTP API, route if the status is ok, and clean the payload.",
        ("schedule_trigger", "http_request", "switch", "code"),
        4,
    ),
    EvalCase(
        "manual_python_transform",
        "Build a manual workflow that parses incoming JSON with Python custom code.",
        ("manual_trigger", "code"),
        2,
    ),
    EvalCase(
        "webhook_email_alert",
        "When a webhook arrives, send an email alert through SMTP.",
        ("webhook_trigger", "smtp_send_email"),
        2,
    ),
    EvalCase(
        "github_issue",
        "Create a GitHub issue when a webhook arrives.",
        ("webhook_trigger", "github_create_issue_v2"),
        2,
    ),
    EvalCase(
        "scheduled_summary_email",
        "Daily summarize an API response with OpenAI and email the result.",
        ("schedule_trigger", "http_request", "ai_chat", "smtp_send_email"),
        4,
    ),
    EvalCase(
        "generic_shape",
        "Accept manual input and shape the data into a message field.",
        ("manual_trigger", "edit_fields"),
        2,
    ),
    EvalCase(
        "slack_only",
        "When an event arrives, post the payload to Slack.",
        ("webhook_trigger", "slack"),
        2,
    ),
    EvalCase(
        "conditional_transform",
        "Receive a webhook, branch on a condition, then parse the payload with Python.",
        ("webhook_trigger", "switch", "code"),
        3,
    ),
    EvalCase(
        "anthropic_summary",
        "When a webhook arrives, summarize it with Claude.",
        ("webhook_trigger", "ai_chat"),
        2,
    ),
)


def _graph_dict(graph: WorkflowGraph | dict[str, Any]) -> dict[str, Any]:
    if isinstance(graph, WorkflowGraph):
        return graph.model_dump()
    return graph


def _iter_values(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_values(item)
    else:
        yield value


def _score_graph(case: EvalCase, graph: WorkflowGraph | dict[str, Any], planner: str) -> EvalResult:
    failures: list[str] = []
    graph_data = _graph_dict(graph)
    try:
        validated = WorkflowGraph.model_validate(graph_data)
    except Exception as exc:  # noqa: BLE001
        return EvalResult(case, False, 0.0, planner, [], [f"invalid graph: {exc}"])

    nodes = validated.nodes
    edges = validated.edges
    node_types = [node.type for node in nodes]
    node_ids = {node.id for node in nodes}
    allowed = allowed_node_types()

    if len(nodes) < case.min_nodes:
        failures.append(f"expected at least {case.min_nodes} nodes, got {len(nodes)}")
    for expected in case.expected_types:
        if expected not in node_types:
            failures.append(f"missing expected node type {expected}")
    unknown = sorted({node.type for node in nodes if node.type not in allowed})
    if unknown:
        failures.append(f"unknown node types: {', '.join(unknown)}")
    dangling = [
        edge.id
        for edge in edges
        if edge.source not in node_ids or edge.target not in node_ids
    ]
    if dangling:
        failures.append(f"dangling edges: {', '.join(dangling)}")
    if not any(node.type.endswith("_trigger") for node in nodes):
        failures.append("missing trigger node")
    for value in _iter_values(graph_data):
        if isinstance(value, str) and _SECRETISH.search(value):
            failures.append("graph contains secret-like text")
            break

    checks = len(case.expected_types) + 5
    score = max(0.0, (checks - len(failures)) / checks)
    return EvalResult(case, not failures, score, planner, node_types, failures)


async def _build_graph(case: EvalCase, planner: str) -> tuple[WorkflowGraph, str]:
    if planner == "service":
        async with SessionLocal() as session:
            response = await build_workflow_draft(
                session,
                "ai-builder-eval",
                AiWorkflowDraftRequest(prompt=case.prompt),
            )
        return response.graph, response.planner

    result = _fallback_draft(case.prompt)
    return result.graph, result.planner


async def evaluate_cases(n: int, planner: str) -> list[EvalResult]:
    selected = [CASES[index % len(CASES)] for index in range(max(1, n))]
    results: list[EvalResult] = []
    for case in selected:
        graph, planner_name = await _build_graph(case, planner)
        results.append(_score_graph(case, graph, planner_name))
    return results


def _print_text(results: list[EvalResult]) -> None:
    passed = sum(1 for result in results if result.passed)
    rate = passed / len(results)
    print(f"AI builder eval: {passed}/{len(results)} passed ({rate:.1%})")
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        nodes = ", ".join(result.node_types)
        print(f"{status} {result.case.name} score={result.score:.2f} planner={result.planner} nodes=[{nodes}]")
        for failure in result.failures:
            print(f"  - {failure}")


def _print_json(results: list[EvalResult]) -> None:
    passed = sum(1 for result in results if result.passed)
    payload = {
        "passed": passed,
        "total": len(results),
        "pass_rate": passed / len(results),
        "results": [
            {
                "case": result.case.name,
                "passed": result.passed,
                "score": result.score,
                "planner": result.planner,
                "node_types": result.node_types,
                "failures": result.failures,
            }
            for result in results
        ],
    }
    print(json.dumps(payload, indent=2))


async def async_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate AI builder graph quality.")
    parser.add_argument("--n", type=int, default=len(CASES), help="number of cases to run")
    parser.add_argument(
        "--planner",
        choices=("fallback", "service"),
        default="fallback",
        help="fallback is deterministic; service uses build_workflow_draft",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.8,
        help="minimum pass rate required for exit code 0",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable output")
    args = parser.parse_args(argv)

    results = await evaluate_cases(args.n, args.planner)
    if args.json:
        _print_json(results)
    else:
        _print_text(results)
    pass_rate = sum(1 for result in results if result.passed) / len(results)
    return 0 if pass_rate >= args.threshold else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
