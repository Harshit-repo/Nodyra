"""Run product-differentiation proof demos against a live Nodyra API.

The script is intentionally network-local and deterministic: it does not call
an external LLM provider. It proves:

1. Python-native: a code node can be inspected and tested in isolation.
2. AI-inspectable: the same workflow can be explained, checked, and published
   through the human-review surfaces.

The MCP-native proof is `scripts/mcp_smoke.py`, which runs in the same CI smoke
job and exercises workflow creation/running/publishing through MCP tools.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

PYTHON_NATIVE_GRAPH: dict[str, Any] = {
    "nodes": [
        {
            "id": "trigger",
            "type": "manual_trigger",
            "params": {"data": {"n": 41}},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "transform",
            "type": "code",
            "params": {"code": "output = input['n'] + 1"},
            "position": {"x": 320, "y": 0},
        },
    ],
    "edges": [
        {
            "id": "e1",
            "source": "trigger",
            "source_output": "main",
            "target": "transform",
            "target_input": "input",
        }
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=os.environ.get("NODYRA_API_URL", "http://localhost:8000"),
    )
    parser.add_argument("--timeout", type=float, default=90.0)
    return parser.parse_args()


class ApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = os.environ.get("NODYRA_TOKEN", "")

    def request(self, method: str, path: str, body: dict | None = None) -> Any:
        data = json.dumps(body or {}).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} failed: {exc.code} {detail}") from exc
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))


def create_python_native_workflow(api: ApiClient) -> str:
    workflow = api.request("POST", "/workflows", {"name": "Proof - Python Native"})
    workflow_id = workflow["id"]
    api.request("PUT", f"/workflows/{workflow_id}", {"graph": PYTHON_NATIVE_GRAPH})
    return workflow_id


def wait_for_run(api: ApiClient, run_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = api.request("GET", f"/runs/{run_id}")
        if last.get("status") in {"success", "error", "cancelled"}:
            return last
        time.sleep(1)
    raise TimeoutError(f"run {run_id} did not finish within {timeout:.0f}s: {last}")


def run_python_native_proof(api: ApiClient, workflow_id: str, timeout: float) -> dict:
    node_test = api.request(
        "POST",
        f"/workflows/{workflow_id}/nodes/transform/test",
        {"inputs": {"input": {"n": 41}}, "use_pinned": False},
    )
    if node_test.get("status") != "success" or node_test.get("output") != {"main": 42}:
        raise RuntimeError(f"python-native node test failed: {node_test}")

    started = api.request("POST", f"/workflows/{workflow_id}/run", {"mode": "manual"})
    run_id = started.get("run_id") or started.get("id")
    if not run_id:
        raise RuntimeError(f"run response did not include a run id: {started}")
    run = wait_for_run(api, run_id, timeout)
    if run.get("status") != "success":
        raise RuntimeError(f"python-native workflow run failed: {run}")
    return {"node_test": node_test, "run_id": run_id, "status": run["status"]}


def run_ai_inspectable_proof(api: ApiClient, workflow_id: str) -> dict:
    explanation = api.request("POST", f"/workflows/{workflow_id}/explain", {})
    if not isinstance(explanation, dict) or not explanation:
        raise RuntimeError(f"explain returned no usable content: {explanation}")

    generated = api.request("POST", f"/workflows/{workflow_id}/generate-tests", {})
    if not generated.get("tests"):
        raise RuntimeError(f"generate-tests returned no cases: {generated}")

    saved = api.request(
        "POST",
        f"/workflows/{workflow_id}/checks",
        {
            "checks": [
                {
                    "name": "Python increment contract",
                    "input_data": {"n": 41},
                    "expected_outputs": {"transform.main": 42},
                    "assertions": ["run.status == 'success'"],
                }
            ],
            "replace": True,
        },
    )
    if not saved:
        raise RuntimeError("saving workflow checks returned an empty list")

    results = api.request("POST", f"/workflows/{workflow_id}/checks/run", {})
    if not results or not all(item.get("passed") for item in results):
        raise RuntimeError(f"workflow checks did not pass: {results}")

    published = api.request("POST", f"/workflows/{workflow_id}/publish", {})
    return {
        "explain_keys": sorted(explanation.keys()),
        "generated_tests": len(generated["tests"]),
        "checks": len(results),
        "published_version": published.get("version"),
    }


def main() -> int:
    args = parse_args()
    api = ApiClient(args.base_url)
    workflow_id = create_python_native_workflow(api)
    result = {
        "workflow_id": workflow_id,
        "python_native": run_python_native_proof(api, workflow_id, args.timeout),
        "ai_inspectable": run_ai_inspectable_proof(api, workflow_id),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - CLI should print a concise failure.
        print(f"proof-point demos failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
