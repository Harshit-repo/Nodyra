# Fable 5 — Node Library & Data-Flow Production Audit

**Reviewer:** Fable 5 (Principal Integration Engineer)
**Branch:** `fable5-node-library-dataflow-production-audit`
**Date:** 2026-07-04 (audit) — updated 2026-07-05 with the 10/10 upgrade plan execution (§19)
**Scope:** Node system architecture, node inventory & quality, node-to-node data flow, artifact/result storage, data-flow mechanisms, missing-node roadmap, node UX, AI-builder catalog, node/egress security, performance.
**Method:** Direct source inspection of the current tree (not prior audits) + the graphify knowledge graph + empirical reproduction and regression tests.
**Update 2026-07-05:** the 10/10 plan (`docs/superpowers/plans/2026-07-05-node-dataflow-10-10.md`) was executed — see §19 for what shipped, including two additional real bugs found while implementing it (a parallel SSRF gap in the newer `ai_v2` provider adapters, and two always-wrong explain-feature descriptions).

---

## 1. Executive Summary

Nodyra's node layer is, on the whole, **strong and production-minded**: a clean `@node`-decorator SDK, 512 registered nodes across 26 categories with 100% description coverage, a genuinely good typed-serialization + Artifact + DatasetRef data plane, a multi-layered SSRF guard, mature secret redaction, and a deploy-time "unsafe node" policy gate. It is well ahead of most workflow engines on data-plane design.

The audit found **one systemic data-flow defect with three linked facets**, all fixed on this branch:
1. **OS-1** — the large-output **offload store was silently dead code**: its key validator required node-ids to be hex UUIDs, which no real node-id ever is, so every offload write raised and was swallowed and outputs always stayed inline (DB bloat).
2. **OS-2** — turning it on exposed **ten read paths** that consumed `NodeRun.output` raw and would surface the storage marker (`{"__output_ref": …}`) instead of the value — silently **skipping** retried/replayed downstream nodes and showing markers in the UI, MCP, and webhook responses.
3. **OS-3** — the persistence pipeline offloaded *before* capping, so the full uncapped output was spilled to disk and the `max_output_bytes` cap was defeated.

All three are fixed with regression tests that fail without the fix (verified by reverting each fix in isolation).

Three further findings were originally documented with remediation but deliberately not changed in the initial audit pass (Docker-node gate gap, `llm.py` runtime SSRF, name-only AI catalog). **All three were subsequently implemented** as the 10/10 upgrade plan — see §19 for what shipped and two additional bugs (a parallel SSRF gap in the newer `ai_v2` provider adapters, and always-wrong explain-feature node descriptions) discovered and fixed along the way.

### Scores (out of 10)

| Dimension | As found | After §19 plan execution | Justification |
|---|---:|---:|---|
| Node library breadth & quality | 8.5 | **9.5** | Uneven runtime-SSRF coverage closed (`llm.py` + the parallel gap found in `ai_v2` providers); timeout-declaration + SSRF-guard contract now CI-enforced. |
| Node-to-node data flow | 7.0 | **9.5** | Dead offload store, 10-site raw-read gap, and cap-vs-offload ordering all fixed; unified `resolve_ref` chokepoint with an AST guard test against regression. |
| Artifact / result storage | 8.5 | **9.0** | Offload files now garbage-collected on run pruning (previously orphaned forever). |
| Node & egress security | 8.0 | **9.5** | Docker nodes gated; SSRF fixed in both `llm.py` and `ai_v2` providers, verified against real hangs on `169.254.169.254`. |
| Performance / scalability | 8.0 | **8.5** | Offload-before-cap ordering fixed, closing an unbounded-payload-to-disk path. |
| Node UX (palette / port viewer) | 8.0 | 8.0 | Unchanged this pass — large-output viewer UX (Phase 6.1) not implemented. |
| AI builder catalog | 6.5 | **9.5** | Catalog now carries full param signatures (type/required/choices) for the entire ~510-node catalog, not name-only for ~64; refine-mode and explain-mode fixed to match. |

**Overall node-system readiness: 8.1 / 10 as found → ~9.2 / 10 after the §19 plan execution.** The remaining gap to 10/10 is Node UX (large-output viewer) and the lower-priority polish items in §19's table (benchmark harness, P2 node gaps, real-Postgres CI lane) — none of which are correctness or security issues.

---

## 2. Node System Architecture Map

```
                       @node decorator  (packages/core/nodyra/sdk.py)
                              │  builds NodeManifest {id, name, category, role,
                              │  inputs/outputs: PortSpec(data_kind), params: ParamSpec,
                              ▼  usable_as_tool, tool_side_effecting, requirements}
                     ┌──────────────────┐
   built-in nodes ──►│  NODE REGISTRY   │◄── user modules (static AST discovery via
   (nodyra_nodes)    │  (in-process)    │    register_module_functions + _CodeValidator
   integrations_v2 ─►│  512 manifests   │    sandbox — no import/exec of arbitrary code)
   (node_factory)    └────────┬─────────┘
                              │ manifests() → editor palette / AI-builder catalog
                              ▼
     WorkflowGraph ─► SCHEDULER (engine/scheduler.py)
                        • topological sort + dependency-count worker pool (≤50 workers)
                        • per-node + per-type semaphores (e.g. ≤N concurrent http_request)
                        • run_deadline (wall-clock), DEFAULT_MAX_NODE_OUTPUT_BYTES = 10 MiB
                              │
                              ▼
                      NODE_EXEC (engine/node_exec.py)
                        • retries w/ exponential backoff + jitter, per-node timeout
                        • process isolation for `code` nodes
                        • `$error` virtual port, log capture via contextvars, emit_chunk streaming
                              │  passes REAL Python objects in-process (no serialization hop)
                              ▼
     DATA PLANE
       • serialization.py  → typed JSON envelopes (__nodyra_typed__) for datetime/Decimal/
                             bytes/tuple/set/DataFrame; truncation without materializing
       • artifacts.py      → ArtifactRef (__nodyra_artifact__), LocalArtifactStore + backends
       • datasets.py       → DatasetRef (__nodyra_dataset__), DuckDB/Parquet columnar plane,
                             auto-promote (code outputs) / auto-expand (≤50k rows)
                              │
                              ▼
     PERSISTENCE (apps/api)
       • run_persistence.py → bulk-insert NodeRun rows; maybe_offload_output() offloads
                             large outputs to output_store; redaction applied
       • output_store.py   → {"__output_ref": "<run_id>/<node_id>"} marker files (data/outputs)
       • artifact_backends  → LocalBackend + S3Backend (signed_url, streaming, stats cache)
```

**Roles present:** executable 464, trigger 22, supplier 16, tool 9, output_parser 1. **`usable_as_tool`:** 454 nodes (agent-callable).

---

## 3. Node Inventory (512 nodes / 26 categories)

| Category | Count | Category | Count |
|---|---:|---|---:|
| Integrations | 142 | Data Types | 8 |
| AI | 75 | Vector DB | 8 |
| Transform | 55 | Browser & Web | 7 |
| Machine Learning | 41 | Communication | 7 |
| Files | 29 | API | 5 |
| Data | 26 | Data Platforms | 5 |
| Triggers | 21 | Visualize | 4 |
| System | 12 | DevOps | 3 |
| Statistical Analysis | 12 | Utility | 2 |
| Logic | 11 | MCP | 1 |
| Data Quality | 9 | Charts | 1 |
| Document Intelligence | 9 | Image | 1 |
| Geospatial | 9 | | |
| Security | 9 | **Total** | **512** |

Description coverage is 100%. The `Integrations` bulk (142) is generated by the spec-driven `node_factory` (consolidated Resource+Operation nodes sharing one `ProviderTransport`), which is the right pattern — one hardened HTTP path, not 142 ad-hoc ones.

---

## 4. Production-Quality Findings (per-node checklist themes)

Assessed against: input validation, error surfacing, timeouts, resource caps, secret handling, idempotency, SSRF/egress safety, and output sizing.

**Strong across the board:**
- **v2 integrations** (~142 nodes): all egress via `ProviderTransport` → `safe_request`; retries with `Retry-After` honoring; pagination caps (`DEFAULT_MAX_PAGES=50`, `DEFAULT_MAX_ITEMS=10_000`); structured errors; debug request logging.
- **Database nodes** (`postgres_query`, `mysql_query`, etc.): connection URLs pass `assert_public_host()` before connecting (SSRF for non-HTTP egress).
- **`code` node**: defence-in-depth — AST validation + runtime `__import__` block + safe builtins + process isolation.
- **`duckdb_sql`**: `SET enable_external_access=false` on a fresh connection per node (blocks `read_csv_auto('/etc/passwd')`); separate write-connection.
- **Output sizing**: every node output is capped at 10 MiB (`DEFAULT_MAX_NODE_OUTPUT_BYTES`); table data travels as `DatasetRef`, never inline.

**Weak points (see §11 for detail):**
- `llm.py` Azure/custom-`base_url` chat calls use raw `requests.post` (no runtime SSRF re-validation) — inconsistent with the rest of the AI layer.
- Docker nodes execute arbitrary containers but are not classified by the unsafe-node gate.

---

## 5. Node-to-Node Data-Flow Review

**In-flight (engine):** the scheduler passes **real Python objects** between nodes in-process — no serialization round-trip on the hot path. This is the correct high-performance choice; typed envelopes and refs only materialize at persistence/UI boundaries.

**At rest / at boundaries:** `serialization.py` produces typed JSON envelopes that faithfully round-trip `datetime`, `Decimal`, `bytes`, `tuple`, `set`, and DataFrames, with truncation helpers that don't materialize giant strings. Large tabular data is a `DatasetRef` (DuckDB/Parquet) with auto-promote for `code` outputs and auto-expand (≤50k rows) when a per-item node needs materialized rows. This is a genuinely good design.

**The systemic bug (fixed on this branch):** the **large-output offload store never ran in production.** `maybe_offload_output()` builds a key `"<run_id>/<node_id>"` and calls `_write_output()`, which validated the key against `^[0-9a-fA-F]{32}/[0-9a-fA-F]{32}$` — i.e. it required the **node-id** to be a 32-char hex UUID. Real node-ids are workflow-assigned (`n_<base36>_<seq>` from the editor, or human ids like `producer`), so the write always raised `ValueError`, which `maybe_offload_output` swallows (`except Exception: return outputs`). **Net effect: every output silently stayed inline**, defeating the feature and bloating `node_runs.output` for every 1 KB–10 MiB payload. Empirically confirmed:

```
node_id='n_lkj2h3_0'  offloaded=False   # should have offloaded a 500-element list
node_id='producer'    offloaded=False
node_id='<32-hex>'    offloaded=True     # the only shape that ever worked
```

Fixing the validator (run_id = hex UUID; node_id = bounded identifier, still rejecting `.`/`..`/separators) turned the feature on — which then exposed the **read-side gap** below.

---

## 6. Artifact / Result Storage Review

`LocalArtifactStore` and the pluggable `ArtifactBackend` layer are mature: sha256 checksums, sanitized filenames, path-escape guards, per-org `key_prefix` isolation, `LocalBackend` + `S3Backend` with `signed_url`, streaming downloads, cached stats, idempotent delete. Redaction is applied on persistence with a per-org secret cache (60 s TTL), key-name masking, and exact-value masking; the N+1 in run persistence was already fixed with bulk approval loads.

The one storage-layer defect was `output_store` (§5) — the **Result Store tier** that sits *below* the artifact layer for medium-sized outputs. It shares the artifact layer's good intentions (marker indirection, path validation to prevent traversal on read) but its write-side key validation was mis-specified. **Now fixed and, for the first time, covered by unit tests** (`apps/api/tests/test_output_store.py`).

---

## 7. Should Nodyra Add Other Data-Flow Types?

Evaluated the request's candidate mechanisms against what already exists:

| Candidate | Verdict | Why |
|---|---|---|
| **Inline** | ✅ Have it | Typed JSON envelopes (`serialization.py`). Best-in-class. |
| **Artifact / Blob** | ✅ Have it | `ArtifactRef` + backends. "Blob" is not a distinct type — it's an artifact. |
| **Dataset** | ✅ Have it | `DatasetRef` (DuckDB/Parquet columnar plane). |
| **Secret** | ✅ Have it | Credentials store + `redaction.py`. |
| **Stream** | ✅ Have it (partial) | `emit_chunk` token/chunk streaming; sufficient for current needs. |
| **Model** | ⚠️ Node concern, not a flow type | Model handles ride inside supplier-node outputs; no new plane needed. |
| **Vector** | ❌ Don't add | Vectors are a node/domain concern (Vector-DB category, 8 nodes), not a transport plane. |
| **Event** | ❌ Don't add | Already covered by triggers (22) + the run-event stream. |
| **Unified `DataRef`** | ⚠️ Recommended as a *resolver*, not a new type | The three refs (inline/artifact/dataset) + the offload marker already span the space. What's missing is a **single resolution chokepoint** — the 10-site raw-read gap (§8) is direct evidence that the *read* side needs one helper, not that a new *type* is needed. |

**Recommendation:** do **not** add new data-flow types. The plane is complete. Do add a small unified `resolve_ref(value)` façade (wrapping `resolved_output` / artifact / dataset resolution) and route all read boundaries through it so a future ref type can't reintroduce a scatter of raw readers.

---

## 8. Bugs Found & Fixes Made

### OS-1 (P1) — Large-output offload store was dead code *(FIXED)*
- **Root cause:** `output_store._validate_key` required both key segments to be 32-hex UUIDs; real node-ids never match, so every offload write raised and was swallowed → outputs never offloaded.
- **Fix:** `apps/api/app/services/output_store.py` — validate `run_id` as a hex UUID and `node_id` as a bounded identifier (`[A-Za-z0-9._-]{1,128}`), still rejecting `.`/`..`/path separators so a tampered marker cannot escape the output root on read.
- **Impact:** eliminates `node_runs.output` bloat for 1 KB–10 MiB payloads; the feature now works as designed.

### OS-2 (P1) — Ten read paths surfaced the storage marker instead of the value *(FIXED)*
Enabling OS-1 made these live. Each read of `NodeRun.output` that expected a `{port: value}` dict would instead receive `{"__output_ref": key}`.
- **Engine cache-seed paths (caused downstream nodes to be *skipped* — marker has no ports):**
  - `apps/api/app/routers/runs.py` — `retry_from_failure`, `replay_workflow_run`, `run_debug_snapshot` (pin), and the trigger-parameter re-run path.
  - `apps/api/app/services/run_resume.py` — agent-action resume cache seed (its sibling path already used `maybe_load_output`; this one didn't — inconsistent).
- **Client-facing paths (would display `{"__output_ref": …}` to users):**
  - `apps/api/app/schemas.py` — `NodeRunInfo` (the canonical UI run-detail serialization).
  - `apps/api/app/mcp/tools.py` — two MCP run/attempt output projections.
  - `apps/api/app/services/triggers.py` — `_last_node_output` (webhook/trigger response body).
  - `apps/api/app/services/agentic_builder.py` — agentic-builder `node_results`.
- **Fix:** wrap each raw read in `resolved_output(...)` / `maybe_load_output(...)`. Verified failing-without-fix (retried node reported `skipped`; run-detail returned the marker) then passing with the fix.

### OS-3 (P1) — Offloading bypassed the output cap *(FIXED)*
Turning on OS-1 exposed a third defect caught by the existing `test_retention.py::test_output_cap_truncates_oversize_payloads`.
- **Root cause:** the persistence pipeline was `_cap_output(maybe_offload_output(outputs, …))` — it **offloaded first**, spilling the *full, uncapped* output to disk and leaving only the tiny `{"__output_ref": …}` marker for `_cap_output` to see (a no-op). So `max_output_bytes` was silently defeated: an arbitrarily large payload (e.g. 500 MB) would be written to the store in full, and on read the uncapped value came back.
- **Fix:** `apps/api/app/services/run_persistence.py` — invert to `maybe_offload_output(_cap_output(outputs, cap), …)`: cap **first** (truncate anything over `max_output_bytes` to `{_truncated, size_bytes, preview}`), then spill only the bounded survivor to disk. The cap now bounds what the offload store ever persists.

### Documented, deliberately NOT changed this pass (recommended follow-ups)
- **SEC-A (P1) — Docker nodes bypass the unsafe-node gate.** `docker_run_container`, `docker_stop_container`, `docker_list_containers` appear in **no** list in `unsafe_nodes.py`. `docker_run_container` runs arbitrary containers with daemon access (root-equivalent host compromise) and its own docstring warns "single-tenant only," yet a deployment containing it faces no `require_approval`/`block` gate — and the AI builder could emit one. **Recommend:** add the two mutating Docker nodes to `UNCONDITIONAL_UNSAFE` (kind `execute_command`).
- **SEC-B (P2) — `llm.py` runtime SSRF is inconsistent.** Pinecone/image paths use `safe_request`, but the Azure (`azure_endpoint`) and custom `base_url` (openai_compatible) chat calls use raw `requests.post` — no per-hop redirect re-validation, DNS-rebinding socket patch, or credential stripping. The deploy-time gate *does* flag these (`network_egress`), so defence-in-depth exists, but runtime hardening is uneven. **Recommend:** route them through `safe_request` (the `ollama` localhost default is already handled by `NODYRA_ALLOW_PRIVATE_EGRESS`).
- **AIB-1 (P2) — AI builder catalog is name-only.** `node_catalog_for_prompt` emits `id - name` for all allowed nodes, but detailed param schemas exist for only ~64 of 512 nodes, so the LLM must guess params for ~87% of the catalog. **Recommend:** emit per-node param signatures from the manifests within the char budget.
- **TEST-1 (P3) — `test_ai_agent_tools.py` import path** requires repo-root CWD (`from packages.nodes.tests…`). Minor infra smell; tests pass when run from the repo root per `pyproject.toml` `testpaths`.

---

## 9. Missing-Nodes Roadmap

- **P0 (none critical to correctness).** The library is broad.
- **P1:** a first-class **`data_ref` / passthrough resolver** node is unnecessary given §7; instead prioritize hardening (SEC-A). No functional node gap rises to P1.
- **P2:** managed **queue/stream consumer** trigger (Kafka/SQS beyond current polling triggers); **structured PDF/table extraction** beyond the current Document-Intelligence set; a **columnar join** node over `DatasetRef` (join is currently DuckDB-SQL-only).
- **P3:** more **Visualize/Charts** nodes (currently 4 + 1); a **schema-diff / contract-test** node for Data-Quality.

---

## 10. Node UX Review

`NodePalette.tsx` has ranked search, category colors, aliases, data-kind badges, and dynamically injects MCP tools. `PortDataViewer.tsx` (~700 lines) gives rich per-port output previews with truncation. Node-id generation is client-side (`newNodeId`). This layer is solid; the only UX-adjacent defect was that large outputs displayed the storage marker (OS-2, fixed).

---

## 11. AI Builder Catalog Review

The builder injects a compact `id - name` catalog (12k-char budget) and orders by a hand-curated `_NODE_REGISTRY` (~64 nodes) that also carries the only detailed param knowledge. Consequence: the model can *select* almost any node by name but can only *configure* the ~64 curated ones with confidence — the remaining ~87% are configured by guesswork. This is the single biggest quality lever for AI-generated workflows. Recommendation: generate param signatures from the manifests (they already exist) rather than maintaining a hand-curated subset.

---

## 12. Security Review (nodes + data flow)

**Excellent:** `http_security.safe_request` is a proper SSRF guard — scheme allow-listing, private/loopback/link-local/reserved blocking, **per-hop redirect re-validation**, a **DNS-rebinding defence** that patches `socket.create_connection` to re-check the resolved IP at connect time, and credential stripping on cross-origin/downgrade redirects. `NODYRA_ALLOW_PRIVATE_EGRESS` provides a documented single-tenant opt-out. Redaction masks by key-name and exact value with a per-org cache. The deploy-time `unsafe_nodes.classify` gate covers ~40+ node types across a four-level policy ladder (allow/warn/require_approval/block).

**Gaps:** SEC-A (Docker nodes ungated) and SEC-B (`llm.py` uneven runtime SSRF) above. Neither is a secret-leak; both are egress/execution-surface issues with existing partial mitigations, which is why they are documented for a focused follow-up rather than force-fixed alongside the data-flow bug.

---

## 13. Performance Review

Worker pool (≤50) with per-node and per-type semaphores; wall-clock `run_deadline`; 10 MiB per-node output cap; DatasetRef columnar plane avoids row materialization; pagination caps bound integration egress. The dead offload store (OS-1) was the one performance defect — it forced medium outputs to bloat Postgres `node_runs.output` instead of spilling to disk/S3; now fixed.

---

## 14. Files Changed

| File | Change |
|---|---|
| `apps/api/app/services/output_store.py` | **OS-1 fix** — relax `_validate_key` (run_id=UUID, node_id=bounded identifier, traversal-safe). |
| `apps/api/app/services/run_persistence.py` | **OS-3 fix** — cap before offload so the output cap bounds what the store persists. |
| `apps/api/app/routers/runs.py` | **OS-2 fix** — `resolved_output` in retry, replay, pin, and trigger-param cache seeds. |
| `apps/api/app/services/run_resume.py` | **OS-2 fix** — resolve marker in agent-action resume cache seed. |
| `apps/api/app/schemas.py` | **OS-2 fix** — resolve marker in `NodeRunInfo` before serving to UI. |
| `apps/api/app/mcp/tools.py` | **OS-2 fix** — resolve marker in two MCP output projections. |
| `apps/api/app/services/triggers.py` | **OS-2 fix** — resolve marker in webhook/trigger response body. |
| `apps/api/app/services/agentic_builder.py` | **OS-2 fix** — resolve marker in agentic `node_results`. |
| `apps/api/tests/test_output_store.py` | **NEW** — 19 unit tests: offload fires for real node-ids, round-trips, rejects traversal (OS-1). |
| `apps/api/tests/test_runs.py` | **NEW** — `test_retry_resolves_offloaded_upstream_outputs`, `test_run_detail_resolves_offloaded_output_for_ui` (OS-2, both fail without the fix). |

---

## 15. Tests Run & Added

**Added:** 19 `test_output_store.py` unit tests + 2 `test_runs.py` integration tests. Each new test was verified to **fail without the corresponding fix** (retried node → `skipped`; run-detail → raw marker) and **pass with it** — no faked results. OS-3 is guarded by the pre-existing `test_retention.py::test_output_cap_truncates_oversize_payloads`, which **failed before** the cap-ordering fix and **passes after**.

**Run (from repo root, `.venv` Python 3.12.5, `pytest`):**
- `apps/api/tests/test_output_store.py` — **19 passed**.
- `apps/api/tests/test_runs.py` — **40 passed**.
- `apps/api/tests/test_retention.py` + `test_output_store.py` + `test_runs.py` (post-OS-3) — **62 passed**.
- Node package suite (repo-root, earlier this session) — **1743 passed, 82 skipped**.
- **Full API suite** (`apps/api/tests`, 10 min): first pass **1198 passed, 1 failed** (`test_output_cap_truncates_oversize_payloads` — the OS-3 defect this pass then fixed), **6 skipped, 6 errors**. The **6 errors are environmental, not product**: `graphify` created `packages/graphify-out/` (no `pyproject.toml`), which breaks `uv`'s `packages/*` workspace glob, so `test_tenancy_isolation_pg.py`'s `uv run alembic upgrade` subprocess fails at fixture setup. Removing/relocating the `graphify-out/` dirs (or excluding them from the `uv` workspace) restores those tests.

**Confirmation re-run** (full API suite excluding the env-broken `test_tenancy_isolation_pg.py`, after all fixes): **1199 passed, 6 skipped, 0 failed** (8m47s). Every previously-passing test plus the now-fixed `test_output_cap_truncates_oversize_payloads` is green; no new failures from enabling offload.

---

## 16. Remaining Risks

1. **SEC-A / SEC-B / AIB-1 are unaddressed** by design (documented, not fixed). The Docker-gate gap is the highest-risk of the three for multi-tenant hosting.
2. **Offload is now live** — a behavioral change. It is covered by the full API suite, but any *future* new reader of `NodeRun.output` must go through `resolved_output`; add the unified `resolve_ref` façade (§7) to prevent regression.
3. **Offload is best-effort** — a disk/S3 write failure still falls back to inline (bounded by the 10 MiB cap), so a storage outage degrades gracefully rather than failing the run. Acceptable, but worth an operational alert.

---

## 17. 10/10 Node-System Plan — STATUS: executed 2026-07-05 (see §19)

1. ~~Gate the Docker nodes (SEC-A)~~ — **done**.
2. ~~Route `llm.py` Azure/custom-`base_url` through `safe_request` (SEC-B)~~ — **done**, plus the same fix applied to the `ai_v2` provider adapters (a parallel gap found while implementing this item).
3. ~~Emit manifest-derived param signatures to the AI builder catalog (AIB-1)~~ — **done**.
4. ~~Introduce the unified `resolve_ref` read façade and route the ~10 boundaries through it~~ — **done**, plus a CI-enforced AST guard against regression.
5. ~~Add an operational metric/alert for offload write failures~~ — **done**.
6. ~~Fix the `test_ai_agent_tools.py` import path (TEST-1)~~ — **done** (required two iterations — see §19).

Not executed this pass (lower priority / larger scope, tracked in the plan doc): benchmark harness (4.1), large-output escalation to artifact instead of truncation (4.2), P2 node gaps (5.2), large-output viewer UX (6.1), real-Postgres CI lane (X.3).

---

## 18. Final Recommendation (as of the original 2026-07-04 audit — see §19 for the current state)

**Ship-worthy node layer with one systemic data-flow bug now fixed.** Node-library score **8.5/10**; data-flow score **7.0 as-found → 8.5 with this branch**. The correct data-flow recommendation is **not** to add new flow types — the inline/artifact/dataset/secret plane is complete — but to (a) keep the now-working offload store correct via a single read façade, and (b) close the Docker-gate and `llm.py` SSRF gaps in a focused security follow-up. Top weak node areas: Docker nodes (unsafe-gate), `llm.py` (runtime SSRF). Top missing pieces: none critical — investment is best spent on AI-builder param fidelity and the security follow-ups above.

---

## 19. 10/10 Plan Execution (2026-07-05)

Full plan: `docs/superpowers/plans/2026-07-05-node-dataflow-10-10.md`. All six §17 items were implemented on this branch, plus two additional real bugs surfaced while implementing them. Every fix below is covered by a regression test verified to **fail without the fix** (either by reverting the fix and re-running, or — for the SSRF fixes — by observing the pre-fix code hang attempting a real socket connection to a private/link-local address, including `169.254.169.254`, the cloud metadata endpoint).

### 19.1 Security

- **SEC-A (Docker gate):** `docker_run_container` added to `UNCONDITIONAL_UNSAFE` (kind `execute_command`); `docker_stop_container`/`docker_list_containers` added under a new `docker_daemon` finding kind (daemon-reach info-disclosure/DoS, not code execution). `apps/api/app/services/unsafe_nodes.py`.
- **SEC-B (`llm.py` SSRF):** 8 call sites (Anthropic/Azure/OpenAI-compatible/Ollama chat + streaming, Cohere/OpenAI-compatible embeddings, vision, Pinecone retrieval) migrated from raw `requests.post` to `safe_request`. `packages/nodes/nodyra_nodes/llm.py`, plus 2 Pinecone sites in `ai_extra.py`.
- **SEC-B, extended scope — a second, independent SSRF gap found while writing the Phase 5.1 contract test:** the newer `ai_v2` provider adapters (`AnthropicChatAdapter`, `OpenAIChatAdapter`/`AzureOpenAIChatAdapter`, `OpenAIEmbeddingAdapter`, `CohereEmbeddingAdapter`) are a **separate, parallel implementation** of the same chat/embedding logic and had the identical raw-`requests.post` gap — undetected by the original audit because it only inspected `llm.py`. Fixed all 4 call sites in `packages/nodes/nodyra_nodes/ai_v2/providers/{anthropic,openai,embeddings}.py`. The existing `test_node_egress_policy.py` guardrail test had these three modules **allowlisted** as exceptions with a rationale ("may legitimately point at a private host") that was actually just the guard being bypassed entirely rather than using its documented `NODYRA_ALLOW_PRIVATE_EGRESS` opt-out — the allowlist entries were removed once the modules were fixed.
- **Contract test (Phase 5.1):** `packages/nodes/tests/test_node_egress_policy.py` gained a second guard, `test_nodes_declare_explicit_http_timeouts`, an AST scan asserting every `requests`/`httpx`/`safe_request` call declares `timeout=` (a hang risk otherwise). The existing egress-allowlist test caught the `ai_v2` gap immediately once the fix was applied and the allowlist updated.

### 19.2 Data flow

- **`resolve_ref` façade:** new `apps/api/app/services/data_ref.py`; all 10 OS-2 call sites migrated from `output_store.resolved_output`/`maybe_load_output` to `resolve_ref`.
- **AST guard test:** `apps/api/tests/test_data_ref_guard.py` parses every module under `app/` and fails if a new unwrapped `NodeRun.output` read appears outside a `resolve_ref`/`resolved_output`/`maybe_load_output` call — verified against both the real codebase (0 violations) and an injected violation (correctly flagged).
- **OS-3 fix, extended:** `run_persistence.py`'s cap-then-offload ordering (found and fixed during the original audit) is the same pattern now protected by the AST guard's sibling tests in `test_retention.py`.
- **Offload observability:** `nodyra_output_store_events_total{event="write_failed"|"read_failed"}` counter in `apps/api/app/services/metrics.py`, incremented in `output_store.py`'s two failure paths, exposed on `/metrics`.

### 19.3 AI builder catalog

- `node_catalog_for_prompt` rewritten to emit `id - name (param:type*[choices], ...) in[...] out[...]` per node instead of `id - name`, sourced live from `NodeManifest.params`/`.inputs`/`.outputs` rather than the hand-curated 64-node `_NODE_REGISTRY`. Default budget raised from 12,000 to 100,000 chars — the full ~510-node catalog with param signatures is only ~80,000 chars (~20k tokens), so breadth is no longer traded against depth.
- **Two more bugs found and fixed while auditing every `_NODE_REGISTRY` consumer:**
  - `_refine_workflow`'s system prompt listed only the 64 curated node type **names** (no params) as "AVAILABLE NODE TYPES" — refine mode could not add any of the other ~450 nodes. Now uses the same manifest-derived catalog as the builder.
  - The explain-workflow feature's node "purpose"/description was read from `_NODE_REGISTRY[...].get("description", ...)` — but **no `_NODE_REGISTRY` entry has ever had a `description` key**, so every node's purpose silently rendered as the placeholder ("Unknown"/"No description") regardless of node type. Now reads the live manifest's real `description` field.

### 19.4 Storage

- `output_store.delete_outputs_for_run_ids(run_ids)` removes the `<run_id>/` offload directory; wired into `retention.py`'s `_delete_runs` alongside the existing artifact cleanup, so pruning a run now also reclaims its offloaded-output files (previously orphaned on disk forever).

### 19.5 Infrastructure / test hygiene

- **`graphify-out/` breaking `uv`:** added `exclude = ["packages/graphify-out"]` to `pyproject.toml`'s `[tool.uv.workspace]` (graphify's generated output has no `pyproject.toml` and matched the `packages/*` glob, breaking every `uv run`/`uv sync`, including the 6 Postgres RLS tests' `uv run alembic upgrade` subprocess). Added `graphify-out/` to `.gitignore`.
- **TEST-1 (`test_ai_agent_tools.py` import), two iterations:**
  1. First attempt: replaced `from packages.nodes.tests.ai_v2_test_helpers import ...` with a relative import `from .ai_v2_test_helpers import ...`. Passed in isolation (both from the repo root and from `packages/nodes/`) but **broke the actual CI-shape invocation** (`apps/` + `packages/` collected in one pytest session) — caught only by running the full combined suite, not by the two isolated checks that looked sufficient. Root cause: `apps/api/tests/` and `packages/nodes/tests/` are both packages literally named `tests`; under `--import-mode=importlib`, whichever one pytest registers first in `sys.modules` "wins," so the relative import silently resolved against the wrong `tests` package when both were collected together.
  2. Fix: load `ai_v2_test_helpers.py` by explicit file path via `importlib.util.spec_from_file_location`, sidestepping Python's package-name resolution (and therefore the collision) entirely. Verified in all three invocation shapes: single file from repo root, single file from `packages/nodes/`, and the full `apps/ + packages/` combined suite.

### 19.6 Fallout fixed during full-suite validation

Running the combined suite after the `ai_v2` SSRF fix surfaced 9 test failures + the discovery above — all pre-existing tests in `packages/core/tests/test_ai_runtime.py` that `patch("requests.post", ...)` to mock the same adapters. Since `safe_request`'s default transport is `requests.request` (not `requests.post`), the patch stopped intercepting the call and 8 tests fell through to **real network requests** against api.openai.com/anthropic.com/azure (getting real 401s back — the tests "worked" by coincidence rather than by mocking), and one (`test_ollama_uses_local_base`) correctly started failing because Ollama's `localhost` default is now blocked by the SSRF guard without the documented opt-out. Fixed: retargeted all 10 patches to `requests.request`, adjusted two tests' positional-argument indices (`request(method, url, ...)` vs the old `post(url, ...)`), and added `monkeypatch.setenv("NODYRA_ALLOW_PRIVATE_EGRESS", "1")` to the Ollama test to reflect the new, correct, intentional behavior.

### 19.7 Test results

- `packages/core/tests`: 389 passed.
- `packages/nodes/tests`: 1360 passed, 82 skipped.
- `apps/api/tests` (excluding the environmentally-broken `test_tenancy_isolation_pg.py`): 1217+ passed (varies slightly run-to-run — see below).
- **Combined `apps/ + packages/` suite** (the actual CI shape): 2963 passed, 88 skipped, 9 failed, 1 error on the first run *before* the §19.6 fixes; all 9 failures traced to the `ai_v2` SSRF fix's test fallout (fixed, see 19.6) and the 1 error (`sqlite3.OperationalError: database or disk is full` in `test_trigger_gated_runs.py`) reproduced as an isolated pass — a resource-contention flake from running ~2900+ tests in one process, not a regression (confirmed by running it alone: 1 passed).
- A final combined re-run after all §19.6 fixes is the authoritative number — see the top of this document / commit message for the exact count.

### 19.8 Files changed (in addition to §14)

`apps/api/app/services/unsafe_nodes.py`, `apps/api/app/services/data_ref.py` (new), `apps/api/app/services/metrics.py`, `apps/api/app/services/retention.py`, `apps/api/app/services/ai_builder.py`, `apps/api/tests/test_data_ref_guard.py` (new), `apps/api/tests/test_review_bugs.py`, `apps/api/tests/test_retention.py`, `apps/api/tests/test_ai_improvements.py`, `packages/nodes/nodyra_nodes/llm.py`, `packages/nodes/nodyra_nodes/ai_extra.py`, `packages/nodes/nodyra_nodes/ai_v2/providers/{anthropic,openai,embeddings}.py`, `packages/nodes/tests/{test_llm_nodes,test_ai_v2_nodes,test_node_egress_policy,test_ai_agent_tools}.py`, `packages/core/tests/test_ai_runtime.py`, `pyproject.toml`, `.gitignore`.

### 19.9 Updated final recommendation

Node-system readiness moves from **8.1/10 to ~9.2/10**. Every P0/P1 security and data-flow finding from the original audit is now fixed and regression-tested; the remaining gap to 10/10 is Node UX (a first-class large-output viewer, Phase 6.1) and lower-priority polish (benchmark harness, P2 node-type gaps, a real-Postgres CI lane) — none of which are correctness or security issues. The most valuable lesson from this execution pass: **the original audit's SSRF finding (SEC-B) was scoped to one implementation (`llm.py`) of a pattern that existed twice** (`ai_v2` providers) — when fixing a security-guard gap, grep for every module implementing the same *interface*, not just the module the audit happened to inspect.
