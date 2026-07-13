from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _load_mcp_smoke():
    path = Path(__file__).resolve().parents[3] / "scripts" / "mcp_smoke.py"
    spec = importlib.util.spec_from_file_location("mcp_smoke", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mcp_smoke_marks_write_calls_as_human_approved(monkeypatch) -> None:
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
        lambda method, params=None: {"result": {"serverInfo": {"name": "test"}}},
    )
    monkeypatch.setattr(smoke, "call", fake_call)

    smoke.main()

    by_name = {name: arguments for name, arguments in calls}
    assert by_name["set_workflow_graph"]["approved_by_user"] is True
    assert by_name["publish_workflow"]["approved_by_user"] is True
    assert "approved_by_user" not in by_name["run_workflow"]
