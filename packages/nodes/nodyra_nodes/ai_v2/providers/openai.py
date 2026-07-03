"""OpenAI-compatible ChatModelAdapter.

Covers: OpenAI, Azure OpenAI, Ollama, OpenRouter, and any
``openai_compatible`` endpoint.  Uses only ``requests`` — no openai SDK.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

import requests

from nodyra.ai_runtime import (
    AIMessage,
    ChatModelAdapter,
    ChatRequest,
    ChatResponse,
    MessageRole,
    ModelCapabilities,
    ModelUsage,
    ToolCall,
    ToolSchema,
)

_OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_OPENAI_BASE = "https://api.openai.com/v1"

# Models known to support function/tool calling
_TOOL_CAPABLE_PREFIXES = (
    "gpt-4",
    "gpt-3.5-turbo",
    "o1",
    "o3",
    "o4",
    "openai/",
    "anthropic/",
    "google/",
    "mistralai/",
    "meta-llama/",
)

# Models known to support JSON mode
_JSON_MODE_PREFIXES = ("gpt-4", "gpt-3.5-turbo", "o1", "o3", "o4")


def _model_supports_tools(model: str) -> bool:
    m = (model or "").lower()
    return any(m.startswith(p) for p in _TOOL_CAPABLE_PREFIXES)


def _model_supports_json(model: str) -> bool:
    m = (model or "").lower()
    return any(m.startswith(p) for p in _JSON_MODE_PREFIXES)


def _with_chat_completions(base_url: str) -> str:
    base = (base_url or _OPENAI_BASE).rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _raise_if_tools_unsupported(
    response: requests.Response, *, service: str, model: str, has_tools: bool
) -> None:
    """Turn a provider's "this model can't do tools" rejection into guidance.

    OpenRouter/OpenAI answer a tool-bearing request to a non-tool model with a
    400/404 mentioning ``tool_choice``/``tools``/``function``. The raw body is
    opaque, so when we attached tools and the failure is tool-related, raise a
    message that tells the user exactly what to change.
    """
    if not has_tools or response.status_code < 400:
        return
    body = response.text[:800]
    lowered = body.lower()
    if "tool" not in lowered and "function" not in lowered:
        return
    raise RuntimeError(
        f"Model '{model}' ({service}) does not support tool calling, but this "
        f"Agent has tool(s) attached. Pick a tool-capable model — e.g. "
        f"openai/gpt-4o-mini, anthropic/claude-3.7-sonnet, or google/gemini-2.5-flash "
        f"on OpenRouter — or disconnect the tool node(s). Provider said: {body}"
    )


def _expect_json(response: requests.Response, service: str) -> dict[str, Any]:
    if response.status_code >= 400:
        body = response.text[:800]
        raise RuntimeError(f"{service}: HTTP {response.status_code}: {body}")
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{service}: expected JSON response") from exc
    if not isinstance(body, dict):
        raise RuntimeError(f"{service}: expected JSON object response")
    return body


def _content_text(content: Any) -> str:
    """Message ``content`` as plain text. Providers behind OpenRouter may
    return a list of typed parts instead of a string; join the text parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    return "" if content is None else str(content)


def _normalize_response(body: dict[str, Any], provider: str) -> ChatResponse:
    choices = body.get("choices") if isinstance(body.get("choices"), list) else []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    text = _content_text(message.get("content"))

    raw_tool_calls = message.get("tool_calls") or []
    tool_calls: list[ToolCall] = []
    for tc in raw_tool_calls:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except (ValueError, TypeError):
            args = {"_raw": raw_args}
        tool_calls.append(
            ToolCall(
                id=str(tc.get("id") or ""),
                name=str(fn.get("name") or ""),
                arguments=args if isinstance(args, dict) else {"_raw": args},
            )
        )

    raw_usage = body.get("usage") or {}
    usage = ModelUsage(
        prompt_tokens=int(raw_usage.get("prompt_tokens") or 0),
        completion_tokens=int(raw_usage.get("completion_tokens") or 0),
        total_tokens=int(raw_usage.get("total_tokens") or 0),
    )

    return ChatResponse(
        text=text,
        tool_calls=tool_calls,
        usage=usage,
        model=str(body.get("model") or ""),
        finish_reason=str(first.get("finish_reason") or ""),
        provider=provider,
    )


def _with_cost_estimate(
    response: ChatResponse,
    *,
    prompt_price_per_1m_tokens: float | None,
    completion_price_per_1m_tokens: float | None,
) -> ChatResponse:
    usage = response.usage.with_estimated_cost(
        prompt_price_per_1m_tokens=prompt_price_per_1m_tokens,
        completion_price_per_1m_tokens=completion_price_per_1m_tokens,
    )
    return response.model_copy(update={"usage": usage})


def _messages_to_openai(messages: list[AIMessage]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for msg in messages:
        item: dict[str, Any] = {
            "role": msg.role.value,
            "content": msg.content,
        }
        if msg.role == MessageRole.assistant and msg.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    },
                }
                for call in msg.tool_calls
            ]
        if msg.role == MessageRole.tool:
            item["tool_call_id"] = msg.tool_call_id or ""
            if msg.name:
                item["name"] = msg.name
        result.append(item)
    return result


def _tools_to_openai(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    result = []
    for tool in tools:
        params = tool.parameters.model_dump(exclude_none=True, by_alias=True)
        params.pop("additional_properties", None)
        result.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": params,
                },
            }
        )
    return result


class OpenAIChatAdapter(ChatModelAdapter):
    """Adapter for OpenAI, Ollama, OpenRouter, and openai_compatible endpoints.

    Config keys (all optional except those noted):
    - ``api_key`` (required for OpenAI / OpenRouter)
    - ``base_url``
    - ``model``
    - ``provider`` — one of ``openai``, ``openai_compatible``, ``ollama``,
      ``openrouter``
    - ``organization``
    - ``site_url`` / ``app_name`` — OpenRouter headers
    - ``temperature``, ``max_tokens``, ``timeout_seconds``
    """

    _PROVIDER_KEY = "openai"

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "",
        model: str = "gpt-4.1-mini",
        provider: str = "openai",
        organization: str = "",
        site_url: str = "",
        app_name: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: int = 75,
        prompt_price_per_1m_tokens: float | None = None,
        completion_price_per_1m_tokens: float | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._provider = provider.strip().lower() or "openai"
        self._organization = organization
        self._site_url = site_url
        self._app_name = app_name
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout_seconds = int(timeout_seconds or 75)
        self._prompt_price_per_1m_tokens = prompt_price_per_1m_tokens
        self._completion_price_per_1m_tokens = completion_price_per_1m_tokens

    # ------------------------------------------------------------------
    # ChatModelAdapter interface
    # ------------------------------------------------------------------

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            supports_tools=_model_supports_tools(self._model),
            supports_json_mode=_model_supports_json(self._model),
            supports_vision=self._model.startswith(("gpt-4", "gpt-4o", "gpt-4.1")),
        )

    def complete(self, request: ChatRequest) -> ChatResponse:
        provider = self._provider
        if provider == "ollama":
            base_url = self._base_url or "http://localhost:11434/v1"
            api_key = self._api_key or "ollama"
        elif provider == "openrouter":
            base_url = self._base_url or _OPENROUTER_BASE
            api_key = self._api_key
            if not api_key:
                raise ValueError("OpenRouter requires an api_key")
        elif provider == "openai_compatible":
            base_url = self._base_url
            api_key = self._api_key
            if not base_url:
                raise ValueError("openai_compatible provider requires a base_url")
        else:
            base_url = self._base_url or _OPENAI_BASE
            api_key = self._api_key
            if not api_key:
                raise ValueError("OpenAI requires an api_key")

        model = request.model or self._model

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if self._organization:
            headers["OpenAI-Organization"] = self._organization
        if provider == "openrouter":
            if self._site_url:
                headers["HTTP-Referer"] = self._site_url
            if self._app_name:
                headers["X-Title"] = self._app_name

        effective_temp = (
            request.temperature
            if request.temperature is not None
            else (
                self._temperature
                if self._temperature is not None
                else 0.2
            )
        )
        effective_max_tokens = request.max_tokens or self._max_tokens
        effective_timeout = request.timeout_seconds or self._timeout_seconds or 75

        payload: dict[str, Any] = {
            "model": model,
            "messages": _messages_to_openai(request.messages),
            "temperature": float(effective_temp),
        }
        if effective_max_tokens:
            payload["max_tokens"] = int(effective_max_tokens)
        if request.response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        if request.tools:
            payload["tools"] = _tools_to_openai(request.tools)
            payload["tool_choice"] = "auto"

        resp = requests.post(
            _with_chat_completions(base_url),
            headers=headers,
            json=payload,
            timeout=max(1, min(300, effective_timeout)),
        )
        _raise_if_tools_unsupported(
            resp, service=provider, model=model, has_tools=bool(request.tools)
        )
        body = _expect_json(resp, provider)
        return _with_cost_estimate(
            _normalize_response(body, provider),
            prompt_price_per_1m_tokens=self._prompt_price_per_1m_tokens,
            completion_price_per_1m_tokens=self._completion_price_per_1m_tokens,
        )

    def as_config(self) -> dict[str, Any]:
        return {
            "adapter": "openai",
            "api_key": self._api_key,
            "base_url": self._base_url,
            "model": self._model,
            "provider": self._provider,
            "organization": self._organization,
            "site_url": self._site_url,
            "app_name": self._app_name,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "timeout_seconds": self._timeout_seconds,
            "prompt_price_per_1m_tokens": self._prompt_price_per_1m_tokens,
            "completion_price_per_1m_tokens": self._completion_price_per_1m_tokens,
        }

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> OpenAIChatAdapter:
        return cls(
            api_key=str(config.get("api_key") or ""),
            base_url=str(config.get("base_url") or ""),
            model=str(config.get("model") or "gpt-4.1-mini"),
            provider=str(config.get("provider") or "openai"),
            organization=str(config.get("organization") or ""),
            site_url=str(config.get("site_url") or ""),
            app_name=str(config.get("app_name") or ""),
            temperature=config.get("temperature"),
            max_tokens=config.get("max_tokens"),
            timeout_seconds=int(config.get("timeout_seconds") or 75),
            prompt_price_per_1m_tokens=config.get("prompt_price_per_1m_tokens"),
            completion_price_per_1m_tokens=config.get(
                "completion_price_per_1m_tokens"
            ),
        )


class AzureOpenAIChatAdapter(ChatModelAdapter):
    """Adapter for Azure OpenAI deployments.

    Config keys:
    - ``api_key`` (required)
    - ``azure_endpoint`` / ``base_url`` (required)
    - ``deployment`` / ``model`` (required)
    - ``azure_api_version`` (optional, defaults to 2024-02-15-preview)
    """

    def __init__(
        self,
        *,
        api_key: str,
        azure_endpoint: str,
        deployment: str,
        azure_api_version: str = "2024-02-15-preview",
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: int = 75,
        prompt_price_per_1m_tokens: float | None = None,
        completion_price_per_1m_tokens: float | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("AzureOpenAIChatAdapter requires api_key")
        if not azure_endpoint:
            raise ValueError("AzureOpenAIChatAdapter requires azure_endpoint")
        if not deployment:
            raise ValueError("AzureOpenAIChatAdapter requires deployment")
        self._api_key = api_key
        self._azure_endpoint = azure_endpoint.rstrip("/")
        self._deployment = deployment
        self._api_version = azure_api_version or "2024-02-15-preview"
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout_seconds = int(timeout_seconds or 75)
        self._prompt_price_per_1m_tokens = prompt_price_per_1m_tokens
        self._completion_price_per_1m_tokens = completion_price_per_1m_tokens

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            supports_tools=_model_supports_tools(self._deployment),
            supports_json_mode=_model_supports_json(self._deployment),
        )

    def complete(self, request: ChatRequest) -> ChatResponse:
        effective_temp = (
            request.temperature
            if request.temperature is not None
            else (
                self._temperature
                if self._temperature is not None
                else 0.2
            )
        )
        effective_max_tokens = request.max_tokens or self._max_tokens
        effective_timeout = request.timeout_seconds or self._timeout_seconds or 75
        timeout = max(1, min(300, effective_timeout))
        url = (
            f"{self._azure_endpoint}/openai/deployments/{self._deployment}"
            f"/chat/completions?{urlencode({'api-version': self._api_version})}"
        )
        payload: dict[str, Any] = {
            "messages": _messages_to_openai(request.messages),
            "temperature": float(effective_temp),
        }
        if effective_max_tokens:
            payload["max_tokens"] = int(effective_max_tokens)
        if request.response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        if request.tools:
            payload["tools"] = _tools_to_openai(request.tools)
            payload["tool_choice"] = "auto"

        resp = requests.post(
            url,
            headers={"api-key": self._api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        _raise_if_tools_unsupported(
            resp,
            service="azure_openai",
            model=self._deployment,
            has_tools=bool(request.tools),
        )
        body = _expect_json(resp, "azure_openai")
        return _with_cost_estimate(
            _normalize_response(body, "azure_openai"),
            prompt_price_per_1m_tokens=self._prompt_price_per_1m_tokens,
            completion_price_per_1m_tokens=self._completion_price_per_1m_tokens,
        )

    def as_config(self) -> dict[str, Any]:
        return {
            "adapter": "azure_openai",
            "api_key": self._api_key,
            "azure_endpoint": self._azure_endpoint,
            "deployment": self._deployment,
            "azure_api_version": self._api_version,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "timeout_seconds": self._timeout_seconds,
            "prompt_price_per_1m_tokens": self._prompt_price_per_1m_tokens,
            "completion_price_per_1m_tokens": self._completion_price_per_1m_tokens,
        }

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> AzureOpenAIChatAdapter:
        endpoint = str(config.get("azure_endpoint") or config.get("base_url") or "")
        return cls(
            api_key=str(config.get("api_key") or ""),
            azure_endpoint=endpoint,
            deployment=str(config.get("deployment") or config.get("model") or ""),
            azure_api_version=str(
                config.get("azure_api_version") or "2024-02-15-preview"
            ),
            temperature=config.get("temperature"),
            max_tokens=config.get("max_tokens"),
            timeout_seconds=int(config.get("timeout_seconds") or 75),
            prompt_price_per_1m_tokens=config.get("prompt_price_per_1m_tokens"),
            completion_price_per_1m_tokens=config.get(
                "completion_price_per_1m_tokens"
            ),
        )
