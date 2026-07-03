"""Tests for data_platform_nodes (Snowflake, BigQuery, dbt Cloud, MLflow)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pyarrow.dataset  # ensure loaded before any sys.modules patching  # noqa: F401
import pytest

import nodyra_nodes  # noqa: F401 - registers nodes
from nodyra.artifacts import LocalArtifactStore
from nodyra.context import artifact_store, current_node_id
from nodyra_nodes.data_platform_nodes import (
    bigquery_query,
    dbt_cloud_trigger_job,
    mlflow_log_artifact,
    mlflow_log_metric,
    snowflake_query,
)


@pytest.fixture()
def store_ctx(tmp_path: Path):
    store = LocalArtifactStore(tmp_path, run_id="test-run")
    tok_a = artifact_store.set(store)
    tok_n = current_node_id.set("test-node")
    yield store
    current_node_id.reset(tok_n)
    artifact_store.reset(tok_a)


def _make_snowflake_modules() -> dict:
    """Return sys.modules patch dict for snowflake.connector."""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.close = MagicMock()

    mock_sf = MagicMock()
    mock_sf.connect.return_value = mock_conn
    mock_sf.DictCursor = MagicMock()

    mock_snowflake_pkg = ModuleType("snowflake")
    mock_snowflake_pkg.connector = mock_sf

    return {
        "snowflake": mock_snowflake_pkg,
        "snowflake.connector": mock_sf,
    }


# ---------------------------------------------------------------------------
# snowflake_query
# ---------------------------------------------------------------------------

def test_snowflake_query_returns_dataset(store_ctx) -> None:
    with patch.dict(sys.modules, _make_snowflake_modules()):
        result = snowflake_query(
            input=None,
            credentials={"account": "a", "user": "u", "password": "p"},
            query="SELECT id, name FROM users",
        )

    assert result.get("__nodyra_dataset__") is True
    assert result["row_count"] == 2


def test_snowflake_query_requires_query(store_ctx) -> None:
    with pytest.raises(ValueError, match="query"):
        snowflake_query(input=None, credentials={}, query="")


def test_snowflake_query_missing_module(store_ctx) -> None:
    with patch.dict(sys.modules, {"snowflake": None, "snowflake.connector": None}):
        with pytest.raises(RuntimeError, match="snowflake-connector-python"):
            snowflake_query(input=None, credentials={}, query="SELECT 1")


# ---------------------------------------------------------------------------
# bigquery_query
# ---------------------------------------------------------------------------

def _make_bigquery_modules() -> dict:
    """Return sys.modules patch dict for google.cloud.bigquery.

    Preserves the existing google namespace package so pyarrow/protobuf are unaffected.
    Only injects the bigquery-specific modules.
    """
    mock_row1 = {"project": "alpha", "cost": 100.0}
    mock_row2 = {"project": "beta", "cost": 200.0}

    mock_job = MagicMock()
    mock_job.result.return_value = iter([mock_row1, mock_row2])

    mock_client_instance = MagicMock()
    mock_client_instance.query.return_value = mock_job

    mock_bq = MagicMock()
    mock_bq.Client.return_value = mock_client_instance
    mock_bq.QueryJobConfig.return_value = MagicMock()

    mock_sa = MagicMock()
    mock_sa.Credentials.from_service_account_info.return_value = MagicMock()

    # Build google.cloud as a ModuleType whose .bigquery attr resolves to our mock.
    # We do NOT replace sys.modules["google"] to avoid breaking pyarrow's google namespace.
    mock_google_cloud = ModuleType("google.cloud")
    mock_google_cloud.bigquery = mock_bq

    mock_google_oauth2 = ModuleType("google.oauth2")
    mock_google_oauth2.service_account = mock_sa

    result: dict = {
        "google.cloud": mock_google_cloud,
        "google.cloud.bigquery": mock_bq,
        "google.oauth2": mock_google_oauth2,
        "google.oauth2.service_account": mock_sa,
    }
    # Only add top-level google mock if it isn't already a real installed package
    if "google" not in sys.modules:
        mock_google = ModuleType("google")
        mock_google.cloud = mock_google_cloud
        mock_google.oauth2 = mock_google_oauth2
        result["google"] = mock_google
    return result


def test_bigquery_query_returns_dataset(store_ctx) -> None:
    sa_json = json.dumps({"type": "service_account", "project_id": "my-project"})
    with patch.dict(sys.modules, _make_bigquery_modules()):
        result = bigquery_query(
            input=None,
            credentials={"service_account_json": sa_json, "project_id": "my-project"},
            query="SELECT project, cost FROM billing",
        )

    assert result.get("__nodyra_dataset__") is True
    assert result["row_count"] == 2


def test_bigquery_query_requires_query(store_ctx) -> None:
    with pytest.raises(ValueError, match="query"):
        bigquery_query(input=None, credentials={}, query="")


def test_bigquery_query_missing_module(store_ctx) -> None:
    with patch.dict(sys.modules, {
        "google": None, "google.cloud": None,
        "google.cloud.bigquery": None, "google.oauth2": None,
        "google.oauth2.service_account": None,
    }):
        with pytest.raises(RuntimeError, match="google-cloud-bigquery"):
            bigquery_query(input=None, credentials={}, query="SELECT 1")


# ---------------------------------------------------------------------------
# dbt_cloud_trigger_job
# ---------------------------------------------------------------------------

def _make_dbt_urlopen(responses: list[bytes]):
    """Return a side_effect function for urllib.request.urlopen."""
    idx = {"i": 0}

    def _urlopen(req):
        body = responses[idx["i"]]
        idx["i"] += 1
        mock = MagicMock()
        mock.read.return_value = body
        mock.__enter__ = lambda s: s
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    return _urlopen


def test_dbt_cloud_trigger_job_no_wait() -> None:
    trigger_resp = json.dumps({"data": {"id": 77}}).encode()

    with patch("urllib.request.urlopen", side_effect=_make_dbt_urlopen([trigger_resp])):
        result = dbt_cloud_trigger_job(
            input=None,
            credentials={"api_token": "tok", "account_id": "123"},
            job_id="456",
            wait_for_completion=False,
        )

    assert result["run_id"] == 77
    assert result["status"] == "triggered"


def test_dbt_cloud_trigger_job_waits(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)

    trigger_resp = json.dumps({"data": {"id": 99}}).encode()
    status_resp = json.dumps({
        "data": {
            "id": 99,
            "status_humanized": "Success",
            "is_complete": True,
            "is_success": True,
            "duration": 30,
        }
    }).encode()

    with patch("urllib.request.urlopen", side_effect=_make_dbt_urlopen([trigger_resp, status_resp])):
        result = dbt_cloud_trigger_job(
            input=None,
            credentials={"api_token": "tok", "account_id": "123"},
            job_id="789",
            wait_for_completion=True,
        )

    assert result["run_id"] == 99
    assert result["status"] == "Success"


def test_dbt_cloud_trigger_job_requires_job_id() -> None:
    with pytest.raises(ValueError, match="job_id"):
        dbt_cloud_trigger_job(input=None, credentials={"api_token": "x", "account_id": "y"}, job_id="")


def test_dbt_cloud_trigger_job_requires_api_token() -> None:
    with pytest.raises(ValueError, match="api_token"):
        dbt_cloud_trigger_job(input=None, credentials={"account_id": "y"}, job_id="123")


# ---------------------------------------------------------------------------
# mlflow_log_metric
# ---------------------------------------------------------------------------

def _make_mlflow_mock() -> MagicMock:
    mock_run = MagicMock()
    mock_run.info.run_id = "run-abc"
    mock_run.__enter__ = lambda s: s
    mock_run.__exit__ = MagicMock(return_value=False)

    mock_mlflow = MagicMock()
    mock_mlflow.start_run.return_value = mock_run
    mock_mlflow.active_run.return_value = mock_run
    return mock_mlflow


def test_mlflow_log_metric_calls_mlflow() -> None:
    mock_mlflow = _make_mlflow_mock()

    with patch.dict(sys.modules, {"mlflow": mock_mlflow}):
        result = mlflow_log_metric(
            input=None,
            tracking_uri="http://localhost:5000",
            metrics={"accuracy": 0.95, "loss": 0.05},
            step=10,
        )

    mock_mlflow.log_metrics.assert_called_once_with({"accuracy": 0.95, "loss": 0.05}, step=10)
    assert result["metrics"]["accuracy"] == 0.95
    assert result["step"] == 10


def test_mlflow_log_metric_requires_metrics() -> None:
    with pytest.raises(ValueError, match="metrics"):
        mlflow_log_metric(input=None, metrics=None)


def test_mlflow_log_metric_missing_module() -> None:
    with patch.dict(sys.modules, {"mlflow": None}):
        with pytest.raises(RuntimeError, match="mlflow"):
            mlflow_log_metric(input=None, metrics={"loss": 0.1})


# ---------------------------------------------------------------------------
# mlflow_log_artifact
# ---------------------------------------------------------------------------

def test_mlflow_log_artifact_with_file(tmp_path: Path) -> None:
    p = tmp_path / "model.json"
    p.write_text('{"weights": [1, 2, 3]}')

    mock_mlflow = _make_mlflow_mock()

    with patch.dict(sys.modules, {"mlflow": mock_mlflow}):
        result = mlflow_log_artifact(input=str(p), tracking_uri="http://localhost:5000")

    mock_mlflow.log_artifact.assert_called_once_with(str(p), artifact_path=None)
    assert "run_id" in result


def test_mlflow_log_artifact_with_dataset_ref(tmp_path: Path, store_ctx) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from nodyra_nodes.file_nodes import read_parquet_file

    p = tmp_path / "data.parquet"
    pq.write_table(pa.table({"x": [1, 2]}), str(p))
    ref = read_parquet_file(input=None, path=str(p))

    mock_mlflow = _make_mlflow_mock()

    with patch.dict(sys.modules, {"mlflow": mock_mlflow}):
        result = mlflow_log_artifact(input=ref)

    mock_mlflow.log_artifact.assert_called_once()
    assert "run_id" in result


def test_mlflow_log_artifact_requires_input() -> None:
    with pytest.raises(ValueError, match="input"):
        mlflow_log_artifact(input=None)


def test_mlflow_log_artifact_missing_module() -> None:
    with patch.dict(sys.modules, {"mlflow": None}):
        with pytest.raises(RuntimeError, match="mlflow"):
            mlflow_log_artifact(input="some_file.json")
