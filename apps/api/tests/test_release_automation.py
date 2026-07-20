from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_script(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"nodyra_test_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load release script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_release_evidence = _load_script("build_release_evidence")
evaluate_rollout = _load_script("evaluate_rollout")


def _window(**overrides: Any) -> dict[str, Any]:
    signals: dict[str, Any] = {
        "api_5xx_ratio": {"canary": 0.001, "stable": 0.001},
        "run_error_ratio": {"canary": 0.01, "stable": 0.01},
        "queue_p95_seconds": {"canary": 5.0, "stable": 5.0},
        "artifact_failure_ratio": {"canary": 0.001, "stable": 0.001},
        "worker_churn_per_replica_15m": 0,
        "readiness_failure_minutes": 0,
        "browser_smoke_pass": True,
        "bundle_budget_pass": True,
    }
    signals.update(overrides)
    return {"ended_at": "2026-07-20T00:00:00Z", "signals": signals}


def test_rollout_continues_for_healthy_consecutive_windows() -> None:
    result = evaluate_rollout.evaluate(
        {"stage": "canary", "windows": [_window(), _window()]},
        required_windows=2,
    )

    assert result["decision"] == "continue"
    assert result["persistent_breaches"] == []


def test_rollout_ignores_non_persistent_breach() -> None:
    result = evaluate_rollout.evaluate(
        {
            "stage": "canary",
            "windows": [
                _window(api_5xx_ratio={"canary": 0.02, "stable": 0.001}),
                _window(),
            ],
        },
        required_windows=2,
    )

    assert result["decision"] == "continue"
    assert result["windows"][0]["breaches"] == ["api_5xx_ratio"]


def test_rollout_halts_for_persistent_breach() -> None:
    failed = _window(browser_smoke_pass=False)

    result = evaluate_rollout.evaluate(
        {"stage": "canary", "windows": [failed, failed]},
        required_windows=2,
    )

    assert result["decision"] == "halt"
    assert result["persistent_breaches"] == ["browser_smoke_pass"]


def test_rollout_rejects_incomplete_signal_windows() -> None:
    with pytest.raises(ValueError, match="missing comparison signal"):
        evaluate_rollout.evaluate(
            {"windows": [{"signals": {"browser_smoke_pass": True}}]},
            required_windows=1,
        )


def test_release_evidence_is_version_checked_and_hash_addressed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path
    artifact = root / "verification.json"
    artifact.write_text('{"passed": true}\n', encoding="utf-8")
    (root / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    output = root / "release-evidence.json"
    monkeypatch.setattr(build_release_evidence, "ROOT", root)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_release_evidence.py",
            "--version",
            "0.1.0",
            "--source-sha",
            "a" * 40,
            "--stage",
            "canary",
            "--artifact",
            artifact.name,
            "--image-digest",
            f"api=sha256:{'b' * 64}",
            "--output",
            str(output),
        ],
    )

    assert build_release_evidence.main() == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["version"] == "0.1.0"
    assert document["image_digests"]["api"] == f"sha256:{'b' * 64}"
    assert document["artifacts"] == [
        {
            "path": artifact.name,
            "bytes": len(artifact.read_bytes()),
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        }
    ]


def test_release_evidence_rejects_mismatched_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    artifact = tmp_path / "verification.json"
    artifact.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(build_release_evidence, "ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_release_evidence.py",
            "--version",
            "0.2.0",
            "--source-sha",
            "a" * 40,
            "--stage",
            "internal",
            "--artifact",
            artifact.name,
        ],
    )

    with pytest.raises(SystemExit, match="does not match VERSION"):
        build_release_evidence.main()
