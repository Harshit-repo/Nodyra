from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_soak_module():
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts" / "soak_test.py"
    spec = importlib.util.spec_from_file_location("soak_test", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_sandbox_payloads_force_sandboxed_execution() -> None:
    soak = _load_soak_module()

    assert soak.run_request_body(sandbox=True) == {"sandbox": True}
    assert soak.run_request_body(sandbox=False) == {}
    assert soak.workflow_update_payload(sandbox=True)["execution_mode"] == "sandboxed"
    assert "execution_mode" not in soak.workflow_update_payload(sandbox=False)


def test_parse_args_sandbox_profile() -> None:
    soak = _load_soak_module()

    args = soak.parse_args(["--sandbox", "--expect-sandbox-mode", "required"])

    assert args.sandbox is True
    assert args.expect_sandbox_mode == "required"
