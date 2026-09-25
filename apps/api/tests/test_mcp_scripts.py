from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


def _load_mcp_smoke():
    path = Path(__file__).resolve().parents[3] / "scripts" / "mcp_smoke.py"
    spec = importlib.util.spec_from_file_location("mcp_smoke", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mcp_smoke_never_asserts_human_approval(monkeypatch) -> None:
    smoke = _load_mcp_smoke()
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append((name, arguments))
        if name == "create_workflow":
            return {"workflow_id": "wf_123"}
        if name == "run_workflow":
            return {"run_id": "run_123", "status": "success"}
        return {"ok": True}

    monkeypatch.setattr(
        smoke,
        "rpc",
        lambda method, params=None: {"result": {"serverInfo": {"name": "test"}, "protocolVersion": "2025-11-25"}},
    )
    monkeypatch.setattr(smoke, "call", fake_call)

    smoke.main()

    by_name = {name: arguments for name, arguments in calls}
    assert "set_workflow_graph" in by_name
    assert "publish_workflow" in by_name
    assert "run_workflow" in by_name
    assert all("approved_by_user" not in arguments for _, arguments in calls)


def _approval_result(smoke):
    payload = {
        "error": "human_approval_required", "approval_id": "a" * 32,
        "review_path": "/mcp-approvals/" + "a" * 32,
        "org_id": "org-1", "expires_at": "2026-09-25T12:00:00Z",
    }
    return {"result": {"isError": True, "content": [{"type": "text", "text": smoke.json.dumps(payload)}]}}


def test_interactive_smoke_waits_then_retries_exact_arguments_without_review_api(monkeypatch, capsys):
    smoke = _load_mcp_smoke()
    calls = []
    args = {"workflow_id": "wf-1", "graph": {"nodes": [], "edges": []}}
    reviewed = False

    def rpc(method, params):
        calls.append((method, params))
        if len(calls) == 1:
            return _approval_result(smoke)
        assert reviewed
        assert params == {"name": "set_workflow_graph", "arguments": {**args, "approval_id": "a" * 32}}
        return {"result": {"content": [], "structuredContent": {"ok": True}}}

    def human_input(prompt):
        nonlocal reviewed
        assert len(calls) == 1
        reviewed = True
        return ""

    monkeypatch.setattr(smoke, "rpc", rpc)
    monkeypatch.setattr(smoke, "WEB_BASE", "https://nodyra.example")
    monkeypatch.setattr(smoke.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", human_input)
    assert smoke.call("set_workflow_graph", args) == {"ok": True}
    assert len(calls) == 2
    assert all(method == "tools/call" for method, _ in calls)
    assert "approval_id" not in args
    assert "https://nodyra.example/mcp-approvals/" + "a" * 32 in capsys.readouterr().out


def test_noninteractive_smoke_prints_actionable_review_and_stops(monkeypatch, capsys):
    smoke = _load_mcp_smoke()
    calls = []

    def rpc(method, params):
        calls.append((method, params))
        return _approval_result(smoke)

    monkeypatch.setattr(smoke, "rpc", rpc)
    monkeypatch.setattr(smoke.sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit, match="Approval required"):
        smoke.call("create_workflow", {"name": "Smoke"})
    assert len(calls) == 1
    text = capsys.readouterr().out
    assert "/mcp-approvals/" + "a" * 32 in text
    assert '"approval_id": "' + "a" * 32 in text


@pytest.mark.parametrize("answer", ["cancel", "stop"])
def test_cancelled_review_makes_no_retry(monkeypatch, answer):
    smoke = _load_mcp_smoke()
    calls = []
    monkeypatch.setattr(smoke, "rpc", lambda method, params: calls.append(params) or _approval_result(smoke))
    monkeypatch.setattr(smoke.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    with pytest.raises(SystemExit, match="stopped"):
        smoke.call("run_workflow", {"workflow_id": "wf-1"})
    assert len(calls) == 1


def test_unapproved_retry_does_not_forge_approval_or_loop(monkeypatch):
    smoke = _load_mcp_smoke()
    calls = []

    def rpc(method, params):
        calls.append(params)
        if len(calls) == 1:
            return _approval_result(smoke)
        return {"result": {"isError": True, "content": [{"type": "text", "text": "Approval has not been granted"}]}}

    monkeypatch.setattr(smoke, "rpc", rpc)
    monkeypatch.setattr(smoke.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    with pytest.raises(SystemExit, match="has not been granted"):
        smoke.call("run_workflow", {"workflow_id": "wf-1"})
    assert len(calls) == 2
    assert all("approved_by_user" not in params["arguments"] for params in calls)


def test_smoke_does_not_publish_a_failed_run(monkeypatch):
    smoke = _load_mcp_smoke()
    calls = []

    def call(name, arguments):
        calls.append(name)
        if name == "create_workflow":
            return {"workflow_id": "wf-1"}
        if name == "run_workflow":
            return {"run_id": "run-1", "status": "failed"}
        return {"ok": True}

    monkeypatch.setattr(smoke, "call", call)
    monkeypatch.setattr(smoke, "rpc", lambda method, params=None: {"result": {
        "serverInfo": {"name": "test"}, "protocolVersion": "2025-11-25",
    }})
    with pytest.raises(SystemExit, match="did not finish successfully"):
        smoke.main()
    assert "publish_workflow" not in calls
