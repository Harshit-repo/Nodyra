import noodle_nodes  # noqa: F401 - registers the built-in nodes
from noodle.models import WorkflowGraph
from noodle.sdk import registry
from noodle_exporter.module_codegen import workflow_to_module

GRAPH = WorkflowGraph.model_validate(
    {
        "nodes": [
            {"id": "start", "type": "manual_trigger", "params": {}},
            {
                "id": "fetch",
                "type": "http_request",
                "params": {"url": "https://example.com", "method": "GET"},
                "position": {"x": 300, "y": 120},
                "on_error": "continue",
            },
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "fetch", "target_input": "input"}
        ],
    }
)


def _load_module(source: str) -> dict:
    module_globals: dict = {}
    exec(compile(source, "exported_wf.py", "exec"), module_globals)  # noqa: S102
    return module_globals


def test_module_compiles() -> None:
    source = workflow_to_module(GRAPH, "Demo Flow", registry=registry)
    compile(source, "exported_wf.py", "exec")
    assert "@node(" in source
    assert "def main()" in source
    assert "GRAPH =" not in source  # no embedded JSON graph


def test_params_become_function_defaults() -> None:
    source = workflow_to_module(GRAPH, "Demo Flow", registry=registry)
    assert "url='https://example.com'" in source or 'url="https://example.com"' in source


def test_build_graph_roundtrip() -> None:
    module = _load_module(workflow_to_module(GRAPH, "Demo Flow", registry=registry))
    graph = module["_build_graph"]()
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert by_id["start"]["type"] == "manual_trigger"
    assert by_id["fetch"]["type"] == "http_request"
    assert by_id["fetch"]["params"]["url"] == "https://example.com"
    assert by_id["fetch"]["on_error"] == "continue"
    assert any(
        e["source"] == "start"
        and e["target"] == "fetch"
        and e["target_input"] == "input"
        for e in graph["edges"]
    )
    parsed = WorkflowGraph.model_validate(graph)
    assert len(parsed.nodes) == 2


def test_editing_a_default_changes_the_built_graph() -> None:
    source = workflow_to_module(GRAPH, "Demo Flow", registry=registry)
    edited = source.replace("https://example.com", "https://edited.example")
    module = _load_module(edited)
    graph = module["_build_graph"]()
    fetch = next(n for n in graph["nodes"] if n["id"] == "fetch")
    assert fetch["params"]["url"] == "https://edited.example"


def test_unknown_node_types_fall_back_to_extra_nodes() -> None:
    graph = WorkflowGraph.model_validate(
        {
            "nodes": [
                {"id": "start", "type": "manual_trigger", "params": {}},
                {"id": "custom", "type": "user:abc123:my_fn", "params": {"x": 1}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "custom", "target_input": "input"}
            ],
        }
    )
    module = _load_module(workflow_to_module(graph, "Custom", registry=registry))
    built = module["_build_graph"]()
    types = {n["id"]: n["type"] for n in built["nodes"]}
    assert types["custom"] == "user:abc123:my_fn"
    assert any(e["target"] == "custom" for e in built["edges"])


def test_main_runs_trigger_only_graph(capsys) -> None:
    graph = WorkflowGraph.model_validate(
        {"nodes": [{"id": "t", "type": "manual_trigger", "params": {}}], "edges": []}
    )
    module = _load_module(workflow_to_module(graph, "Trigger Only", registry=registry))
    module["main"]()
    captured = capsys.readouterr()
    assert "success" in captured.out
