"""Phase 4 (A3): sub-workflow resolution through the engine callback."""

import noodle_nodes  # noqa: F401 - registers execute_workflow / manual_trigger / code
from noodle.engine import execute
from noodle.engine.subworkflows import (
    InlineSubworkflow,
    SubworkflowCall,
    SubworkflowMeta,
    extract_leaf_value,
)
from noodle.models import WorkflowGraph
from noodle.sdk import registry


def test_extract_leaf_value_single_leaf():
    value = extract_leaf_value(
        sources={"t"},
        node_status={"t": "success", "c": "success"},
        node_outputs={"t": {"main": 1}, "c": {"main": 42}},
    )
    assert value == 42


def test_extract_leaf_value_multiple_leaves_keyed_by_id():
    value = extract_leaf_value(
        sources={"t"},
        node_status={"t": "success", "a": "success", "b": "success"},
        node_outputs={"a": {"main": 1}, "b": {"main": 2}},
    )
    assert value == {"a": 1, "b": 2}


def test_extract_leaf_value_no_successful_leaf_is_none():
    assert (
        extract_leaf_value(
            sources={"t"},
            node_status={"t": "success", "c": "error"},
            node_outputs={},
        )
        is None
    )


def test_call_payload_round_trip():
    call = SubworkflowCall(
        workflow_id="wf2",
        parameters={"x": 1},
        use_published=False,
        parent_run_id="run1",
        depth=2,
        call_chain=frozenset({"wf1", "wf2"}),
    )
    assert SubworkflowCall.from_payload(call.to_payload()) == call


def test_meta_payload_round_trip():
    meta = SubworkflowMeta(
        use_published=False,
        parent_run_id="run1",
        depth=1,
        call_chain=frozenset({"wf1"}),
        max_depth=5,
    )
    assert SubworkflowMeta.from_payload(meta.to_payload()) == meta


def test_meta_from_empty_payload_uses_defaults():
    meta = SubworkflowMeta.from_payload({})
    assert meta == SubworkflowMeta()
    assert meta.use_published is True and meta.depth == 0


# ---------------------------------------------------------------------------
# Engine wiring: execute(..., subworkflow_runner=..., subworkflow_meta=...)
# ---------------------------------------------------------------------------


def _parent_graph(sub_id: str = "wf-child") -> WorkflowGraph:
    return WorkflowGraph.model_validate({
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {"data": 7},
             "position": {"x": 0, "y": 0}},
            {"id": "sub", "type": "execute_workflow",
             "params": {"workflow_id": sub_id}, "position": {"x": 200, "y": 0}},
        ],
        "edges": [
            {"id": "e", "source": "t", "source_output": "main",
             "target": "sub", "target_input": "input"},
        ],
    })


CHILD_GRAPH = {
    "nodes": [
        {"id": "ct", "type": "manual_trigger", "params": {},
         "position": {"x": 0, "y": 0}},
        {"id": "cc", "type": "code", "params": {"code": "output = input['v'] * 2"},
         "position": {"x": 200, "y": 0}},
    ],
    "edges": [
        {"id": "ce", "source": "ct", "source_output": "main",
         "target": "cc", "target_input": "input"},
    ],
}


async def test_engine_runs_subworkflow_through_stub_resolver():
    """Master-plan acceptance: a sub-workflow exercised via a stub resolver."""
    seen: list[SubworkflowCall] = []

    async def resolver(call: SubworkflowCall):
        seen.append(call)
        return {"doubled": True}

    meta = SubworkflowMeta(
        use_published=False, parent_run_id="run-1",
        call_chain=frozenset({"wf-root"}),
    )
    result = await execute(
        _parent_graph(), registry,
        subworkflow_runner=resolver, subworkflow_meta=meta,
    )
    assert str(result.nodes["sub"].status) == "success"
    assert result.nodes["sub"].outputs["main"] == {"doubled": True}
    call = seen[0]
    assert call.workflow_id == "wf-child"
    assert call.parameters == 7
    assert call.use_published is False
    assert call.parent_run_id == "run-1"
    assert call.depth == 1
    assert call.call_chain == frozenset({"wf-root", "wf-child"})


async def test_engine_detects_cycle_from_meta_chain():
    async def resolver(call):  # pragma: no cover - must not be reached
        raise AssertionError("resolver must not run for a cyclic call")

    meta = SubworkflowMeta(call_chain=frozenset({"wf-child"}))
    result = await execute(
        _parent_graph(), registry,
        subworkflow_runner=resolver, subworkflow_meta=meta,
    )
    assert str(result.nodes["sub"].status) == "error"
    assert "cycle" in (result.nodes["sub"].error or "")


async def test_engine_enforces_depth_limit():
    async def resolver(call):  # pragma: no cover - must not be reached
        raise AssertionError("resolver must not run past the depth limit")

    meta = SubworkflowMeta(depth=3, max_depth=3)
    result = await execute(
        _parent_graph(), registry,
        subworkflow_runner=resolver, subworkflow_meta=meta,
    )
    assert str(result.nodes["sub"].status) == "error"
    assert "depth limit" in (result.nodes["sub"].error or "")


async def test_engine_executes_inline_directive():
    async def resolver(call: SubworkflowCall):
        return InlineSubworkflow(
            graph=CHILD_GRAPH,
            cache={"ct": {"main": {"v": call.parameters}}},
            targets=None,
            sources=("ct",),
        )

    result = await execute(
        _parent_graph(), registry,
        subworkflow_runner=resolver,
        subworkflow_meta=SubworkflowMeta(call_chain=frozenset({"wf-root"})),
    )
    assert str(result.nodes["sub"].status) == "success"
    assert result.nodes["sub"].outputs["main"] == 14  # 7 * 2


async def test_inline_child_nested_call_carries_extended_chain():
    """Nested calls inside inline children are allowed and chain-checked."""
    calls: list[SubworkflowCall] = []

    nested_child = {
        "nodes": [
            {"id": "ct", "type": "manual_trigger", "params": {},
             "position": {"x": 0, "y": 0}},
            {"id": "csub", "type": "execute_workflow",
             "params": {"workflow_id": "wf-grandchild"},
             "position": {"x": 200, "y": 0}},
        ],
        "edges": [
            {"id": "ce", "source": "ct", "source_output": "main",
             "target": "csub", "target_input": "input"},
        ],
    }

    async def resolver(call: SubworkflowCall):
        calls.append(call)
        if call.workflow_id == "wf-child":
            return InlineSubworkflow(
                graph=nested_child, cache={"ct": {"main": {}}},
                targets=None, sources=("ct",),
            )
        return "leaf"

    result = await execute(
        _parent_graph(), registry,
        subworkflow_runner=resolver,
        subworkflow_meta=SubworkflowMeta(call_chain=frozenset({"wf-root"})),
    )
    assert str(result.nodes["sub"].status) == "success"
    assert [c.workflow_id for c in calls] == ["wf-child", "wf-grandchild"]
    assert calls[1].depth == 2
    assert calls[1].call_chain == frozenset({"wf-root", "wf-child", "wf-grandchild"})
