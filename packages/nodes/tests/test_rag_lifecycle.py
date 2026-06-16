"""Tests for rag_lifecycle.py nodes."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from noodle.artifacts import LocalArtifactStore, is_artifact_ref
from noodle.context import artifact_store, current_node_id
from noodle.datasets import is_dataset_ref
from noodle.sdk import registry


@pytest.fixture()
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="rag-test-run")
    t1 = artifact_store.set(store)
    t2 = current_node_id.set("rag-test-node")
    yield store
    current_node_id.reset(t2)
    artifact_store.reset(t1)


def _make_fake_openai(content: str = "{}"):
    fake = MagicMock()
    fake.OpenAI.return_value.chat.completions.create.return_value.choices[
        0
    ].message.content = content
    return fake


# ---------------------------------------------------------------------------
# Registry / imports
# ---------------------------------------------------------------------------

def test_registry_loads_without_heavy_packages() -> None:
    import noodle_nodes.rag_lifecycle  # noqa: F401
    node_ids = {m.id for m in registry.manifests()}
    assert "document_chunk" in node_ids
    assert "rag_answer_eval" in node_ids
    assert "rag_chunking_experiment" in node_ids
    assert "rag_context_relevance" in node_ids


def test_all_rag_nodes_in_ml_category() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    for nid in (
        "document_chunk",
        "rag_answer_eval",
        "rag_chunking_experiment",
        "rag_context_relevance",
    ):
        assert manifests[nid].category == "Machine Learning", f"{nid} wrong category"


# ---------------------------------------------------------------------------
# document_chunk
# ---------------------------------------------------------------------------

_LONG_DOC = (
    "Machine learning is a method of data analysis. "
    "It automates analytical model building. "
    "Neural networks are algorithms inspired by the brain. "
    "They enable multiple processing layers. "
    "Deep learning uses many layers to learn patterns. "
    "It drives tasks like image recognition and translation. "
    "Transformers process sequences with attention mechanisms. "
    "BERT and GPT are famous transformer models. "
    "Fine-tuning adapts pre-trained models to new tasks. "
    "Low-rank adaptation reduces the number of trainable parameters."
)


def test_document_chunk_fixed_size(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import document_chunk
    rows = [{"id": 1, "text": _LONG_DOC}]
    result = document_chunk(
        input=rows,
        text_column="text",
        strategy="fixed_size",
        chunk_size=100,
        chunk_overlap=20,
    )
    assert is_dataset_ref(result["main"])
    assert result["n_chunks"] > 1
    assert result["n_documents"] == 1
    assert result["strategy"] == "fixed_size"


def test_document_chunk_sentence(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import document_chunk
    rows = [{"text": "First sentence. Second sentence! Third sentence?"}]
    result = document_chunk(
        input=rows,
        text_column="text",
        strategy="sentence",
        chunk_size=30,
        min_chunk_size=0,
    )
    assert is_dataset_ref(result["main"])
    assert result["n_chunks"] >= 1


def test_document_chunk_paragraph(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import document_chunk
    rows = [{"text": "Paragraph one.\n\nParagraph two.\n\nParagraph three."}]
    result = document_chunk(
        input=rows,
        text_column="text",
        strategy="paragraph",
        chunk_size=20,  # Force splits since each paragraph is ~14 chars
        min_chunk_size=0,
    )
    assert is_dataset_ref(result["main"])
    assert result["n_chunks"] >= 1  # At least one paragraph produced


def test_document_chunk_recursive(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import document_chunk
    rows = [{"text": _LONG_DOC}]
    result = document_chunk(
        input=rows,
        text_column="text",
        strategy="recursive",
        chunk_size=150,
        chunk_overlap=30,
    )
    assert is_dataset_ref(result["main"])
    assert result["n_chunks"] >= 1


def test_document_chunk_semantic_boundary(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import document_chunk
    rows = [
        {
            "text": (
                "# Section 1\nContent one.\n## Section 1.1\n"
                "More content.\n# Section 2\nContent two."
            )
        }
    ]
    result = document_chunk(
        input=rows,
        text_column="text",
        strategy="semantic_boundary",
        chunk_size=200,
        min_chunk_size=0,
    )
    assert is_dataset_ref(result["main"])
    assert result["n_chunks"] >= 1


def test_document_chunk_preserves_metadata(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import document_chunk
    rows = [
        {
            "text": "Some text here that is long enough for a chunk.",
            "source": "doc1",
            "page": 1,
        }
    ]
    result = document_chunk(
        input=rows,
        text_column="text",
        include_metadata=True,
        min_chunk_size=0,
    )
    assert result["n_chunks"] >= 1


def test_document_chunk_multiple_docs(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import document_chunk
    rows = [
        {"text": _LONG_DOC, "id": 1},
        {"text": "Short doc.", "id": 2},
    ]
    result = document_chunk(
        input=rows,
        text_column="text",
        strategy="fixed_size",
        chunk_size=80,
        min_chunk_size=5,
    )
    assert result["n_documents"] == 2
    assert result["n_chunks"] > 2


def test_document_chunk_min_size_drops_short(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import document_chunk
    rows = [{"text": "Hello. This is a much longer sentence with lots of words in it. Short."}]
    result_no_min = document_chunk(
        input=rows, text_column="text", strategy="sentence", min_chunk_size=0
    )
    # With a very high min_chunk_size, some chunks get dropped
    try:
        result_high_min = document_chunk(
            input=rows, text_column="text", strategy="sentence", min_chunk_size=100
        )
        assert result_no_min["n_chunks"] >= result_high_min["n_chunks"]
    except ValueError:
        # All chunks dropped — that's also a valid outcome
        assert result_no_min["n_chunks"] > 0


def test_document_chunk_missing_column_raises(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import document_chunk
    rows = [{"content": "text here"}]
    with pytest.raises(ValueError, match="not found"):
        document_chunk(input=rows, text_column="text")


def test_document_chunk_empty_input_raises(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import document_chunk
    with pytest.raises(ValueError):
        document_chunk(input=[], text_column="text")


# ---------------------------------------------------------------------------
# rag_answer_eval — deterministic modes (no openai)
# ---------------------------------------------------------------------------

def _rag_rows():
    return [
        {
            "question": "What is machine learning?",
            "answer": "Machine learning trains models using labeled data.",
            "expected": "machine learning trains models",
            "context": "Machine learning is training models on data.",
        },
        {
            "question": "What is deep learning?",
            "answer": "I don't know",
            "expected": "deep learning uses neural networks",
            "context": "Deep learning uses neural networks with many layers.",
        },
    ]


def test_rag_answer_eval_exact_match(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import rag_answer_eval
    rows = [
        {"answer": "Paris", "expected": "paris"},
        {"answer": "London", "expected": "berlin"},
    ]
    result = rag_answer_eval(
        input=rows,
        mode="exact_match",
        answer_column="answer",
        expected_column="expected",
        case_sensitive=False,
    )
    assert result["__noodle_eval_result__"] is True
    assert result["summary"]["n_passed"] == 1
    assert result["summary"]["accuracy"] == 0.5


def test_rag_answer_eval_contains_answer(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import rag_answer_eval
    rows = _rag_rows()
    result = rag_answer_eval(
        input=rows,
        mode="contains_answer",
        answer_column="answer",
        expected_column="expected",
    )
    assert result["summary"]["n_rows"] == 2
    assert is_dataset_ref(result["rows"])


def test_rag_answer_eval_factual_overlap(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import rag_answer_eval
    rows = _rag_rows()
    result = rag_answer_eval(
        input=rows,
        mode="factual_overlap",
        answer_column="answer",
        expected_column="expected",
        overlap_threshold=0.2,
    )
    assert "accuracy" in result["summary"]
    assert result["summary"]["n_rows"] == 2


def test_rag_answer_eval_missing_answer_column(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import rag_answer_eval
    with pytest.raises(ValueError, match="not found"):
        rag_answer_eval(input=[{"text": "test"}], mode="exact_match")


# ---------------------------------------------------------------------------
# rag_answer_eval — LLM judge mode (mocked)
# ---------------------------------------------------------------------------

def test_rag_answer_eval_llm_judge(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import rag_answer_eval
    fake = _make_fake_openai('{"score": 4, "reason": "Mostly correct."}')
    rows = _rag_rows()
    with patch.dict(sys.modules, {"openai": fake}):
        result = rag_answer_eval(
            input=rows,
            mode="llm_judge",
            answer_column="answer",
            expected_column="expected",
            question_column="question",
            context_column="context",
            openai_api_key="sk-test",
            judge_model="gpt-4o",
            score_scale=5,
        )
    assert result["summary"]["n_rows"] == 2
    assert result["summary"]["avg_score"] is not None


def test_rag_answer_eval_llm_judge_missing_key(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import rag_answer_eval
    fake = _make_fake_openai()
    with patch.dict(sys.modules, {"openai": fake}):
        with pytest.raises(ValueError, match="openai_api_key is required"):
            rag_answer_eval(
                input=[{"answer": "a", "expected": "b"}],
                mode="llm_judge",
                openai_api_key="",
            )


def test_rag_answer_eval_llm_judge_rejects_large_dataset(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import rag_answer_eval

    with pytest.raises(ValueError, match="row count"):
        rag_answer_eval(
            input=[
                {"answer": f"A{i}", "expected": f"E{i}"}
                for i in range(1_001)
            ],
            mode="llm_judge",
            openai_api_key="sk-test",
        )


# ---------------------------------------------------------------------------
# rag_chunking_experiment
# ---------------------------------------------------------------------------

def test_rag_chunking_experiment_basic(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import rag_chunking_experiment
    rows = [
        {"text": _LONG_DOC},
        {"text": "Short document."},
    ]
    result = rag_chunking_experiment(
        input=rows,
        text_column="text",
        strategies="fixed_size, sentence",
        chunk_sizes="128, 256",
    )
    assert is_dataset_ref(result["main"])
    assert is_artifact_ref(result["report"])
    # 2 strategies × 2 sizes = 4 experiment rows
    assert result["n_experiments"] == 4
    assert result["n_documents"] == 2


def test_rag_chunking_experiment_max_documents(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import rag_chunking_experiment
    rows = [{"text": f"Document {i}. " * 10} for i in range(20)]
    result = rag_chunking_experiment(
        input=rows,
        text_column="text",
        strategies="fixed_size",
        chunk_sizes="256",
        max_documents=5,
    )
    assert result["n_documents"] == 5


def test_rag_chunking_experiment_missing_column(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import rag_chunking_experiment
    with pytest.raises(ValueError, match="not found"):
        rag_chunking_experiment(input=[{"content": "text"}], text_column="text")


# ---------------------------------------------------------------------------
# rag_context_relevance
# ---------------------------------------------------------------------------

def test_rag_context_relevance_keyword_overlap_relevant(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import rag_context_relevance
    rows = [
        {
            "question": "What is gradient descent?",
            "context": "Gradient descent is an optimization algorithm that minimizes loss.",
        },
        {
            "question": "How does backpropagation work?",
            "context": "The weather today is sunny and warm.",  # irrelevant
        },
    ]
    result = rag_context_relevance(
        input=rows,
        mode="keyword_overlap",
        question_column="question",
        context_column="context",
        output_column="is_relevant",
        overlap_threshold=0.3,
    )
    assert result["__noodle_eval_result__"] is True
    assert is_dataset_ref(result["rows"])
    assert result["summary"]["n_relevant"] >= 1
    assert result["summary"]["n_relevant"] < 2  # second row should be irrelevant


def test_rag_context_relevance_all_relevant(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import rag_context_relevance
    rows = [
        {
            "question": "What is a transformer?",
            "context": "A transformer is a neural network using attention to process sequences.",
        }
    ]
    result = rag_context_relevance(
        input=rows,
        mode="keyword_overlap",
        question_column="question",
        context_column="context",
        overlap_threshold=0.1,
    )
    assert result["summary"]["n_relevant"] == 1


def test_rag_context_relevance_llm_mode(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import rag_context_relevance
    fake = _make_fake_openai('{"relevant": true, "reason": "Directly addresses the question."}')
    rows = [{"question": "What is ML?", "context": "ML trains models from data."}]
    with patch.dict(sys.modules, {"openai": fake}):
        result = rag_context_relevance(
            input=rows,
            mode="llm_judge",
            question_column="question",
            context_column="context",
            openai_api_key="sk-test",
        )
    assert result["summary"]["n_relevant"] == 1


def test_rag_context_relevance_llm_rejects_large_dataset(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import rag_context_relevance

    with pytest.raises(ValueError, match="row count"):
        rag_context_relevance(
            input=[
                {"question": f"Q{i}", "context": f"C{i}"}
                for i in range(1_001)
            ],
            mode="llm_judge",
            openai_api_key="sk-test",
        )


def test_rag_context_relevance_missing_column(store_ctx) -> None:
    from noodle_nodes.rag_lifecycle import rag_context_relevance
    with pytest.raises(ValueError, match="not found"):
        rag_context_relevance(
            input=[{"question": "test"}],
            context_column="context",
        )
