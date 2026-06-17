from app.mcp.tools import STATIC_TOOLS, get_tool


def test_static_registry_names_unique_and_complete() -> None:
    names = [t.name for t in STATIC_TOOLS]
    assert len(names) == len(set(names))
    for expected in (
        "list_workflows",
        "get_workflow",
        "run_workflow",
        "get_run",
        "list_node_types",
        "get_node_type",
        "create_workflow",
        "set_workflow_graph",
        "validate_graph",
        "publish_workflow",
    ):
        assert expected in names


def test_get_tool_lookup() -> None:
    assert get_tool("list_workflows") is not None
    assert get_tool("nope") is None


def test_every_tool_has_object_schema() -> None:
    for tool in STATIC_TOOLS:
        assert tool.input_schema.get("type") == "object"
        assert isinstance(tool.description, str) and tool.description
