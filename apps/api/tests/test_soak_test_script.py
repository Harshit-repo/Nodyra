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

    args = soak.parse_args(
        [
            "--sandbox",
            "--expect-sandbox-mode",
            "required",
            "--max-p95-seconds",
            "120",
            "--exercise-drain",
            "--outage-seconds",
            "7",
        ]
    )

    assert args.sandbox is True
    assert args.expect_sandbox_mode == "required"
    assert args.max_p95_seconds == 120
    assert args.exercise_drain is True
    assert args.outage_seconds == 7


def test_latency_summary_uses_finished_minus_started_and_nearest_rank_p95() -> None:
    soak = _load_soak_module()

    run = {
        "started_at": "2026-07-13T01:00:00+00:00",
        "finished_at": "2026-07-13T01:00:03.500000+00:00",
    }

    assert soak.run_latency_seconds(run) == 3.5
    assert soak.latency_summary([1.0, 2.0, 3.0, 4.0]) == {
        "count": 4,
        "p50": 2.0,
        "p95": 4.0,
        "max": 4.0,
    }


def test_timed_out_is_a_terminal_run_status() -> None:
    soak = _load_soak_module()

    assert "timed_out" in soak.TERMINAL
