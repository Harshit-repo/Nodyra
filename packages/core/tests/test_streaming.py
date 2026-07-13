"""Live output streaming: a node can push incremental ``node_chunk`` events
mid-execution via ``nodyra.emit_chunk``. The engine wires the emit hook around
every node (thread-safe for sync nodes running in a worker thread), and it is a
no-op when no run is listening."""

import nodyra
import nodyra_nodes  # noqa: F401 - registers loop_start/loop_end/code on the global registry
from nodyra.context import node_emitter
from nodyra.engine import execute
from nodyra.models import GraphNode, RunStatus, WorkflowGraph
from nodyra.sdk import NodeRegistry, node
from nodyra.sdk import registry as global_registry


@node(
    name="StreamBody",
    id="stream_body_test",
    inputs=["item"],
    registry=global_registry,
    hidden=True,
)
def _stream_body_test(item: int) -> int:
    """Loop-body helper that streams one chunk per iteration (test fixture)."""
    nodyra.emit_chunk(f"i{item}")
    return item


def _chunks(events: list[dict], node_id: str) -> list[str]:
    return [
        e["delta"]
        for e in events
        if e.get("type") == "node_chunk" and e.get("node_id") == node_id
    ]


async def test_sync_node_streams_chunks_in_order():
    reg = NodeRegistry()

    @node(name="Streamer", id="streamer", inputs=[], registry=reg)
    def streamer() -> dict:
        nodyra.emit_chunk("Hel")
        nodyra.emit_chunk("lo ")
        nodyra.emit_chunk("world")
        return {"text": "Hello world"}

    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    graph = WorkflowGraph(nodes=[GraphNode(id="s", type="streamer")])
    result = await execute(graph, reg, on_event=on_event)

    assert result.status == RunStatus.success
    assert _chunks(events, "s") == ["Hel", "lo ", "world"]
    # Every streamed chunk must arrive before the node's node_finished.
    finish_idx = next(
        i for i, e in enumerate(events)
        if e["type"] == "node_finished" and e["node_id"] == "s"
    )
    last_chunk_idx = max(
        i for i, e in enumerate(events)
        if e.get("type") == "node_chunk" and e.get("node_id") == "s"
    )
    assert last_chunk_idx < finish_idx


async def test_async_node_streams_chunks():
    reg = NodeRegistry()

    @node(name="AStreamer", id="astreamer", inputs=[], registry=reg)
    async def astreamer() -> dict:
        nodyra.emit_chunk("a")
        nodyra.emit_chunk("b")
        return {"ok": True}

    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    graph = WorkflowGraph(nodes=[GraphNode(id="n", type="astreamer")])
    result = await execute(graph, reg, on_event=on_event)
    assert result.status == RunStatus.success
    assert _chunks(events, "n") == ["a", "b"]


async def test_emit_chunk_is_a_noop_outside_a_run():
    # Calling the public helper with nothing listening must never raise and must
    # leave no emitter installed.
    assert node_emitter.get() is None
    nodyra.emit_chunk("ignored")
    assert node_emitter.get() is None


async def test_chunks_inside_a_loop_carry_iteration_path():
    def _n(nid, ntype, params=None):
        return {"id": nid, "type": ntype, "params": params or {}, "position": {"x": 0, "y": 0}}

    def _e(src, tgt, src_out="main", tgt_in="input"):
        return {"id": f"{src}->{tgt}", "source": src, "source_output": src_out,
                "target": tgt, "target_input": tgt_in}

    graph = WorkflowGraph.model_validate(
        {
            "nodes": [
                _n("seed", "code", {"code": "output = [1, 2]"}),
                _n("ls", "loop_start"),
                _n("b", "stream_body_test"),
                _n("le", "loop_end", {"loop_start_id": "ls"}),
            ],
            "edges": [
                _e("seed", "ls"),
                _e("ls", "b", src_out="item", tgt_in="item"),
                _e("b", "le"),
            ],
        }
    )

    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    result = await execute(graph, global_registry, on_event=on_event)
    assert result.status == RunStatus.success
    body_chunks = [
        e for e in events
        if e.get("type") == "node_chunk" and e.get("node_id") == "b"
    ]
    assert {e["delta"] for e in body_chunks} == {"i1", "i2"}
    # Each chunk is attributed to its loop iteration.
    assert all(isinstance(e.get("iteration_path"), list) for e in body_chunks)
    assert {tuple(e["iteration_path"]) for e in body_chunks} == {(0,), (1,)}
