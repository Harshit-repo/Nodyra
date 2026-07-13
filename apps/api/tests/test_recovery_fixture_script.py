from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_module():
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts" / "recovery_fixture.py"
    spec = importlib.util.spec_from_file_location("recovery_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recovery_fixture_ids_and_checksum_are_stable() -> None:
    recovery = _load_module()

    assert len(recovery.WORKFLOW_ID) == 32
    assert len(recovery.RUN_ID) == 32
    assert len(recovery.CREDENTIAL_ID) == 32
    assert len(recovery.ARTIFACT_ID) == 32
    assert recovery.ARTIFACT_CHECKSUM == (
        "1a08f1667b27fba37d2aa810c70a56296d7d4a82472c373a095cb127e1e43975"
    )


def test_recovery_report_write_is_atomic_and_contains_no_secret(tmp_path: Path) -> None:
    recovery = _load_module()
    target = tmp_path / "evidence.json"

    recovery._write_json(target, {"checks": {"credential_decryptable": True}})

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload == {"checks": {"credential_decryptable": True}}
    assert recovery.FIXTURE_SECRET["api_key"] not in target.read_text(encoding="utf-8")
    assert not target.with_suffix(".json.tmp").exists()
