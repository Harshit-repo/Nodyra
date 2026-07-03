"""OpenAI v2 operation specs and executors."""

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

OPENAI_API_BASE = "https://api.openai.com/v1"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="openai",
            key="*",
            label="OpenAI API key",
            fields=["api_key"],
            multi=True,
        ),
        description="OpenAI API key.",
    )


def _transport(credentials: Any) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    api_key = str(creds.get("api_key") or "")
    if not api_key:
        raise ValueError("openai: api_key is required")
    return ProviderTransport(
        provider="openai",
        base_url=OPENAI_API_BASE,
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


def _text_from_input(input_value: Any, text: str = "") -> str:
    if text:
        return text
    if input_value is None:
        return ""
    return str(input_value)


def _parse_json(value: Any) -> Any:
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value


OPENAI_CHAT_SPEC = OperationSpec(
    node_id="openai_chat_v2",
    name="OpenAI Chat",
    provider="openai",
    resource="chat",
    operation="create",
    description="Send a chat completion request to OpenAI.",
    icon="brand:openai",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="model",
            required=True,
            default="gpt-4o",
            choices=("gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo", "o3-mini"),
        ),
        OperationParamSpec(
            name="messages",
            required=True,
            type="json",
            multiline=True,
            placeholder='[{"role": "user", "content": "Hello"}]',
        ),
        OperationParamSpec(
            name="temperature",
            type="number",
            default=1,
            group="Options",
        ),
        OperationParamSpec(
            name="max_tokens",
            type="number",
            default=4096,
            group="Options",
        ),
        OperationParamSpec(
            name="top_p",
            type="number",
            default=1,
            group="Options",
        ),
    ),
)

OPENAI_CREATE_EMBEDDING_SPEC = OperationSpec(
    node_id="openai_create_embedding_v2",
    name="OpenAI Create Embedding",
    provider="openai",
    resource="embedding",
    operation="create",
    description="Create an embedding vector for the provided input text.",
    icon="brand:openai",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="model",
            default="text-embedding-3-small",
            choices=("text-embedding-3-small", "text-embedding-3-large", "text-embedding-ada-002"),
        ),
        OperationParamSpec(
            name="input_text",
            required=True,
            multiline=True,
            placeholder="Text to embed. Uses input payload if blank.",
        ),
        OperationParamSpec(
            name="encoding_format",
            group="Options",
            choices=("float", "base64"),
        ),
    ),
)

OPENAI_CREATE_IMAGE_SPEC = OperationSpec(
    node_id="openai_create_image_v2",
    name="OpenAI Create Image",
    provider="openai",
    resource="image",
    operation="create",
    description="Generate an image using DALL-E.",
    icon="brand:openai",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="prompt",
            required=True,
            multiline=True,
            placeholder="Image description. Uses input payload if blank.",
        ),
        OperationParamSpec(
            name="model",
            default="dall-e-3",
            choices=("dall-e-3", "dall-e-2"),
        ),
        OperationParamSpec(name="n", type="number", default=1),
        OperationParamSpec(
            name="size",
            default="1024x1024",
            choices=("256x256", "512x512", "1024x1024", "1792x1024", "1024x1792"),
        ),
        OperationParamSpec(
            name="quality",
            default="standard",
            choices=("standard", "hd"),
        ),
    ),
)

OPENAI_CREATE_SPEECH_SPEC = OperationSpec(
    node_id="openai_create_speech_v2",
    name="OpenAI Create Speech",
    provider="openai",
    resource="audio",
    operation="create_speech",
    description="Convert text to speech using OpenAI TTS.",
    icon="brand:openai",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="model",
            default="tts-1",
            choices=("tts-1", "tts-1-hd"),
        ),
        OperationParamSpec(
            name="input_text",
            required=True,
            multiline=True,
            placeholder="Text to speak. Uses input payload if blank.",
        ),
        OperationParamSpec(
            name="voice",
            default="alloy",
            choices=("alloy", "echo", "fable", "onyx", "nova", "shimmer"),
        ),
        OperationParamSpec(
            name="response_format",
            group="Options",
            choices=("mp3", "opus", "aac", "flac", "wav"),
        ),
        OperationParamSpec(
            name="speed",
            type="number",
            default=1.0,
            group="Options",
        ),
    ),
)

OPENAI_LIST_MODELS_SPEC = OperationSpec(
    node_id="openai_list_models_v2",
    name="OpenAI List Models",
    provider="openai",
    resource="model",
    operation="list",
    description="List all available OpenAI models.",
    icon="brand:openai",
    tool_side_effecting=False,
    params=(_credentials_param(),),
)

OPENAI_CREATE_ASSISTANT_SPEC = OperationSpec(
    node_id="openai_create_assistant_v2",
    name="OpenAI Create Assistant",
    provider="openai",
    resource="assistant",
    operation="create",
    description="Create a new OpenAI assistant.",
    icon="brand:openai",
    params=(
        _credentials_param(),
        OperationParamSpec(name="name", required=True, placeholder="Assistant name"),
        OperationParamSpec(
            name="instructions",
            required=True,
            multiline=True,
            placeholder="System instructions for the assistant.",
        ),
        OperationParamSpec(name="model", required=True),
        OperationParamSpec(
            name="tools",
            type="json",
            group="Options",
            placeholder='[{"type": "code_interpreter"}]',
        ),
        OperationParamSpec(name="description", group="Options"),
    ),
)


def chat(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    model: str = "gpt-4o",
    messages: Any = None,
    temperature: float = 1,
    max_tokens: int = 4096,
    top_p: float = 1,
) -> Any:
    if not messages:
        raise ValueError("openai_chat_v2: messages is required")
    parsed_messages = _parse_json(messages)
    body: dict[str, Any] = {
        "model": model,
        "messages": parsed_messages,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "top_p": float(top_p),
    }
    return _transport(credentials).request(
        "POST",
        "/chat/completions",
        operation="chat",
        json_body=body,
    )


def create_embedding(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    model: str = "text-embedding-3-small",
    input_text: str = "",
    encoding_format: str = "",
) -> Any:
    body_input = _text_from_input(input, input_text)
    if not body_input:
        raise ValueError("openai_create_embedding_v2: input is required")
    body: dict[str, Any] = {"model": model, "input": body_input}
    if encoding_format:
        body["encoding_format"] = encoding_format
    return _transport(credentials).request(
        "POST",
        "/embeddings",
        operation="create_embedding",
        json_body=body,
    )


def create_image(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    prompt: str = "",
    model: str = "dall-e-3",
    n: int = 1,
    size: str = "1024x1024",
    quality: str = "standard",
) -> Any:
    body_prompt = _text_from_input(input, prompt)
    if not body_prompt:
        raise ValueError("openai_create_image_v2: prompt is required")
    body: dict[str, Any] = {
        "model": model,
        "prompt": body_prompt,
        "n": max(1, int(n)),
        "size": size,
        "quality": quality,
    }
    return _transport(credentials).request(
        "POST",
        "/images/generations",
        operation="create_image",
        json_body=body,
    )


def create_speech(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    model: str = "tts-1",
    input_text: str = "",
    voice: str = "alloy",
    response_format: str = "",
    speed: float = 1.0,
) -> Any:
    body_input = _text_from_input(input, input_text)
    if not body_input:
        raise ValueError("openai_create_speech_v2: input is required")
    body: dict[str, Any] = {
        "model": model,
        "input": body_input,
        "voice": voice,
        "speed": float(speed),
    }
    if response_format:
        body["response_format"] = response_format
    return _transport(credentials).request(
        "POST",
        "/audio/speech",
        operation="create_speech",
        json_body=body,
    )


def list_models(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
) -> Any:
    return _transport(credentials).request(
        "GET",
        "/models",
        operation="list_models",
    )


def create_assistant(
    *,
    input: Any = None,
    credentials: dict[str, str] | None = None,
    name: str = "",
    instructions: str = "",
    model: str = "",
    tools: Any = None,
    description: str = "",
) -> Any:
    if not name:
        raise ValueError("openai_create_assistant_v2: name is required")
    if not instructions:
        raise ValueError("openai_create_assistant_v2: instructions is required")
    if not model:
        raise ValueError("openai_create_assistant_v2: model is required")
    body: dict[str, Any] = {
        "name": name,
        "instructions": instructions,
        "model": model,
    }
    if tools:
        parsed_tools = _parse_json(tools)
        if isinstance(parsed_tools, list):
            body["tools"] = parsed_tools
    if description:
        body["description"] = description
    return _transport(credentials).request(
        "POST",
        "/assistants",
        operation="create_assistant",
        json_body=body,
    )


register_operation(OPENAI_CHAT_SPEC, chat, node_registry=None)
register_operation(OPENAI_CREATE_EMBEDDING_SPEC, create_embedding, node_registry=None)
register_operation(OPENAI_CREATE_IMAGE_SPEC, create_image, node_registry=None)
register_operation(OPENAI_CREATE_SPEECH_SPEC, create_speech, node_registry=None)
register_operation(OPENAI_LIST_MODELS_SPEC, list_models, node_registry=None)
register_operation(OPENAI_CREATE_ASSISTANT_SPEC, create_assistant, node_registry=None)

OPENAI_V2_INTEGRATION = IntegrationSpec(
    id="openai",
    name="OpenAI",
    description="Chat, generate embeddings, create images, convert text to speech, and manage assistants using OpenAI APIs.",
    icon="brand:openai",
    credential_types=("openai",),
    resources=(
        ResourceSpec(
            id="chat",
            name="Chat",
            operations=(OPENAI_CHAT_SPEC,),
        ),
        ResourceSpec(
            id="embedding",
            name="Embedding",
            operations=(OPENAI_CREATE_EMBEDDING_SPEC,),
        ),
        ResourceSpec(
            id="image",
            name="Image",
            operations=(OPENAI_CREATE_IMAGE_SPEC,),
        ),
        ResourceSpec(
            id="audio",
            name="Audio",
            operations=(OPENAI_CREATE_SPEECH_SPEC,),
        ),
        ResourceSpec(
            id="model",
            name="Model",
            operations=(OPENAI_LIST_MODELS_SPEC,),
        ),
        ResourceSpec(
            id="assistant",
            name="Assistant",
            operations=(OPENAI_CREATE_ASSISTANT_SPEC,),
        ),
    ),
)

register_integration(OPENAI_V2_INTEGRATION)
