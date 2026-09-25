from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


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


@pytest.mark.parametrize("key_restored", [True, False])
async def test_restore_evidence_rejects_missing_org_key_even_if_legacy_credential_decrypts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key_restored: bool
) -> None:
    recovery = _load_module()
    manifest = tmp_path / "manifest.json"
    evidence = tmp_path / "evidence.json"
    recovery._write_json(
        manifest,
        {
            "workflow_id": recovery.WORKFLOW_ID,
            "run_id": recovery.RUN_ID,
            "credential_id": recovery.CREDENTIAL_ID,
            "seeded_at": datetime.now(UTC).isoformat(),
            "artifact": {
                "id": recovery.ARTIFACT_ID,
                "key": recovery.ARTIFACT_KEY,
                "checksum_sha256": recovery.ARTIFACT_CHECKSUM,
                "size_bytes": len(recovery.ARTIFACT_BYTES),
            },
        },
    )
    session = AsyncMock()
    session.get.side_effect = [
        SimpleNamespace(draft_graph=recovery.FIXTURE_GRAPH),
        SimpleNamespace(status="success"),
        SimpleNamespace(),
        SimpleNamespace(
            checksum_sha256=recovery.ARTIFACT_CHECKSUM, storage_key=recovery.ARTIFACT_KEY
        ),
    ]
    context = AsyncMock()
    context.__aenter__.return_value = session
    monkeypatch.setattr(recovery, "SessionLocal", Mock(return_value=context))
    monkeypatch.setattr(recovery.org_keys, "invalidate_kek_cache", Mock())
    monkeypatch.setattr(
        recovery.org_keys,
        "batch_get_org_keks",
        AsyncMock(
            return_value={recovery.DEFAULT_ORG_ID: b"restored-key" if key_restored else None},
        ),
    )
    monkeypatch.setattr(
        recovery.org_keys,
        "decrypt_credential_for",
        AsyncMock(
            return_value=recovery.FIXTURE_SECRET,
        ),
    )
    client = Mock()
    client.get_object.return_value = {"Body": BytesIO(recovery.ARTIFACT_BYTES)}
    monkeypatch.setattr(recovery, "_s3_client", Mock(return_value=client))
    if key_restored:
        await recovery.verify_fixture(str(manifest), str(evidence), 1, 3600)
    else:
        with pytest.raises(RuntimeError, match="organization_key_decryptable"):
            await recovery.verify_fixture(str(manifest), str(evidence), 1, 3600)
    checks = json.loads(evidence.read_text())["checks"]
    assert checks["credential_decryptable"] is True
    assert checks["organization_key_decryptable"] is key_restored
