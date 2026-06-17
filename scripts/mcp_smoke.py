"""Drive the Noodle MCP server over HTTP to build, run, and publish a workflow.

Usage:
    python scripts/mcp_smoke.py            # uses BASE/TOKEN below or env vars
    NOODLE_MCP_TOKEN=... python scripts/mcp_smoke.py

This is a self-contained MCP client (no SDK) showing the exact JSON-RPC calls an
LLM makes against POST /mcp: initialize -> tools/call (create -> set_graph ->
validate -> run -> publish).
"""

import json
import os
import sys
import urllib.request

BASE = os.environ.get("NOODLE_MCP_URL", "http://localhost:8000/mcp")
TOKEN = os.environ.get("NOODLE_MCP_TOKEN", "")

_id = 0


def rpc(method: str, params: dict | None = None) -> dict:
    global _id
    _id += 1
    body = json.dumps({"jsonrpc": "2.0", "id": _id, "method": method, "params": params or {}}).encode()
    req = urllib.request.Request(BASE, data=body, headers={"Content-Type": "application/json"})
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def call(name: str, arguments: dict) -> dict:
    """tools/call helper that unwraps the text content (parsing JSON when it is JSON)."""
    out = rpc("tools/call", {"name": name, "arguments": arguments})
    if "error" in out:
        raise SystemExit(f"transport error calling {name}: {out['error']}")
    result = out["result"]
    text = result["content"][0]["text"]
    if result.get("isError"):
        raise SystemExit(f"tool {name} returned isError: {text}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_text": text}


def main() -> None:
    info = rpc("initialize", {"protocolVersion": "2025-06-18"})
    print("initialize ->", info["result"]["serverInfo"])

    wf = call("create_workflow", {"name": "MCP Smoke Test — Doubler"})
    wid = wf["workflow_id"]
    print("create_workflow ->", wid)

    graph = {
        "nodes": [
            {"id": "trigger", "type": "manual_trigger",
             "params": {"data": {"value": 21}}, "position": {"x": 0, "y": 0}},
            {"id": "double", "type": "code",
             "params": {"code": "output = {'doubled': input['value'] * 2}"},
             "position": {"x": 320, "y": 0}},
        ],
        "edges": [
            {"source": "trigger", "source_output": "main",
             "target": "double", "target_input": "input"},
        ],
    }
    print("validate_graph ->", call("validate_graph", {"graph": graph}))
    print("set_workflow_graph ->", call("set_workflow_graph", {"workflow_id": wid, "graph": graph}))

    run = call("run_workflow", {"workflow_id": wid, "use_draft": True, "wait_seconds": 60})
    print("run_workflow ->", json.dumps(run))

    pub = call("publish_workflow", {"workflow_id": wid, "notes": "MCP smoke test"})
    print("publish_workflow ->", json.dumps(pub))
    print(f"\nDONE. Workflow {wid} created, run status={run.get('status')}.")


if __name__ == "__main__":
    if not TOKEN and "--no-token" not in sys.argv:
        print("warning: NOODLE_MCP_TOKEN not set (ok only if AUTH_REQUIRED=false)", file=sys.stderr)
    main()
