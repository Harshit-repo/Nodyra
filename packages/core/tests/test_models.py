"""Tests for nodyra.models — Pydantic data envelopes and schema classes."""
from nodyra.models import (
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


# ── ParamSpec description fallback ─────────────────────────────────────────
#
# A tool param with no description reaches an LLM as a bare name. The fallback
# fills what can be filled from the param itself; the rule that matters is that
# it never invents anything.


def test_an_authored_description_is_never_overwritten():
    from nodyra.models import ParamSpec

    spec = ParamSpec(
        name="limit", description="Rows to keep after sorting.", choices=["1", "2"]
    )
    assert spec.description == "Rows to keep after sorting."


def test_choices_become_the_description():
    """Accurate by construction: it restates the param's own declared values."""
    from nodyra.models import ParamSpec

    spec = ParamSpec(name="operation", choices=["create", "get", "list", "delete"])
    assert spec.description == "One of: create, get, list, delete."


def test_choice_objects_render_their_values():
    from nodyra.models import ParamSpec

    spec = ParamSpec(
        name="mode", choices=[{"value": "fast", "label": "Fast"}, {"value": "slow"}]
    )
    assert spec.description == "One of: fast, slow."


def test_a_long_choice_list_is_truncated_not_dumped():
    """A hundred-entry enum in a description is noise to a model, not signal."""
    from nodyra.models import ParamSpec

    spec = ParamSpec(name="country", choices=[f"c{i}" for i in range(40)])
    assert spec.description.endswith(", ….")
    assert len(spec.description) < 200


def test_a_known_generic_name_gets_its_shared_meaning():
    from nodyra.models import ParamSpec

    assert ParamSpec(name="limit").description == "Maximum number of items to return."
    assert "owner/name" in ParamSpec(name="repo").description


def test_an_unknown_name_is_left_blank_rather_than_guessed():
    """The whole point. A plausible wrong description is worse than none,
    because the model believes it."""
    from nodyra.models import ParamSpec

    assert ParamSpec(name="widget_frobnicator").description == ""


def test_deliberately_ambiguous_names_are_not_in_the_vocabulary():
    """``state`` is an OAuth nonce on one node and an issue status on another;
    ``right`` is a join side or a boundary. One sentence cannot be true of
    both, so neither is documented generically."""
    from nodyra.models import GENERIC_PARAM_DOCS

    for ambiguous in ("state", "right", "flags", "type", "value", "key"):
        assert ambiguous not in GENERIC_PARAM_DOCS, (
            f"{ambiguous!r} means different things on different nodes; a shared "
            "description would be wrong somewhere"
        )


def test_choices_win_over_the_generic_vocabulary():
    """A param's own declared values are more specific than a shared sentence."""
    from nodyra.models import ParamSpec

    spec = ParamSpec(name="provider", choices=["openai", "anthropic"])
    assert spec.description == "One of: openai, anthropic."
