"""Metanode handling: transparent inlining at plan time, isolated execution
as a nested engine run."""

import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from nodyra.engine.scheduler import execute
from nodyra.engine.types import EventCallback
from nodyra.models import NodeRunResult, NodeStatus, RunStatus, WorkflowGraph
from nodyra.sdk import NodeRegistry

if TYPE_CHECKING:
    from nodyra.process_isolation import ProcessIsolator


def _expand_graph_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively inline every ``transparent`` metanode in a graph dict.

    A metanode (``type == "meta_node"``) carries ``params.subgraph`` ({nodes,
    edges}) and ``params.ports`` mapping its boundary ports to internal nodes.
    Transparent metanodes are flattened: child nodes are inlined with namespaced
    ids (``<meta_id>/<child_id>``) and boundary edges are rewired through the
    port mapping. ``isolated`` metanodes are left intact (driven separately).
    """
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []

    metas: dict[str, dict[str, Any]] = {}
    for n in nodes:
        if n.get("type") == "meta_node":
            params = n.get("params") or {}
            if str(params.get("execution", "transparent") or "transparent") != "isolated":
                metas[n["id"]] = n
    if not metas:
        return data

    # Boundary port maps: input port -> list of internal (target, target_input);
    # output port -> single internal (source, source_output).
    port_in: dict[tuple[str, str], list[tuple[str, str]]] = {}
    port_out: dict[tuple[str, str], tuple[str, str]] = {}
    for mid, m in metas.items():
        ports = (m.get("params") or {}).get("ports") or {}
        for p in ports.get("inputs") or []:
            targets = [
                (t["target"], t.get("target_input", "input")) for t in (p.get("targets") or [])
            ]
            port_in[(mid, p["port"])] = targets
        for p in ports.get("outputs") or []:
            port_out[(mid, p["port"])] = (p["source"], p.get("source_output", "main"))

    out_nodes: list[dict[str, Any]] = []
    for n in nodes:
        if n["id"] in metas:
            sub = (n.get("params") or {}).get("subgraph") or {"nodes": [], "edges": []}
            sub = _expand_graph_dict(sub)  # flatten nested metanodes first
            prefix = f"{n['id']}/"
            for sn in sub.get("nodes") or []:
                c = dict(sn)
                c["id"] = prefix + sn["id"]
                out_nodes.append(c)
        else:
            out_nodes.append(n)

    out_edges: list[dict[str, Any]] = []
    for n in nodes:
        if n["id"] not in metas:
            continue
        sub = (n.get("params") or {}).get("subgraph") or {"nodes": [], "edges": []}
        sub = _expand_graph_dict(sub)
        prefix = f"{n['id']}/"
        for se in sub.get("edges") or []:
            e = dict(se)
            e["source"] = prefix + se["source"]
            e["target"] = prefix + se["target"]
            e["id"] = prefix + str(se.get("id", f"{se['source']}->{se['target']}"))
            out_edges.append(e)

    for e in edges:
        s_meta = e["source"] in metas
        t_meta = e["target"] in metas
        if not s_meta and not t_meta:
            out_edges.append(e)
            continue
        # Resolve the (possibly fanned-out) target side.
        targets: list[tuple[str, str]] = [(e["target"], e.get("target_input", "input"))]
        if t_meta:
            mapped = port_in.get((e["target"], e.get("target_input", "input")))
            if not mapped:
                continue  # unmapped boundary edge — drop
            targets = [(f"{e['target']}/{tid}", tin) for tid, tin in mapped]
        # Resolve the source side.
        source = e["source"]
        source_output = e.get("source_output", "main")
        if s_meta:
            mapped_src = port_out.get((e["source"], e.get("source_output", "main")))
            if mapped_src is None:
                continue
            source = f"{e['source']}/{mapped_src[0]}"
            source_output = mapped_src[1]
        for idx, (tid, tin) in enumerate(targets):
            ne = dict(e)
            ne["source"] = source
            ne["source_output"] = source_output
            ne["target"] = tid
            ne["target_input"] = tin
            ne["id"] = (
                f"{e.get('id', f'{source}->{tid}')}#{idx}"
                if len(targets) > 1
                else e.get("id", f"{source}->{tid}")
            )
            out_edges.append(ne)

    return {**data, "nodes": out_nodes, "edges": out_edges}


def _expand_metanodes(graph: WorkflowGraph) -> WorkflowGraph:
    """Return a graph with all transparent metanodes inlined (see
    :func:`_expand_graph_dict`). A no-op when there are no metanodes."""
    if not any(n.type == "meta_node" for n in graph.nodes):
        return graph
    return WorkflowGraph.model_validate(_expand_graph_dict(graph.model_dump()))


async def _run_metanode(
    *,
    node: Any,
    incoming: dict[str, dict[str, tuple[str, str]]],
    node_outputs: dict[str, dict[str, Any]],
    registry: NodeRegistry,
    emit: EventCallback,
    finish: Callable[[NodeRunResult], Awaitable[None]],
    default_timeouts: dict[str, float],
    max_node_output_bytes: int | None,
    process_isolator: "ProcessIsolator | None" = None,
) -> RunStatus:
    """Run an ``isolated`` metanode: execute its embedded sub-graph as a nested
    run with its own scope, feeding boundary inputs in and mapping the boundary
    outputs back onto the metanode's ports."""
    mid = node.id
    params = node.params or {}
    subgraph = params.get("subgraph") or {"nodes": [], "edges": []}
    ports = params.get("ports") or {}

    await emit({"type": "node_started", "node_id": mid})
    started = time.time()

    # Resolve the value on each wired input port from the parent outputs.
    edges_in = incoming.get(mid, {})
    aug_nodes = [dict(n) for n in (subgraph.get("nodes") or [])]
    aug_edges = [dict(e) for e in (subgraph.get("edges") or [])]
    cache: dict[str, dict[str, Any]] = {}
    for p in ports.get("inputs") or []:
        port = p["port"]
        value = None
        if port in edges_in:
            src, out = edges_in[port]
            value = (node_outputs.get(src) or {}).get(out)
        sid = f"__mn_{mid}_{port}"
        aug_nodes.append(
            {"id": sid, "type": "__metanode_input__", "params": {}, "position": {"x": 0, "y": 0}}
        )
        cache[sid] = {"main": value}
        for t in p.get("targets") or []:
            aug_edges.append(
                {
                    "id": f"{sid}->{t['target']}",
                    "source": sid,
                    "source_output": "main",
                    "target": t["target"],
                    "target_input": t.get("target_input", "input"),
                }
            )

    try:
        sub_graph = WorkflowGraph.model_validate({"nodes": aug_nodes, "edges": aug_edges})
        sub_result = await execute(
            sub_graph,
            registry,
            cache=cache,
            default_timeouts=default_timeouts,
            max_node_output_bytes=max_node_output_bytes,
            process_isolator=process_isolator,
        )
    except Exception as exc:  # noqa: BLE001 - surface as a node error
        node_outputs[mid] = {}
        await finish(
            NodeRunResult(
                node_id=mid,
                status=NodeStatus.error,
                error=f"{type(exc).__name__}: {exc}",
                started_at=started,
                finished_at=time.time(),
            )
        )
        return RunStatus.error

    outputs: dict[str, Any] = {}
    for p in ports.get("outputs") or []:
        run = sub_result.nodes.get(p["source"])
        outputs[p["port"]] = (
            run.outputs.get(p.get("source_output", "main")) if run is not None else None
        )

    node_outputs[mid] = outputs
    if str(sub_result.status) == "error":
        await finish(
            NodeRunResult(
                node_id=mid,
                status=NodeStatus.error,
                error="metanode sub-graph failed",
                outputs=outputs,
                started_at=started,
                finished_at=time.time(),
            )
        )
        return RunStatus.error
    await finish(
        NodeRunResult(
            node_id=mid,
            status=NodeStatus.success,
            outputs=outputs,
            started_at=started,
            finished_at=time.time(),
        )
    )
    return RunStatus.success
