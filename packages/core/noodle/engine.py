"""DAG execution engine.

Executes a :class:`WorkflowGraph` in topological order. Each edge feeds an
upstream node's output into a downstream node's input port. Supports:

* branching — multi-output nodes; consumers of an untaken branch are skipped;
* partial execution — ``cache`` supplies precomputed outputs for some nodes;
* targeted runs — ``targets`` restricts execution to a subset and its ancestors;
* live events — ``on_event`` is awaited with per-node start/finish events.

The same engine runs inside env runners and inside exported scripts.
"""

import asyncio
import concurrent.futures
import contextvars
import functools
import json
import random
import sys
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from noodle.ai_runtime import (
    AgentActionRequest,
    AgentActionResponse,
    AgentApprovalRequired,
    AgentStepEvent,
    ToolAdapter,
    ToolResult,
)
from noodle.context import (
    current_node_id,
    iteration_path,
    node_debug,
    org_run_limits,
)
from noodle.expr import build_context, evaluate
from noodle.models import (
    NodeRunResult,
    NodeStatus,
    PortSpec,
    RunResult,
    RunStatus,
    WorkflowGraph,
)
from noodle.node_tool import TOOL_MODE_OUTPUT, build_node_tool_adapter
from noodle.sdk import NodeRegistry

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]

PROCESS_ISOLATED_NODE_TYPES: frozenset[str] = frozenset({"code"})

# Node types whose outputs the engine will inspect and auto-promote heavy
# values (DataFrames, large row lists, big bytes/text) into Dataset/Artifact
# refs before the output-size cap is applied. Used for Code-like nodes where
# the user might intentionally produce table-shaped data.
AUTO_PROMOTE_NODE_TYPES: frozenset[str] = frozenset({"code"})

# Node types that handle DatasetRefs natively and therefore should *not* have
# their generic inputs auto-expanded into rows. The Code node can call dataset
# SDK helpers on the ref directly, and dataset_* nodes declare their ports as
# ``dataset`` (which are skipped anyway). Every other node receiving a
# DatasetRef on an ``any`` port gets the rows materialized so per-item logic
# (Loop Over Items, Filter, Edit Fields, …) works as authored.
DATASET_PASSTHROUGH_NODE_TYPES: frozenset[str] = frozenset({"code"})

# Hard cap on rows the engine will expand inline from a DatasetRef before a
# generic node runs. Beyond this the node errors with guidance to reduce rows
# upstream — datasets are meant to stay artifact-backed, not materialized whole.
DATASET_AUTO_EXPAND_CAP: int = 50_000

# Per-environment-key pool dict. Keyed by an opaque string (env id) so that
# code nodes from different environments cannot share worker state. None is the
# default bucket used when no key is set (in-process tests, legacy callers).
_process_pools: dict[str | None, concurrent.futures.ProcessPoolExecutor] = {}
# Tracks the last time each pool was actually used so idle pools can be reaped.
_pool_last_used: dict[str | None, float] = {}
# Seconds a process pool is allowed to be idle before the next _get_process_pool
# call evicts it. Mirrors runner_idle_seconds (default 600s) at the engine layer.
_POOL_IDLE_SECONDS: float = 600.0

# Hard cap on the number of agent-loop iterations that resolve_agent_actions
# will attempt before raising RuntimeError.  Guards against a buggy node that
# always returns AgentActionRequest(step=0) with no tool_calls — the existing
# max_steps check only fires when the node itself increments the step counter.
_MAX_AGENT_LOOP_ITERATIONS: int = 200


class _LengthCountingSink:
    """A minimal write-only sink that counts characters instead of buffering
    them. Used to measure ``json.dump`` output size without materializing the
    whole encoded string in memory — important for multi-MB node outputs that
    are checked against ``max_node_output_bytes`` on every run."""

    __slots__ = ("length",)

    def __init__(self) -> None:
        self.length = 0

    def write(self, chunk: str) -> None:
        self.length += len(chunk)


def _approx_encoded_length(value: Any) -> int:
    """Character length of ``value`` encoded as JSON, computed by streaming
    into a counting sink. Equivalent to ``len(json.dumps(value, default=str))``
    but without holding the full string."""
    sink = _LengthCountingSink()
    json.dump(value, sink, default=str)
    return sink.length


def _get_process_pool(
    max_workers: int = 4,
    *,
    key: str | None = None,
) -> concurrent.futures.ProcessPoolExecutor:
    """Return (or create) the process pool for the given isolation key.

    Each distinct key gets its own pool so worker state (imported modules,
    patched globals) cannot leak between environments. ``key=None`` is the
    shared default used by tests and the in-process dev path.

    Idle pools (no activity for ``_POOL_IDLE_SECONDS``) are evicted before
    returning a pool so the dict does not accumulate indefinitely — one pool
    per environment-id means O(envs * max_workers) background processes on a
    busy server, quickly exhausting process/FD limits.
    """
    now = time.monotonic()
    # Sweep idle pools before potentially creating a new one.
    idle_keys = [
        k for k, last in _pool_last_used.items()
        if now - last > _POOL_IDLE_SECONDS and k != key
    ]
    for k in idle_keys:
        _evict_pool(k)

    pool = _process_pools.get(key)
    if pool is None:
        pool = concurrent.futures.ProcessPoolExecutor(max_workers=max_workers)
        _process_pools[key] = pool
    _pool_last_used[key] = now
    return pool


def _evict_pool(key: str | None) -> None:
    """Shutdown and remove the pool for ``key`` so the next use gets a fresh one."""
    pool = _process_pools.pop(key, None)
    _pool_last_used.pop(key, None)
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


# Callers (runner.py) set this to the workflow's environment id so each
# environment gets its own ProcessPoolExecutor and worker state cannot
# bleed across environments. Defaults to None (shared pool, legacy path).
pool_key: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "noodle_pool_key", default=None
)

# Per-node-type default timeouts (seconds). ``code`` is intentionally
# *absent* so heavy/long-running Python isn't capped by an arbitrary default;
# it's bounded only by the overall workflow timeout. Callers (the API runner /
# runtime server) can inject a code default via the ``default_timeouts`` arg,
# and any node may still set its own ``timeout_seconds``.
DEFAULT_NODE_TIMEOUTS: dict[str, float] = {
    "http_request": 45.0,
    "ai_agent_v2": 300.0,
}

# Per-node log capture. Writes to stdout/stderr are diverted into this
# context-local buffer *only* while a node is executing, so capture is safe
# under concurrent runs / sub-workflows sharing one process — unlike a global
# ``redirect_stdout`` which would cross-capture other tasks' output.
_log_capture: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "noodle_log_capture", default=None
)


class _CaptureProxy:
    """stdout/stderr proxy: divert into the active buffer, else pass through."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def write(self, s: str) -> int:
        buf = _log_capture.get()
        if buf is not None:
            buf.append(s)
            return len(s)
        return self._wrapped.write(s)

    def flush(self) -> None:
        try:
            self._wrapped.flush()
        except Exception:  # noqa: BLE001 - flushing must never raise into the engine
            pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


def _install_capture() -> None:
    """Install the capture proxies once. Idempotent and process-wide-safe:
    when no buffer is active for the current task, writes pass straight through.
    """
    if not isinstance(sys.stdout, _CaptureProxy):
        sys.stdout = _CaptureProxy(sys.stdout)
    if not isinstance(sys.stderr, _CaptureProxy):
        sys.stderr = _CaptureProxy(sys.stderr)


class GraphError(Exception):
    """Raised when a workflow graph is structurally invalid (e.g. has a cycle)."""


AI_PORT_KINDS: frozenset[str] = frozenset(
    {
        "ai_language_model",
        "ai_embedding_model",
        "ai_memory",
        "ai_tool",
        "ai_output_parser",
        "ai_retriever",
        "ai_vector_store",
        "ai_document_loader",
        "ai_guardrail",
    }
)


def _port_kind(port: PortSpec | None) -> str:
    return str(getattr(port, "data_kind", "any") or "any")


def _find_port(
    ports: list[PortSpec],
    name: str | None,
    default_name: str,
) -> PortSpec | None:
    wanted = name or default_name
    return next((port for port in ports if port.name == wanted), None) or (
        ports[0] if ports else None
    )


def _kind_label(kind: str) -> str:
    labels = {
        "any": "any data",
        "main": "main data",
        "control": "control",
        "dataset": "DatasetRef",
        "artifact": "ArtifactRef",
        "file": "FileRef",
        "ai_language_model": "AI language model",
        "ai_embedding_model": "AI embedding model",
        "ai_memory": "AI memory",
        "ai_tool": "AI tool",
        "ai_output_parser": "AI output parser",
        "ai_retriever": "AI retriever",
        "ai_vector_store": "AI vector store",
        "ai_document_loader": "AI document loader",
        "ai_guardrail": "AI guardrail",
    }
    return labels.get(kind, kind)


def _connection_kind_error(source_kind: str, target_kind: str) -> str | None:
    if source_kind == target_kind:
        return None

    if source_kind in AI_PORT_KINDS or target_kind in AI_PORT_KINDS:
        return (
            f"{_kind_label(source_kind)} cannot connect to "
            f"{_kind_label(target_kind)}"
        )

    # DatasetRef ports are strict: they may only connect to a port that
    # explicitly accepts them ("dataset") or to the permissive "any" escape
    # hatch (where _auto_expand_dataset_inputs handles runtime expansion).
    if source_kind == "dataset":
        if target_kind in {"dataset", "any"}:
            return None
        return (
            f"{_kind_label(source_kind)} cannot connect to "
            f"{_kind_label(target_kind)}"
        )
    if target_kind == "dataset":
        if source_kind in {"dataset", "any"}:
            return None
        return (
            f"{_kind_label(source_kind)} cannot connect to "
            f"{_kind_label(target_kind)}"
        )

    # `main` is the explicit form of ordinary item/data flow; `any` remains the
    # permissive escape hatch for legacy and generic nodes.
    if source_kind in {"any", "main"} or target_kind in {"any", "main"}:
        return None

    return (
        f"{_kind_label(source_kind)} cannot connect to "
        f"{_kind_label(target_kind)}"
    )


def _validate_connection_kinds(
    graph: WorkflowGraph,
    registry: NodeRegistry,
    needed: set[str] | None = None,
) -> None:
    nodes_by_id = {node.id: node for node in graph.nodes}
    for edge in graph.edges:
        if needed is not None and (
            edge.source not in needed or edge.target not in needed
        ):
            continue
        source_node = nodes_by_id.get(edge.source)
        target_node = nodes_by_id.get(edge.target)
        if source_node is None or target_node is None:
            continue
        try:
            source_def = registry.get(source_node.type)
            target_def = registry.get(target_node.type)
        except KeyError:
            # Preserve existing unknown-node behavior: execution reports the
            # missing node as a node error instead of failing graph validation.
            continue

        source_port = _find_port(
            source_def.manifest.outputs,
            edge.source_output,
            "main",
        )
        target_port = _find_port(
            target_def.manifest.inputs,
            edge.target_input,
            "input",
        )
        source_kind = _port_kind(source_port)
        # A tool-mode node exposes a single `tool` output of kind ai_tool,
        # regardless of its normal (data-flow) manifest outputs.
        if (
            getattr(source_node, "tool_mode", False)
            and edge.source_output == TOOL_MODE_OUTPUT
        ):
            source_kind = "ai_tool"
        target_kind = _port_kind(target_port)
        error = _connection_kind_error(source_kind, target_kind)
        if error:
            raise GraphError(
                "Port kind mismatch on edge "
                f"{edge.source}.{edge.source_output} -> "
                f"{edge.target}.{edge.target_input}: {error}."
            )


def _predecessors(graph: WorkflowGraph) -> dict[str, set[str]]:
    preds: dict[str, set[str]] = {n.id: set() for n in graph.nodes}
    for edge in graph.edges:
        if edge.source in preds and edge.target in preds and edge.source != edge.target:
            preds[edge.target].add(edge.source)
    return preds


@dataclass(frozen=True)
class LoopRegion:
    start_id: str
    end_id: str
    body_ids: frozenset[str]      # nodes strictly between start and end
    parent_start_id: str | None   # enclosing loop's start_id, or None


def _descendants(graph: WorkflowGraph, root: str) -> set[str]:
    succ: dict[str, set[str]] = defaultdict(set)
    for e in graph.edges:
        succ[e.source].add(e.target)
    seen: set[str] = set()
    stack = list(succ[root])
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(succ[n])
    return seen


def _ancestors(graph: WorkflowGraph, root: str) -> set[str]:
    preds = _predecessors(graph)
    seen: set[str] = set()
    stack = list(preds.get(root, ()))
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(preds.get(n, ()))
    return seen


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
                (t["target"], t.get("target_input", "input"))
                for t in (p.get("targets") or [])
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
            ne["id"] = f"{e.get('id', f'{source}->{tid}')}#{idx}" if len(targets) > 1 else e.get("id", f"{source}->{tid}")
            out_edges.append(ne)

    return {**data, "nodes": out_nodes, "edges": out_edges}


def _expand_metanodes(graph: WorkflowGraph) -> WorkflowGraph:
    """Return a graph with all transparent metanodes inlined (see
    :func:`_expand_graph_dict`). A no-op when there are no metanodes."""
    if not any(n.type == "meta_node" for n in graph.nodes):
        return graph
    return WorkflowGraph.model_validate(_expand_graph_dict(graph.model_dump()))


def _loop_regions(graph: WorkflowGraph) -> dict[str, LoopRegion]:
    """Map each loop_start node id to its LoopRegion.

    Body = descendants(start) ∩ ancestors(end), excluding start and end.
    Pairing is read from loop_end.params['loop_start_id'].
    """
    starts = [n.id for n in graph.nodes if n.type == "loop_start"]
    ends_for_start: dict[str, str] = {}
    for n in graph.nodes:
        if n.type == "loop_end":
            sid = str(n.params.get("loop_start_id") or "")
            if sid:
                ends_for_start[sid] = n.id

    regions: dict[str, LoopRegion] = {}
    for sid in starts:
        eid = ends_for_start.get(sid)
        if eid is None:
            raise GraphError(f"Loop Start '{sid}' has no paired Loop End")
        body = (_descendants(graph, sid) & _ancestors(graph, eid)) - {sid, eid}
        regions[sid] = LoopRegion(
            start_id=sid, end_id=eid, body_ids=frozenset(body), parent_start_id=None
        )

    # Resolve nesting: a region whose start is inside another region's body is nested.
    for sid, r in list(regions.items()):
        for other_sid, other in regions.items():
            if other_sid != sid and sid in other.body_ids:
                regions[sid] = LoopRegion(
                    start_id=r.start_id, end_id=r.end_id,
                    body_ids=r.body_ids, parent_start_id=other_sid,
                )
                break
    return regions


def _validate_loop_regions(
    graph: WorkflowGraph, regions: dict[str, LoopRegion]
) -> None:
    """Raise GraphError unless every loop region is single-entry/single-exit
    and any two regions are disjoint or strictly nested."""
    for r in regions.values():
        body = set(r.body_ids)
        for e in graph.edges:
            into_body = e.target in body
            from_body = e.source in body
            # Single entry: the only edge entering the body comes from start.
            if into_body and e.source not in body and e.source != r.start_id:
                raise GraphError(
                    f"Loop '{r.start_id}': single entry violated — node "
                    f"'{e.target}' is fed from '{e.source}' outside the loop"
                )
            # Single exit: the only edge leaving the body goes to end.
            if from_body and e.target not in body and e.target != r.end_id:
                raise GraphError(
                    f"Loop '{r.start_id}': single exit violated — body node "
                    f"'{e.source}' wires to '{e.target}' outside the loop"
                )
        # Every body node must reach end (no dead-ends inside the region).
        for nid in body:
            if r.end_id not in (_descendants(graph, nid) | {r.end_id}):
                raise GraphError(
                    f"Loop '{r.start_id}': body node '{nid}' does not reach Loop End"
                )

    # Well-nestedness: regions overlap only by strict containment.
    items = list(regions.values())
    for i, a in enumerate(items):
        a_set = set(a.body_ids) | {a.start_id, a.end_id}
        for b in items[i + 1:]:
            b_set = set(b.body_ids) | {b.start_id, b.end_id}
            inter = a_set & b_set
            if inter and not (a_set <= b_set or b_set <= a_set):
                raise GraphError(
                    f"Loops '{a.start_id}' and '{b.start_id}' partially overlap; "
                    f"loops must be disjoint or strictly nested"
                )


def _topo_order(graph: WorkflowGraph) -> list[str]:
    """Return a deterministic topological order of node ids.

    Contract:
      * Order depends only on edges and node insertion order in ``graph.nodes``.
      * Canvas position (``GraphNode.position`` / x,y) is never read here and
        must never influence execution order — the editor may reorder nodes
        visually without changing semantics.
      * Among nodes with equal indegree, ties are broken by their index in
        ``graph.nodes`` (stable insertion order), not by node id.
    """
    node_index = {n.id: i for i, n in enumerate(graph.nodes)}
    preds = _predecessors(graph)
    successors: dict[str, set[str]] = defaultdict(set)
    for target, sources in preds.items():
        for source in sources:
            successors[source].add(target)

    indegree = {nid: len(sources) for nid, sources in preds.items()}
    by_index = lambda nid: node_index[nid]  # noqa: E731
    ready = sorted(
        (nid for nid, deg in indegree.items() if deg == 0), key=by_index
    )
    order: list[str] = []

    while ready:
        nid = ready.pop(0)
        order.append(nid)
        for succ in sorted(successors[nid], key=by_index):
            indegree[succ] -= 1
            if indegree[succ] == 0:
                ready.append(succ)
        ready.sort(key=by_index)

    if len(order) != len(graph.nodes):
        raise GraphError("Workflow graph has a cycle")
    return order


def _topo_levels(graph: WorkflowGraph) -> list[list[str]]:
    """Return nodes grouped by depth level.

    All nodes in one level have all their predecessors in earlier levels, so
    they can safely execute in parallel via ``asyncio.gather``. Ties within a
    level are broken by insertion order (same rule as ``_topo_order``).
    """
    node_index = {n.id: i for i, n in enumerate(graph.nodes)}
    preds = _predecessors(graph)
    successors: dict[str, set[str]] = defaultdict(set)
    for target, sources in preds.items():
        for source in sources:
            successors[source].add(target)

    indegree = {nid: len(sources) for nid, sources in preds.items()}
    remaining = set(indegree.keys())
    levels: list[list[str]] = []

    while remaining:
        level = sorted(
            [nid for nid in remaining if indegree[nid] == 0],
            key=lambda nid: node_index[nid],
        )
        if not level:
            raise GraphError("Cycle detected")
        levels.append(level)
        for nid in level:
            remaining.remove(nid)
            for succ in successors[nid]:
                indegree[succ] -= 1
    return levels


def _needed_nodes(
    graph: WorkflowGraph,
    targets: set[str] | None,
    cache: dict[str, dict[str, Any]],
) -> set[str]:
    if targets is None:
        return {n.id for n in graph.nodes}

    preds = _predecessors(graph)
    needed: set[str] = set()
    stack = list(targets)
    while stack:
        nid = stack.pop()
        if nid in needed:
            continue
        needed.add(nid)
        if nid in cache:
            continue  # cached node supplies a value; its ancestors aren't needed
        stack.extend(preds.get(nid, ()))
    return needed


def _normalize_outputs(raw: Any, output_names: list[str], node_id: str) -> dict[str, Any]:
    if len(output_names) == 1:
        return {output_names[0]: raw}
    if not isinstance(raw, dict):
        raise ValueError(
            f"node '{node_id}' declares multiple outputs and must return a dict"
        )
    return {name: raw[name] for name in output_names if name in raw}


def _auto_expand_dataset_inputs(
    node_def: Any,
    kwargs: dict[str, Any],
    node_type: str,
) -> None:
    """Expand DatasetRef inputs into rows for generic per-item nodes.

    A DatasetRef is a single envelope dict. Without this, item-processing
    nodes (Loop Over Items, Filter, …) would treat the whole dataset as one
    item and run once. Here we materialize the rows so those nodes operate on
    the records. Dataset-native node types (``DATASET_PASSTHROUGH_NODE_TYPES``)
    and ports explicitly declared as ``dataset``/``artifact`` are left as the
    raw ref.
    """
    if node_type in DATASET_PASSTHROUGH_NODE_TYPES:
        return
    from noodle.datasets import is_dataset_ref, materialize_dataset_rows

    for port in node_def.manifest.inputs:
        kind = getattr(port, "data_kind", "any")
        if kind != "any" or port.name not in kwargs:
            continue
        value = kwargs[port.name]
        if is_dataset_ref(value):
            kwargs[port.name] = materialize_dataset_rows(
                value, cap=DATASET_AUTO_EXPAND_CAP
            )


def _validate_input_kinds(
    node_def: Any,
    kwargs: dict[str, Any],
    node_id: str,
) -> None:
    """Validate inputs declared as dataset/artifact actually receive that kind."""
    from noodle.artifacts import is_artifact_ref
    from noodle.datasets import is_dataset_ref

    for port in node_def.manifest.inputs:
        kind = getattr(port, "data_kind", "any")
        if kind in ("any", "control") or port.name not in kwargs:
            continue
        value = kwargs[port.name]
        if value is None:
            if kind in AI_PORT_KINDS:
                raise ValueError(
                    f"node '{node_id}' input '{port.name}' expected "
                    f"{_kind_label(kind)}, but the connected upstream node "
                    "produced no value. Enable the upstream node or disconnect "
                    "this AI port."
                )
            continue
        if kind == "dataset" and not is_dataset_ref(value):
            raise ValueError(
                f"node '{node_id}' input '{port.name}' expected a DatasetRef. "
                f"Add a Records To Dataset or CSV Parse node upstream."
            )
        if kind in ("artifact", "file") and not is_artifact_ref(value):
            raise ValueError(
                f"node '{node_id}' input '{port.name}' expected an ArtifactRef."
            )


def _validate_output_kinds(
    node_def: Any,
    outputs: dict[str, Any],
    node_id: str,
) -> None:
    from noodle.artifacts import is_artifact_ref
    from noodle.datasets import is_dataset_ref

    for port in node_def.manifest.outputs:
        kind = getattr(port, "data_kind", "any")
        if kind in ("any", "control") or port.name not in outputs:
            continue
        value = outputs[port.name]
        if value is None:
            continue
        if kind == "dataset" and not is_dataset_ref(value):
            raise ValueError(
                f"node '{node_id}' output '{port.name}' was declared as a "
                f"dataset port but produced {type(value).__name__}."
            )
        if kind in ("artifact", "file") and not is_artifact_ref(value):
            raise ValueError(
                f"node '{node_id}' output '{port.name}' was declared as an "
                f"artifact port but produced {type(value).__name__}."
            )


def _auto_promote_outputs(
    outputs: dict[str, Any],
    *,
    max_inline_rows: int = 1000,
    max_inline_bytes: int = 256 * 1024,
) -> dict[str, Any]:
    """Promote large/typed values from auto-mode nodes to Dataset/Artifact refs.

    Only triggers when the value is clearly heavy (DataFrame, big row list,
    big bytes/text). Refs that exist already, small inline values, and
    control-shaped dicts are returned unchanged.
    """
    from noodle.artifacts import is_artifact_ref
    from noodle.datasets import is_dataset_ref

    try:
        from noodle.dataset_promote import promote_value
    except ImportError:
        return outputs

    promoted: dict[str, Any] = {}
    for name, value in outputs.items():
        if value is None or is_dataset_ref(value) or is_artifact_ref(value):
            promoted[name] = value
            continue
        promoted[name] = promote_value(
            value,
            port_name=name,
            max_inline_rows=max_inline_rows,
            max_inline_bytes=max_inline_bytes,
        )
    return promoted


def _node_timeout(
    node_type: str,
    timeout: float | None,
    default_timeouts: dict[str, float],
) -> float | None:
    if timeout is not None:
        return timeout
    return default_timeouts.get(node_type)


def _collect_tool_adapters(value: Any) -> list[ToolAdapter]:
    """Collect ToolAdapter instances from a wired agent input value."""
    if isinstance(value, ToolAdapter):
        return [value]
    if isinstance(value, (list, tuple, set, frozenset)):
        tools: list[ToolAdapter] = []
        for item in value:
            tools.extend(_collect_tool_adapters(item))
        return tools
    if isinstance(value, dict):
        tools: list[ToolAdapter] = []
        for key in ("tool", "tools"):
            if key in value:
                tools.extend(_collect_tool_adapters(value[key]))
        return tools
    return []


def _tool_index(tool_values: Iterable[Any]) -> dict[str, ToolAdapter]:
    index: dict[str, ToolAdapter] = {}
    for value in tool_values:
        for tool in _collect_tool_adapters(value):
            name = str(tool.schema.name or "").strip()
            if not name:
                continue
            if name in index and index[name] is not tool:
                raise ValueError(f"duplicate AI tool name: {name}")
            index[name] = tool
    return index


def _tool_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def _agent_approval_key(agent_node_id: str, step: int, call_name: str, call_id: str) -> str:
    raw = "|".join([agent_node_id, str(step), call_id, call_name])
    return raw[:240]


async def _dispatch_agent_action_request(
    request: AgentActionRequest,
    *,
    agent_node_id: str,
    tool_values: Iterable[Any],
    emit: EventCallback,
    pause_on_approval: bool = False,
) -> AgentActionResponse:
    max_steps = max(1, int(request.max_steps or 1))
    step = max(0, int(request.step or 0))
    if step >= max_steps:
        raise RuntimeError(f"agent reached max_steps={max_steps}")

    tools = _tool_index(tool_values)
    await emit(
        {
            **AgentStepEvent(
                type="agent_action_requested",
                agent_node_id=agent_node_id,
                step=step,
                max_steps=max_steps,
            ).model_dump(exclude_none=True),
            "tool_calls": [
                call.model_dump(exclude_none=True) for call in request.tool_calls
            ],
        }
    )

    results: list[ToolResult] = []
    for call in request.tool_calls:
        started = time.time()
        await emit(
            {
                **AgentStepEvent(
                    type="agent_tool_started",
                    agent_node_id=agent_node_id,
                    step=step,
                    max_steps=max_steps,
                    tool_call_id=call.id,
                    tool_name=call.name,
                ).model_dump(exclude_none=True),
                "arguments": call.arguments,
            }
        )

        tool = tools.get(call.name)
        approval_key = _agent_approval_key(agent_node_id, step, call.name, call.id)
        approved_call_ids = set(request.approved_tool_call_ids or [])
        rejected_call_ids = set(request.rejected_tool_call_ids or [])
        if tool is None:
            result = ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=f"Unknown tool: {call.name}",
                is_error=True,
            )
        elif call.id in rejected_call_ids:
            # An operator denied this side-effecting call. Feed the denial back
            # to the agent as a tool error so it can recover gracefully (e.g.
            # apologise or pick another approach) instead of the run hanging.
            result = ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=f"Tool {call.name!r} was denied by the operator.",
                is_error=True,
            )
        elif (
            tool.side_effecting
            and not request.allow_side_effects
            and call.id not in approved_call_ids
        ):
            message = f"Tool {call.name!r} requires approval before running."
            await emit(
                {
                    **AgentStepEvent(
                        type="agent_tool_approval_required",
                        agent_node_id=agent_node_id,
                        step=step,
                        max_steps=max_steps,
                        tool_call_id=call.id,
                        tool_name=call.name,
                        approval_key=approval_key,
                        status="blocked",
                        message=message,
                    ).model_dump(exclude_none=True),
                    "arguments": call.arguments,
                }
            )
            if pause_on_approval:
                raise AgentApprovalRequired(
                    request=request,
                    tool_call=call,
                    approval_key=approval_key,
                    message=message,
                )
            result = ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content=message,
                is_error=True,
            )
        else:
            if tool.side_effecting and request.allow_side_effects:
                await emit(
                    {
                        **AgentStepEvent(
                            type="agent_tool_auto_approved",
                            agent_node_id=agent_node_id,
                            step=step,
                            max_steps=max_steps,
                            tool_call_id=call.id,
                            tool_name=call.name,
                            approval_key=approval_key,
                            status="approved",
                            message="Side-effecting tool auto-approved by agent setting.",
                        ).model_dump(exclude_none=True),
                        "arguments": call.arguments,
                    }
                )
            try:
                content = await tool.invoke_async(dict(call.arguments))
                result = ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=_tool_content(content),
                )
            except Exception as exc:  # noqa: BLE001 - tool failures go back to the model
                result = ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    content=f"{type(exc).__name__}: {exc}",
                    is_error=True,
                )

        duration_ms = int((time.time() - started) * 1000)
        await emit(
            {
                **AgentStepEvent(
                    type="agent_tool_finished",
                    agent_node_id=agent_node_id,
                    step=step,
                    max_steps=max_steps,
                    tool_call_id=call.id,
                    tool_name=call.name,
                    status="error" if result.is_error else "success",
                ).model_dump(exclude_none=True),
                "tool_result": result.model_dump(),
                "duration_ms": duration_ms,
            }
        )
        results.append(result)

    next_step = step + 1
    response = AgentActionResponse(
        tool_results=results,
        messages_so_far=list(request.messages_so_far),
        step=next_step,
        max_steps=max_steps,
    )
    await emit(
        {
            **AgentStepEvent(
                type="agent_action_completed",
                agent_node_id=agent_node_id,
                step=next_step,
                max_steps=max_steps,
                status="success",
            ).model_dump(exclude_none=True),
            "tool_results": [result.model_dump() for result in results],
        }
    )
    return response


async def _run_one_node(
    *,
    nid: str,
    nodes_by_id: dict[str, Any],
    incoming: dict[str, dict[str, tuple[str, str]]],
    node_outputs: dict[str, dict[str, Any]],
    cache: dict[str, dict[str, Any]],
    registry: NodeRegistry,
    emit: EventCallback,
    finish: Callable[[NodeRunResult], Awaitable[None]],
    default_timeouts: dict[str, float],
    max_node_output_bytes: int | None,
    pause_on_approval: bool,
    agent_action_resume: dict[str, AgentActionRequest],
) -> RunStatus:
    """Execute a single node against ``node_outputs`` and return the worst
    RunStatus it produced. Extracted verbatim from the old ``execute()`` inner
    closure so the loop driver can reuse the exact same per-node logic."""
    run_status = RunStatus.success
    graph_node = nodes_by_id[nid]

    if nid in cache:
        node_outputs[nid] = dict(cache[nid])
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.success, outputs=node_outputs[nid]
            )
        )
        return run_status

    edges_in = incoming.get(nid, {})

    skip_reason = None
    for source, source_output in edges_in.values():
        if source not in node_outputs:
            skip_reason = f"upstream node '{source}' produced no output"
            break
        if source_output not in node_outputs[source]:
            skip_reason = f"branch '{source_output}' of node '{source}' was not taken"
            break
    if skip_reason is not None:
        await finish(
            NodeRunResult(node_id=nid, status=NodeStatus.skipped, error=skip_reason)
        )
        return run_status

    await emit({"type": "node_started", "node_id": nid})
    started = time.time()

    try:
        node_def = registry.get(graph_node.type)
    except KeyError as exc:
        run_status = RunStatus.error
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.error, error=str(exc),
                started_at=started, finished_at=time.time(),
            )
        )
        return run_status

    output_names = (
        graph_node.outputs_override
        or [o.name for o in node_def.manifest.outputs]
        or ["main"]
    )

    if graph_node.disabled:
        passthrough: Any = None
        for port in node_def.manifest.inputs:
            if port.name in edges_in:
                source, source_output = edges_in[port.name]
                passthrough = node_outputs[source][source_output]
                break
        outputs = {output_names[0]: passthrough}
        node_outputs[nid] = outputs
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.success, outputs=outputs,
                started_at=started, finished_at=time.time(),
            )
        )
        return run_status

    if getattr(graph_node, "tool_mode", False):
        # Tool-mode node: don't run in the data flow. Emit a ToolAdapter on
        # the `tool` output so it flows into the AI Agent's tool port; the
        # node's function runs deferred when the agent invokes the tool.
        try:
            adapter = build_node_tool_adapter(node_def, graph_node)
        except Exception as exc:  # noqa: BLE001 - surface a clean node error
            run_status = RunStatus.error
            await finish(
                NodeRunResult(
                    node_id=nid, status=NodeStatus.error,
                    error=f"{type(exc).__name__}: {exc}",
                    started_at=started, finished_at=time.time(),
                )
            )
            return run_status
        tool_outputs = {TOOL_MODE_OUTPUT: adapter}
        node_outputs[nid] = tool_outputs
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.success, outputs=tool_outputs,
                started_at=started, finished_at=time.time(),
                node_type_version=node_def.manifest.version,
            )
        )
        return run_status

    kwargs: dict[str, Any] = {}
    for port in node_def.manifest.inputs:
        if port.name in edges_in:
            source, source_output = edges_in[port.name]
            kwargs[port.name] = node_outputs[source][source_output]

    try:
        _auto_expand_dataset_inputs(node_def, kwargs, graph_node.type)
        _validate_input_kinds(node_def, kwargs, nid)
    except (ValueError, RuntimeError) as exc:
        run_status = RunStatus.error
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.error, error=str(exc),
                started_at=started, finished_at=time.time(),
            )
        )
        return run_status

    missing: list[str] = []
    for spec in node_def.manifest.params:
        if spec.name in kwargs:
            continue
        if spec.name in graph_node.params:
            kwargs[spec.name] = graph_node.params[spec.name]
        elif spec.required:
            missing.append(spec.name)

    if missing:
        run_status = RunStatus.error
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.error,
                error=f"missing required parameters: {', '.join(missing)}",
                started_at=started, finished_at=time.time(),
            )
        )
        return run_status

    first_input_name = (
        node_def.manifest.inputs[0].name
        if node_def.manifest.inputs
        else None
    )
    expr_context = build_context(
        first_input=kwargs.get(first_input_name) if first_input_name else None,
        inputs={
            p.name: kwargs.get(p.name)
            for p in node_def.manifest.inputs
            if p.name in kwargs
        },
        node_outputs=node_outputs,
    )
    for spec in node_def.manifest.params:
        if spec.name in kwargs:
            kwargs[spec.name] = evaluate(kwargs[spec.name], expr_context)

    timeout = _node_timeout(
        graph_node.type, graph_node.timeout_seconds, default_timeouts
    )
    attempts = (
        max(1, graph_node.retries + 1) if graph_node.retry_on_fail else 1
    )
    caught: Exception | None = None
    outputs: dict[str, Any] | None = None
    debug: dict[str, Any] = {}
    log_buf: list[str] = []
    log_token = _log_capture.set(log_buf)
    debug_token = node_debug.set(debug)
    node_token = current_node_id.set(nid)
    if node_def.accepts_var_keyword or not node_def.param_names:
        base_call_kwargs = dict(kwargs)
    else:
        base_call_kwargs = {
            k: v for k, v in kwargs.items() if k in node_def.param_names
        }

    async def invoke_node(current_kwargs: dict[str, Any]) -> Any:
        if node_def.is_async:
            if timeout is not None:
                return await asyncio.wait_for(
                    node_def.func(**current_kwargs), timeout
                )
            return await node_def.func(**current_kwargs)
        if graph_node.type in PROCESS_ISOLATED_NODE_TYPES:
            _key = pool_key.get()
            loop = asyncio.get_running_loop()  # MINOR: always inside a running loop
            fn_with_kwargs = functools.partial(node_def.func, **current_kwargs)
            fut = loop.run_in_executor(_get_process_pool(key=_key), fn_with_kwargs)
            try:
                return await asyncio.wait_for(fut, timeout)
            except TimeoutError:
                _evict_pool(_key)
                raise
            except concurrent.futures.process.BrokenProcessPool as exc:
                # Child died (segfault / OOM / unpicklable arg). Recycle
                # the pool so the next attempt gets a fresh one and
                # re-raise as a normal ValueError so the node fails cleanly.
                _evict_pool(_key)
                raise ValueError(
                    "code node crashed: subprocess died (possible "
                    "out-of-memory, segfault, or unpicklable value)"
                ) from exc
        if timeout is not None:
            return await asyncio.wait_for(
                asyncio.to_thread(node_def.func, **current_kwargs), timeout
            )
        # Always run synchronous nodes in a thread — even without a timeout.
        # Calling node_def.func() directly blocks the event loop for the
        # node's full duration, stalling all concurrent runs and heartbeats.
        return await asyncio.to_thread(node_def.func, **current_kwargs)

    async def resolve_agent_actions(raw: Any, current_kwargs: dict[str, Any]) -> Any:
        next_raw = raw
        call_kwargs = current_kwargs
        _loop_iter = 0
        while isinstance(next_raw, AgentActionRequest):
            response = await _dispatch_agent_action_request(
                next_raw,
                agent_node_id=nid,
                tool_values=call_kwargs.values(),
                emit=emit,
                pause_on_approval=pause_on_approval,
            )
            if not (
                node_def.accepts_var_keyword or "agent_resume" in node_def.param_names
            ):
                return response
            _loop_iter += 1
            if _loop_iter >= _MAX_AGENT_LOOP_ITERATIONS:
                raise RuntimeError(
                    f"agent node {nid!r} exceeded {_MAX_AGENT_LOOP_ITERATIONS} "
                    "resolve iterations — the node appears to return an "
                    "AgentActionRequest without advancing its step counter"
                )
            call_kwargs = dict(call_kwargs)
            call_kwargs["agent_resume"] = response.as_resume_input()
            next_raw = await invoke_node(call_kwargs)
        return next_raw

    try:
        for attempt in range(attempts):
            try:
                call_kwargs = dict(base_call_kwargs)
                raw = agent_action_resume.get(nid)
                if raw is None:
                    raw = await invoke_node(call_kwargs)
                raw = await resolve_agent_actions(raw, call_kwargs)
                outputs = _normalize_outputs(raw, output_names, graph_node.type)
                if graph_node.type in AUTO_PROMOTE_NODE_TYPES:
                    outputs = _auto_promote_outputs(outputs)
                _validate_output_kinds(node_def, outputs, nid)
                if max_node_output_bytes is not None and max_node_output_bytes > 0:
                    try:
                        approx = _approx_encoded_length(outputs)
                    except (TypeError, ValueError):
                        approx = 0
                    if approx > max_node_output_bytes:
                        raise ValueError(
                            f"node output of {approx} bytes exceeds limit of "
                            f"{max_node_output_bytes} bytes"
                        )
                caught = None
                break
            except AgentApprovalRequired as exc:
                caught = exc
                break
            except Exception as exc:  # noqa: BLE001
                caught = exc
                if attempt + 1 < attempts and graph_node.retry_wait_seconds > 0:
                    wait = graph_node.retry_wait_seconds
                    delay = wait * (2**attempt if graph_node.retry_backoff else 1)
                    delay += random.uniform(0, wait * 0.1)
                    await asyncio.sleep(delay)
    finally:
        current_node_id.reset(node_token)
        node_debug.reset(debug_token)
        _log_capture.reset(log_token)
    logs = "".join(log_buf).splitlines()

    if caught is None and outputs is not None:
        node_outputs[nid] = outputs
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.success, outputs=outputs,
                logs=logs, debug=debug,
                started_at=started, finished_at=time.time(),
                node_type_version=node_def.manifest.version,
            )
        )
        return run_status

    if isinstance(caught, AgentApprovalRequired):
        run_status = RunStatus.waiting
        debug["agent_approval_state"] = {
            "agent_node_id": nid,
            "approval_key": caught.approval_key,
            "tool_call_id": caught.tool_call.id,
            "tool_name": caught.tool_call.name,
            "request": caught.request.model_dump(mode="json"),
        }
        await finish(
            NodeRunResult(
                node_id=nid,
                status=NodeStatus.waiting,
                error=caught.message,
                logs=logs,
                debug=debug,
                started_at=started,
                finished_at=time.time(),
                node_type_version=node_def.manifest.version,
            )
        )
        return run_status

    if isinstance(caught, (TimeoutError, asyncio.TimeoutError)):
        error_msg = f"node timed out after {timeout}s"
    else:
        error_msg = f"{type(caught).__name__}: {caught}"
        notes = getattr(caught, "__notes__", None) or ()
        if notes:
            error_msg = "\n\n".join((error_msg, *notes))
    continue_on_error = (
        graph_node.on_error == "continue" or graph_node.always_output_data
    )
    if continue_on_error:
        fallback_outputs = {output_names[0]: None}
        node_outputs[nid] = fallback_outputs
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.error, error=error_msg,
                outputs=fallback_outputs, logs=logs, debug=debug,
                started_at=started, finished_at=time.time(),
            )
        )
    else:
        run_status = RunStatus.error
        await finish(
            NodeRunResult(
                node_id=nid, status=NodeStatus.error, error=error_msg,
                logs=logs, debug=debug,
                started_at=started, finished_at=time.time(),
            )
        )
    return run_status


class _LoopRowError(Exception):
    """Raised inside a loop iteration to abort the whole loop (on_error=fail)."""

    def __init__(self, index: int) -> None:
        self.index = index


def _restricted_levels(graph: WorkflowGraph, node_ids: frozenset[str]) -> list[list[str]]:
    """Topo levels over the induced subgraph on ``node_ids`` only."""
    node_index = {n.id: i for i, n in enumerate(graph.nodes)}
    preds = {nid: set() for nid in node_ids}
    for e in graph.edges:
        if e.source in node_ids and e.target in node_ids:
            preds[e.target].add(e.source)
    indeg = {nid: len(p) for nid, p in preds.items()}
    succ: dict[str, set[str]] = defaultdict(set)
    for nid, p in preds.items():
        for s in p:
            succ[s].add(nid)
    remaining = set(node_ids)
    levels: list[list[str]] = []
    while remaining:
        level = sorted([n for n in remaining if indeg[n] == 0], key=lambda n: node_index[n])
        if not level:
            raise GraphError("cycle inside loop body")
        levels.append(level)
        for n in level:
            remaining.remove(n)
            for s in succ[n]:
                indeg[s] -= 1
    return levels


def _as_loop_rows(value: Any, *, max_rows: int) -> list[Any]:
    """Resolve a loop_start input into an ordered list of rows (one per item)."""
    from noodle.datasets import is_dataset_ref, materialize_dataset_rows
    if is_dataset_ref(value):
        total_cap = max(1, int(max_rows or 10000))
        return materialize_dataset_rows(value, cap=total_cap, allow_truncate=False)
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _loop_items(
    value: Any,
    *,
    mode: str = "each",
    batch_size: int = 1,
    group_key: str = "",
    count: int = 0,
    start: int = 0,
    step: int = 1,
    max_rows: int = 10000,
) -> list[Any]:
    """Resolve a loop_start input into the ordered list of iteration *units*.

    The unit shape depends on ``mode``:
      * each   -> one row per unit
      * batch  -> a list of up to ``batch_size`` rows per unit (non-overlapping)
      * group  -> {"key": k, "rows": [...]} per distinct ``group_key`` value
      * range  -> the integers start, start+step, ... (``count`` of them)
      * window -> overlapping sliding windows of ``batch_size`` rows, sliding
                  by ``step``; only full windows are produced
    """
    if mode == "range":
        n = max(0, int(count or 0))
        st = int(step or 1) or 1
        return [int(start) + k * st for k in range(n)]

    rows = _as_loop_rows(value, max_rows=max_rows)

    if mode == "batch":
        size = max(1, int(batch_size or 1))
        return [rows[i : i + size] for i in range(0, len(rows), size)]

    if mode == "window":
        size = max(1, int(batch_size or 1))
        slide = max(1, int(step or 1))
        return [rows[i : i + size] for i in range(0, len(rows) - size + 1, slide)]

    if mode == "group":
        groups: dict[Any, dict[str, Any]] = {}
        order: list[Any] = []
        for row in rows:
            key = row.get(group_key) if isinstance(row, dict) else None
            if key not in groups:
                groups[key] = {"key": key, "rows": []}
                order.append(key)
            groups[key]["rows"].append(row)
        return [groups[k] for k in order]

    # "each" (and any unknown mode) -> one row per unit.
    return rows


async def _run_loop(
    *,
    region: "LoopRegion",
    graph: WorkflowGraph,
    registry: NodeRegistry,
    nodes_by_id: dict[str, Any],
    incoming: dict[str, dict[str, tuple[str, str]]],
    node_outputs: dict[str, dict[str, Any]],
    cache: dict[str, dict[str, Any]],
    emit: EventCallback,
    finish: Callable[[NodeRunResult], Awaitable[None]],
    default_timeouts: dict[str, float],
    max_node_output_bytes: int | None,
    pause_on_approval: bool,
    agent_action_resume: dict[str, AgentActionRequest],
    loop_regions: dict[str, "LoopRegion"],
    owned: set[str],
) -> RunStatus:
    """Drive a loop region: resolve its input into items and run the body
    sub-DAG once per item, collecting each iteration's value flowing into
    Loop End in item order."""
    start = nodes_by_id[region.start_id]
    mode = str(start.params.get("mode", "each") or "each")
    concurrency = max(1, int(start.params.get("concurrency", 1) or 1))
    on_error = str(start.params.get("on_error", "fail") or "fail")
    max_rows = int(start.params.get("max_rows", 10000) or 10000)
    batch_size = int(start.params.get("batch_size", 1) or 1)
    group_key = str(start.params.get("group_key", "") or "")
    count = int(start.params.get("count", 0) or 0)
    range_start = int(start.params.get("start", 0) or 0)
    step = int(start.params.get("step", 1) or 1)

    # The loop_start input is the value on its 'input' port (from the graph).
    # range mode ignores the input.
    start_in = incoming.get(region.start_id, {})
    raw_input: Any = None
    if "input" in start_in:
        src, out = start_in["input"]
        raw_input = (node_outputs.get(src) or {}).get(out)
    items = _loop_items(
        raw_input, mode=mode, batch_size=batch_size, group_key=group_key,
        count=count, start=range_start, step=step, max_rows=max_rows,
    )

    if len(items) > max_rows:
        node_outputs[region.end_id] = {"results": [], "errors": []}
        await finish(NodeRunResult(
            node_id=region.end_id, status=NodeStatus.error,
            error=f"loop received {len(items)} rows but max_rows is {max_rows}",
        ))
        return RunStatus.error

    # Multi-tenancy C5: the org's loop-iteration ceiling beats the node's own
    # max_rows. Reject (never truncate) so quota pressure is always visible.
    org_loop_cap = int((org_run_limits.get() or {}).get("max_loop_iterations") or 0)
    if org_loop_cap and len(items) > org_loop_cap:
        node_outputs[region.end_id] = {"results": [], "errors": []}
        await finish(NodeRunResult(
            node_id=region.end_id, status=NodeStatus.error,
            error=(
                f"loop received {len(items)} rows but this organization's "
                f"iteration cap is {org_loop_cap}"
            ),
        ))
        return RunStatus.error

    # Skip-set for the body run: only nodes owned by *directly nested* loops
    # (their own driver handles them). This loop's own direct body nodes run.
    child_owned: set[str] = set()
    for other in loop_regions.values():
        if other.parent_start_id == region.start_id:
            child_owned |= set(other.body_ids)
            child_owned.add(other.end_id)

    body_levels = _restricted_levels(graph, region.body_ids)
    # The node + port feeding loop_end.input, captured per iteration.
    end_in = incoming.get(region.end_id, {}).get("input")

    # Reduce: thread an accumulator across the (fixed) for-each units. Sequential
    # by nature; the `item` port carries {"acc": <accumulator>, "item": <unit>}
    # and the value into Loop End becomes the next accumulator.
    if bool(start.params.get("accumulate", False)):
        from noodle.expr import build_context, evaluate
        acc = evaluate(
            start.params.get("initial"),
            build_context(node_outputs=node_outputs),
        )
        err = _expr_error(acc)
        if err is not None:
            node_outputs[region.end_id] = {"results": None, "errors": []}
            await finish(NodeRunResult(
                node_id=region.end_id, status=NodeStatus.error,
                error=f"loop initial state {err}",
            ))
            return RunStatus.error
        for i, unit in enumerate(items):
            path_token = iteration_path.set(iteration_path.get() + (i,))
            try:
                iter_outputs = dict(node_outputs)
                iter_outputs[region.start_id] = {
                    "item": {"acc": acc, "item": unit}, "index": i, "state": acc,
                }
                st = await _execute_nodes(
                    node_ids=set(region.body_ids),
                    levels=body_levels, graph=graph, registry=registry,
                    nodes_by_id=nodes_by_id, incoming=incoming,
                    node_outputs=iter_outputs, cache=cache,
                    emit=emit, finish=finish, default_timeouts=default_timeouts,
                    max_node_output_bytes=max_node_output_bytes,
                    pause_on_approval=pause_on_approval,
                    agent_action_resume=agent_action_resume,
                    loop_regions=loop_regions, owned=child_owned,
                )
            finally:
                iteration_path.reset(path_token)
            if st is RunStatus.error:
                node_outputs[region.end_id] = {"results": None, "errors": []}
                await finish(NodeRunResult(
                    node_id=region.end_id, status=NodeStatus.error,
                    error=f"loop reduce iteration {i} failed",
                ))
                return RunStatus.error
            if end_in is not None:
                esrc, eout = end_in
                acc = (iter_outputs.get(esrc) or {}).get(eout)
            iter_outputs.clear()
        out = {"results": acc, "errors": []}
        node_outputs[region.end_id] = out
        await finish(NodeRunResult(
            node_id=region.end_id, status=NodeStatus.success, outputs=out,
        ))
        return RunStatus.success

    collected: list[tuple[int, Any]] = []
    errors: list[dict] = []
    sem = asyncio.Semaphore(concurrency)

    async def _one_iteration(i: int, item: Any) -> None:
        async with sem:
            path_token = iteration_path.set(iteration_path.get() + (i,))
            try:
                iter_outputs = dict(node_outputs)  # inherit upstream values
                iter_outputs[region.start_id] = {"item": item, "index": i}
                st = await _execute_nodes(
                    node_ids=set(region.body_ids),
                    levels=body_levels, graph=graph, registry=registry,
                    nodes_by_id=nodes_by_id, incoming=incoming,
                    node_outputs=iter_outputs, cache=cache,
                    emit=emit, finish=finish, default_timeouts=default_timeouts,
                    max_node_output_bytes=max_node_output_bytes,
                    pause_on_approval=pause_on_approval,
                    agent_action_resume=agent_action_resume,
                    loop_regions=loop_regions, owned=child_owned,
                )
                if st is RunStatus.error:
                    if on_error == "fail":
                        raise _LoopRowError(i)
                    errors.append({"index": i, "error": "row failed", "input": item})
                    return
                value = None
                if end_in is not None:
                    esrc, eout = end_in
                    value = (iter_outputs.get(esrc) or {}).get(eout)
                collected.append((i, value))
                # Release refs to upstream outputs so concurrent iterations
                # don't hold N copies of large upstream values simultaneously.
                iter_outputs.clear()
            finally:
                iteration_path.reset(path_token)

    if concurrency == 1:
        for i, item in enumerate(items):
            try:
                await _one_iteration(i, item)
            except _LoopRowError as exc:
                node_outputs[region.end_id] = {"results": [], "errors": errors}
                await finish(NodeRunResult(
                    node_id=region.end_id, status=NodeStatus.error,
                    error=f"loop row {exc.index} failed (on_error=fail)",
                ))
                return RunStatus.error
    else:
        # ENG-1: drive iterations as explicit tasks so an on_error="fail" abort
        # can CANCEL the still-in-flight ones. A bare ``asyncio.gather`` raises
        # the first _LoopRowError to us but leaves the other iteration coroutines
        # running detached — they keep executing body nodes (emitting events,
        # consuming compute, mutating shared state) for a run we've already marked
        # failed. Same orphaned-task class as REL-2.
        tasks = [
            asyncio.ensure_future(_one_iteration(i, it))
            for i, it in enumerate(items)
        ]
        try:
            await asyncio.gather(*tasks)
        except _LoopRowError as exc:
            for t in tasks:
                if not t.done():
                    t.cancel()
            # Drain the cancellations so no iteration runs past this point.
            await asyncio.gather(*tasks, return_exceptions=True)
            node_outputs[region.end_id] = {"results": [], "errors": errors}
            await finish(NodeRunResult(
                node_id=region.end_id, status=NodeStatus.error,
                error=f"loop row {exc.index} failed (on_error=fail)",
            ))
            return RunStatus.error

    collected.sort(key=lambda t: t[0])
    values = [v for _, v in collected]
    end = nodes_by_id[region.end_id]
    if str(end.params.get("output_mode", "records") or "records") == "dataset":
        from noodle.datasets import dataset_from_records
        rows = [v if isinstance(v, dict) else {"result": v} for v in values]
        results_out: Any = dataset_from_records(rows, name="loop_output.parquet")
    else:
        results_out = values
    out = {"results": results_out, "errors": errors}
    node_outputs[region.end_id] = out
    await finish(NodeRunResult(
        node_id=region.end_id, status=NodeStatus.success, outputs=out,
    ))
    return RunStatus.success


def _expr_error(value: Any) -> str | None:
    """Return the message if ``value`` is an expression-eval error sentinel."""
    if isinstance(value, str) and value.startswith("[expr error"):
        return value
    return None


async def _run_conditional_loop(
    *,
    region: "LoopRegion",
    graph: WorkflowGraph,
    registry: NodeRegistry,
    nodes_by_id: dict[str, Any],
    incoming: dict[str, dict[str, tuple[str, str]]],
    node_outputs: dict[str, dict[str, Any]],
    cache: dict[str, dict[str, Any]],
    emit: EventCallback,
    finish: Callable[[NodeRunResult], Awaitable[None]],
    default_timeouts: dict[str, float],
    max_node_output_bytes: int | None,
    pause_on_approval: bool,
    agent_action_resume: dict[str, AgentActionRequest],
    loop_regions: dict[str, "LoopRegion"],
    owned: set[str],
) -> RunStatus:
    """Drive a while/until loop: thread an accumulator (state) across iterations,
    re-checking an expression condition each time, until it stops or a safety cap
    is hit. Sequential by construction (each iteration depends on the previous
    state). Delivers the v2 accumulator + break behavior."""
    from noodle.expr import _wrap, build_context, evaluate

    start = nodes_by_id[region.start_id]
    end = nodes_by_id[region.end_id]
    mode = str(start.params.get("mode", "while") or "while")
    max_iterations = max(0, int(start.params.get("max_iterations", 1000) or 1000))
    # Multi-tenancy C5: the org's iteration ceiling clamps the node's own cap
    # (while/until loops have no row count to reject up front).
    org_loop_cap = int((org_run_limits.get() or {}).get("max_loop_iterations") or 0)
    if org_loop_cap and (max_iterations == 0 or max_iterations > org_loop_cap):
        max_iterations = org_loop_cap
    on_max = str(start.params.get("on_max_iterations", "fail") or "fail")
    condition_expr = start.params.get("condition", "")
    conditional_output = str(
        end.params.get("conditional_output", "final_state") or "final_state"
    )

    async def _fail(message: str) -> RunStatus:
        node_outputs[region.end_id] = {"results": None, "errors": []}
        await finish(NodeRunResult(
            node_id=region.end_id, status=NodeStatus.error, error=message,
        ))
        return RunStatus.error

    def _ctx(state: Any, i: int) -> dict[str, Any]:
        ctx = build_context(first_input=state, node_outputs=node_outputs)
        ctx["state"] = _wrap(state)
        ctx["index"] = i
        return ctx

    # Seed state from the `initial` param (literal or expression).
    state = evaluate(start.params.get("initial"), _ctx(None, 0))
    err = _expr_error(state)
    if err is not None:
        return await _fail(f"loop initial state {err}")

    child_owned: set[str] = set()
    for other in loop_regions.values():
        if other.parent_start_id == region.start_id:
            child_owned |= set(other.body_ids)
            child_owned.add(other.end_id)

    body_levels = _restricted_levels(graph, region.body_ids)
    end_in = incoming.get(region.end_id, {}).get("input")

    states: list[Any] = []
    i = 0
    hit_cap = False
    while True:
        if i >= max_iterations:
            hit_cap = True
            break
        keep = evaluate(condition_expr, _ctx(state, i)) if condition_expr else False
        err = _expr_error(keep)
        if err is not None:
            return await _fail(f"loop condition {err}")
        go = bool(keep) if mode == "while" else (not bool(keep))
        if not go:
            break

        path_token = iteration_path.set(iteration_path.get() + (i,))
        try:
            iter_outputs = dict(node_outputs)
            iter_outputs[region.start_id] = {"item": state, "index": i, "state": state}
            st = await _execute_nodes(
                node_ids=set(region.body_ids),
                levels=body_levels, graph=graph, registry=registry,
                nodes_by_id=nodes_by_id, incoming=incoming,
                node_outputs=iter_outputs, cache=cache,
                emit=emit, finish=finish, default_timeouts=default_timeouts,
                max_node_output_bytes=max_node_output_bytes,
                pause_on_approval=pause_on_approval,
                agent_action_resume=agent_action_resume,
                loop_regions=loop_regions, owned=child_owned,
            )
        finally:
            iteration_path.reset(path_token)

        # A body failure makes the next state uncomputable; abort rather than
        # spin forever on the same state (on_error=continue does not apply here).
        if st is RunStatus.error:
            return await _fail(f"loop iteration {i} failed")

        new_state = None
        if end_in is not None:
            esrc, eout = end_in
            new_state = (iter_outputs.get(esrc) or {}).get(eout)
        state = new_state
        if conditional_output == "all_states":
            states.append(state)
        iter_outputs.clear()
        i += 1

    logs: list[str] = []
    if hit_cap:
        if on_max == "fail":
            return await _fail(
                f"loop exceeded max_iterations ({max_iterations})"
            )
        logs.append(
            f"loop stopped at the max_iterations cap ({max_iterations}); "
            "emitting the current state"
        )

    if conditional_output == "all_states":
        results_out: Any = {"final": state, "states": states}
    else:
        results_out = state
    out = {"results": results_out, "errors": []}
    node_outputs[region.end_id] = out
    await finish(NodeRunResult(
        node_id=region.end_id, status=NodeStatus.success, outputs=out, logs=logs,
    ))
    return RunStatus.success


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
        aug_nodes.append({"id": sid, "type": "__metanode_input__", "params": {},
                          "position": {"x": 0, "y": 0}})
        cache[sid] = {"main": value}
        for t in p.get("targets") or []:
            aug_edges.append({
                "id": f"{sid}->{t['target']}", "source": sid, "source_output": "main",
                "target": t["target"], "target_input": t.get("target_input", "input"),
            })

    try:
        sub_graph = WorkflowGraph.model_validate({"nodes": aug_nodes, "edges": aug_edges})
        sub_result = await execute(
            sub_graph, registry, cache=cache,
            default_timeouts=default_timeouts,
            max_node_output_bytes=max_node_output_bytes,
        )
    except Exception as exc:  # noqa: BLE001 - surface as a node error
        node_outputs[mid] = {}
        await finish(NodeRunResult(
            node_id=mid, status=NodeStatus.error,
            error=f"{type(exc).__name__}: {exc}",
            started_at=started, finished_at=time.time(),
        ))
        return RunStatus.error

    outputs: dict[str, Any] = {}
    for p in ports.get("outputs") or []:
        run = sub_result.nodes.get(p["source"])
        outputs[p["port"]] = (
            run.outputs.get(p.get("source_output", "main")) if run is not None else None
        )

    node_outputs[mid] = outputs
    if str(sub_result.status) == "error":
        await finish(NodeRunResult(
            node_id=mid, status=NodeStatus.error,
            error="metanode sub-graph failed", outputs=outputs,
            started_at=started, finished_at=time.time(),
        ))
        return RunStatus.error
    await finish(NodeRunResult(
        node_id=mid, status=NodeStatus.success, outputs=outputs,
        started_at=started, finished_at=time.time(),
    ))
    return RunStatus.success


async def _execute_nodes(
    *,
    node_ids: set[str],
    levels: list[list[str]],
    graph: WorkflowGraph,
    registry: NodeRegistry,
    nodes_by_id: dict[str, Any],
    incoming: dict[str, dict[str, tuple[str, str]]],
    node_outputs: dict[str, dict[str, Any]],
    cache: dict[str, dict[str, Any]],
    emit: EventCallback,
    finish: Callable[[NodeRunResult], Awaitable[None]],
    default_timeouts: dict[str, float],
    max_node_output_bytes: int | None,
    pause_on_approval: bool,
    agent_action_resume: dict[str, AgentActionRequest],
    loop_regions: dict[str, "LoopRegion"],
    owned: set[str],
) -> RunStatus:
    """Run ``node_ids`` in ``levels`` order against ``node_outputs``. Returns the
    worst RunStatus seen. Loop Start nodes are intercepted and driven via
    ``_run_loop``; ``owned`` nodes (loop body + end) are skipped here — their
    loop populates them."""
    run_status = RunStatus.success
    for level in levels:
        async def _one(nid: str) -> None:
            nonlocal run_status
            if nid in owned:
                return
            gn = nodes_by_id[nid]
            if gn.type == "meta_node":
                st = await _run_metanode(
                    node=gn, incoming=incoming, node_outputs=node_outputs,
                    registry=registry, emit=emit, finish=finish,
                    default_timeouts=default_timeouts,
                    max_node_output_bytes=max_node_output_bytes,
                )
                if st is not RunStatus.success:
                    run_status = st
                return
            if gn.type == "loop_start" and nid in loop_regions:
                mode = str(gn.params.get("mode", "each") or "each")
                driver = (
                    _run_conditional_loop
                    if mode in ("while", "until")
                    else _run_loop
                )
                st = await driver(
                    region=loop_regions[nid],
                    graph=graph, registry=registry, nodes_by_id=nodes_by_id,
                    incoming=incoming, node_outputs=node_outputs, cache=cache,
                    emit=emit, finish=finish, default_timeouts=default_timeouts,
                    max_node_output_bytes=max_node_output_bytes,
                    pause_on_approval=pause_on_approval,
                    agent_action_resume=agent_action_resume,
                    loop_regions=loop_regions, owned=owned,
                )
            else:
                st = await _run_one_node(
                    nid=nid, nodes_by_id=nodes_by_id, incoming=incoming,
                    node_outputs=node_outputs, cache=cache, registry=registry,
                    emit=emit, finish=finish, default_timeouts=default_timeouts,
                    max_node_output_bytes=max_node_output_bytes,
                    pause_on_approval=pause_on_approval,
                    agent_action_resume=agent_action_resume,
                )
            if st is not RunStatus.success:
                run_status = st
        await asyncio.gather(*[_one(nid) for nid in level if nid in node_ids])
    return run_status


async def execute(
    graph: WorkflowGraph,
    registry: NodeRegistry,
    *,
    cache: dict[str, dict[str, Any]] | None = None,
    targets: Iterable[str] | None = None,
    on_event: EventCallback | None = None,
    default_timeouts: dict[str, float] | None = None,
    max_node_output_bytes: int | None = None,
    pause_on_approval: bool = False,
    agent_action_resume: dict[str, AgentActionRequest] | None = None,
) -> RunResult:
    """Run a workflow graph and return per-node results."""
    _install_capture()
    default_timeouts = DEFAULT_NODE_TIMEOUTS if default_timeouts is None else default_timeouts
    cache = cache or {}
    agent_action_resume = agent_action_resume or {}
    # Transparent metanodes are purely organizational: inline them before any
    # planning so the rest of the engine sees an ordinary flat graph.
    graph = _expand_metanodes(graph)
    target_set = set(targets) if targets is not None else None
    needed = _needed_nodes(graph, target_set, cache)
    _validate_connection_kinds(graph, registry, needed)
    levels = _topo_levels(graph)
    nodes_by_id = {n.id: n for n in graph.nodes}

    incoming: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    for edge in graph.edges:
        incoming[edge.target][edge.target_input] = (edge.source, edge.source_output)

    node_outputs: dict[str, dict[str, Any]] = {}
    results: dict[str, NodeRunResult] = {}

    async def emit(event: dict[str, Any]) -> None:
        if on_event is None:
            return
        path = iteration_path.get()
        if path and "iteration_path" not in event:
            event = {**event, "iteration_path": list(path)}
        await on_event(event)

    async def finish(result: NodeRunResult) -> None:
        results[result.node_id] = result
        duration_ms = None
        if result.started_at is not None and result.finished_at is not None:
            duration_ms = int((result.finished_at - result.started_at) * 1000)
        await emit(
            {
                "type": "node_finished",
                "node_id": result.node_id,
                "status": str(result.status),
                "outputs": result.outputs,
                "error": result.error,
                "logs": result.logs,
                "debug": result.debug,
                "started_at": result.started_at,
                "finished_at": result.finished_at,
                "duration_ms": duration_ms,
                "node_type_version": result.node_type_version,
            }
        )

    loop_regions = _loop_regions(graph)
    if loop_regions:
        _validate_loop_regions(graph, loop_regions)
    owned: set[str] = set()
    for r in loop_regions.values():
        owned |= set(r.body_ids)
        owned.add(r.end_id)

    # Execute level by level; nodes within a level have no interdependencies
    # and can run in parallel via asyncio.gather. Loop Start nodes are
    # intercepted by _execute_nodes and driven over their body sub-DAG.
    run_status = await _execute_nodes(
        node_ids=needed,
        levels=levels,
        graph=graph,
        registry=registry,
        nodes_by_id=nodes_by_id,
        incoming=incoming,
        node_outputs=node_outputs,
        cache=cache,
        emit=emit,
        finish=finish,
        default_timeouts=default_timeouts,
        max_node_output_bytes=max_node_output_bytes,
        pause_on_approval=pause_on_approval,
        agent_action_resume=agent_action_resume,
        loop_regions=loop_regions,
        owned=owned,
    )

    return RunResult(status=run_status, nodes=results)


def run(
    graph: WorkflowGraph,
    registry: NodeRegistry,
    *,
    cache: dict[str, dict[str, Any]] | None = None,
    targets: Iterable[str] | None = None,
) -> RunResult:
    """Synchronous wrapper around :func:`execute` for scripts and exports."""
    return asyncio.run(execute(graph, registry, cache=cache, targets=targets))
