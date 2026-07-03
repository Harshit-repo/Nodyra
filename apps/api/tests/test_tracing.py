"""A5: tracing module unit tests. The provider is module-local, so each test
sets up against an InMemorySpanExporter and tears down via shutdown_tracing."""

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from app import tracing
from app.config import settings


@pytest.fixture
def exporter(monkeypatch):
    monkeypatch.setattr(settings, "otel_enabled", True)
    exp = InMemorySpanExporter()
    tracing.shutdown_tracing()
    tracing.setup_tracing("nodyra-test", exporter=exp)
    yield exp
    tracing.shutdown_tracing()


def test_disabled_by_default_everything_no_ops():
    tracing.shutdown_tracing()  # known-clean module state
    assert tracing.enabled() is False
    assert tracing.inject_context() is None
    with tracing.span("run.execute") as sp:
        assert sp is None
    # must not raise
    tracing.record_node_span({"node_id": "n1"}, node_types={}, org_id=None)


def test_setup_is_idempotent(exporter):
    assert tracing.enabled() is True
    tracing.setup_tracing("nodyra-test-again")  # second call: no-op, no raise
    assert tracing.enabled() is True


def test_node_span_synthesis_under_run_execute(exporter):
    with tracing.span("run.execute", attributes={"nodyra.run_id": "r1"}):
        tracing.record_node_span(
            {
                "node_id": "n1",
                "status": "success",
                "started_at": 1000.0,
                "finished_at": 1002.5,
                "iteration_path": [0, 2],
            },
            node_types={"n1": "code"},
            org_id="org-1",
        )
    spans = {s.name: s for s in exporter.get_finished_spans()}
    run, node = spans["run.execute"], spans["node.execute"]
    assert node.parent is not None
    assert node.parent.span_id == run.context.span_id
    assert node.context.trace_id == run.context.trace_id
    assert node.attributes["nodyra.node_id"] == "n1"
    assert node.attributes["nodyra.node_type"] == "code"
    assert node.attributes["nodyra.status"] == "success"
    assert node.attributes["nodyra.org_id"] == "org-1"
    assert node.attributes["nodyra.iteration_path"] == "0/2"
    assert node.start_time == 1_000_000_000_000  # 1000.0 s → ns
    assert node.end_time == 1_002_500_000_000


def test_node_span_skips_events_without_timestamps(exporter):
    tracing.record_node_span(
        {"node_id": "n1", "status": "success"}, node_types={}, org_id=None
    )
    assert exporter.get_finished_spans() == ()


def test_carrier_round_trip_connects_spans(exporter):
    with tracing.span("run.enqueue"):
        carrier = tracing.inject_context()
    assert carrier is not None and "traceparent" in carrier
    with tracing.span("run.lease", carrier=carrier):
        pass
    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert (
        spans["run.lease"].context.trace_id
        == spans["run.enqueue"].context.trace_id
    )
    assert spans["run.lease"].parent.span_id == spans["run.enqueue"].context.span_id


# ---------------------------------------------------------------------------
# Integration: a real run through start_run produces one connected trace.
# Graph/boilerplate mirror tests/test_runs.py::test_run_executes_the_graph.
# ---------------------------------------------------------------------------

from httpx import AsyncClient  # noqa: E402

GRAPH = {
    "nodes": [
        {"id": "t", "type": "manual_trigger", "params": {"data": {"n": 3}},
         "position": {"x": 0, "y": 0}},
        {"id": "c", "type": "code", "params": {"code": "output = input['n'] * 2"},
         "position": {"x": 250, "y": 0}},
    ],
    "edges": [
        {"id": "e1", "source": "t", "source_output": "main",
         "target": "c", "target_input": "input"},
    ],
}


async def test_run_produces_connected_trace(client: AsyncClient, exporter) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Traced"})).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": GRAPH})
    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] == "success"

    spans = exporter.get_finished_spans()
    by_name: dict[str, list] = {}
    for s in spans:
        by_name.setdefault(s.name, []).append(s)

    assert len(by_name.get("run.enqueue", [])) == 1
    assert len(by_name.get("run.execute", [])) == 1
    node_spans = by_name.get("node.execute", [])
    assert {s.attributes["nodyra.node_id"] for s in node_spans} == {"t", "c"}

    run_span = by_name["run.execute"][0]
    assert run_span.attributes["nodyra.run_id"] == run_id
    assert run_span.attributes["nodyra.workflow_id"] == workflow_id
    assert run_span.attributes["nodyra.status"] == "success"

    # single connected trace: every span shares the enqueue span's trace id
    trace_id = by_name["run.enqueue"][0].context.trace_id
    assert all(s.context.trace_id == trace_id for s in spans)
    # node spans hang off run.execute
    assert all(
        s.parent is not None and s.parent.span_id == run_span.context.span_id
        for s in node_spans
    )
    # node types resolved from the graph
    types = {s.attributes["nodyra.node_id"]: s.attributes["nodyra.node_type"]
             for s in node_spans}
    assert types == {"t": "manual_trigger", "c": "code"}


async def test_runtime_mode_reports_otel_flag(client: AsyncClient) -> None:
    body = (await client.get("/ops/runtime-mode")).json()
    assert body["otel_enabled"] is False
