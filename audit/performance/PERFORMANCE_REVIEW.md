# Performance & Scalability Review (Synthesis)

Independent audit, 2026-06-16. Synthesizes execution-engine (06), queue/workers
(07), database (04), observability (10), and frontend canvas (02) reviews.

## Strengths (verified)
- **Durable queue is genuinely production-grade**: Postgres
  `SELECT … FOR UPDATE SKIP LOCKED` for contention-free multi-worker leasing,
  Redis pub/sub for low-latency wakeup (with a polling fallback), org-fair
  anti-starvation pre-pass, exponential backoff, dead-letter + replay, and a
  crash-recovery reaper for expired leases. **Recommendation: keep this custom
  queue** — it is well-suited and avoids a Celery/RQ dependency.
- **Execution engine scales correctly**: Kahn topological sort with cycle
  detection, dependency-counting concurrency, cancellation hygiene, and **bounded
  dynamic fan-out** (loop/map caps) so a runaway loop can't exhaust workers.
- **Warm subprocess pool per environment**: amortizes interpreter + import cost
  (pandas/boto3) across runs — the core perf bet of the "Python-native at native
  speed" model.
- **Frontend canvas**: module-scope `nodeTypes`/`edgeTypes` (the critical React
  Flow rule), memoized derivations, atomic Zustand selectors, bounded undo
  history — re-renders stay surgical.
- **DB schema is well-indexed** with FK/index alignment migrations; tracing wraps
  SQLAlchemy so slow queries are observable when OTel is on.

## Issues / scaling watch-items
| ID | Severity | Issue | Recommendation |
|---|---|---|---|
| ENGINE-1 | Low-Med | `max_node_concurrency` never wired → a single wide static DAG runs **unbounded** node concurrency (dynamic fan-out IS bounded) | Wire the cap into the static-DAG scheduler; default to a sane N |
| ENGINE-2 | Info | Default run/node wall-clock cap is 0 (unlimited) | Set non-zero defaults for any SaaS/shared deployment |
| QUEUE-1 | Low | Fair pre-pass caps at 5 orgs/lease — possible ~1s wasted tick under heavy MT contention | Tune cap or make adaptive; minor |
| QUEUE-3 / OBS-3 | Low-Med | No queue-depth/lag/dead-letter/reclaim **metrics** surfaced (OTel tracing exists; counters don't) | Expose `/metrics` or OTel metrics — these are the ops alert signals |
| AUTH-5 | Low-Med | Login rate-limit in-process (N× looser behind replicas) | Redis-backed counter |
| FE-5/6 | Low | No React Flow virtualization; `NodeCard` memo unverified | Profile-driven only; don't add speculatively |
| DB-2 | Low | 51 migrations on 0.0.1 | Squash a baseline before OSS release (cold-start migration time) |

## Top performance recommendations (priority order)
1. **Wire `max_node_concurrency`** (ENGINE-1): the one real unbounded-resource
   path. A 200-wide fan-out DAG today schedules all 200 nodes at once.
2. **Set default wall-clock caps** (ENGINE-2) for any non-single-trusted-user
   deployment — prevents a single run monopolizing a worker indefinitely.
3. **Expose operational metrics** (QUEUE-3/OBS-3): queue depth by status,
   oldest-eligible lag, reclaim count, dead-letter rate, run success/fail/duration.
   The queue already computes most of these in its `stats` surface.
4. **Squash the migration baseline** (DB-2) before public release.

## Verdict
The performance architecture is **a strength**: the durable Postgres queue and the
bounded, cancellation-correct engine are the hard parts and they're done well, and
the frontend respects the React Flow performance rules. The gaps are a single
unbounded static-fan-out path (ENGINE-1, easy fix), missing default time caps
(ENGINE-2), and operational metrics for alerting (OBS-3) — none are architectural
rewrites.
