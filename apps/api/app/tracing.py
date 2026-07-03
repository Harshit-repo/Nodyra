"""OpenTelemetry tracing for the API/worker processes (program A5).

Span tree per run (see the Phase 5 plan):

    HTTP request (FastAPIInstrumentor)
      └── run.enqueue            start_run; carrier persisted on run_queue
           └── run.lease         _execute_queued_entry (worker)
                └── run.execute  _execute_run
                     └── node.execute   one per node_finished event

``node.execute`` spans are synthesized HOST-side from the engine's
``node_finished`` events (they carry ``started_at``/``finished_at`` epoch
floats), so runtime subprocesses in user-built venvs need no OTel deps.

Everything is a no-op unless ``settings.otel_enabled`` is true, guarded by
one module boolean — the run/event hot paths pay a single ``if`` when off.
The provider is module-local (never ``trace.set_tracer_provider``) so tests
can tear down and re-create it; instrumentors get it passed explicitly.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_enabled = False
_provider: Any = None  # opentelemetry.sdk.trace.TracerProvider when enabled
_tracer: Any = None


def enabled() -> bool:
    return _enabled


def setup_tracing(service_name: str, *, exporter: Any | None = None) -> None:
    """Initialise the module-local tracer provider.

    Idempotent; a complete no-op when ``settings.otel_enabled`` is false.
    ``exporter`` overrides the OTLP/HTTP exporter (tests pass an
    ``InMemorySpanExporter``, wired through a SimpleSpanProcessor so spans
    are visible synchronously)."""
    global _enabled, _provider, _tracer
    if not settings.otel_enabled or _provider is not None:
        return
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        SimpleSpanProcessor,
    )

    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name})
    )
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    elif settings.otel_exporter_protocol == "grpc":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as GrpcExporter,
        )

        kwargs: dict[str, Any] = {}
        if settings.otel_exporter_otlp_endpoint:
            kwargs["endpoint"] = settings.otel_exporter_otlp_endpoint
        provider.add_span_processor(BatchSpanProcessor(GrpcExporter(**kwargs)))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as HttpExporter,
        )

        kwargs: dict[str, Any] = {}
        if settings.otel_exporter_otlp_endpoint:
            kwargs["endpoint"] = settings.otel_exporter_otlp_endpoint
        provider.add_span_processor(BatchSpanProcessor(HttpExporter(**kwargs)))
    _provider = provider
    _tracer = provider.get_tracer("nodyra")
    _enabled = True
    logger.info("tracing enabled service=%s", service_name)


def shutdown_tracing() -> None:
    """Flush + drop the provider. Safe to call when never set up."""
    global _enabled, _provider, _tracer
    if _provider is not None:
        try:
            _provider.shutdown()
        except Exception:  # noqa: BLE001 - teardown must never raise
            pass
    _provider = None
    _tracer = None
    _enabled = False


def flush(timeout_millis: int = 5_000) -> None:
    """Force-flush buffered spans (lifespan teardown / worker drain)."""
    if _provider is not None:
        try:
            _provider.force_flush(timeout_millis)
        except Exception:  # noqa: BLE001
            pass


def instrument_app(app: Any) -> None:
    if not _enabled:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, tracer_provider=_provider)


def instrument_sqlalchemy(async_engine: Any) -> None:
    if not _enabled:
        return
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(
        engine=async_engine.sync_engine, tracer_provider=_provider
    )


def inject_context() -> dict | None:
    """The current span context as a W3C carrier dict, or None when disabled
    (callers store the None directly — absent carrier means no tracing)."""
    if not _enabled:
        return None
    from opentelemetry.propagate import inject

    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier or None


def _context_from(carrier: dict | None) -> Any:
    if not carrier:
        return None  # None → ambient current context
    from opentelemetry.propagate import extract

    return extract(carrier)


@contextmanager
def span(
    name: str,
    *,
    carrier: dict | None = None,
    attributes: dict | None = None,
) -> Iterator[Any]:
    """Run ``name`` as the current span. Parent comes from ``carrier`` when
    given, else the ambient context. Yields the span, or None when disabled."""
    if not _enabled:
        yield None
        return
    with _tracer.start_as_current_span(
        name, context=_context_from(carrier), attributes=attributes or {}
    ) as sp:
        yield sp


def record_node_span(
    event: dict, *, node_types: dict[str, str], org_id: str | None
) -> None:
    """Synthesize a ``node.execute`` span from a ``node_finished`` event using
    the event's own timestamps (epoch seconds → ns). Parent is the ambient
    current span — the run.execute span in ``_execute_run``."""
    if not _enabled:
        return
    started = event.get("started_at")
    finished = event.get("finished_at")
    if not isinstance(started, (int, float)) or not isinstance(finished, (int, float)):
        return
    node_id = str(event.get("node_id") or "")
    attrs: dict[str, Any] = {
        "nodyra.node_id": node_id,
        "nodyra.node_type": node_types.get(node_id, ""),
        "nodyra.status": str(event.get("status") or ""),
    }
    if org_id:
        attrs["nodyra.org_id"] = org_id
    path = event.get("iteration_path")
    if isinstance(path, list) and path:
        attrs["nodyra.iteration_path"] = "/".join(str(p) for p in path)
    sp = _tracer.start_span(
        "node.execute", start_time=int(started * 1e9), attributes=attrs
    )
    sp.end(end_time=int(finished * 1e9))
