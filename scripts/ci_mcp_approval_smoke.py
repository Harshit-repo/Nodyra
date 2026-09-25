"""CI-only approval-protocol exercise against a disposable loopback stack.

This fixture deliberately plays both requester and human-review browser roles.
It is not an operator CLI, approval bypass, or example for production agents.
The ordinary mcp_smoke.py continues to require interactive human review.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import time
from urllib.parse import urlsplit

import httpx


def require_disposable_ci(base: str) -> None:
    target = urlsplit(base)
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("NODYRA_CI_DISPOSABLE_INSTANCE") != "1"
        or target.scheme not in {"http", "https"}
        or target.hostname not in {"127.0.0.1", "localhost", "::1"}
        or target.username is not None
        or target.password is not None
        or target.path not in {"", "/"}
        or target.query
        or target.fragment
    ):
        raise RuntimeError("This CI fixture requires an explicitly disposable GitHub Actions loopback instance")


def payload(response: httpx.Response, expected: int = 200) -> dict:
    if response.status_code != expected:
        raise RuntimeError(f"{response.request.method} {response.request.url.path}: expected HTTP {expected}, got {response.status_code}")
    return response.json()


def tool_value(result: dict) -> dict:
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    return json.loads(result["content"][0]["text"])


async def run(base: str, *, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    require_disposable_ci(base)
    options = {"base_url": base.rstrip("/"), "timeout": 120.0, "follow_redirects": False}
    if transport is not None:
        options["transport"] = transport
    password = "Ci-Smoke-" + secrets.token_urlsafe(32)
    email = "mcp-ci-owner@example.invalid"
    async with httpx.AsyncClient(**options) as bootstrap, httpx.AsyncClient(**options) as browser, httpx.AsyncClient(**options) as agent:
        registered = payload(await bootstrap.post("/auth/register", json={
            "name": "CI MCP reviewer", "company": "Disposable CI fixture",
            "email": email, "password": password,
        }), 201)
        if registered["user"]["role"] != "owner":
            raise RuntimeError("Refusing to run: this fixture must bootstrap the first owner of a disposable instance")
        print("CI MCP: disposable owner registered", flush=True)
        owner_headers = {"Authorization": f"Bearer {registered['token']}"}
        created_pat = payload(await bootstrap.post("/auth/api-tokens", headers=owner_headers, json={
            "name": "Disposable MCP smoke requester", "scopes": ["workflow:write", "workflow:run"],
            "expires_in_days": 1,
        }), 201)
        agent.headers["Authorization"] = f"Bearer {created_pat['token']}"
        agent.headers["Accept"] = "application/json, text/event-stream"
        try:
            payload(await browser.post("/auth/login", json={"email": email, "password": password}))
            csrf = browser.cookies.get("nodyra_csrf")
            if not csrf or not browser.cookies.get("nodyra_session"):
                raise RuntimeError("Browser login did not issue session and CSRF cookies")
            browser_headers = {"X-CSRF-Token": csrf}
            sequence = 0
            reviewed = []

            async def rpc(method: str, params: dict | None = None) -> dict:
                nonlocal sequence
                sequence += 1
                message = {"jsonrpc": "2.0", "id": sequence, "method": method, "params": params or {}}
                body = payload(await agent.post("/mcp", json=message))
                if "error" in body or body.get("id") != sequence:
                    raise RuntimeError(f"MCP protocol failure for {method}")
                return body["result"]

            info = await rpc("initialize", {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "nodyra-disposable-ci", "version": "1"},
            })
            agent.headers["MCP-Protocol-Version"] = info["protocolVersion"]
            initialized = await agent.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
            if initialized.status_code != 202:
                raise RuntimeError("Initialized notification failed")

            async def call(name: str, arguments: dict, *, review: bool = False) -> dict:
                result = await rpc("tools/call", {"name": name, "arguments": arguments})
                if not review:
                    if result.get("isError"):
                        raise RuntimeError(f"MCP read/validation failed: {name}")
                    return tool_value(result)
                required = tool_value(result)
                if not result.get("isError") or required.get("error") != "human_approval_required":
                    raise RuntimeError(f"Sensitive tool executed without requesting review: {name}")
                approval_id = required["approval_id"]
                review_path = f"/mcp-approvals/{approval_id}"
                decision_path = review_path + "/decision"
                pending = payload(await browser.get(review_path))
                if pending["status"] != "pending" or pending["tool_name"] != name or pending["arguments"] != arguments:
                    raise RuntimeError(f"Approval preview did not match the requested action: {name}")
                if not reviewed:
                    denied = await agent.post(decision_path, json={"decision": "approve"})
                    if denied.status_code != 401:
                        raise RuntimeError("Requester PAT was able to enter the browser review API")
                    denied = await browser.post(decision_path, json={"decision": "approve"})
                    if denied.status_code != 403:
                        raise RuntimeError("Browser approval did not require CSRF protection")
                decision = payload(await browser.post(decision_path, headers=browser_headers, json={"decision": "approve"}))
                if decision["status"] != "approved":
                    raise RuntimeError("Browser approval was not recorded")
                retry = {**arguments, "approval_id": approval_id}
                result = await rpc("tools/call", {"name": name, "arguments": retry})
                if result.get("isError"):
                    raise RuntimeError(f"Approved MCP action failed: {name}: {result['content'][0]['text'][:600]}")
                if payload(await browser.get(review_path))["status"] != "consumed":
                    raise RuntimeError("Approval was not consumed")
                replay = await rpc("tools/call", {"name": name, "arguments": retry})
                if not replay.get("isError"):
                    raise RuntimeError("Consumed command approval was replayable")
                reviewed.append(name)
                print(f"CI MCP: {name} reviewed, executed, consumed and replay-blocked", flush=True)
                return tool_value(result)

            workflow = await call("create_workflow", {"name": "CI MCP Smoke Doubler"}, review=True)
            workflow_id = workflow["workflow_id"]
            graph = {
                "nodes": [
                    {"id": "trigger", "type": "manual_trigger", "params": {"data": {"value": 21}}, "position": {"x": 0, "y": 0}},
                    {"id": "double", "type": "code", "params": {"code": "output = {'doubled': input['value'] * 2}"}, "position": {"x": 320, "y": 0}},
                ],
                "edges": [{"source": "trigger", "source_output": "main", "target": "double", "target_input": "input"}],
            }
            validation = await call("validate_graph", {"graph": graph})
            if not validation.get("valid"):
                raise RuntimeError("Smoke graph failed validation")
            await call("set_workflow_graph", {"workflow_id": workflow_id, "graph": graph}, review=True)
            run_result = await call("run_workflow", {"workflow_id": workflow_id, "use_draft": True, "wait_seconds": 60}, review=True)
            run_id = run_result["run_id"]
            deadline = time.monotonic() + 180
            while True:
                detail = await call("get_run", {"run_id": run_id})
                if detail["status"] not in {"queued", "running", "pending"} or time.monotonic() >= deadline:
                    break
                await asyncio.sleep(1)
            if detail["status"] != "success":
                raise RuntimeError(f"Smoke run did not succeed: {json.dumps(detail)[:1200]}")
            doubled = next((node for node in detail["nodes"] if node["node_id"] == "double"), None)
            if doubled is None or doubled["status"] != "success" or doubled.get("output") not in ({"doubled": 42}, {"main": {"doubled": 42}}):
                raise RuntimeError(f"Doubler returned an unexpected result: {json.dumps(doubled)[:600]}")
            published = await call("publish_workflow", {"workflow_id": workflow_id, "notes": "CI approval protocol smoke"}, review=True)
            return {"workflow_id": workflow_id, "run_id": run_id, "status": detail["status"], "reviewed_actions": reviewed, "published": published}
        finally:
            revoked = await bootstrap.delete(f"/auth/api-tokens/{created_pat['id']}", headers=owner_headers)
            if revoked.status_code != 204:
                raise RuntimeError("Failed to revoke the disposable CI requester token")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    evidence = asyncio.run(run(args.base_url))
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
