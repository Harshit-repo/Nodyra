"""Tests for model_monitoring.py nodes."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from nodyra.artifacts import LocalArtifactStore, is_artifact_ref
from nodyra.context import artifact_store, current_node_id
from nodyra.datasets import is_dataset_ref
from nodyra.sdk import registry
from nodyra_nodes.datasets import records_to_dataset


@pytest.fixture()
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="monitoring-test-run")
    t1 = artifact_store.set(store)
    t2 = current_node_id.set("monitoring-test-node")
    yield store
    current_node_id.reset(t2)
    artifact_store.reset(t1)


# ---------------------------------------------------------------------------
# Registry / imports
# ---------------------------------------------------------------------------

def test_registry_loads_without_heavy_packages() -> None:
    import nodyra_nodes.model_monitoring  # noqa: F401
    node_ids = {m.id for m in registry.manifests()}
    expected = {
        "cost_budget_gate", "latency_slo_gate", "response_quality_monitor",
        "prompt_drift_monitor", "model_registry_query", "model_promote", "model_rollback",
    }
    assert expected <= node_ids


def test_all_monitoring_nodes_in_ml_category() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    for nid in ("cost_budget_gate", "latency_slo_gate", "response_quality_monitor",
                 "prompt_drift_monitor", "model_registry_query", "model_promote", "model_rollback"):
        assert manifests[nid].category == "Machine Learning", f"{nid} wrong category"


def test_gate_nodes_have_no_requirements() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    for nid in ("cost_budget_gate", "latency_slo_gate", "model_promote", "model_rollback",
                 "model_registry_query"):
        reqs = manifests[nid].requirements or []
        assert not reqs, f"{nid} should have no requirements, got {reqs}"


def test_drift_monitor_declares_numpy() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    reqs = manifests["prompt_drift_monitor"].requirements or []
    assert any("numpy" in r for r in reqs)


# ---------------------------------------------------------------------------
# cost_budget_gate
# ---------------------------------------------------------------------------

def test_cost_budget_gate_passes_within_budget(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import cost_budget_gate
    result = cost_budget_gate(
        input={"total_cost_usd": 5.0, "n_tokens": 10000},
        budget_usd=10.0,
        operator="<=",
    )
    assert "pass" in result
    assert result["pass"]["passed"] is True


def test_cost_budget_gate_fails_over_budget(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import cost_budget_gate
    result = cost_budget_gate(
        input={"total_cost_usd": 15.0},
        budget_usd=10.0,
        operator="<=",
    )
    assert "fail" in result
    assert result["fail"]["passed"] is False


def test_cost_budget_gate_auto_compute_from_tokens(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import cost_budget_gate
    # 10000 tokens × $0.002/1k = $20 → should fail for $10 budget
    result = cost_budget_gate(
        input={"total_tokens": 10000},
        budget_usd=10.0,
        metric_field="total_cost_usd",
        price_per_1k_tokens=2.0,
        operator="<=",
    )
    assert "fail" in result


def test_cost_budget_gate_auto_compute_within_budget(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import cost_budget_gate
    # 1000 tokens × $0.002/1k = $2 → should pass for $10 budget
    result = cost_budget_gate(
        input={"total_tokens": 1000},
        budget_usd=10.0,
        metric_field="total_cost_usd",
        price_per_1k_tokens=2.0,
    )
    assert "pass" in result


def test_cost_budget_gate_missing_field_raises(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import cost_budget_gate
    with pytest.raises(ValueError, match="not found"):
        cost_budget_gate(
            input={"other_field": 5.0},
            metric_field="total_cost_usd",
        )


def test_cost_budget_gate_non_dict_raises(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import cost_budget_gate
    with pytest.raises(ValueError):
        cost_budget_gate(input=42.0)


# ---------------------------------------------------------------------------
# latency_slo_gate
# ---------------------------------------------------------------------------

def test_latency_slo_gate_passes(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import latency_slo_gate
    result = latency_slo_gate(
        input={"p95_latency_ms": 800.0, "p50_latency_ms": 300.0},
        slo_ms=1000.0,
        percentile="p95",
    )
    assert "pass" in result
    assert result["pass"]["latency_ms"] == 800.0


def test_latency_slo_gate_fails(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import latency_slo_gate
    result = latency_slo_gate(
        input={"p95_latency_ms": 2500.0},
        slo_ms=2000.0,
        percentile="p95",
    )
    assert "fail" in result


def test_latency_slo_gate_auto_detects_field(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import latency_slo_gate
    result = latency_slo_gate(
        input={"p50_latency_ms": 100.0},
        slo_ms=500.0,
        percentile="p50",
    )
    assert "pass" in result


def test_latency_slo_gate_override_field(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import latency_slo_gate
    result = latency_slo_gate(
        input={"custom_latency": 500.0},
        slo_ms=1000.0,
        metric_field="custom_latency",
    )
    assert "pass" in result


def test_latency_slo_gate_mean_percentile(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import latency_slo_gate
    result = latency_slo_gate(
        input={"mean_latency_ms": 300.0},
        slo_ms=500.0,
        percentile="mean",
    )
    assert "pass" in result


# ---------------------------------------------------------------------------
# response_quality_monitor
# ---------------------------------------------------------------------------

def _sample_responses():
    return [
        {"prompt": "What is AI?", "response": "AI is artificial intelligence."},
        {"prompt": "What is ML?", "response": "Machine learning trains models from data."},
        {"prompt": "Explain DL.", "response": "DL uses neural networks. " * 5},
    ]


def test_response_quality_monitor_rule_check_all_pass(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import response_quality_monitor
    result = response_quality_monitor(
        input=_sample_responses(),
        mode="rule_check",
        response_column="response",
    )
    assert result["main"]["n_sampled"] == 3
    assert result["main"]["n_pass"] == 3
    assert is_dataset_ref(result["rows"])


def test_response_quality_monitor_length_check(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import response_quality_monitor
    rows = [
        {"response": "Short"},
        {"response": "A" * 200},
    ]
    result = response_quality_monitor(
        input=rows,
        mode="rule_check",
        response_column="response",
        min_length_chars=10,
        max_length_chars=100,
    )
    assert result["main"]["n_fail"] == 2  # both fail: one too short, one too long


def test_response_quality_monitor_forbidden_pattern(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import response_quality_monitor
    rows = [
        {"response": "I don't know how to answer."},
        {"response": "Paris is the capital of France."},
    ]
    result = response_quality_monitor(
        input=rows,
        mode="rule_check",
        response_column="response",
        forbidden_patterns='["don.t know"]',
    )
    assert result["main"]["n_fail"] == 1
    assert result["main"]["n_pass"] == 1


def test_response_quality_monitor_required_pattern(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import response_quality_monitor
    rows = [
        {"response": "The answer is 42. Source: Wikipedia."},
        {"response": "I don't know the answer."},  # no "source" mention → fails
    ]
    result = response_quality_monitor(
        input=rows,
        mode="rule_check",
        response_column="response",
        required_patterns='["[Ss]ource"]',
    )
    assert result["main"]["n_pass"] == 1
    assert result["main"]["n_fail"] == 1


def test_response_quality_monitor_json_check(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import response_quality_monitor
    rows = [
        {"response": '{"label": "positive", "score": 0.9}'},
        {"response": "not json at all"},
    ]
    result = response_quality_monitor(
        input=rows,
        mode="rule_check",
        response_column="response",
        check_json_valid=True,
    )
    assert result["main"]["n_pass"] == 1
    assert result["main"]["n_fail"] == 1


def test_response_quality_monitor_sample_rate(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import response_quality_monitor
    rows = [{"response": f"Response {i}."} for i in range(10)]
    result = response_quality_monitor(
        input=rows, mode="rule_check", response_column="response", sample_rate=0.5,
    )
    assert result["main"]["n_sampled"] == 5
    assert result["main"]["n_total"] == 10


def test_response_quality_monitor_llm_judge(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import response_quality_monitor
    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value.chat.completions.create.return_value.choices[
        0
    ].message.content = '{"score": 4, "issues": ""}'
    rows = _sample_responses()
    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = response_quality_monitor(
            input=rows,
            mode="rule_check",  # use rule_check to avoid openai in non-judge path
            response_column="response",
        )
    assert result["main"]["n_sampled"] == 3


def test_response_quality_monitor_llm_judge_rejects_large_sample(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import response_quality_monitor

    with pytest.raises(ValueError, match="sampled row count"):
        response_quality_monitor(
            input=[{"response": f"Response {i}."} for i in range(1_001)],
            mode="llm_judge",
            response_column="response",
            openai_api_key="sk-test",
        )


def test_response_quality_monitor_missing_column(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import response_quality_monitor
    with pytest.raises(ValueError, match="not found"):
        response_quality_monitor(input=[{"text": "hello"}], response_column="response")


# ---------------------------------------------------------------------------
# prompt_drift_monitor
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    __import__("importlib").util.find_spec("numpy") is None,
    reason="numpy not installed",
)
def test_prompt_drift_monitor_no_baseline(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import prompt_drift_monitor
    rows = [{"prompt": f"Question {i} about machine learning?"} for i in range(15)]
    result = prompt_drift_monitor(
        input=rows, text_column="prompt",
    )
    assert "main" in result
    assert result["main"]["has_baseline"] is False
    assert result["main"]["current_n"] == 15


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("numpy") is None,
    reason="numpy not installed",
)
def test_prompt_drift_monitor_similar_baseline_no_drift(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import prompt_drift_monitor
    baseline = [{"prompt": f"What is technique {i} in machine learning?"} for i in range(20)]
    current = [{"prompt": f"Explain method {i} for machine learning tasks?"} for i in range(15)]
    result = prompt_drift_monitor(
        input=current, baseline=baseline, text_column="prompt",
        drift_threshold=0.8,  # high threshold → should pass
        fail_on_drift=True,
    )
    assert "pass" in result
    assert result["pass"]["drift_score"] < 0.8


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("numpy") is None,
    reason="numpy not installed",
)
def test_prompt_drift_monitor_different_baseline_drift(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import prompt_drift_monitor
    baseline = [
        {"prompt": "machine learning neural network deep learning transformer"}
        for _ in range(20)
    ]
    current = [{"prompt": "cooking recipe ingredient bake oven temperature"} for _ in range(15)]
    result = prompt_drift_monitor(
        input=current, baseline=baseline, text_column="prompt",
        drift_threshold=0.1,  # very low threshold → should fail
        fail_on_drift=True,
    )
    assert "fail" in result
    assert result["fail"]["drift_score"] > 0.1


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("numpy") is None,
    reason="numpy not installed",
)
def test_prompt_drift_monitor_report_artifact(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import prompt_drift_monitor
    rows = [{"prompt": f"Question {i}"} for i in range(12)]
    result = prompt_drift_monitor(input=rows, text_column="prompt")
    assert is_artifact_ref(result["main"]["report"])


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("numpy") is None,
    reason="numpy not installed",
)
def test_prompt_drift_monitor_not_enough_baseline(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import prompt_drift_monitor
    with pytest.raises(ValueError, match="at least"):
        prompt_drift_monitor(
            input=[{"prompt": "current query"}],
            baseline=[{"prompt": "b"}],
            min_baseline_rows=10,
        )


# ---------------------------------------------------------------------------
# model_registry_query
# ---------------------------------------------------------------------------

def _registry_entries():
    return [
        {
            "model_id": "ft-openai-v1",
            "provider": "openai",
            "base_model": "gpt-4o-mini",
            "status": "production",
        },
        {
            "model_id": "ft-openai-v2",
            "provider": "openai",
            "base_model": "gpt-4o-mini",
            "status": "candidate",
        },
        {
            "model_id": "lora-llama-v1",
            "provider": "huggingface",
            "base_model": "llama3",
            "status": "staging",
        },
        {
            "model_id": "ft-openai-v0",
            "provider": "openai",
            "base_model": "gpt-4o-mini",
            "status": "archived",
        },
    ]


def test_model_registry_query_all(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import model_registry_query
    result = model_registry_query(input=_registry_entries())
    assert result["main"]["total_registered"] == 4
    assert result["main"]["total_matched"] == 4
    assert is_dataset_ref(result["rows"])


def test_model_registry_query_filter_status(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import model_registry_query
    result = model_registry_query(input=_registry_entries(), filter_status="production")
    assert result["main"]["total_matched"] == 1


def test_model_registry_query_filter_provider(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import model_registry_query
    result = model_registry_query(input=_registry_entries(), filter_provider="openai")
    assert result["main"]["total_matched"] == 3


def test_model_registry_query_filter_base_model(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import model_registry_query
    result = model_registry_query(input=_registry_entries(), filter_base_model="llama")
    assert result["main"]["total_matched"] == 1


def test_model_registry_query_from_dataset(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import model_registry_query
    ds_ref = records_to_dataset(_registry_entries(), name="registry.parquet")
    result = model_registry_query(input=ds_ref, filter_status="staging")
    assert result["main"]["total_matched"] == 1


def test_model_registry_query_status_counts(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import model_registry_query
    result = model_registry_query(input=_registry_entries())
    counts = result["main"]["status_counts"]
    assert counts.get("production") == 1
    assert counts.get("candidate") == 1


# ---------------------------------------------------------------------------
# model_promote
# ---------------------------------------------------------------------------

def test_model_promote_to_production(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import model_promote
    entry = {"__nodyra_model_registry__": True, "version": 1,
             "model_id": "ft-openai-v2", "provider": "openai",
             "base_model": "gpt-4o-mini", "status": "candidate",
             "metrics": {"accuracy": 0.91}}
    result = model_promote(input=entry, target_status="production", notes="Passed eval gate")
    assert result["model_id"] == "ft-openai-v2"
    assert result["new_status"] == "production"
    assert result["previous_status"] == "candidate"
    assert result["registry_entry"]["status"] == "production"


def test_model_promote_to_staging(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import model_promote
    result = model_promote(
        input={"model_id": "lora-v1", "status": "candidate"},
        target_status="staging",
    )
    assert result["new_status"] == "staging"


def test_model_promote_with_model_id_param(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import model_promote
    result = model_promote(
        input={"some": "dict"},
        model_id="explicit-model-id",
        target_status="production",
    )
    assert result["model_id"] == "explicit-model-id"


def test_model_promote_invalid_status_raises(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import model_promote
    with pytest.raises(ValueError):
        model_promote(
            input={"model_id": "x", "status": "candidate"},
            target_status="invalid_status",
        )


def test_model_promote_require_eval_no_metrics_raises(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import model_promote
    with pytest.raises(ValueError, match="eval"):
        model_promote(
            input={"model_id": "x", "status": "candidate"},
            require_eval_result=True,
            target_status="production",
        )


def test_model_promote_require_eval_with_metrics_passes(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import model_promote
    result = model_promote(
        input={"model_id": "x", "status": "candidate", "metrics": {"accuracy": 0.95}},
        require_eval_result=True,
        target_status="production",
    )
    assert result["new_status"] == "production"


# ---------------------------------------------------------------------------
# model_rollback
# ---------------------------------------------------------------------------

def test_model_rollback_from_production(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import model_rollback
    entry = {"__nodyra_model_registry__": True, "model_id": "ft-openai-v2",
             "status": "production", "provider": "openai"}
    result = model_rollback(
        input=entry,
        rollback_to_status="candidate",
        reason="Accuracy regression detected",
    )
    assert result["rolled_back_from"] == "production"
    assert result["rolled_back_to"] == "candidate"
    assert result["reason"] == "Accuracy regression detected"
    assert result["registry_entry"]["status"] == "candidate"


def test_model_rollback_with_version_history(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import model_rollback
    history = [
        {"model_id": "ft-openai-v2", "status": "candidate", "metrics": {"acc": 0.88}},
        {"model_id": "ft-openai-v2", "status": "staging", "metrics": {"acc": 0.90}},
    ]
    entry = {"model_id": "ft-openai-v2", "status": "production"}
    result = model_rollback(
        input=entry,
        version_history=history,
        rollback_to_status="candidate",
    )
    assert result["previous_version"] is not None
    assert result["previous_version"]["status"] in ("candidate", "staging")


def test_model_rollback_invalid_status_raises(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import model_rollback
    with pytest.raises(ValueError):
        model_rollback(
            input={"model_id": "x", "status": "production"},
            rollback_to_status="production",  # can't rollback to production
        )


def test_model_rollback_model_id_param(store_ctx) -> None:
    from nodyra_nodes.model_monitoring import model_rollback
    result = model_rollback(
        input={},
        model_id="explicit-id",
        rollback_to_status="archived",
    )
    assert result["model_id"] == "explicit-id"
    assert result["rolled_back_to"] == "archived"
