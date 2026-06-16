"""Tests for the Machine Learning nodes (scikit-learn backed)."""

from __future__ import annotations

import random

import pytest

import noodle_nodes  # noqa: F401 - registers nodes
from noodle.artifacts import LocalArtifactStore, is_artifact_ref
from noodle.context import artifact_store, current_node_id
from noodle.datasets import is_dataset_ref

pytest.importorskip("sklearn")

from noodle_nodes.datasets import dataset_to_records, records_to_dataset  # noqa: E402
from noodle_nodes.ml import (  # noqa: E402
    evaluate_model,
    is_model_ref,
    load_model,
    ml_predict,
    monitor_model,
    register_model,
    save_model,
    select_features,
    train_classifier,
    train_regressor,
)


@pytest.fixture
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="ml-test-run")
    a = artifact_store.set(store)
    n = current_node_id.set("ml-test-node")
    yield store
    current_node_id.reset(n)
    artifact_store.reset(a)


def _classification_dataset(n: int = 200):
    rng = random.Random(7)
    rows = []
    for _ in range(n):
        x1 = rng.uniform(0, 1)
        x2 = rng.uniform(0, 1)
        noise = rng.uniform(-0.05, 0.05)
        label = 1 if (x1 + x2 + noise) > 1.0 else 0
        rows.append({"x1": round(x1, 4), "x2": round(x2, 4), "label": label})
    return records_to_dataset(rows)


def _regression_dataset(n: int = 200):
    rng = random.Random(11)
    rows = []
    for _ in range(n):
        x1 = rng.uniform(0, 10)
        x2 = rng.uniform(0, 5)
        y = 3.0 * x1 - 2.0 * x2 + 5.0 + rng.uniform(-0.5, 0.5)
        rows.append({"x1": round(x1, 4), "x2": round(x2, 4), "y": round(y, 4)})
    return records_to_dataset(rows)


def test_train_classifier_produces_model_and_metrics(store_ctx) -> None:
    ds = _classification_dataset()
    out = train_classifier(input=ds, target_column="label", algorithm="logistic_regression")
    assert is_model_ref(out["model"])
    assert is_artifact_ref(out["model"]["artifact"])
    metrics = out["metrics"]
    assert metrics["task"] == "classification"
    assert metrics["accuracy"] >= 0.8
    assert set(metrics["classes"]) == {0, 1}
    assert metrics["n_test"] > 0


def test_predict_appends_prediction_column(store_ctx) -> None:
    ds = _classification_dataset()
    trained = train_classifier(input=ds, target_column="label", algorithm="random_forest")
    predicted = ml_predict(model=trained["model"], data=ds, include_proba=True)
    assert is_dataset_ref(predicted)
    rows = dataset_to_records(predicted, max_rows=500)
    assert rows
    first = rows[0]
    assert "prediction" in first
    assert any(key.startswith("proba_") for key in first)


def test_train_regressor_reports_r2(store_ctx) -> None:
    ds = _regression_dataset()
    out = train_regressor(input=ds, target_column="y", algorithm="linear_regression")
    assert is_model_ref(out["model"])
    metrics = out["metrics"]
    assert metrics["task"] == "regression"
    assert metrics["r2"] >= 0.95
    assert "rmse" in metrics
    assert len(metrics["coefficients"]) == 2


def test_evaluate_model_matches_training_task(store_ctx) -> None:
    ds = _regression_dataset()
    trained = train_regressor(input=ds, target_column="y", algorithm="ridge")
    scored = evaluate_model(model=trained["model"], data=ds)
    assert scored["task"] == "regression"
    assert scored["r2"] >= 0.95
    assert scored["n_rows"] > 0


def test_select_features_keeps_top_k(store_ctx) -> None:
    ds = _regression_dataset()
    out = select_features(input=ds, target_column="y", k=1, task="regression")
    assert is_dataset_ref(out["main"])
    rows = dataset_to_records(out["main"], max_rows=500)
    cols = set(rows[0].keys())
    assert "y" in cols
    assert len(cols) == 2  # one feature + target
    assert out["scores"][0]["score"] >= out["scores"][-1]["score"]


def test_save_model_emits_named_artifact(store_ctx) -> None:
    ds = _classification_dataset()
    trained = train_classifier(input=ds, target_column="label")
    saved = save_model(model=trained["model"], name="iris-model")
    assert saved["saved_as"] == "iris-model.joblib"
    assert is_artifact_ref(saved["artifact"])
    assert is_model_ref(saved["model"])  # passthrough preserved
    assert saved["size_bytes"] > 0


def test_train_classifier_requires_target(store_ctx) -> None:
    ds = _classification_dataset(20)
    with pytest.raises(ValueError):
        train_classifier(input=ds, target_column="")


def test_predict_rejects_non_model(store_ctx) -> None:
    ds = _classification_dataset(20)
    with pytest.raises(ValueError):
        ml_predict(model={"not": "a model"}, data=ds)


def test_register_then_load_roundtrip_predicts(store_ctx) -> None:
    ds = _classification_dataset()
    trained = train_classifier(input=ds, target_column="label")
    registered = register_model(model=trained["model"], name="iris-species")
    assert registered["registered_as"] == "iris-species"
    assert is_model_ref(registered["model"])  # passthrough
    assert registered["metadata"]["task"] == "classification"
    assert (store_ctx.base_dir / "model-registry" / "iris-species" / "model.joblib").exists()

    loaded = load_model(name="iris-species")
    assert is_model_ref(loaded["model"])
    assert is_artifact_ref(loaded["model"]["artifact"])
    assert loaded["metadata"]["name"] == "iris-species"
    # The loaded model must produce predictions in a fresh run context.
    predicted = ml_predict(model=loaded["model"], data=ds)
    rows = dataset_to_records(predicted, max_rows=500)
    assert rows and "prediction" in rows[0]


def test_model_registry_uses_artifact_key_prefix(tmp_path) -> None:
    store_a = LocalArtifactStore(tmp_path, run_id="ml-run-a", key_prefix="org-a")
    store_b = LocalArtifactStore(tmp_path, run_id="ml-run-b", key_prefix="org-b")
    node_token = current_node_id.set("ml-test-node")
    try:
        artifact_token = artifact_store.set(store_a)
        try:
            ds = _classification_dataset(60)
            trained = train_classifier(input=ds, target_column="label")
            register_model(model=trained["model"], name="shared-name")
        finally:
            artifact_store.reset(artifact_token)

        assert (
            tmp_path / "org-a" / "model-registry" / "shared-name" / "model.joblib"
        ).exists()
        assert not (tmp_path / "model-registry" / "shared-name").exists()

        artifact_token = artifact_store.set(store_b)
        try:
            with pytest.raises(ValueError, match="no model named"):
                load_model(name="shared-name")
        finally:
            artifact_store.reset(artifact_token)
    finally:
        current_node_id.reset(node_token)


def test_register_model_requires_name(store_ctx) -> None:
    ds = _classification_dataset(20)
    trained = train_classifier(input=ds, target_column="label")
    with pytest.raises(ValueError):
        register_model(model=trained["model"], name="")


def test_load_model_missing_name_errors(store_ctx) -> None:
    with pytest.raises(ValueError):
        load_model(name="does-not-exist")


def test_monitor_model_records_history_and_delta(store_ctx) -> None:
    ds = _classification_dataset()
    trained = train_classifier(input=ds, target_column="label")
    first = monitor_model(model=trained["model"], data=ds, name="iris-species")
    assert first["metrics"]["metric"] == "accuracy"
    assert first["metrics"]["value"] is not None
    assert len(first["history"]) == 1
    assert first["metrics"]["delta"] is None

    second = monitor_model(model=trained["model"], data=ds, name="iris-species")
    assert len(second["history"]) == 2
    assert second["metrics"]["delta"] is not None


def test_monitor_model_threshold_raises_alert(store_ctx) -> None:
    ds = _classification_dataset()
    trained = train_classifier(input=ds, target_column="label")
    out = monitor_model(
        model=trained["model"], data=ds, name="iris-alert",
        metric="accuracy", threshold=1.5,
    )
    assert out["alert"] is True
    assert out["metrics"]["reasons"]


def test_monitor_model_requires_target(store_ctx) -> None:
    ds = _regression_dataset()
    trained = train_regressor(input=ds, target_column="y")
    # Strip the target so it can't be inferred and none is supplied.
    model = dict(trained["model"])
    model["target"] = ""
    with pytest.raises(ValueError):
        monitor_model(model=model, data=ds, target_column="")
