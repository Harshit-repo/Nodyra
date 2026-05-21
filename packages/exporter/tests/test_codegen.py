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
