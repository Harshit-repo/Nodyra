from typing import Any
from unittest.mock import MagicMock

import pytest

import nodyra_nodes  # noqa: F401 - importing registers provider nodes
from nodyra.sdk import registry
from nodyra_nodes.integrations_v2.providers.airtable import operations


def _mock_transport(return_value: Any) -> MagicMock:
    transport = MagicMock()
    transport.request.return_value = return_value
    return transport


def test_airtable_v2_nodes_are_registered() -> None:
    manifests = {manifest.id: manifest for manifest in registry.manifests()}
    expected = {
        "airtable_list_records_v2": ("Airtable List Records", False),
        "airtable_create_record_v2": ("Airtable Create Record", True),
        "airtable_get_record_v2": ("Airtable Get Record", False),
        "airtable_update_record_v2": ("Airtable Update Record", True),
        "airtable_delete_record_v2": ("Airtable Delete Record", True),
        "airtable_batch_create_records_v2": ("Airtable Batch Create Records", True),
        "airtable_batch_update_records_v2": ("Airtable Batch Update Records", True),
        "airtable_upsert_records_v2": ("Airtable Upsert Records", True),
    }

    for node_id, (name, side_effecting) in expected.items():
        manifest = manifests[node_id]
        assert manifest.name == name
        assert manifest.icon == "brand:airtable"
        assert manifest.category == "Integrations"
        assert manifest.usable_as_tool is True
        assert manifest.tool_side_effecting is side_effecting

    params = {param.name: param for param in manifests["airtable_create_record_v2"].params}
    assert params["credentials"].credential is not None
    assert params["credentials"].credential.type == "airtable"
    assert params["credentials"].credential.multi is True
    assert params["credentials"].credential.test_service == "airtable"
    assert params["typecast"].group == "Options"


def test_airtable_v2_generated_source_is_available() -> None:
    source = getattr(
        registry.get("airtable_create_record_v2").func,
        "__nodyra_source__",
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


def test_airtable_record_crud_nodes(monkeypatch) -> None:
    transport = _mock_transport({"id": "rec123"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("airtable_get_record_v2").func(
        input=None,
        credentials={"token": "pat-test"},
        base_id="app123",
        table_name="Tasks",
        record_id="rec123",
    )
    registry.get("airtable_update_record_v2").func(
        input={"Status": "Closed"},
        credentials={"token": "pat-test"},
        base_id="app123",
        table_name="Tasks",
        record_id="rec123",
        typecast=True,
    )
    registry.get("airtable_delete_record_v2").func(
        input=None,
        credentials={"token": "pat-test"},
        base_id="app123",
        table_name="Tasks",
        record_id="rec123",
    )

    assert transport.request.call_args_list[0].args == ("GET", "/app123/Tasks/rec123")
    assert transport.request.call_args_list[0].kwargs == {"operation": "get_record"}
    assert transport.request.call_args_list[1].args == ("PATCH", "/app123/Tasks/rec123")
    assert transport.request.call_args_list[1].kwargs == {
        "operation": "update_record",
        "json_body": {"fields": {"Status": "Closed"}, "typecast": True},
    }
    assert transport.request.call_args_list[2].args == ("DELETE", "/app123/Tasks/rec123")
    assert transport.request.call_args_list[2].kwargs == {"operation": "delete_record"}


def test_airtable_batch_create_and_upsert_nodes(monkeypatch) -> None:
    transport = _mock_transport({"records": []})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("airtable_batch_create_records_v2").func(
        input=[{"Name": "Ada"}, {"fields": {"Name": "Grace"}}],
        credentials={"token": "pat-test"},
        base_id="app123",
        table_name="Tasks",
    )
    registry.get("airtable_upsert_records_v2").func(
        input=None,
        credentials={"token": "pat-test"},
        base_id="app123",
        table_name="Tasks",
        fields_to_merge_on="External ID",
        records=[{"External ID": "1", "Name": "Ada"}],
        typecast=True,
    )
    registry.get("airtable_batch_update_records_v2").func(
        input=[{"id": "rec1", "Status": "Closed"}],
        credentials={"token": "pat-test"},
        base_id="app123",
        table_name="Tasks",
    )

    assert transport.request.call_args_list[0].args == ("POST", "/app123/Tasks")
    assert transport.request.call_args_list[0].kwargs == {
        "operation": "batch_create_records",
        "json_body": {
            "records": [
                {"fields": {"Name": "Ada"}},
                {"fields": {"Name": "Grace"}},
            ],
            "typecast": False,
        },
    }
    assert transport.request.call_args_list[1].args == ("PATCH", "/app123/Tasks")
    assert transport.request.call_args_list[1].kwargs == {
        "operation": "upsert_records",
        "json_body": {
            "performUpsert": {"fieldsToMergeOn": ["External ID"]},
            "records": [{"fields": {"External ID": "1", "Name": "Ada"}}],
            "typecast": True,
        },
    }
    assert transport.request.call_args_list[2].args == ("PATCH", "/app123/Tasks")
    assert transport.request.call_args_list[2].kwargs == {
        "operation": "batch_update_records",
        "json_body": {
            "records": [{"id": "rec1", "fields": {"Status": "Closed"}}],
            "typecast": False,
        },
    }


def test_airtable_v2_requires_base_and_table() -> None:
    with pytest.raises(ValueError, match="base_id"):
        operations.list_records(credentials={"token": "pat-test"}, base_id="", table_name="Tasks")

    with pytest.raises(ValueError, match="fields_to_merge_on"):
        operations.upsert_records(
            credentials={"token": "pat-test"},
            base_id="app123",
            table_name="Tasks",
            records=[{"Name": "Ada"}],
        )
