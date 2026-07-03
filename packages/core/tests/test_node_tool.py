import asyncio

from nodyra.models import GraphNode
from nodyra.node_tool import TOOL_MODE_OUTPUT, build_node_tool_adapter
from nodyra.sdk import NodeRegistry, node


def _registry_with_echo() -> NodeRegistry:
    reg = NodeRegistry()

    @node(name="Echo Upper", id="echo_upper", registry=reg,
          params={"text": {}, "prefix": {}})
    def echo_upper(text: str = "", prefix: str = "") -> dict:
        return {"shout": prefix + text.upper()}

    return reg


def test_tool_mode_output_constant() -> None:
    assert TOOL_MODE_OUTPUT == "tool"


def test_build_adapter_derives_schema_from_from_ai_params() -> None:
    reg = _registry_with_echo()
    gnode = GraphNode(
        id="t", type="echo_upper", tool_mode=True,
        tool_name="shout", tool_description="Shout text",
        params={
            "prefix": ">> ",  # Fixed
            "text": "{{ $fromAI('text', 'what to shout', 'string') }}",  # From-AI
        },
    )
    adapter = build_node_tool_adapter(reg.get("echo_upper"), gnode)
    assert adapter.schema.name == "shout"
    assert adapter.schema.description == "Shout text"
    assert list(adapter.schema.parameters.properties.keys()) == ["text"]
    assert adapter.schema.parameters.required == ["text"]
    assert adapter.side_effecting is True  # default conservative


def test_adapter_invoke_runs_node_with_fixed_plus_ai_args() -> None:
    reg = _registry_with_echo()
    gnode = GraphNode(
        id="t", type="echo_upper", tool_mode=True,
        params={
            "prefix": ">> ",
            "text": "{{ $fromAI('text', 'what to shout', 'string') }}",
        },
    )
    adapter = build_node_tool_adapter(reg.get("echo_upper"), gnode)
    out = asyncio.run(adapter.invoke_async({"text": "hello"}))
    # Output is JSON-encoded since the node returns a dict.
    assert out == '{"shout": ">> HELLO"}'


def test_adapter_infers_blank_core_param_when_no_from_ai_bindings() -> None:
    reg = _registry_with_echo()
    gnode = GraphNode(
        id="t",
        type="echo_upper",
        tool_mode=True,
        params={"prefix": ">> ", "text": ""},
    )
    adapter = build_node_tool_adapter(reg.get("echo_upper"), gnode)

    assert list(adapter.schema.parameters.properties.keys()) == ["text"]
    assert adapter.schema.parameters.required == ["text"]

    out = asyncio.run(adapter.invoke_async({"text": "hello"}))
    assert out == '{"shout": ">> HELLO"}'
