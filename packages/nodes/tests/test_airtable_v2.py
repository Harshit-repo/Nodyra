from typing import Any
from unittest.mock import MagicMock

import pytest

import noodle_nodes  # noqa: F401 - importing registers provider nodes
from noodle.sdk import registry
from noodle_nodes.integrations_v2.providers.airtable import operations


def _mock_transport(return_value: Any) -> MagicMock:
    transport = MagicMock()
    transport.request.return_value = return_value
    return transport


def test_airtable_v2_nodes_are_registered() -> None:
    manifests = {manifest.id: manifest for manifest in registry.manifests()}
    list_records = manifests["airtable_list_records_v2"]
    create_record = manifests["airtable_create_record_v2"]

    assert list_records.category == "Integrations"
    assert create_record.category == "Integrations"
    params = {param.name: param for param in create_record.params}
    assert params["credentials"].credential is not None
    assert params["credentials"].credential.type == "airtable"
    assert params["credentials"].credential.multi is True
    assert params["credentials"].credential.test_service == "airtable"
    assert params["typecast"].group == "Options"


def test_airtable_v2_generated_source_is_available() -> None:
    source = getattr(
        registry.get("airtable_create_record_v2").func,
        "__noodle_source__",
        "",
    )

    assert "def airtable_create_record_v2(" in source
    assert "credentials=None" in source
    assert "execute_registered_operation" in source
    assert "airtable.record.create" in source


def test_airtable_list_records_v2_builds_query(monkeypatch) -> None:
    transport = _mock_transport({"records": []})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = registry.get("airtable_list_records_v2").func(
        input=None,
        credentials={"token": "pat-test"},
        base_id="app123",
        table_name="Tasks Table",
        view="Open",
        max_records=25,
        filter_formula="{Status} = 'Open'",
    )

    assert result == {"records": []}
    transport.request.assert_called_once_with(
        "GET",
        "/app123/Tasks%20Table",
        operation="list_records",
        params={
            "maxRecords": 25,
            "view": "Open",
            "filterByFormula": "{Status} = 'Open'",
        },
    )


def test_airtable_create_record_v2_uses_input_fields(monkeypatch) -> None:
    transport = _mock_transport({"id": "rec123"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = registry.get("airtable_create_record_v2").func(
        input={"Name": "Ada", "Status": "Open"},
        credentials={"token": "pat-test"},
        base_id="app123",
        table_name="Tasks",
        typecast=True,
    )

    assert result == {"id": "rec123"}
    transport.request.assert_called_once_with(
        "POST",
        "/app123/Tasks",
        operation="create_record",
        json_body={
            "fields": {"Name": "Ada", "Status": "Open"},
            "typecast": True,
        },
    )


def test_airtable_v2_requires_base_and_table() -> None:
    with pytest.raises(ValueError, match="base_id"):
        operations.list_records(credentials={"token": "pat-test"}, base_id="", table_name="Tasks")
