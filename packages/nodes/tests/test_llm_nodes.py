from __future__ import annotations

import asyncio
import json

import requests

import noodle_nodes  # noqa: F401 - registers nodes
import noodle_nodes.llm as llm_module
from noodle.artifacts import LocalArtifactStore
from noodle.context import artifact_store, current_node_id
from noodle.datasets import is_dataset_ref
from noodle.sdk import registry
from noodle_nodes.datasets import dataset_to_records, records_to_dataset
from noodle_nodes.llm import (
    ai_agent,
    ai_batch_embeddings,
    ai_chat,
    ai_chat_model,
    ai_memory_buffer,
    ai_prompt_template,
    ai_structured_output,
    ai_text_chunk,
    ai_tool,
    ai_tool_box,
)


class FakeResponse:
    def __init__(self, payload: object, status_code: int = 200, content: bytes | None = None):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.content = content if content is not None else self.text.encode("utf-8")
        self.headers = {"content-type": "application/json"}

    def json(self) -> object:
        return self._payload


class StreamingFakeResponse:
    def __init__(self, lines: list[str], status_code: int = 200):
        self._lines = lines
        self.status_code = status_code
        self.text = "\n".join(lines)
        self.headers = {"content-type": "text/event-stream"}

    def iter_lines(self, decode_unicode: bool = False):
        for line in self._lines:
            yield line if decode_unicode else line.encode("utf-8")

    def json(self) -> object:
        return {}


def test_assemble_openai_stream_emits_and_accumulates() -> None:
    from noodle_nodes.llm import _assemble_openai_stream

    emitted: list[str] = []
    lines = [
        'data: {"model":"gpt-x","choices":[{"delta":{"content":"Hel"}}]}',
        'data: {"choices":[{"delta":{"content":"lo"}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"completion_tokens":2}}',
        "data: [DONE]",
    ]
    out = _assemble_openai_stream(lines, emitted.append)
    assert emitted == ["Hel", "lo"]
    assert out["text"] == "Hello"
    assert out["finish_reason"] == "stop"
    assert out["usage"] == {"completion_tokens": 2}
    assert out["model"] == "gpt-x"
    assert out["message"] == {"role": "assistant", "content": "Hello"}


def test_assemble_openai_stream_accumulates_tool_call_deltas() -> None:
    from noodle_nodes.llm import _assemble_openai_stream

    lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
        '"function":{"name":"get","arguments":"{\\"a\\":"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        '"function":{"arguments":"1}"}}]}}]}',
        "data: [DONE]",
    ]
    out = _assemble_openai_stream(lines, lambda _x: None)
    assert out["tool_calls"] == [
        {"id": "call_1", "type": "function",
         "function": {"name": "get", "arguments": '{"a":1}'}}
    ]


def test_assemble_anthropic_stream_emits_text() -> None:
    from noodle_nodes.llm import _assemble_anthropic_stream

    emitted: list[str] = []
    lines = [
        'data: {"type":"message_start","message":{"model":"claude-x",'
        '"usage":{"input_tokens":3}}}',
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}',
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" there"}}',
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
        '"usage":{"output_tokens":2}}',
    ]
    out = _assemble_anthropic_stream(lines, emitted.append)
    assert emitted == ["Hi", " there"]
    assert out["text"] == "Hi there"
    assert out["finish_reason"] == "end_turn"
    assert out["model"] == "claude-x"


def test_ai_chat_streams_when_emitter_active(monkeypatch) -> None:
    from noodle.context import node_emitter

    chunks: list[str] = []

    def emitter(delta: str, *, channel: str = "output") -> None:
        chunks.append(delta)

    def fake_post(url: str, **kwargs):
        assert kwargs["json"].get("stream") is True
        assert kwargs.get("stream") is True
        return StreamingFakeResponse(
            [
                'data: {"model":"gpt-x","choices":[{"delta":{"content":"Hello"}}]}',
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
                "data: [DONE]",
            ]
        )

    monkeypatch.setattr(requests, "post", fake_post)

    token = node_emitter.set(emitter)
    try:
        out = ai_chat(
            {"x": 1},
            credentials={"provider": "openai", "api_key": "sk-test"},
            provider="openai",
            model="gpt-x",
        )
    finally:
        node_emitter.reset(token)

    assert chunks == ["Hello"]
    assert out["text"] == "Hello"
    assert out["provider"] == "openai"


def test_ai_chat_does_not_stream_without_emitter(monkeypatch) -> None:
    # No emitter installed → classic non-streaming request (no stream flag).
    def fake_post(url: str, **kwargs):
        assert "stream" not in kwargs["json"]
        return FakeResponse(
            {"model": "gpt-x", "choices": [
                {"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}]}
        )

    monkeypatch.setattr(requests, "post", fake_post)
    out = ai_chat(
        {"x": 1},
        credentials={"provider": "openai", "api_key": "sk-test"},
        provider="openai",
        model="gpt-x",
    )
    assert out["text"] == "hi"


def test_ai_nodes_register_in_ai_category() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    expected = {
        "ai_prompt_template",
        "ai_chat_model",
        "ai_chat",
        "ai_structured_output",
        "ai_text_chunk",
        "ai_batch_embeddings",
        "ai_dataset_map",
        "ai_vector_retriever",
        "ai_rag_answer",
        "ai_memory_buffer",
        "ai_tool",
        "ai_tool_box",
        "ai_agent",
        "ai_moderation_guard",
        "ai_vision_analyze",
        "ai_image_generate",
        "openai_chat",
        "anthropic_message",
        "openai_embeddings",
        "openai_tts",
    }
    assert expected <= manifests.keys()
    assert all(manifests[node_id].category == "AI" for node_id in expected)


def test_agent_manifest_has_model_memory_and_tools_ports() -> None:
    manifest = registry.get("ai_agent").manifest
    assert [port.name for port in manifest.inputs] == ["input", "model", "memory", "tools"]
    param_names = {param.name for param in manifest.params}
    assert "fallback_model" in param_names
    assert "model" not in param_names


def test_legacy_agent_stack_is_hidden_with_v2_replacements() -> None:
    replacements = {
        "ai_chat_model": "ai_chat_model_openai",
        "ai_memory_buffer": "ai_buffer_memory",
        "ai_tool": "ai_http_tool",
        "ai_tool_box": "ai_tool_bundle",
        "ai_agent": "ai_agent_v2",
    }
    for node_id, replacement_id in replacements.items():
        manifest = registry.get(node_id).manifest
        assert manifest.hidden is True
        assert manifest.deprecated is True
        assert manifest.replacement_id == replacement_id


def test_chat_model_manifest_uses_provider_and_model_dropdowns() -> None:
    manifest = registry.get("ai_chat_model").manifest
    params = {param.name: param for param in manifest.params}
    assert "openrouter" in params["provider"].choices
    assert "openai/gpt-4.1-mini" in params["model"].choices


def test_prompt_template_renders_jinja_context() -> None:
    out = ai_prompt_template(
        {"name": "Ada", "score": 9},
        system_template="Score bot",
        prompt_template="User {{ row.name }} scored {{ row.score }}",
    )
    assert out["system"] == "Score bot"
    assert out["prompt"] == "User Ada scored 9"
    assert out["messages"] == [
        {"role": "system", "content": "Score bot"},
        {"role": "user", "content": "User Ada scored 9"},
    ]


def test_ai_chat_normalizes_openai_response(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return FakeResponse(
            {
                "model": "gpt-test",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "hello"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1},
            }
        )

    monkeypatch.setattr(requests, "post", fake_post)

    out = ai_chat(
        {"topic": "ops"},
        credentials={"provider": "openai", "api_key": "sk-test"},
        provider="openai",
        model="gpt-test",
        system="Be brief.",
    )

    assert out["text"] == "hello"
    assert out["provider"] == "openai"
    assert out["usage"] == {"prompt_tokens": 5, "completion_tokens": 1}
    assert calls[0]["url"] == "https://api.openai.com/v1/chat/completions"
    assert calls[0]["kwargs"]["headers"]["Authorization"] == "Bearer sk-test"
    assert calls[0]["kwargs"]["json"]["messages"][0] == {
        "role": "system",
        "content": "Be brief.",
    }


def test_ai_chat_openrouter_uses_first_class_provider(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return FakeResponse(
            {
                "model": "openai/gpt-4.1-mini",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "hello"},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    monkeypatch.setattr(requests, "post", fake_post)

    out = ai_chat(
        "hello",
        credentials={
            "provider": "openrouter",
            "api_key": "sk-or-test",
            "site_url": "https://noodle.dev",
            "app_name": "Noodle",
        },
        provider="openrouter",
        model="openai/gpt-4.1-mini",
    )

    assert out["provider"] == "openrouter"
    assert calls[0]["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert calls[0]["kwargs"]["headers"]["Authorization"] == "Bearer sk-or-test"
    assert calls[0]["kwargs"]["headers"]["HTTP-Referer"] == "https://noodle.dev"
    assert calls[0]["kwargs"]["headers"]["X-Title"] == "Noodle"


def test_structured_output_validates_schema(monkeypatch) -> None:
    def fake_post(url: str, **kwargs):  # noqa: ARG001
        return FakeResponse(
            {
                "model": "gpt-test",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"name":"Ada","score":9}',
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    monkeypatch.setattr(requests, "post", fake_post)

    out = ai_structured_output(
        {"name": "Ada"},
        credentials={"provider": "openai", "api_key": "sk-test"},
        provider="openai",
        model="gpt-test",
        schema_json='{"type":"object","required":["name","score"]}',
    )

    assert out["valid"] is True
    assert out["json"] == {"name": "Ada", "score": 9}


def test_text_chunker_uses_overlap() -> None:
    chunks = ai_text_chunk(text="x" * 250, chunk_size=100, overlap=10)
    assert [chunk["start"] for chunk in chunks] == [0, 90, 180]
    assert [chunk["char_count"] for chunk in chunks] == [100, 100, 70]


def test_batch_embeddings_returns_dataset_for_dataset_input(monkeypatch, tmp_path) -> None:
    def fake_post(url: str, **kwargs):  # noqa: ARG001
        texts = kwargs["json"]["input"]
        return FakeResponse(
            {
                "data": [
                    {"index": idx, "embedding": [float(idx), 1.0]}
                    for idx, _text in enumerate(texts)
                ],
                "usage": {"total_tokens": len(texts)},
            }
        )

    monkeypatch.setattr(requests, "post", fake_post)
    store = LocalArtifactStore(tmp_path, run_id="llm-test")
    a = artifact_store.set(store)
    n = current_node_id.set("embed")
    try:
        ds = records_to_dataset([{"text": "one"}, {"text": "two"}])
        out = ai_batch_embeddings(
            ds,
            credentials={"provider": "openai", "api_key": "sk-test"},
            provider="openai",
            model="text-embedding-test",
        )
        assert is_dataset_ref(out)
        rows = dataset_to_records(out, max_rows=10)
        assert rows[0]["embedding"] == [0.0, 1.0]
        assert rows[1]["embedding"] == [1.0, 1.0]
    finally:
        current_node_id.reset(n)
        artifact_store.reset(a)


def test_chat_model_and_tool_box_supply_agent_inputs(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return FakeResponse(
            {
                "model": "gpt-model-node",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"action":"final","answer":"done"}',
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    monkeypatch.setattr(requests, "post", fake_post)
    model_config = ai_chat_model(
        credentials={"provider": "openai", "api_key": "sk-test"},
        provider="openai",
        model="gpt-model-node",
        temperature=0.05,
    )
    memory = ai_memory_buffer(
        messages_json='[{"role":"user","content":"Earlier context"}]',
        max_messages=10,
    )
    tool = ai_tool(
        name="lookup_customer",
        description="Find a customer by id.",
        parameters_schema_json='{"type":"object","properties":{"id":{"type":"string"}}}',
        url="https://example.test/customer",
    )
    toolbox = ai_tool_box(tool_1=tool)

    out = asyncio.run(
        ai_agent(
            input={"task": "Answer using the available context."},
            model=model_config,
            memory=memory,
            tools=toolbox,
            fallback_model="ignored-model",
        )
    )

    assert out["answer"] == "done"
    assert out["provider"] == "openai"
    payload = calls[0]["kwargs"]["json"]
    assert payload["model"] == "gpt-model-node"
    assert payload["temperature"] == 0.05
    assert any(msg["content"] == "Earlier context" for msg in payload["messages"])
    assert "lookup_customer" in payload["messages"][-1]["content"]


def test_tool_box_merges_unique_tools() -> None:
    first = ai_tool(name="search", description="Search records.", url="https://example.test/search")
    duplicate = ai_tool(name="search", description="Duplicate.", url="https://example.test/dup")
    second = ai_tool(
        name="summarize", description="Summarize data.", url="https://example.test/sum"
    )

    out = ai_tool_box(tool_1=first, tool_2=duplicate, tool_3=second)

    assert out["count"] == 2
    assert [tool["name"] for tool in out["tools"]] == ["search", "summarize"]


def test_legacy_ai_tool_blocks_private_targets(monkeypatch) -> None:
    def fake_request(*args, **kwargs):
        raise AssertionError("private target should be blocked before requests")

    monkeypatch.setattr(requests, "request", fake_request)
    try:
        asyncio.run(
            llm_module._execute_tool(
                {
                    "name": "metadata",
                    "type": "http",
                    "url": "http://127.0.0.1:8080/latest",
                    "method": "GET",
                },
                {},
            )
        )
    except ValueError as exc:
        assert "private" in str(exc)
    else:
        raise AssertionError("private legacy AI HTTP tool target should be blocked")


def test_ai_vector_retriever_blocks_private_pinecone_host(monkeypatch) -> None:
    def fake_embeddings(**kwargs):
        return [[0.1, 0.2]], {}

    def fake_post(*args, **kwargs):
        raise AssertionError("private target should be blocked before requests")

    monkeypatch.setattr(llm_module, "_call_embeddings", fake_embeddings)
    monkeypatch.setattr(requests, "post", fake_post)
    try:
        llm_module.ai_vector_retriever(
            embedding_credentials={"provider": "openai", "api_key": "sk-test"},
            pinecone_credentials={
                "api_key": "pc-test",
                "index_host": "127.0.0.1:8080",
            },
            query="hello",
        )
    except ValueError as exc:
        assert "private" in str(exc)
    else:
        raise AssertionError("private Pinecone host should be blocked")


def test_ai_chat_model_param_uses_dynamic_loader() -> None:
    manifest = registry.get("ai_chat").manifest
    model = next(p for p in manifest.params if p.name == "model")
    assert model.load_options == "llm_models"
    assert "credentials" in model.depends_on
    assert model.choices  # curated fallback preserved
