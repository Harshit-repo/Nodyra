"""Drive the Nodyra MCP server over HTTP to build, run, and publish a workflow.

Usage:
    NODYRA_MCP_TOKEN=ndpat_... NODYRA_WEB_URL=http://localhost:5173 python scripts/mcp_smoke.py

This is a self-contained MCP client (no SDK) showing the exact JSON-RPC calls an
LLM makes against POST /mcp. Mutations and runs require an independent browser
review. Run interactively: the script pauses at each approval and never makes a
review decision itself. Noninteractive execution stops at the first approval.
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request

BASE = os.environ.get("NODYRA_MCP_URL", "http://localhost:8000/mcp")
TOKEN = os.environ.get("NODYRA_MCP_TOKEN", "")
WEB_BASE = os.environ.get("NODYRA_WEB_URL", "http://localhost:5173").rstrip("/")
PROTOCOL_VERSION = "2025-11-25"
MAX_RESPONSE_BYTES = 2_000_000

_id = 0


def _configure_output() -> None:
    """Keep Unicode progress output readable on Windows cp1252 consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def rpc(method: str, params: dict | None = None) -> dict:
    global _id
    _id += 1
    message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    notification = method == "notifications/initialized"
    if not notification:
        message["id"] = _id
    body = json.dumps(message).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if method != "initialize":
        headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
    req = urllib.request.Request(BASE, data=body, headers=headers)
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=90) as resp:
        if notification:
            if resp.status != 202:
                raise SystemExit("Nodyra did not accept the initialized notification.")
            return {}
        payload = resp.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise SystemExit("MCP response exceeded the smoke-test size limit.")
        out = json.loads(payload.decode("utf-8"))
        if not isinstance(out, dict) or out.get("jsonrpc") != "2.0" or type(out.get("id")) is not int or out["id"] != _id:
            raise SystemExit("Invalid MCP response envelope.")
        return out


def _result_value(result: dict) -> dict:
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    content = result.get("content", [])
    text = "\n".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {"_text": text}
    return value if isinstance(value, dict) else {"_value": value}


def _review_arguments(name: str, arguments: dict, approval: dict) -> dict:
    """Surface a review; terminal input only triggers a server-verified retry."""
    approval_id = approval.get("approval_id")
    if not isinstance(approval_id, str) or re.fullmatch(r"[a-f0-9]{32}", approval_id) is None:
        raise SystemExit("Invalid approval response from Nodyra.")
    expected_path = f"/mcp-approvals/{approval_id}"
    if approval.get("review_path") != expected_path:
        raise SystemExit("Invalid approval review path from Nodyra.")
    review_url = urllib.parse.urljoin(WEB_BASE + "/", expected_path.lstrip("/"))
    retry_arguments = {**arguments, "approval_id": approval_id}
    print(f"\nHuman review required for {name}: {review_url}")
    print(f"Workspace: {approval.get('org_id')} | Expires: {approval.get('expires_at')}")
    print("Sign in as the token owner using a separate browser session, select this workspace, and review the exact action.")
    if not sys.stdin.isatty():
        print("After approving, retry tools/call with:")
        print(json.dumps({"name": name, "arguments": retry_arguments}, ensure_ascii=False))
        raise SystemExit("Approval required. Use an interactive terminal for the complete guided smoke test; no review decision was made by this script.")
    answer = input("After approving in Nodyra, press Enter to retry; type cancel to stop: ")
    if answer.strip():
        raise SystemExit("Smoke test stopped before the pending action was executed.")
    return retry_arguments


def call(name: str, arguments: dict) -> dict:
    """tools/call helper that unwraps the text content (parsing JSON when it is JSON)."""
    out = rpc("tools/call", {"name": name, "arguments": arguments})
    if "error" in out:
        raise SystemExit(f"transport error calling {name}: {out['error']}")
    result = out["result"]
    value = _result_value(result)
    if result.get("isError"):
        if value.get("error") == "human_approval_required" and "approval_id" not in arguments:
            retry_arguments = _review_arguments(name, arguments, value)
            # Exactly one retry with the bound grant. Pending/denied/expired or
            # changed-target failures stop; we never create a review loop.
            return call(name, retry_arguments)
        raise SystemExit(f"tool {name} returned isError: {json.dumps(value, ensure_ascii=False)}")
    return value


def main() -> None:
    global PROTOCOL_VERSION
    info = rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "nodyra-smoke", "version": "1"}})
    if "error" in info:
        raise SystemExit(f"MCP initialization failed: {info['error']}")
    protocol = info["result"].get("protocolVersion")
    if protocol not in {"2025-11-25", "2025-06-18", "2025-03-26"}:
        raise SystemExit("Nodyra selected an unsupported MCP protocol version.")
    PROTOCOL_VERSION = protocol
    print("initialize ->", info["result"]["serverInfo"])
    rpc("notifications/initialized")

    wf = call("create_workflow", {"name": "MCP Smoke Test — Doubler"})
    wid = wf["workflow_id"]
    print("create_workflow ->", wid)

    graph = {
        "nodes": [
            {
                "id": "trigger",
                "type": "manual_trigger",
                "params": {"data": {"value": 21}},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "double",
                "type": "code",
                "params": {"code": "output = {'doubled': input['value'] * 2}"},
                "position": {"x": 320, "y": 0},
            },
        ],
        "edges": [
            {
                "source": "trigger",
                "source_output": "main",
                "target": "double",
                "target_input": "input",
            },
        ],
    }
    print("validate_graph ->", call("validate_graph", {"graph": graph}))
    print(
        "set_workflow_graph ->",
        call("set_workflow_graph", {"workflow_id": wid, "graph": graph}),
    )

    run = call("run_workflow", {"workflow_id": wid, "use_draft": True, "wait_seconds": 60})
    print("run_workflow ->", json.dumps(run))

    if run.get("status") != "success":
        raise SystemExit(f"Smoke workflow did not finish successfully; inspect run {run.get('run_id')} before publishing.")
    pub = call("publish_workflow", {"workflow_id": wid, "notes": "MCP smoke test"})
    print("publish_workflow ->", json.dumps(pub))
    print(f"\nDONE. Workflow {wid} created, run status={run.get('status')}.")


if __name__ == "__main__":
    _configure_output()
    if not TOKEN:
        raise SystemExit("Set NODYRA_MCP_TOKEN to a scoped automation token. Mutations require an authenticated requester and independent browser review, including in development mode.")
    main()
