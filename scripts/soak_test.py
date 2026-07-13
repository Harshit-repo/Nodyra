"""Durable-queue soak test: run storm + cancel storm + worker kill.

Usage (against the compose stack):
    docker compose -f deploy/docker-compose.yml up -d api worker
    NODYRA_TOKEN=... uv run python scripts/soak_test.py \
        --base-url http://localhost:8000 --runs 100 --cancel-ratio 0.2 \
        --kill-container nodyra-worker-1

Sandbox lane:
    EXECUTION_SANDBOX=required docker compose \
        -f deploy/docker-compose.yml -f deploy/docker-compose.sandbox.yml up -d api worker
    uv run python scripts/soak_test.py \
        --base-url http://localhost:8000 --runs 50 --cancel-ratio 0.2 \
        --kill-container nodyra-worker-1 --sandbox --expect-sandbox-mode required \
        --max-p95-seconds 240

Invariants asserted:
  I1  every started run reaches a terminal status (success/error/cancelled)
      within --settle-seconds of the storm ending;
  I2  no queue entry is left leased/running after settling;
  I3  the worker kill loses zero runs (they re-lease and finish);
  I4  when --sandbox is set, runs are submitted with execution_mode=sandboxed.
  PERF  when --max-p95-seconds > 0, run completion p95 stays below the threshold.
Exit 0 = all invariants hold; exit 1 = violations printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import httpx

TERMINAL = {"success", "error", "cancelled", "timed_out"}

WORKFLOW_GRAPH = {
    "nodes": [
        {
            "id": "t",
            "type": "manual_trigger",
            "params": {},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "c",
            "type": "code",
            "params": {"code": "import time\ntime.sleep(2)\noutput = {'ok': True}"},
            "position": {"x": 200, "y": 0},
        },
    ],
    "edges": [
        {
            "id": "e1",
            "source": "t",
            "source_output": "main",
            "target": "c",
            "target_input": "main",
        },
    ],
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--cancel-ratio", type=float, default=0.2)
    ap.add_argument("--kill-container", default="")
    ap.add_argument("--restart-api-container", default="")
    ap.add_argument("--interrupt-redis-container", default="")
    ap.add_argument("--interrupt-postgres-container", default="")
    ap.add_argument(
        "--outage-seconds",
        type=float,
        default=5.0,
        help="Seconds to pause Redis/Postgres during their fault injection.",
    )
    ap.add_argument(
        "--exercise-drain",
        action="store_true",
        help="Prove /ops/drain stops split-topology workers from leasing new runs.",
    )
    ap.add_argument(
        "--report-json",
        default="",
        help="Optional path for machine-readable fault timings and invariant evidence.",
    )
    ap.add_argument("--settle-seconds", type=float, default=180.0)
    ap.add_argument(
        "--ready-timeout",
        type=float,
        default=120.0,
        help="Seconds to wait for /health/live before starting the storm.",
    )
    ap.add_argument(
        "--sandbox",
        action="store_true",
        help="Submit the workflow and every run with sandboxed execution enabled.",
    )
    ap.add_argument(
        "--expect-sandbox-mode",
        choices=("off", "auto", "required"),
        default="",
        help=(
            "Fail before the storm unless /ops/sandbox reports this configured mode. "
            "Use 'required' in the sandbox compose/nightly lane."
        ),
    )
    ap.add_argument(
        "--max-p95-seconds",
        type=float,
        default=0.0,
        help=(
            "Optional p95 run-completion latency threshold. Values <= 0 only "
            "report latency without failing the soak."
        ),
    )
    return ap.parse_args(argv)


def _headers() -> dict[str, str]:
    token = os.environ.get("NODYRA_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def workflow_update_payload(*, sandbox: bool) -> dict:
    payload = {"graph": WORKFLOW_GRAPH}
    if sandbox:
        payload["execution_mode"] = "sandboxed"
    return payload


def run_request_body(*, sandbox: bool) -> dict:
    return {"sandbox": True} if sandbox else {}


def _parse_api_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def run_latency_seconds(run: dict) -> float | None:
    started_at = _parse_api_datetime(run.get("started_at"))
    finished_at = _parse_api_datetime(run.get("finished_at"))
    if started_at is None or finished_at is None:
        return None
    return max(0.0, (finished_at - started_at).total_seconds())


def latency_summary(latencies: Sequence[float]) -> dict[str, float | int]:
    if not latencies:
        return {"count": 0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "count": len(latencies),
        "p50": _percentile(latencies, 0.50) or 0.0,
        "p95": _percentile(latencies, 0.95) or 0.0,
        "max": max(latencies),
    }


async def make_workflow(client: httpx.AsyncClient, *, sandbox: bool) -> str:
    r = await client.post("/workflows", json={"name": f"soak-{int(time.time())}"})
    r.raise_for_status()
    wf_id = r.json()["id"]
    r = await client.put(f"/workflows/{wf_id}", json=workflow_update_payload(sandbox=sandbox))
    r.raise_for_status()
    return wf_id


async def start_run(client: httpx.AsyncClient, wf_id: str, *, sandbox: bool) -> str | None:
    last_error = "not attempted"
    for attempt in range(1, 6):
        try:
            r = await client.post(
                f"/workflows/{wf_id}/run", json=run_request_body(sandbox=sandbox)
            )
            if r.status_code < 400:
                body = r.json()
                return body["run_id"] if "run_id" in body else body.get("id")
            last_error = f"HTTP {r.status_code}: {r.text[:120]}"
            if r.status_code < 500:
                break
        except httpx.HTTPError as exc:
            last_error = str(exc)
        if attempt < 5:
            await asyncio.sleep(min(float(attempt), 3.0))
    print(f"  start rejected after retries: {last_error}")
    return None


async def wait_for_api(client: httpx.AsyncClient, timeout_seconds: float) -> None:
    await wait_for_endpoint(client, "/health/live", timeout_seconds)


async def wait_for_endpoint(
    client: httpx.AsyncClient, path: str, timeout_seconds: float
) -> float:
    started_at = time.monotonic()
    deadline = time.monotonic() + timeout_seconds
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            r = await client.get(path)
            if r.status_code == 200:
                return time.monotonic() - started_at
            last_error = f"{path} returned {r.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        await asyncio.sleep(2)
    raise RuntimeError(
        f"{path} did not become healthy within {timeout_seconds}s ({last_error})"
    )


def _docker(*args: str) -> None:
    subprocess.run(
        ["docker", *args],
        check=True,
        capture_output=True,
        text=True,
    )


async def restart_container(
    client: httpx.AsyncClient,
    container: str,
    *,
    wait_for_api_health: bool,
    ready_timeout: float,
) -> float:
    started_at = time.monotonic()
    await asyncio.to_thread(_docker, "restart", container)
    if wait_for_api_health:
        await wait_for_endpoint(client, "/health/ready", ready_timeout)
    return time.monotonic() - started_at


async def interrupt_dependency(
    client: httpx.AsyncClient,
    container: str,
    *,
    outage_seconds: float,
    ready_timeout: float,
) -> float:
    started_at = time.monotonic()
    await asyncio.to_thread(_docker, "pause", container)
    try:
        await asyncio.sleep(max(0.1, outage_seconds))
    finally:
        await asyncio.to_thread(_docker, "unpause", container)
    await wait_for_endpoint(client, "/health/ready", ready_timeout)
    return time.monotonic() - started_at


async def exercise_cluster_drain(
    client: httpx.AsyncClient,
    wf_id: str,
    *,
    sandbox: bool,
) -> tuple[str | None, str | None, float]:
    started_at = time.monotonic()
    response = await client.post("/ops/drain", json={"draining": True})
    response.raise_for_status()
    violation: str | None = None
    run_id: str | None = None
    try:
        # Let every worker observe the shared state before submitting the probe.
        await asyncio.sleep(2.0)
        run_id = await start_run(client, wf_id, sandbox=sandbox)
        if run_id is None:
            violation = "DRAIN violated: probe run could not be submitted"
        else:
            await asyncio.sleep(2.0)
            probe = await client.get(f"/runs/{run_id}")
            probe.raise_for_status()
            status = str(probe.json().get("status") or "")
            if status not in {"queued", "waiting"}:
                violation = (
                    "DRAIN violated: worker leased probe while cluster drain was active "
                    f"(status={status!r})"
                )
    finally:
        response = await client.post("/ops/drain", json={"draining": False})
        response.raise_for_status()
    return run_id, violation, time.monotonic() - started_at


def write_report(path: str, report: dict) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)


async def assert_sandbox_mode(client: httpx.AsyncClient, expected_mode: str) -> None:
    if not expected_mode:
        return
    r = await client.get("/ops/sandbox")
    if r.status_code != 200:
        raise RuntimeError(f"/ops/sandbox returned {r.status_code}: {r.text[:160]}")
    body = r.json()
    actual = str(body.get("mode") or "")
    if actual != expected_mode:
        raise RuntimeError(
            f"expected sandbox mode {expected_mode!r}, got {actual!r}: {body}"
        )


async def main() -> int:
    args = parse_args()

    async with httpx.AsyncClient(
        base_url=args.base_url,
        headers=_headers(),
        timeout=30.0,
    ) as client:
        await wait_for_api(client, args.ready_timeout)
        await assert_sandbox_mode(client, args.expect_sandbox_mode)
        wf_id = await make_workflow(client, sandbox=args.sandbox)
        profile = "sandboxed" if args.sandbox else "standard"
        print(f"workflow {wf_id}; storming {args.runs} {profile} runs")

        run_ids: list[str] = []
        cancelled: set[str] = set()
        faults: dict[str, float] = {}
        violations: list[str] = []
        cancel_every = max(int(1 / max(args.cancel_ratio, 0.01)), 1)
        for i in range(args.runs):
            rid = await start_run(client, wf_id, sandbox=args.sandbox)
            if rid:
                run_ids.append(rid)
                if args.cancel_ratio > 0 and i % cancel_every == 0:
                    await client.post(f"/runs/{rid}/cancel")
                    cancelled.add(rid)
            if args.kill_container and i == max(1, args.runs // 4):
                print(f"killing worker {args.kill_container} mid-storm")
                faults["worker_restart_seconds"] = await restart_container(
                    client,
                    args.kill_container,
                    wait_for_api_health=False,
                    ready_timeout=args.ready_timeout,
                )
            if args.interrupt_redis_container and i == max(1, args.runs * 2 // 5):
                print(f"pausing Redis {args.interrupt_redis_container}")
                faults["redis_recovery_seconds"] = await interrupt_dependency(
                    client,
                    args.interrupt_redis_container,
                    outage_seconds=args.outage_seconds,
                    ready_timeout=args.ready_timeout,
                )
            if args.interrupt_postgres_container and i == max(1, args.runs // 2):
                print(f"pausing PostgreSQL {args.interrupt_postgres_container}")
                faults["postgres_recovery_seconds"] = await interrupt_dependency(
                    client,
                    args.interrupt_postgres_container,
                    outage_seconds=args.outage_seconds,
                    ready_timeout=args.ready_timeout,
                )
            if args.restart_api_container and i == max(1, args.runs * 3 // 5):
                print(f"restarting API {args.restart_api_container}")
                faults["api_recovery_seconds"] = await restart_container(
                    client,
                    args.restart_api_container,
                    wait_for_api_health=True,
                    ready_timeout=args.ready_timeout,
                )
            await asyncio.sleep(0.05)

        if len(run_ids) != args.runs:
            violations.append(
                f"I0 violated: requested {args.runs} runs but only {len(run_ids)} were accepted"
            )
        if args.exercise_drain:
            print("exercising cluster-wide graceful drain")
            probe_id, drain_violation, elapsed = await exercise_cluster_drain(
                client, wf_id, sandbox=args.sandbox
            )
            faults["drain_probe_seconds"] = elapsed
            if probe_id:
                run_ids.append(probe_id)
            if drain_violation:
                violations.append(drain_violation)

        print(
            f"storm done ({len(run_ids)} started, "
            f"{len(cancelled)} cancel requests); settling"
        )
        deadline = time.monotonic() + args.settle_seconds
        pending = set(run_ids)
        statuses: dict[str, str] = {}
        final_runs: dict[str, dict] = {}
        while pending and time.monotonic() < deadline:
            for rid in list(pending):
                r = await client.get(f"/runs/{rid}")
                if r.status_code == 200:
                    info = r.json()
                    st = info.get("status", "")
                    statuses[rid] = st
                    if st in TERMINAL:
                        final_runs[rid] = info
                        pending.discard(rid)
            if pending:
                await asyncio.sleep(3)

        if pending:
            violations.append(
                f"I1 violated: {len(pending)} run(s) not terminal after "
                f"{args.settle_seconds}s: "
                + ", ".join(f"{r}={statuses.get(r)}" for r in sorted(pending)[:10])
            )
        r = await client.get("/ops/queue")
        if r.status_code == 200:
            stats = r.json()
            for key in ("leased", "running"):
                if int(stats.get(key, 0)) > 0:
                    violations.append(f"I2 violated: queue reports {key}={stats[key]}")
        else:
            print(f"note: /ops/queue returned {r.status_code}; skipping I2")

        by_status: dict[str, int] = {}
        for st in statuses.values():
            by_status[st] = by_status.get(st, 0) + 1
        latencies = [
            latency
            for info in final_runs.values()
            if (latency := run_latency_seconds(info)) is not None
        ]
        summary = latency_summary(latencies)
        print(f"terminal breakdown: {by_status}")
        print(
            "latency seconds: "
            f"count={summary['count']} p50={summary['p50']:.3f} "
            f"p95={summary['p95']:.3f} max={summary['max']:.3f}"
        )
        if args.max_p95_seconds > 0 and summary["p95"] > args.max_p95_seconds:
            violations.append(
                "PERF violated: p95 run-completion latency "
                f"{summary['p95']:.3f}s exceeded {args.max_p95_seconds:.3f}s"
            )
        report = {
            "cancel_requests": len(cancelled),
            "faults": faults,
            "latency_seconds": summary,
            "requested_runs": args.runs,
            "started_runs": len(run_ids),
            "status_counts": by_status,
            "violations": violations,
        }
        write_report(args.report_json, report)
        if violations:
            print("\nSOAK FAILED:")
            for v in violations:
                print(f"  - {v}")
            return 1
        print("\nSOAK PASSED: all runs terminal, queue drained, and injected faults recovered.")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
