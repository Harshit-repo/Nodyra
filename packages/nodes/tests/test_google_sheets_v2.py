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


def _run(resource: str, operation: str, **kwargs: Any) -> Any:
    """Invoke the consolidated Google Sheets node for a resource + operation."""
    return registry.get("google_sheets").func(
        resource=resource, operation=operation, **kwargs
    )


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
    # The per-operation nodes are consolidated into a single "google_sheets" node.
    assert "google_sheets_read_v2" not in manifests
    assert "google_sheets_append_v2" not in manifests

    node = manifests["google_sheets"]
    assert node.name == "Google Sheets"
    assert node.icon == "brand:googlesheets"
    assert node.category == "Integrations"
    assert node.usable_as_tool is True
    assert node.integration is not None

    resources = {r.id: [op.id for op in r.operations] for r in node.integration.resources}
    assert resources["values"] == ["read", "append", "update", "clear", "batch_update"]
    assert resources["spreadsheet"] == ["get_metadata", "create"]
    assert resources["sheet"] == ["add", "rename", "delete"]
    assert resources["row"] == ["lookup", "upsert"]

    params = {param.name: param for param in node.params}
    credentials = params["credentials"]
    assert credentials.credential is not None
    assert credentials.credential.type == "google_sheets_oauth2"
    assert credentials.credential.multi is True
    assert credentials.required_scopes == [
        "https://www.googleapis.com/auth/spreadsheets"
    ]
    # value_input_option is append-only; it carries its Options group and a
    # display_when gating it to the (values, append) selection.
    assert params["value_input_option"].group == "Options"
    assert params["value_input_option"].display_when is not None
    # The sheet/tab picker is a dynamic dropdown that cascades off the chosen
    # spreadsheet (and credential).
    sheet = params["sheet_name"]
    assert sheet.load_options == "google_sheets.list_sheet_names"
    assert "credentials" in sheet.depends_on
    assert "spreadsheet_id" in sheet.depends_on


def test_google_sheets_v2_generated_source_is_available() -> None:
    node_def = registry.get("google_sheets")
    source = getattr(node_def.func, "__noodle_source__", "")

    assert "def google_sheets(" in source
    assert "execute_integration_operation" in source
    assert "resource" in source
    assert "operation" in source


def test_google_sheets_read_v2_uses_transport(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return FakeResponse({"values": [["A1"]]})

    monkeypatch.setattr("requests.request", fake_request)
    result = _run(
        "values",
        "read",
        input=None,
        credentials={"access_token": "token"},
        spreadsheet_id="sheet-id",
        range_name="Sheet1!A1:B2",
    )

    assert result == {"values": [["A1"]]}
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/spreadsheets/sheet-id/values/Sheet1!A1:B2")
    assert calls[0]["kwargs"]["headers"]["Authorization"] == "Bearer token"


def test_google_sheets_sheet_name_qualifies_a_sheetless_range(monkeypatch) -> None:
    """Picking a sheet from the dropdown + a bare cell range builds Sheet!Range;
    a range that already names a sheet is left untouched (back-compat)."""
    calls: list[dict] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append({"url": url})
        return FakeResponse({"values": [["A1"]]})

    monkeypatch.setattr("requests.request", fake_request)

    _run(
        "values",
        "read",
        credentials={"access_token": "token"},
        spreadsheet_id="sheet-id",
        sheet_name="Q3 Data",
        range_name="A1:B2",
    )
    # Sheet with a space gets quoted, then URL-encoded by the transport.
    assert calls[0]["url"].endswith("/values/'Q3%20Data'!A1:B2")

    _run(
        "values",
        "read",
        credentials={"access_token": "token"},
        spreadsheet_id="sheet-id",
        sheet_name="Ignored",
        range_name="Other!A1:B2",
    )
    assert calls[1]["url"].endswith("/values/Other!A1:B2")


def test_google_sheets_append_v2_derives_rows_from_input(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return FakeResponse({"updates": {"updatedRows": 1}})

    monkeypatch.setattr("requests.request", fake_request)
    result = _run(
        "values",
        "append",
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

    _run(
        "spreadsheet",
        "create",
        input=None,
        credentials={"access_token": "token"},
        title="Pipeline",
        locale="en_US",
        time_zone="Australia/Sydney",
    )
    _run(
        "sheet",
        "add",
        input=None,
        credentials={"access_token": "token"},
        spreadsheet_id="sheet-id",
        title="Runs",
        row_count=50,
        column_count=10,
    )
    _run(
        "sheet",
        "rename",
        input=None,
        credentials={"access_token": "token"},
        spreadsheet_id="sheet-id",
        sheet_id=123,
        title="Archive",
    )
    _run(
        "sheet",
        "delete",
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

    _run(
        "values",
        "batch_update",
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

    result = _run(
        "row",
        "lookup",
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

    updated = _run(
        "row",
        "upsert",
        input=None,
        credentials={"access_token": "token"},
        spreadsheet_id="sheet-id",
        range_name="People Sheet!A1:B",
        key_column="Email",
        key_value="ada@example.test",
        row_values={"Name": "Ada Updated"},
    )
    appended = _run(
        "row",
        "upsert",
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
