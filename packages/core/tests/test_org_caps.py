"""Multi-tenancy C5: org amplification caps enforced inside the engine."""
from __future__ import annotations

import pytest

import nodyra_nodes  # noqa: F401 - registers loop_start/loop_end/code
from nodyra.context import org_run_limits
from nodyra.engine import execute
from nodyra.models import WorkflowGraph
from nodyra.sdk import registry


def _g(nodes, edges) -> WorkflowGraph:
    return WorkflowGraph.model_validate({"nodes": nodes, "edges": edges})


def _n(nid, ntype, params=None):
    return {"id": nid, "type": ntype, "params": params or {}, "position": {"x": 0, "y": 0}}


def _e(src, tgt, src_out="main", tgt_in="input"):
    return {"id": f"{src}->{tgt}", "source": src, "source_output": src_out,
            "target": tgt, "target_input": tgt_in}


@pytest.fixture
def org_caps():
    token = org_run_limits.set({"max_loop_iterations": 2, "max_map_width": 2})
    yield
    org_run_limits.reset(token)


def _loop_graph(rows: list) -> WorkflowGraph:
    return _g(
        [
            _n("trig", "manual_trigger", {"data": rows}),
            _n("s", "loop_start", {}),
            _n("b", "code", {"code": "output = input"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("trig", "s"), _e("s", "b", src_out="item"), _e("b", "e")],
    )


async def test_loop_within_org_cap_succeeds(org_caps):
    result = await execute(_loop_graph([1, 2]), registry)
    assert str(result.nodes["e"].status) == "success"
    assert result.nodes["e"].outputs["results"] == [1, 2]


async def test_loop_above_org_cap_errors(org_caps):
    result = await execute(_loop_graph([1, 2, 3]), registry)
    assert str(result.nodes["e"].status) == "error"
    assert "iteration cap is 2" in (result.nodes["e"].error or "")


async def test_no_caps_means_uncapped():
    result = await execute(_loop_graph([1, 2, 3, 4, 5]), registry)
    assert str(result.nodes["e"].status) == "success"


async def test_while_loop_clamped_to_org_cap(org_caps):
    g = _g(
        [
            _n("s", "loop_start", {
                "mode": "while",
                "initial": {"count": 0},
                "condition": "{{ state.count < 100 }}",
                "max_iterations": 50,
                "on_max_iterations": "stop",
            }),
            _n("b", "code", {"code": "output = {'count': input['count'] + 1}"}),
            _n("e", "loop_end", {"loop_start_id": "s"}),
        ],
        [_e("s", "b", src_out="state"), _e("b", "e")],
    )
    result = await execute(g, registry)
    e = result.nodes["e"]
    assert str(e.status) == "success"
    # The org cap (2) clamped the node's own max_iterations (50).
    assert e.outputs["results"] == {"count": 2}


async def test_map_items_above_org_cap_rejected(org_caps):
    from nodyra.context import workflow_caller
    from nodyra_nodes.builtin import map_items

    async def _fake_caller(workflow_id, payload):  # pragma: no cover - never reached
        return {"ok": True}

    token = workflow_caller.set(_fake_caller)
    try:
        with pytest.raises(ValueError, match="map fan-out cap is 2"):
            await map_items(input=[1, 2, 3], workflow_id="anything")
    finally:
        workflow_caller.reset(token)
