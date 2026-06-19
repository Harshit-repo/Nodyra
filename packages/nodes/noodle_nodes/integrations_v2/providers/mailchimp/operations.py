"""Mailchimp v2 operation specs and executors."""

from __future__ import annotations

import hashlib
from typing import Any

from noodle.models import CredentialSpec
from noodle_nodes.integrations_v2.registry import register_integration, register_operation
from noodle_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationParamSpec,
    OperationSpec,
    ResourceSpec,
)
from noodle_nodes.integrations_v2.transport import ProviderTransport


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="mailchimp",
            key="*",
            label="Mailchimp API key",
            fields=["api_key"],
            multi=True,
            test_service="mailchimp",
        ),
        description="Mailchimp API key from Account > Extras > API keys.",
    )


def _transport(credentials: Any) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    api_key = str(creds.get("api_key") or "")
    if not api_key:
        raise ValueError("mailchimp: api_key is required")
    if "-" not in api_key:
        raise ValueError(
            "mailchimp: invalid api_key - must contain datacenter suffix (e.g. abc123-us20)"
        )
    dc = api_key.rsplit("-", 1)[-1]
    return ProviderTransport(
        provider="mailchimp",
        base_url=f"https://{dc}.api.mailchimp.com/3.0",
        default_headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        return {"api_key": value}
    return {}


MAILCHIMP_LIST_CAMPAIGNS_SPEC = OperationSpec(
    node_id="mailchimp_list_campaigns_v2",
    name="Mailchimp List Campaigns",
    provider="mailchimp",
    resource="campaign",
    operation="list",
    description="List Mailchimp campaigns with optional status filter.",
    icon="brand:mailchimp",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="status",
            choices=("save", "paused", "schedule", "sending", "sent"),
            group="Filters",
        ),
        OperationParamSpec(
            name="limit",
            type="number",
            default=20,
            group="Options",
        ),
    ),
)

MAILCHIMP_CREATE_CAMPAIGN_SPEC = OperationSpec(
    node_id="mailchimp_create_campaign_v2",
    name="Mailchimp Create Campaign",
    provider="mailchimp",
    resource="campaign",
    operation="create",
    description="Create a new Mailchimp campaign.",
    icon="brand:mailchimp",
    params=(
        _credentials_param(),
        OperationParamSpec(name="list_id", required=True, placeholder="abc123"),
        OperationParamSpec(name="subject", required=True),
        OperationParamSpec(name="from_name", required=True),
        OperationParamSpec(name="reply_to", required=True, placeholder="sender@example.com"),
        OperationParamSpec(name="title", group="Options"),
        OperationParamSpec(
            name="type",
            choices=("regular", "plaintext", "absplit", "rss", "variate"),
            default="regular",
            group="Options",
        ),
    ),
)

MAILCHIMP_SEND_CAMPAIGN_SPEC = OperationSpec(
    node_id="mailchimp_send_campaign_v2",
    name="Mailchimp Send Campaign",
    provider="mailchimp",
    resource="campaign",
    operation="send",
    description="Send a Mailchimp campaign immediately.",
    icon="brand:mailchimp",
    params=(
        _credentials_param(),
        OperationParamSpec(name="campaign_id", required=True, placeholder="abc123"),
    ),
)

MAILCHIMP_LIST_AUDIENCES_SPEC = OperationSpec(
    node_id="mailchimp_list_audiences_v2",
    name="Mailchimp List Audiences",
    provider="mailchimp",
    resource="audience",
    operation="list",
    description="List Mailchimp audiences (lists).",
    icon="brand:mailchimp",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="limit",
            type="number",
            default=20,
            group="Options",
        ),
    ),
)

MAILCHIMP_ADD_MEMBER_SPEC = OperationSpec(
    node_id="mailchimp_add_member_v2",
    name="Mailchimp Add Member",
    provider="mailchimp",
    resource="member",
    operation="add",
    description="Add or update a subscriber in a Mailchimp audience list.",
    icon="brand:mailchimp",
    params=(
        _credentials_param(),
        OperationParamSpec(name="list_id", required=True),
        OperationParamSpec(name="email_address", required=True),
        OperationParamSpec(
            name="status",
            choices=("subscribed", "unsubscribed", "pending", "cleaned"),
            default="subscribed",
            group="Options",
        ),
        OperationParamSpec(name="first_name", group="Options"),
        OperationParamSpec(name="last_name", group="Options"),
        OperationParamSpec(
            name="tags",
            group="Options",
            placeholder="tag1,tag2",
        ),
    ),
)

MAILCHIMP_GET_MEMBER_SPEC = OperationSpec(
    node_id="mailchimp_get_member_v2",
    name="Mailchimp Get Member",
    provider="mailchimp",
    resource="member",
    operation="get",
    description="Look up a list member by email address.",
    icon="brand:mailchimp",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(name="list_id", required=True),
        OperationParamSpec(name="email_address", required=True),
    ),
)


def list_campaigns(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    status: str = "",
    limit: int = 20,
) -> Any:
    params: dict[str, Any] = {"count": max(1, int(limit or 20))}
    if status:
        params["status"] = status
    return _transport(credentials).request(
        "GET",
        "/campaigns",
        operation="list_campaigns",
        params=params,
    )


def create_campaign(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    list_id: str = "",
    subject: str = "",
    from_name: str = "",
    reply_to: str = "",
    title: str = "",
    type: str = "regular",
) -> Any:
    if not list_id:
        raise ValueError("mailchimp_create_campaign_v2: list_id is required")
    if not subject:
        raise ValueError("mailchimp_create_campaign_v2: subject is required")
    if not from_name:
        raise ValueError("mailchimp_create_campaign_v2: from_name is required")
    if not reply_to:
        raise ValueError("mailchimp_create_campaign_v2: reply_to is required")
    payload: dict[str, Any] = {
        "type": type or "regular",
        "recipients": {"list_id": list_id},
        "settings": {
            "subject_line": subject,
            "from_name": from_name,
            "reply_to": reply_to,
        },
    }
    if title:
        payload["settings"]["title"] = title
    return _transport(credentials).request(
        "POST",
        "/campaigns",
        operation="create_campaign",
        json_body=payload,
    )


def send_campaign(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    campaign_id: str = "",
) -> Any:
    if not campaign_id:
        raise ValueError("mailchimp_send_campaign_v2: campaign_id is required")
    return _transport(credentials).request(
        "POST",
        f"/campaigns/{campaign_id}/actions/send",
        operation="send_campaign",
    )


def list_audiences(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    limit: int = 20,
) -> Any:
    params: dict[str, Any] = {"count": max(1, int(limit or 20))}
    return _transport(credentials).request(
        "GET",
        "/lists",
        operation="list_audiences",
        params=params,
    )


def add_member(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    list_id: str = "",
    email_address: str = "",
    status: str = "subscribed",
    first_name: str = "",
    last_name: str = "",
    tags: str = "",
) -> Any:
    if not list_id:
        raise ValueError("mailchimp_add_member_v2: list_id is required")
    if not email_address:
        raise ValueError("mailchimp_add_member_v2: email_address is required")
    payload: dict[str, Any] = {
        "email_address": email_address,
        "status": status or "subscribed",
    }
    merge_fields: dict[str, str] = {}
    if first_name:
        merge_fields["FNAME"] = first_name
    if last_name:
        merge_fields["LNAME"] = last_name
    if merge_fields:
        payload["merge_fields"] = merge_fields
    if tags:
        payload["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    return _transport(credentials).request(
        "POST",
        f"/lists/{list_id}/members",
        operation="add_member",
        json_body=payload,
    )


def get_member(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    list_id: str = "",
    email_address: str = "",
) -> Any:
    if not list_id:
        raise ValueError("mailchimp_get_member_v2: list_id is required")
    if not email_address:
        raise ValueError("mailchimp_get_member_v2: email_address is required")
    subscriber_hash = hashlib.md5(email_address.lower().encode("utf-8")).hexdigest()
    return _transport(credentials).request(
        "GET",
        f"/lists/{list_id}/members/{subscriber_hash}",
        operation="get_member",
    )


register_operation(MAILCHIMP_LIST_CAMPAIGNS_SPEC, list_campaigns, node_registry=None)
register_operation(MAILCHIMP_CREATE_CAMPAIGN_SPEC, create_campaign, node_registry=None)
register_operation(MAILCHIMP_SEND_CAMPAIGN_SPEC, send_campaign, node_registry=None)
register_operation(MAILCHIMP_LIST_AUDIENCES_SPEC, list_audiences, node_registry=None)
register_operation(MAILCHIMP_ADD_MEMBER_SPEC, add_member, node_registry=None)
register_operation(MAILCHIMP_GET_MEMBER_SPEC, get_member, node_registry=None)


MAILCHIMP_INTEGRATION = IntegrationSpec(
    id="mailchimp",
    name="Mailchimp",
    description="Manage Mailchimp campaigns, audiences, and subscribers.",
    icon="brand:mailchimp",
    credential_types=("mailchimp",),
    resources=(
        ResourceSpec(
            id="campaign",
            name="Campaign",
            operations=(
                MAILCHIMP_LIST_CAMPAIGNS_SPEC,
                MAILCHIMP_CREATE_CAMPAIGN_SPEC,
                MAILCHIMP_SEND_CAMPAIGN_SPEC,
            ),
        ),
        ResourceSpec(
            id="audience",
            name="Audience",
            operations=(MAILCHIMP_LIST_AUDIENCES_SPEC,),
        ),
        ResourceSpec(
            id="member",
            name="Member",
            operations=(
                MAILCHIMP_ADD_MEMBER_SPEC,
                MAILCHIMP_GET_MEMBER_SPEC,
            ),
        ),
    ),
)

register_integration(MAILCHIMP_INTEGRATION)
