"""Tests for WP9 — AI runtime contracts and provider adapters.

All provider HTTP calls are mocked; no real network calls are made.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from noodle.ai_runtime import (
    AgentActionRequest,
    AgentActionResponse,
    AgentResumeInput,
    AIMessage,
    ChatModelAdapter,
    ChatRequest,
    EmbeddingModelAdapter,
    EmbeddingRequest,
    GuardrailAdapter,
    MemoryAdapter,
    MessageRole,
    ModelUsage,
    OutputParserAdapter,
    ToolAdapter,
    ToolCall,
    ToolParameterSchema,
    ToolResult,
    ToolSchema,
)

# ---------------------------------------------------------------------------
# AIMessage helpers
# ---------------------------------------------------------------------------


class TestAIMessage:
    def test_system_factory(self):
        msg = AIMessage.system("Be helpful.")
        assert msg.role == MessageRole.system
        assert msg.content == "Be helpful."

    def test_user_factory(self):
        msg = AIMessage.user("Hello!")
        assert msg.role == MessageRole.user
        assert msg.content == "Hello!"

    def test_assistant_factory(self):
        msg = AIMessage.assistant("Hi there.")
        assert msg.role == MessageRole.assistant

    def test_tool_result_factory(self):
        msg = AIMessage.tool_result(
            tool_call_id="call_1", name="search", content='{"results": []}'
        )
        assert msg.role == MessageRole.tool
        assert msg.tool_call_id == "call_1"
        assert msg.name == "search"

    def test_role_is_str_enum(self):
        msg = AIMessage(role="user", content="hi")
        assert msg.role == MessageRole.user


# ---------------------------------------------------------------------------
# ToolSchema serialization
# ---------------------------------------------------------------------------


class TestToolSchema:
    def test_basic_schema(self):
        schema = ToolSchema(
            name="get_weather",
            description="Get the current weather.",
            parameters=ToolParameterSchema(
                properties={"location": {"type": "string"}},
                required=["location"],
            ),
        )
        assert schema.name == "get_weather"
        assert schema.parameters.required == ["location"]

    def test_schema_roundtrip(self):
        schema = ToolSchema(
            name="add",
            description="Add two numbers.",
            parameters=ToolParameterSchema(
                properties={"a": {"type": "number"}, "b": {"type": "number"}},
                required=["a", "b"],
            ),
        )
        dumped = schema.model_dump()
        restored = ToolSchema.model_validate(dumped)
        assert restored.name == schema.name
        assert restored.parameters.required == ["a", "b"]


# ---------------------------------------------------------------------------
# ModelUsage arithmetic
# ---------------------------------------------------------------------------


class TestModelUsage:
    def test_add(self):
        a = ModelUsage(
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            estimated_cost_usd=0.01,
        )
        b = ModelUsage(
            prompt_tokens=5,
            completion_tokens=15,
            total_tokens=20,
            estimated_cost_usd=0.02,
        )
        c = a + b
        assert c.prompt_tokens == 15
        assert c.completion_tokens == 35
        assert c.total_tokens == 50
        assert c.estimated_cost_usd == pytest.approx(0.03)

    def test_default_zero(self):
        u = ModelUsage()
        assert u.total_tokens == 0
        assert u.estimated_cost_usd is None

    def test_with_estimated_cost(self):
        usage = ModelUsage(prompt_tokens=1_000, completion_tokens=500, total_tokens=1_500)
        costed = usage.with_estimated_cost(
            prompt_price_per_1m_tokens=2.0,
            completion_price_per_1m_tokens=8.0,
        )
        assert costed.estimated_cost_usd == 0.006
        assert costed.prompt_price_per_1m_tokens == 2.0
        assert costed.completion_price_per_1m_tokens == 8.0


# ---------------------------------------------------------------------------
# AgentActionRequest / AgentResumeInput
# ---------------------------------------------------------------------------


class TestAgentActionRequest:
    def test_fields(self):
        tc = ToolCall(id="c1", name="search", arguments={"q": "noodle"})
        req = AgentActionRequest(
            tool_calls=[tc],
            messages_so_far=[AIMessage.user("go")],
            step=1,
            max_steps=5,
        )
        assert req.step == 1
        assert req.tool_calls[0].name == "search"

    def test_resume_input(self):
        tr = ToolResult(tool_call_id="c1", name="search", content="results")
        resume = AgentResumeInput(
            tool_results=[tr],
            messages_so_far=[AIMessage.user("go")],
            step=1,
            max_steps=5,
        )
        assert resume.tool_results[0].is_error is False

    def test_action_response_converts_to_resume_input(self):
        tr = ToolResult(tool_call_id="c1", name="search", content="results")
        response = AgentActionResponse(
            tool_results=[tr],
            messages_so_far=[AIMessage.user("go")],
            step=1,
            max_steps=5,
        )
        resume = response.as_resume_input()
        assert resume.step == 1
        assert resume.tool_results[0].name == "search"


# ---------------------------------------------------------------------------
# Abstract adapter instantiation guards
# ---------------------------------------------------------------------------


class TestAbstractAdapters:
    def test_chat_model_adapter_is_abstract(self):
        with pytest.raises(TypeError):
            ChatModelAdapter()  # type: ignore[abstract]

    def test_embedding_model_adapter_is_abstract(self):
        with pytest.raises(TypeError):
            EmbeddingModelAdapter()  # type: ignore[abstract]

    def test_memory_adapter_is_abstract(self):
        with pytest.raises(TypeError):
            MemoryAdapter()  # type: ignore[abstract]

    def test_output_parser_adapter_is_abstract(self):
        with pytest.raises(TypeError):
            OutputParserAdapter()  # type: ignore[abstract]

    def test_tool_adapter_is_abstract(self):
        with pytest.raises(TypeError):
            ToolAdapter()  # type: ignore[abstract]

    def test_guardrail_adapter_is_abstract(self):
        with pytest.raises(TypeError):
            GuardrailAdapter()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# OpenAI adapter
# ---------------------------------------------------------------------------

_OPENAI_SUCCESS_BODY: dict[str, Any] = {
    "id": "chatcmpl-abc",
    "object": "chat.completion",
    "model": "gpt-4.1-mini",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Hello from OpenAI!",
                "tool_calls": None,
            },
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
}

_OPENAI_TOOL_BODY: dict[str, Any] = {
    "id": "chatcmpl-def",
    "object": "chat.completion",
    "model": "gpt-4.1-mini",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"location": "London"}',
                        },
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32},
}


def _mock_response(body: dict[str, Any], status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


class TestOpenAIChatAdapter:
    def test_basic_completion(self):
        from noodle_nodes.ai_v2.providers.openai import OpenAIChatAdapter

        adapter = OpenAIChatAdapter(
            api_key="sk-test",
            model="gpt-4.1-mini",
            prompt_price_per_1m_tokens=2.0,
            completion_price_per_1m_tokens=8.0,
        )
        req = ChatRequest(
            messages=[AIMessage.user("Hello!")],
            model="gpt-4.1-mini",
        )
        with patch("requests.post", return_value=_mock_response(_OPENAI_SUCCESS_BODY)):
            resp = adapter.complete(req)

        assert resp.text == "Hello from OpenAI!"
        assert resp.provider == "openai"
        assert resp.usage.total_tokens == 18
        assert resp.usage.estimated_cost_usd == 0.000084
        assert resp.tool_calls == []

    def test_tool_call_response(self):
        from noodle_nodes.ai_v2.providers.openai import OpenAIChatAdapter

        adapter = OpenAIChatAdapter(api_key="sk-test", model="gpt-4.1-mini")
        tool = ToolSchema(
            name="get_weather",
            description="Get weather.",
            parameters=ToolParameterSchema(
                properties={"location": {"type": "string"}},
                required=["location"],
            ),
        )
        req = ChatRequest(
            messages=[AIMessage.user("What's the weather in London?")],
            model="gpt-4.1-mini",
            tools=[tool],
        )
        with patch("requests.post", return_value=_mock_response(_OPENAI_TOOL_BODY)):
            resp = adapter.complete(req)

        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "get_weather"
        assert resp.tool_calls[0].arguments == {"location": "London"}
        assert resp.tool_calls[0].id == "call_1"

    def test_http_error_raises(self):
        from noodle_nodes.ai_v2.providers.openai import OpenAIChatAdapter

        adapter = OpenAIChatAdapter(api_key="sk-test", model="gpt-4.1-mini")
        req = ChatRequest(messages=[AIMessage.user("Hi")], model="gpt-4.1-mini")
        with patch(
            "requests.post",
            return_value=_mock_response({"error": {"message": "Unauthorized"}}, 401),
        ):
            with pytest.raises(RuntimeError, match="401"):
                adapter.complete(req)

    def test_missing_api_key_raises(self):
        from noodle_nodes.ai_v2.providers.openai import OpenAIChatAdapter

        adapter = OpenAIChatAdapter(api_key="", model="gpt-4.1-mini", provider="openai")
        req = ChatRequest(messages=[AIMessage.user("Hi")], model="gpt-4.1-mini")
        with pytest.raises(ValueError, match="api_key"):
            adapter.complete(req)

    def test_capabilities_for_gpt4(self):
        from noodle_nodes.ai_v2.providers.openai import OpenAIChatAdapter

        adapter = OpenAIChatAdapter(api_key="k", model="gpt-4o")
        caps = adapter.capabilities
        assert caps.supports_tools is True
        assert caps.supports_json_mode is True

    def test_config_roundtrip(self):
        from noodle_nodes.ai_v2.providers.openai import OpenAIChatAdapter

        adapter = OpenAIChatAdapter(
            api_key="sk-x",
            model="gpt-4o",
            provider="openai",
            organization="org-1",
        )
        cfg = adapter.as_config()
        restored = OpenAIChatAdapter.from_config(cfg)
        assert restored._model == "gpt-4o"
        assert restored._organization == "org-1"

    def test_ollama_uses_local_base(self):
        from noodle_nodes.ai_v2.providers.openai import OpenAIChatAdapter

        adapter = OpenAIChatAdapter(model="llama3.2", provider="ollama")
        req = ChatRequest(messages=[AIMessage.user("Hi")], model="llama3.2")
        with patch("requests.post", return_value=_mock_response(_OPENAI_SUCCESS_BODY)) as mock_post:
            adapter.complete(req)

        url = mock_post.call_args[0][0]
        assert "localhost:11434" in url

    def test_openrouter_requires_api_key(self):
        from noodle_nodes.ai_v2.providers.openai import OpenAIChatAdapter

        adapter = OpenAIChatAdapter(api_key="", provider="openrouter")
        req = ChatRequest(messages=[AIMessage.user("Hi")], model="openai/gpt-4o")
        with pytest.raises(ValueError, match="api_key"):
            adapter.complete(req)


# ---------------------------------------------------------------------------
# Azure OpenAI adapter
# ---------------------------------------------------------------------------


class TestAzureOpenAIChatAdapter:
    def test_basic_completion(self):
        from noodle_nodes.ai_v2.providers.openai import AzureOpenAIChatAdapter

        adapter = AzureOpenAIChatAdapter(
            api_key="azure-key",
            azure_endpoint="https://myinstance.openai.azure.com",
            deployment="gpt-4o",
        )
        req = ChatRequest(messages=[AIMessage.user("Hi Azure")], model="gpt-4o")
        with patch(
            "requests.post", return_value=_mock_response(_OPENAI_SUCCESS_BODY)
        ) as mock_post:
            resp = adapter.complete(req)

        assert resp.provider == "azure_openai"
        url = mock_post.call_args[0][0]
        assert "deployments/gpt-4o" in url
        assert "api-version" in url

    def test_missing_endpoint_raises(self):
        with pytest.raises(ValueError, match="azure_endpoint"):
            from noodle_nodes.ai_v2.providers.openai import AzureOpenAIChatAdapter

            AzureOpenAIChatAdapter(api_key="key", azure_endpoint="", deployment="gpt-4o")

    def test_config_roundtrip(self):
        from noodle_nodes.ai_v2.providers.openai import AzureOpenAIChatAdapter

        adapter = AzureOpenAIChatAdapter(
            api_key="az-key",
            azure_endpoint="https://x.openai.azure.com",
            deployment="gpt-4o",
            azure_api_version="2024-05-01-preview",
        )
        cfg = adapter.as_config()
        restored = AzureOpenAIChatAdapter.from_config(cfg)
        assert restored._deployment == "gpt-4o"
        assert restored._api_version == "2024-05-01-preview"


# ---------------------------------------------------------------------------
# Anthropic adapter
# ---------------------------------------------------------------------------

_ANTHROPIC_SUCCESS_BODY: dict[str, Any] = {
    "id": "msg_abc",
    "type": "message",
    "role": "assistant",
    "model": "claude-3-5-haiku-latest",
    "content": [{"type": "text", "text": "Hello from Anthropic!"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 12, "output_tokens": 6},
}

_ANTHROPIC_TOOL_BODY: dict[str, Any] = {
    "id": "msg_def",
    "type": "message",
    "role": "assistant",
    "model": "claude-3-5-haiku-latest",
    "content": [
        {
            "type": "tool_use",
            "id": "toolu_01",
            "name": "get_weather",
            "input": {"location": "Paris"},
        }
    ],
    "stop_reason": "tool_use",
    "usage": {"input_tokens": 25, "output_tokens": 14},
}


class TestAnthropicChatAdapter:
    def test_basic_completion(self):
        from noodle_nodes.ai_v2.providers.anthropic import AnthropicChatAdapter

        adapter = AnthropicChatAdapter(
            api_key="ant-key",
            model="claude-3-5-haiku-latest",
            prompt_price_per_1m_tokens=3.0,
            completion_price_per_1m_tokens=15.0,
        )
        req = ChatRequest(
            messages=[AIMessage.user("Hi Claude!")],
            model="claude-3-5-haiku-latest",
        )
        with patch(
            "requests.post", return_value=_mock_response(_ANTHROPIC_SUCCESS_BODY)
        ):
            resp = adapter.complete(req)

        assert resp.text == "Hello from Anthropic!"
        assert resp.provider == "anthropic"
        assert resp.usage.prompt_tokens == 12
        assert resp.usage.completion_tokens == 6
        assert resp.usage.estimated_cost_usd == 0.000126

    def test_system_message_extracted(self):
        from noodle_nodes.ai_v2.providers.anthropic import AnthropicChatAdapter

        adapter = AnthropicChatAdapter(api_key="k")
        req = ChatRequest(
            messages=[
                AIMessage.system("You are a helpful assistant."),
                AIMessage.user("Hi!"),
            ],
            model="claude-3-5-haiku-latest",
        )
        with patch(
            "requests.post", return_value=_mock_response(_ANTHROPIC_SUCCESS_BODY)
        ) as mock_post:
            adapter.complete(req)

        payload = mock_post.call_args[1]["json"]
        assert payload.get("system") == "You are a helpful assistant."
        # Only non-system messages in messages list
        assert all(m["role"] != "system" for m in payload["messages"])

    def test_tool_call_response(self):
        from noodle_nodes.ai_v2.providers.anthropic import AnthropicChatAdapter

        adapter = AnthropicChatAdapter(api_key="k")
        req = ChatRequest(
            messages=[AIMessage.user("Weather in Paris?")],
            model="claude-3-5-haiku-latest",
            tools=[
                ToolSchema(
                    name="get_weather",
                    description="Get weather.",
                    parameters=ToolParameterSchema(
                        properties={"location": {"type": "string"}},
                        required=["location"],
                    ),
                )
            ],
        )
        with patch(
            "requests.post", return_value=_mock_response(_ANTHROPIC_TOOL_BODY)
        ):
            resp = adapter.complete(req)

        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "get_weather"
        assert resp.tool_calls[0].arguments == {"location": "Paris"}

    def test_tool_result_message_format(self):
        """Tool results must be sent as Anthropic's tool_result content blocks."""
        from noodle_nodes.ai_v2.providers.anthropic import AnthropicChatAdapter

        adapter = AnthropicChatAdapter(api_key="k")
        req = ChatRequest(
            messages=[
                AIMessage.user("Weather in Paris?"),
                AIMessage.assistant(""),  # assistant turn with tool_use
                AIMessage.tool_result(
                    tool_call_id="toolu_01",
                    name="get_weather",
                    content='{"temp": 18}',
                ),
            ],
            model="claude-3-5-haiku-latest",
        )
        with patch(
            "requests.post", return_value=_mock_response(_ANTHROPIC_SUCCESS_BODY)
        ) as mock_post:
            adapter.complete(req)

        msgs = mock_post.call_args[1]["json"]["messages"]
        # Last message should be user role with tool_result content
        last = msgs[-1]
        assert last["role"] == "user"
        assert last["content"][0]["type"] == "tool_result"
        assert last["content"][0]["tool_use_id"] == "toolu_01"

    def test_missing_api_key_raises(self):
        from noodle_nodes.ai_v2.providers.anthropic import AnthropicChatAdapter

        with pytest.raises(ValueError, match="api_key"):
            AnthropicChatAdapter(api_key="")

    def test_capabilities(self):
        from noodle_nodes.ai_v2.providers.anthropic import AnthropicChatAdapter

        adapter = AnthropicChatAdapter(api_key="k", model="claude-3-5-haiku-latest")
        caps = adapter.capabilities
        assert caps.supports_tools is True

    def test_config_roundtrip(self):
        from noodle_nodes.ai_v2.providers.anthropic import AnthropicChatAdapter

        adapter = AnthropicChatAdapter(
            api_key="ant-k",
            model="claude-3-7-sonnet-latest",
            anthropic_version="2023-06-01",
        )
        cfg = adapter.as_config()
        restored = AnthropicChatAdapter.from_config(cfg)
        assert restored._model == "claude-3-7-sonnet-latest"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestAdapterFactory:
    def test_openai_provider(self):
        from noodle_nodes.ai_v2.factory import adapter_from_credentials
        from noodle_nodes.ai_v2.providers.openai import OpenAIChatAdapter

        adapter = adapter_from_credentials(
            {"api_key": "sk-x", "provider": "openai"},
            model="gpt-4.1-mini",
        )
        assert isinstance(adapter, OpenAIChatAdapter)

    def test_anthropic_provider(self):
        from noodle_nodes.ai_v2.factory import adapter_from_credentials
        from noodle_nodes.ai_v2.providers.anthropic import AnthropicChatAdapter

        adapter = adapter_from_credentials(
            {"api_key": "ant-x", "provider": "anthropic"},
            model="claude-3-5-haiku-latest",
        )
        assert isinstance(adapter, AnthropicChatAdapter)

    def test_azure_openai_provider(self):
        from noodle_nodes.ai_v2.factory import adapter_from_credentials
        from noodle_nodes.ai_v2.providers.openai import AzureOpenAIChatAdapter

        adapter = adapter_from_credentials(
            {
                "api_key": "az-k",
                "azure_endpoint": "https://x.openai.azure.com",
                "deployment": "gpt-4o",
                "provider": "azure_openai",
            }
        )
        assert isinstance(adapter, AzureOpenAIChatAdapter)

    def test_alias_azure(self):
        from noodle_nodes.ai_v2.factory import adapter_from_credentials
        from noodle_nodes.ai_v2.providers.openai import AzureOpenAIChatAdapter

        adapter = adapter_from_credentials(
            {
                "api_key": "az-k",
                "azure_endpoint": "https://x.openai.azure.com",
                "deployment": "gpt-4o",
            },
            provider="azure",  # alias
        )
        assert isinstance(adapter, AzureOpenAIChatAdapter)

    def test_none_credentials_defaults_to_openai(self):
        from noodle_nodes.ai_v2.factory import adapter_from_credentials
        from noodle_nodes.ai_v2.providers.openai import OpenAIChatAdapter

        adapter = adapter_from_credentials(None, provider="openai", model="gpt-4o")
        assert isinstance(adapter, OpenAIChatAdapter)

    def test_ollama_provider(self):
        from noodle_nodes.ai_v2.factory import adapter_from_credentials
        from noodle_nodes.ai_v2.providers.openai import OpenAIChatAdapter

        adapter = adapter_from_credentials({}, provider="ollama", model="llama3.2")
        assert isinstance(adapter, OpenAIChatAdapter)
        assert adapter._provider == "ollama"


# ---------------------------------------------------------------------------
# Embedding adapters
# ---------------------------------------------------------------------------


class TestEmbeddingAdapters:
    def test_openai_embedding_usage_cost(self):
        from noodle_nodes.ai_v2.providers.embeddings import OpenAIEmbeddingAdapter

        adapter = OpenAIEmbeddingAdapter(
            api_key="sk-test",
            model="text-embedding-3-small",
            prompt_price_per_1m_tokens=0.02,
        )
        body = {
            "model": "text-embedding-3-small",
            "data": [{"index": 0, "embedding": [0.1, 0.2]}],
            "usage": {"prompt_tokens": 1_000, "total_tokens": 1_000},
        }
        with patch("requests.post", return_value=_mock_response(body)):
            resp = adapter.embed(
                EmbeddingRequest(
                    texts=["hello"],
                    model="text-embedding-3-small",
                )
            )

        assert resp.embeddings == [[0.1, 0.2]]
        assert resp.usage.prompt_tokens == 1_000
        assert resp.usage.estimated_cost_usd == 0.00002
