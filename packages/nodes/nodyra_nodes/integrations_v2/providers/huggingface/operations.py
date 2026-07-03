"""HuggingFace Inference v2 operation specs and executors."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from nodyra.models import CredentialSpec
from nodyra_nodes.integrations_v2.registry import register_integration, register_operation
from nodyra_nodes.integrations_v2.specs import (
    IntegrationSpec,
    OperationParamSpec,
    OperationSpec,
    ResourceSpec,
)
from nodyra_nodes.integrations_v2.transport import ProviderTransport

HUGGINGFACE_ROUTER_BASE = "https://router.huggingface.co"


def _credentials_param() -> OperationParamSpec:
    return OperationParamSpec(
        name="credentials",
        type="credential",
        required=True,
        credential=CredentialSpec(
            type="huggingface_api",
            key="*",
            label="HuggingFace API token",
            fields=["api_token"],
            multi=True,
            test_service="huggingface_api",
        ),
        description="HuggingFace access token with Inference Providers permission.",
    )


def _credentials_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if isinstance(value, str):
        return {"api_token": value}
    return {}


def _transport(credentials: Any) -> ProviderTransport:
    creds = _credentials_dict(credentials)
    api_token = str(creds.get("api_token") or creds.get("api_key") or "")
    if not api_token:
        raise ValueError("huggingface: api_token is required")
    return ProviderTransport(
        provider="huggingface",
        base_url=HUGGINGFACE_ROUTER_BASE,
        default_headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
    )


def _text_from_input(input_value: Any, text: str = "") -> str:
    if text:
        return str(text)
    if input_value is None:
        return ""
    return str(input_value)


def _router_model(model: str, provider: str = "hf-inference") -> str:
    clean_model = str(model or "").strip()
    clean_provider = str(provider or "").strip()
    if clean_provider and ":" not in clean_model:
        return f"{clean_model}:{clean_provider}"
    return clean_model


def _model_path(model: str) -> str:
    return quote(str(model or "").strip(), safe="/:-._")


def _text_from_chat_response(result: Any) -> str:
    if isinstance(result, dict):
        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict):
                message = choice.get("message")
                if isinstance(message, dict) and message.get("content") is not None:
                    return str(message["content"])
                if choice.get("text") is not None:
                    return str(choice["text"])
        for key in ("generated_text", "text", "content"):
            if result.get(key) is not None:
                return str(result[key])
    if isinstance(result, list) and result:
        first = result[0]
        if isinstance(first, dict):
            for key in ("generated_text", "text", "content"):
                if first.get(key) is not None:
                    return str(first[key])
        return str(first)
    return "" if result is None else str(result)


def _is_number_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, int | float) for item in value)


def _embedding_from_response(result: Any) -> Any:
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict) and first.get("embedding") is not None:
                return first["embedding"]
        if result.get("embedding") is not None:
            return result["embedding"]
    if isinstance(result, list):
        if _is_number_list(result):
            return result
        if result and _is_number_list(result[0]):
            return result[0]
    return result


HUGGINGFACE_TEXT_GENERATION_SPEC = OperationSpec(
    node_id="huggingface_text_generation_v2",
    name="HuggingFace Text Generation",
    provider="huggingface",
    resource="inference",
    operation="text_generation",
    description="Generate text through HuggingFace Inference Providers chat completions.",
    icon="brand:huggingface",
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="model",
            required=True,
            placeholder="Qwen/Qwen3-235B-A22B",
            description="HuggingFace model ID. A provider suffix is added when provider is set.",
        ),
        OperationParamSpec(
            name="prompt",
            required=True,
            multiline=True,
            placeholder="Ask the model to do something...",
        ),
        OperationParamSpec(
            name="router_provider",
            default="hf-inference",
            group="Options",
            advanced=True,
            description="Router provider suffix. Leave blank if the model value already includes one.",
        ),
        OperationParamSpec(
            name="max_tokens",
            type="number",
            default=512,
            group="Options",
        ),
        OperationParamSpec(
            name="temperature",
            type="number",
            default=0.7,
            group="Options",
        ),
    ),
)

HUGGINGFACE_EMBEDDINGS_SPEC = OperationSpec(
    node_id="huggingface_embeddings_v2",
    name="HuggingFace Embeddings",
    provider="huggingface",
    resource="inference",
    operation="embeddings",
    description="Create embedding vectors through HuggingFace HF Inference feature extraction.",
    icon="brand:huggingface",
    tool_side_effecting=False,
    params=(
        _credentials_param(),
        OperationParamSpec(
            name="model",
            required=True,
            placeholder="sentence-transformers/all-MiniLM-L6-v2",
        ),
        OperationParamSpec(
            name="input_text",
            required=True,
            multiline=True,
            placeholder="Text to embed. Uses input payload if blank.",
        ),
        OperationParamSpec(
            name="normalize",
            type="boolean",
            default=None,
            group="Options",
        ),
        OperationParamSpec(
            name="truncate",
            type="boolean",
            default=None,
            group="Options",
        ),
    ),
)


def text_generation(
    *,
    input: Any = None,
    credentials: Any = None,
    model: str = "Qwen/Qwen3-235B-A22B",
    prompt: str = "",
    router_provider: str = "hf-inference",
    max_tokens: int = 512,
    temperature: float = 0.7,
) -> dict[str, Any]:
    body_prompt = _text_from_input(input, prompt)
    if not model:
        raise ValueError("huggingface_text_generation_v2: model is required")
    if not body_prompt:
        raise ValueError("huggingface_text_generation_v2: prompt is required")
    body: dict[str, Any] = {
        "model": _router_model(model, router_provider),
        "messages": [{"role": "user", "content": body_prompt}],
        "max_tokens": int(max_tokens or 512),
        "temperature": float(temperature if temperature is not None else 0.7),
    }
    result = _transport(credentials).request(
        "POST",
        "/v1/chat/completions",
        operation="text_generation",
        json_body=body,
    )
    return {"text": _text_from_chat_response(result), "raw": result}


def embeddings(
    *,
    input: Any = None,
    credentials: Any = None,
    model: str = "sentence-transformers/all-MiniLM-L6-v2",
    input_text: str = "",
    normalize: bool | None = None,
    truncate: bool | None = None,
) -> dict[str, Any]:
    body_input = _text_from_input(input, input_text)
    if not model:
        raise ValueError("huggingface_embeddings_v2: model is required")
    if not body_input:
        raise ValueError("huggingface_embeddings_v2: input_text is required")
    body: dict[str, Any] = {"inputs": body_input}
    if normalize is not None:
        body["normalize"] = bool(normalize)
    if truncate is not None:
        body["truncate"] = bool(truncate)
    result = _transport(credentials).request(
        "POST",
        f"/hf-inference/models/{_model_path(model)}",
        operation="embeddings",
        json_body=body,
    )
    return {"embedding": _embedding_from_response(result), "raw": result}


register_operation(
    HUGGINGFACE_TEXT_GENERATION_SPEC,
    text_generation,
    node_registry=None,
)
register_operation(HUGGINGFACE_EMBEDDINGS_SPEC, embeddings, node_registry=None)

HUGGINGFACE_INTEGRATION = IntegrationSpec(
    id="huggingface",
    name="HuggingFace",
    description="Generate text and embeddings with HuggingFace Inference Providers.",
    icon="brand:huggingface",
    credential_types=("huggingface_api",),
    resources=(
        ResourceSpec(
            id="inference",
            name="Inference",
            operations=(
                HUGGINGFACE_TEXT_GENERATION_SPEC,
                HUGGINGFACE_EMBEDDINGS_SPEC,
            ),
        ),
    ),
)

register_integration(HUGGINGFACE_INTEGRATION)
