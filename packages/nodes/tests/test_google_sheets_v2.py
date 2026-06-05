import json
from typing import Any
from unittest.mock import MagicMock

import noodle_nodes  # noqa: F401 - registers built-in and v2 nodes
from noodle.sdk import registry
from noodle_nodes.integrations_v2.providers.google_sheets import operations


def _mock_transport(return_value: Any) -> MagicMock:
    transport = MagicMock()
    transport.request.return_value = return_value
    return transport


class FakeResponse:
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.text = json.dumps(payload)
        self.content = self.text.encode()

    def json(self) -> dict:
        return self._payload


def test_google_sheets_v2_nodes_are_registered() -> None:
    manifests = {manifest.id: manifest for manifest in registry.manifests()}
    expected = {
        "google_sheets_read_v2": ("Google Sheets Read", False),
        "google_sheets_append_v2": ("Google Sheets Append", True),
        "google_sheets_update_v2": ("Google Sheets Update", True),
        "google_sheets_clear_v2": ("Google Sheets Clear", True),
        "google_sheets_get_metadata_v2": ("Google Sheets Get Metadata", False),
        "google_sheets_create_spreadsheet_v2": (
            "Google Sheets Create Spreadsheet",
            True,
        ),
        "google_sheets_batch_update_values_v2": (
            "Google Sheets Batch Update Values",
            True,
        ),
        "google_sheets_add_sheet_v2": ("Google Sheets Add Sheet", True),
        "google_sheets_rename_sheet_v2": ("Google Sheets Rename Sheet", True),
        "google_sheets_delete_sheet_v2": ("Google Sheets Delete Sheet", True),
        "google_sheets_lookup_rows_v2": ("Google Sheets Lookup Rows", False),
        "google_sheets_upsert_row_v2": ("Google Sheets Upsert Row", True),
    }

    for node_id, (name, side_effecting) in expected.items():
        manifest = manifests[node_id]
        assert manifest.name == name
        assert manifest.icon == "brand:googlesheets"
        assert manifest.category == "Integrations"
        assert manifest.usable_as_tool is True
        assert manifest.tool_side_effecting is side_effecting

    read = manifests["google_sheets_read_v2"]
    append = manifests["google_sheets_append_v2"]
    assert read.name == "Google Sheets Read"
    assert append.name == "Google Sheets Append"
    assert read.icon == "brand:googlesheets"
    assert append.icon == "brand:googlesheets"
    assert read.category == "Integrations"

    append_params = {param.name: param for param in append.params}
    credentials = append_params["credentials"]
    assert credentials.credential is not None
    assert credentials.credential.type == "google_sheets_oauth2"
    assert credentials.credential.multi is True
    assert credentials.required_scopes == [
        "https://www.googleapis.com/auth/spreadsheets"
    ]
    assert append_params["value_input_option"].group == "Options"


def test_google_sheets_v2_generated_source_is_available() -> None:
    node_def = registry.get("google_sheets_append_v2")
    source = getattr(node_def.func, "__noodle_source__", "")

    assert "def google_sheets_append_v2(" in source
    assert "execute_registered_operation" in source
    assert "google_sheets.values.append" in source


def test_google_sheets_read_v2_uses_transport(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return FakeResponse({"values": [["A1"]]})

    monkeypatch.setattr("requests.request", fake_request)
    result = registry.get("google_sheets_read_v2").func(
        input=None,
        credentials={"access_token": "token"},
        spreadsheet_id="sheet-id",
        range_name="Sheet1!A1:B2",
    )

    assert result == {"values": [["A1"]]}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/spreadsheets/sheet-id/values/Sheet1!A1:B2")
    assert calls[0]["kwargs"]["headers"]["Authorization"] == "Bearer token"


def test_google_sheets_append_v2_derives_rows_from_input(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return FakeResponse({"updates": {"updatedRows": 1}})

    monkeypatch.setattr("requests.request", fake_request)
    result = registry.get("google_sheets_append_v2").func(
        input={"Name": "Ada", "Email": "ada@example.test"},
        credentials={"access_token": "token"},
        spreadsheet_id="sheet-id",
        range_name="Sheet1!A:B",
        values=None,
        value_input_option="USER_ENTERED",
    )

    assert result == {"updates": {"updatedRows": 1}}
    assert calls[0]["method"] == "POST"
    assert calls[0]["kwargs"]["params"] == {"valueInputOption": "USER_ENTERED"}
    assert calls[0]["kwargs"]["json"] == {
        "values": [["Ada", "ada@example.test"]]
    }


def test_google_sheets_create_and_sheet_batch_nodes(monkeypatch) -> None:
    transport = _mock_transport({"spreadsheetId": "sheet-id"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("google_sheets_create_spreadsheet_v2").func(
        input=None,
        credentials={"access_token": "token"},
        title="Pipeline",
        locale="en_US",
        time_zone="Australia/Sydney",
    )
    registry.get("google_sheets_add_sheet_v2").func(
        input=None,
        credentials={"access_token": "token"},
        spreadsheet_id="sheet-id",
        title="Runs",
        row_count=50,
        column_count=10,
    )
    registry.get("google_sheets_rename_sheet_v2").func(
        input=None,
        credentials={"access_token": "token"},
        spreadsheet_id="sheet-id",
        sheet_id=123,
        title="Archive",
    )
    registry.get("google_sheets_delete_sheet_v2").func(
        input=None,
        credentials={"access_token": "token"},
        spreadsheet_id="sheet-id",
        sheet_id=123,
    )

    assert transport.request.call_args_list[0].args == ("POST", "/spreadsheets")
    assert transport.request.call_args_list[0].kwargs == {
        "operation": "create_spreadsheet",
        "json_body": {
            "properties": {
                "title": "Pipeline",
                "locale": "en_US",
                "timeZone": "Australia/Sydney",
            }
        },
    }
    assert transport.request.call_args_list[1].kwargs["json_body"] == {
        "requests": [
            {
                "addSheet": {
                    "properties": {
                        "title": "Runs",
                        "gridProperties": {"rowCount": 50, "columnCount": 10},
                    }
                }
            }
        ]
    }
    assert transport.request.call_args_list[2].kwargs["json_body"] == {
        "requests": [
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": 123, "title": "Archive"},
                    "fields": "title",
                }
            }
        ]
    }
    assert transport.request.call_args_list[3].kwargs["json_body"] == {
        "requests": [{"deleteSheet": {"sheetId": 123}}]
    }


def test_google_sheets_batch_update_values_v2_uses_input(monkeypatch) -> None:
    transport = _mock_transport({"totalUpdatedRows": 2})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("google_sheets_batch_update_values_v2").func(
        input={"data": [{"range": "Sheet1!A1:B1", "values": [["A", "B"]]}]},
        credentials={"access_token": "token"},
        spreadsheet_id="sheet-id",
    )

    transport.request.assert_called_once_with(
        "POST",
        "/spreadsheets/sheet-id/values:batchUpdate",
        operation="batch_update_values",
        json_body={
            "valueInputOption": "USER_ENTERED",
            "data": [{"range": "Sheet1!A1:B1", "values": [["A", "B"]]}],
        },
    )


def test_google_sheets_lookup_rows_v2_returns_records(monkeypatch) -> None:
    transport = _mock_transport(
        {
            "values": [
                ["Email", "Name", "Status"],
                ["ada@example.test", "Ada", "Open"],
                ["grace@example.test", "Grace", "Closed"],
            ]
        }
    )
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = registry.get("google_sheets_lookup_rows_v2").func(
        input=None,
        credentials={"access_token": "token"},
        spreadsheet_id="sheet-id",
        range_name="Sheet1!A1:C",
        key_column="Email",
        key_value="ada@example.test",
    )

    assert result == {
        "matches": [
            {
                "row_number": 2,
                "values": ["ada@example.test", "Ada", "Open"],
                "record": {
                    "Email": "ada@example.test",
                    "Name": "Ada",
                    "Status": "Open",
                },
            }
        ],
        "match_count": 1,
        "headers": ["Email", "Name", "Status"],
    }


def test_google_sheets_upsert_row_v2_updates_or_appends(monkeypatch) -> None:
    transport = MagicMock()
    transport.request.side_effect = [
        {"values": [["Email", "Name"], ["ada@example.test", "Ada"]]},
        {"updatedRows": 1},
        {"values": [["Email", "Name"], ["ada@example.test", "Ada"]]},
        {"updates": {"updatedRows": 1}},
    ]
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    updated = registry.get("google_sheets_upsert_row_v2").func(
        input=None,
        credentials={"access_token": "token"},
        spreadsheet_id="sheet-id",
        range_name="People Sheet!A1:B",
        key_column="Email",
        key_value="ada@example.test",
        row_values={"Name": "Ada Updated"},
    )
    appended = registry.get("google_sheets_upsert_row_v2").func(
        input=None,
        credentials={"access_token": "token"},
        spreadsheet_id="sheet-id",
        range_name="People Sheet!A1:B",
        key_column="Email",
        key_value="grace@example.test",
        row_values={"Name": "Grace"},
    )

    assert updated["action"] == "updated"
    assert appended["action"] == "appended"
    assert transport.request.call_args_list[1].args == (
        "PUT",
        "/spreadsheets/sheet-id/values/'People%20Sheet'!A2:B2",
    )
    assert transport.request.call_args_list[1].kwargs["json_body"] == {
        "values": [["ada@example.test", "Ada Updated"]],
        "range": "'People Sheet'!A2:B2",
    }
    assert transport.request.call_args_list[3].args == (
        "POST",
        "/spreadsheets/sheet-id/values/People%20Sheet!A1:B:append",
    )
