"""Engine-level metanode flattening (transparent execution)."""

from __future__ import annotations

import nodyra_nodes  # noqa: F401 - registers code/manual_trigger
from nodyra.engine import _expand_metanodes, execute
from nodyra.models import WorkflowGraph
from nodyra.sdk import registry


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


def _meta(nid, sub_nodes, sub_edges, ports, execution="transparent"):
    return _n(
        nid,
        "meta_node",
        {
            "execution": execution,
            "subgraph": {"nodes": sub_nodes, "edges": sub_edges},
            "ports": ports,
        },
    )


def test_expand_inlines_a_transparent_metanode():
    meta = _meta(
        "m",
        [_n("c", "code", {"code": "output = input * 2"})],
        [],
        {
            "inputs": [{"port": "input", "targets": [{"target": "c", "target_input": "input"}]}],
            "outputs": [{"port": "main", "source": "c", "source_output": "main"}],
        },
    )
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": 5}),
            meta,
            _n("sink", "code", {"code": "output = input"}),
        ],
        [_e("trig", "m", tgt_in="input"), _e("m", "sink")],
    )
    flat = _expand_metanodes(g)
    ids = {n.id for n in flat.nodes}
    assert "m" not in ids  # metanode is gone
    assert "m/c" in ids  # child inlined + namespaced
    # boundary edges rewired through the metanode
    pairs = {(e.source, e.target) for e in flat.edges}
    assert ("trig", "m/c") in pairs
    assert ("m/c", "sink") in pairs


async def test_transparent_metanode_runs_like_ungrouped():
    meta = _meta(
        "m",
        [_n("c", "code", {"code": "output = input * 2"})],
        [],
        {
            "inputs": [{"port": "input", "targets": [{"target": "c", "target_input": "input"}]}],
            "outputs": [{"port": "main", "source": "c", "source_output": "main"}],
        },
    )
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": 5}),
            meta,
            _n("sink", "code", {"code": "output = input"}),
        ],
        [_e("trig", "m", tgt_in="input"), _e("m", "sink")],
    )
    result = await execute(g, registry)
    assert str(result.nodes["sink"].status) == "success"
    assert result.nodes["sink"].outputs["main"] == 10
    assert result.nodes["m/c"].outputs["main"] == 10


def test_expand_handles_nested_metanodes():
    inner = _meta(
        "inner",
        [_n("c", "code", {"code": "output = input + 1"})],
        [],
        {
            "inputs": [{"port": "input", "targets": [{"target": "c", "target_input": "input"}]}],
            "outputs": [{"port": "main", "source": "c", "source_output": "main"}],
        },
    )
    outer = _meta(
        "outer",
        [inner],
        [],
        {
            "inputs": [
                {"port": "input", "targets": [{"target": "inner", "target_input": "input"}]}
            ],
            "outputs": [{"port": "main", "source": "inner", "source_output": "main"}],
        },
    )
    g = _g([outer], [])
    flat = _expand_metanodes(g)
    ids = {n.id for n in flat.nodes}
    assert ids == {"outer/inner/c"}


async def test_isolated_metanode_runs_its_subgraph_and_maps_ports():
    meta = _meta(
        "m",
        [_n("c", "code", {"code": "output = input * 2"})],
        [],
        {
            "inputs": [{"port": "input", "targets": [{"target": "c", "target_input": "input"}]}],
            "outputs": [{"port": "main", "source": "c", "source_output": "main"}],
        },
        execution="isolated",
    )
    g = _g(
        [
            _n("trig", "manual_trigger", {"data": 5}),
            meta,
            _n("sink", "code", {"code": "output = input"}),
        ],
        [_e("trig", "m", tgt_in="input"), _e("m", "sink")],
    )
    result = await execute(g, registry)
    assert str(result.nodes["m"].status) == "success"
    assert result.nodes["m"].outputs["main"] == 10
    assert result.nodes["sink"].outputs["main"] == 10
    # isolated: the child ran in a nested scope, not surfaced at top level
    assert "m/c" not in result.nodes


async def test_isolated_metanode_error_fails_the_run():
    meta = _meta(
        "m",
        [_n("c", "code", {"code": "raise ValueError('boom')"})],
        [],
        {
            "inputs": [{"port": "input", "targets": [{"target": "c", "target_input": "input"}]}],
            "outputs": [{"port": "main", "source": "c", "source_output": "main"}],
        },
        execution="isolated",
    )
    g = _g(
        [_n("trig", "manual_trigger", {"data": 1}), meta],
        [_e("trig", "m", tgt_in="input")],
    )
    result = await execute(g, registry)
    assert str(result.nodes["m"].status) == "error"
    assert str(result.status) == "error"


def test_isolated_metanode_is_left_intact():
    meta = _meta(
        "m",
        [_n("c", "code", {"code": "output = input"})],
        [],
        {"inputs": [], "outputs": []},
        execution="isolated",
    )
    g = _g([meta], [])
    flat = _expand_metanodes(g)
    assert {n.id for n in flat.nodes} == {"m"}
