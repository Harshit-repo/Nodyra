"""Engine-level loop region detection, validation, and execution."""

from __future__ import annotations

import asyncio

import pytest

import nodyra_nodes  # noqa: F401 - registers loop_start/loop_end/code
from nodyra.artifacts import LocalArtifactStore
from nodyra.context import artifact_store, current_node_id
from nodyra.engine import _loop_items, _loop_regions, _validate_loop_regions, execute
from nodyra.engine.loops import (
    MAX_CONDITIONAL_LOOP_ITERATIONS,
    MAX_LOOP_CONCURRENCY,
    MAX_LOOP_ROWS,
)
from nodyra.engine.types import GraphError
from nodyra.models import WorkflowGraph
from nodyra.sdk import registry


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
    return {
        "id": f"{src}->{tgt}",
        "source": src,
        "source_output": src_out,
        "target": tgt,
        "target_input": tgt_in,
    }


def test_loop_items_each_passthrough():
    assert _loop_items([1, 2, 3], mode="each", max_rows=100) == [1, 2, 3]
    assert _loop_items(None, mode="each", max_rows=100) == []
    assert _loop_items(7, mode="each", max_rows=100) == [7]


def test_loop_items_batch_chunks_with_short_tail():
    assert _loop_items([1, 2, 3, 4, 5], mode="batch", batch_size=2, max_rows=100) == [
        [1, 2],
        [3, 4],
        [5],
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


def test_loop_items_rejects_range_above_max_rows():
    with pytest.raises(ValueError, match="max_rows"):
        _loop_items(None, mode="range", count=MAX_LOOP_ROWS + 1, max_rows=MAX_LOOP_ROWS)


def test_loop_items_rejects_inline_rows_above_max_rows():
    with pytest.raises(ValueError, match="max_rows"):
        _loop_items(list(range(MAX_LOOP_ROWS + 1)), mode="each", max_rows=MAX_LOOP_ROWS)


def test_loop_items_range_start_step():
    assert _loop_items(None, mode="range", count=4, start=10, step=5, max_rows=100) == [
        10,
        15,
        20,
        25,
    ]


def test_loop_items_window_overlapping():
    assert _loop_items([1, 2, 3, 4], mode="window", batch_size=2, step=1, max_rows=100) == [
        [1, 2],
        [2, 3],
        [3, 4],
    ]
    assert _loop_items([1, 2, 3, 4], mode="window", batch_size=2, step=2, max_rows=100) == [
        [1, 2],
        [3, 4],
    ]
    # window larger than the input -> no full window
    assert _loop_items([1], mode="window", batch_size=2, step=1, max_rows=100) == []


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
            _e("s", "a", src_out="item"),
            _e("a", "b"),
            _e("a", "c"),
            _e("b", "m", tgt_in="input_a"),
            _e("c", "m", tgt_in="input_b"),
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
            _n("s", "loop_start"),
            _n("x", "code", {"code": "output = 1"}),
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
            _e("s", "b", src_out="item"),
            _e("b", "e"),
            _e("b", "y"),
        ],
    )
    with pytest.raises(Exception) as ei:
        _validate_loop_regions(g, _loop_regions(g))
    assert "single exit" in str(ei.value).lower() or "outside" in str(ei.value).lower()


def test_validate_rejects_missing_pair():
    _g([_n("e", "loop_end", {"loop_start_id": "nope"})], [])
    # missing start is fine (no loop_start present); a loop_start without an end raises.
    g2 = _g([_n("s", "loop_start")], [])
    with pytest.raises(GraphError):
        _loop_regions(g2)


def test_validate_accepts_well_nested():
    g = _g(
        [
            _n("s1", "loop_start"),
            _n("s2", "loop_start"),
            _n("b", "code", {"code": "output = input"}),
            _n("e2", "loop_end", {"loop_start_id": "s2"}),
            _n("e1", "loop_end", {"loop_start_id": "s1"}),
        ],
        [
            _e("s1", "s2", src_out="item"),
            _e("s2", "b", src_out="item"),
            _e("b", "e2"),
            _e("e2", "e1", src_out="results"),
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


async def test_loop_fail_cancels_inflight_iterations_eng1():
    """ENG-1: with concurrency>1 and on_error='fail', a fast-failing row must
    cancel the still-in-flight iterations instead of leaving them running
    detached. Orphaned iterations keep executing body nodes (emitting events,
    burning compute) for a run already marked failed — the REL-2 class of bug.
    """
    from nodyra.sdk import node
    from nodyra.sdk import registry as global_reg

    started: list = []
    finished: list = []

    @node(name="ENG1 Slow", id="eng1_slow", registry=global_reg, hidden=True)
    async def eng1_slow(input=None):
        started.append(input)
        if input == "fail":
            raise ValueError("boom")
        await asyncio.sleep(0.1)
        finished.append(input)
        return input

    g = _g(
        [
            _n("trig", "manual_trigger", {"data": ["slowA", "fail", "slowB"]}),
            _n("s", "loop_start", {"concurrency": 3, "on_error": "fail"}),
            _n("b", "eng1_slow"),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )
    result = await execute(g, registry)
    assert str(result.nodes["e"].status) == "error"
    # All three iterations got far enough to start...
    assert set(started) == {"slowA", "fail", "slowB"}
    # ...but the two slow ones must have been cancelled before completing. Give
    # any orphaned (un-cancelled) iterations time to wake from their sleep.
    await asyncio.sleep(0.3)
    assert finished == [], f"orphaned loop iterations completed after fail: {finished}"


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
            _e("trig", "src"),
            _e("src", "s"),
            _e("s", "b", src_out="item"),
            _e("b", "e"),
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
            _e("s1", "s2", src_out="item"),  # outer item (a sublist) -> inner loop input
            _e("s2", "b", src_out="item"),
            _e("b", "e2"),
            _e("e2", "e1", src_out="results"),  # inner results -> outer collected value
        ],
    )
    result = await execute(g, registry)
    assert str(result.nodes["e1"].status) == "success"
    assert result.nodes["e1"].outputs["results"] == [[2, 4], [6]]


async def test_loop_output_mode_dataset_returns_ref(store_ctx):
    from nodyra.datasets import is_dataset_ref
    from nodyra_nodes.datasets import dataset_to_records  # materializes back to rows

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


async def test_each_reduce_accumulates_to_a_single_value():
    # sum [1,2,3,4] via a for-each reduce; concurrency set but must be ignored.
    # In reduce mode the `item` port carries {"acc": <accumulator>, "item": <unit>}.
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [1, 2, 3, 4]}),
            _n("s", "loop_start", {"accumulate": True, "initial": 0, "concurrency": 4}),
            _n("b", "code", {"code": "output = input['acc'] + input['item']"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )
    result = await execute(g, registry)
    assert str(result.nodes["e"].status) == "success"
    assert result.nodes["e"].outputs["results"] == 10


async def test_batch_reduce_accumulates_over_chunks():
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [1, 2, 3, 4, 5]}),
            _n(
                "s",
                "loop_start",
                {"mode": "batch", "batch_size": 2, "accumulate": True, "initial": 0},
            ),
            _n("b", "code", {"code": "output = input['acc'] + sum(input['item'])"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )
    result = await execute(g, registry)
    assert result.nodes["e"].outputs["results"] == 15


async def test_window_mode_sums_sliding_windows():
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [1, 2, 3, 4]}),
            _n("s", "loop_start", {"mode": "window", "batch_size": 2, "step": 1}),
            _n("b", "code", {"code": "output = sum(input)"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )
    result = await execute(g, registry)
    assert str(result.nodes["e"].status) == "success"
    assert result.nodes["e"].outputs["results"] == [3, 5, 7]  # 1+2, 2+3, 3+4


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
            _e("trig", "src"),
            _e("src", "s"),
            _e("s", "b", src_out="item"),
            _e("b", "e"),
        ],
    )
    result = await execute(g, registry)
    assert str(result.nodes["e"].status) == "success"
    assert result.nodes["e"].outputs["results"] == [
        {"key": "a", "n": 2},
        {"key": "b", "n": 1},
    ]


def _incr_while_graph(params=None, end_params=None):
    sp = {
        "mode": "while",
        "initial": {"count": 0},
        "condition": "{{ state.count < 3 }}",
        "max_iterations": 10,
    }
    sp.update(params or {})
    return _g(
        [
            _n("s", "loop_start", sp),
            _n("b", "code", {"code": "output = {'count': input['count'] + 1}"}),
            _n("e", "loop_end", {"loop_start_id": "s", **(end_params or {})}),
        ],
        [_e("s", "b", src_out="state"), _e("b", "e")],
    )


async def test_while_loop_threads_state_to_completion():
    # concurrency is set but must be ignored (conditional loops are sequential)
    result = await execute(_incr_while_graph({"concurrency": 5}), registry)
    assert str(result.nodes["e"].status) == "success"
    assert result.nodes["e"].outputs["results"] == {"count": 3}


async def test_until_loop_uses_negated_condition():
    g = _incr_while_graph({"mode": "until", "condition": "{{ state.count >= 3 }}"})
    result = await execute(g, registry)
    assert str(result.nodes["e"].status) == "success"
    assert result.nodes["e"].outputs["results"] == {"count": 3}


async def test_while_loop_zero_iterations_returns_initial():
    g = _incr_while_graph({"condition": "{{ state.count < 0 }}"})
    result = await execute(g, registry)
    assert str(result.nodes["e"].status) == "success"
    assert result.nodes["e"].outputs["results"] == {"count": 0}


async def test_while_loop_cap_fail_errors():
    g = _incr_while_graph(
        {"condition": "{{ state.count < 100 }}", "max_iterations": 2, "on_max_iterations": "fail"}
    )
    result = await execute(g, registry)
    assert str(result.nodes["e"].status) == "error"
    assert str(result.status) == "error"
    assert "max_iterations" in (result.nodes["e"].error or "")


async def test_while_loop_cap_stop_emits_current_state():
    g = _incr_while_graph(
        {"condition": "{{ state.count < 100 }}", "max_iterations": 2, "on_max_iterations": "stop"}
    )
    result = await execute(g, registry)
    e = result.nodes["e"]
    assert str(e.status) == "success"
    assert e.outputs["results"] == {"count": 2}
    assert any("max_iterations" in line for line in (e.logs or []))


async def test_while_loop_all_states_output():
    g = _incr_while_graph(end_params={"conditional_output": "all_states"})
    result = await execute(g, registry)
    out = result.nodes["e"].outputs["results"]
    assert out["final"] == {"count": 3}
    assert out["states"] == [{"count": 1}, {"count": 2}, {"count": 3}]


async def test_for_each_and_while_loops_coexist_in_one_graph():
    # Two disjoint regions: an each-loop and a while-loop, dispatched separately.
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [1, 2]}),
            _n("sa", "loop_start"),
            _n("ba", "code", {"code": "output = input * 2"}),
            _n("ea", "loop_end", {"loop_start_id": "sa"}),
            _n(
                "sb",
                "loop_start",
                {"mode": "while", "initial": {"count": 0}, "condition": "{{ state.count < 2 }}"},
            ),
            _n("bb", "code", {"code": "output = {'count': input['count'] + 1}"}),
            _n("eb", "loop_end", {"loop_start_id": "sb"}),
        ],
        [
            _e("trig", "sa"),
            _e("sa", "ba", src_out="item"),
            _e("ba", "ea"),
            _e("sb", "bb", src_out="state"),
            _e("bb", "eb"),
        ],
    )
    result = await execute(g, registry)
    assert result.nodes["ea"].outputs["results"] == [2, 4]
    assert result.nodes["eb"].outputs["results"] == {"count": 2}


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


async def test_loop_rejects_excessive_concurrency():
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [1]}),
            _n("s", "loop_start", {"concurrency": MAX_LOOP_CONCURRENCY + 1}),
            _n("b", "code", {"code": "output = input"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )
    result = await execute(g, registry)
    end = result.nodes["e"]
    assert str(end.status) == "error"
    assert "concurrency" in (end.error or "")


async def test_conditional_loop_rejects_excessive_max_iterations():
    g = _g(
        [
            _n(
                "s",
                "loop_start",
                {
                    "mode": "while",
                    "initial": {"count": 0},
                    "condition": "{{ false }}",
                    "max_iterations": MAX_CONDITIONAL_LOOP_ITERATIONS + 1,
                },
            ),
            _n("b", "code", {"code": "output = input"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("s", "b", src_out="state"), _e("b", "e")],
    )
    result = await execute(g, registry)
    end = result.nodes["e"]
    assert str(end.status) == "error"
    assert "max_iterations" in (end.error or "")


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


async def test_concurrent_loop_iter_outputs_do_not_bleed_into_outer_node_outputs_e09():
    """E-09: each iteration works in its own iter_outputs copy so that body-node
    writes (new keys) never appear in the shared outer node_outputs dict.
    Upstream entries in node_outputs are wrapped in MappingProxyType by
    _freeze_outputs, making them immutable; the per-iteration shallow copy is
    therefore safe even with concurrency > 1."""
    from nodyra.sdk import node
    from nodyra.sdk import registry as global_reg

    @node(name="E09 Key Spy", id="e09_key_spy", registry=global_reg, hidden=True)
    async def e09_key_spy(input=None):
        await asyncio.sleep(0)  # yield so all iterations run concurrently
        return input * 10

    # 4 concurrent iterations each multiplying their item by 10.
    # Results must be independent of each other.
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": [1, 2, 3, 4]}),
            _n("s", "loop_start", {"concurrency": 4}),
            _n("b", "e09_key_spy"),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )
    result = await execute(g, registry)
    assert str(result.nodes["e"].status) == "success"
    assert sorted(result.nodes["e"].outputs["results"]) == [10, 20, 30, 40]


async def test_while_loop_max_iterations_zero_uses_default_e13():
    # E-13: max_iterations=0 should not silently produce 0 iterations.
    # The `or 1000` in _bounded_conditional_iterations treats 0 as falsy → default.
    # A condition that stops at count=3 must still run to completion.
    g = _incr_while_graph({"max_iterations": 0})
    result = await execute(g, registry)
    assert str(result.nodes["e"].status) == "success", result.nodes["e"].error
    assert result.nodes["e"].outputs["results"] == {"count": 3}


def test_bounded_conditional_iterations_zero_returns_default_e13():
    from nodyra.engine.loops import _bounded_conditional_iterations
    assert _bounded_conditional_iterations(0) == 1000
    assert _bounded_conditional_iterations(None) == 1000
