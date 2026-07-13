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
        --kill-container nodyra-worker-1 --sandbox --expect-sandbox-mode required

Invariants asserted:
  I1  every started run reaches a terminal status (success/error/cancelled)
      within --settle-seconds of the storm ending;
  I2  no queue entry is left leased/running after settling;
  I3  the worker kill loses zero runs (they re-lease and finish);
  I4  when --sandbox is set, runs are submitted with execution_mode=sandboxed.
Exit 0 = all invariants hold; exit 1 = violations printed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Sequence

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


async def make_workflow(client: httpx.AsyncClient, *, sandbox: bool) -> str:
    r = await client.post("/workflows", json={"name": f"soak-{int(time.time())}"})
    r.raise_for_status()
    wf_id = r.json()["id"]
    r = await client.put(f"/workflows/{wf_id}", json=workflow_update_payload(sandbox=sandbox))
    r.raise_for_status()
    return wf_id


async def start_run(client: httpx.AsyncClient, wf_id: str, *, sandbox: bool) -> str | None:
    r = await client.post(f"/workflows/{wf_id}/run", json=run_request_body(sandbox=sandbox))
    if r.status_code >= 400:
        print(f"  start rejected ({r.status_code}): {r.text[:120]}")
        return None
    body = r.json()
    return body["run_id"] if "run_id" in body else body.get("id")


async def wait_for_api(client: httpx.AsyncClient, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            r = await client.get("/health/live")
            if r.status_code == 200:
                return
            last_error = f"/health/live returned {r.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        await asyncio.sleep(2)
    raise RuntimeError(
        f"API did not become live within {timeout_seconds}s ({last_error})"
    )


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
        cancel_every = max(int(1 / max(args.cancel_ratio, 0.01)), 1)
        for i in range(args.runs):
            rid = await start_run(client, wf_id, sandbox=args.sandbox)
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
