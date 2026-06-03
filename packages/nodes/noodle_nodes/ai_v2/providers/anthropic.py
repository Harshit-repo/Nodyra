"""Anthropic ChatModelAdapter.

Supports Claude-3.x and Claude-3.7+.  Uses only ``requests`` — no
anthropic SDK.

Tool calls use Anthropic's native tool_use / tool_result format and are
normalized to the shared ``ToolCall`` / ``ToolResult`` types.
"""

from __future__ import annotations

from typing import Any

import requests

from noodle.ai_runtime import (
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

_ANTHROPIC_BASE = "https://api.anthropic.com/v1/messages"
_DEFAULT_ANTHROPIC_VERSION = "2023-06-01"


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


def _split_system(messages: list[AIMessage]) -> tuple[str, list[AIMessage]]:
    """Separate system messages from the rest (Anthropic uses a top-level field)."""
    system_parts = [m.content for m in messages if m.role == MessageRole.system]
    non_system = [m for m in messages if m.role != MessageRole.system]
    return "\n\n".join(p for p in system_parts if p), non_system


def _messages_to_anthropic(messages: list[AIMessage]) -> list[dict[str, Any]]:
    """Convert to Anthropic message format.

    Tool result messages become role=user with type=tool_result content blocks.
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == MessageRole.tool:
            # Anthropic tool results are user messages with content blocks
            result.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id or "",
                            "content": msg.content,
                        }
                    ],
                }
            )
        elif msg.role == MessageRole.assistant and msg.tool_calls:
            content: list[dict[str, Any]] = []
            if msg.content:
                content.append({"type": "text", "text": msg.content})
            content.extend(
                {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": call.arguments,
                }
                for call in msg.tool_calls
            )
            result.append({"role": "assistant", "content": content})
        else:
            result.append(
                {
                    "role": msg.role.value,
                    "content": msg.content,
                }
            )
    return result


def _tools_to_anthropic(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    result = []
    for tool in tools:
        params = tool.parameters.model_dump(exclude_none=True, by_alias=True)
        params.pop("additional_properties", None)
        result.append(
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": params,
            }
        )
    return result


def _normalize_response(body: dict[str, Any]) -> ChatResponse:
    content = body.get("content") if isinstance(body.get("content"), list) else []
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []

    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text_parts.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use":
            raw_input = block.get("input") or {}
            tool_calls.append(
                ToolCall(
                    id=str(block.get("id") or ""),
                    name=str(block.get("name") or ""),
                    arguments=raw_input if isinstance(raw_input, dict) else {},
                )
            )

    raw_usage = body.get("usage") or {}
    usage = ModelUsage(
        prompt_tokens=int(raw_usage.get("input_tokens") or 0),
        completion_tokens=int(raw_usage.get("output_tokens") or 0),
        total_tokens=int(
            (raw_usage.get("input_tokens") or 0) + (raw_usage.get("output_tokens") or 0)
        ),
    )

    return ChatResponse(
        text="".join(text_parts),
        tool_calls=tool_calls,
        usage=usage,
        model=str(body.get("model") or ""),
        finish_reason=str(body.get("stop_reason") or ""),
        provider="anthropic",
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


class AnthropicChatAdapter(ChatModelAdapter):
    """Adapter for Anthropic Claude models.

    Config keys:
    - ``api_key`` (required)
    - ``model`` (defaults to ``claude-3-5-haiku-latest``)
    - ``base_url`` (defaults to Anthropic API)
    - ``anthropic_version`` (defaults to ``2023-06-01``)
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-3-5-haiku-latest",
        base_url: str = "",
        anthropic_version: str = _DEFAULT_ANTHROPIC_VERSION,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: int = 75,
        prompt_price_per_1m_tokens: float | None = None,
        completion_price_per_1m_tokens: float | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("AnthropicChatAdapter requires api_key")
        self._api_key = api_key
        self._model = model or "claude-3-5-haiku-latest"
        self._base_url = (base_url or _ANTHROPIC_BASE).rstrip("/")
        if not self._base_url.endswith("/messages"):
            self._base_url = f"{self._base_url}/messages"
        self._anthropic_version = anthropic_version or _DEFAULT_ANTHROPIC_VERSION
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout_seconds = int(timeout_seconds or 75)
        self._prompt_price_per_1m_tokens = prompt_price_per_1m_tokens
        self._completion_price_per_1m_tokens = completion_price_per_1m_tokens

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            supports_tools=True,
            supports_vision="claude-3" in self._model,
            supports_json_mode=False,  # Anthropic doesn't have a JSON mode flag
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
        effective_max_tokens = request.max_tokens or self._max_tokens or 1024
        effective_timeout = request.timeout_seconds or self._timeout_seconds or 75
        timeout = max(1, min(300, effective_timeout))
        model = request.model or self._model
        system, non_system_messages = _split_system(request.messages)

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": int(effective_max_tokens),
            "messages": _messages_to_anthropic(non_system_messages),
            "temperature": float(effective_temp),
        }
        if system:
            payload["system"] = system
        if request.tools:
            payload["tools"] = _tools_to_anthropic(request.tools)

        resp = requests.post(
            self._base_url,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": self._anthropic_version,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        body = _expect_json(resp, "anthropic")
        return _with_cost_estimate(
            _normalize_response(body),
            prompt_price_per_1m_tokens=self._prompt_price_per_1m_tokens,
            completion_price_per_1m_tokens=self._completion_price_per_1m_tokens,
        )

    def as_config(self) -> dict[str, Any]:
        return {
            "adapter": "anthropic",
            "api_key": self._api_key,
            "model": self._model,
            "base_url": self._base_url,
            "anthropic_version": self._anthropic_version,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "timeout_seconds": self._timeout_seconds,
            "prompt_price_per_1m_tokens": self._prompt_price_per_1m_tokens,
            "completion_price_per_1m_tokens": self._completion_price_per_1m_tokens,
        }

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> AnthropicChatAdapter:
        return cls(
            api_key=str(config.get("api_key") or ""),
            model=str(config.get("model") or "claude-3-5-haiku-latest"),
            base_url=str(config.get("base_url") or ""),
            anthropic_version=str(
                config.get("anthropic_version") or _DEFAULT_ANTHROPIC_VERSION
            ),
            temperature=config.get("temperature"),
            max_tokens=config.get("max_tokens"),
            timeout_seconds=int(config.get("timeout_seconds") or 75),
            prompt_price_per_1m_tokens=config.get("prompt_price_per_1m_tokens"),
            completion_price_per_1m_tokens=config.get(
                "completion_price_per_1m_tokens"
            ),
        )
