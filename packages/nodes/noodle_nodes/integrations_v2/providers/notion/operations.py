"""Notion v2 operation specs and executors."""

from __future__ import annotations

import json
from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_operation
from noodle_nodes.integrations_v2.specs import OperationParamSpec, OperationSpec
from noodle_nodes.integrations_v2.transport import ProviderTransport

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
        raise ValueError("notion_create_page_v2: credentials are required")
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


register_operation(NOTION_CREATE_PAGE_SPEC, create_page)
