"""Sub-workflow resolution callback (A3).

The engine owns the *semantics* of calling another workflow — cycle
detection, depth limiting, inline-child execution, leaf extraction — while
each host supplies a :data:`SubworkflowRunner` that owns *resolution*:
looking up the child graph, deciding where it executes, and enforcing
host-side caps (org sub-workflow quotas, spawn throttles, child Run rows).

Hosts:

* API (``apps/api/app/services/subworkflows.py``) — DB lookup, child Run
  rows, org caps via the runtime pool's ``subworkflow_slot``.
* Runtime subprocess (``nodyra_runtime.server``) — RPC back to the host over
  the stdio protocol.
* Exporter (``nodyra_exporter``) — bundled graphs resolved locally as
  :class:`InlineSubworkflow` directives.

A resolver may answer a call two ways:

* a concrete **leaf value** — the child ran wherever the host chose;
* an :class:`InlineSubworkflow` directive — "run this prepared child graph
  yourself". The engine executes it recursively with correct depth/chain
  metadata, which is what makes inline children semantically identical to
  spawned ones (the old runner.py splice skipped chain tracking and had to
  forbid nested calls).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nodyra.context import call_chain

if TYPE_CHECKING:
    from nodyra.process_isolation import ProcessIsolator
    from nodyra.sdk import NodeRegistry


@dataclass(frozen=True)
class SubworkflowCall:
    """One request to run another workflow, built by the engine adapter."""

    workflow_id: str
    parameters: Any  # input payload seeded into the child's trigger
    use_published: bool  # False = prefer draft graphs (editor iteration)
    parent_run_id: str | None
    depth: int  # 1 = direct child of the root run
    call_chain: frozenset[str] = frozenset()  # ancestors + this workflow
    org_id: str | None = None  # tenant scope for the child workflow

    def to_payload(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "workflow_id": self.workflow_id,
            "input": self.parameters,
            "use_published": self.use_published,
            "parent_run_id": self.parent_run_id,
            "depth": self.depth,
            "call_chain": sorted(self.call_chain),
        }
        if self.org_id is not None:
            d["org_id"] = self.org_id
        return d

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SubworkflowCall:
        return cls(
            workflow_id=str(payload.get("workflow_id") or ""),
            parameters=payload.get("input"),
            use_published=bool(payload.get("use_published", True)),
            parent_run_id=payload.get("parent_run_id") or None,
            depth=int(payload.get("depth") or 1),
            call_chain=frozenset(payload.get("call_chain") or ()),
            org_id=payload.get("org_id") or None,
        )


@dataclass(frozen=True)
class SubworkflowMeta:
    """Root-run facts the host injects so calls carry correct context."""

    use_published: bool = True
    parent_run_id: str | None = None
    depth: int = 0  # depth of THIS graph (0 = root run)
    call_chain: frozenset[str] = frozenset()  # must include this graph's id
    max_depth: int = 16  # 0 = unlimited
    org_id: str | None = None  # tenant scope for child workflow calls

    def to_payload(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "use_published": self.use_published,
            "parent_run_id": self.parent_run_id,
            "depth": self.depth,
            "call_chain": sorted(self.call_chain),
            "max_depth": self.max_depth,
        }
        if self.org_id is not None:
            d["org_id"] = self.org_id
        return d

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SubworkflowMeta:
        raw_max = payload.get("max_depth")
        return cls(
            use_published=bool(payload.get("use_published", True)),
            parent_run_id=payload.get("parent_run_id") or None,
            depth=int(payload.get("depth") or 0),
            call_chain=frozenset(payload.get("call_chain") or ()),
            max_depth=cls.max_depth if raw_max is None else int(raw_max),
            org_id=payload.get("org_id") or None,
        )


@dataclass(frozen=True)
class InlineSubworkflow:
    """Resolver directive: execute this prepared child graph in-engine.

    ``graph``/``cache``/``targets`` are exactly what the host would have
    passed to a fresh child engine (credentials resolved, trigger seeded,
    targets gated). ``sources`` is the set of edge-source node ids, used for
    leaf extraction.
    """

    graph: dict
    cache: dict | None
    targets: list[str] | None
    sources: tuple[str, ...] = ()


# Returns the child's leaf value, or an InlineSubworkflow directive.
SubworkflowRunner = Callable[[SubworkflowCall], Awaitable[Any]]


def extract_leaf_value(
    sources: set[str],
    node_status: dict[str, str],
    node_outputs: dict[str, dict],
) -> Any:
    """Leaf-node output(s) of a finished child run.

    The "leaf" is any successful node no edge originates from. One leaf →
    its ``main`` output; multiple → dict keyed by node id; none → ``None``.
    (Single shared implementation of the rule previously duplicated in
    ``runner._extract_sub_leaf`` and ``nodyra_runtime.server._resolve_inline``.)
    """
    leaves = [
        nid
        for nid, status in node_status.items()
        if status == "success" and nid not in sources
    ]
    if len(leaves) == 1:
        return (node_outputs.get(leaves[0]) or {}).get("main")
    if leaves:
        return {nid: (node_outputs.get(nid) or {}).get("main") for nid in leaves}
    return None


def make_workflow_caller(
    runner: SubworkflowRunner,
    meta: SubworkflowMeta,
    registry: NodeRegistry,
    *,
    default_timeouts: dict[str, float] | None = None,
    process_isolator: ProcessIsolator | None = None,
) -> Callable[[str, Any], Awaitable[Any]]:
    """Build the ``nodyra.context.workflow_caller`` adapter for one run.

    The adapter enforces depth + cycle invariants, then delegates to the
    host resolver. Inline directives are executed here, recursively, with
    the child's meta — so every execution path shares one rulebook.
    """

    async def _run_inline(directive: InlineSubworkflow, call: SubworkflowCall) -> Any:
        # Lazy import: scheduler imports this module at top level.
        from nodyra.engine.scheduler import execute
        from nodyra.models import WorkflowGraph

        child_meta = SubworkflowMeta(
            use_published=meta.use_published,
            parent_run_id=meta.parent_run_id,
            depth=call.depth,
            call_chain=call.call_chain,
            max_depth=meta.max_depth,
            org_id=call.org_id or meta.org_id,
        )
        result = await execute(
            WorkflowGraph.model_validate(directive.graph),
            registry,
            cache=dict(directive.cache) if directive.cache else None,
            targets=list(directive.targets) if directive.targets else None,
            default_timeouts=default_timeouts,
            process_isolator=process_isolator,
            subworkflow_runner=runner,
            subworkflow_meta=child_meta,
        )
        node_status = {nid: str(r.status) for nid, r in result.nodes.items()}
        node_outputs = {nid: dict(r.outputs) for nid, r in result.nodes.items()}
        return extract_leaf_value(set(directive.sources), node_status, node_outputs)

    async def _call(workflow_id: str, input_value: Any) -> Any:
        depth = meta.depth + 1
        if meta.max_depth and depth > meta.max_depth:
            raise RuntimeError(
                f"sub-workflow depth limit ({meta.max_depth}) exceeded "
                f"at '{workflow_id}'"
            )
        chain = call_chain.get()
        if workflow_id in chain:
            raise RuntimeError(
                f"sub-workflow cycle detected — '{workflow_id}' is already running"
            )
        call = SubworkflowCall(
            workflow_id=workflow_id,
            parameters=input_value,
            use_published=meta.use_published,
            parent_run_id=meta.parent_run_id,
            depth=depth,
            call_chain=frozenset(chain | {workflow_id}),
            org_id=meta.org_id,
        )
        token = call_chain.set(chain | {workflow_id})
        try:
            outcome = await runner(call)
            if isinstance(outcome, InlineSubworkflow):
                return await _run_inline(outcome, call)
            return outcome
        finally:
            call_chain.reset(token)

    return _call
