# Multi-Tenancy Phase C — Quotas, Fair Scheduling, Metering: Implementation Plan

> **Status: IMPLEMENTED 2026-06-10** (C1–C6 on `feat/multi-tenancy`,
> migrations 0045–0047). Deviations: C2 went straight to the two-pass
> org-fair lease (grouped pre-pass + per-org SKIP LOCKED select) instead of
> correlated subqueries; C3 counts `runs` at ADMISSION (hard daily ceiling)
> with compute/node_runs at completion; queue `_get` now bypasses the
> tenancy filter (internal unique-key infrastructure — scoping caused silent
> misses from mixed org contexts). Web UI chip for `by_org` backpressure
> remains a follow-up; the API exposes the data via /ops/queue.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One noisy org cannot starve the others: per-org concurrency/volume quotas, org-fair queue leasing, and compute metering (the loop/map amplification countermeasure + future billing substrate).

**Architecture:** A new `org_settings` overrides table (NULL = inherit the existing `SystemSetting` instance defaults — the singleton stays untouched, so `live_settings` keeps working); fairness folded into the existing lease query in `services/queue.py`; a `run_meters` daily-rollup table written from the run completion path; per-org limits shipped to the engine in the run request (the subprocess has no DB access), mirroring how `artifact_key_prefix` travels.

**Tech Stack:** SQLAlchemy 2 async + Alembic (next revision: `0045`), existing queue lease machinery (`FOR UPDATE SKIP LOCKED` on Postgres), engine loop/map drivers in `packages/core/noodle/engine.py` + `packages/nodes/noodle_nodes/_map.py`.

**Prereqs (already on `feat/multi-tenancy`):** org_id on `run_queue` and `runs`; `tenancy.run_as_system()`; `start_run` pins runs to their workflow's org; X4's dispatch gate shows where admission checks live.

---

### Task C1: `org_settings` table + resolution helper

**Files:**
- Modify: `apps/api/app/models.py`
- Create: `apps/api/alembic/versions/0045_org_settings.py`
- Create: `apps/api/app/services/org_limits.py`
- Test: `apps/api/tests/test_org_limits.py`

- [ ] Model (all quota columns `Integer | None`; NULL = inherit instance default, 0 = unlimited — matching the existing convention):

```python
class OrgSettings(Base):
    """Per-org quota overrides. NULL inherits the instance default
    (SystemSetting singleton / config); 0 means unlimited."""

    __tablename__ = "org_settings"

    org_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    max_concurrent_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    executions_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_map_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_loop_iterations: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_inflight_subworkflows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_quota_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] `org_limits.py`: `@dataclass EffectiveLimits` + `async def effective_limits(session, org_id) -> EffectiveLimits` resolving override → instance default (`live_settings` for `max_concurrent_runs`, `settings` for the rest; map/loop defaults come from the node-level defaults: width 10000, iterations 0=uncapped). Cache per org with a 5s TTL like `live_settings`. `GET/PUT /orgs/{org_id}/settings` endpoints (org admin read, owner write) in `routers/orgs.py`.
- [ ] Commit: `feat(tenancy): per-org quota settings table + resolution`

### Task C2: Org-fair queue leasing + org concurrency cap

**Files:**
- Modify: `apps/api/app/services/queue.py` (the lease query + `queue_reason`)
- Modify: `apps/api/app/models.py` (`ix_run_queue_lease` gains `org_id`; migration `0046_queue_org_index.py`)
- Test: `apps/api/tests/test_queue_fairness.py` (+ a `@postgres`-lane case for SKIP LOCKED)

- [ ] Lease predicate: exclude entries whose org is at its `max_concurrent_runs` (correlated count of that org's `leased|running` entries). Park them with `queue_reason="org_quota_exceeded"` instead of leasing.
- [ ] Fairness ordering: `ORDER BY <org in-flight count> ASC, priority DESC, available_at ASC` — orgs with fewer running entries lease first, so a 1000-entry backlog from org A interleaves with org B's single run instead of starving it. Verify the plan on Postgres with EXPLAIN; fall back to a two-pass lease (pick org, then entry) if the correlated sort can't use the index.
- [ ] Tests: two orgs, org A floods the queue, org B's entry leases within the first N dispatches; org at cap → entries parked with `org_quota_exceeded`, resume when capacity frees.
- [ ] Commit: `feat(tenancy): org-fair queue leasing with per-org concurrency caps`

### Task C3: `run_meters` daily rollup + executions/day quota

**Files:**
- Modify: `apps/api/app/models.py` (`RunMeter`, unique `(org_id, day)`)
- Create: `apps/api/alembic/versions/0047_run_meters.py`
- Modify: `apps/api/app/services/queue.py` completion path + `runner.py` finalize
- Test: `apps/api/tests/test_run_meters.py`

- [ ] On run completion (success/error/cancelled), upsert today's row: `runs += 1`, `compute_seconds += (finished_at - started_at)`, `node_runs += count(NodeRun where run_id)` — the count is the loop-amplification meter (a 10k-iteration loop is one run but ~10k node_runs). Use dialect upsert (`on_conflict_do_update`) with a SQLite fallback.
- [ ] Admission: `start_run` checks today's `runs` against `executions_per_day` (after the X4 gate; same ValueError pattern, message names the quota). Map child runs count naturally — they go through `start_run`.
- [ ] `GET /orgs/{org_id}/usage?days=30` for the UI/billing export.
- [ ] Commit: `feat(tenancy): per-org run metering + executions-per-day quota`

### Task C4: Per-org sub-workflow slots

**Files:**
- Modify: `apps/api/app/services/runtime_pool.py` (`subworkflow_slot`)
- Test: extend `apps/api/tests/test_runtime_pool.py` slot tests with two orgs

- [ ] Replace the single `_subworkflow_sem` with a per-org dict (`active_org_id() or "default"` → semaphore sized from `effective_limits.max_inflight_subworkflows`, falling back to the global setting). Keep the existing soft-timeout semantics verbatim (the deadlock rationale in the current docstring still applies per org). Conftest's `_reset_run_dispatch_state` must clear the dict.
- [ ] Commit: `feat(tenancy): per-org sub-workflow spawn throttle`

### Task C5: Map width / loop iteration caps in the engine

**Files:**
- Modify: `apps/api/app/services/runtime_pool.py` + `runner.py` (resolve limits at dispatch → run request field `org_limits: {"max_map_width": int, "max_loop_iterations": int}`)
- Modify: `packages/runtime/noodle_runtime/server.py` (read field → ContextVar)
- Modify: `packages/core/noodle/engine.py` loop driver + `packages/nodes/noodle_nodes/_map.py` (clamp/reject above cap with a clear node error)
- Test: `packages/core` loop test with a cap of 3 iterations → run fails with the cap message; map test rejecting `max_rows` above the org cap

- [ ] Same threading pattern as `artifact_key_prefix` (resolved once per dispatch under `run_as_system`). Reject (error the node) rather than silently truncate.
- [ ] Commit: `feat(tenancy): org caps on map fan-out and loop iterations`

### Task C6: Backpressure visibility

**Files:**
- Modify: `apps/api/app/routers/ops.py` (`/ops/queue` gains per-org queued/leased/parked counts), `apps/web/src` ops surface chip for `org_quota_exceeded`
- [ ] Commit: `feat(tenancy): org quota state in queue backpressure UI`

---

## Risks

- **The lease query is the hottest path in the system.** C2 must keep the SKIP LOCKED fast path; measure on the Postgres lane before/after. The two-pass fallback (lease per org round-robin) is the escape hatch.
- **Meter upsert contention**: one row per (org, day) is a hot row under high run volume — acceptable at self-hosted scale; note Redis buffering as the cloud-scale follow-up.
- **`node_runs` count at completion** is one COUNT per run; index `ix_node_runs_run_id_node_id` covers it.
