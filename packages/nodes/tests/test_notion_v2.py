from typing import Any
from unittest.mock import MagicMock

import pytest

import noodle_nodes  # noqa: F401 - importing registers provider nodes
from noodle.sdk import registry
from noodle_nodes.integrations_v2.providers.notion import operations


def _mock_transport(return_value: Any) -> MagicMock:
    transport = MagicMock()
    transport.request.return_value = return_value
    return transport


def test_notion_v2_node_is_registered() -> None:
    manifests = {manifest.id: manifest for manifest in registry.manifests()}
    manifest = manifests["notion_create_page_v2"]

    assert manifest.name == "Notion Create Page"
    assert manifest.icon == "brand:notion"
    assert manifest.category == "Integrations"
    params = {param.name: param for param in manifest.params}
    assert params["credentials"].credential is not None
    assert params["credentials"].credential.type == "notion"
    assert params["credentials"].credential.multi is True
    assert params["credentials"].credential.test_service == "notion"
    assert params["content"].group == "Options"


def test_notion_v2_generated_source_is_available() -> None:
    source = getattr(registry.get("notion_create_page_v2").func, "__noodle_source__", "")

    assert "def notion_create_page_v2(" in source
    assert "credentials=None" in source
    assert "execute_registered_operation" in source
    assert "notion.page.create" in source


def test_notion_create_page_v2_database_payload(monkeypatch) -> None:
    transport = _mock_transport({"id": "page123"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    result = registry.get("notion_create_page_v2").func(
        input={"body": "hello"},
        credentials={"token": "secret-test"},
        database_id="db123",
        title="Ada",
        properties={"Status": {"select": {"name": "Open"}}},
        content="First paragraph",
    )

    assert result == {"id": "page123"}
    payload = transport.request.call_args.kwargs["json_body"]
    assert payload["parent"] == {"database_id": "db123"}
    assert payload["properties"]["Name"] == {
        "title": [{"text": {"content": "Ada"}}],
    }
    assert payload["properties"]["Status"] == {"select": {"name": "Open"}}
    assert payload["children"][0]["paragraph"]["rich_text"][0]["text"]["content"] == (
        "First paragraph"
    )


def test_notion_create_page_v2_parent_page_payload(monkeypatch) -> None:
    transport = _mock_transport({"id": "page123"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("notion_create_page_v2").func(
        input=None,
        credentials={"token": "secret-test"},
        parent_page_id="page-parent",
        title="Child page",
    )

    payload = transport.request.call_args.kwargs["json_body"]
    assert payload["parent"] == {"page_id": "page-parent"}
    assert payload["properties"]["title"] == [{"text": {"content": "Child page"}}]


def test_notion_create_page_v2_requires_parent() -> None:
    with pytest.raises(ValueError, match="database_id"):
        operations.create_page(credentials={"token": "secret-test"}, title="No parent")
