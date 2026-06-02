"""Shared graph helpers used by dispatch and gating logic.

These exist because multiple modules need the same answer to the same
questions ("which node is the trigger?", "what runs when this one does?")
and used to keep re-inventing it locally.
"""

from typing import Any

from noodle.models import WorkflowGraph

TRIGGER_TYPES: tuple[str, ...] = (
    "manual_trigger",
    "webhook_trigger",
    "schedule_trigger",
)


def _graph_nodes(graph: dict | WorkflowGraph) -> list[Any]:
    if isinstance(graph, WorkflowGraph):
        return list(graph.nodes)
    return list((graph or {}).get("nodes") or [])


def _graph_edges(graph: dict | WorkflowGraph) -> list[Any]:
    if isinstance(graph, WorkflowGraph):
        return list(graph.edges)
    return list((graph or {}).get("edges") or [])


def _node_id(node: Any) -> str | None:
    if isinstance(node, dict):
        nid = node.get("id")
        return nid if isinstance(nid, str) else None
    return getattr(node, "id", None)


def _node_type(node: Any) -> str | None:
    if isinstance(node, dict):
        nt = node.get("type")
        return nt if isinstance(nt, str) else None
    return getattr(node, "type", None)


def _edge_endpoints(edge: Any) -> tuple[str | None, str | None]:
    if isinstance(edge, dict):
        return edge.get("source"), edge.get("target")
    return getattr(edge, "source", None), getattr(edge, "target", None)


def forward_descendants(
    graph: dict | WorkflowGraph, seeds: set[str]
) -> set[str]:
    """Return ``seeds`` plus every node reachable by following outgoing edges."""
    by_source: dict[str, list[str]] = {}
    for edge in _graph_edges(graph):
        src, tgt = _edge_endpoints(edge)
        if src and tgt:
            by_source.setdefault(src, []).append(tgt)
    visited: set[str] = set(seeds)
    queue = list(seeds)
    while queue:
        nid = queue.pop()
        for nxt in by_source.get(nid, []):
            if nxt not in visited:
                visited.add(nxt)
                queue.append(nxt)
    return visited


def backward_ancestors(
    graph: dict | WorkflowGraph, seeds: set[str]
) -> set[str]:
    """Return ``seeds`` plus every node reachable by following incoming edges."""
    by_target: dict[str, list[str]] = {}
    for edge in _graph_edges(graph):
        src, tgt = _edge_endpoints(edge)
        if src and tgt:
            by_target.setdefault(tgt, []).append(src)
    visited: set[str] = set(seeds)
    queue = list(seeds)
    while queue:
        nid = queue.pop()
        for prev in by_target.get(nid, []):
            if prev not in visited:
                visited.add(prev)
                queue.append(prev)
    return visited


def targets_have_trigger(
    graph: dict | WorkflowGraph, targets: list[str]
) -> bool:
    """True if every target is a trigger or has a trigger somewhere upstream.

    Used to gate "Run this step" requests: a single action node should only
    run when it is wired (directly or transitively) to a trigger, so stray
    nodes like Execute Command can't fire on their own.
    """
    type_by_id: dict[str, str | None] = {}
    for node in _graph_nodes(graph):
        nid = _node_id(node)
        if nid is not None:
            type_by_id[nid] = _node_type(node)
    for target in targets:
        if type_by_id.get(target) in TRIGGER_TYPES:
            continue
        ancestors = backward_ancestors(graph, {target})
        if not any(
            type_by_id.get(a) in TRIGGER_TYPES for a in ancestors
        ):
            return False
    return True


def first_trigger_node(
    graph: dict | WorkflowGraph, *, prefer_manual: bool = False
) -> Any | None:
    """Return the first node whose type is in ``TRIGGER_TYPES``.

    When ``prefer_manual`` is True, a ``manual_trigger`` anywhere in the
    graph wins over webhook / schedule triggers — useful for the editor's
    default Run button where the user usually wants manual iteration.
    """
    nodes = _graph_nodes(graph)
    if prefer_manual:
        for node in nodes:
            if _node_type(node) == "manual_trigger":
                return node
    for node in nodes:
        if _node_type(node) in TRIGGER_TYPES:
            return node
    return None


def resolve_trigger_targets(
    graph: dict | WorkflowGraph,
    trigger_node_id: str | None,
    explicit_targets: list[str] | None,
) -> list[str] | None:
    """Compute the ``targets`` list for ``engine.execute``.

    Rules (first match wins):

    1. Explicit ``targets`` (retry / rerun / "Run this step") pass through.
    2. A ``trigger_node_id`` gates execution to that trigger plus every node
       reachable forward from it.
    3. ``None`` means "no restriction" — the engine runs the whole graph.
       Callers that supply pre-seeded ``cache`` for a trigger may pick this.

    Raises ``ValueError`` if ``trigger_node_id`` is set but doesn't name a
    valid trigger node in the graph.
    """
    if explicit_targets:
        return explicit_targets
    if trigger_node_id is None:
        return None
    match = None
    for node in _graph_nodes(graph):
        if _node_id(node) == trigger_node_id:
            match = node
            break
    if match is None:
        raise ValueError(
            f"trigger_node_id '{trigger_node_id}' is not a node in this graph"
        )
    if _node_type(match) not in TRIGGER_TYPES:
        raise ValueError(
            f"node '{trigger_node_id}' is not a trigger "
            f"(type={_node_type(match)!r})"
        )
    return sorted(forward_descendants(graph, {trigger_node_id}))
