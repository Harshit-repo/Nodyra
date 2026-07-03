"""Retell AI v2 operation specs and executors."""

from __future__ import annotations

import json
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

RETELL_API_BASE = "https://api.retellai.com"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="retell",
            key="*",
            label="Retell API key",
            fields=["api_key"],
            multi=True,
            test_service="retell",
        ),
        description="Retell AI API key.",
    )


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        return {"api_key": value}
    return {}


def _transport(credentials: Any) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    api_key = str(creds.get("api_key") or "")
    if not api_key:
        raise ValueError("retell: api_key is required")
    return ProviderTransport(
        provider="retell",
        base_url=RETELL_API_BASE,
        default_headers={"Authorization": f"Bearer {api_key}"},
    )


# ---------------------------------------------------------------------------
# Operation specs
# ---------------------------------------------------------------------------

RETELL_CREATE_CALL_SPEC = OperationSpec(
    node_id="retell_create_call",
    name="Retell Create Call",
    provider="retell",
    resource="call",
    operation="create",
    description="Start an outbound or inbound-originated call via Retell AI.",
    icon="brand:retell",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="agent_id",
            required=True,
            description="Retell agent ID to use for the call.",
        ),
        OperationParamSpec(
            name="from_number",
            required=True,
            placeholder="+1234567890",
            description="Retell phone number to call from (E.164).",
        ),
        OperationParamSpec(
            name="to_number",
            required=True,
            placeholder="+1987654321",
            description="Destination phone number (E.164).",
        ),
        OperationParamSpec(
            name="metadata",
            multiline=True,
            group="Options",
            description="JSON string of metadata to attach to the call.",
        ),
        OperationParamSpec(
            name="retell_llm_dynamic_variables",
            multiline=True,
            group="Options",
            description="JSON string of dynamic variables for the Retell LLM prompt.",
        ),
    ),
)

RETELL_GET_CALL_SPEC = OperationSpec(
    node_id="retell_get_call",
    name="Retell Get Call",
    provider="retell",
    resource="call",
    operation="get",
    description="Fetch call details, transcript, and recording from Retell AI.",
    icon="brand:retell",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="call_id",
            required=True,
            placeholder="call_abc123",
            description="Retell call ID.",
        ),
    ),
)

RETELL_LIST_CALLS_SPEC = OperationSpec(
    node_id="retell_list_calls",
    name="Retell List Calls",
    provider="retell",
    resource="call",
    operation="list",
    description="List recent Retell AI calls.",
    icon="brand:retell",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="limit",
            type="number",
            default=20,
            group="Options",
            description="Max calls to return.",
        ),
        OperationParamSpec(
            name="agent_id",
            group="Filters",
            description="Filter calls by agent ID.",
        ),
        OperationParamSpec(
            name="start_timestamp",
            type="number",
            group="Filters",
            description="Unix ms timestamp — only calls after this.",
        ),
    ),
)

RETELL_CREATE_AGENT_SPEC = OperationSpec(
    node_id="retell_create_agent",
    name="Retell Create Agent",
    provider="retell",
    resource="agent",
    operation="create",
    description="Define a Retell AI voice agent (LLM, voice, language, etc.).",
    icon="brand:retell",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="agent_name",
            required=True,
            description="Display name for the agent.",
        ),
        OperationParamSpec(
            name="llm_websocket_url",
            required=True,
            placeholder="wss://...",
            description="WebSocket URL for the LLM server or Retell LLM ID.",
        ),
        OperationParamSpec(
            name="voice_id",
            required=True,
            description="Voice ID for the agent (Retell or ElevenLabs voice).",
        ),
        OperationParamSpec(
            name="language",
            default="en-US",
            group="Options",
            description="Language code, e.g. en-US.",
        ),
        OperationParamSpec(
            name="agent_config",
            multiline=True,
            group="Advanced",
            description="Full agent config as JSON (overrides all other fields if set).",
        ),
    ),
)

RETELL_LIST_AGENTS_SPEC = OperationSpec(
    node_id="retell_list_agents",
    name="Retell List Agents",
    provider="retell",
    resource="agent",
    operation="list",
    description="List all configured Retell AI agents.",
    icon="brand:retell",
    tool_side_effecting=False,
    params=(_credentials_param(),),
)

RETELL_CREATE_PHONE_NUMBER_SPEC = OperationSpec(
    node_id="retell_create_phone_number",
    name="Retell Create Phone Number",
    provider="retell",
    resource="phone_number",
    operation="create",
    description="Purchase or configure a phone number for inbound Retell calls.",
    icon="brand:retell",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="area_code",
            placeholder="415",
            description="US area code for the new number (leave blank for any).",
        ),
        OperationParamSpec(
            name="inbound_agent_id",
            description="Agent ID to use when an inbound call arrives.",
        ),
        OperationParamSpec(
            name="nickname",
            description="Human-readable label for this number.",
        ),
    ),
)


# ---------------------------------------------------------------------------
# Executor functions
# ---------------------------------------------------------------------------

def create_call(
    *,
    input: Any = None,
    credentials: Any = None,
    agent_id: str = "",
    from_number: str = "",
    to_number: str = "",
    metadata: str = "",
    retell_llm_dynamic_variables: str = "",
) -> Any:
    if not agent_id:
        raise ValueError("retell_create_call: agent_id is required")
    if not from_number:
        raise ValueError("retell_create_call: from_number is required")
    if not to_number:
        raise ValueError("retell_create_call: to_number is required")
    body: dict[str, Any] = {
        "agent_id": agent_id,
        "from_number": from_number,
        "to_number": to_number,
    }
    if metadata:
        body["metadata"] = json.loads(metadata)
    if retell_llm_dynamic_variables:
        body["retell_llm_dynamic_variables"] = json.loads(retell_llm_dynamic_variables)
    return _transport(credentials).request(
        "POST", "/v2/create-phone-call", operation="create_call", json_body=body
    )


def get_call(
    *,
    input: Any = None,
    credentials: Any = None,
    call_id: str = "",
) -> Any:
    if not call_id:
        raise ValueError("retell_get_call: call_id is required")
    return _transport(credentials).request(
        "GET", f"/v2/get-call/{call_id}", operation="get_call"
    )


def list_calls(
    *,
    input: Any = None,
    credentials: Any = None,
    limit: int = 20,
    agent_id: str = "",
    start_timestamp: int = 0,
) -> Any:
    query: dict[str, Any] = {"limit": int(limit or 20)}
    if agent_id:
        query["filter_criteria"] = {"agent_id": [agent_id]}
    if start_timestamp:
        query["filter_criteria"] = {
            **query.get("filter_criteria", {}),
            "start_timestamp": {"lower_threshold": int(start_timestamp)},
        }
    return _transport(credentials).request(
        "GET", "/v2/list-calls", operation="list_calls", params=query
    )


def create_agent(
    *,
    input: Any = None,
    credentials: Any = None,
    agent_name: str = "",
    llm_websocket_url: str = "",
    voice_id: str = "",
    language: str = "en-US",
    agent_config: str = "",
) -> Any:
    if agent_config:
        body = json.loads(agent_config)
    else:
        if not agent_name:
            raise ValueError("retell_create_agent: agent_name is required")
        if not llm_websocket_url:
            raise ValueError("retell_create_agent: llm_websocket_url is required")
        if not voice_id:
            raise ValueError("retell_create_agent: voice_id is required")
        body = {
            "agent_name": agent_name,
            "llm_websocket_url": llm_websocket_url,
            "voice_id": voice_id,
            "language": language or "en-US",
        }
    return _transport(credentials).request(
        "POST", "/v2/create-agent", operation="create_agent", json_body=body
    )


def list_agents(
    *,
    input: Any = None,
    credentials: Any = None,
) -> Any:
    return _transport(credentials).request(
        "GET", "/v2/list-agents", operation="list_agents"
    )


def create_phone_number(
    *,
    input: Any = None,
    credentials: Any = None,
    area_code: str = "",
    inbound_agent_id: str = "",
    nickname: str = "",
) -> Any:
    body: dict[str, Any] = {}
    if area_code:
        body["area_code"] = int(area_code)
    if inbound_agent_id:
        body["inbound_agent_id"] = inbound_agent_id
    if nickname:
        body["nickname"] = nickname
    return _transport(credentials).request(
        "POST", "/v2/create-phone-number", operation="create_phone_number", json_body=body
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

register_operation(RETELL_CREATE_CALL_SPEC, create_call, node_registry=None)
register_operation(RETELL_GET_CALL_SPEC, get_call, node_registry=None)
register_operation(RETELL_LIST_CALLS_SPEC, list_calls, node_registry=None)
register_operation(RETELL_CREATE_AGENT_SPEC, create_agent, node_registry=None)
register_operation(RETELL_LIST_AGENTS_SPEC, list_agents, node_registry=None)
register_operation(RETELL_CREATE_PHONE_NUMBER_SPEC, create_phone_number, node_registry=None)

RETELL_INTEGRATION = IntegrationSpec(
    id="retell",
    name="Retell AI",
    description="Manage AI voice calls and agents with Retell AI.",
    icon="brand:retell",
    credential_types=("retell",),
    resources=(
        ResourceSpec(
            id="call",
            name="Call",
            operations=(
                RETELL_CREATE_CALL_SPEC,
                RETELL_GET_CALL_SPEC,
                RETELL_LIST_CALLS_SPEC,
            ),
        ),
        ResourceSpec(
            id="agent",
            name="Agent",
            operations=(RETELL_CREATE_AGENT_SPEC, RETELL_LIST_AGENTS_SPEC),
        ),
        ResourceSpec(
            id="phone_number",
            name="Phone Number",
            operations=(RETELL_CREATE_PHONE_NUMBER_SPEC,),
        ),
    ),
)

register_integration(RETELL_INTEGRATION)
