"""Measure deterministic engine-planning hot paths against generous CI budgets."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nodyra.engine.loops import LoopRegion
from nodyra.engine.scheduler import _loop_owner_index, _owner_unit, _topo_order
from nodyra.models import Edge, GraphNode, WorkflowGraph


def _wide_graph(size: int = 10_000) -> WorkflowGraph:
    return WorkflowGraph(
        nodes=[GraphNode(id=f"node-{index}", type="benchmark") for index in range(size)]
    )


def _linear_graph(size: int = 10_000) -> WorkflowGraph:
    nodes = [GraphNode(id=f"node-{index}", type="benchmark") for index in range(size)]
    edges = [
        Edge(source=f"node-{index}", target=f"node-{index + 1}")
        for index in range(size - 1)
    ]
    return WorkflowGraph(nodes=nodes, edges=edges)


def _fan_graph(width: int = 5_000) -> WorkflowGraph:
    nodes = [GraphNode(id="hub", type="benchmark")]
    nodes.extend(
        GraphNode(id=f"input-{index}", type="benchmark") for index in range(width)
    )
    nodes.extend(
        GraphNode(id=f"output-{index}", type="benchmark") for index in range(width)
    )
    edges = [
        Edge(source=f"input-{index}", target="hub") for index in range(width)
    ]
    edges.extend(
        Edge(source="hub", target=f"output-{index}") for index in range(width)
    )
    return WorkflowGraph(nodes=nodes, edges=edges)


def _nested_loop_ownership(depth: int = 200) -> None:
    regions: dict[str, LoopRegion] = {}
    for index in range(depth):
        nested_nodes = {"payload"}
        for nested in range(index + 1, depth):
            nested_nodes.add(f"loop-{nested}")
            nested_nodes.add(f"end-{nested}")
        regions[f"loop-{index}"] = LoopRegion(
            start_id=f"loop-{index}",
            end_id=f"end-{index}",
            body_ids=frozenset(nested_nodes),
            parent_start_id=f"loop-{index - 1}" if index else None,
        )
    owner_index = _loop_owner_index(regions)
    owner = _owner_unit("payload", owner_index, {"loop-0"})
    if owner != "loop-0":
        raise RuntimeError(f"nested-loop owner mismatch: {owner!r}")


def _median_seconds(operation: Callable[[], Any], *, samples: int = 5) -> float:
    operation()  # warm caches and imports outside the recorded samples
    durations: list[float] = []
    for _ in range(samples):
        started = time.perf_counter()
        operation()
        durations.append(time.perf_counter() - started)
    return statistics.median(durations)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("engine-planning-benchmark.json")
    )
    parser.add_argument(
        "--budget-multiplier",
        type=float,
        default=1.0,
        help="multiply every CI budget for slower supported runners",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.budget_multiplier <= 0:
        raise SystemExit("--budget-multiplier must be greater than zero")

    wide = _wide_graph()
    linear = _linear_graph()
    fan = _fan_graph()
    cases: list[tuple[str, Callable[[], Any], float]] = [
        ("wide_10k_topology", lambda: _topo_order(wide), 2.0),
        ("deep_10k_topology", lambda: _topo_order(linear), 3.0),
        ("fan_in_out_10k_topology", lambda: _topo_order(fan), 3.0),
        ("nested_loop_owner_index_depth_200", _nested_loop_ownership, 2.0),
    ]

    results: list[dict[str, Any]] = []
    for name, operation, base_budget in cases:
        budget = base_budget * args.budget_multiplier
        duration = _median_seconds(operation)
        results.append(
            {
                "name": name,
                "median_seconds": round(duration, 6),
                "budget_seconds": budget,
                "passed": duration <= budget,
            }
        )

    payload = {
        "schema_version": 1,
        "generated_at_epoch": int(time.time()),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        lines = [
            "## Engine planning benchmark",
            "",
            "| Case | Median | Budget | Result |",
            "|---|---:|---:|---|",
        ]
        for result in results:
            outcome = "pass" if result["passed"] else "FAIL"
            lines.append(
                f"| {result['name']} | {result['median_seconds']:.6f}s | "
                f"{result['budget_seconds']:.2f}s | {outcome} |"
            )
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write("\n".join(lines) + "\n")

    print(json.dumps(payload, separators=(",", ":")))
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
