"""Tests for synthetic_data.py nodes."""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from noodle.artifacts import LocalArtifactStore, is_artifact_ref
from noodle.context import artifact_store, current_node_id
from noodle.datasets import is_dataset_ref
from noodle.sdk import registry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="synth-test-run")
    t1 = artifact_store.set(store)
    t2 = current_node_id.set("synth-test-node")
    yield store
    current_node_id.reset(t2)
    artifact_store.reset(t1)


def _make_fake_openai(
    chat_content: str | list[str] | None = None,
    side_effects: list[Any] | None = None,
):
    """Return a fake openai module with a controllable chat mock."""
    fake = MagicMock()

    call_count = [0]
    contents = (
        chat_content
        if isinstance(chat_content, list)
        else ([chat_content] if chat_content is not None else None)
    )

    def _create(**kwargs):
        resp = MagicMock()
        if side_effects and call_count[0] < len(side_effects):
            effect = side_effects[call_count[0]]
            call_count[0] += 1
            if isinstance(effect, Exception):
                raise effect
            resp.choices[0].message.content = effect
        elif contents:
            idx = min(call_count[0], len(contents) - 1)
            resp.choices[0].message.content = contents[idx]
            call_count[0] += 1
        else:
            resp.choices[0].message.content = '[{"prompt": "Q?", "answer": "A."}]'
        return resp

    fake.OpenAI.return_value.chat.completions.create.side_effect = _create
    return fake


@contextmanager
def _patched_openai(fake):
    with patch.dict(sys.modules, {"openai": fake}):
        yield


# ---------------------------------------------------------------------------
# Registry / import tests
# ---------------------------------------------------------------------------

def test_registry_loads_without_heavy_packages() -> None:
    """synthetic_data.py must register its nodes without importing openai/tiktoken."""
    import noodle_nodes.synthetic_data  # noqa: F401
    node_ids = {m.id for m in registry.manifests()}
    assert "synthetic_examples_generate" in node_ids
    assert "preference_pair_generate" in node_ids
    assert "weak_label" in node_ids
    assert "token_profile" in node_ids


def test_all_synth_nodes_in_ml_category() -> None:
    node_ids = {
        "synthetic_examples_generate",
        "preference_pair_generate",
        "weak_label",
        "token_profile",
    }
    manifests = {m.id: m for m in registry.manifests()}
    for nid in node_ids:
        assert manifests[nid].category == "Machine Learning", (
            f"{nid} should be in Machine Learning category"
        )


def test_openai_nodes_declare_requirements() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    # These always need openai regardless of mode
    for nid in ("synthetic_examples_generate", "preference_pair_generate"):
        reqs = manifests[nid].requirements
        assert any("openai" in r for r in reqs), (
            f"{nid} must declare openai>=1.0 in requirements"
        )
    # weak_label's rule modes work without openai; no static requirement declared
    assert manifests["weak_label"].requirements == [] or not any(
        "openai" in r for r in manifests["weak_label"].requirements
    )


def test_token_profile_declares_tiktoken() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    reqs = manifests["token_profile"].requirements
    assert any("tiktoken" in r for r in reqs)


# ---------------------------------------------------------------------------
# Token Profile (uses tiktoken — may skip if not installed)
# ---------------------------------------------------------------------------

def _has_tiktoken() -> bool:
    try:
        import tiktoken  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _has_tiktoken(), reason="tiktoken not installed")
def test_token_profile_basic(store_ctx) -> None:
    from noodle_nodes.synthetic_data import token_profile
    rows = [
        {"prompt": "What is machine learning?", "answer": "ML is learning from data."},
        {"prompt": "What is a neural network?", "answer": "A graph of interconnected nodes."},
        {"prompt": "What is fine-tuning?", "answer": "Adapting a pre-trained model."},
    ]
    result = token_profile(
        input=rows,
        text_columns="prompt, answer",
        encoding="cl100k_base",
        add_token_column=True,
    )
    assert is_dataset_ref(result["main"])
    assert result["n_rows"] == 3
    assert result["total_tokens"] > 0
    assert result["mean_tokens"] > 0
    assert "encoding" in result


@pytest.mark.skipif(not _has_tiktoken(), reason="tiktoken not installed")
def test_token_profile_cost_estimate(store_ctx) -> None:
    from noodle_nodes.synthetic_data import token_profile
    rows = [{"text": "Hello world " * 10}]
    result = token_profile(
        input=rows,
        text_columns="text",
        add_token_column=True,
        price_per_1k_tokens=0.002,
    )
    assert "estimated_cost_usd" in result
    assert result["estimated_cost_usd"] >= 0.0


@pytest.mark.skipif(not _has_tiktoken(), reason="tiktoken not installed")
def test_token_profile_oversized_warning(store_ctx) -> None:
    from noodle_nodes.synthetic_data import token_profile
    # Create a row that will exceed the warning threshold
    rows = [
        {"text": "word " * 50},
        {"text": "short"},
    ]
    result = token_profile(
        input=rows,
        text_columns="text",
        add_token_column=False,
        max_tokens_warning=10,  # Very low threshold
    )
    assert result["n_oversized"] >= 1


@pytest.mark.skipif(not _has_tiktoken(), reason="tiktoken not installed")
def test_token_profile_missing_column(store_ctx) -> None:
    from noodle_nodes.synthetic_data import token_profile
    rows = [{"prompt": "test"}]
    with pytest.raises(ValueError, match="not found"):
        token_profile(input=rows, text_columns="nonexistent")


# ---------------------------------------------------------------------------
# Weak Label — rule modes (no OpenAI needed)
# ---------------------------------------------------------------------------

def test_weak_label_rule_contains(store_ctx) -> None:
    from noodle_nodes.synthetic_data import weak_label
    rows = [
        {"text": "I need help with my invoice"},
        {"text": "There's a bug in the software"},
        {"text": "Just saying hello"},
    ]
    patterns = json.dumps({"billing": ["invoice", "payment"], "tech": ["bug", "error"]})
    result = weak_label(
        input=rows,
        mode="rule_contains",
        text_column="text",
        labels="billing, tech",
        output_column="label",
        rule_patterns=patterns,
        default_label="other",
    )
    assert is_dataset_ref(result["main"])
    assert result["n_labeled"] >= 2
    counts = result["label_distribution"]
    assert counts.get("billing", 0) >= 1
    assert counts.get("tech", 0) >= 1


def test_weak_label_rule_exact(store_ctx) -> None:
    from noodle_nodes.synthetic_data import weak_label
    rows = [
        {"text": "yes"},
        {"text": "no"},
        {"text": "maybe"},
    ]
    patterns = json.dumps({"positive": ["yes"], "negative": ["no"]})
    result = weak_label(
        input=rows,
        mode="rule_exact",
        text_column="text",
        labels="positive,negative",
        output_column="sentiment",
        rule_patterns=patterns,
        default_label="neutral",
    )
    assert result["label_distribution"].get("positive", 0) == 1
    assert result["label_distribution"].get("negative", 0) == 1
    assert result["label_distribution"].get("neutral", 0) == 1


def test_weak_label_rule_regex(store_ctx) -> None:
    from noodle_nodes.synthetic_data import weak_label
    rows = [
        {"text": "Error code 404"},
        {"text": "Status: OK"},
    ]
    patterns = json.dumps({"error": ["error\\s+code\\s+\\d+"]})
    result = weak_label(
        input=rows,
        mode="rule_regex",
        text_column="text",
        labels="error",
        output_column="label",
        rule_patterns=patterns,
        default_label="ok",
    )
    assert result["label_distribution"].get("error", 0) == 1
    assert result["label_distribution"].get("ok", 0) == 1


def test_weak_label_keyword_vote(store_ctx) -> None:
    from noodle_nodes.synthetic_data import weak_label
    rows = [{"text": "invoice payment billing question"},
            {"text": "software error crash bug"}]
    patterns = json.dumps({
        "billing": ["invoice", "payment", "billing"],
        "tech": ["error", "crash", "bug"],
    })
    result = weak_label(
        input=rows,
        mode="rule_keyword_vote",
        text_column="text",
        labels="billing,tech",
        output_column="label",
        rule_patterns=patterns,
    )
    dist = result["label_distribution"]
    assert dist.get("billing", 0) == 1
    assert dist.get("tech", 0) == 1


def test_weak_label_missing_column_raises(store_ctx) -> None:
    from noodle_nodes.synthetic_data import weak_label
    rows = [{"prompt": "test"}]
    patterns = json.dumps({"a": ["test"]})
    with pytest.raises(ValueError, match="not found"):
        weak_label(
            input=rows,
            mode="rule_contains",
            text_column="nonexistent",
            labels="a",
            rule_patterns=patterns,
        )


def test_weak_label_invalid_rule_json(store_ctx) -> None:
    from noodle_nodes.synthetic_data import weak_label
    rows = [{"text": "hello"}]
    with pytest.raises(ValueError, match="not valid JSON"):
        weak_label(
            input=rows,
            mode="rule_contains",
            text_column="text",
            labels="a",
            rule_patterns="{invalid json",
        )


# ---------------------------------------------------------------------------
# Weak Label — LLM mode (mocked openai)
# ---------------------------------------------------------------------------

def test_weak_label_llm_classify(store_ctx) -> None:
    from noodle_nodes.synthetic_data import weak_label
    fake = _make_fake_openai('{"label": "billing", "confidence": 0.9}')
    rows = [
        {"text": "I need a refund for my subscription."},
        {"text": "The app crashes on startup."},
    ]
    with _patched_openai(fake):
        result = weak_label(
            input=rows,
            mode="llm_classify",
            text_column="text",
            labels="billing, tech, other",
            output_column="category",
            openai_api_key="sk-test",
            model="gpt-4.1-mini",
            add_confidence=True,
        )
    assert is_dataset_ref(result["main"])
    assert result["n_labeled"] == 2
    assert "billing" in result["label_distribution"]


def test_weak_label_llm_missing_key(store_ctx) -> None:
    from noodle_nodes.synthetic_data import weak_label
    rows = [{"text": "test"}]
    fake = _make_fake_openai()
    with _patched_openai(fake):
        with pytest.raises(ValueError, match="openai_api_key is required"):
            weak_label(
                input=rows,
                mode="llm_classify",
                text_column="text",
                labels="a,b",
                openai_api_key="",
            )


# ---------------------------------------------------------------------------
# Synthetic Examples Generate (mocked openai)
# ---------------------------------------------------------------------------

def test_synthetic_examples_generate_basic(store_ctx) -> None:
    from noodle_nodes.synthetic_data import synthetic_examples_generate
    generated = json.dumps([
        {"user": "What is ML?", "assistant": "ML is learning from data."},
        {"user": "What is DL?", "assistant": "DL uses neural networks."},
        {"user": "What is RL?", "assistant": "RL learns via rewards."},
        {"user": "What is NLP?", "assistant": "NLP processes text."},
        {"user": "What is CV?", "assistant": "CV processes images."},
    ])
    fake = _make_fake_openai(generated)
    with _patched_openai(fake):
        result = synthetic_examples_generate(
            input=None,
            openai_api_key="sk-test",
            model="gpt-4.1-mini",
            instruction="Generate Q&A pairs about machine learning concepts.",
            n_examples=5,
            batch_size=5,
        )
    assert is_dataset_ref(result["main"])
    assert result["n_generated"] >= 1
    assert result["n_requested"] == 5


def test_synthetic_examples_with_seed_rows(store_ctx) -> None:
    from noodle_nodes.synthetic_data import synthetic_examples_generate
    seeds = [
        {"user": "What is gradient descent?", "assistant": "It minimizes loss."},
        {"user": "What is backprop?", "assistant": "It computes gradients."},
    ]
    generated = json.dumps([
        {"user": "What is momentum?", "assistant": "Momentum accelerates SGD."},
    ])
    fake = _make_fake_openai(generated)
    with _patched_openai(fake):
        result = synthetic_examples_generate(
            input=seeds,
            openai_api_key="sk-test",
            instruction="Generate more ML Q&A pairs similar to the seeds.",
            n_examples=1,
            batch_size=1,
            n_seed_examples=2,
        )
    assert result["n_generated"] >= 1


def test_synthetic_examples_dedup(store_ctx) -> None:
    from noodle_nodes.synthetic_data import synthetic_examples_generate
    # Return 3 items where 2 have the same 'user' field
    generated = json.dumps([
        {"user": "What is ML?", "assistant": "A."},
        {"user": "What is ML?", "assistant": "B."},  # duplicate user
        {"user": "What is DL?", "assistant": "C."},
    ])
    fake = _make_fake_openai(generated)
    with _patched_openai(fake):
        result = synthetic_examples_generate(
            input=None,
            openai_api_key="sk-test",
            instruction="Generate ML Q&A pairs.",
            n_examples=3,
            batch_size=3,
            dedupe_column="user",
        )
    assert result["dupes_removed"] >= 1
    assert result["n_generated"] == 2


def test_synthetic_examples_missing_instruction(store_ctx) -> None:
    from noodle_nodes.synthetic_data import synthetic_examples_generate
    fake = _make_fake_openai()
    with _patched_openai(fake):
        with pytest.raises(ValueError, match="instruction is required"):
            synthetic_examples_generate(
                input=None,
                openai_api_key="sk-test",
                instruction="",
            )


def test_synthetic_examples_missing_key(store_ctx) -> None:
    from noodle_nodes.synthetic_data import synthetic_examples_generate
    fake = _make_fake_openai()
    with _patched_openai(fake):
        with pytest.raises(ValueError, match="openai_api_key is required"):
            synthetic_examples_generate(
                input=None,
                openai_api_key="",
                instruction="Generate something.",
            )


# ---------------------------------------------------------------------------
# Preference Pair Generate (mocked openai)
# ---------------------------------------------------------------------------

def test_preference_pair_generate_basic(store_ctx) -> None:
    from noodle_nodes.synthetic_data import preference_pair_generate
    call_n = [0]
    responses = ["Helpful chosen response.", "Bad rejected response."] * 5

    def _create(**kwargs):
        resp = MagicMock()
        idx = call_n[0] % len(responses)
        resp.choices[0].message.content = responses[idx]
        call_n[0] += 1
        return resp

    fake = MagicMock()
    fake.OpenAI.return_value.chat.completions.create.side_effect = _create

    prompts = [
        {"prompt": "What is supervised learning?"},
        {"prompt": "Explain neural networks."},
    ]
    with _patched_openai(fake):
        result = preference_pair_generate(
            input=prompts,
            openai_api_key="sk-test",
            prompt_column="prompt",
            chosen_model="gpt-4.1",
            rejected_model="gpt-4.1-mini",
        )
    assert is_dataset_ref(result["main"])
    assert result["n_pairs"] == 2
    assert result["n_input"] == 2


def test_preference_pair_generate_missing_column(store_ctx) -> None:
    from noodle_nodes.synthetic_data import preference_pair_generate
    fake = _make_fake_openai()
    rows = [{"question": "test"}]
    with _patched_openai(fake):
        with pytest.raises(ValueError, match="not found"):
            preference_pair_generate(
                input=rows,
                openai_api_key="sk-test",
                prompt_column="prompt",
            )


def test_preference_pair_generate_max_rows(store_ctx) -> None:
    from noodle_nodes.synthetic_data import preference_pair_generate
    call_n = [0]

    def _create(**kwargs):
        resp = MagicMock()
        resp.choices[0].message.content = "Response text."
        call_n[0] += 1
        return resp

    fake = MagicMock()
    fake.OpenAI.return_value.chat.completions.create.side_effect = _create

    rows = [{"prompt": f"Question {i}?"} for i in range(10)]
    with _patched_openai(fake):
        result = preference_pair_generate(
            input=rows,
            openai_api_key="sk-test",
            prompt_column="prompt",
            chosen_model="gpt-4.1-mini",
            max_rows=3,
        )
    assert result["n_input"] == 3  # capped at max_rows
    assert result["n_pairs"] <= 3
