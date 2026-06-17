"""Tests for the consolidated (Resource + Operation) integration node machinery."""

from __future__ import annotations

import pytest

import noodle_nodes  # noqa: F401 - importing registers provider nodes
from noodle.sdk import registry
from noodle_nodes.integrations_v2.registry import (
    execute_integration_operation,
    resolve_operation_node_id,
)


def test_consolidated_nodes_replace_per_operation_nodes() -> None:
    ids = {manifest.id for manifest in registry.manifests()}
    assert "google_sheets" in ids
    assert "slack" in ids
    # Breaking change: the old per-operation palette nodes are gone (provider
    # trigger nodes like google_sheets_new_row_trigger_v2 are unaffected).
    removed = {
        "google_sheets_read_v2",
        "google_sheets_append_v2",
        "google_sheets_upsert_row_v2",
        "slack_send_message_v2",
        "slack_list_channels_v2",
        "slack_open_direct_message_v2",
    }
    assert removed.isdisjoint(ids)


def test_resolve_operation_node_id_maps_selection_to_executor() -> None:
    assert resolve_operation_node_id("slack", "message", "send") == "slack_send_message_v2"
    assert (
        resolve_operation_node_id("google_sheets", "values", "append")
        == "google_sheets_append_v2"
    )


def test_resolve_operation_node_id_rejects_unknown_selection() -> None:
    with pytest.raises(KeyError):
        resolve_operation_node_id("slack", "message", "nope")


def test_execute_integration_operation_filters_unknown_kwargs(monkeypatch) -> None:
    from noodle_nodes.integrations_v2.providers.slack import operations

    captured: dict = {}

    def fake_transport(_credentials):
        class _T:
            def request(self, *args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs
                return {"ok": True}

        return _T()

    monkeypatch.setattr(operations, "_transport", fake_transport)

    # Pass extra params that belong to OTHER operations; the dispatcher must drop
    # them before calling send_message (whose signature would reject them).
    result = execute_integration_operation(
        "slack",
        "message",
        "send",
        input=None,
        credentials={"bot_token": "x"},
        channel="C1",
        text="hi",
        ts="999",  # belongs to update/delete, not send
        reaction_name="thumbsup",  # belongs to reaction.add
    )
    assert result == {"ok": True}
    assert captured["kwargs"]["json_body"]["channel"] == "C1"


def test_consolidated_node_dispatch_runs_executor(monkeypatch) -> None:
    from noodle_nodes.integrations_v2.providers.slack import operations

    class _T:
        def request(self, *args, **kwargs):
            return {"ok": True, "ts": "1.2"}

    monkeypatch.setattr(operations, "_transport", lambda _c: _T())

    result = registry.get("slack").func(
        resource="message",
        operation="send",
        input=None,
        credentials={"bot_token": "x"},
        channel="C1",
        text="hello",
    )
    assert result == {"ok": True, "ts": "1.2"}
