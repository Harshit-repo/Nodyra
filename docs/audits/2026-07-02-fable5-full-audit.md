# Fable 5 Noodle Full Audit, Optimization, and Product Ideas

Date: 2026-07-02 · Branch: `fable5-full-noodle-audit-optimization-product-review`
Companion document: `FABLE5_NOODLE_FULL_APP_EXPLORATION_REPORT.md` (architecture map + feature inventory in full).

---

## Executive Summary

| Dimension | Score | One-line justification |
|---|---|---|
| **Overall product** | **7.5/10** | Engine + security are launch-grade; discoverability, AI-builder breadth, and polish lag the surface area |
| Backend | 8.5/10 | Disciplined layering, fail-closed startup, durable queue; a few god files and one fresh regression (fixed) |
| Frontend | 7.5/10 | Modern stack, 439 passing tests, slices + React Query; NodeDetails.tsx (5,000 lines) and onboarding are the weak spots |
| Workflow engine | 8.5/10 | Deterministic scheduling, loop regions, checkpoints, output caps; better than most OSS competitors |
| MCP | 8/10 | 45-tool server with optimistic concurrency + dynamic workflow tools is a genuine moat; client-side needs discovery UX and trace |
| Runner/worker | 8/10 | Leases, fairness, dead-letter, drain, sandbox; head-of-line blocking bug found and fixed |
| Python-native | 7/10 | Envs, preflight, typed code-node I/O, export/import exist; not yet surfaced as a coherent "best for Python" story |
| AI-native | 6.5/10 | Builder is safe but narrow (~60 of ~400 node types allowlisted); no AI repair/test-gen/explain loops |
| Security | 8.5/10 | Two-layer tenancy, SSRF depth (DNS-rebinding re-check), CSRF, KEK-per-org; MCP httpx gap + auth-guard regression (fixed) |
| Performance | 7.5/10 | Bulk inserts, caches, budget-bounded pools; per-node checkpoint commits and unreferenced publish tasks were the notable issues |

**Biggest strengths**: the execution engine, the MCP server depth, the tenancy/security architecture, and an unusually strong test culture (2,800+ backend tests, 439 frontend tests).

**Biggest blockers**: (1) a red test suite on the branch tip — the ops auth-guard commit broke 7 tests and no-auth ops UI (fixed in this audit); (2) AI builder vocabulary far behind the node library; (3) first-run experience/discoverability of the enormous feature surface.

**Biggest opportunities**: own the "AI agent builds visual Python workflows" wedge (MCP server is already there — market it), MCP tool discovery UI, Python SDK/CLI + notebook round-trip, template gallery.

**Final recommendation**: **Beta-ready for single-tenant/self-hosted today** (after this audit's fixes). Multi-tenant hosted needs a staged rollout with the sandbox policy enforced and a real-world load soak. Details in Final Recommendation below.

---

## Architecture Map

See `FABLE5_NOODLE_FULL_APP_EXPLORATION_REPORT.md` §1 for the full map. Summary:

- `apps/api/app/` — FastAPI: 33 routers, ~60 services; middleware chain CORS→tenant-scope→auth-gate→CSRF→headers→metrics→body-cap; 12 background loops gated by `dispatch_role`/`scheduler_role` with leader election.
- `packages/core/noodle/engine/` — scheduler (dependency-counting), node_exec (retries/timeouts/isolation/hooks/streaming), loops, metanodes, subworkflows, validation.
- `apps/api/app/services/queue.py` — durable DB queue: lease/heartbeat/backoff/dead-letter/replay, org fairness, Redis wakeup.
- `apps/api/app/mcp/` + `routers/mcp.py` — MCP server (tools/resources/prompts, stateless streamable-HTTP); `services/mcp_client.py` + `noodle_nodes/mcp_tool.py` — MCP client nodes.
- `packages/nodes/` — ~400 node types in ~50 modules + integrations_v2 provider framework.
- `apps/web/src/` — React 18/Vite/zustand-slices/React Query editor with drill-in metanodes, run sidecar, AI builder panel.
- `deploy/` — compose (postgres/redis/minio/api/worker/web), Helm, hardened Dockerfile. 79 alembic migrations.

## Current Functionality Inventory

See exploration report §2. Highlights: complete = versioning/diff/rollback, durable queue, checkpoint resume, webhooks/schedules/deployments, credentials+OAuth+KEK, environments+preflight, runner pools+sandbox, MCP server+client, multi-tenancy+quotas+custom roles+SSO, audit, metrics/tracing, GitHub sync, Python export/import. Partial = AI repair/test-gen, template gallery, MCP discovery UX, per-change AI diff approval. Missing = MCP tool trace UI, CLI, notebook round-trip, data preview on wires, run comparison.

---

## Bugs Found

### BUG-1 — Ops monitoring endpoints 401 on no-auth instances (P1) — **FIXED**
- **Area**: Backend/API auth · **File**: `apps/api/app/routers/ops.py`
- **Evidence**: Commit `81c0e690` added strict `Depends(current_user)` to `GET /system/status`, `/ops/runtime-mode`, `/ops/queue`, `/ops/drain`. `current_user` raises 401 whenever no token is presented — *even when `auth_required=False`*. 7 tests failed on branch tip (`test_ops.py` ×6, `test_tracing.py` ×1). The SPA attaches a Bearer token only after login (`apps/web/src/api.ts:286`), so no-auth self-hosted installs lost the runtime-mode/queue-stats surface (`queries/index.ts:322-336`).
- **Repro**: `pytest apps/api/tests/test_ops.py` on branch tip → 6 failures (401 != 200).
- **Root cause**: wrong dependency primitive — the codebase convention for "guard when auth is on, allow anonymous when off" is `require_role`/`require_permission` (via `optional_current_user`), not `current_user`.
- **Fix**: swapped to a shared `Depends(require_role("viewer"))` route dependency. Security intent preserved: with `auth_required=True` anonymous callers still get 401 (locked in by a new regression test).

### BUG-2 — Test-suite pollution: `app.main` reload poisons `process_isolator` (P1, test infra) — **FIXED**
- **Area**: Tests · **Files**: `apps/api/tests/conftest.py`, `apps/api/tests/test_triggers.py:1363`
- **Evidence**: `test_webhook_role_disabled_blocks_production_but_keeps_test_paths` calls `importlib.reload(app.main)` while the autouse fixture has swapped `runner.process_isolator` for `_InlineTestProcessIsolator`. The reload rebinds `main.process_isolator` to the fake **permanently**; any later test running the app lifespan (e.g. `test_workflow_events.py` via `TestClient(app)`) crashes in shutdown: `'_InlineTestProcessIsolator' object has no attribute 'shutdown'`. Reproduced deterministically with the 2-file pair.
- **Fix**: gave the fake a no-op `shutdown()` (interface parity). The reload test's restore logic remains as-is.

### BUG-3 — Live-settings artifact caps silently ignored (P2) — **FIXED**
- **Area**: Runner · **File**: `apps/api/app/services/runner.py`
- **Evidence**: `_execute_run_impl` declared `live: Any = None` and never assigned it after the `_prepare_run_context` extraction; the in-process artifact store was always built with `max_bytes=None, max_count=None` (`live.max_artifact_bytes if live is not None else None`). Admin-UI live overrides for `max_artifact_bytes`/`max_artifacts_per_run` therefore never applied (boot `.env` values did, via `make_artifact_store` fallback).
- **Fix**: `_PreparedRunContext` now carries `max_artifact_bytes`/`max_artifacts_per_run` from `get_live_settings()`; the artifact store uses them. Regression tests added (`test_runner_live_artifact_caps.py`, 2 tests, incl. an end-to-end spy on `make_artifact_store`).

### BUG-4 — Event broker fire-and-forget publish tasks unreferenced (P2) — **FIXED**
- **Area**: Events · **File**: `apps/api/app/services/events.py:138`
- **Evidence**: `publish()` in Redis mode did `loop.create_task(self._async_publish(...))` and discarded the result. The event loop holds only weak refs to tasks — per asyncio docs a task whose result is discarded can be garbage-collected mid-flight, silently dropping the run event (missed node updates on the editor, missed history entries).
- **Fix**: tasks are now held in `self._publish_tasks` with a done-callback discard.

### BUG-5 — Queue dispatch head-of-line blocking starves remote pools (P1) — **FIXED**
- **Area**: Queue/worker · **File**: `apps/api/app/services/queue.py` (dispatch loop)
- **Evidence**: when the leased head entry was local (`runner_pool_id is None`) and `local_budget <= 0`, the loop did `rollback(); break` — ending the whole tick. Lease ordering is stable (priority, then oldest), so every subsequent tick re-leased the *same* local entry and broke again: docker/k8s/agent runs queued behind one saturated local pool never dispatched until local capacity freed. One tenant's local burst could stall every remote pool on an `inline` replica.
- **Fix**: `lease()` gained `exclude_local`; on local-budget exhaustion the loop now continues leasing remote-only entries for the rest of the tick. Regression tests added (2 tests in `test_run_queue.py`, incl. composition with the provider-capability filter).

### BUG-6 — Redis event history ordering not guaranteed under bursts (P2) — OPEN
- **Area**: Events · **File**: `apps/api/app/services/events.py`
- **Evidence**: each `publish()` schedules an independent `_async_publish` task; two in-flight tasks can interleave their pipelined `rpush` calls, so history order can diverge from publish order under bursts (worst case: `node_finished` recorded before its `node_chunk`s). Live subscribers are similarly at the mercy of task scheduling.
- **Recommended fix (later)**: per-topic ordered queue (single writer task per broker) or monotonic sequence numbers stamped at publish time that clients sort on.

### BUG-7 — `requeue_expired_leases` unlocked across replicas (P3) — OPEN
- **File**: `apps/api/app/services/queue.py:553`
- **Evidence**: the expired-lease SELECT takes no `FOR UPDATE SKIP LOCKED`; with multiple dispatch loops, two processes can both requeue the same expired entry — harmless for state (both set `queued`) but double-appends `attempts_log` and double-logs. Low impact; fix by adding `with_for_update(skip_locked=True)` on Postgres.

### BUG-8 — MCP client bypasses connect-time DNS-rebinding guard (P2) — OPEN
- **Files**: `apps/api/app/services/mcp_client.py`, `packages/nodes/noodle_nodes/http_security.py`
- **Evidence**: the SSRF hardening re-validates resolved IPs at TCP-connect time by patching `socket.create_connection` — which only intercepts `requests`/urllib3. `discover_tools`/`call_tool` use `httpx.AsyncClient` (asyncio `loop.create_connection`), so only the pre-flight `assert_public_http_url` DNS check applies; a rebinding attacker who flips DNS between pre-flight and connect isn't caught on the MCP path. Redirects: httpx defaults to no redirect following, which mitigates the redirect vector.
- **Recommended fix (later)**: custom httpx transport that pins the pre-validated IP (connect to the resolved address, send SNI/hostname separately) — same pattern for any other httpx egress.

### BUG-9 — Dev venv missing `signxml` (P3, environment) — OPEN
- 3 SAML tests fail with `ModuleNotFoundError: No module named 'signxml'` in this machine's `.venv`. Declared in `apps/api/pyproject.toml`; the venv is stale (and `uv sync` was blocked by the full C: drive — see Remaining Risks). Not a code bug.

### BUG-10 — `test_running_run_can_be_cancelled` order-dependent flake (P3) — OPEN
- Fails in the full suite (`'running' == 'cancelled'`), passes in isolation and in the 9-suite affected run. Timing-sensitive cancel assertion; likely aggravated by suite-level state. Deserves a deterministic wait/poll rewrite.

### Observations (not bugs)
- **Checkpoint write amplification**: `_save_checkpoint` runs an UPDATE+commit after every completed node. Bounded (1 MiB) and incremental, but a 200-node graph is 200 commits; consider debouncing (e.g. ≥250 ms coalescing) for large graphs.
- **AI builder allowlist**: `_ALLOWED_NODE_TYPES` covers ~60 of ~400 node types — the builder cannot place most of the library (no MCP tool nodes, Discord/Teams/Telegram, most data-science nodes). Product gap more than safety necessity, since validation is manifest-driven.
- **MCP `permission=None` read tools** (list/get workflow, versions, diff, environments, schedules) are anonymous when `auth_required=False` — consistent with the API's open-instance posture, but worth documenting for operators who front `/mcp` publicly.
- **God files**: `mcp/tools.py` (2,781), `builtin.py` (2,464), `NodeDetails.tsx` (5,000), `EditorPage.tsx` (2,091) — refactor when next touched, not preemptively.

---

## Fixes Made

| # | Issue | Files changed | Tests | Verification |
|---|---|---|---|---|
| 1 | BUG-1 ops auth regression | `app/routers/ops.py` | +1 (`test_ops_monitoring_requires_auth_when_auth_enabled`); un-broke 7 existing | `test_ops.py` + `test_tracing.py`: 18 passed |
| 2 | BUG-2 reload pollution | `tests/conftest.py` (fake `shutdown()`) | un-broke 2 existing | triggers+workflow_events pair: passed |
| 3 | BUG-3 live artifact caps | `app/services/runner.py` | +2 (`test_runner_live_artifact_caps.py`) | 2 passed |
| 4 | BUG-4 publish task refs | `app/services/events.py` | covered by existing broker suites | affected suites green |
| 5 | BUG-5 head-of-line blocking | `app/services/queue.py` | +2 (`test_run_queue.py::test_lease_exclude_local_*`) | `test_run_queue.py`: 29 passed |

Affected-area sweep after all fixes: `test_dispatch_role, test_drain_mode, test_queue_fairness, test_runs, test_architecture_fixes, test_ops, test_tracing, test_workflow_events, test_runner_live_artifact_caps` → **103 passed**. Ruff clean on every touched file.

---

## Backend and Architecture Review

**Strengths**: fail-closed startup validation (secret key, topology, Postgres role RLS check); global org-resolution dependency + ORM/RLS double enforcement; consistent RBAC primitives; request body caps with chunked-stream limiting; structured logging with request/org/run correlation; queue as the single orchestration source of truth; executor seam isolating persistence from execution; live-settings overlay for runtime tuning.

**Weaknesses**: routers occasionally reach into other routers' handlers (MCP tools call `publish_workflow`/`update_deployment` route functions directly — pragmatic but couples layers); several services import lazily to dodge cycles (runner↔queue), a hint the module graph wants a `runs_domain` package; `_execute_run_impl` remains ~430 lines after extraction; `Any`-typed context dicts in the executor seam would benefit from pydantic models.

**Recommendations**: (now) nothing blocking beyond the fixes made; (soon) split `mcp/tools.py` by domain (read/build/lifecycle/schedule/ops), pull router-called logic into services; (later) typed RunExecutionContext.

## Workflow Engine Review

Verified from code: structural validation before topo-sort (specific error messages), cycle detection, deterministic ordering independent of canvas position, skip semantics for untaken branches, disabled-node passthrough, `$error` port routing, retries with backoff+jitter and chunk-reset events, per-type and global concurrency semaphores that loop/metanode drivers correctly bypass (no deadlock on nested regions), run deadline that refuses to start new nodes, worst-status aggregation so a late waiting node can't mask an error, 10 MiB output cap with a cheap upper-bound fast path, frozen output maps preventing cross-node mutation, checkpoint after each success + resume from checkpoint or NodeRun reconstruction, cancellation via task cancel + cooperative `cancel_event` for sync nodes.

Gaps: non-cooperative sync nodes keep their thread after timeout (documented limitation of `to_thread`); engine `run_status` for loop bodies aggregates via drivers (tests cover it); replay-from-node exists via `replay_seed` cache/targets — solid.

## Runners, Workers, and Queue Review

Local warm pool: per-env process pools with RSS budgets, idle reaper, autoscaler, recycle thresholds. Sandbox: hardened container-per-(org,env) with readiness probes, dirty-exit no-reuse, LRU eviction, cancel kill — the Phase D tests are extensive. Remote: WS agents with heartbeat loops, lease-expiry recovery, `_QueuedError` capacity backoff mapping to queue retry, stale-runner offlining. Queue: see Bugs — with BUG-5 fixed, fairness holds across both org dimension (fewest in-flight first, quota parking) and provider dimension (local saturation no longer blocks remote). Worker restart: covered by `requeue_expired_leases` + startup `_mark_interrupted_runs` on inline (with the split-topology carve-out); `stuck_run_detector` covers engine hangs (30 min default).

Stress-testing note: I did not run live 50–100-run load tests in this session (no running Postgres/Redis stack on this machine and the dev box's C: drive was full); the fairness/lease/restart behaviors are covered by the suite (`test_queue_fairness`, `test_run_queue`, `test_drain_mode`, `test_dispatch_role`). A compose-based soak (webhook burst → 100 queued runs → worker kill/restart mid-flight) is the top remaining QA action before beta.

## MCP Review

**Server**: transport-correct (stateless JSON, protocol-version negotiation, single-message POSTs, 202 notifications), Origin allowlisting, RFC 9728 metadata + bearer challenge, PAT/OAuth-introspection principals restricted to `/mcp` (webhook credentials never forwarded to the introspector — nice touch), 120/min rate limit, cursor pagination, per-tool RBAC + automation-token scope checks, `isError` tool results with correlation ids for internal failures. Tool design: incremental graph editing (add/patch/remove node/edge) with `expected_graph_revision` optimistic concurrency and revision journaling — this is exactly what coding agents need. Dynamic workflow-as-tool with schema validation, reserved-name and collision protection.

**Client**: connection CRUD is admin-gated (`mcp_connection:manage`), secrets Fernet-encrypted under org KEK, dangerous-header blocklist, SSRF pre-flight; tool discovery → node manifests → `mcp_tool` node executes through a per-run installed hook that re-loads and re-scopes the connection by org.

**Gaps / recommendations**: BUG-8 (httpx rebinding); no per-tool allow/deny policy for which *static* tools an automation token may call beyond scopes; no MCP tool-call trace surfaced in run debug; no discovery UI to browse an external server's tools and drop them onto the canvas; workflow tool descriptions are free text (prompt-injection surface for calling agents — consider stripping/flagging suspicious instructions in `mcp_description`); no approval-gate option for dynamic workflow tools (side-effecting workflows are callable by any `workflow:run` principal).

## Node Library and Tooling Review

~400 nodes with per-node requirements + preflight, typed ports (`data_kind`), credential presets, and an `unsafe_nodes` classifier feeding approval gates. Integrations_v2 provider framework (operations + triggers per provider) is the right architecture for scale. Data-science coverage (datasets/DuckDB/stats/ML/monitoring/serving) is unusually deep.

Missing high-value nodes vs. the P0/P1 wishlist: **Discord / Teams / Telegram send** (Slack exists), **Docker run container**, **GitHub Actions trigger** (GitLab pipeline exists), **HuggingFace inference**; Parquet/Excel and BigQuery/Snowflake already exist. MCP Call Tool/Discover exist via connections; a "Dynamic MCP Tool" palette section fed by live discovery is the missing UX. Priority: Telegram/Discord/Teams (table stakes, low complexity, credential: bot tokens), HF Inference (AI-native fit), Docker Run (needs the unsafe-node gate).

## Python-Native Experience Review

Exists and works: per-env venvs (uv), package preflight with actionable errors, code nodes with typed I/O JSON schemas, process isolation, code modules (`@node`-decorated functions become nodes, incl. undecorated discovery), workflow→Python export and Python→workflow import, wheel index for runners. Missing for the "best Python workflow tool" claim: per-workflow requirements manifest; a pip-installable client SDK + CLI (`packages/client` exists but isn't packaged/documented as a product); notebook import/export; step-through debugging; DataFrame preview on wires (DataPanel shows outputs, but not inline previews); publish-a-custom-node flow.

**Build first**: (1) CLI + client SDK on PyPI (`noodle run`, `noodle export`, `noodle import script.py`), (2) per-workflow requirements + preflight, (3) DataFrame wire previews. These three convert Python developers fastest.

## AI Workflow Builder Review

`ai_builder.py` (draft) + `agentic_builder.py` (iterative) + `GenerateNodeModal`/AI panels. Safety design is right: strict JSON proposals, allowlist, credential-ref stripping, deterministic fallback. Weaknesses: allowlist breadth (see Observations); no post-generation auto-test-run loop; no failed-run→AI-repair button; no per-change approval diff (draft replace is all-or-nothing from the modal); no AI explain panel. The MCP server actually offers a *better* agent-building surface than the built-in builder — an external Claude/Cursor session using the MCP tools gets incremental edits + validation + runs. Consider making the internal builder consume the same MCP tool layer (one brain, two frontends).

## Frontend and UX Review

Verified: 76 test files / 439 tests passing; typecheck clean; store slices with undo, drill, clipboard, streaming; a11y-tested modals; onboarding tour component; error boundaries; command palette; virtualized-ish canvas via React Flow. Concerns: `NodeDetails.tsx` 5,000 lines (param rendering, expression editor, typed-IO, code panels all in one), `EditorPage.tsx` 2,091 with keyboard/run/undo logic; run-event flood handling relies on broker replay caps (10k) — a 100+ node loop-heavy run should be profiled against the DataPanel; no template gallery surfaced at create-time; credential setup is functional but presets could drive a guided flow. These are polish-tier, not launch blockers.

## Security Review

Strong: two-layer tenant isolation (verified ORM hook + RLS GUC + NOBYPASSRLS assertion + dedicated-pool execution isolation refusal), fail-closed startup, CSRF double-submit with correct Bearer/cookie discrimination, session revocation cutoffs enforced even on `?token=` paths, PAT hashing + org binding + scope enforcement (incl. inside MCP), webhook HMAC + body-size caps + rate limits + redacted captures, SSRF defense-in-depth for `requests`-based nodes (pre-flight, per-hop redirect re-validation, connect-time rebinding check, credential stripping on cross-origin redirects), egress policy env (`NOODLE_ALLOW_PRIVATE_EGRESS`), secret redaction word-lists on events/logs, KEK-per-org credential encryption, sandbox policy that refuses unsafe MT configs, CSP + security headers, structured audit log.

Found: BUG-1 (fixed), BUG-8 (httpx rebinding gap, open, P2), MCP anonymous read tools on open instances (documented posture), workflow-tool description injection surface (P3). Nothing P0. No cross-tenant leak found: MCP tools inherit the org filter via the session ContextVar set by the global `resolve_org` dependency (verified the dependency applies to `/mcp` and that dynamic tool dispatch stays inside the scoped session).

## Performance and Optimization Review

Good: bulk NodeRun/RunEvent inserts, batched org-limit fetches, metrics cache, introspection cache, live-settings cache, incremental checkpoint serialization, counting JSON sink for output caps, history replay caps, queue wakeup vs. poll. Issues found: per-node checkpoint commits (debounce — should fix soon); publish-task interleaving (BUG-6); `_org_fair_order` runs two grouped queries per lease attempt in MT mode (fine at current scale; revisit past ~50 orgs with deep queues); frontend NodeDetails re-render breadth (profile when touched). Must-fix-now: none beyond the fixed bugs. Premature: sharding the queue, Redis Streams migration, canvas virtualization beyond React Flow defaults.

---

## Product Enhancement Ideas

### AI-native
| Idea | Priority | Complexity | Value / adoption impact |
|---|---|---|---|
| Widen AI-builder node vocabulary (manifest-driven, drop the static allowlist for read-safe nodes) | P1 | M | Directly increases "prompt → working workflow" hit rate — the core promise |
| "Fix failed run" button (feed run error + graph to LLM → patch proposal + diff) | P1 | M | Differentiating; MCP tools already expose everything needed |
| AI explain-workflow panel | P2 | S | Onboarding + trust; cheap win |
| AI-generated test cases (pin inputs → assert outputs; store as regression pins) | P2 | M | Differentiating vs n8n/Windmill |
| Internal builder rebased on the MCP tool layer | P2 | M | One agent surface; halves maintenance |

### Python-native
| Idea | Priority | Complexity | Value |
|---|---|---|---|
| PyPI `noodle` CLI + client SDK | P1 | M | Table stakes for the positioning; enables CI usage |
| Per-workflow requirements + preflight | P1 | S | Removes the #1 Python-env footgun |
| DataFrame preview on wires | P2 | M | Demo-defining for data folks |
| Notebook import/export | P3 | M | Differentiating, niche |
| Custom node publish flow (module → shared palette entry) | P2 | M | Community flywheel |

### MCP
| Idea | Priority | Complexity | Value |
|---|---|---|---|
| MCP discovery UI (browse external server tools → drag as nodes) | P1 | M | Turns the MCP client from plumbing into a headline feature |
| MCP tool-call trace in run debug | P1 | S | Trust + debuggability for agent runs |
| Approval gates + side-effect metadata for dynamic workflow tools | P2 | S | Enterprise safety story |
| MCP server allowlist policy (org-level) | P2 | S | Hosted-mode requirement |
| "Let Claude/Cursor build your workflow" docs + one-click MCP config | **P0 (marketing)** | S | The moat is built; nobody knows |

### Workflow UX
Template gallery at create-time (P1/S); run comparison view (P2/M); replay-from-node button surfacing the existing `replay_seed` capability (P1/S — backend done!); workflow health score/production checklist (P3); guided onboarding expansion (P2).

### Enterprise
Usage analytics dashboard per org (P2); credential sharing policies (P2); SCIM (P3); GitOps sync is already ahead of competitors — document it (P1/S).

---

## Prioritized Roadmap

### P0: Fix before any more users
1. ~~Ops auth regression~~ ✅ fixed
2. ~~Queue head-of-line blocking~~ ✅ fixed
3. ~~Event publish task GC risk~~ ✅ fixed
4. Compose-based soak test: webhook burst → 100 queued runs, worker kill mid-run, cancel storm (few hours of QA; the machinery all exists)
5. Make CI green the gate for `main` (the branch tip shipped with 9 broken tests — CI on PRs would have caught 81c0e690)

### P1: Required for strong beta
Event ordering guarantee (BUG-6) · httpx SSRF connect-time guard (BUG-8) · checkpoint debounce · AI-builder vocabulary expansion · replay-from-node button · MCP tool trace · template gallery · CLI/SDK on PyPI · per-workflow requirements · Telegram/Discord/Teams nodes · deflake `test_running_run_can_be_cancelled`.

### P2: Differentiators
MCP discovery UI · fix-failed-run AI loop · AI test generation · DataFrame wire previews · custom node publishing · run comparison · approval gates for dynamic MCP tools.

### P3: Enterprise/future
SCIM · usage analytics · MCP server marketplace · notebook round-trip · workflow health score.

---

## Tests Run

| Command | Result |
|---|---|
| `pytest apps/api/tests packages/{core,nodes,runtime}/tests` (pre-fix, full) | **13 failed, 2,828 passed, 94 skipped** (5m07s) |
| Same, after fixes — affected 9 suites | **103 passed** |
| Same, full suite re-run (post-fix) | **3 failed, 2,843 passed, 94 skipped** (7m12s) — all 3 failures are the `signxml` env issue (BUG-9); the cancel flake (BUG-10) passed this run |
| `vitest run` (apps/web) | **439 passed, 1 skipped** (76 files) |
| `tsc --noEmit` (apps/web) | clean |
| `ruff check` (all touched files) | clean |
| `uv run pytest` | blocked by machine issue: C: drive 100% full broke uv cache; ran via `.venv` python directly |

## Tests Added
- `test_ops.py::test_ops_monitoring_requires_auth_when_auth_enabled` — locks the auth-on 401 contract for the four ops GETs.
- `test_runner_live_artifact_caps.py` (2 tests) — live-settings artifact caps reach `_prepare_run_context` and the in-process artifact store.
- `test_run_queue.py::test_lease_exclude_local_skips_local_head_of_queue` and `::test_lease_exclude_local_composes_with_provider_filter` — remote entries lease past a saturated local head.

## Files Changed
- `apps/api/app/routers/ops.py` — require_role dep on 4 monitoring GETs (+ import fix)
- `apps/api/app/services/queue.py` — `lease(exclude_local=)` + dispatch-loop continue-on-saturation
- `apps/api/app/services/events.py` — strong refs for publish tasks
- `apps/api/app/services/runner.py` — live artifact caps via `_PreparedRunContext`; removed dead `live` local (+ pre-existing import-order lint fix)
- `apps/api/tests/conftest.py` — fake isolator `shutdown()`
- `apps/api/tests/test_ops.py`, `apps/api/tests/test_run_queue.py`, `apps/api/tests/test_runner_live_artifact_caps.py` (new)
- `FABLE5_NOODLE_FULL_APP_EXPLORATION_REPORT.md` (new), this report (new)

## Remaining Risks
- **Honest suite status (post-fix full run)**: 2,843 passed / 94 skipped / 3 failed — the 3 failures are all `signxml` missing from this dev venv (env, not code). The cancel test (BUG-10) passed this run but has flaked before; treat as order-sensitive until rewritten.
- BUG-6/7/8 open (event ordering, lease-requeue locking, httpx rebinding) — none P0, all documented above.
- No live multi-process load soak was run in this session; queue semantics are unit/integration-tested but not load-proven on this machine.
- **Dev machine**: C: drive was 100% full during this audit (broke uv and even shell output); I freed ~1.6 GB by cleaning the uv cache, but the box needs real cleanup — this will keep corrupting tool runs otherwise.
- Repo hygiene: ~30 stray screenshots/logs at repo root and duplicated historical audit reports; suggest a `docs/audits/` sweep and `.gitignore` additions.

## Final Recommendation

**Status**: *Beta-ready for single-tenant/self-hosted* with this audit's fixes. Multi-tenant hosted is architecturally ready (isolation layers are real and tested) but should follow a staged rollout gated on the compose soak test and the P1 security items (BUG-8, event ordering).

**Build next**: the P1 list — above all, AI-builder vocabulary + fix-failed-run loop, MCP discovery UI + trace, and the PyPI CLI. **Don't over-engineer yet**: queue sharding, canvas virtualization, marketplace infrastructure, more enterprise RBAC surface.

**Strongest product direction**: the intersection nobody else occupies — *AI agents building inspectable, Python-native, self-hostable workflows*. n8n has breadth but weak Python and no real MCP server; Windmill has Python but no visual-AI story; LangGraph/Flowise have agents but no ops backbone. Noodle already has the ops backbone and the MCP surface.

**Strongest marketing angle**: "Point Claude (or any MCP agent) at your Noodle server and watch it build a workflow you can see, edit, test, and deploy." It's demonstrably true today — 45 MCP tools, live canvas updates, optimistic concurrency and audit trail included. Lead with a 90-second screen capture of exactly that.
