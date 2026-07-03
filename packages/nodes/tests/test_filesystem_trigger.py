"""Tests for the file-change polling trigger."""
from __future__ import annotations

import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import nodyra_nodes  # noqa: F401
from nodyra.sdk import registry
from nodyra_nodes.integrations_v2.providers.filesystem import triggers as fs_triggers
from nodyra_nodes.integrations_v2.specs import ProviderTriggerPollContext


def _ctx(params: dict, cursor: dict) -> ProviderTriggerPollContext:
    return ProviderTriggerPollContext(params=params, cursor=cursor)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_file_change_trigger_is_registered() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    assert "file_change_trigger" in manifests
    m = manifests["file_change_trigger"]
    assert m.name == "File Change"
    assert m.category == "Triggers"


# ---------------------------------------------------------------------------
# display_when on params
# ---------------------------------------------------------------------------


def test_params_have_display_when() -> None:
    spec = fs_triggers.FILE_CHANGE_TRIGGER_SPEC
    by_name = {p.name: p for p in spec.params}
    assert by_name["watch_path"].display_when == {"param": "source_type", "value": "local"}
    assert by_name["bucket"].display_when == {"param": "source_type", "values": ["s3", "gcs"]}
    assert by_name["s3_credentials"].display_when == {"param": "source_type", "value": "s3"}
    assert by_name["gcs_credentials"].display_when == {"param": "source_type", "value": "gcs"}


# ---------------------------------------------------------------------------
# Local filesystem
# ---------------------------------------------------------------------------


def test_local_first_run_no_events(tmp_path: Path) -> None:
    """First run initialises the cursor with no events fired."""
    (tmp_path / "a.txt").write_text("hello")
    result = fs_triggers.poll_file_changes(
        _ctx({"source_type": "local", "watch_path": str(tmp_path)}, {})
    )
    assert result.events == []
    assert len(result.cursor["files"]) == 1


def test_local_new_file_fires_created(tmp_path: Path) -> None:
    # First run — baseline
    result = fs_triggers.poll_file_changes(
        _ctx({"source_type": "local", "watch_path": str(tmp_path)}, {})
    )
    cursor = result.cursor

    # Add a new file
    (tmp_path / "new.txt").write_text("new content")
    result2 = fs_triggers.poll_file_changes(
        _ctx({"source_type": "local", "watch_path": str(tmp_path)}, cursor)
    )
    assert len(result2.events) == 1
    ev = result2.events[0]
    assert ev["event_type"] == "created"
    assert ev["filename"] == "new.txt"
    assert ev["source_type"] == "local"
    assert "size_bytes" in ev
    assert "modified_at" in ev


def test_local_modified_file_fires_modified(tmp_path: Path) -> None:
    f = tmp_path / "data.txt"
    f.write_text("original")
    result = fs_triggers.poll_file_changes(
        _ctx({"source_type": "local", "watch_path": str(tmp_path)}, {})
    )
    cursor = result.cursor

    # Modify the file — change mtime by touching it with new content
    time.sleep(0.01)
    f.write_text("modified content that is longer")
    result2 = fs_triggers.poll_file_changes(
        _ctx({"source_type": "local", "watch_path": str(tmp_path)}, cursor)
    )
    assert any(e["event_type"] == "modified" for e in result2.events)


def test_local_glob_pattern_filters_files(tmp_path: Path) -> None:
    (tmp_path / "report.csv").write_text("a,b")
    (tmp_path / "notes.txt").write_text("notes")
    # First run to set baseline
    result = fs_triggers.poll_file_changes(
        _ctx({"source_type": "local", "watch_path": str(tmp_path), "glob_pattern": "*.csv"}, {})
    )
    cursor = result.cursor
    assert len(cursor["files"]) == 1
    assert any("report.csv" in k for k in cursor["files"])

    # Add a new csv and a txt; only csv should be tracked
    (tmp_path / "new.csv").write_text("c,d")
    (tmp_path / "extra.txt").write_text("extra")
    result2 = fs_triggers.poll_file_changes(
        _ctx({"source_type": "local", "watch_path": str(tmp_path), "glob_pattern": "*.csv"}, cursor)
    )
    assert len(result2.events) == 1
    assert result2.events[0]["filename"] == "new.csv"


def test_local_missing_watch_path_raises() -> None:
    import pytest
    with pytest.raises((ValueError, FileNotFoundError)):
        fs_triggers.poll_file_changes(
            _ctx({"source_type": "local", "watch_path": ""}, {})
        )


# ---------------------------------------------------------------------------
# S3 (mocked — boto3 injected via sys.modules since it's not installed)
# ---------------------------------------------------------------------------


def _make_fake_boto3(fake_session: MagicMock) -> types.ModuleType:
    """Build a minimal boto3 stub that returns fake_session from Session()."""
    mod = types.ModuleType("boto3")
    mod.Session = MagicMock(return_value=fake_session)  # type: ignore[attr-defined]
    return mod


def test_s3_first_run_no_events() -> None:
    fake_paginator = MagicMock()
    fake_paginator.paginate.return_value = [
        {"Contents": [{"Key": "uploads/file.csv", "Size": 100, "ETag": '"abc"'}]}
    ]
    fake_s3 = MagicMock()
    fake_s3.get_paginator.return_value = fake_paginator
    fake_session = MagicMock()
    fake_session.client.return_value = fake_s3

    with patch.dict(sys.modules, {"boto3": _make_fake_boto3(fake_session)}):
        result = fs_triggers.poll_file_changes(
            _ctx({"source_type": "s3", "bucket": "my-bucket", "prefix": "uploads/"}, {})
        )
    assert result.events == []
    assert "uploads/file.csv" in result.cursor["files"]


def test_s3_new_object_fires_created() -> None:
    fake_paginator = MagicMock()
    fake_paginator.paginate.return_value = [
        {"Contents": [
            {"Key": "uploads/existing.csv", "Size": 50, "ETag": '"old"'},
            {"Key": "uploads/new.csv", "Size": 200, "ETag": '"fresh"'},
        ]}
    ]
    fake_s3 = MagicMock()
    fake_s3.get_paginator.return_value = fake_paginator
    fake_session = MagicMock()
    fake_session.client.return_value = fake_s3

    prev_cursor = {"files": {"uploads/existing.csv": [50, "old"]}}
    with patch.dict(sys.modules, {"boto3": _make_fake_boto3(fake_session)}):
        result = fs_triggers.poll_file_changes(
            _ctx({"source_type": "s3", "bucket": "my-bucket"}, prev_cursor)
        )
    assert len(result.events) == 1
    ev = result.events[0]
    assert ev["event_type"] == "created"
    assert ev["key"] == "uploads/new.csv"
    assert ev["source_type"] == "s3"
