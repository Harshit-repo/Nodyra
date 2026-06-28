"""Tests for noodle.models — Pydantic data envelopes and schema classes."""
from noodle.models import (
    CredentialSpec,
    Edge,
    GraphNode,
    NodeManifest,
    NodeRole,
    NodeRunResult,
    NodeStatus,
    ParamSpec,
    PortDataKind,
    PortSpec,
    Position,
    RunResult,
    RunStatus,
    WorkflowGraph,
)


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
