from noodle.models import CredentialSpec, PortDataKind
from noodle.sdk import NodeRegistry
from noodle_nodes.integrations_v2.node_factory import operation_source, trigger_source
from noodle_nodes.integrations_v2.registry import (
    execute_registered_operation,
    get_registered_provider_trigger,
    register_operation,
    register_provider_trigger,
    registered_operation_specs,
    registered_provider_trigger_specs,
    unregister_operation,
    unregister_provider_trigger,
)
from noodle_nodes.integrations_v2.specs import (
    OperationParamSpec,
    OperationSpec,
    ProviderTriggerSpec,
)


def setup_function() -> None:
    unregister_operation("google_sheets_append_v2_test")
    unregister_operation("google_sheets_read_v2_test")
    unregister_provider_trigger("github_repository_trigger_v2_test")


def teardown_function() -> None:
    unregister_operation("google_sheets_append_v2_test")
    unregister_operation("google_sheets_read_v2_test")
    unregister_provider_trigger("github_repository_trigger_v2_test")


def _append_spec() -> OperationSpec:
    return OperationSpec(
        node_id="google_sheets_append_v2_test",
        name="Google Sheets Append V2",
        provider="google_sheets",
        resource="values",
        operation="append",
        description="Append rows to a Google Sheet.",
        icon="sheet",
        params=(
            OperationParamSpec(
                name="credentials",
                type="credential",
                required=True,
                credential=CredentialSpec(
                    type="google_sheets_oauth2",
                    key="*",
                    label="Google Sheets OAuth2",
                    fields=["access_token", "refresh_token"],
                    multi=True,
                    test_service="google_sheets",
                ),
                required_scopes=("https://www.googleapis.com/auth/spreadsheets",),
            ),
            OperationParamSpec(
                name="spreadsheet_id",
                required=True,
                placeholder="Spreadsheet ID",
            ),
            OperationParamSpec(
                name="range_name",
                default="Sheet1!A:Z",
                group="Options",
            ),
            OperationParamSpec(
                name="value_input_option",
                default="USER_ENTERED",
                choices=("RAW", "USER_ENTERED"),
                group="Options",
            ),
        ),
    )


def test_operation_spec_registers_manifest_and_callable() -> None:
    calls: list[dict] = []

    def executor(**kwargs):
        calls.append(kwargs)
        return {"updatedRange": kwargs["range_name"]}

    registry = NodeRegistry()
    node_def = register_operation(_append_spec(), executor, node_registry=registry)

    manifest = node_def.manifest
    assert registry.get("google_sheets_append_v2_test") is node_def
    assert manifest.name == "Google Sheets Append V2"
    assert manifest.category == "Integrations"
    assert manifest.usable_as_tool is True
    assert manifest.tool_side_effecting is True
    assert manifest.inputs[0].data_kind == PortDataKind.main
    assert manifest.outputs[0].data_kind == PortDataKind.main

    params = {param.name: param for param in manifest.params}
    assert params["credentials"].credential is not None
    assert params["credentials"].credential.type == "google_sheets_oauth2"
    assert params["credentials"].required_scopes == [
        "https://www.googleapis.com/auth/spreadsheets"
    ]
    assert params["value_input_option"].group == "Options"

    result = node_def.func(
        input={"row": 1},
        credentials={"access_token": "secret"},
        spreadsheet_id="sheet-id",
        range_name="Sheet1!A:B",
        value_input_option="RAW",
    )

    assert result == {"updatedRange": "Sheet1!A:B"}
    assert calls == [
        {
            "input": {"row": 1},
            "credentials": {"access_token": "secret"},
            "spreadsheet_id": "sheet-id",
            "range_name": "Sheet1!A:B",
            "value_input_option": "RAW",
        }
    ]


def test_operation_spec_can_mark_read_only_tool() -> None:
    spec = OperationSpec(
        node_id="google_sheets_read_v2_test",
        name="Google Sheets Read V2",
        provider="google_sheets",
        resource="values",
        operation="read",
        description="Read rows from a Google Sheet.",
        icon="sheet",
        tool_side_effecting=False,
        params=(
            OperationParamSpec(name="spreadsheet_id", required=True),
            OperationParamSpec(name="range_name", default="Sheet1!A:Z"),
        ),
    )

    node_def = register_operation(
        spec,
        lambda **kwargs: kwargs,
        node_registry=NodeRegistry(),
    )

    assert node_def.manifest.usable_as_tool is True
    assert node_def.manifest.tool_side_effecting is False


def test_registered_operation_can_execute_by_node_id() -> None:
    def executor(**kwargs):
        return {"received": kwargs["spreadsheet_id"]}

    register_operation(_append_spec(), executor, node_registry=NodeRegistry())

    assert execute_registered_operation(
        "google_sheets_append_v2_test",
        input=None,
        credentials={},
        spreadsheet_id="sheet-id",
        range_name="Sheet1",
        value_input_option="RAW",
    ) == {"received": "sheet-id"}
    operation_keys = {spec.operation_key for spec in registered_operation_specs()}
    assert "google_sheets.values.append" in operation_keys


def test_operation_source_is_explicit_python() -> None:
    source = operation_source(_append_spec())

    assert "def google_sheets_append_v2_test(" in source
    assert "credentials=None" in source
    assert "spreadsheet_id=None" in source
    assert "value_input_option='USER_ENTERED'" in source
    assert "execute_registered_operation" in source
    assert "google_sheets.values.append" in source


def _github_trigger_spec() -> ProviderTriggerSpec:
    return ProviderTriggerSpec(
        node_id="github_repository_trigger_v2_test",
        name="GitHub Repository Trigger V2 Test",
        provider="github",
        resource="repository",
        event="webhook",
        icon="github",
        params=(
            OperationParamSpec(name="owner", required=True),
            OperationParamSpec(name="repo", required=True),
        ),
    )


def test_provider_trigger_spec_registers_manifest() -> None:
    registry = NodeRegistry()
    node_def = register_provider_trigger(_github_trigger_spec(), node_registry=registry)

    manifest = node_def.manifest
    assert registry.get("github_repository_trigger_v2_test") is node_def
    assert manifest.role.value == "trigger"
    assert manifest.usable_as_tool is False
    assert manifest.inputs == []
    assert manifest.outputs[0].data_kind == PortDataKind.main
    assert manifest.params[0].name == "owner"

    registered = get_registered_provider_trigger("github_repository_trigger_v2_test")
    assert registered.spec.trigger_key == "github.repository.webhook"
    assert "github.repository.webhook" in {
        spec.trigger_key for spec in registered_provider_trigger_specs()
    }


def test_provider_trigger_source_is_explicit_python() -> None:
    source = trigger_source(_github_trigger_spec())

    assert "def github_repository_trigger_v2_test(" in source
    assert "owner=None" in source
    assert "repo=None" in source
    assert "return {}" in source


def test_operation_manifest_defaults_to_empty_requirements():
    from noodle_nodes.integrations_v2.node_factory import operation_manifest
    from noodle_nodes.integrations_v2.specs import OperationSpec

    spec = OperationSpec(
        node_id="acme.thing.do",
        name="Acme Do",
        provider="acme",
        resource="thing",
        operation="do",
    )
    assert operation_manifest(spec).requirements == []


def test_operation_manifest_passes_requirements_through():
    from noodle_nodes.integrations_v2.node_factory import operation_manifest
    from noodle_nodes.integrations_v2.specs import OperationSpec

    spec = OperationSpec(
        node_id="acme.thing.do",
        name="Acme Do",
        provider="acme",
        resource="thing",
        operation="do",
        requirements=("acme-sdk>=2",),
    )
    assert operation_manifest(spec).requirements == ["acme-sdk>=2"]
