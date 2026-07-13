# ADR-0005: Defer Streaming Node Output Events

Status: Deferred

Date: 2026-07-13

## Context

The engine caps in-memory node outputs and persistence caps or offloads final
payloads. The production plan proposed optional chunked output events so large
payloads could render progressively in the UI. The same plan explicitly marked
the item as deferred unless a real workload needs it.

## Decision

Keep the current final-output model for now: nodes produce a bounded final
output, persistence caps or offloads that value, and the UI reads the final
result or DatasetRef query surfaces. Do not add a streaming output-event
contract until a concrete workload defines required ordering, backpressure,
resume, retention, and UI semantics.

## Consequences

The engine and persistence contracts stay simpler and remain compatible with
existing node authors. Very large progressive outputs should be represented as
DatasetRefs or artifacts until streaming has a validated product requirement.

## Alternatives Rejected

Adding a generic stream API without a workload was rejected because it would
create a durable protocol surface with unclear backpressure and retention
semantics. Raising output caps was rejected because it increases memory and
database pressure without solving progressive rendering.
