"""Tests for File Watcher polling trigger."""

from __future__ import annotations

import pytest

from noodle_nodes.integrations_v2.providers.filesystem_triggers.triggers import (
    _matches_patterns,
    poll_file_watcher,
)
from noodle_nodes.integrations_v2.registry import is_registered_provider_trigger
from noodle_nodes.integrations_v2.specs import ProviderTriggerPollContext


def _ctx(params: dict, cursor: dict | None = None) -> ProviderTriggerPollContext:
    return ProviderTriggerPollContext(params=params, cursor=cursor or {})


_PARAMS = {"directory": "/fake/dir", "events": "create,modify,delete"}


def _fake_stat(size: int = 100, mtime: float = 1_000_000.0):
    class _St:
        st_size = size
        st_mtime = mtime
    return _St()


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


def test_matches_patterns_wildcard():
    assert _matches_patterns("report.csv", ["*"])
    assert _matches_patterns("anything.txt", [])


def test_matches_patterns_specific():
    assert _matches_patterns("report.csv", ["*.csv", "*.json"])
    assert not _matches_patterns("report.txt", ["*.csv", "*.json"])


# ---------------------------------------------------------------------------
# Poll — first run
# ---------------------------------------------------------------------------


def test_first_run_seeds_without_events(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    ctx = _ctx({"directory": str(tmp_path), "events": "create"})
    result = poll_file_watcher(ctx)
    assert result.events == []
    assert "snapshot" in result.cursor
    assert any(str(tmp_path / "a.txt") in k for k in result.cursor["snapshot"])


# ---------------------------------------------------------------------------
# Poll — create event
# ---------------------------------------------------------------------------


def test_create_event_detected(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    # Seed snapshot with no files
    ctx = _ctx(
        {"directory": str(tmp_path), "events": "create"},
        cursor={"snapshot": {}},
    )
    result = poll_file_watcher(ctx)
    assert len(result.events) == 1
    evt = result.events[0]
    assert evt["event"] == "create"
    assert evt["filename"] == "a.txt"


def test_modify_event_detected(tmp_path):
    f = tmp_path / "b.txt"
    f.write_text("original")
    old_mtime = f.stat().st_mtime - 10  # simulate old mtime
    ctx = _ctx(
        {"directory": str(tmp_path), "events": "modify"},
        cursor={"snapshot": {str(f): old_mtime}},
    )
    result = poll_file_watcher(ctx)
    assert any(e["event"] == "modify" for e in result.events)


def test_delete_event_detected(tmp_path):
    # Snapshot has file but directory doesn't
    f_path = str(tmp_path / "gone.txt")
    ctx = _ctx(
        {"directory": str(tmp_path), "events": "delete"},
        cursor={"snapshot": {f_path: 1_000_000.0}},
    )
    result = poll_file_watcher(ctx)
    assert any(e["event"] == "delete" and e["path"] == f_path for e in result.events)


def test_no_events_when_nothing_changed(tmp_path):
    f = tmp_path / "stable.txt"
    f.write_text("unchanged")
    mtime = f.stat().st_mtime
    ctx = _ctx(
        {"directory": str(tmp_path), "events": "create,modify,delete"},
        cursor={"snapshot": {str(f): mtime}},
    )
    result = poll_file_watcher(ctx)
    assert result.events == []


def test_pattern_filter_excludes_non_matching(tmp_path):
    (tmp_path / "data.csv").write_text("col1,col2")
    (tmp_path / "readme.txt").write_text("readme")
    ctx = _ctx(
        {"directory": str(tmp_path), "events": "create", "patterns": "*.csv"},
        cursor={"snapshot": {}},
    )
    result = poll_file_watcher(ctx)
    assert all(e["filename"].endswith(".csv") for e in result.events)


def test_missing_directory_raises():
    ctx = _ctx({"directory": "/nonexistent/path/xyz", "events": "create"})
    with pytest.raises(ValueError, match="not found"):
        poll_file_watcher(ctx)


def test_missing_directory_param_raises():
    ctx = _ctx({"directory": "", "events": "create"})
    with pytest.raises(ValueError, match="required"):
        poll_file_watcher(ctx)


def test_trigger_registered():
    assert is_registered_provider_trigger("file_watcher_trigger")
