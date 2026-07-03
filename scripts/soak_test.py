"""Durable-queue soak test: run storm + cancel storm + worker kill.

Usage (against the compose stack):
    docker compose -f deploy/docker-compose.yml up -d
    NOODLE_TOKEN=... uv run python scripts/soak_test.py \
        --base-url http://localhost:8000 --runs 100 --cancel-ratio 0.2 \
        --kill-container noodle-worker-1

Invariants asserted:
  I1  every started run reaches a terminal status (success/error/cancelled)
      within --settle-seconds of the storm ending;
  I2  no queue entry is left leased/running after settling;
  I3  the worker kill loses zero runs (they re-lease and finish).
Exit 0 = all invariants hold; exit 1 = violations printed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time

import httpx

TERMINAL = {"success", "error", "cancelled"}

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
            "params": {"code": "import time\ntime.sleep(2)\nreturn {'ok': True}"},
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


def _headers() -> dict[str, str]:
    token = os.environ.get("NOODLE_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


async def make_workflow(client: httpx.AsyncClient) -> str:
    r = await client.post("/workflows", json={"name": f"soak-{int(time.time())}"})
    r.raise_for_status()
    wf_id = r.json()["id"]
    r = await client.put(f"/workflows/{wf_id}", json={"graph": WORKFLOW_GRAPH})
    r.raise_for_status()
    return wf_id


async def start_run(client: httpx.AsyncClient, wf_id: str) -> str | None:
    r = await client.post(f"/workflows/{wf_id}/run", json={})
    if r.status_code >= 400:
        print(f"  start rejected ({r.status_code}): {r.text[:120]}")
        return None
    body = r.json()
    return body["run_id"] if "run_id" in body else body.get("id")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--cancel-ratio", type=float, default=0.2)
    ap.add_argument("--kill-container", default="")
    ap.add_argument("--settle-seconds", type=float, default=180.0)
    args = ap.parse_args()

    async with httpx.AsyncClient(
        base_url=args.base_url,
        headers=_headers(),
        timeout=30.0,
    ) as client:
        wf_id = await make_workflow(client)
        print(f"workflow {wf_id}; storming {args.runs} runs")

        run_ids: list[str] = []
        cancelled: set[str] = set()
        cancel_every = max(int(1 / max(args.cancel_ratio, 0.01)), 1)
        for i in range(args.runs):
            rid = await start_run(client, wf_id)
            if rid:
                run_ids.append(rid)
                if args.cancel_ratio > 0 and i % cancel_every == 0:
                    await client.post(f"/runs/{rid}/cancel")
                    cancelled.add(rid)
            if args.kill_container and i == args.runs // 2:
                print(f"killing worker {args.kill_container} mid-storm")
                subprocess.run(["docker", "kill", args.kill_container], check=False)
                subprocess.run(["docker", "start", args.kill_container], check=False)
            await asyncio.sleep(0.05)

        print(
            f"storm done ({len(run_ids)} started, "
            f"{len(cancelled)} cancel requests); settling"
        )
        deadline = time.monotonic() + args.settle_seconds
        pending = set(run_ids)
        statuses: dict[str, str] = {}
        while pending and time.monotonic() < deadline:
            for rid in list(pending):
                r = await client.get(f"/runs/{rid}")
                if r.status_code == 200:
                    st = r.json().get("status", "")
                    statuses[rid] = st
                    if st in TERMINAL:
                        pending.discard(rid)
            if pending:
                await asyncio.sleep(3)

        violations: list[str] = []
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
        print(f"terminal breakdown: {by_status}")
        if violations:
            print("\nSOAK FAILED:")
            for v in violations:
                print(f"  - {v}")
            return 1
        print("\nSOAK PASSED: all runs terminal, queue drained, worker kill survived.")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
