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
    tracing.setup_tracing("noodle-test", exporter=exp)
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
    tracing.setup_tracing("noodle-test-again")  # second call: no-op, no raise
    assert tracing.enabled() is True


def test_node_span_synthesis_under_run_execute(exporter):
    with tracing.span("run.execute", attributes={"noodle.run_id": "r1"}):
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
    assert node.attributes["noodle.node_id"] == "n1"
    assert node.attributes["noodle.node_type"] == "code"
    assert node.attributes["noodle.status"] == "success"
    assert node.attributes["noodle.org_id"] == "org-1"
    assert node.attributes["noodle.iteration_path"] == "0/2"
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
