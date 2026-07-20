"""Evaluate consecutive canary signal windows against Nodyra rollback gates."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _relative_or_absolute(canary: float, stable: float, ceiling: float) -> bool:
    return canary > ceiling or (stable > 0 and canary > stable * 2)


def breached_signals(window: dict[str, Any]) -> list[str]:
    signals = window.get("signals")
    if not isinstance(signals, dict):
        raise ValueError("every window requires a signals object")

    breached: list[str] = []
    comparisons = {
        "api_5xx_ratio": 0.01,
        "run_error_ratio": 0.05,
        "queue_p95_seconds": 60.0,
        "artifact_failure_ratio": 0.005,
    }
    for name, ceiling in comparisons.items():
        signal = signals.get(name)
        if not isinstance(signal, dict):
            raise ValueError(f"missing comparison signal: {name}")
        canary = float(signal.get("canary", 0))
        stable = float(signal.get("stable", 0))
        if canary < 0 or stable < 0:
            raise ValueError(f"signal values cannot be negative: {name}")
        if _relative_or_absolute(canary, stable, ceiling):
            breached.append(name)

    worker_churn = float(signals.get("worker_churn_per_replica_15m", 0))
    if worker_churn > 3:
        breached.append("worker_churn_per_replica_15m")
    readiness_minutes = float(signals.get("readiness_failure_minutes", 0))
    if readiness_minutes > 5:
        breached.append("readiness_failure_minutes")
    for name in ("browser_smoke_pass", "bundle_budget_pass"):
        if signals.get(name) is not True:
            breached.append(name)
    return sorted(breached)


def evaluate(document: dict[str, Any], required_windows: int) -> dict[str, Any]:
    windows = document.get("windows")
    if not isinstance(windows, list) or len(windows) < required_windows:
        raise ValueError(f"at least {required_windows} signal windows are required")
    recent = windows[-required_windows:]
    per_window = [breached_signals(window) for window in recent]
    persistent = sorted(set.intersection(*(set(items) for items in per_window)))
    return {
        "schema_version": 1,
        "stage": document.get("stage", "canary"),
        "decision": "halt" if persistent else "continue",
        "required_consecutive_windows": required_windows,
        "evaluated_at": datetime.now(UTC).isoformat(),
        "persistent_breaches": persistent,
        "windows": [
            {
                "ended_at": window.get("ended_at"),
                "breaches": breaches,
            }
            for window, breaches in zip(recent, per_window, strict=True)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("rollout-decision.json"))
    parser.add_argument("--required-windows", type=int, default=2)
    args = parser.parse_args()
    if args.required_windows < 1:
        raise SystemExit("--required-windows must be positive")
    document = json.loads(args.input.read_text(encoding="utf-8"))
    try:
        decision = evaluate(document, args.required_windows)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"invalid rollout signal document: {exc}") from exc
    args.output.write_text(json.dumps(decision, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(decision, indent=2))
    return 1 if decision["decision"] == "halt" else 0


if __name__ == "__main__":
    raise SystemExit(main())
