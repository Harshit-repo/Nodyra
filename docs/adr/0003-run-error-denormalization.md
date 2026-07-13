# ADR-0003: Denormalize Run-Level Errors Onto Runs

Status: Accepted

Date: 2026-07-13

## Context

Run-level failures such as graph validation failures, timeout decisions, and
pre-node dispatch failures were historically stored only as `run_error` events.
Read paths reconstructed the user-facing error by querying events. That added
work to run detail reads and made the error disappear if retention policy
pruned the events before the run row.

## Decision

Persist the durable run-level failure reason in nullable `runs.error` when a
run reaches a terminal failure state and no node-level error owns the reason.
Read paths prefer `runs.error` and keep the event lookup only as a compatibility
fallback for older rows.

## Consequences

Run detail reads have a stable failure reason without an extra event query for
new rows, and retention can prune events without erasing the run summary. The
column is write-on-finalize state, not an event stream replacement; node-owned
errors remain on `node_runs.error`.

## Alternatives Rejected

Keeping event-only reconstruction was rejected because it made retention policy
observable as missing run errors. Duplicating all node errors onto the run row
was rejected because it would blur the node-level and run-level failure models.
