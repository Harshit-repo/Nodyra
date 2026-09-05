import io
from contextlib import redirect_stdout

from nodyra_exporter import docker_bundle, slugify, workflow_to_script

GRAPH = {
    "nodes": [
        {
            "id": "t",
            "type": "manual_trigger",
            "params": {"data": {"n": 5}},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "c",
            "type": "code",
            "params": {"code": "output = input['n'] + 1"},
            "position": {"x": 250, "y": 0},
        },
    ],
    "edges": [
        {
            "id": "e",
            "source": "t",
            "source_output": "main",
            "target": "c",
            "target_input": "input",
        }
    ],
}


def test_exported_script_runs() -> None:
    script = workflow_to_script(GRAPH, "My Flow")
    out = io.StringIO()
    namespace = {"__name__": "__main__"}
    with redirect_stdout(out):
        exec(compile(script, "my-flow.py", "exec"), namespace)  # noqa: S102
    assert "workflow finished: success" in out.getvalue()


def test_docker_bundle_has_all_files() -> None:
    bundle = docker_bundle(
        GRAPH, "My Flow", python_version="3.12", packages=["pandas"]
    )
    assert set(bundle) == {
        "workflow.py",
        "requirements.txt",
        "Dockerfile",
        "README.md",
    }
    assert "pandas" in bundle["requirements.txt"]
    assert "nodyra-core" in bundle["requirements.txt"]
    assert "python:3.12-slim" in bundle["Dockerfile"]


def test_slugify() -> None:
    assert slugify("My Cool Flow!") == "my-cool-flow"
    assert slugify("") == "workflow"


# --- A3: bundled sub-workflows ----------------------------------------------

PARENT_GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual_trigger", "params": {"data": 5},
         "position": {"x": 0, "y": 0}},
        {"id": "sub", "type": "execute_workflow",
         "params": {"workflow_id": "child-1"}, "position": {"x": 200, "y": 0}},
    ],
    "edges": [
        {"id": "e", "source": "t", "source_output": "main",
         "target": "sub", "target_input": "input"},
    ],
}

CHILD_GRAPH = {
    "nodes": [
        {"id": "ct", "type": "manual_trigger", "params": {},
         "position": {"x": 0, "y": 0}},
        {"id": "cc", "type": "code", "params": {"code": "output = input * 3"},
         "position": {"x": 200, "y": 0}},
    ],
    "edges": [
        {"id": "ce", "source": "ct", "source_output": "main",
         "target": "cc", "target_input": "input"},
    ],
}


def test_script_with_bundled_subworkflow_round_trips() -> None:
    """A3 acceptance: exported workflow containing a workflow_call runs."""
    script = workflow_to_script(
        PARENT_GRAPH, "Parent", subworkflows={"child-1": CHILD_GRAPH},
        root_id="parent-1",
    )
    out = io.StringIO()
    namespace = {"__name__": "__main__"}
    with redirect_stdout(out):
        exec(compile(script, "parent.py", "exec"), namespace)  # noqa: S102
    stdout = out.getvalue()
    assert "workflow finished: success" in stdout
    assert "sub: success" in stdout


def test_script_without_subworkflows_has_empty_bundle() -> None:
    script = workflow_to_script(PARENT_GRAPH, "Parent")
    # Resolver scaffolding is always present; the bundle is just empty, so a
    # firing workflow_call raises a clear "not bundled in this export" error.
    assert "SUBWORKFLOWS = {}" in script
    assert "not bundled in this export" in script


# A node as the API actually serializes it: label is null, flags are false.
# JSON spells those null/false, which Python does not understand.
SERIALIZED_GRAPH = {
    "nodes": [
        {
            "id": "t",
            "type": "manual_trigger",
            "label": None,
            "params": {"data": {"n": 5}},
            "position": {"x": 0.0, "y": 0.0},
            "disabled": False,
            "outputs_override": None,
            "retry_on_fail": False,
            "always_output_data": True,
            "timeout_seconds": None,
        },
        {
            "id": "c",
            "type": "code",
            "label": None,
            "params": {"code": "output = input['n'] + 1"},
            "position": {"x": 250.0, "y": 0.0},
            "disabled": False,
        },
    ],
    "edges": [{"source": "t", "source_output": "main", "target": "c"}],
}


def test_exported_script_runs_for_a_serialized_graph() -> None:
    """Graphs straight off the API carry None/False and must still execute.

    Embedding the graph as JSON emitted null/false/true, so the generated file
    died with "NameError: name 'null' is not defined" before running a node.
    """
    script = workflow_to_script(SERIALIZED_GRAPH, "Serialized Flow")

    assert ": null" not in script
    assert ": false" not in script

    out = io.StringIO()
    namespace = {"__name__": "__main__"}
    with redirect_stdout(out):
        exec(compile(script, "serialized-flow.py", "exec"), namespace)  # noqa: S102
    assert "workflow finished: success" in out.getvalue()


def test_exported_graph_round_trips_exactly() -> None:
    script = workflow_to_script(SERIALIZED_GRAPH, "Serialized Flow")
    namespace: dict = {}
    exec(compile(script, "serialized-flow.py", "exec"), namespace)  # noqa: S102

    assert namespace["GRAPH"] == SERIALIZED_GRAPH


DATASET_GRAPH = {
    "nodes": [
        {
            "id": "t",
            "type": "manual_trigger",
            "params": {"data": {}},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "rows",
            "type": "code",
            "params": {"code": "output = [{'a': 1}, {'a': 2}]"},
            "position": {"x": 250, "y": 0},
        },
        {
            "id": "ds",
            "type": "records_to_dataset",
            "params": {},
            "position": {"x": 500, "y": 0},
        },
    ],
    "edges": [
        {"source": "t", "target": "rows"},
        {"source": "rows", "target": "ds"},
    ],
}


def test_exported_script_can_produce_datasets(tmp_path, monkeypatch) -> None:
    """Dataset nodes need an artifact store, which the script must set up.

    Without one every dataset-producing node fails with "datasets are not
    available in this execution context" — which is most useful workflows.
    """
    monkeypatch.setenv("NODYRA_ARTIFACTS_DIR", str(tmp_path))
    script = workflow_to_script(DATASET_GRAPH, "Dataset Flow")

    out = io.StringIO()
    namespace = {"__name__": "__main__"}
    with redirect_stdout(out):
        exec(compile(script, "dataset-flow.py", "exec"), namespace)  # noqa: S102

    printed = out.getvalue()
    assert "workflow finished: success" in printed
    assert "datasets are not available" not in printed
    assert any(tmp_path.rglob("*.parquet"))
