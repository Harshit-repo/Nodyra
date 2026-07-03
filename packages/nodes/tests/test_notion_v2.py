from typing import Any
from unittest.mock import MagicMock

import pytest

import nodyra_nodes  # noqa: F401 - importing registers provider nodes
from nodyra.sdk import registry
from nodyra_nodes.integrations_v2.providers.notion import operations


def _mock_transport(return_value: Any) -> MagicMock:
    transport = MagicMock()
    transport.request.return_value = return_value
    return transport


def test_notion_v2_node_is_registered() -> None:
    manifests = {manifest.id: manifest for manifest in registry.manifests()}
    expected = {
        "notion_create_page_v2": ("Notion Create Page", True),
        "notion_get_page_v2": ("Notion Get Page", False),
        "notion_update_page_v2": ("Notion Update Page", True),
        "notion_archive_page_v2": ("Notion Archive Page", True),
        "notion_query_database_v2": ("Notion Query Database", False),
        "notion_get_database_v2": ("Notion Get Database", False),
        "notion_search_v2": ("Notion Search", False),
        "notion_list_block_children_v2": ("Notion List Block Children", False),
        "notion_append_block_children_v2": ("Notion Append Block Children", True),
    }

    for node_id, (name, side_effecting) in expected.items():
        manifest = manifests[node_id]
        assert manifest.name == name
        assert manifest.icon == "brand:notion"
        assert manifest.category == "Integrations"
        assert manifest.usable_as_tool is True
        assert manifest.tool_side_effecting is side_effecting

    params = {param.name: param for param in manifests["notion_create_page_v2"].params}
    assert params["credentials"].credential is not None
    assert params["credentials"].credential.type == "notion"
    assert params["credentials"].credential.multi is True
    assert params["credentials"].credential.test_service == "notion"
    assert params["content"].group == "Options"


def test_notion_v2_generated_source_is_available() -> None:
    source = getattr(registry.get("notion_create_page_v2").func, "__nodyra_source__", "")

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


def test_notion_page_read_update_and_archive_nodes(monkeypatch) -> None:
    transport = _mock_transport({"id": "page123"})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("notion_get_page_v2").func(
        input=None,
        credentials={"token": "secret-test"},
        page_id="page123",
    )
    registry.get("notion_update_page_v2").func(
        input=None,
        credentials={"token": "secret-test"},
        page_id="page123",
        properties={"Status": {"select": {"name": "Closed"}}},
        archived=False,
    )
    registry.get("notion_archive_page_v2").func(
        input=None,
        credentials={"token": "secret-test"},
        page_id="page123",
    )

    assert transport.request.call_args_list[0].args == ("GET", "/pages/page123")
    assert transport.request.call_args_list[0].kwargs == {"operation": "get_page"}
    assert transport.request.call_args_list[1].args == ("PATCH", "/pages/page123")
    assert transport.request.call_args_list[1].kwargs == {
        "operation": "update_page",
        "json_body": {
            "properties": {"Status": {"select": {"name": "Closed"}}},
            "archived": False,
        },
    }
    assert transport.request.call_args_list[2].args == ("PATCH", "/pages/page123")
    assert transport.request.call_args_list[2].kwargs == {
        "operation": "archive_page",
        "json_body": {"archived": True},
    }


def test_notion_database_and_search_nodes(monkeypatch) -> None:
    transport = _mock_transport({"results": []})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("notion_query_database_v2").func(
        input=None,
        credentials={"token": "secret-test"},
        database_id="db123",
        filter={"property": "Status", "select": {"equals": "Open"}},
        sorts=[{"timestamp": "created_time", "direction": "descending"}],
        page_size=250,
    )
    registry.get("notion_get_database_v2").func(
        input=None,
        credentials={"token": "secret-test"},
        database_id="db123",
    )
    registry.get("notion_search_v2").func(
        input=None,
        credentials={"token": "secret-test"},
        query="Roadmap",
        filter={"property": "object", "value": "page"},
        page_size=5,
    )

    assert transport.request.call_args_list[0].args == (
        "POST",
        "/databases/db123/query",
    )
    assert transport.request.call_args_list[0].kwargs == {
        "operation": "query_database",
        "json_body": {
            "filter": {"property": "Status", "select": {"equals": "Open"}},
            "sorts": [{"timestamp": "created_time", "direction": "descending"}],
            "page_size": 100,
        },
    }
    assert transport.request.call_args_list[1].args == ("GET", "/databases/db123")
    assert transport.request.call_args_list[2].args == ("POST", "/search")
    assert transport.request.call_args_list[2].kwargs["json_body"] == {
        "query": "Roadmap",
        "filter": {"property": "object", "value": "page"},
        "page_size": 5,
    }


def test_notion_block_children_nodes(monkeypatch) -> None:
    transport = _mock_transport({"results": []})
    monkeypatch.setattr(operations, "_transport", lambda _credentials: transport)

    registry.get("notion_list_block_children_v2").func(
        input=None,
        credentials={"token": "secret-test"},
        block_id="block123",
        page_size=10,
    )
    registry.get("notion_append_block_children_v2").func(
        input=None,
        credentials={"token": "secret-test"},
        block_id="block123",
        content="Hello",
        position={"type": "end"},
    )

    assert transport.request.call_args_list[0].args == (
        "GET",
        "/blocks/block123/children",
    )
    assert transport.request.call_args_list[0].kwargs == {
        "operation": "list_block_children",
        "params": {"page_size": 10},
    }
    assert transport.request.call_args_list[1].args == (
        "PATCH",
        "/blocks/block123/children",
    )
    payload = transport.request.call_args_list[1].kwargs["json_body"]
    assert payload["position"] == {"type": "end"}
    assert payload["children"][0]["paragraph"]["rich_text"][0]["text"]["content"] == (
        "Hello"
    )
