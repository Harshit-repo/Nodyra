"""Engine-level loop region detection, validation, and execution."""
from __future__ import annotations

import pytest

import noodle_nodes  # noqa: F401 - registers loop_start/loop_end/code
from noodle.artifacts import LocalArtifactStore
from noodle.context import artifact_store, current_node_id
from noodle.engine import _loop_items, _loop_regions, _validate_loop_regions, execute
from noodle.models import WorkflowGraph
from noodle.sdk import registry


@pytest.fixture
def store_ctx(tmp_path):
    """Provide an artifact-store context so dataset writes have somewhere to go."""
    store = LocalArtifactStore(tmp_path, run_id="test-run")
    a = artifact_store.set(store)
    n = current_node_id.set("test-node")
    yield
    current_node_id.reset(n)
    artifact_store.reset(a)


def _g(nodes, edges) -> WorkflowGraph:
    return WorkflowGraph.model_validate({"nodes": nodes, "edges": edges})


def _n(nid, ntype, params=None):
    return {"id": nid, "type": ntype, "params": params or {}, "position": {"x": 0, "y": 0}}


def _e(src, tgt, src_out="main", tgt_in="input"):
    return {"id": f"{src}->{tgt}", "source": src, "source_output": src_out,
            "target": tgt, "target_input": tgt_in}


def test_loop_items_each_passthrough():
    assert _loop_items([1, 2, 3], mode="each", max_rows=100) == [1, 2, 3]
    assert _loop_items(None, mode="each", max_rows=100) == []
    assert _loop_items(7, mode="each", max_rows=100) == [7]


def test_loop_items_batch_chunks_with_short_tail():
    assert _loop_items([1, 2, 3, 4, 5], mode="batch", batch_size=2, max_rows=100) == [
        [1, 2], [3, 4], [5],
    ]


def test_loop_items_group_by_key_stable_order():
    rows = [
        {"c": "a", "v": 1},
        {"c": "b", "v": 2},
        {"c": "a", "v": 3},
        {"c": "b", "v": 4},
    ]
    units = _loop_items(rows, mode="group", group_key="c", max_rows=100)
    assert [u["key"] for u in units] == ["a", "b"]
    assert units[0]["rows"] == [{"c": "a", "v": 1}, {"c": "a", "v": 3}]
    assert units[1]["rows"] == [{"c": "b", "v": 2}, {"c": "b", "v": 4}]


def test_loop_items_range_counts_from_zero():
    assert _loop_items(None, mode="range", count=3, max_rows=100) == [0, 1, 2]
    assert _loop_items(None, mode="range", count=0, max_rows=100) == []


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


async def test_loop_concurrency_preserves_order():
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [0, 1, 2, 3, 4]}),
            _n("s", "loop_start", {"concurrency": 3}),
            _n("b", "code", {"code": "output = input"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )
    result = await execute(g, registry)
    assert result.nodes["e"].outputs["results"] == [0, 1, 2, 3, 4]


async def test_loop_empty_input_yields_empty_results():
    # manual_trigger collapses a falsy [] to {}, so produce a real empty list
    # from a code node to exercise the zero-iteration path.
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": {"go": 1}}),
            _n("src", "code", {"code": "output = []"}),
            _n("s", "loop_start"),
            _n("b", "code", {"code": "output = input"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [
            _e("trig", "src"), _e("src", "s"),
            _e("s", "b", src_out="item"), _e("b", "e"),
        ],
    )
    result = await execute(g, registry)
    assert str(result.nodes["e"].status) == "success"
    assert result.nodes["e"].outputs["results"] == []


async def test_nested_loops_flatten_correctly():
    # outer items [[1,2],[3]] ; inner doubles each -> results [[2,4],[6]]
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [[1, 2], [3]]}),
            _n("s1", "loop_start"),
            _n("s2", "loop_start"),
            _n("b", "code", {"code": "output = input * 2"}),
            _n("e2", "loop_end", {"loop_start_id": "s2"}),
            _n("e1", "loop_end", {"loop_start_id": "s1"}),
        ],
        [
            _e("trig", "s1"),
            _e("s1", "s2", src_out="item"),       # outer item (a sublist) -> inner loop input
            _e("s2", "b", src_out="item"),
            _e("b", "e2"),
            _e("e2", "e1", src_out="results"),    # inner results -> outer collected value
        ],
    )
    result = await execute(g, registry)
    assert str(result.nodes["e1"].status) == "success"
    assert result.nodes["e1"].outputs["results"] == [[2, 4], [6]]


async def test_loop_output_mode_dataset_returns_ref(store_ctx):
    from noodle.datasets import is_dataset_ref
    from noodle_nodes.datasets import dataset_to_records  # materializes back to rows
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [10, 20]}),
            _n("s", "loop_start"),
            _n("b", "code", {"code": "output = {'v': input}"}),
            _n("e", "loop_end", {"loop_start_id": "s", "output_mode": "dataset"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )
    result = await execute(g, registry)
    ref = result.nodes["e"].outputs["results"]
    assert is_dataset_ref(ref)
    rows = dataset_to_records(input=ref, max_rows=10)
    assert sorted(r["v"] for r in rows) == [10, 20]


async def test_batch_mode_maps_over_chunks():
    # batch_size=2 over [1..5]; body doubles each element of the batch.
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [1, 2, 3, 4, 5]}),
            _n("s", "loop_start", {"mode": "batch", "batch_size": 2}),
            _n("b", "code", {"code": "output = [x * 2 for x in input]"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )
    result = await execute(g, registry)
    assert str(result.nodes["e"].status) == "success"
    assert result.nodes["e"].outputs["results"] == [[2, 4], [6, 8], [10]]


async def test_range_mode_loops_count_times():
    g = _g(
        [
            _n("s", "loop_start", {"mode": "range", "count": 4}),
            _n("b", "code", {"code": "output = input * input"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("s", "b", src_out="item"), _e("b", "e")],
    )
    result = await execute(g, registry)
    assert str(result.nodes["e"].status) == "success"
    assert result.nodes["e"].outputs["results"] == [0, 1, 4, 9]


async def test_group_mode_iterates_per_key():
    rows = [
        {"c": "a", "v": 1},
        {"c": "b", "v": 2},
        {"c": "a", "v": 3},
    ]
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": {"rows": rows}}),
            _n("src", "code", {"code": "output = input['rows']"}),
            _n("s", "loop_start", {"mode": "group", "group_key": "c"}),
            _n("b", "code", {"code": "output = {'key': input['key'], 'n': len(input['rows'])}"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [
            _e("trig", "src"), _e("src", "s"),
            _e("s", "b", src_out="item"), _e("b", "e"),
        ],
    )
    result = await execute(g, registry)
    assert str(result.nodes["e"].status) == "success"
    assert result.nodes["e"].outputs["results"] == [
        {"key": "a", "n": 2},
        {"key": "b", "n": 1},
    ]


async def test_loop_events_are_iteration_tagged():
    events: list[dict] = []

    async def collect(ev):
        events.append(ev)

    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [1, 2, 3]}),
            _n("s", "loop_start"),
            _n("b", "code", {"code": "output = input * 2"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )
    await execute(g, registry, on_event=collect)
    paths = sorted(
        ev.get("iteration_path")
        for ev in events
        if ev.get("type") == "node_finished" and ev.get("node_id") == "b"
    )
    assert paths == [[0], [1], [2]]
    e_paths = [
        ev.get("iteration_path")
        for ev in events
        if ev.get("type") == "node_finished" and ev.get("node_id") == "e"
    ]
    assert e_paths == [None]  # loop_end runs at parent scope


async def test_nested_loop_events_carry_full_path():
    events: list[dict] = []

    async def collect(ev):
        events.append(ev)

    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [[1, 2], [3]]}),
            _n("s1", "loop_start"),
            _n("s2", "loop_start"),
            _n("b", "code", {"code": "output = input * 2"}),
            _n("e2", "loop_end", {"loop_start_id": "s2"}),
            _n("e1", "loop_end", {"loop_start_id": "s1"}),
        ],
        [
            _e("trig", "s1"),
            _e("s1", "s2", src_out="item"),
            _e("s2", "b", src_out="item"),
            _e("b", "e2"),
            _e("e2", "e1", src_out="results"),
        ],
    )
    await execute(g, registry, on_event=collect)
    paths = sorted(
        ev.get("iteration_path")
        for ev in events
        if ev.get("type") == "node_finished" and ev.get("node_id") == "b"
    )
    assert paths == [[0, 0], [0, 1], [1, 0]]
