"""Data platform nodes: Snowflake, BigQuery, dbt Cloud, MLflow."""

from __future__ import annotations

from typing import Any

from noodle.sdk import node
from noodle_nodes._creds import cred_multi


# ---------------------------------------------------------------------------
# Snowflake
# ---------------------------------------------------------------------------

@node(
    id="snowflake_query",
    name="Snowflake Query",
    category="Data Platforms",
    description="Execute a SQL query against Snowflake and return results as a dataset.",
    icon="database",
    params={
        "credentials": {
            **cred_multi(
                "snowflake",
                "Snowflake Credentials",
                ["account", "user", "password", "warehouse", "database", "schema", "role"],
            ),
            "description": "Snowflake account credentials.",
        },
        "query": {
            "type": "string",
            "display_name": "SQL Query",
            "multiline": True,
        },
        "limit": {
            "type": "integer",
            "display_name": "Row limit (0 = unlimited)",
            "default": 0,
            "required": False,
        },
    },
    input_kinds={"main": "any"},
    output_kinds={"main": "dataset"},
)
def snowflake_query(
    input: Any,
    *,
    credentials: dict[str, str] | None = None,
    query: str = "",
    limit: int = 0,
) -> dict:
    """Execute a Snowflake SQL query and return results as a DatasetRef."""
    if not query:
        raise ValueError("snowflake_query: query is required")

    try:
        import snowflake.connector as _sf
    except ImportError:
        raise RuntimeError("snowflake-connector-python is required: pip install snowflake-connector-python")

    from noodle_nodes.datasets import records_to_dataset

    creds = credentials or {}
    conn = _sf.connect(
        account=creds.get("account", ""),
        user=creds.get("user", ""),
        password=creds.get("password", ""),
        warehouse=creds.get("warehouse") or None,
        database=creds.get("database") or None,
        schema=creds.get("schema") or None,
        role=creds.get("role") or None,
    )
    try:
        cur = conn.cursor(_sf.DictCursor)
        cur.execute(query)
        rows = cur.fetchmany(int(limit)) if limit and limit > 0 else cur.fetchall()
        records = [dict(r) for r in rows]
    finally:
        conn.close()

    return records_to_dataset(records)


# ---------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------

@node(
    id="bigquery_query",
    name="BigQuery Query",
    category="Data Platforms",
    description="Execute a SQL query against Google BigQuery and return results as a dataset.",
    icon="database",
    params={
        "credentials": {
            **cred_multi(
                "gcp_service_account",
                "GCP Service Account JSON",
                ["service_account_json", "project_id"],
            ),
            "description": "GCP service account JSON and project ID.",
        },
        "query": {
            "type": "string",
            "display_name": "SQL Query",
            "multiline": True,
        },
        "limit": {
            "type": "integer",
            "display_name": "Row limit (0 = unlimited)",
            "default": 0,
            "required": False,
        },
    },
    input_kinds={"main": "any"},
    output_kinds={"main": "dataset"},
)
def bigquery_query(
    input: Any,
    *,
    credentials: dict[str, str] | None = None,
    query: str = "",
    limit: int = 0,
) -> dict:
    """Execute a BigQuery SQL query and return results as a DatasetRef."""
    if not query:
        raise ValueError("bigquery_query: query is required")

    try:
        import json as _json
        from google.cloud import bigquery as _bq
        from google.oauth2 import service_account as _sa
    except ImportError:
        raise RuntimeError("google-cloud-bigquery is required: pip install google-cloud-bigquery")

    from noodle_nodes.datasets import records_to_dataset

    creds = credentials or {}
    sa_json = creds.get("service_account_json", "")
    project_id = creds.get("project_id", "")

    if sa_json:
        sa_info = _json.loads(sa_json)
        gcp_creds = _sa.Credentials.from_service_account_info(
            sa_info, scopes=["https://www.googleapis.com/auth/bigquery.readonly"]
        )
        client = _bq.Client(project=project_id or sa_info.get("project_id"), credentials=gcp_creds)
    else:
        client = _bq.Client(project=project_id or None)

    job = client.query(query)
    rows = job.result()

    records = []
    for i, row in enumerate(rows):
        if limit and limit > 0 and i >= limit:
            break
        records.append(dict(row))

    return records_to_dataset(records)


# ---------------------------------------------------------------------------
# dbt Cloud
# ---------------------------------------------------------------------------

@node(
    id="dbt_cloud_trigger_job",
    name="dbt Cloud Trigger Job",
    category="Data Platforms",
    description="Trigger a dbt Cloud job and optionally wait for its completion.",
    icon="database",
    params={
        "credentials": {
            **cred_multi(
                "dbt_cloud",
                "dbt Cloud API Token",
                ["api_token", "account_id"],
            ),
            "description": "dbt Cloud API token and account ID.",
        },
        "job_id": {
            "type": "string",
            "display_name": "Job ID",
        },
        "cause": {
            "type": "string",
            "display_name": "Cause",
            "default": "Triggered by Noodle",
            "required": False,
        },
        "wait_for_completion": {
            "type": "boolean",
            "display_name": "Wait for completion",
            "default": True,
            "required": False,
        },
        "poll_interval": {
            "type": "integer",
            "display_name": "Poll interval (seconds)",
            "default": 10,
            "required": False,
        },
        "timeout": {
            "type": "integer",
            "display_name": "Max wait (seconds)",
            "default": 600,
            "required": False,
        },
    },
    input_kinds={"main": "any"},
    output_kinds={"main": "any"},
)
def dbt_cloud_trigger_job(
    input: Any,
    *,
    credentials: dict[str, str] | None = None,
    job_id: str = "",
    cause: str = "Triggered by Noodle",
    wait_for_completion: bool = True,
    poll_interval: int = 10,
    timeout: int = 600,
) -> dict:
    """Trigger a dbt Cloud job run and optionally poll until complete."""
    import json as _json
    import time as _time
    import urllib.error as _urlerr
    import urllib.request as _urlreq

    if not job_id:
        raise ValueError("dbt_cloud_trigger_job: job_id is required")

    creds = credentials or {}
    api_token = creds.get("api_token", "")
    account_id = creds.get("account_id", "")
    if not api_token:
        raise ValueError("dbt_cloud_trigger_job: api_token credential is required")
    if not account_id:
        raise ValueError("dbt_cloud_trigger_job: account_id credential is required")

    base = f"https://cloud.getdbt.com/api/v2/accounts/{account_id}"
    headers = {
        "Authorization": f"Token {api_token}",
        "Content-Type": "application/json",
    }

    def _api(method: str, path: str, body: dict | None = None) -> dict:
        data = _json.dumps(body).encode() if body else None
        req = _urlreq.Request(f"{base}{path}", data=data, headers=headers, method=method)
        try:
            with _urlreq.urlopen(req) as resp:
                return _json.loads(resp.read())
        except _urlerr.HTTPError as exc:
            raise RuntimeError(f"dbt Cloud API error {exc.code}: {exc.read().decode()}") from exc

    run_resp = _api("POST", f"/jobs/{job_id}/run/", {"cause": cause or "Triggered by Noodle"})
    run_data = run_resp.get("data", run_resp)
    run_id = run_data.get("id")

    if not wait_for_completion:
        return {"run_id": run_id, "status": "triggered", "job_id": job_id}

    deadline = _time.time() + int(timeout or 600)
    interval = max(5, int(poll_interval or 10))

    while _time.time() < deadline:
        status_resp = _api("GET", f"/runs/{run_id}/")
        status_data = status_resp.get("data", status_resp)
        status_humanized = status_data.get("status_humanized", "")
        finished = status_data.get("is_complete", False)
        if finished:
            success = status_data.get("is_success", False)
            if not success:
                raise RuntimeError(
                    f"dbt Cloud job {job_id} run {run_id} failed with status: {status_humanized}"
                )
            return {
                "run_id": run_id,
                "job_id": job_id,
                "status": status_humanized,
                "duration_seconds": status_data.get("duration"),
            }
        _time.sleep(interval)

    raise TimeoutError(f"dbt Cloud job {job_id} run {run_id} did not complete within {timeout}s")


# ---------------------------------------------------------------------------
# MLflow
# ---------------------------------------------------------------------------

@node(
    id="mlflow_log_metric",
    name="MLflow Log Metric",
    category="Data Platforms",
    description="Log one or more metrics to an MLflow run.",
    icon="chart-line",
    params={
        "tracking_uri": {
            "type": "string",
            "display_name": "Tracking URI",
            "default": "http://localhost:5000",
            "required": False,
        },
        "run_id": {
            "type": "string",
            "display_name": "Run ID (blank = new run)",
            "required": False,
        },
        "experiment_name": {
            "type": "string",
            "display_name": "Experiment name",
            "required": False,
        },
        "metrics": {
            "type": "object",
            "display_name": "Metrics (key-value pairs)",
        },
        "step": {
            "type": "integer",
            "display_name": "Step",
            "default": 0,
            "required": False,
        },
    },
    input_kinds={"main": "any"},
    output_kinds={"main": "any"},
)
def mlflow_log_metric(
    input: Any,
    *,
    tracking_uri: str = "http://localhost:5000",
    run_id: str = "",
    experiment_name: str = "",
    metrics: dict[str, float] | None = None,
    step: int = 0,
) -> dict:
    """Log metrics to an MLflow run."""
    if not metrics:
        raise ValueError("mlflow_log_metric: metrics dict is required and must not be empty")

    try:
        import mlflow as _mlflow
    except ImportError:
        raise RuntimeError("mlflow is required: pip install mlflow")

    if tracking_uri:
        _mlflow.set_tracking_uri(tracking_uri)

    if experiment_name:
        _mlflow.set_experiment(experiment_name)

    if run_id:
        with _mlflow.start_run(run_id=run_id):
            _mlflow.log_metrics(metrics, step=int(step or 0))
    else:
        with _mlflow.start_run() as active:
            run_id = active.info.run_id
            _mlflow.log_metrics(metrics, step=int(step or 0))

    return {"run_id": run_id, "metrics": metrics, "step": int(step or 0)}


@node(
    id="mlflow_log_artifact",
    name="MLflow Log Artifact",
    category="Data Platforms",
    description="Log a file or dataset as an artifact to an MLflow run.",
    icon="file",
    params={
        "tracking_uri": {
            "type": "string",
            "display_name": "Tracking URI",
            "default": "http://localhost:5000",
            "required": False,
        },
        "run_id": {
            "type": "string",
            "display_name": "Run ID (blank = new run)",
            "required": False,
        },
        "experiment_name": {
            "type": "string",
            "display_name": "Experiment name",
            "required": False,
        },
        "artifact_path": {
            "type": "string",
            "display_name": "Artifact sub-path (optional)",
            "required": False,
        },
    },
    input_kinds={"main": "any"},
    output_kinds={"main": "any"},
)
def mlflow_log_artifact(
    input: Any,
    *,
    tracking_uri: str = "http://localhost:5000",
    run_id: str = "",
    experiment_name: str = "",
    artifact_path: str = "",
) -> dict:
    """Log an artifact (file path string or DatasetRef) to an MLflow run."""
    if input is None:
        raise ValueError("mlflow_log_artifact: input is required (file path string or DatasetRef)")

    try:
        import mlflow as _mlflow
    except ImportError:
        raise RuntimeError("mlflow is required: pip install mlflow")

    import pathlib as _pathlib
    import tempfile as _tempfile

    if tracking_uri:
        _mlflow.set_tracking_uri(tracking_uri)

    if experiment_name:
        _mlflow.set_experiment(experiment_name)

    def _do_log(run: Any) -> None:
        nonlocal run_id
        run_id = run.info.run_id

        if isinstance(input, dict) and input.get("__noodle_dataset__"):
            from noodle.datasets import dataset_path_for_ref
            src = dataset_path_for_ref(input)
            _mlflow.log_artifact(str(src), artifact_path=artifact_path or None)
        elif isinstance(input, str):
            p = _pathlib.Path(input)
            if not p.exists():
                raise FileNotFoundError(f"mlflow_log_artifact: file not found: {input}")
            _mlflow.log_artifact(str(p), artifact_path=artifact_path or None)
        else:
            import json as _json
            with _tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as tmp:
                _json.dump(input, tmp)
                tmp_path = tmp.name
            try:
                _mlflow.log_artifact(tmp_path, artifact_path=artifact_path or None)
            finally:
                _pathlib.Path(tmp_path).unlink(missing_ok=True)

    if run_id:
        with _mlflow.start_run(run_id=run_id) as run:
            _do_log(run)
    else:
        with _mlflow.start_run() as run:
            _do_log(run)

    return {"run_id": run_id, "artifact_path": artifact_path}
