import json

from nodyra_importer import analyze_migration


def _node_types(result) -> set[str]:
    assert result.graph is not None
    return {node["type"] for node in result.graph["nodes"]}


def test_python_script_is_preserved_without_execution() -> None:
    source = "raise RuntimeError('must never execute during import')\noutput = {'ok': True}\n"

    result = analyze_migration(source, "python_script")

    assert result.importable is True
    assert result.partial is False
    assert result.summary == {
        "exact": 0,
        "transformed": 1,
        "manual": 0,
        "unsupported": 0,
    }
    assert _node_types(result) == {"manual_trigger", "code"}
    code = next(node for node in result.graph["nodes"] if node["type"] == "code")
    assert "must never execute during import" in code["params"]["code"]


def test_python_imports_require_explicit_partial_import() -> None:
    source = "import requests\noutput = requests.get('https://example.com').status_code\n"

    blocked = analyze_migration(source, "python_script")
    allowed = analyze_migration(source, "python_script", allow_partial=True)

    assert blocked.importable is False
    assert blocked.partial is True
    assert blocked.summary["manual"] == 1
    assert allowed.importable is True


def test_n8n_reports_unsupported_nodes_and_preserves_supported_edges() -> None:
    source = json.dumps(
        {
            "nodes": [
                {
                    "id": "trigger-id",
                    "name": "Start",
                    "type": "n8n-nodes-base.manualTrigger",
                    "parameters": {},
                },
                {
                    "id": "request-id",
                    "name": "Fetch",
                    "type": "n8n-nodes-base.httpRequest",
                    "parameters": {"url": "https://example.com", "method": "post"},
                },
                {
                    "id": "custom-id",
                    "name": "Custom",
                    "type": "vendor.customNode",
                    "parameters": {},
                },
            ],
            "connections": {
                "Start": {"main": [[{"node": "Fetch", "type": "main", "index": 0}]]}
            },
        }
    )

    blocked = analyze_migration(source, "n8n")
    allowed = analyze_migration(source, "n8n", allow_partial=True)

    assert blocked.importable is False
    assert blocked.partial is True
    assert blocked.summary == {
        "exact": 1,
        "transformed": 1,
        "manual": 0,
        "unsupported": 1,
    }
    assert allowed.importable is True
    assert _node_types(allowed) == {"manual_trigger", "http_request"}
    assert allowed.graph["edges"][0]["source"] == "trigger-id"
    assert allowed.graph["edges"][0]["target"] == "request-id"


def test_airflow_builds_reviewable_draft_and_dependencies() -> None:
    source = """
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator

def transform(value):
    return {"value": value}

start = EmptyOperator(task_id="start")
work = PythonOperator(task_id="work", python_callable=transform)
start >> work
"""

    blocked = analyze_migration(source, "airflow")
    allowed = analyze_migration(source, "airflow", allow_partial=True)

    assert blocked.importable is False
    assert blocked.partial is True
    assert allowed.importable is True
    assert _node_types(allowed) == {"manual_trigger", "no_op", "code"}
    assert any(
        edge["source"] == "start" and edge["target"] == "work"
        for edge in allowed.graph["edges"]
    )


def test_prefect_builds_reviewable_task_chain() -> None:
    source = """
from prefect import flow, task

@task
def load():
    return [1, 2, 3]

@task
def transform(value):
    return [item * 2 for item in value]

@flow
def pipeline():
    loaded = load()
    transformed = transform(loaded)
    return transformed
"""

    result = analyze_migration(source, "prefect", allow_partial=True)

    assert result.importable is True
    assert result.partial is True
    assert _node_types(result) == {"manual_trigger", "code"}
    assert any(
        edge["source"] == "loaded" and edge["target"] == "transformed"
        for edge in result.graph["edges"]
    )
