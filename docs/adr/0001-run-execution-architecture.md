# ADR-0001: Run Execution Architecture

Status: Accepted

Date: 2026-07-03

## Context

Nodyra has one visual editor and API control plane, while workflow runs must
survive process death. Execution must support three isolation postures:
trusted subprocesses, hardened containers, and remote runner pools.

## Decision

1. Durable DB-backed queue (`RunQueueEntry`, SKIP LOCKED leasing) is the only
   dispatch path. There is no in-memory fast path.
2. The `RunExecutor` protocol seam (`executors/base.py`) separates local,
   sandbox, and remote implementations. Executors own zero persistence.
3. `runner._execute_run` is the single bookkeeping chokepoint for context
   prep, events, and terminal writes.
4. The engine hosts state through explicit kwargs and `RuntimeContext`.
   ContextVars are reserved for per-node mutable state.
5. Resume uses the checkpoint column, with `NodeRun` rows as fallback
   reconstruction source, instead of full event sourcing.

## Consequences

Executors are swappable without touching bookkeeping. Every trigger path
funnels through one admission gate for quotas, single-flight behavior, and
isolation policy. The 1 MiB checkpoint cap trades resume completeness for
bounded row size; truncation is surfaced as a run event. Split topology
requires Redis for events and Postgres for leasing, enforced by
`dispatch_topology_errors()`.

## Alternatives Rejected

Full event sourcing was rejected because storage and replay cost have no
operational win today. A Redis-primary queue was rejected because the database
is the correctness baseline and Redis is reserved as an optimization. Per
executor persistence was rejected because it would fragment the terminal-state
invariant.
