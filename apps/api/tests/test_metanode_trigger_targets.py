"""A transparent metanode must run on the ordinary trigger path.

The engine inlines transparent metanodes before planning, so ``<meta_id>``
stops existing and its children become ``<meta_id>/<child>``. ``start_run``
resolved a trigger's forward descendants against the *unexpanded* graph, so
it handed the planner a target that no longer existed:

    KeyError: '<meta_id>'

raised inside _build_plan, surfacing as a run with no node runs at all. Every
workflow containing a transparent metanode failed this way — the step-run
branch right below already expanded the graph for its own gating, so the fix
was to use the same helper one branch up.
"""

import asyncio

from httpx import AsyncClient

TRIM = (
    "rows = input or []\n"
    "output = [{**r, 'name': ' '.join(str(r['name']).split()).title()} for r in rows]"
)

METANODE_GRAPH = {
    "nodes": [
        {"id": "start", "type": "manual_trigger", "params": {"data": {}},
         "position": {"x": 0, "y": 0}},
        {"id": "src", "type": "code",
         "params": {"code": "output = [{'name': '  ada  LOVELACE '}]"},
         "position": {"x": 200, "y": 0}},
        {"id": "block", "type": "meta_node", "position": {"x": 400, "y": 0},
         "params": {
             "execution": "transparent",
             "subgraph": {
                 "nodes": [
                     {"id": "trim", "type": "code", "params": {"code": TRIM},
                      "position": {"x": 0, "y": 0}},
                 ],
                 "edges": [],
             },
             "ports": {
                 "inputs": [{"port": "input",
                             "targets": [{"target": "trim", "target_input": "input"}]}],
                 "outputs": [{"port": "main", "source": "trim",
                              "source_output": "main"}],
             },
         }},
        {"id": "tail", "type": "code",
         "params": {"code": "output = {'names': [r['name'] for r in (input or [])]}"},
         "position": {"x": 600, "y": 0}},
    ],
    "edges": [
        {"source": "start", "target": "src"},
        {"source": "src", "target": "block"},
        {"source": "block", "target": "tail"},
    ],
}


def test_trigger_targets_name_the_inlined_children_not_the_metanode() -> None:
    """The planner sees the expanded graph, so the targets must match it."""
    from app.services.graph_utils import resolve_trigger_targets
    from app.services.runner import _expanded_for_gating

    targets = resolve_trigger_targets(
        _expanded_for_gating(METANODE_GRAPH), "start", None
    )

    assert "block" not in targets, "the metanode id does not survive expansion"
    assert "block/trim" in targets
    assert {"start", "src", "tail"} <= set(targets)


def test_unexpanded_resolution_would_name_a_node_the_planner_cannot_find() -> None:
    """Pins the actual defect, so the fix cannot be quietly reverted."""
    from app.services.graph_utils import resolve_trigger_targets

    stale = resolve_trigger_targets(METANODE_GRAPH, "start", None)

    assert "block" in stale
    assert "block/trim" not in stale


async def test_a_metanode_workflow_runs_from_its_trigger(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Meta"})).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": METANODE_GRAPH})

    response = await client.post(f"/workflows/{workflow_id}/run", json={"inputs": {}})
    assert response.status_code in (200, 201, 202), response.text
    run_id = response.json()["run_id"]

    # Dispatch is accepted asynchronously; wait for the terminal state.
    for _ in range(60):
        run = (await client.get(f"/runs/{run_id}")).json()
        if run["status"] in ("success", "error", "timed_out"):
            break
        await asyncio.sleep(0.25)
    assert run["status"] == "success", run.get("error")

    by_id = {n["node_id"]: n for n in run["node_runs"]}
    assert "block/trim" in by_id, sorted(by_id)
    assert by_id["block/trim"]["status"] == "success"
    assert by_id["tail"]["output"]["main"] == {"names": ["Ada Lovelace"]}
