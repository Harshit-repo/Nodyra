"""Notion v2 operation specs and executors."""

from __future__ import annotations

import json
from typing import Any

from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.registry import register_operation
from nodyra_nodes.integrations_v2.specs import OperationParamSpec, OperationSpec
from nodyra_nodes.integrations_v2.transport import ProviderTransport

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="notion",
            key="*",
            label="Notion integration token",
            fields=["token"],
            multi=True,
            test_service="notion",
        ),
        description="Notion integration token.",
    )


NOTION_CREATE_PAGE_SPEC = OperationSpec(
    node_id="notion_create_page_v2",
    name="Notion Create Page",
    provider="notion",
    resource="page",
    operation="create",
    description="Create a Notion page using the v2 provider transport.",
    icon="brand:notion",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="database_id",
            description="Create inside this database when set.",
        ),
        OperationParamSpec(
            name="parent_page_id",
            group="Options",
            description="Create under this page when database is blank.",
        ),
        OperationParamSpec(
            name="title",
            placeholder="New page title",
        ),
        OperationParamSpec(
            name="title_property",
            default="Name",
            group="Options",
            placeholder="Name",
        ),
        OperationParamSpec(
            name="properties",
            type="object",
            group="Options",
            description="Additional Notion page properties.",
        ),
        OperationParamSpec(
            name="content",
            multiline=True,
            group="Options",
            description="Optional first paragraph.",
        ),
    ),
)

NOTION_GET_PAGE_SPEC = OperationSpec(
    node_id="notion_get_page_v2",
    name="Notion Get Page",
    provider="notion",
    resource="page",
    operation="get",
    description="Retrieve a Notion page by ID.",
    icon="brand:notion",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="page_id", required=True),
    ),
)

NOTION_UPDATE_PAGE_SPEC = OperationSpec(
    node_id="notion_update_page_v2",
    name="Notion Update Page",
    provider="notion",
    resource="page",
    operation="update",
    description="Update Notion page properties, icon, cover, or archive state.",
    icon="brand:notion",
    params=(
        _credentials_param(),
        OperationParamSpec(name="page_id", required=True),
        OperationParamSpec(name="properties", type="object", group="Fields"),
        OperationParamSpec(name="archived", type="boolean", default=None, group="Fields"),
        OperationParamSpec(name="icon", type="object", group="Fields"),
        OperationParamSpec(name="cover", type="object", group="Fields"),
    ),
)

NOTION_ARCHIVE_PAGE_SPEC = OperationSpec(
    node_id="notion_archive_page_v2",
    name="Notion Archive Page",
    provider="notion",
    resource="page",
    operation="archive",
    description=(
        "Archive a Notion page by id, removing it from its database or parent "
        "while keeping it recoverable from the Notion trash."
    ),
    icon="brand:notion",
    params=(
        _credentials_param(),
        OperationParamSpec(name="page_id", required=True),
    ),
)

NOTION_QUERY_DATABASE_SPEC = OperationSpec(
    node_id="notion_query_database_v2",
    name="Notion Query Database",
    provider="notion",
    resource="database",
    operation="query",
    description=(
        "Read pages from a Notion database, optionally filtered and sorted. "
        "Returns page properties as structured rows; results are paginated."
    ),
    icon="brand:notion",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="database_id", required=True),
        OperationParamSpec(name="filter", type="object", group="Query"),
        OperationParamSpec(name="sorts", type="array", group="Query"),
        OperationParamSpec(name="page_size", type="number", default=25, group="Options"),
        OperationParamSpec(name="start_cursor", group="Options"),
    ),
)

NOTION_GET_DATABASE_SPEC = OperationSpec(
    node_id="notion_get_database_v2",
    name="Notion Get Database",
    provider="notion",
    resource="database",
    operation="get",
    description="Retrieve a Notion database by ID.",
    icon="brand:notion",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="database_id", required=True),
    ),
)

NOTION_SEARCH_SPEC = OperationSpec(
    node_id="notion_search_v2",
    name="Notion Search",
    provider="notion",
    resource="search",
    operation="search",
    description="Search pages and databases available to the integration.",
    icon="brand:notion",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="query", placeholder="Roadmap"),
        OperationParamSpec(name="filter", type="object", group="Query"),
        OperationParamSpec(name="sort", type="object", group="Query"),
        OperationParamSpec(name="page_size", type="number", default=25, group="Options"),
        OperationParamSpec(name="start_cursor", group="Options"),
    ),
)

NOTION_LIST_BLOCK_CHILDREN_SPEC = OperationSpec(
    node_id="notion_list_block_children_v2",
    name="Notion List Block Children",
    provider="notion",
    resource="block",
    operation="list_children",
    description="List child blocks for a page or block.",
    icon="brand:notion",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="block_id", required=True),
        OperationParamSpec(name="page_size", type="number", default=25, group="Options"),
        OperationParamSpec(name="start_cursor", group="Options"),
    ),
)

NOTION_APPEND_BLOCK_CHILDREN_SPEC = OperationSpec(
    node_id="notion_append_block_children_v2",
    name="Notion Append Block Children",
    provider="notion",
    resource="block",
    operation="append_children",
    description="Append child blocks to a Notion page or block.",
    icon="brand:notion",
    params=(
        _credentials_param(),
        OperationParamSpec(name="block_id", required=True),
        OperationParamSpec(name="children", type="array", description="Block objects."),
        OperationParamSpec(name="content", multiline=True, group="Simple Paragraph"),
        OperationParamSpec(name="position", type="object", group="Options"),
    ),
)


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(val) for key, val in value.items()}
    if isinstance(value, str):
        return {"token": value}
    return {}


def _token(credentials: Any) -> str:
    creds = _credentials_dict(credentials)
    return str(creds.get("token") or creds.get("api_key") or "")


def _transport(credentials: Any) -> ProviderTransport:
    token = _token(credentials)
    if not token:
        raise ValueError("notion v2: credentials are required")
    return ProviderTransport(
        provider="notion",
        base_url=NOTION_API_BASE,
        default_headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        },
    )


def _text_from_input(input_value: Any) -> str:
    if input_value is None:
        return ""
    if isinstance(input_value, str):
        return input_value
    return json.dumps(input_value, default=str)


def _page_title(input_value: Any, title: str) -> str:
    if title:
        return title
    if isinstance(input_value, dict) and input_value.get("title"):
        return str(input_value["title"])
    return _text_from_input(input_value)


def _require(value: str, node_id: str, name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{node_id}: {name} is required")
    return clean


def _page_size(value: Any) -> int:
    return max(1, min(100, int(value or 25)))


def _payload_without_blanks(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != "" and value != [] and value != {}
    }


def _query_payload(
    *,
    filter: dict[str, Any] | None = None,  # noqa: A002
    sorts: list[dict[str, Any]] | None = None,
    sort: dict[str, Any] | None = None,
    page_size: int = 25,
    start_cursor: str = "",
) -> dict[str, Any]:
    payload = {
        "filter": filter,
        "sorts": sorts,
        "sort": sort,
        "page_size": _page_size(page_size),
        "start_cursor": start_cursor,
    }
    return _payload_without_blanks(payload)


def _children_from_input(
    input_value: Any,
    children: list[dict[str, Any]] | None,
    content: str,
) -> list[dict[str, Any]]:
    if children:
        return children
    if isinstance(input_value, dict) and isinstance(input_value.get("children"), list):
        return input_value["children"]
    text = content or _text_from_input(input_value)
    if not text:
        raise ValueError("notion_append_block_children_v2: children or content is required")
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": text}}],
            },
        }
    ]


def create_page(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    database_id: str = "",
    parent_page_id: str = "",
    title: str = "",
    title_property: str = "Name",
    properties: dict[str, Any] | None = None,
    content: str = "",
) -> Any:
    page_title = _page_title(input, title)
    props = dict(properties or {})
    if database_id:
        parent = {"database_id": database_id}
        props.setdefault(
            title_property or "Name",
            {"title": [{"text": {"content": page_title}}]},
        )
    elif parent_page_id:
        parent = {"page_id": parent_page_id}
        props = {"title": [{"text": {"content": page_title}}], **props}
    else:
        raise ValueError("notion_create_page_v2: database_id or parent_page_id is required")

    payload: dict[str, Any] = {"parent": parent, "properties": props}
    paragraph = content or (
        json.dumps(input, default=str) if isinstance(input, (dict, list)) else ""
    )
    if paragraph:
        payload["children"] = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "text", "text": {"content": paragraph}},
                    ],
                },
            }
        ]
    return _transport(credentials).request(
        "POST",
        "/pages",
        operation="create_page",
        json_body=payload,
    )


def get_page(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    page_id: str = "",
) -> Any:
    page = _require(page_id, "notion_get_page_v2", "page_id")
    return _transport(credentials).request(
        "GET",
        f"/pages/{page}",
        operation="get_page",
    )


def update_page(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    page_id: str = "",
    properties: dict[str, Any] | None = None,
    archived: bool | None = None,
    icon: dict[str, Any] | None = None,
    cover: dict[str, Any] | None = None,
) -> Any:
    source = input if isinstance(input, dict) else {}
    page = _require(page_id, "notion_update_page_v2", "page_id")
    payload = _payload_without_blanks(
        {
            "properties": properties if properties is not None else source.get("properties"),
            "archived": archived,
            "icon": icon if icon is not None else source.get("icon"),
            "cover": cover if cover is not None else source.get("cover"),
        }
    )
    if not payload:
        raise ValueError("notion_update_page_v2: at least one field is required")
    return _transport(credentials).request(
        "PATCH",
        f"/pages/{page}",
        operation="update_page",
        json_body=payload,
    )


def archive_page(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    page_id: str = "",
) -> Any:
    page = _require(page_id, "notion_archive_page_v2", "page_id")
    return _transport(credentials).request(
        "PATCH",
        f"/pages/{page}",
        operation="archive_page",
        json_body={"archived": True},
    )


def query_database(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    database_id: str = "",
    filter: dict[str, Any] | None = None,  # noqa: A002
    sorts: list[dict[str, Any]] | None = None,
    page_size: int = 25,
    start_cursor: str = "",
) -> Any:
    database = _require(database_id, "notion_query_database_v2", "database_id")
    return _transport(credentials).request(
        "POST",
        f"/databases/{database}/query",
        operation="query_database",
        json_body=_query_payload(
            filter=filter,
            sorts=sorts,
            page_size=page_size,
            start_cursor=start_cursor,
        ),
    )


def get_database(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    database_id: str = "",
) -> Any:
    database = _require(database_id, "notion_get_database_v2", "database_id")
    return _transport(credentials).request(
        "GET",
        f"/databases/{database}",
        operation="get_database",
    )


def search(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    query: str = "",
    filter: dict[str, Any] | None = None,  # noqa: A002
    sort: dict[str, Any] | None = None,
    page_size: int = 25,
    start_cursor: str = "",
) -> Any:
    return _transport(credentials).request(
        "POST",
        "/search",
        operation="search",
        json_body=_payload_without_blanks(
            {
                "query": query,
                **_query_payload(
                    filter=filter,
                    sort=sort,
                    page_size=page_size,
                    start_cursor=start_cursor,
                ),
            }
        ),
    )


def list_block_children(
    *,
    input: Any = None,  # noqa: ARG001
    credentials: dict[str, str] | None = None,
    block_id: str = "",
    page_size: int = 25,
    start_cursor: str = "",
) -> Any:
    block = _require(block_id, "notion_list_block_children_v2", "block_id")
    return _transport(credentials).request(
        "GET",
        f"/blocks/{block}/children",
        operation="list_block_children",
        params=_payload_without_blanks(
            {"page_size": _page_size(page_size), "start_cursor": start_cursor}
        ),
    )


def append_block_children(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    block_id: str = "",
    children: list[dict[str, Any]] | None = None,
    content: str = "",
    position: dict[str, Any] | None = None,
) -> Any:
    block = _require(block_id, "notion_append_block_children_v2", "block_id")
    payload = {
        "children": _children_from_input(input, children, content),
        "position": position,
    }
    return _transport(credentials).request(
        "PATCH",
        f"/blocks/{block}/children",
        operation="append_block_children",
        json_body=_payload_without_blanks(payload),
    )


register_operation(NOTION_CREATE_PAGE_SPEC, create_page)
register_operation(NOTION_GET_PAGE_SPEC, get_page)
register_operation(NOTION_UPDATE_PAGE_SPEC, update_page)
register_operation(NOTION_ARCHIVE_PAGE_SPEC, archive_page)
register_operation(NOTION_QUERY_DATABASE_SPEC, query_database)
register_operation(NOTION_GET_DATABASE_SPEC, get_database)
register_operation(NOTION_SEARCH_SPEC, search)
register_operation(NOTION_LIST_BLOCK_CHILDREN_SPEC, list_block_children)
register_operation(NOTION_APPEND_BLOCK_CHILDREN_SPEC, append_block_children)
