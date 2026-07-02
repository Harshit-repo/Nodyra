"""Compose soak test: burst runs, kill worker, verify terminal states.

Usage:
    python scripts/soak_test.py --base-url http://localhost:8000 --token <PAT>
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from typing import Any

import httpx

TERMINAL = {"success", "error", "cancelled", "failed", "dead_lettered"}
SLEEP_GRAPH = {
    "nodes": [
        {
            "id": "t",
            "type": "manual_trigger",
            "name": "Start",
            "params": {},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "c",
            "type": "code",
            "name": "Sleep",
            "params": {"code": "import time\ntime.sleep(2)\noutput = {'ok': True}"},
            "position": {"x": 250, "y": 0},
        },
    ],
    "edges": [
        {
            "id": "e1",
            "source": "t",
            "source_output": "main",
            "target": "c",
            "target_input": "input",
        }
    ],
}


def _id(payload: dict[str, Any]) -> str:
    value = payload.get("run_id") or payload.get("id")
    if not value:
        raise RuntimeError(f"response did not include an id: {payload!r}")
    return str(value)


def _status(client: httpx.Client, run_id: str) -> str:
    return str(client.get(f"/runs/{run_id}").raise_for_status().json()["status"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--token", default=None)
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--worker-service", default="worker")
    parser.add_argument("--compose-file", default="deploy/docker-compose.yml")
    args = parser.parse_args()

    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=30) as client:
        workflow = client.post(
            "/workflows",
            json={"name": f"soak-{int(time.time())}"},
        ).raise_for_status().json()
        workflow_id = str(workflow["id"])
        client.put(
            f"/workflows/{workflow_id}",
            json={"graph": SLEEP_GRAPH},
        ).raise_for_status()
        client.post(f"/workflows/{workflow_id}/publish").raise_for_status()
        print(f"workflow {workflow_id} published")

        run_ids: list[str] = []
        start = time.monotonic()
        for _ in range(args.runs):
            payload = client.post(
                f"/workflows/{workflow_id}/run",
                json={"mode": "manual"},
            ).raise_for_status().json()
            run_ids.append(_id(payload))
        print(f"enqueued {len(run_ids)} runs in {time.monotonic() - start:.1f}s")

        time.sleep(5)
        print(f"killing worker service '{args.worker_service}' ...")
        subprocess.run(
            ["docker", "compose", "-f", args.compose_file, "kill", args.worker_service],
            check=True,
        )
        time.sleep(10)
        print("restarting worker ...")
        subprocess.run(
            ["docker", "compose", "-f", args.compose_file, "start", args.worker_service],
            check=True,
        )

        cancelled = 0
        for run_id in run_ids:
            if cancelled >= 20:
                break
            if _status(client, run_id) == "running":
                client.post(f"/runs/{run_id}/cancel").raise_for_status()
                cancelled += 1
        print(f"cancel storm: {cancelled} cancels issued")

        deadline = time.monotonic() + 15 * 60
        pending = set(run_ids)
        while pending and time.monotonic() < deadline:
            for run_id in list(pending):
                if _status(client, run_id) in TERMINAL:
                    pending.discard(run_id)
            print(f"pending: {len(pending)}")
            time.sleep(5)

    if pending:
        sample = ", ".join(sorted(pending)[:10])
        print(f"FAIL: {len(pending)} runs never reached a terminal state: {sample}")
        return 1
    print("PASS: all runs reached terminal states after worker kill + cancel storm")
    return 0


if __name__ == "__main__":
    sys.exit(main())
