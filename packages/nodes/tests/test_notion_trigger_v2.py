"""Tests for the Notion new database page polling trigger."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import noodle_nodes  # noqa: F401
from noodle.sdk import registry
from noodle_nodes.integrations_v2.providers.notion import triggers as notion_triggers
from noodle_nodes.integrations_v2.specs import ProviderTriggerPollContext


def _mock_transport(return_value):
    t = MagicMock()
    t.request.return_value = return_value
    return t


def test_notion_trigger_is_registered() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    assert "notion_new_database_page_trigger_v2" in manifests
    m = manifests["notion_new_database_page_trigger_v2"]
    assert m.name == "Notion New Database Page"
    assert m.category == "Triggers"


def test_poll_first_run_no_events() -> None:
    """First run with empty cursor fires no events and sets baseline timestamp."""
    pages = [
        {"id": "page1", "created_time": "2024-01-01T10:00:00.000Z", "properties": {}},
        {"id": "page2", "created_time": "2024-01-01T11:00:00.000Z", "properties": {}},
    ]
    mock_t = _mock_transport({"results": pages, "has_more": False})
    with patch(
        "noodle_nodes.integrations_v2.providers.notion.triggers._transport",
        return_value=mock_t,
    ):
        result = notion_triggers.poll_new_pages(
            ProviderTriggerPollContext(
                params={"credentials": {"token": "tok"}, "database_id": "db1"},
                cursor={},
            )
        )
    assert result.events == []
    assert result.cursor["last_created_time"] == "2024-01-01T11:00:00.000Z"
    assert set(result.cursor["seen_ids"]) == {"page1", "page2"}


def test_poll_returns_new_pages_after_cursor() -> None:
    new_pages = [
        {
            "id": "page3",
            "created_time": "2024-01-01T12:00:00.000Z",
            "properties": {
                "Name": {"title": [{"plain_text": "New Entry"}]}
            },
        }
    ]
    mock_t = _mock_transport({"results": new_pages, "has_more": False})
    with patch(
        "noodle_nodes.integrations_v2.providers.notion.triggers._transport",
        return_value=mock_t,
    ):
        result = notion_triggers.poll_new_pages(
            ProviderTriggerPollContext(
                params={"credentials": {"token": "tok"}, "database_id": "db1"},
                cursor={
                    "last_created_time": "2024-01-01T11:00:00.000Z",
                    "seen_ids": ["page1", "page2"],
                },
            )
        )
    assert len(result.events) == 1
    assert result.events[0]["page_id"] == "page3"
    assert result.cursor["last_created_time"] == "2024-01-01T12:00:00.000Z"
    assert "page3" in result.cursor["seen_ids"]


def test_poll_deduplicates_already_seen_ids() -> None:
    """Pages already in seen_ids must not be returned as events."""
    pages = [{"id": "page1", "created_time": "2024-01-01T10:00:00.000Z", "properties": {}}]
    mock_t = _mock_transport({"results": pages, "has_more": False})
    with patch(
        "noodle_nodes.integrations_v2.providers.notion.triggers._transport",
        return_value=mock_t,
    ):
        result = notion_triggers.poll_new_pages(
            ProviderTriggerPollContext(
                params={"credentials": {"token": "tok"}, "database_id": "db1"},
                cursor={
                    "last_created_time": "2024-01-01T09:00:00.000Z",
                    "seen_ids": ["page1"],
                },
            )
        )
    assert result.events == []
