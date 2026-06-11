"""Phase 4 (A3): sub-workflow resolution through the engine callback."""

from noodle.engine.subworkflows import (
    InlineSubworkflow,
    SubworkflowCall,
    SubworkflowMeta,
    extract_leaf_value,
)


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
