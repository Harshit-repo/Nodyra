"""Engine-level loop region detection, validation, and execution."""
from __future__ import annotations

import pytest

import noodle_nodes  # noqa: F401 - registers loop_start/loop_end/code
from noodle.engine import _loop_regions, _validate_loop_regions, execute
from noodle.models import WorkflowGraph
from noodle.sdk import registry


def _g(nodes, edges) -> WorkflowGraph:
    return WorkflowGraph.model_validate({"nodes": nodes, "edges": edges})


def _n(nid, ntype, params=None):
    return {"id": nid, "type": ntype, "params": params or {}, "position": {"x": 0, "y": 0}}


def _e(src, tgt, src_out="main", tgt_in="input"):
    return {"id": f"{src}->{tgt}", "source": src, "source_output": src_out,
            "target": tgt, "target_input": tgt_in}


def test_loop_regions_simple_body():
    # start -> body(code) -> end
    g = _g(
        [
            _n("s", "loop_start"),
            _n("b", "code", {"code": "output = input"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [
            _e("s", "b", src_out="item"),
            _e("b", "e"),
        ],
    )
    regions = _loop_regions(g)
    assert set(regions.keys()) == {"s"}
    r = regions["s"]
    assert r.start_id == "s"
    assert r.end_id == "e"
    assert r.body_ids == {"b"}


def test_loop_regions_branched_body():
    # s -> a -> {b, c} -> m -> e   (branch + merge inside the loop)
    g = _g(
        [
            _n("s", "loop_start"),
            _n("a", "code", {"code": "output = input"}),
            _n("b", "code", {"code": "output = input"}),
            _n("c", "code", {"code": "output = input"}),
            _n("m", "merge"),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [
            _e("s", "a", src_out="item"), _e("a", "b"), _e("a", "c"),
            _e("b", "m", tgt_in="input_a"), _e("c", "m", tgt_in="input_b"),
            _e("m", "e"),
        ],
    )
    r = _loop_regions(g)["s"]
    assert r.body_ids == {"a", "b", "c", "m"}


def test_loop_regions_nesting_parent_link():
    # outer s1 ... inner s2/e2 ... e1
    g = _g(
        [
            _n("s1", "loop_start"),
            _n("s2", "loop_start"),
            _n("b", "code", {"code": "output = input"}),
            _n("e2", "loop_end", {"loop_start_id": "s2"}),
            _n("e1", "loop_end", {"loop_start_id": "s1"}),
        ],
        [
            _e("s1", "s2", src_out="item", tgt_in="input"),
            _e("s2", "b", src_out="item"),
            _e("b", "e2"),
            _e("e2", "e1", src_out="results"),
        ],
    )
    regions = _loop_regions(g)
    assert regions["s2"].parent_start_id == "s1"
    assert regions["s1"].parent_start_id is None
    assert "s2" in regions["s1"].body_ids and "b" in regions["s1"].body_ids


def test_validate_rejects_edge_crossing_into_body_from_outside():
    # 'x' (outside) wires directly into body node 'b' — not allowed.
    g = _g(
        [
            _n("s", "loop_start"), _n("x", "code", {"code": "output = 1"}),
            _n("b", "merge"),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [
            _e("s", "b", src_out="item", tgt_in="input_a"),
            _e("x", "b", tgt_in="input_b"),
            _e("b", "e"),
        ],
    )
    with pytest.raises(Exception) as ei:
        _validate_loop_regions(g, _loop_regions(g))
    assert "single entry" in str(ei.value).lower() or "outside" in str(ei.value).lower()


def test_validate_rejects_body_node_leaking_out():
    # body node 'b' wires to 'y' outside the region (not via loop_end).
    g = _g(
        [
            _n("s", "loop_start"),
            _n("b", "code", {"code": "output = input"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
            _n("y", "code", {"code": "output = input"}),
        ],
        [
            _e("s", "b", src_out="item"), _e("b", "e"), _e("b", "y"),
        ],
    )
    with pytest.raises(Exception) as ei:
        _validate_loop_regions(g, _loop_regions(g))
    assert "single exit" in str(ei.value).lower() or "outside" in str(ei.value).lower()


def test_validate_rejects_missing_pair():
    g = _g([_n("e", "loop_end", {"loop_start_id": "nope"})], [])
    # missing start is fine (no loop_start present); a loop_start without an end raises.
    g2 = _g([_n("s", "loop_start")], [])
    with pytest.raises(Exception):
        _loop_regions(g2)


def test_validate_accepts_well_nested():
    g = _g(
        [
            _n("s1", "loop_start"), _n("s2", "loop_start"),
            _n("b", "code", {"code": "output = input"}),
            _n("e2", "loop_end", {"loop_start_id": "s2"}),
            _n("e1", "loop_end", {"loop_start_id": "s1"}),
        ],
        [
            _e("s1", "s2", src_out="item"), _e("s2", "b", src_out="item"),
            _e("b", "e2"), _e("e2", "e1", src_out="results"),
        ],
    )
    _validate_loop_regions(g, _loop_regions(g))  # must not raise


async def test_loop_runs_body_once_per_item_in_order():
    # items [1,2,3] -> body doubles -> results [2,4,6]
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [1, 2, 3]}),
            _n("s", "loop_start"),
            _n("b", "code", {"code": "output = input * 2"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [
            _e("trig", "s"),
            _e("s", "b", src_out="item"),
            _e("b", "e"),
        ],
    )
    result = await execute(g, registry)
    assert str(result.nodes["e"].status) == "success"
    assert result.nodes["e"].outputs["results"] == [2, 4, 6]
    assert result.nodes["e"].outputs["errors"] == []


async def test_loop_on_error_continue_collects_errors():
    # row value 2 raises in the body; continue -> results [1,3] + 1 error
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [1, 2, 3]}),
            _n("s", "loop_start", {"on_error": "continue"}),
            _n("b", "code", {"code": "assert input != 2, 'boom'\noutput = input"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )
    result = await execute(g, registry)
    e = result.nodes["e"]
    assert str(e.status) == "success"
    assert sorted(e.outputs["results"]) == [1, 3]
    assert len(e.outputs["errors"]) == 1
    assert e.outputs["errors"][0]["index"] == 1


async def test_loop_on_error_fail_aborts():
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [1, 2, 3]}),
            _n("s", "loop_start", {"on_error": "fail"}),
            _n("b", "code", {"code": "assert input != 2, 'boom'\noutput = input"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )
    result = await execute(g, registry)
    assert str(result.nodes["e"].status) == "error"
    assert str(result.status) == "error"
