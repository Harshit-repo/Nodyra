"""Sustained-load endurance test with resource-drift assertions.

``soak_test.py`` is a chaos test: it storms a fixed number of runs while killing
workers and interrupting Redis and Postgres, and proves the system *recovers*.
It runs 50 runs and finishes in minutes.

That is a different question from whether the system stays healthy for a week.
A file descriptor leaked once per run, or a cache that never evicts, is
invisible in a 50-run burst and OOM-kills a pod on day three with nothing in the
metrics to explain it. This holds a steady load for a chosen duration and
watches the resource gauges for drift.

The analysis is deliberately separated from the driving so it can be unit-tested
without a live stack — see ``tests/test_endurance_analysis.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# Gauges sampled from /metrics. Each is a resource whose unbounded growth is a
# leak rather than load: they should track concurrency, not elapsed time.
TRACKED_GAUGES = (
    "nodyra_process_resident_bytes",
    "nodyra_process_open_fds",
    "nodyra_process_threads",
)

# A leak has to clear both bars to be reported: a relative rise (so a small
# absolute wobble on a tiny process is not flagged) and an absolute one (so a
# noisy but harmless gauge on a large process is not either).
DEFAULT_GROWTH_RATIO = 1.5
MIN_ABSOLUTE_GROWTH = {
    "nodyra_process_resident_bytes": 64 * 1024 * 1024,  # 64 MiB
    "nodyra_process_open_fds": 128.0,
    "nodyra_process_threads": 32.0,
}


@dataclass
class Sample:
    at: float
    gauges: dict[str, float] = field(default_factory=dict)


def parse_metrics(text: str) -> dict[str, float]:
    """Pull the tracked gauges out of a Prometheus text exposition."""
    values: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, rest = line.partition(" ")
        # Strip any label set; these gauges are unlabelled.
        base = name.split("{", 1)[0]
        if base in TRACKED_GAUGES:
            try:
                values[base] = float(rest.strip())
            except ValueError:
                continue
    return values


def _trend(values: Sequence[float]) -> dict[str, float]:
    """Compare the first and last thirds, ignoring the middle.

    Start-up allocation and JIT warm-up all land in the first samples, so a
    simple first-vs-last comparison flags healthy processes. Comparing the
    medians of the outer thirds is robust to both that and to single spikes.
    """
    if len(values) < 6:
        return {"baseline": float(values[0]) if values else 0.0,
                "final": float(values[-1]) if values else 0.0}
    third = max(2, len(values) // 3)
    return {
        "baseline": statistics.median(values[:third]),
        "final": statistics.median(values[-third:]),
    }


def analyse_samples(
    samples: Sequence[Sample], *, growth_ratio: float = DEFAULT_GROWTH_RATIO
) -> dict[str, Any]:
    """Report per-gauge drift and the leaks that cleared both thresholds."""
    if len(samples) < 2:
        return {"gauges": {}, "leaks": ["not enough samples to judge drift"]}

    gauges: dict[str, dict[str, float]] = {}
    leaks: list[str] = []
    for name in TRACKED_GAUGES:
        series = [s.gauges[name] for s in samples if name in s.gauges]
        if len(series) < 2:
            continue
        trend = _trend(series)
        baseline, final = trend["baseline"], trend["final"]
        grew_by = final - baseline
        ratio = (final / baseline) if baseline > 0 else 0.0
        still_climbing = _still_climbing(series)
        gauges[name] = {
            "baseline": round(baseline, 2),
            "final": round(final, 2),
            "peak": round(max(series), 2),
            "growth": round(grew_by, 2),
            "growth_ratio": round(ratio, 3),
            "samples": len(series),
            "still_climbing": still_climbing,
            # The raw series, so a verdict can be checked rather than trusted.
            "series": [round(v, 2) for v in series],
        }
        # Three conditions, not two. Growth alone is not a leak: a warm worker
        # pool fills over minutes under load and then sits at its ceiling,
        # which looks identical to a leak if you only compare the start and the
        # end. A leak keeps climbing; a pool plateaus. Requiring the series to
        # still be rising at the end is what separates them, and it is the
        # check that stopped this harness reporting Nodyra's own warm pool as a
        # descriptor leak on its first real run.
        if (
            ratio >= growth_ratio
            and grew_by >= MIN_ABSOLUTE_GROWTH.get(name, 0.0)
            and still_climbing
        ):
            leaks.append(
                f"{name} grew {grew_by:,.0f} ({ratio:.2f}x) and was still rising "
                "at the end of the run — that tracks elapsed time rather than "
                "load, which is a leak"
            )
    return {"gauges": gauges, "leaks": leaks}


def _still_climbing(values: Sequence[float]) -> bool:
    """Whether the series is higher at the very end than in its own late-middle.

    A resource that has plateaued — a pool at its ceiling — is flat or falling
    across its final stretch even though it is far above where it started.
    Something genuinely leaking is still going up.
    """
    if len(values) < 9:
        # Too short to distinguish a plateau from a climb; fall back to the
        # endpoint comparison alone rather than silently clearing a real leak.
        return True
    tail = max(3, len(values) // 4)
    late_middle = statistics.median(values[-2 * tail : -tail])
    end = statistics.median(values[-tail:])
    return end > late_middle


# ── Driving ────────────────────────────────────────────────────────────────


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument(
        "--duration-minutes",
        type=float,
        default=60.0,
        help="how long to hold load; a leak needs hours to be visible",
    )
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument(
        "--sample-interval-seconds",
        type=float,
        default=30.0,
        help="how often to scrape /metrics",
    )
    ap.add_argument(
        "--growth-ratio",
        type=float,
        default=DEFAULT_GROWTH_RATIO,
        help="fail when a gauge's final third exceeds its first third by this factor",
    )
    ap.add_argument("--report-json", default="endurance-evidence.json")
    return ap.parse_args(argv)


def _headers() -> dict[str, str]:
    token = os.environ.get("NODYRA_API_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


async def _make_workflow(client: httpx.AsyncClient) -> str:
    created = await client.post("/workflows", json={"name": "endurance"})
    created.raise_for_status()
    workflow_id = created.json()["id"]
    graph = {
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {}},
            {
                "id": "work",
                "type": "code",
                "params": {"code": "output = {'n': sum(range(1000))}"},
            },
        ],
        "edges": [{"source": "t", "target": "work"}],
    }
    updated = await client.put(f"/workflows/{workflow_id}", json={"graph": graph})
    updated.raise_for_status()
    return workflow_id


async def _drive(client: httpx.AsyncClient, workflow_id: str, stop: asyncio.Event) -> int:
    completed = 0
    while not stop.is_set():
        try:
            response = await client.post(f"/workflows/{workflow_id}/run", json={})
            if response.status_code < 400:
                completed += 1
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.05)
    return completed


async def _sample(
    client: httpx.AsyncClient, stop: asyncio.Event, interval: float
) -> list[Sample]:
    samples: list[Sample] = []
    while not stop.is_set():
        try:
            response = await client.get("/metrics")
            if response.status_code < 400:
                samples.append(Sample(at=time.time(), gauges=parse_metrics(response.text)))
        except httpx.HTTPError:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
    return samples


async def main() -> int:  # pragma: no cover - needs a live stack
    args = parse_args()
    stop = asyncio.Event()

    async with httpx.AsyncClient(
        base_url=args.base_url, headers=_headers(), timeout=30.0
    ) as client:
        workflow_id = await _make_workflow(client)
        print(
            f"holding {args.concurrency} concurrent drivers against {workflow_id} "
            f"for {args.duration_minutes:g} minutes"
        )

        sampler = asyncio.create_task(_sample(client, stop, args.sample_interval_seconds))
        drivers = [
            asyncio.create_task(_drive(client, workflow_id, stop))
            for _ in range(max(1, args.concurrency))
        ]

        await asyncio.sleep(args.duration_minutes * 60)
        stop.set()

        runs = sum(await asyncio.gather(*drivers))
        samples = await sampler

    analysis = analyse_samples(samples, growth_ratio=args.growth_ratio)
    report = {
        "schema_version": 1,
        "generated_at_epoch": int(time.time()),
        "duration_minutes": args.duration_minutes,
        "concurrency": args.concurrency,
        "runs_started": runs,
        "samples": len(samples),
        **analysis,
    }
    Path(args.report_json).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))

    if analysis["leaks"]:
        for leak in analysis["leaks"]:
            print(f"LEAK: {leak}")
        return 1
    print(f"no resource drift across {runs} runs and {len(samples)} samples")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(asyncio.run(main()))
