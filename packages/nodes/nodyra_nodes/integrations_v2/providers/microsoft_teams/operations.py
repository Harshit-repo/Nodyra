"""Microsoft Teams v2 operation specs and executors."""

from __future__ import annotations

from typing import Any

from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.registry import register_integration, register_operation
from nodyra_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationParamSpec,
    OperationSpec,
    ResourceSpec,
)
from nodyra_nodes.integrations_v2.transport import ProviderTransport

TEAMS_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _graph_credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="microsoft_teams",
            key="*",
            label="Microsoft Teams access token",
            fields=["access_token"],
            multi=True,
            test_service="microsoft_teams",
        ),
        description="Microsoft Graph API access token for Teams.",
    )


def _webhook_credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="microsoft_teams_webhook",
            key="*",
            label="Teams webhook URL",
            fields=["webhook_url"],
            multi=True,
            test_service="microsoft_teams_webhook",
        ),
        description="Teams incoming webhook URL.",
    )


def _graph_transport(credentials: Any) -> ProviderTransport:
    creds = _graph_credentials_dict(credentials)
    token = str(creds.get("access_token") or creds.get("token") or "")
    if not token:
        raise ValueError("microsoft_teams: access_token is required")
    return ProviderTransport(
        provider="microsoft_teams",
        base_url=TEAMS_GRAPH_BASE,
        default_headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )


def _webhook_transport(credentials: Any) -> ProviderTransport:
    creds = _webhook_credentials_dict(credentials)
    webhook_url = str(creds.get("webhook_url") or "")
    if not webhook_url:
        raise ValueError("microsoft_teams_webhook: webhook_url is required")
    return ProviderTransport(
        provider="microsoft_teams",
        base_url="",
        default_headers={"Content-Type": "application/json"},
    )


def _graph_credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        return {"access_token": value}
    return {}


def _webhook_credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        return {"webhook_url": value}
    return {}


def _text_from_input(input_value: Any, text: str = "") -> str:
    if text:
        return text
    if input_value is None:
        return ""
    return str(input_value)


TEAMS_SEND_MESSAGE_SPEC = OperationSpec(
    node_id="teams_send_message_v2",
    name="Teams Send Message",
    provider="microsoft_teams",
    resource="message",
    operation="send",
    description="Send a message to a Teams channel via Graph API.",
    icon="brand:microsoft-teams",
    params=(
        _graph_credentials_param(),
        OperationParamSpec(name="team_id", required=True),
        OperationParamSpec(name="channel_id", required=True),
        OperationParamSpec(
            name="body",
            multiline=True,
            placeholder="Message text. Blank uses the input payload.",
        ),
        OperationParamSpec(name="subject", group="Options"),
    ),
)

TEAMS_LIST_TEAMS_SPEC = OperationSpec(
    node_id="teams_list_teams_v2",
    name="Teams List Teams",
    provider="microsoft_teams",
    resource="team",
    operation="list",
    description="List all Microsoft Teams the user belongs to.",
    icon="brand:microsoft-teams",
    tool_side_effecting=False,
    params=(
        _graph_credentials_param(),
        OperationParamSpec(name="limit", type="number", default=20, group="Options"),
    ),
)

TEAMS_LIST_CHANNELS_SPEC = OperationSpec(
    node_id="teams_list_channels_v2",
    name="Teams List Channels",
    provider="microsoft_teams",
    resource="channel",
    operation="list",
    description="List channels in a Microsoft Team.",
    icon="brand:microsoft-teams",
    tool_side_effecting=False,
    params=(
        _graph_credentials_param(),
        OperationParamSpec(name="team_id", required=True),
    ),
)

TEAMS_SEND_WEBHOOK_SPEC = OperationSpec(
    node_id="teams_send_webhook_v2",
    name="Teams Send Webhook",
    provider="microsoft_teams",
    resource="webhook",
    operation="send",
    description="Send a message to a Teams channel via incoming webhook.",
    icon="brand:microsoft-teams",
    params=(
        _webhook_credentials_param(),
        OperationParamSpec(
            name="body",
            multiline=True,
            placeholder="Message text. Blank uses the input payload.",
        ),
        OperationParamSpec(name="title", group="Options"),
    ),
)


def send_message(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    team_id: str = "",
    channel_id: str = "",
    body: str = "",
    subject: str = "",
) -> Any:
    if not team_id:
        raise ValueError("teams_send_message_v2: team_id is required")
    if not channel_id:
        raise ValueError("teams_send_message_v2: channel_id is required")
    message_text = _text_from_input(input, body)
    if not message_text:
        raise ValueError("teams_send_message_v2: body is required")
    payload: dict[str, Any] = {"body": {"content": message_text}}
    if subject:
        payload["subject"] = subject
    return _graph_transport(credentials).request(
        "POST",
        f"/teams/{team_id}/channels/{channel_id}/messages",
        operation="send_message",
        json_body=payload,
    )


def list_teams(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    limit: int = 20,
) -> Any:
    return _graph_transport(credentials).request(
        "GET",
        "/groups",
        operation="list_teams",
        params={
            "$filter": "resourceProvisioningOptions/Any(x:x eq 'Team')",
            "$top": max(1, min(999, int(limit or 20))),
        },
    )


def list_channels(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    team_id: str = "",
) -> Any:
    if not team_id:
        raise ValueError("teams_list_channels_v2: team_id is required")
    return _graph_transport(credentials).request(
        "GET",
        f"/teams/{team_id}/channels",
        operation="list_channels",
    )


def send_webhook(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    body: str = "",
    title: str = "",
) -> Any:
    message_text = _text_from_input(input, body)
    if not message_text:
        raise ValueError("teams_send_webhook_v2: body is required")
    payload: dict[str, Any] = {"text": message_text}
    if title:
        payload["title"] = title
    transport = _webhook_transport(credentials)
    webhook_url = ""
    creds = _webhook_credentials_dict(credentials)
    if isinstance(creds, dict):
        webhook_url = str(creds.get("webhook_url") or "")
    if not webhook_url:
        raise ValueError("teams_send_webhook_v2: webhook_url is required")
    return transport.request(
        "POST",
        webhook_url,
        operation="send_webhook",
        json_body=payload,
    )


register_operation(TEAMS_SEND_MESSAGE_SPEC, send_message, node_registry=None)
register_operation(TEAMS_LIST_TEAMS_SPEC, list_teams, node_registry=None)
register_operation(TEAMS_LIST_CHANNELS_SPEC, list_channels, node_registry=None)
register_operation(TEAMS_SEND_WEBHOOK_SPEC, send_webhook, node_registry=None)

TEAMS_INTEGRATION = IntegrationSpec(
    id="microsoft_teams",
    name="Microsoft Teams",
    description="Send messages, list teams and channels via Graph API, and send webhook messages.",
    icon="brand:microsoft-teams",
    credential_types=("microsoft_teams", "microsoft_teams_webhook"),
    resources=(
        ResourceSpec(
            id="message",
            name="Message",
            operations=(TEAMS_SEND_MESSAGE_SPEC,),
        ),
        ResourceSpec(
            id="team",
            name="Team",
            operations=(TEAMS_LIST_TEAMS_SPEC,),
        ),
        ResourceSpec(
            id="channel",
            name="Channel",
            operations=(TEAMS_LIST_CHANNELS_SPEC,),
        ),
        ResourceSpec(
            id="webhook",
            name="Webhook",
            operations=(TEAMS_SEND_WEBHOOK_SPEC,),
        ),
    ),
)

register_integration(TEAMS_INTEGRATION)
