"""Tests for model_serving.py nodes."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from noodle.artifacts import LocalArtifactStore, is_artifact_ref
from noodle.context import artifact_store, current_node_id
from noodle.datasets import is_dataset_ref
from noodle.sdk import registry
from noodle_nodes.datasets import records_to_dataset


@pytest.fixture()
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="serving-test-run")
    t1 = artifact_store.set(store)
    t2 = current_node_id.set("serving-test-node")
    yield store
    current_node_id.reset(t2)
    artifact_store.reset(t1)


def _make_fake_requests(models_ok=True, completion_ok=True, models_list=None, content="Hello!"):
    fake = MagicMock()
    models_resp = MagicMock()
    models_resp.status_code = 200 if models_ok else 503
    models_resp.json.return_value = {
        "data": [{"id": m} for m in (models_list or ["llama3.2"])]
    }
    models_resp.text = "error" if not models_ok else ""

    completion_resp = MagicMock()
    completion_resp.status_code = 200 if completion_ok else 503
    completion_resp.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
    }
    if not completion_ok:
        completion_resp.raise_for_status.side_effect = Exception("503 Service Unavailable")

    fake.get.return_value = models_resp
    fake.post.return_value = completion_resp
    return fake


# ---------------------------------------------------------------------------
# Registry / imports
# ---------------------------------------------------------------------------

def test_registry_loads_without_requests() -> None:
    import noodle_nodes.model_serving  # noqa: F401
    node_ids = {m.id for m in registry.manifests()}
    assert "model_deployment_spec" in node_ids
    assert "model_endpoint_probe" in node_ids
    assert "model_endpoint_benchmark" in node_ids
    assert "shadow_compare_endpoint" in node_ids


def test_all_serving_nodes_in_ml_category() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    for nid in ("model_deployment_spec", "model_endpoint_probe",
                 "model_endpoint_benchmark", "shadow_compare_endpoint"):
        assert manifests[nid].category == "Machine Learning", f"{nid} wrong category"


def test_no_requests_import_at_module_level() -> None:
    import noodle_nodes.model_serving  # noqa: F401 — importing should succeed without requests


def test_probe_benchmark_shadow_declare_requests_requirement() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    for nid in ("model_endpoint_probe", "model_endpoint_benchmark", "shadow_compare_endpoint"):
        reqs = manifests[nid].requirements or []
        assert any("requests" in r for r in reqs), f"{nid} should declare requests requirement"


def test_deployment_spec_no_requirements() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    spec = manifests["model_deployment_spec"]
    assert not spec.requirements or spec.requirements == []


# ---------------------------------------------------------------------------
# model_deployment_spec
# ---------------------------------------------------------------------------

def test_deployment_spec_ollama_compose(store_ctx) -> None:
    from noodle_nodes.model_serving import model_deployment_spec
    result = model_deployment_spec(
        runtime="ollama", model="llama3.2", port=11434,
        format="docker-compose",
    )
    assert result["runtime"] == "ollama"
    assert result["model"] == "llama3.2"
    assert result["port"] == 11434
    assert "http://localhost:11434/v1" == result["base_url"]
    assert is_artifact_ref(result["spec_artifact"])


def test_deployment_spec_vllm_shell(store_ctx) -> None:
    from noodle_nodes.model_serving import model_deployment_spec
    result = model_deployment_spec(
        runtime="vllm", model="mistralai/Mistral-7B-v0.1",
        format="shell-commands", port=8000,
    )
    assert result["runtime"] == "vllm"
    assert "8000" in result["base_url"]
    assert is_artifact_ref(result["spec_artifact"])


def test_deployment_spec_kubernetes(store_ctx) -> None:
    from noodle_nodes.model_serving import model_deployment_spec
    result = model_deployment_spec(
        runtime="ollama", model="phi3", format="kubernetes",
    )
    assert result["format"] == "kubernetes"
    assert is_artifact_ref(result["spec_artifact"])


def test_deployment_spec_gpu_flag(store_ctx) -> None:
    from noodle_nodes.model_serving import model_deployment_spec
    result = model_deployment_spec(
        runtime="ollama", model="llama3.2", gpu=True, format="docker-compose",
    )
    assert is_artifact_ref(result["spec_artifact"])


def test_deployment_spec_extra_env(store_ctx) -> None:
    from noodle_nodes.model_serving import model_deployment_spec
    result = model_deployment_spec(
        runtime="ollama", model="llama3.2",
        extra_env='{"MY_ENV_VAR": "hello"}',
    )
    assert is_artifact_ref(result["spec_artifact"])


# ---------------------------------------------------------------------------
# model_endpoint_probe — mocked _require_requests
# ---------------------------------------------------------------------------

def test_endpoint_probe_healthy(store_ctx) -> None:
    from noodle_nodes.model_serving import model_endpoint_probe
    fake_req = _make_fake_requests()
    with patch("noodle_nodes.model_serving._require_requests", return_value=fake_req):
        result = model_endpoint_probe(
            base_url="http://localhost:11434",
            model="llama3.2",
            check_models_list=True,
            check_completion=True,
        )
    assert "pass" in result or "main" in result
    summary = result.get("pass", result.get("main", {}))
    assert summary.get("is_healthy") is True


def test_endpoint_probe_unhealthy_routes_to_fail(store_ctx) -> None:
    from noodle_nodes.model_serving import model_endpoint_probe
    fake_req = _make_fake_requests(models_ok=False)
    with patch("noodle_nodes.model_serving._require_requests", return_value=fake_req):
        result = model_endpoint_probe(
            base_url="http://localhost:11434",
            model="llama3.2",
            check_models_list=True,
            check_completion=False,
            fail_if_unhealthy=True,
        )
    assert "fail" in result


def test_endpoint_probe_skip_completion(store_ctx) -> None:
    from noodle_nodes.model_serving import model_endpoint_probe
    fake_req = _make_fake_requests()
    with patch("noodle_nodes.model_serving._require_requests", return_value=fake_req):
        result = model_endpoint_probe(
            base_url="http://localhost:11434",
            model="llama3.2",
            check_models_list=True,
            check_completion=False,
        )
    summary = result.get("pass", result.get("main", {}))
    assert summary is not None


def test_endpoint_probe_auto_selects_model(store_ctx) -> None:
    from noodle_nodes.model_serving import model_endpoint_probe
    fake_req = _make_fake_requests(models_list=["phi3", "llama3.2"])
    with patch("noodle_nodes.model_serving._require_requests", return_value=fake_req):
        result = model_endpoint_probe(
            base_url="http://localhost:11434",
            model="",
            check_models_list=True,
            check_completion=True,
        )
    summary = result.get("pass", result.get("main", {}))
    assert summary.get("model") == "phi3"


def test_endpoint_probe_missing_requests_raises() -> None:
    from noodle_nodes.model_serving import model_endpoint_probe
    with patch("noodle_nodes.model_serving._require_requests",
               side_effect=RuntimeError("requests not installed")):
        with pytest.raises(RuntimeError):
            model_endpoint_probe(base_url="http://localhost:11434", model="test")


# ---------------------------------------------------------------------------
# model_endpoint_benchmark — mocked _require_requests
# ---------------------------------------------------------------------------

def test_endpoint_benchmark_basic(store_ctx) -> None:
    from noodle_nodes.model_serving import model_endpoint_benchmark
    fake_req = _make_fake_requests()
    with patch("noodle_nodes.model_serving._require_requests", return_value=fake_req):
        result = model_endpoint_benchmark(
            base_url="http://localhost:11434",
            model="llama3.2",
            prompts='["Hello", "World"]',
            n_requests=4,
            concurrency=2,
        )
    assert result["main"]["n_requests"] == 4
    assert result["main"]["n_success"] == 4
    assert result["main"]["error_rate"] == 0.0
    assert is_dataset_ref(result["rows"])


def test_endpoint_benchmark_partial_failure(store_ctx) -> None:
    from noodle_nodes.model_serving import model_endpoint_benchmark
    call_count = [0]

    def fake_post(url, **kwargs):
        call_count[0] += 1
        resp = MagicMock()
        if call_count[0] % 2 == 0:
            resp.raise_for_status.side_effect = Exception("timeout")
            resp.status_code = 504
        else:
            resp.status_code = 200
            resp.json.return_value = {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
            }
        return resp

    fake_req = MagicMock()
    fake_req.post.side_effect = fake_post

    with patch("noodle_nodes.model_serving._require_requests", return_value=fake_req):
        result = model_endpoint_benchmark(
            base_url="http://localhost:11434",
            model="llama3.2",
            prompts='["Hello"]',
            n_requests=4,
            concurrency=1,
        )
    assert result["main"]["n_errors"] > 0
    assert result["main"]["error_rate"] > 0


def test_endpoint_benchmark_stats_structure(store_ctx) -> None:
    from noodle_nodes.model_serving import model_endpoint_benchmark
    fake_req = _make_fake_requests()
    with patch("noodle_nodes.model_serving._require_requests", return_value=fake_req):
        result = model_endpoint_benchmark(
            base_url="http://localhost:11434",
            model="llama3.2",
            prompts='["Hello"]',
            n_requests=5,
        )
    stats = result["main"]
    for key in ("p50_latency_ms", "p95_latency_ms", "mean_latency_ms", "tokens_per_second"):
        assert key in stats, f"Missing stat: {key}"


def test_endpoint_benchmark_rejects_unbounded_requests(store_ctx) -> None:
    from noodle_nodes.model_serving import MAX_ENDPOINT_REQUESTS, model_endpoint_benchmark

    with pytest.raises(ValueError, match="n_requests"):
        model_endpoint_benchmark(n_requests=MAX_ENDPOINT_REQUESTS + 1)


def test_endpoint_benchmark_rejects_unbounded_concurrency(store_ctx) -> None:
    from noodle_nodes.model_serving import (
        MAX_ENDPOINT_CONCURRENCY,
        model_endpoint_benchmark,
    )

    with pytest.raises(ValueError, match="concurrency"):
        model_endpoint_benchmark(concurrency=MAX_ENDPOINT_CONCURRENCY + 1)


# ---------------------------------------------------------------------------
# shadow_compare_endpoint — mocked _require_requests
# ---------------------------------------------------------------------------

def test_shadow_compare_basic(store_ctx) -> None:
    from noodle_nodes.model_serving import shadow_compare_endpoint
    fake_req = _make_fake_requests()
    with patch("noodle_nodes.model_serving._require_requests", return_value=fake_req):
        result = shadow_compare_endpoint(
            primary_url="http://localhost:11434",
            primary_model="llama3.2",
            shadow_url="http://localhost:11435",
            shadow_model="llama3.1",
            prompts='["What is AI?", "Explain ML."]',
            concurrency=2,
        )
    assert result["main"]["n_prompts"] == 2
    assert is_dataset_ref(result["rows"])


def test_shadow_compare_from_dataset(store_ctx) -> None:
    from noodle_nodes.model_serving import shadow_compare_endpoint
    fake_req = _make_fake_requests()
    rows = [{"prompt": "Explain embeddings."}, {"prompt": "What is BERT?"}]
    ds_ref = records_to_dataset(rows, name="prompts.parquet")
    with patch("noodle_nodes.model_serving._require_requests", return_value=fake_req):
        result = shadow_compare_endpoint(
            primary_url="http://localhost:11434",
            primary_model="llama3.2",
            shadow_url="http://localhost:11435",
            shadow_model="llama3.1",
            input=ds_ref,
            prompt_column="prompt",
        )
    assert result["main"]["n_prompts"] == 2


def test_shadow_compare_summary_fields(store_ctx) -> None:
    from noodle_nodes.model_serving import shadow_compare_endpoint
    fake_req = _make_fake_requests()
    with patch("noodle_nodes.model_serving._require_requests", return_value=fake_req):
        result = shadow_compare_endpoint(
            primary_url="http://localhost:11434",
            primary_model="llama3.2",
            shadow_url="http://localhost:11435",
            shadow_model="llama3.1",
            prompts='["Hello"]',
        )
    summary = result["main"]
    for key in ("primary_success_rate", "shadow_success_rate", "primary_p50_ms", "shadow_p50_ms"):
        assert key in summary, f"Missing summary key: {key}"


def test_shadow_compare_rejects_unbounded_prompt_list(store_ctx) -> None:
    from noodle_nodes.model_serving import MAX_SHADOW_PROMPTS, shadow_compare_endpoint

    prompts = json.dumps(["hello"] * (MAX_SHADOW_PROMPTS + 1))
    with pytest.raises(ValueError, match="prompts"):
        shadow_compare_endpoint(prompts=prompts)
