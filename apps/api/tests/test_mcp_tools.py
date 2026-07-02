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
        "search_node_catalog",
        "get_node_schema",
        "suggest_node_config",
        "create_workflow",
        "set_workflow_graph",
        "validate_graph",
        "validate_workflow_graph",
        "preview_workflow_patch",
        "apply_workflow_patch",
        "publish_workflow",
        "update_workflow_settings",
        "create_environment",
        "add_environment_package",
        "set_environment_packages",
        "remove_environment_package",
        "rebuild_environment",
        "list_environment_build_jobs",
        "get_environment_build_job",
        "get_workflow_version",
        "diff_workflow_versions",
        "update_schedule",
        "get_node_run",
        "list_run_approvals",
        "resolve_run_approval",
    ):
        assert expected in names


def test_get_tool_lookup() -> None:
    assert get_tool("list_workflows") is not None
    assert get_tool("nope") is None


def test_every_tool_has_object_schema() -> None:
    for tool in STATIC_TOOLS:
        assert tool.input_schema.get("type") == "object"
        assert isinstance(tool.description, str) and tool.description
        descriptor = tool.descriptor()
        assert descriptor["outputSchema"]["type"] == "object"
        assert descriptor["execution"]["taskSupport"] == "forbidden"


def test_mutating_mcp_tool_annotations_are_conservative() -> None:
    apply_patch = get_tool("apply_workflow_patch")
    assert apply_patch is not None
    apply_annotations = apply_patch.descriptor()["annotations"]
    assert apply_annotations["readOnlyHint"] is False
    assert apply_annotations["destructiveHint"] is True
    assert apply_annotations["requiresHumanApprovalHint"] is True
    apply_schema = apply_patch.descriptor()["inputSchema"]
    assert "approved_by_user" in apply_schema["properties"]

    set_packages = get_tool("set_environment_packages")
    assert set_packages is not None
    package_annotations = set_packages.descriptor()["annotations"]
    assert package_annotations["readOnlyHint"] is False
    assert package_annotations["destructiveHint"] is True
    assert package_annotations["openWorldHint"] is True
    assert package_annotations["requiresHumanApprovalHint"] is True
    package_schema = set_packages.descriptor()["inputSchema"]
    assert "approved_by_user" in package_schema["properties"]

    create_env = get_tool("create_environment")
    assert create_env is not None
    create_annotations = create_env.descriptor()["annotations"]
    assert create_annotations["readOnlyHint"] is False
    assert create_annotations["openWorldHint"] is True
    assert create_annotations["destructiveHint"] is False
    assert create_annotations["requiresHumanApprovalHint"] is True
    create_schema = create_env.descriptor()["inputSchema"]
    assert "approved_by_user" in create_schema["properties"]
