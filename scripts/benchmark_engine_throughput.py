"""Measure end-to-end engine execution throughput against CI budgets.

``benchmark_engine_planning.py`` measures how fast the engine *plans* a graph —
topological order, loop ownership. That is the cheap half. This measures the
half users actually wait on: nodes executed per second once a run starts, and
how well the engine overlaps work that could run in parallel.

The two answer different questions, and the planning benchmark passing 100x
under budget says nothing about whether execution regressed. A change that
serialises awaits, adds a per-node round-trip, or holds the event loop would
sail through planning and be caught here.

Node bodies are deliberately trivial: this measures engine overhead per node,
not the speed of anyone's Python. The async cases sleep, so a serialised
scheduler shows up as wall-clock rather than as CPU.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nodyra.engine.scheduler import execute
from nodyra.models import Edge, GraphNode, WorkflowGraph
from nodyra.sdk import NodeRegistry, node

# Each node sleeps this long in the concurrency cases. Long enough that
# serialisation is unmistakable against timer noise, short enough that a
# correctly-overlapping engine finishes the whole case in roughly one sleep.
CONCURRENT_SLEEP_SECONDS = 0.05


def _registry() -> NodeRegistry:
    """A registry of minimal nodes, isolated from the shipped node library."""
    registry = NodeRegistry()

    @node(id="bench_sync", name="Bench Sync", category="Benchmark", registry=registry)
    def bench_sync(input: Any = None) -> dict:
        """Return a small payload with no work of its own."""
        return {"n": (input or {}).get("n", 0) + 1 if isinstance(input, dict) else 1}

    @node(id="bench_async", name="Bench Async", category="Benchmark", registry=registry)
    async def bench_async(input: Any = None) -> dict:
        """Await briefly so a serialised scheduler shows up as wall-clock."""
        await asyncio.sleep(CONCURRENT_SLEEP_SECONDS)
        return {"n": (input or {}).get("n", 0) + 1 if isinstance(input, dict) else 1}

    return registry


def _chain(size: int, node_type: str = "bench_sync") -> WorkflowGraph:
    """A strictly sequential graph: the engine cannot overlap anything."""
    nodes = [GraphNode(id=f"n{i}", type=node_type) for i in range(size)]
    edges = [Edge(source=f"n{i}", target=f"n{i + 1}") for i in range(size - 1)]
    return WorkflowGraph(nodes=nodes, edges=edges)


def _independent(size: int, node_type: str = "bench_sync") -> WorkflowGraph:
    """Fully parallel graph: every node is eligible from the first tick."""
    return WorkflowGraph(
        nodes=[GraphNode(id=f"n{i}", type=node_type) for i in range(size)]
    )


def _fan_out_in(width: int) -> WorkflowGraph:
    """The common real shape: one source, a parallel middle, one sink."""
    nodes = [GraphNode(id="source", type="bench_sync")]
    nodes += [GraphNode(id=f"mid{i}", type="bench_sync") for i in range(width)]
    nodes.append(GraphNode(id="sink", type="bench_sync"))
    edges = [Edge(source="source", target=f"mid{i}") for i in range(width)]
    edges += [Edge(source=f"mid{i}", target="sink") for i in range(width)]
    return WorkflowGraph(nodes=nodes, edges=edges)


async def _run(graph: WorkflowGraph, registry: NodeRegistry) -> int:
    result = await execute(graph, registry)
    if result.status.value not in ("success",):
        raise RuntimeError(f"benchmark graph did not succeed: {result.status}")
    return len(result.nodes)


def _median_seconds(operation: Callable[[], Any], *, samples: int = 5) -> float:
    operation()  # warm imports and caches outside the recorded samples
    durations = []
    for _ in range(samples):
        started = time.perf_counter()
        operation()
        durations.append(time.perf_counter() - started)
    return statistics.median(durations)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("engine-throughput-benchmark.json")
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

    registry = _registry()

    # (name, graph, node count, budget seconds, what a regression here means)
    cases: list[tuple[str, WorkflowGraph, int, float, str]] = [
        (
            "sequential_chain_500_nodes",
            _chain(500),
            500,
            5.0,
            "per-node engine overhead on the critical path",
        ),
        (
            "independent_1000_nodes",
            _independent(1000),
            1000,
            8.0,
            "scheduler dispatch cost when everything is eligible at once",
        ),
        (
            "fan_out_in_500_wide",
            _fan_out_in(500),
            502,
            6.0,
            "edge bookkeeping on the most common real graph shape",
        ),
        (
            # 100 nodes that each await 50ms. Overlapped: ~0.05s plus overhead.
            # Serialised: 5s. The budget sits far below the serialised figure so
            # losing concurrency is caught rather than merely slowing the run.
            "concurrency_100_awaiting_nodes",
            _independent(100, "bench_async"),
            100,
            2.0,
            "loss of node-level concurrency (serialised: ~5s)",
        ),
    ]

    results: list[dict[str, Any]] = []
    for name, graph, expected_nodes, base_budget, meaning in cases:
        budget = base_budget * args.budget_multiplier

        def _once(graph: WorkflowGraph = graph) -> int:
            return asyncio.run(_run(graph, registry))

        executed = _once()
        if executed != expected_nodes:
            raise SystemExit(
                f"{name}: executed {executed} nodes, expected {expected_nodes}"
            )
        duration = _median_seconds(_once)
        results.append(
            {
                "name": name,
                "nodes": expected_nodes,
                "median_seconds": round(duration, 6),
                "nodes_per_second": round(expected_nodes / duration, 1) if duration else None,
                "budget_seconds": budget,
                "passed": duration <= budget,
                "regression_means": meaning,
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
            "## Engine throughput benchmark",
            "",
            "| Case | Nodes | Median | Nodes/s | Budget | Result |",
            "|---|---:|---:|---:|---:|---|",
        ]
        for result in results:
            lines.append(
                f"| {result['name']} | {result['nodes']} | "
                f"{result['median_seconds']:.3f}s | {result['nodes_per_second']} | "
                f"{result['budget_seconds']:.1f}s | "
                f"{'pass' if result['passed'] else 'FAIL'} |"
            )
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    print(json.dumps(payload, separators=(",", ":")))
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
