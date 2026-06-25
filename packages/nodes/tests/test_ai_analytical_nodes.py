"""Tests for Tasks 22-29: AI analytical nodes."""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from noodle.ai_runtime import (
    ChatModelAdapter,
    ChatRequest,
    ChatResponse,
    EmbeddingModelAdapter,
    EmbeddingRequest,
    EmbeddingResponse,
    ModelUsage,
)
from noodle_nodes.ai_analytical_nodes import (
    _chunk_text,
    _cosine_similarity,
    _parse_json_response,
    ai_batch_processor,
    ai_code_review,
    ai_image_classifier,
    ai_named_entity_recognition,
    ai_semantic_search,
    ai_sentiment_analysis,
    ai_summarizer,
    huggingface_inference,
)

# ---------------------------------------------------------------------------
# Mock adapters
# ---------------------------------------------------------------------------


class _MockChatAdapter(ChatModelAdapter):
    def __init__(self, response_text: str = "{}"):
        self._response_text = response_text
        self._calls: list[ChatRequest] = []

    def complete(self, request: ChatRequest) -> ChatResponse:
        self._calls.append(request)
        return ChatResponse(text=self._response_text, model="mock", usage=ModelUsage())

    def as_config(self):
        return {"model": "mock-model"}


class _MockEmbeddingAdapter(EmbeddingModelAdapter):
    def __init__(self, vectors: list[list[float]] | None = None):
        self._vectors = vectors

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        if self._vectors is not None:
            return EmbeddingResponse(embeddings=self._vectors, model="mock-emb")
        # Default: return unique deterministic vectors per text
        vecs = [[float(i), float(ord(t[0]) if t else 0)] for i, t in enumerate(request.texts)]
        return EmbeddingResponse(embeddings=vecs, model="mock-emb")


# ---------------------------------------------------------------------------
# Shared helper tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_chunk_text_splits_on_words(self):
        text = "Hello world. This is a test. Testing chunking."
        chunks = _chunk_text(text, 20)
        assert all(len(c) <= 20 for c in chunks)
        assert len(chunks) > 1

    def test_cosine_similarity_identical(self):
        v = [1.0, 0.0, 0.0]
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6

    def test_cosine_similarity_orthogonal(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine_similarity(a, b)) < 1e-6

    def test_parse_json_response_plain(self):
        result = _parse_json_response('{"key": "val"}')
        assert result == {"key": "val"}

    def test_parse_json_response_markdown_fence(self):
        text = '```json\n{"key": "val"}\n```'
        result = _parse_json_response(text)
        assert result == {"key": "val"}

    def test_parse_json_response_embedded(self):
        text = 'Here is the result: {"score": 0.9}'
        result = _parse_json_response(text)
        assert result == {"score": 0.9}


# ---------------------------------------------------------------------------
# Task 22 — AI Named Entity Recognition
# ---------------------------------------------------------------------------


class TestAINER:
    def test_llm_returns_entities(self):
        response_json = json.dumps(
            [
                {"text": "Alice", "type": "PERSON", "start": 0, "end": 5},
                {"text": "Acme", "type": "ORG", "start": 10, "end": 14},
            ]
        )
        adapter = _MockChatAdapter(response_json)
        result = ai_named_entity_recognition(
            text="Alice works at Acme Corp in New York.",
            model=adapter,
            backend="llm",
        )
        assert len(result["entities"]) == 2
        assert result["entities"][0]["type"] == "PERSON"

    def test_llm_backend_without_model_raises(self):
        with pytest.raises(ValueError, match="AI Chat Model"):
            ai_named_entity_recognition(text="Some text", model=None, backend="llm")

    def test_empty_text_raises(self):
        adapter = _MockChatAdapter("[]")
        with pytest.raises(ValueError, match="text input"):
            ai_named_entity_recognition(text="", model=adapter)

    def test_spacy_backend_import_error(self):
        with patch.dict(sys.modules, {"spacy": None}):
            with pytest.raises((ImportError, Exception)):
                ai_named_entity_recognition(text="Test", model=None, backend="spacy")

    def test_result_has_text_field(self):
        adapter = _MockChatAdapter("[]")
        result = ai_named_entity_recognition(text="No entities here.", model=adapter)
        assert result["text"] == "No entities here."
        assert result["backend"] == "llm"


# ---------------------------------------------------------------------------
# Task 23 — AI Summarizer
# ---------------------------------------------------------------------------


class TestAISummarizer:
    def test_abstractive_returns_summary(self):
        adapter = _MockChatAdapter("This is a summary.")
        result = ai_summarizer(text="Long text here. " * 20, model=adapter, mode="abstractive")
        assert result["summary"] == "This is a summary."
        assert result["mode"] == "abstractive"

    def test_extractive_no_model_needed(self):
        text = "The quick brown fox. Jumps over the lazy dog. It was a nice day."
        result = ai_summarizer(text=text, model=None, mode="extractive", summary_length="short")
        assert result["summary"]
        assert result["mode"] == "extractive"

    def test_empty_text_raises(self):
        adapter = _MockChatAdapter("sum")
        with pytest.raises(ValueError, match="text input"):
            ai_summarizer(text="", model=adapter)

    def test_abstractive_without_model_raises(self):
        with pytest.raises(ValueError, match="AI Chat Model"):
            ai_summarizer(text="Some text to summarize.", model=None, mode="abstractive")

    def test_wired_input_fallback(self):
        adapter = _MockChatAdapter("Short summary.")
        result = ai_summarizer(input="Some input text.", model=adapter)
        assert result["summary"] == "Short summary."


# ---------------------------------------------------------------------------
# Task 24 — AI Sentiment Analysis
# ---------------------------------------------------------------------------


class TestAISentiment:
    def test_returns_sentiment(self):
        adapter = _MockChatAdapter('{"sentiment": "positive", "score": 0.9}')
        result = ai_sentiment_analysis(
            text="This is amazing!",
            model=adapter,
            output_format="score",
        )
        assert result["sentiment"] == "positive"
        assert result["score"] == 0.9

    def test_empty_text_raises(self):
        adapter = _MockChatAdapter("{}")
        with pytest.raises(ValueError, match="text input"):
            ai_sentiment_analysis(text="", model=adapter)

    def test_no_model_raises(self):
        with pytest.raises(ValueError, match="AI Chat Model"):
            ai_sentiment_analysis(text="hello", model=None)

    def test_result_includes_input_text(self):
        adapter = _MockChatAdapter('{"sentiment": "neutral"}')
        result = ai_sentiment_analysis(text="okay.", model=adapter)
        assert result["text"] == "okay."

    def test_raw_fallback_on_non_json(self):
        adapter = _MockChatAdapter("positive")
        result = ai_sentiment_analysis(text="great", model=adapter)
        assert "sentiment" in result


# ---------------------------------------------------------------------------
# Task 25 — AI Semantic Search
# ---------------------------------------------------------------------------


class TestAISemanticSearch:
    def _make_adapter(self) -> _MockEmbeddingAdapter:
        # query=[1,0], docs=[0,1],[1,0],[0.5,0.5]
        vecs = [
            [1.0, 0.0],  # query
            [0.0, 1.0],  # doc0 — orthogonal to query
            [1.0, 0.0],  # doc1 — identical to query
            [0.707, 0.707],  # doc2 — 45 degrees
        ]
        return _MockEmbeddingAdapter(vecs)

    def test_returns_top_k_results(self):
        adapter = self._make_adapter()
        result = ai_semantic_search(
            query="test",
            documents=["doc0", "doc1", "doc2"],
            embedding_model=adapter,
            top_k=2,
        )
        assert len(result["results"]) == 2
        # doc1 should rank highest (score=1.0)
        assert result["results"][0]["score"] == pytest.approx(1.0, abs=1e-3)

    def test_min_score_filters(self):
        adapter = self._make_adapter()
        result = ai_semantic_search(
            query="test",
            documents=["doc0", "doc1", "doc2"],
            embedding_model=adapter,
            top_k=10,
            min_score=0.9,
        )
        # Only doc1 (score=1.0) passes
        assert all(r["score"] >= 0.9 for r in result["results"])

    def test_no_embedding_model_raises(self):
        with pytest.raises(ValueError, match="Embedding Model"):
            ai_semantic_search(query="test", documents=["a"], embedding_model=None)

    def test_empty_query_raises(self):
        adapter = _MockEmbeddingAdapter([[1.0]])
        with pytest.raises(ValueError, match="query"):
            ai_semantic_search(query="", documents=["a"], embedding_model=adapter)

    def test_dict_documents(self):
        vecs = [[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]]
        adapter = _MockEmbeddingAdapter(vecs)
        docs = [{"id": "a", "text": "first"}, {"id": "b", "text": "second"}]
        result = ai_semantic_search(query="x", documents=docs, embedding_model=adapter, top_k=2)
        assert result["results"][0]["id"] in ("a", "b")


# ---------------------------------------------------------------------------
# Task 26 — HuggingFace Inference
# ---------------------------------------------------------------------------


class TestHuggingFaceInference:
    def test_raises_without_huggingface_hub(self):
        with patch.dict(sys.modules, {"huggingface_hub": None}):
            with pytest.raises((ImportError, Exception)):
                huggingface_inference(inputs="test", model="bert-base", credentials="tok")

    def test_endpoint_url_uses_requests(self):
        mock_hf = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"generated_text": "hello"}]

        with patch.dict(sys.modules, {"huggingface_hub": mock_hf}):
            with patch(
                "noodle_nodes.ai_analytical_nodes.safe_request",
                return_value=mock_resp,
            ) as mock_post:
                result = huggingface_inference(
                    inputs="translate: hello",
                    model="t5-small",
                    credentials={"api_token": "hf-123"},
                    endpoint_url="https://my-endpoint.cloud",
                )

        mock_post.assert_called_once()
        assert result["endpoint"] == "https://my-endpoint.cloud"

    def test_endpoint_error_raises(self):
        mock_hf = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.text = "Service unavailable"

        with patch.dict(sys.modules, {"huggingface_hub": mock_hf}):
            with patch(
                "noodle_nodes.ai_analytical_nodes.safe_request",
                return_value=mock_resp,
            ):
                with pytest.raises(RuntimeError, match="503"):
                    huggingface_inference(
                        inputs="test",
                        model="t5-small",
                        endpoint_url="https://endpoint.cloud",
                    )

    def test_sdk_text_generation(self):
        mock_hf_module = MagicMock()
        mock_client = MagicMock()
        mock_client.text_generation.return_value = "Generated text"
        mock_hf_module.InferenceClient.return_value = mock_client

        with patch.dict(sys.modules, {"huggingface_hub": mock_hf_module}):
            result = huggingface_inference(
                inputs="Tell me a story",
                model="gpt2",
                task="text-generation",
                credentials={"api_token": "hf-abc"},
            )

        mock_client.text_generation.assert_called_once()
        assert result["task"] == "text-generation"


# ---------------------------------------------------------------------------
# Task 27 — AI Batch Processor
# ---------------------------------------------------------------------------


class TestAIBatchProcessor:
    def test_processes_all_items(self):
        adapter = _MockChatAdapter("processed")
        result = ai_batch_processor(
            input=["item1", "item2", "item3"],
            model=adapter,
            prompt_template="Process: {{ item }}",
            batch_size=2,
            concurrency=1,
        )
        assert result["total"] == 3
        assert all(r["result"] == "processed" for r in result["results"])

    def test_empty_items_returns_empty(self):
        adapter = _MockChatAdapter("{}")
        result = ai_batch_processor(input=[], model=adapter, prompt_template="{{ item }}")
        assert result["results"] == []
        assert result["total"] == 0

    def test_missing_prompt_template_raises(self):
        adapter = _MockChatAdapter("{}")
        with pytest.raises(ValueError, match="prompt_template"):
            ai_batch_processor(input=["a"], model=adapter, prompt_template="")

    def test_no_model_raises(self):
        with pytest.raises(ValueError, match="AI Chat Model"):
            ai_batch_processor(
                input=["a"],
                model=None,
                prompt_template="{{ item }}",
            )

    def test_continue_on_error_skips_failed(self):
        call_count = 0

        class FailingAdapter(ChatModelAdapter):
            def complete(self, request: ChatRequest) -> ChatResponse:
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise RuntimeError("simulated failure")
                return ChatResponse(text="ok", model="x", usage=ModelUsage())

        result = ai_batch_processor(
            input=["a", "b", "c"],
            model=FailingAdapter(),
            prompt_template="{{ item }}",
            batch_size=1,
            continue_on_error=True,
        )
        assert result["errors"] == 1
        assert result["total"] == 3

    def test_dict_items_preserve_fields(self):
        adapter = _MockChatAdapter("classified")
        result = ai_batch_processor(
            input=[{"id": 1, "text": "hello"}, {"id": 2, "text": "world"}],
            model=adapter,
            prompt_template="Classify: {{ item.text }}",
        )
        assert all("id" in r for r in result["results"])
        assert all(r["result"] == "classified" for r in result["results"])


# ---------------------------------------------------------------------------
# Task 28 — AI Image Classifier
# ---------------------------------------------------------------------------


class TestAIImageClassifier:
    def test_classifies_image(self):
        adapter = _MockChatAdapter('{"classifications": [{"label": "cat", "confidence": 0.9}]}')
        result = ai_image_classifier(
            input=b"fake_image_bytes",
            model=adapter,
            labels="cat, dog, bird",
        )
        assert result["classifications"][0]["label"] == "cat"
        assert result["image_count"] == 1

    def test_multiple_images(self):
        adapter = _MockChatAdapter('{"classifications": [{"label": "dog", "confidence": 0.8}]}')
        result = ai_image_classifier(
            input=[b"img1", b"img2"],
            model=adapter,
            labels="cat, dog",
        )
        assert result["image_count"] == 2
        assert len(result["classifications"]) == 2

    def test_empty_labels_raises(self):
        adapter = _MockChatAdapter("{}")
        with pytest.raises(ValueError, match="labels"):
            ai_image_classifier(input=b"img", model=adapter, labels="")

    def test_no_image_raises(self):
        adapter = _MockChatAdapter("{}")
        with pytest.raises(ValueError, match="image input"):
            ai_image_classifier(input=None, model=adapter, labels="cat")

    def test_no_model_raises(self):
        with pytest.raises(ValueError, match="AI Chat Model"):
            ai_image_classifier(input=b"img", model=None, labels="cat")


# ---------------------------------------------------------------------------
# Task 29 — AI Code Review
# ---------------------------------------------------------------------------


class TestAICodeReview:
    _REVIEW_JSON = json.dumps(
        {
            "issues": [
                {
                    "severity": "high",
                    "line": "5",
                    "category": "security",
                    "description": "SQL injection",
                    "suggestion": "Use parameterized queries",
                },
                {
                    "severity": "low",
                    "line": "10",
                    "category": "style",
                    "description": "Magic number",
                    "suggestion": "Extract constant",
                },
            ],
            "summary": "Found security issue.",
            "language": "Python",
            "overall_quality": "poor",
        }
    )

    def test_returns_issues(self):
        adapter = _MockChatAdapter(self._REVIEW_JSON)
        result = ai_code_review(
            code="SELECT * FROM users WHERE id = " + "'" + "x' OR 1=1",
            model=adapter,
            severity_threshold="low",
        )
        assert len(result["issues"]) == 2

    def test_severity_threshold_filters(self):
        adapter = _MockChatAdapter(self._REVIEW_JSON)
        result = ai_code_review(
            code="code here",
            model=adapter,
            severity_threshold="high",
        )
        # Only high+ severity issues
        assert all(i["severity"] in ("high", "critical") for i in result["issues"])
        assert result["all_issue_count"] == 2

    def test_empty_code_raises(self):
        adapter = _MockChatAdapter("{}")
        with pytest.raises(ValueError, match="code input"):
            ai_code_review(code="", model=adapter)

    def test_no_model_raises(self):
        with pytest.raises(ValueError, match="AI Chat Model"):
            ai_code_review(code="x = 1", model=None)

    def test_non_json_response_handled(self):
        adapter = _MockChatAdapter("No issues found.")
        result = ai_code_review(code="x = 1", model=adapter)
        assert result["issues"] == []
        assert "raw" in result

    def test_wired_input_code(self):
        adapter = _MockChatAdapter(self._REVIEW_JSON)
        result = ai_code_review(input="def foo(): pass", model=adapter)
        assert result["language"] == "Python"
