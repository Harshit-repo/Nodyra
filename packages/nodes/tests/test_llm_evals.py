"""Tests for LLM evaluation nodes in llm_evals.py."""

from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import noodle_nodes  # noqa: F401 - registers nodes
from noodle.artifacts import LocalArtifactStore, is_artifact_ref
from noodle.context import artifact_store, current_node_id
from noodle.datasets import is_dataset_ref
from noodle.sdk import registry
from noodle_nodes.datasets import dataset_to_records, records_to_dataset
from noodle_nodes.llm_evals import (
    _EVAL_RESULT_MARKER,
    eval_gate,
    eval_report,
    llm_compare_models,
    llm_eval_dataset,
    llm_judge,
    llm_rule_eval,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="eval-test-run")
    a = artifact_store.set(store)
    n = current_node_id.set("eval-test-node")
    yield store
    current_node_id.reset(n)
    artifact_store.reset(a)


def _eval_records(n: int = 20) -> list[dict[str, Any]]:
    return [
        {
            "prompt": f"What is {i} * 2?",
            "expected": str(i * 2),
        }
        for i in range(1, n + 1)
    ]


def _rule_eval_records(correct_fraction: float = 0.8, n: int = 20) -> list[dict[str, Any]]:
    rows = []
    for i in range(n):
        expected = str(i)
        output = expected if i / n < correct_fraction else "wrong"
        rows.append({"output": output, "expected": expected})
    return rows


def _eval_result(
    accuracy: float = 0.85,
    n_rows: int = 20,
    kind: str = "rule_eval",
) -> dict[str, Any]:
    return {
        _EVAL_RESULT_MARKER: True,
        "version": 1,
        "kind": kind,
        "summary": {
            "accuracy": accuracy,
            "n_rows": n_rows,
            "n_passed": int(n_rows * accuracy),
            "n_failed": int(n_rows * (1 - accuracy)),
        },
        "rows": None,
        "created_at": "2026-06-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Registry / import isolation
# ---------------------------------------------------------------------------

def test_eval_nodes_register_in_machine_learning_category() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    expected_ids = {
        "llm_eval_dataset",
        "llm_compare_models",
        "llm_judge",
        "llm_rule_eval",
        "eval_gate",
        "eval_report",
    }
    assert expected_ids <= manifests.keys(), (
        f"Missing: {expected_ids - manifests.keys()}"
    )
    for node_id in expected_ids:
        assert manifests[node_id].category == "Machine Learning"


def test_eval_nodes_declare_requirements() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    for node_id in ("llm_compare_models", "llm_judge"):
        reqs = manifests[node_id].requirements
        assert any("openai" in r for r in reqs), (
            f"{node_id} must declare openai requirement"
        )
    for node_id in ("llm_eval_dataset", "llm_compare_models"):
        reqs = manifests[node_id].requirements
        assert any("pandas" in r for r in reqs)

    # eval_gate and eval_report need no heavy packages
    for node_id in ("eval_gate", "eval_report"):
        reqs = manifests[node_id].requirements
        assert reqs == [], (
            f"{node_id} should have no requirements, got {reqs}"
        )


# ---------------------------------------------------------------------------
# llm_eval_dataset
# ---------------------------------------------------------------------------

def test_eval_dataset_from_records(store_ctx) -> None:
    rows = _eval_records(30)
    result = llm_eval_dataset(
        input=rows,
        prompt_column="prompt",
        expected_column="expected",
    )
    assert is_dataset_ref(result["dataset"])
    assert result["n_rows"] == 30
    assert result["prompt_column"] == "prompt"
    assert result["expected_column"] == "expected"


def test_eval_dataset_from_dataset_ref(store_ctx) -> None:
    ds = records_to_dataset(_eval_records(25))
    result = llm_eval_dataset(
        input=ds,
        prompt_column="prompt",
        expected_column="expected",
    )
    assert result["n_rows"] == 25


def test_eval_dataset_sample_size(store_ctx) -> None:
    rows = _eval_records(100)
    result = llm_eval_dataset(
        input=rows,
        prompt_column="prompt",
        expected_column="expected",
        sample_size=20,
        random_seed=42,
    )
    assert result["n_rows"] == 20


def test_eval_dataset_deduplication(store_ctx) -> None:
    rows = _eval_records(10) * 2  # 20 rows, 10 unique prompts
    result = llm_eval_dataset(
        input=rows,
        prompt_column="prompt",
        expected_column="expected",
        dedupe=True,
    )
    assert result["n_rows"] == 10


def test_eval_dataset_rejects_missing_columns(store_ctx) -> None:
    rows = [{"text": "hello", "label": "world"}]
    with pytest.raises(ValueError, match="prompt|Required"):
        llm_eval_dataset(
            input=rows,
            prompt_column="prompt",
            expected_column="expected",
        )


def test_eval_dataset_empty_after_filter_raises(store_ctx) -> None:
    rows = [{"prompt": None, "expected": None}]
    with pytest.raises(ValueError, match="empty"):
        llm_eval_dataset(input=rows, prompt_column="prompt", expected_column="expected")


# ---------------------------------------------------------------------------
# llm_rule_eval
# ---------------------------------------------------------------------------

def test_rule_eval_exact_match_perfect_score(store_ctx) -> None:
    rows = [{"output": str(i), "expected": str(i)} for i in range(20)]
    result = llm_rule_eval(
        input=rows,
        mode="exact_match",
        output_column="output",
        expected_column="expected",
    )
    assert result[_EVAL_RESULT_MARKER] is True
    assert result["summary"]["accuracy"] == 1.0
    assert result["summary"]["n_passed"] == 20


def test_rule_eval_exact_match_partial_score(store_ctx) -> None:
    rows = [
        {"output": "correct" if i < 15 else "wrong", "expected": "correct"}
        for i in range(20)
    ]
    result = llm_rule_eval(
        input=rows,
        mode="exact_match",
        output_column="output",
        expected_column="expected",
    )
    assert result["summary"]["n_passed"] == 15
    assert result["summary"]["accuracy"] == pytest.approx(0.75)


def test_rule_eval_contains_mode(store_ctx) -> None:
    rows = [
        {"output": "The answer is 42 and more", "expected": "42"},
        {"output": "No answer here", "expected": "42"},
    ]
    result = llm_rule_eval(
        input=rows,
        mode="contains",
        output_column="output",
        expected_column="expected",
    )
    rows_out = dataset_to_records(result["rows"])
    passed = [r["eval_passed"] for r in rows_out]
    assert passed[0] is True
    assert passed[1] is False


def test_rule_eval_regex_mode(store_ctx) -> None:
    rows = [
        {"output": "The price is $9.99"},
        {"output": "No price here"},
    ]
    result = llm_rule_eval(
        input=rows,
        mode="regex",
        output_column="output",
        expected_column="expected",
        pattern=r"\$\d+\.\d{2}",
    )
    rows_out = dataset_to_records(result["rows"])
    assert rows_out[0]["eval_passed"] is True
    assert rows_out[1]["eval_passed"] is False


def test_rule_eval_numeric_tolerance(store_ctx) -> None:
    rows = [
        {"output": "3.14159", "expected": "3.14"},
        {"output": "100.0", "expected": "3.14"},
    ]
    result = llm_rule_eval(
        input=rows,
        mode="numeric_tolerance",
        output_column="output",
        expected_column="expected",
        numeric_tolerance=0.01,
    )
    rows_out = dataset_to_records(result["rows"])
    assert rows_out[0]["eval_passed"] is True
    assert rows_out[1]["eval_passed"] is False


def test_rule_eval_json_schema_mode(store_ctx) -> None:
    schema = json.dumps({"type": "object", "properties": {"name": {"type": "string"}}})
    rows = [
        {"output": '{"name": "Alice"}'},
        {"output": "not json"},
        {"output": '["array", "not", "object"]'},
    ]
    result = llm_rule_eval(
        input=rows,
        mode="json_schema",
        output_column="output",
        json_schema_str=schema,
    )
    rows_out = dataset_to_records(result["rows"])
    assert rows_out[0]["eval_passed"] is True
    assert rows_out[1]["eval_passed"] is False
    assert rows_out[2]["eval_passed"] is False


def test_rule_eval_produces_dataset_ref_rows(store_ctx) -> None:
    rows = _rule_eval_records(correct_fraction=1.0, n=10)
    result = llm_rule_eval(
        input=rows,
        mode="exact_match",
        output_column="output",
        expected_column="expected",
    )
    assert is_dataset_ref(result["rows"])


def test_rule_eval_accepts_eval_result_input(store_ctx) -> None:
    """Rule eval should accept an EvalResultRef with rows DatasetRef."""
    rows = _eval_records(10)
    ds = records_to_dataset(
        [{"output": r["expected"], "expected": r["expected"]} for r in rows]
    )
    eval_input = {
        _EVAL_RESULT_MARKER: True,
        "version": 1,
        "kind": "model_comparison",
        "summary": {},
        "rows": ds,
    }
    result = llm_rule_eval(
        input=eval_input,
        mode="exact_match",
        output_column="output",
        expected_column="expected",
    )
    assert result["summary"]["n_passed"] == 10


# ---------------------------------------------------------------------------
# eval_gate
# ---------------------------------------------------------------------------

def test_eval_gate_passes_when_metric_exceeds_threshold(store_ctx) -> None:
    ev = _eval_result(accuracy=0.91)
    result = eval_gate(input=ev, metric="accuracy", operator=">=", threshold=0.80)
    assert "pass" in result
    assert result["pass"]["passed"] is True


def test_eval_gate_fails_when_metric_below_threshold(store_ctx) -> None:
    ev = _eval_result(accuracy=0.65)
    result = eval_gate(input=ev, metric="accuracy", operator=">=", threshold=0.80, on_fail="branch")
    assert "fail" in result
    assert result["fail"]["passed"] is False


def test_eval_gate_raises_error_on_fail_when_configured(store_ctx) -> None:
    ev = _eval_result(accuracy=0.65)
    with pytest.raises(RuntimeError, match="failed|gate"):
        eval_gate(input=ev, metric="accuracy", operator=">=", threshold=0.80, on_fail="error")


def test_eval_gate_accepts_plain_dict(store_ctx) -> None:
    metrics = {"win_rate": 0.72, "n_rows": 50}
    result = eval_gate(input=metrics, metric="win_rate", operator=">", threshold=0.70)
    assert "pass" in result


def test_eval_gate_rejects_missing_metric(store_ctx) -> None:
    ev = _eval_result(accuracy=0.9)
    with pytest.raises(ValueError, match="not found"):
        eval_gate(input=ev, metric="nonexistent_metric", threshold=0.5)


@pytest.mark.parametrize("op,val,thresh,should_pass", [
    (">=", 0.80, 0.80, True),
    (">", 0.80, 0.80, False),
    ("<=", 0.70, 0.80, True),
    ("<", 0.70, 0.80, True),
    ("==", 0.75, 0.75, True),
    ("!=", 0.75, 0.80, True),
])
def test_eval_gate_all_operators(store_ctx, op, val, thresh, should_pass) -> None:
    metrics = {"score": val}
    result = eval_gate(
        input=metrics,
        metric="score",
        operator=op,
        threshold=thresh,
        on_fail="branch",
    )
    if should_pass:
        assert "pass" in result
    else:
        assert "fail" in result


# ---------------------------------------------------------------------------
# eval_report
# ---------------------------------------------------------------------------

def test_eval_report_produces_markdown_artifact(store_ctx) -> None:
    ev = _eval_result(accuracy=0.87, n_rows=50)
    result = eval_report(
        input=ev,
        title="My Test Report",
        include_sample_rows=False,
    )
    assert is_artifact_ref(result["report"])
    assert result["title"] == "My Test Report"

    from noodle.artifacts import read_text
    md = read_text(result["report"])
    assert "# My Test Report" in md
    assert "accuracy" in md
    assert "0.87" in md


def test_eval_report_includes_sample_rows(store_ctx) -> None:
    rows = _rule_eval_records(correct_fraction=0.8, n=20)
    eval_input = llm_rule_eval(
        input=rows,
        mode="exact_match",
        output_column="output",
        expected_column="expected",
    )
    result = eval_report(
        input=eval_input,
        title="Rule Eval Report",
        include_sample_rows=True,
        sample_rows=5,
    )

    from noodle.artifacts import read_text
    md = read_text(result["report"])
    assert "Sample Rows" in md


def test_eval_report_rejects_non_eval_input(store_ctx) -> None:
    with pytest.raises(ValueError, match="EvalResultRef"):
        eval_report(input={"some": "dict"})


# ---------------------------------------------------------------------------
# llm_compare_models (mocked)
# ---------------------------------------------------------------------------

def _fake_openai_for_compare(
    baseline_answer: str = "Baseline",
    candidate_answer: str = "Candidate",
):
    client = MagicMock()

    def _create(**kwargs):
        model = kwargs.get("model", "")
        content = baseline_answer if "gpt-4.1-mini" == model else candidate_answer
        choice = MagicMock()
        choice.message.content = content
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    client.chat.completions.create.side_effect = _create

    openai_mod = MagicMock()
    openai_mod.OpenAI.return_value = client
    return openai_mod


def test_compare_models_returns_eval_result(store_ctx) -> None:
    rows = _eval_records(5)
    fake_openai = _fake_openai_for_compare("Base answer", "Candidate answer")

    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = llm_compare_models(
            input=rows,
            openai_api_key="sk-test",
            baseline_model="gpt-4.1-mini",
            candidate_model="ft:gpt-4.1-mini:test::xyz",
            prompt_column="prompt",
            expected_column="expected",
            max_rows=5,
        )

    assert result[_EVAL_RESULT_MARKER] is True
    assert result["kind"] == "model_comparison"
    assert is_dataset_ref(result["rows"])
    assert result["summary"]["n_rows"] == 5


def test_compare_models_rows_contain_both_outputs(store_ctx) -> None:
    rows = _eval_records(3)
    fake_openai = _fake_openai_for_compare("B", "C")

    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = llm_compare_models(
            input=rows,
            openai_api_key="sk-test",
            baseline_model="gpt-4.1-mini",
            candidate_model="ft:gpt-4.1-mini:test::xyz",
            max_rows=3,
        )

    output_rows = dataset_to_records(result["rows"])
    for row in output_rows:
        assert "baseline_output" in row
        assert "candidate_output" in row


def test_compare_models_requires_candidate_model(store_ctx) -> None:
    rows = _eval_records(5)
    fake_openai = _fake_openai_for_compare()
    with patch.dict(sys.modules, {"openai": fake_openai}):
        with pytest.raises(ValueError, match="candidate_model"):
            llm_compare_models(
                input=rows,
                openai_api_key="sk-test",
                baseline_model="gpt-4.1-mini",
                candidate_model="",
            )


def test_compare_models_accepts_eval_dataset_output(store_ctx) -> None:
    rows = _eval_records(5)
    eval_ds = llm_eval_dataset(
        input=rows, prompt_column="prompt", expected_column="expected"
    )
    fake_openai = _fake_openai_for_compare()

    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = llm_compare_models(
            input=eval_ds,
            openai_api_key="sk-test",
            baseline_model="gpt-4.1-mini",
            candidate_model="ft:gpt-4.1-mini:x::001",
            max_rows=5,
        )

    assert result[_EVAL_RESULT_MARKER] is True


def test_compare_models_rejects_excessive_concurrency(store_ctx) -> None:
    fake_openai = _fake_openai_for_compare()

    with patch.dict(sys.modules, {"openai": fake_openai}):
        with pytest.raises(ValueError, match="concurrency"):
            llm_compare_models(
                input=_eval_records(2),
                openai_api_key="sk-test",
                baseline_model="gpt-4.1-mini",
                candidate_model="ft:gpt-4.1-mini:x::001",
                concurrency=21,
            )


# ---------------------------------------------------------------------------
# llm_judge (mocked)
# ---------------------------------------------------------------------------

def _fake_openai_for_judge(score: int = 4, reason: str = "Good answer"):
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = json.dumps({"score": score, "reason": reason})
    resp = MagicMock()
    resp.choices = [choice]
    client.chat.completions.create.return_value = resp

    openai_mod = MagicMock()
    openai_mod.OpenAI.return_value = client
    return openai_mod


def test_judge_produces_eval_result(store_ctx) -> None:
    rows = [
        {"prompt": "Q1", "expected": "A1", "candidate_output": "good answer"},
        {"prompt": "Q2", "expected": "A2", "candidate_output": "bad answer"},
    ]
    fake_openai = _fake_openai_for_judge(score=4)

    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = llm_judge(
            input=rows,
            openai_api_key="sk-test",
            judge_model="gpt-4o",
            output_column="candidate_output",
            prompt_column="prompt",
            expected_column="expected",
            score_scale=5,
        )

    assert result[_EVAL_RESULT_MARKER] is True
    assert result["kind"] == "llm_judge"
    assert is_dataset_ref(result["rows"])
    assert result["summary"]["n_scored"] == 2


def test_judge_computes_summary_scores(store_ctx) -> None:
    rows = [{"prompt": f"Q{i}", "expected": f"A{i}", "candidate_output": f"O{i}"} for i in range(5)]
    fake_openai = _fake_openai_for_judge(score=3)  # 3/5 = 0.6

    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = llm_judge(
            input=rows,
            openai_api_key="sk-test",
            output_column="candidate_output",
            score_scale=5,
        )

    summary = result["summary"]
    assert summary["avg_score"] == pytest.approx(3.0)
    assert "win_rate" in summary


def test_judge_accepts_compare_result(store_ctx) -> None:
    rows = [
        {
            "prompt": f"Q{i}",
            "expected": f"A{i}",
            "baseline_output": f"B{i}",
            "candidate_output": f"C{i}",
        }
        for i in range(3)
    ]
    ds = records_to_dataset(rows)
    compare_result = {
        _EVAL_RESULT_MARKER: True,
        "version": 1,
        "kind": "model_comparison",
        "summary": {"n_rows": 3},
        "rows": ds,
    }
    fake_openai = _fake_openai_for_judge(score=5)

    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = llm_judge(
            input=compare_result,
            openai_api_key="sk-test",
            output_column="candidate_output",
        )

    assert result[_EVAL_RESULT_MARKER] is True


def test_judge_rejects_uncapped_large_dataset(store_ctx) -> None:
    with pytest.raises(ValueError, match="row count"):
        llm_judge(
            input=[
                {"prompt": f"Q{i}", "candidate_output": f"A{i}"}
                for i in range(1_001)
            ],
            openai_api_key="sk-test",
            output_column="candidate_output",
            max_rows=0,
        )
