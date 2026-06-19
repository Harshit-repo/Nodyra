"""ElevenLabs Conversational AI v2 operation specs and executors."""

from __future__ import annotations

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

ELEVENLABS_API_BASE = "https://api.elevenlabs.io"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="elevenlabs",
            key="*",
            label="ElevenLabs API key",
            fields=["api_key"],
            multi=True,
            test_service="elevenlabs",
        ),
        description="ElevenLabs API key.",
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
        raise ValueError("elevenlabs: api_key is required")
    return ProviderTransport(
        provider="elevenlabs",
        base_url=ELEVENLABS_API_BASE,
        default_headers={"xi-api-key": api_key},
    )


# ---------------------------------------------------------------------------
# Operation specs
# ---------------------------------------------------------------------------

ELEVENLABS_CREATE_AGENT_SPEC = OperationSpec(
    node_id="elevenlabs_create_agent",
    name="ElevenLabs Create Agent",
    provider="elevenlabs",
    resource="convai_agent",
    operation="create",
    description="Create or update a conversational AI agent in ElevenLabs.",
    icon="brand:elevenlabs",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="name",
            required=True,
            description="Agent display name.",
        ),
        OperationParamSpec(
            name="first_message",
            description="First thing the agent says when a call connects.",
        ),
        OperationParamSpec(
            name="system_prompt",
            multiline=True,
            description="System prompt for the agent's LLM.",
        ),
        OperationParamSpec(
            name="voice_id",
            description="ElevenLabs voice ID to use for the agent.",
        ),
        OperationParamSpec(
            name="language",
            default="en",
            group="Options",
            description="Language code (e.g. en, fr, es).",
        ),
        OperationParamSpec(
            name="agent_config",
            multiline=True,
            group="Advanced",
            description="Full agent config as JSON (overrides all other fields if set).",
        ),
    ),
)

ELEVENLABS_GET_AGENT_SPEC = OperationSpec(
    node_id="elevenlabs_get_agent",
    name="ElevenLabs Get Agent",
    provider="elevenlabs",
    resource="convai_agent",
    operation="get",
    description="Get the configuration for an ElevenLabs conversational AI agent.",
    icon="brand:elevenlabs",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="agent_id",
            required=True,
            description="ElevenLabs agent ID.",
        ),
    ),
)

ELEVENLABS_LIST_AGENTS_SPEC = OperationSpec(
    node_id="elevenlabs_list_agents",
    name="ElevenLabs List Agents",
    provider="elevenlabs",
    resource="convai_agent",
    operation="list",
    description="List all ElevenLabs conversational AI agents.",
    icon="brand:elevenlabs",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="page_size",
            type="number",
            default=30,
            group="Options",
            description="Number of agents to return.",
        ),
    ),
)

ELEVENLABS_GET_CONVERSATION_SPEC = OperationSpec(
    node_id="elevenlabs_get_conversation",
    name="ElevenLabs Get Conversation",
    provider="elevenlabs",
    resource="conversation",
    operation="get",
    description="Get transcript and metadata for an ElevenLabs ConvAI conversation.",
    icon="brand:elevenlabs",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="conversation_id",
            required=True,
            description="ElevenLabs conversation ID.",
        ),
    ),
)

ELEVENLABS_LIST_CONVERSATIONS_SPEC = OperationSpec(
    node_id="elevenlabs_list_conversations",
    name="ElevenLabs List Conversations",
    provider="elevenlabs",
    resource="conversation",
    operation="list",
    description="List recent ElevenLabs ConvAI conversations.",
    icon="brand:elevenlabs",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="agent_id",
            group="Filters",
            description="Filter conversations by agent ID.",
        ),
        OperationParamSpec(
            name="page_size",
            type="number",
            default=30,
            group="Options",
            description="Number of conversations to return.",
        ),
    ),
)

ELEVENLABS_GET_SIGNED_URL_SPEC = OperationSpec(
    node_id="elevenlabs_get_signed_url",
    name="ElevenLabs Get Signed URL",
    provider="elevenlabs",
    resource="convai_agent",
    operation="get_signed_url",
    description="Generate a signed WebSocket URL for embedding the ElevenLabs voice widget.",
    icon="brand:elevenlabs",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="agent_id",
            required=True,
            description="ElevenLabs agent ID to generate a signed URL for.",
        ),
    ),
)


# ---------------------------------------------------------------------------
# Executor functions
# ---------------------------------------------------------------------------

def create_agent(
    *,
    input: Any = None,
    credentials: Any = None,
    name: str = "",
    first_message: str = "",
    system_prompt: str = "",
    voice_id: str = "",
    language: str = "en",
    agent_config: str = "",
) -> Any:
    import json
    if agent_config:
        body = json.loads(agent_config)
    else:
        if not name:
            raise ValueError("elevenlabs_create_agent: name is required")
        body: dict[str, Any] = {"name": name}
        conversation_config: dict[str, Any] = {}
        if first_message:
            conversation_config["first_message"] = first_message
        if language:
            conversation_config["language"] = language
        if system_prompt:
            conversation_config["agent"] = {"prompt": {"prompt": system_prompt}}
        if voice_id:
            conversation_config["tts"] = {"voice_id": voice_id}
        if conversation_config:
            body["conversation_config"] = conversation_config
    return _transport(credentials).request(
        "POST", "/v1/convai/agents/create", operation="create_agent", json_body=body
    )


def get_agent(
    *,
    input: Any = None,
    credentials: Any = None,
    agent_id: str = "",
) -> Any:
    if not agent_id:
        raise ValueError("elevenlabs_get_agent: agent_id is required")
    return _transport(credentials).request(
        "GET", f"/v1/convai/agents/{agent_id}", operation="get_agent"
    )


def list_agents(
    *,
    input: Any = None,
    credentials: Any = None,
    page_size: int = 30,
) -> Any:
    return _transport(credentials).request(
        "GET",
        "/v1/convai/agents",
        operation="list_agents",
        params={"page_size": int(page_size or 30)},
    )


def get_conversation(
    *,
    input: Any = None,
    credentials: Any = None,
    conversation_id: str = "",
) -> Any:
    if not conversation_id:
        raise ValueError("elevenlabs_get_conversation: conversation_id is required")
    return _transport(credentials).request(
        "GET",
        f"/v1/convai/conversations/{conversation_id}",
        operation="get_conversation",
    )


def list_conversations(
    *,
    input: Any = None,
    credentials: Any = None,
    agent_id: str = "",
    page_size: int = 30,
) -> Any:
    query: dict[str, Any] = {"page_size": int(page_size or 30)}
    if agent_id:
        query["agent_id"] = agent_id
    return _transport(credentials).request(
        "GET", "/v1/convai/conversations", operation="list_conversations", params=query
    )


def get_signed_url(
    *,
    input: Any = None,
    credentials: Any = None,
    agent_id: str = "",
) -> Any:
    if not agent_id:
        raise ValueError("elevenlabs_get_signed_url: agent_id is required")
    return _transport(credentials).request(
        "GET",
        "/v1/convai/conversation/get_signed_url",
        operation="get_signed_url",
        params={"agent_id": agent_id},
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

register_operation(ELEVENLABS_CREATE_AGENT_SPEC, create_agent, node_registry=None)
register_operation(ELEVENLABS_GET_AGENT_SPEC, get_agent, node_registry=None)
register_operation(ELEVENLABS_LIST_AGENTS_SPEC, list_agents, node_registry=None)
register_operation(ELEVENLABS_GET_CONVERSATION_SPEC, get_conversation, node_registry=None)
register_operation(ELEVENLABS_LIST_CONVERSATIONS_SPEC, list_conversations, node_registry=None)
register_operation(ELEVENLABS_GET_SIGNED_URL_SPEC, get_signed_url, node_registry=None)

ELEVENLABS_CONVAI_INTEGRATION = IntegrationSpec(
    id="elevenlabs_convai",
    name="ElevenLabs ConvAI",
    description="Manage AI voice conversations with ElevenLabs Conversational AI.",
    icon="brand:elevenlabs",
    credential_types=("elevenlabs",),
    resources=(
        ResourceSpec(
            id="convai_agent",
            name="Agent",
            operations=(
                ELEVENLABS_CREATE_AGENT_SPEC,
                ELEVENLABS_GET_AGENT_SPEC,
                ELEVENLABS_LIST_AGENTS_SPEC,
                ELEVENLABS_GET_SIGNED_URL_SPEC,
            ),
        ),
        ResourceSpec(
            id="conversation",
            name="Conversation",
            operations=(
                ELEVENLABS_GET_CONVERSATION_SPEC,
                ELEVENLABS_LIST_CONVERSATIONS_SPEC,
            ),
        ),
    ),
)

register_integration(ELEVENLABS_CONVAI_INTEGRATION)
