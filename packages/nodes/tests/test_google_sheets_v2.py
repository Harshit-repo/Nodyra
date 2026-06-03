import json

import noodle_nodes  # noqa: F401 - registers built-in and v2 nodes
from noodle.sdk import registry


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

    read = manifests["google_sheets_read_v2"]
    append = manifests["google_sheets_append_v2"]
    assert read.name == "Google Sheets Read V2"
    assert append.name == "Google Sheets Append V2"
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
