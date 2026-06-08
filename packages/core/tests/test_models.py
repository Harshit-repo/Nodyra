"""Tests for noodle.models — Pydantic data envelopes and schema classes."""
from noodle.models import (
    BinaryRef,
    CredentialSpec,
    Edge,
    GraphNode,
    ItemMeta,
    NodeManifest,
    NodeRole,
    NodeRunResult,
    NodeStatus,
    NoodleItem,
    ParamSpec,
    PortDataKind,
    PortSpec,
    Position,
    RunResult,
    RunStatus,
    WorkflowGraph,
)


# ---------------------------------------------------------------------------
# NoodleItem.wrap
# ---------------------------------------------------------------------------

def test_wrap_plain_value_produces_noodle_item() -> None:
    item = NoodleItem.wrap(42)
    assert isinstance(item, NoodleItem)
    assert item.json == 42


def test_wrap_already_noodle_item_returns_same_object() -> None:
    original = NoodleItem(json="hello")
    assert NoodleItem.wrap(original) is original


def test_wrap_sets_meta_fields() -> None:
    item = NoodleItem.wrap("val", source_node="n1", source_output="out", item_index=3)
    assert item.meta.source_node == "n1"
    assert item.meta.source_output == "out"
    assert item.meta.item_index == 3


def test_wrap_none_is_valid() -> None:
    item = NoodleItem.wrap(None)
    assert item.json is None


def test_wrap_dict_payload() -> None:
    item = NoodleItem.wrap({"key": "val", "num": 1})
    assert item.json == {"key": "val", "num": 1}


def test_unwrap_returns_json_payload() -> None:
    item = NoodleItem.wrap({"answer": 42})
    assert item.unwrap() == {"answer": 42}


# ---------------------------------------------------------------------------
# NoodleItem.wrap_list
# ---------------------------------------------------------------------------

def test_wrap_list_of_scalars() -> None:
    items = NoodleItem.wrap_list([1, 2, 3])
    assert len(items) == 3
    assert all(isinstance(i, NoodleItem) for i in items)
    assert [i.json for i in items] == [1, 2, 3]


def test_wrap_list_assigns_sequential_item_index() -> None:
    items = NoodleItem.wrap_list([10, 20, 30])
    assert [i.meta.item_index for i in items] == [0, 1, 2]


def test_wrap_list_of_already_wrapped_items() -> None:
    raw = [NoodleItem(json="a"), NoodleItem(json="b")]
    items = NoodleItem.wrap_list(raw)
    assert len(items) == 2
    assert items[0].json == "a"
    assert items[1].json == "b"


def test_wrap_list_single_non_list_value_becomes_one_item() -> None:
    items = NoodleItem.wrap_list("hello")
    assert len(items) == 1
    assert items[0].json == "hello"


def test_wrap_list_empty_list_returns_empty() -> None:
    assert NoodleItem.wrap_list([]) == []


def test_wrap_list_propagates_source_meta() -> None:
    items = NoodleItem.wrap_list([1, 2], source_node="src", source_output="main")
    assert all(i.meta.source_node == "src" for i in items)
    assert all(i.meta.source_output == "main" for i in items)


# ---------------------------------------------------------------------------
# PortDataKind enum
# ---------------------------------------------------------------------------

def test_port_data_kind_string_values() -> None:
    assert PortDataKind.any == "any"
    assert PortDataKind.dataset == "dataset"
    assert PortDataKind.artifact == "artifact"
    assert PortDataKind.ai_language_model == "ai_language_model"
    assert PortDataKind.ai_tool == "ai_tool"


# ---------------------------------------------------------------------------
# NodeManifest defaults and construction
# ---------------------------------------------------------------------------

def test_node_manifest_minimal_construction() -> None:
    m = NodeManifest(id="my_node", name="My Node")
    assert m.category == "General"
    assert m.version == "1.0.0"
    assert m.role == NodeRole.executable
    assert m.hidden is False
    assert m.deprecated is False
    assert m.usable_as_tool is False
    assert m.inputs == []
    assert m.outputs == []
    assert m.params == []
    assert m.requirements == []


def test_node_manifest_with_ports() -> None:
    m = NodeManifest(
        id="transform",
        name="Transform",
        inputs=[PortSpec(name="input", data_kind=PortDataKind.main)],
        outputs=[PortSpec(name="output", data_kind=PortDataKind.dataset)],
    )
    assert m.inputs[0].name == "input"
    assert m.outputs[0].data_kind == PortDataKind.dataset


def test_node_manifest_tool_role() -> None:
    m = NodeManifest(id="tool_node", name="Tool", role=NodeRole.tool, usable_as_tool=True)
    assert m.role == NodeRole.tool
    assert m.usable_as_tool is True


def test_node_manifest_param_output_kinds() -> None:
    m = NodeManifest(
        id="n",
        name="N",
        param_output_kinds={"main": {"param": "as_dataset", "true": "dataset", "false": "any"}},
    )
    assert "main" in m.param_output_kinds


# ---------------------------------------------------------------------------
# WorkflowGraph
# ---------------------------------------------------------------------------

def test_workflow_graph_starts_empty() -> None:
    g = WorkflowGraph()
    assert g.nodes == []
    assert g.edges == []


def test_workflow_graph_with_nodes_and_edges() -> None:
    g = WorkflowGraph(
        nodes=[GraphNode(id="a", type="start"), GraphNode(id="b", type="transform")],
        edges=[Edge(source="a", target="b")],
    )
    assert len(g.nodes) == 2
    assert g.edges[0].source == "a"
    assert g.edges[0].target == "b"


def test_graph_node_error_handling_defaults() -> None:
    n = GraphNode(id="n1", type="code")
    assert n.on_error == "stop"
    assert n.retry_on_fail is False
    assert n.retries == 1
    assert n.retry_wait_seconds == 0.0
    assert n.retry_backoff is False
    assert n.always_output_data is False


def test_graph_node_tool_mode_defaults() -> None:
    n = GraphNode(id="n2", type="llm")
    assert n.tool_mode is False
    assert n.tool_name is None
    assert n.tool_description == ""


def test_edge_default_port_names() -> None:
    e = Edge(source="a", target="b")
    assert e.source_output == "main"
    assert e.target_input == "input"


def test_edge_custom_ports() -> None:
    e = Edge(source="a", source_output="items", target="b", target_input="list")
    assert e.source_output == "items"
    assert e.target_input == "list"


# ---------------------------------------------------------------------------
# CredentialSpec and ParamSpec
# ---------------------------------------------------------------------------

def test_credential_spec_defaults() -> None:
    c = CredentialSpec()
    assert c.type == "generic"
    assert c.key == "value"
    assert c.multi is False
    assert c.test_service is None
    assert c.fields == []


def test_param_spec_with_credential() -> None:
    p = ParamSpec(name="api_key", credential=CredentialSpec(type="github", key="token"))
    assert p.credential is not None
    assert p.credential.type == "github"


def test_param_spec_required_default_false() -> None:
    p = ParamSpec(name="opt")
    assert p.required is False
    assert p.default is None


def test_param_spec_choices() -> None:
    p = ParamSpec(name="mode", choices=["a", "b", "c"])
    assert p.choices == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# BinaryRef
# ---------------------------------------------------------------------------

def test_binary_ref_defaults() -> None:
    b = BinaryRef()
    assert b.mime_type == "application/octet-stream"
    assert b.file_name == ""
    assert b.storage_key == ""
    assert b.inline_b64 is None
    assert b.byte_size == 0


def test_noodle_item_carries_binary_data() -> None:
    item = NoodleItem(
        json=None,
        binary_data={"image": BinaryRef(mime_type="image/png", byte_size=1024)},
    )
    assert "image" in item.binary_data
    assert item.binary_data["image"].byte_size == 1024


# ---------------------------------------------------------------------------
# RunResult / NodeRunResult
# ---------------------------------------------------------------------------

def test_run_result_defaults() -> None:
    r = RunResult(status=RunStatus.success)
    assert r.nodes == {}


def test_node_run_result_defaults() -> None:
    nr = NodeRunResult(node_id="n1", status=NodeStatus.success)
    assert nr.outputs == {}
    assert nr.error is None
    assert nr.logs == []
    assert nr.node_type_version == "1.0.0"


def test_position_defaults() -> None:
    p = Position()
    assert p.x == 0.0
    assert p.y == 0.0
