from __future__ import annotations

import json

import requests

import noodle_nodes  # noqa: F401 - registers nodes
from noodle.artifacts import LocalArtifactStore
from noodle.context import artifact_store, current_node_id
from noodle.datasets import is_dataset_ref
from noodle.sdk import registry
from noodle_nodes.datasets import dataset_to_records, records_to_dataset
from noodle_nodes.llm import (
    ai_batch_embeddings,
    ai_chat,
    ai_prompt_template,
    ai_structured_output,
    ai_text_chunk,
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


def test_ai_nodes_register_in_ai_category() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    expected = {
        "ai_prompt_template",
        "ai_chat",
        "ai_structured_output",
        "ai_text_chunk",
        "ai_batch_embeddings",
        "ai_dataset_map",
        "ai_vector_retriever",
        "ai_rag_answer",
        "ai_tool",
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
