import io
from contextlib import redirect_stdout

from noodle_exporter import docker_bundle, slugify, workflow_to_script

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
    assert "noodle-core" in bundle["requirements.txt"]
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
