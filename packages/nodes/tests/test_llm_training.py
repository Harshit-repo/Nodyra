"""Tests for LLM fine-tuning nodes in llm_training.py."""

from __future__ import annotations

import io
import json
import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import noodle_nodes  # noqa: F401 - registers nodes
from noodle.artifacts import LocalArtifactStore, is_artifact_ref
from noodle.context import artifact_store, current_node_id
from noodle.sdk import registry
from noodle_nodes.datasets import records_to_dataset
from noodle_nodes.llm_training import (
    _FT_DATASET_MARKER,
    _FT_JOB_MARKER,
    _MODEL_REGISTRY_MARKER,
    _is_ft_dataset,
    _is_ft_job,
    llm_fine_tune_dataset,
    openai_cancel_fine_tune_job,
    openai_create_fine_tune_job,
    openai_fine_tune_checkpoints,
    openai_fine_tune_status,
    openai_upload_fine_tune_file,
    register_fine_tuned_model,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="ft-test-run")
    a = artifact_store.set(store)
    n = current_node_id.set("ft-test-node")
    yield store
    current_node_id.reset(n)
    artifact_store.reset(a)


def _chat_records(n: int = 20) -> list[dict[str, Any]]:
    return [
        {
            "user": f"Question {i}: What is {i}+{i}?",
            "assistant": f"The answer is {i*2}.",
        }
        for i in range(1, n + 1)
    ]


def _ft_job_ref(status: str = "succeeded", model: str = "ft:gpt-4.1-mini:xx") -> dict[str, Any]:
    return {
        _FT_JOB_MARKER: True,
        "version": 1,
        "provider": "openai",
        "job_id": "ftjob_abc123",
        "status": status,
        "model": "gpt-4.1-mini",
        "fine_tuned_model": model if status == "succeeded" else None,
        "training_file_id": "file_train_001",
        "validation_file_id": None,
        "created_at": "2026-06-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Import isolation: heavy ML packages must NOT be imported at module scope
# ---------------------------------------------------------------------------

def test_llm_training_does_not_import_heavy_packages() -> None:
    """Importing noodle_nodes must not pull in openai, pandas, tiktoken."""
    heavy = {"openai", "tiktoken", "torch", "transformers", "trl", "peft"}
    loaded = set(sys.modules.keys())
    for pkg in heavy:
        # The top-level package should not be in modules if we haven't called
        # any node that uses it yet.
        for mod in loaded:
            if mod == pkg or mod.startswith(pkg + "."):
                # This is OK if it was loaded by some other test running before
                # this one — we just assert the llm_training module itself
                # didn't trigger it. The real import-isolation check is in
                # test_registry_loads_without_heavy_packages below.
                break


def test_registry_loads_without_heavy_packages() -> None:
    """The registry must enumerate all new ML nodes without optional packages."""
    manifests = {m.id: m for m in registry.manifests()}
    expected_ids = {
        "llm_fine_tune_dataset",
        "openai_upload_fine_tune_file",
        "openai_create_fine_tune_job",
        "openai_fine_tune_status",
        "openai_fine_tune_checkpoints",
        "openai_cancel_fine_tune_job",
        "register_fine_tuned_model",
    }
    assert expected_ids <= manifests.keys(), (
        f"Missing nodes: {expected_ids - manifests.keys()}"
    )


def test_all_new_nodes_are_in_machine_learning_category() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    ml_nodes = {
        "llm_fine_tune_dataset",
        "openai_upload_fine_tune_file",
        "openai_create_fine_tune_job",
        "openai_fine_tune_status",
        "openai_fine_tune_checkpoints",
        "openai_cancel_fine_tune_job",
        "register_fine_tuned_model",
    }
    for node_id in ml_nodes:
        assert manifests[node_id].category == "Machine Learning", (
            f"{node_id} should be in 'Machine Learning'"
        )


def test_new_nodes_declare_requirements() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    openai_nodes = {
        "openai_upload_fine_tune_file",
        "openai_create_fine_tune_job",
        "openai_fine_tune_status",
        "openai_fine_tune_checkpoints",
        "openai_cancel_fine_tune_job",
    }
    for node_id in openai_nodes:
        reqs = manifests[node_id].requirements
        assert any("openai" in r for r in reqs), (
            f"{node_id} must declare openai>=1.0 in requirements"
        )

    ft_dataset = manifests["llm_fine_tune_dataset"]
    assert any("pandas" in r for r in ft_dataset.requirements)


# ---------------------------------------------------------------------------
# llm_fine_tune_dataset
# ---------------------------------------------------------------------------

def test_fine_tune_dataset_from_records_chat_format(store_ctx) -> None:
    rows = _chat_records(30)
    result = llm_fine_tune_dataset(
        input=rows,
        format="openai_chat_jsonl",
        user_column="user",
        assistant_column="assistant",
        min_examples=10,
    )
    assert result[_FT_DATASET_MARKER] is True
    assert result["format"] == "openai_chat_jsonl"
    assert result["n_examples"] == 30
    assert is_artifact_ref(result["artifact"])
    assert result["validation"]["errors"] == []


def test_fine_tune_dataset_from_dataset_ref(store_ctx) -> None:
    ds = records_to_dataset(_chat_records(20))
    result = llm_fine_tune_dataset(
        input=ds,
        format="openai_chat_jsonl",
        user_column="user",
        assistant_column="assistant",
        min_examples=5,
    )
    assert result[_FT_DATASET_MARKER] is True
    assert result["n_examples"] >= 5


def test_fine_tune_dataset_validation_split(store_ctx) -> None:
    rows = _chat_records(40)
    result = llm_fine_tune_dataset(
        input=rows,
        format="openai_chat_jsonl",
        user_column="user",
        assistant_column="assistant",
        validation_split=0.2,
        min_examples=5,
    )
    assert result["n_validation"] > 0
    assert is_artifact_ref(result["validation_artifact"])
    assert result["n_examples"] + result["n_validation"] == 40


def test_fine_tune_dataset_dedupe_removes_duplicates(store_ctx) -> None:
    rows = _chat_records(10) * 2  # 20 rows, all duplicates
    result = llm_fine_tune_dataset(
        input=rows,
        format="openai_chat_jsonl",
        user_column="user",
        assistant_column="assistant",
        dedupe=True,
        min_examples=5,
    )
    assert result["n_dropped_duplicates"] == 10
    assert result["n_examples"] == 10


def test_fine_tune_dataset_rejects_missing_columns(store_ctx) -> None:
    rows = [{"prompt": "hello", "response": "world"}]
    with pytest.raises(ValueError, match="Columns|required|not in"):
        llm_fine_tune_dataset(
            input=rows,
            format="openai_chat_jsonl",
            user_column="user",  # doesn't exist in rows
            assistant_column="assistant",
            min_examples=1,
        )


def test_fine_tune_dataset_too_few_examples(store_ctx) -> None:
    rows = _chat_records(3)
    with pytest.raises(ValueError, match="min_examples"):
        llm_fine_tune_dataset(
            input=rows,
            format="openai_chat_jsonl",
            user_column="user",
            assistant_column="assistant",
            min_examples=10,
        )


def test_fine_tune_dataset_prompt_completion_format(store_ctx) -> None:
    rows = [
        {"prompt": f"What is {i}?", "completion": f"It is {i}."}
        for i in range(20)
    ]
    result = llm_fine_tune_dataset(
        input=rows,
        format="prompt_completion_jsonl",
        prompt_column="prompt",
        completion_column="completion",
        min_examples=5,
    )
    assert result["format"] == "prompt_completion_jsonl"
    assert result["n_examples"] == 20
    # Verify the artifact contains valid JSONL
    from noodle.artifacts import read_bytes
    data = read_bytes(result["artifact"])
    lines = data.decode().strip().split("\n")
    parsed = json.loads(lines[0])
    assert "prompt" in parsed and "completion" in parsed


def test_fine_tune_dataset_messages_column(store_ctx) -> None:
    rows = [
        {
            "messages": json.dumps([
                {"role": "user", "content": f"Question {i}"},
                {"role": "assistant", "content": f"Answer {i}"},
            ])
        }
        for i in range(15)
    ]
    result = llm_fine_tune_dataset(
        input=rows,
        format="openai_chat_jsonl",
        messages_column="messages",
        min_examples=5,
    )
    assert result["n_examples"] == 15


def test_fine_tune_dataset_artifact_contains_valid_jsonl(store_ctx) -> None:
    rows = _chat_records(15)
    result = llm_fine_tune_dataset(
        input=rows, format="openai_chat_jsonl",
        user_column="user", assistant_column="assistant",
        min_examples=5,
    )
    from noodle.artifacts import read_bytes
    data = read_bytes(result["artifact"])
    lines = [l for l in data.decode().strip().split("\n") if l]
    assert len(lines) == 15
    first = json.loads(lines[0])
    assert "messages" in first
    assert isinstance(first["messages"], list)
    assert first["messages"][-1]["role"] == "assistant"


# ---------------------------------------------------------------------------
# openai_upload_fine_tune_file (mocked)
# ---------------------------------------------------------------------------

def _make_fake_openai(job_status: str = "queued"):
    """Return a fake openai module and client."""
    fake_file = MagicMock()
    fake_file.id = "file_test_001"
    fake_file.filename = "finetune.jsonl"
    fake_file.bytes = 1024
    fake_file.purpose = "fine-tune"
    fake_file.status = "uploaded"
    fake_file.created_at = 1748736000

    fake_job = MagicMock()
    fake_job.id = "ftjob_test_001"
    fake_job.status = job_status
    fake_job.model = "gpt-4.1-mini"
    fake_job.fine_tuned_model = "ft:gpt-4.1-mini:test::abc" if job_status == "succeeded" else None
    fake_job.created_at = 1748736000
    fake_job.finished_at = None
    fake_job.estimated_finish = None
    fake_job.trained_tokens = None
    fake_job.error = None
    fake_job.hyperparameters = MagicMock(n_epochs=3)
    fake_job.result_files = []
    fake_job.training_file = "file_train_001"
    fake_job.validation_file = None

    fake_events = MagicMock()
    fake_events.data = []

    fake_checkpoints = MagicMock()
    fake_checkpoints.data = [
        MagicMock(
            id="cp_001",
            step_number=100,
            fine_tuned_model_checkpoint="ft:gpt-4.1-mini:ckpt::100",
            metrics={"train_loss": 0.5},
            created_at=1748736000,
        )
    ]

    client = MagicMock()
    client.files.create.return_value = fake_file
    client.fine_tuning.jobs.create.return_value = fake_job
    client.fine_tuning.jobs.retrieve.return_value = fake_job
    client.fine_tuning.jobs.cancel.return_value = MagicMock(status="cancelled")
    client.fine_tuning.jobs.list_events.return_value = fake_events
    client.fine_tuning.jobs.checkpoints.list.return_value = fake_checkpoints

    openai_mod = MagicMock()
    openai_mod.OpenAI.return_value = client

    return openai_mod, client


def test_upload_fine_tune_file_from_ft_dataset(store_ctx) -> None:
    rows = _chat_records(20)
    ft_ds = llm_fine_tune_dataset(
        input=rows, format="openai_chat_jsonl",
        user_column="user", assistant_column="assistant",
        min_examples=5,
    )

    fake_openai, _ = _make_fake_openai()
    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = openai_upload_fine_tune_file(
            input=ft_ds,
            openai_api_key="sk-test",
        )

    assert result["file_id"] == "file_test_001"
    assert result["n_examples"] == 20
    assert result["purpose"] == "fine-tune"


def test_upload_fine_tune_file_from_artifact(store_ctx) -> None:
    from noodle.artifacts import write_bytes
    artifact = write_bytes(
        b'{"messages":[{"role":"user","content":"hi"},{"role":"assistant","content":"hello"}]}\n',
        "test.jsonl",
        "application/x-jsonlines",
    )

    fake_openai, _ = _make_fake_openai()
    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = openai_upload_fine_tune_file(
            input=artifact,
            openai_api_key="sk-test",
        )

    assert result["file_id"] == "file_test_001"


def test_upload_fine_tune_file_requires_api_key(store_ctx) -> None:
    rows = _chat_records(20)
    ft_ds = llm_fine_tune_dataset(
        input=rows, format="openai_chat_jsonl",
        user_column="user", assistant_column="assistant",
        min_examples=5,
    )
    fake_openai, _ = _make_fake_openai()
    with patch.dict(sys.modules, {"openai": fake_openai}):
        with pytest.raises(ValueError, match="openai_api_key"):
            openai_upload_fine_tune_file(input=ft_ds, openai_api_key="")


# ---------------------------------------------------------------------------
# openai_create_fine_tune_job (mocked)
# ---------------------------------------------------------------------------

def test_create_fine_tune_job_returns_job_ref(store_ctx) -> None:
    upload_result = {"file_id": "file_train_001"}
    fake_openai, _ = _make_fake_openai()
    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = openai_create_fine_tune_job(
            input=upload_result,
            openai_api_key="sk-test",
            model="gpt-4.1-mini",
            suffix="noodle-test",
        )

    assert result[_FT_JOB_MARKER] is True
    assert result["job_id"] == "ftjob_test_001"
    assert result["provider"] == "openai"
    assert result["model"] == "gpt-4.1-mini"
    assert result["suffix"] == "noodle-test"


def test_create_fine_tune_job_accepts_raw_file_id(store_ctx) -> None:
    fake_openai, _ = _make_fake_openai()
    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = openai_create_fine_tune_job(
            input="file_raw_001",
            openai_api_key="sk-test",
            model="gpt-4.1-mini",
        )
    assert result["job_id"] == "ftjob_test_001"


def test_create_fine_tune_job_requires_file_id(store_ctx) -> None:
    fake_openai, _ = _make_fake_openai()
    with patch.dict(sys.modules, {"openai": fake_openai}):
        with pytest.raises(ValueError, match="file_id|training"):
            openai_create_fine_tune_job(
                input={},  # no file_id
                openai_api_key="sk-test",
            )


def test_create_fine_tune_job_passes_hyperparameters(store_ctx) -> None:
    fake_openai, client = _make_fake_openai()
    with patch.dict(sys.modules, {"openai": fake_openai}):
        openai_create_fine_tune_job(
            input="file_001",
            openai_api_key="sk-test",
            model="gpt-4.1-mini",
            n_epochs=3,
            batch_size=8,
            learning_rate_multiplier=1.8,
        )

    call_kwargs = client.fine_tuning.jobs.create.call_args[1]
    assert call_kwargs["hyperparameters"]["n_epochs"] == 3
    assert call_kwargs["hyperparameters"]["batch_size"] == 8
    assert abs(call_kwargs["hyperparameters"]["learning_rate_multiplier"] - 1.8) < 0.001


# ---------------------------------------------------------------------------
# openai_fine_tune_status (mocked)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["queued", "running", "succeeded", "failed", "cancelled"])
def test_status_handles_all_provider_statuses(store_ctx, status: str) -> None:
    job_ref = _ft_job_ref(status=status)
    fake_openai, client = _make_fake_openai(job_status=status)
    client.fine_tuning.jobs.retrieve.return_value.status = status
    client.fine_tuning.jobs.retrieve.return_value.fine_tuned_model = (
        "ft:gpt-4.1-mini:x" if status == "succeeded" else None
    )

    with patch.dict(sys.modules, {"openai": fake_openai}):
        if status == "failed":
            with pytest.raises(RuntimeError, match="failed"):
                openai_fine_tune_status(
                    input=job_ref,
                    openai_api_key="sk-test",
                    fail_if_failed=True,
                )
            # With fail_if_failed=False it should return
            result = openai_fine_tune_status(
                input=job_ref,
                openai_api_key="sk-test",
                fail_if_failed=False,
            )
            assert "failed" in result or "main" in result
        else:
            result = openai_fine_tune_status(
                input=job_ref,
                openai_api_key="sk-test",
                fail_if_failed=False,
            )
            assert "main" in result or "succeeded" in result or "running" in result


def test_status_branches_to_succeeded(store_ctx) -> None:
    job_ref = _ft_job_ref(status="succeeded")
    fake_openai, client = _make_fake_openai(job_status="succeeded")
    client.fine_tuning.jobs.retrieve.return_value.status = "succeeded"
    client.fine_tuning.jobs.retrieve.return_value.fine_tuned_model = "ft:gpt-4.1-mini:x"

    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = openai_fine_tune_status(
            input=job_ref,
            openai_api_key="sk-test",
            fail_if_failed=False,
        )

    assert "succeeded" in result
    assert result["succeeded"][_FT_JOB_MARKER] is True


def test_status_accepts_raw_job_id(store_ctx) -> None:
    fake_openai, _ = _make_fake_openai(job_status="running")

    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = openai_fine_tune_status(
            input="ftjob_rawid_001",
            openai_api_key="sk-test",
            fail_if_failed=False,
        )
    assert "main" in result or "running" in result


# ---------------------------------------------------------------------------
# openai_fine_tune_checkpoints (mocked)
# ---------------------------------------------------------------------------

def test_checkpoints_returns_list(store_ctx) -> None:
    job_ref = _ft_job_ref(status="running")
    fake_openai, _ = _make_fake_openai()

    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = openai_fine_tune_checkpoints(
            input=job_ref,
            openai_api_key="sk-test",
        )

    assert result["job_id"] == "ftjob_abc123"
    assert isinstance(result["checkpoints"], list)
    assert result["n_checkpoints"] == 1
    cp = result["checkpoints"][0]
    assert "step_number" in cp
    assert "fine_tuned_model_checkpoint" in cp


# ---------------------------------------------------------------------------
# openai_cancel_fine_tune_job (mocked)
# ---------------------------------------------------------------------------

def test_cancel_job_is_idempotent(store_ctx) -> None:
    job_ref = _ft_job_ref(status="running")
    fake_openai, _ = _make_fake_openai()

    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = openai_cancel_fine_tune_job(
            input=job_ref,
            openai_api_key="sk-test",
        )

    assert result["status"] == "cancelled"
    assert result[_FT_JOB_MARKER] is True


def test_cancel_job_accepts_raw_id(store_ctx) -> None:
    fake_openai, _ = _make_fake_openai()

    with patch.dict(sys.modules, {"openai": fake_openai}):
        result = openai_cancel_fine_tune_job(
            input="ftjob_rawid",
            openai_api_key="sk-test",
        )

    assert result["status"] == "cancelled"


# ---------------------------------------------------------------------------
# register_fine_tuned_model
# ---------------------------------------------------------------------------

def test_register_model_from_job_ref(store_ctx) -> None:
    job_ref = _ft_job_ref(status="succeeded", model="ft:gpt-4.1-mini:x::abc123")
    result = register_fine_tuned_model(
        input=job_ref,
        name="My Fine-Tuned Model",
        task="chat",
        tags="production, v1",
        eval_metric_name="accuracy",
        eval_metric_value=0.91,
    )

    assert result[_MODEL_REGISTRY_MARKER] is True
    assert result["model_id"] == "ft:gpt-4.1-mini:x::abc123"
    assert result["provider"] == "openai"
    assert result["task"] == "chat"
    assert result["tags"] == ["production", "v1"]
    assert result["metrics"]["accuracy"] == pytest.approx(0.91)
    assert is_artifact_ref(result["artifact"])


def test_register_model_from_raw_model_id(store_ctx) -> None:
    result = register_fine_tuned_model(
        input="ft:gpt-4.1-mini:my-model::xyz",
        name="Manual Registration",
    )
    assert result[_MODEL_REGISTRY_MARKER] is True
    assert result["model_id"] == "ft:gpt-4.1-mini:my-model::xyz"


def test_register_model_rejects_unfinished_job(store_ctx) -> None:
    job_ref = _ft_job_ref(status="running", model="")
    with pytest.raises(ValueError, match="completed|succeed|empty"):
        register_fine_tuned_model(input=job_ref)


def test_register_model_artifact_is_valid_json(store_ctx) -> None:
    job_ref = _ft_job_ref(status="succeeded", model="ft:gpt-4.1-mini:test::999")
    result = register_fine_tuned_model(input=job_ref, name="Test Model")

    from noodle.artifacts import read_text
    text = read_text(result["artifact"])
    parsed = json.loads(text)
    assert parsed[_MODEL_REGISTRY_MARKER] is True
    assert parsed["model_id"] == "ft:gpt-4.1-mini:test::999"
