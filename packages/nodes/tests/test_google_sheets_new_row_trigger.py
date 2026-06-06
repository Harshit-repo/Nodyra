"""Tests for the Google Sheets new-row polling trigger."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import noodle_nodes  # noqa: F401
from noodle.sdk import registry
from noodle_nodes.integrations_v2.providers.google_sheets import triggers as gs_triggers
from noodle_nodes.integrations_v2.specs import ProviderTriggerPollContext


def _mock_transport(return_value):
    t = MagicMock()
    t.request.return_value = return_value
    return t


def test_google_sheets_new_row_trigger_is_registered() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    assert "google_sheets_new_row_trigger_v2" in manifests
    m = manifests["google_sheets_new_row_trigger_v2"]
    assert m.name == "Google Sheets New Row"
    assert m.category == "Triggers"


def test_poll_first_run_establishes_cursor_no_events() -> None:
    """On first poll (empty cursor) no events should fire — just sets the baseline."""
    mock_t = _mock_transport(
        {"values": [["Name", "Age"], ["Alice", "30"], ["Bob", "25"]]}
    )
    with patch(
        "noodle_nodes.integrations_v2.providers.google_sheets.triggers._transport",
        return_value=mock_t,
    ):
        result = gs_triggers.poll_new_rows(
            ProviderTriggerPollContext(
                params={
                    "credentials": {"access_token": "tok"},
                    "spreadsheet_id": "sheet1",
                    "sheet_name": "Sheet1",
                    "header_row": 1,
                },
                cursor={},
            )
        )
    assert result.events == []
    assert result.cursor["last_row_index"] == 2


def test_poll_detects_new_rows() -> None:
    """Rows beyond last_row_index should be returned as events."""
    mock_t = _mock_transport(
        {
            "values": [
                ["Name", "Age"],
                ["Alice", "30"],
                ["Bob", "25"],
                ["Carol", "28"],
            ]
        }
    )
    with patch(
        "noodle_nodes.integrations_v2.providers.google_sheets.triggers._transport",
        return_value=mock_t,
    ):
        result = gs_triggers.poll_new_rows(
            ProviderTriggerPollContext(
                params={
                    "credentials": {"access_token": "tok"},
                    "spreadsheet_id": "sheet1",
                    "sheet_name": "Sheet1",
                    "header_row": 1,
                },
                cursor={"last_row_index": 2},
            )
        )
    assert len(result.events) == 1
    assert result.events[0]["row"] == {"Name": "Carol", "Age": "28"}
    assert result.cursor["last_row_index"] == 3


def test_poll_no_new_rows_returns_empty() -> None:
    mock_t = _mock_transport({"values": [["Name", "Age"], ["Alice", "30"]]})
    with patch(
        "noodle_nodes.integrations_v2.providers.google_sheets.triggers._transport",
        return_value=mock_t,
    ):
        result = gs_triggers.poll_new_rows(
            ProviderTriggerPollContext(
                params={
                    "credentials": {"access_token": "tok"},
                    "spreadsheet_id": "sheet1",
                    "sheet_name": "Sheet1",
                    "header_row": 1,
                },
                cursor={"last_row_index": 1},
            )
        )
    assert result.events == []
    assert result.cursor["last_row_index"] == 1
