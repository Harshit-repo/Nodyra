# Nodyra Production Code-Review Audit — 2026-06-14 (Opus pass)

> **Purpose:** Pre-mortem + extensive, resumable, file-by-file production code review.
> Branch: `feat/arch-program-phase5`.
> This supersedes nothing — it **builds on** `docs/production-review-2026-06-14.md`
> (Sonnet pass, findings F-1…F-14) by (a) re-verifying every F-finding against
> *current* code, and (b) reviewing the freshest, never-audited surface: the
> uncommitted node changes and the new untracked node modules.
>
> **How to use this doc:** the checklist tables below track *every* source file.
> Each pass marks files ✅ reviewed / ⬜ pending / 🔁 re-review. New findings get a
> stable `R-n` id. Resume by picking up the first ⬜ file in priority order.

---

## 0. Method & scope

- ~336 source files: 99 API `.py` (~27k LOC), 24 core, 86 node modules, 127 web `.ts/.tsx`.
- A literal "every line by one reviewer in one sitting" is not honest at this scale;
  instead this is a **prioritised, resumable** sweep. Pass 1 (this doc) covers:
  1. Re-verification of the 14 prior F-findings against current code.
  2. The full uncommitted diff (backend fixes + node changes).
  3. The 3 new untracked node modules (`data_quality`, `geospatial`, `security_automation`)
     and the `browser_automation` additions — **zero prior audit coverage**.
- Pending passes (tracked in §4): web frontend deep pass on this branch, core engine
  re-review, remaining API services line-by-line.
- 2026-06-14 Codex continuation: scope expanded from the original prioritized
  pass toward a literal source inventory sweep (current workspace inventory:
  585 code-ish files including tests/config/scripts). The ledger below is now
  the source of truth for which surfaces are covered and which remain pending.

---

## 1. Pre-Mortem — how a production launch of this branch fails

> Framed as "it's 90 days post-launch and we're in an incident review." Ordered by
> blast radius. ✅ = mitigated in current tree, ⚠️ = still live.

> **Status update (this session):** PM-1, PM-2, PM-3 and PM scenario-2 (R-9) are
> now **fixed with tests**. They remain documented below for the incident-review
> narrative; the ⚠️ markers reflect the *pre-fix* risk.

### PM-1 ✅ Cross-tenant credential read (F-3 / R-1)
A support engineer in the default org opens a workflow that, earlier in the same
request-scoped session, touched a credential belonging to an enterprise tenant
(e.g. via a dispatch path using `skip_org_filter`). `credentials._load` calls
`session.get(Credential, id)` (no `populate_existing=True`), hits the SQLAlchemy
identity map, and **skips the org-filter hook**. Result: read/update/delete of
another tenant's secret. Fixed by forcing a SELECT in `credentials._load` so the
org-filter hook runs even after identity-map priming.

### PM-2 ✅ Tenant data exfiltration via error-workflow (F-4, now *armed* by the F-1 fix)
The F-1 fix (this tree) correctly wraps `dispatch_error_handlers` in
`run_as_system()` so error workflows fire for non-default orgs. Before R-2, the
matching **input validation (F-4) was missing**: an org-A admin could set
`error_workflow_id` to an org-B workflow (existence-only `session.get` check, no
`org_id` comparison). Now that the dispatcher resolves that workflow *across all
orgs*, org-A's failure payload (workflow name, node errors, logs, run ids) is
piped into org-B's workflow as input. Fixing F-1 without F-4 **converts a silent
no-op into a working exfiltration channel.** R-2 now rejects cross-org
`error_workflow_id` references in deployment and workflow writes.

### PM-3 ✅ Workflow nodes bypass the deploy-time safety gate (new)
The new node wave ships filesystem and raw-network capabilities —
`shapefile_read` (reads an arbitrary local path → LFI), `network_port_probe`
(a TCP port scanner → internal recon/SSRF), `sftp_transfer`, `ldap_query`,
`sitemap_crawl`/`geocode` (fetch arbitrary URLs → SSRF). Before R-3 these were
not registered in `services/unsafe_nodes.py`, whose `FILESYSTEM_NODE_TYPES` was
still `frozenset()`.
The prior audit added `duckdb_sql`/`polars_transform` to this classifier for
exactly this reason (DSQ-2); this wave was missed. A deployment containing these
nodes would have activated with no `approve_unsafe_nodes` gate. R-3 now classifies
the new filesystem/network nodes.

### PM-4 ✅ Runner impersonation (F-2) — already closed
The Sonnet doc flagged `remote_dispatch.py:159` as an unauthenticated runner
WebSocket. That line is stale (Phase-3 refactor moved the handler). The live route
`runner_pools.py:573 runner_ws` now requires a signed token whose `sub` must equal
the URL `runner_id` and whose `kind ∈ {runner_registration, k8s_run}` before any
registration. Reclassify F-2 as **resolved**. (`Runner` has no `org_id`, so the
bare `session.get(Runner, …)` in the handshake is not an MT-filter bug.)

### PM-5 ⚠️ Quota "0 = unlimited" foot-gun (F-5) — by design, but billing-risky
`if limits.executions_per_day:` treats `0` as falsy → unlimited. An owner who sets
`0` meaning "stop all runs" instead removes their own ceiling. Plus a check-then-
increment race lets a burst exceed the hard cap. Accepted-by-design per the prior
doc, but call it out in the billing runbook. (carry-over)

### PM-6 ⚠️ Reliability cliffs under load (F-10, F-11, F-13) — still open
Zombie subprocesses from fire-and-forget `proc.close()` (runtime_pool), unbounded
Docker build with no timeout (container_runtime), and the two-session startup gap
that can strand `run_queue` rows as permanently `running`. None are launch
blockers individually; together they're the "why is the worker wedged at 3am"
scenario. (carry-over)

---

## 2. Findings ledger — this pass

| ID | File:line | Sev | Category | Status |
|----|-----------|-----|----------|--------|
| R-1 | `app/routers/credentials.py:84` | 🔴 CRITICAL | MT IDOR (identity-map bypass) | ✅ **Fixed this session** (== F-3) |
| R-2 | `app/routers/deployments.py`×2, `workflows.py:369` (error_workflow_id) | 🔴 CRITICAL | MT exfiltration, armed by F-1 fix | ✅ **Fixed this session** (== F-4) |
| R-3 | `app/services/unsafe_nodes.py` | 🟠 HIGH | New nodes bypass deploy safety gate (LFI/SSRF/port-scan) | ✅ **Fixed this session** |
| R-9 | `app/routers/credentials.py` (oauth start+callback) | 🔴 CRITICAL | OAuth cred stamped/encrypted under DEFAULT_ORG_ID for all non-default orgs (wrong KEK + cross-tenant) | ✅ **Fixed this session** (new, was PM scenario 2) |
| R-4 | `nodyra_nodes/geospatial.py:550` | 🟡 MEDIUM | `not lat or not lon` rejects valid 0,0 (equator/meridian) | ✅ **Fixed this session** |
| R-5 | `nodyra_nodes/geospatial.py:316` | 🟡 MEDIUM | `shapefile_read` reads arbitrary local `path` | ✅ **Mitigated** (now gated via R-3 classifier) |
| R-6 | `nodyra_nodes/data_quality.py:721` | 🟢 LOW | `currency_normalize` mis-parses US thousands `1,000`→`1.0` | ✅ **Fixed in continuation** |
| R-7 | `nodyra_nodes/browser_automation.py` (sitemap_crawl) | 🟢 LOW | Re-fetches a sitemap before the `seen` check; dup queue entries | ✅ **Fixed in continuation** |
| R-8 | `nodyra_nodes/geospatial.py:446` | 🟢 LOW | `geospatial_distance` is O(n·m) over inputs capped at 100k each | ✅ **Fixed in continuation** |
| R-10 | `apps/web/src/EditorPage.tsx:689` (`triggerExport`) | 🟡 MEDIUM | Blob-download `fetch` omitted `X-Org-Id` → export 404s for non-default orgs | ✅ **Fixed this session** (new) |
| R-11 | `app/routers/code_modules.py:101` (`_load`) | 🟡 MEDIUM | Same identity-map IDOR class as R-1; GET/preview lack a permission gate | ✅ **Fixed this session** (populate_existing) |
| R-12 | ~40 `session.get(Model, id)` call sites across routers | 🟢 NOTE | Org filtering on `session.get` relies on a cache *miss*; identity-map *hits* bypass it. Only reachable where a cross-org row is pre-loaded into the same session (credentials/code_modules — both fixed). **Do NOT blanket-add `populate_existing`** — it overwrites unflushed in-session edits. Centralise instead. | **Open** (systemic, documented) |
| R-13 | `app/services/provider_triggers.py:486` (`dispatch_provider_webhook`) | 🔴 CRITICAL | Bare `SessionLocal()` on unauthenticated provider callback → subscription/workflow/dedupe lookups filtered to DEFAULT_ORG_ID; **all non-default-org provider triggers 404 (never fire)** + RunEvent rows mis-stamped | ✅ **Fixed this session** (new) |
| R-14 | `app/routers/environments.py:133` (`create_environment`) | 🟡 MEDIUM | Dedicated-isolation orgs could create an environment bound to an agent runner pool; PATCH rejected it, but POST missed the write-time isolation validator, leaving the failure until dispatch time | ✅ **Fixed in continuation** |
| R-15 | `packages/core/nodyra/datasets.py:117` (`reserve_artifact_path`) | 🟡 MEDIUM | DatasetRef artifacts bypassed `LocalArtifactStore`'s org `key_prefix`, so new Parquet/CSV dataset bytes landed under legacy `runs/...` instead of `{org}/runs/...` in multi-tenant runs | ✅ **Fixed in continuation** |
| R-16 | `packages/nodes/nodyra_nodes/statistical_analysis.py` (`monte_carlo_simulate`, `bootstrap_ci`) | 🟡 MEDIUM | Caller-controlled iteration/resample counts could drive unbounded CPU/memory work before writing artifact-backed results | ✅ **Fixed in continuation** |
| R-17 | `app/services/unsafe_nodes.py`, `integrations_v2/providers/rss/triggers.py` | 🟠 HIGH | v2 RSS/local-file triggers missed the deploy-time unsafe-node gate; RSS also parsed untrusted feed XML with stdlib `ElementTree` | ✅ **Fixed in continuation** |
| R-18 | `packages/nodes/nodyra_nodes/model_serving.py`, `app/services/unsafe_nodes.py` | 🟡 MEDIUM | Model endpoint probe/benchmark/shadow nodes made caller-controlled HTTP calls without deploy-time unsafe gating; benchmark/shadow sizes were unbounded | ✅ **Fixed in continuation** |
| R-19 | `cloud_devops.py`, `ai_extra.py`, `communication.py`, `saas.py`, `storage.py`, `app/services/unsafe_nodes.py` | 🟠 HIGH | DB/cache/search/SaaS/webhook/vector/Git nodes opened caller-supplied hosts without activation review; Git clone/pull had no subprocess timeout; Pinecone hosts accepted private/URL-like values | ✅ **Fixed in continuation** |
| R-20 | `llm.py`, `ai_v2/vectorstores.py`, `ai_v2/mcp.py`, `builtin.py`, `ai_v2/document_loaders.py`, `app/services/unsafe_nodes.py` | 🟠 HIGH | Legacy AI HTTP tools/Pinecone retriever and Qdrant/MCP/GraphQL/URL-loader egress surfaces missed runtime and/or deploy-time coverage | ✅ **Fixed in continuation** |
| R-21 | `file_nodes.py`, `transform_extra.py`, `app/services/unsafe_nodes.py` | 🟠 HIGH | Local file readers could read caller-supplied paths without deploy-time gating; XML parse paths accepted unsafe XML/entity input | ✅ **Fixed in continuation** |
| R-22 | `document_intelligence.py` (`pdf_generate`) | 🟠 HIGH | WeasyPrint default resource loading could fetch local/remote resources referenced by rendered HTML | ✅ **Fixed in continuation** |
| R-23 | `llm_evals.py`, `synthetic_data.py`, `rag_lifecycle.py`, `model_monitoring.py` | 🟡 MEDIUM | LLM judge/compare/synthetic/RAG/monitor nodes allowed unbounded row, example, or concurrency fanout before provider calls | ✅ **Fixed in continuation** |
| R-24 | `integrations.py`, `app/services/unsafe_nodes.py` | 🟠 HIGH | Hidden/deprecated legacy integration nodes (Discord webhook, SMTP, Postgres, MySQL, custom S3 endpoints) opened caller-supplied hosts without activation review | ✅ **Fixed in continuation** |
| R-25 | `datasets.py` (`map_dataset`) | 🟡 MEDIUM | Caller-controlled `max_rows`/`concurrency` could create excessive child-workflow fanout and task scheduling | ✅ **Fixed in continuation** |
| R-26 | `ml.py` (`Register Model`/`Load Model`) | 🔴 CRITICAL | Persistent model registry ignored artifact-store org key prefixes, so tenants sharing a base artifact volume could collide on/load another tenant's registered model by name | ✅ **Fixed in continuation** |
| R-27 | `ai_v2/models.py`, `ai_v2/embeddings.py`, `app/services/unsafe_nodes.py` | 🟡 MEDIUM | AI supplier nodes can target custom OpenAI-compatible/Ollama/Azure/base-url endpoints, but deploy-time unsafe coverage only flagged downstream tools/vector/MCP surfaces | ✅ **Fixed in continuation** |
| R-28 | `ai_v2/document_loaders.py`, `ai_v2/text_splitters.py`, `ai_v2/vectorstores.py`, `ai_v2/retrievers.py`, `app/services/unsafe_nodes.py` | 🟡 MEDIUM | AI RAG ingestion could read local files without deploy review and fan out unbounded text/chunks/vector docs/top-k results | ✅ **Fixed in continuation** |
| R-29 | `charts.py` (`chart`) | 🟢 LOW | Inline list inputs bypassed the chart row cap that DatasetRef inputs already enforced, allowing oversized chart specs | ✅ **Fixed in continuation** |
| R-30 | `builtin.py` (`map_items`, `map_group`) | 🟡 MEDIUM | Built-in map nodes accepted caller-controlled item/max-item/concurrency values that could schedule excessive child-workflow task fanout | ✅ **Fixed in continuation** |
| F-1 | `app/services/run_alerts.py:61` | — | Error handlers under wrong org | ✅ **Verified fixed** (uncommitted) |
| F-7 | `app/mcp/tools.py:436` | — | MCP publish notes=None ValidationError | ✅ **Verified fixed** (uncommitted) |
| F-9 | `app/routers/deployments.py:130,208,256` | — | `runner_pool_id` dropped | ✅ **Verified fixed** (uncommitted) |
| F-12 | `app/services/runner.py:647` | — | `cancel_run` no org context | ✅ **Verified fixed** (uncommitted) |
| F-2 | `runner_pools.py:573` | — | Runner WS auth | ✅ **Already fixed** (doc cited stale line) |

### Fixes applied this session (with regression tests)

All in `apps/api/tests/test_review_bugs.py` (RED-first style) + node tests; full
relevant suites green (`test_review_bugs` 10, `test_credentials_v2` 17,
`test_deployments`/`test_workflows`/`test_org_isolation_enforcement`/`test_tenancy_scoping`
51, `test_package_level_nodes` 5).

- **R-1** — `credentials._load` now uses `session.get(..., populate_existing=True)`.
  Test: `test_credential_load_blocks_cross_org_identity_map` (primes identity map cross-org, asserts 404).
- **R-2** — `deployments.create_deployment`/`update_deployment` + `workflows.update_workflow`
  validate `error_workflow_id` with `session.scalar(select(Workflow).where(id==…))`.
  Test: `test_error_workflow_validation_rejects_cross_org`.
- **R-3** — `unsafe_nodes.py`: `FILESYSTEM_NODE_TYPES={"shapefile_read"}`, new
  `NETWORK_EGRESS_NODE_TYPES` (port_probe/sftp/ldap/cert_inspect/sitemap_crawl) →
  `kind="network_egress"`. Test: `test_unsafe_classifier_flags_new_fs_and_network_nodes`.
- **R-9** — OAuth `start` binds `org_id=active_org_id()` into the signed state;
  `callback` re-applies it (`current_org_id.set`) around scope-validation +
  `encrypt_credential_current` + insert, so stamping/KEK/scope all use the
  initiating org. Falls back to request context when absent (back-compat).
  Test: `test_oauth_callback_stamps_credential_to_initiating_org`.
- **R-4** — `isochrone_generate` validates coordinate *range* not truthiness.
- **R-10** — `EditorPage.triggerExport` sends `X-Org-Id` on the blob-download fetch.
- **R-11** — `code_modules._load` uses `populate_existing=True` (same as R-1).
- **R-13** — `dispatch_provider_webhook` resolves the subscription's org via
  `run_as_system()` then runs the whole dispatch under new `tenancy.run_as_org(org)`,
  so lookups/dedupe/RunEvent stamping all use the right tenant. New
  `run_as_org` context manager added to `tenancy.py`. Test:
  `test_provider_webhook_resolves_non_default_org_subscription`.
- **R-14** — `create_environment` now calls `validate_pool_assignment` with
  `active_org_id()` after the runner pool reference check, matching the PATCH
  path. `validate_pool_assignment` now uses `HTTP_422_UNPROCESSABLE_CONTENT`
  to avoid the deprecated FastAPI status alias. Test:
  `test_environment_create_validates_dedicated_pool_assignment`.
- **R-15** — `datasets.reserve_artifact_path` now delegates storage-key
  construction to `LocalArtifactStore._storage_key`, so DatasetRef-backed
  artifacts inherit the store's org prefix while prefix-free stores keep the
  legacy `runs/...` layout. Tests:
  `test_reserve_artifact_path_uses_store_key_prefix` and
  `test_reserve_artifact_path_keeps_legacy_layout_without_prefix`.
- **R-16** — `monte_carlo_simulate` and `bootstrap_ci` now reject non-positive
  and over-cap iteration/resample counts before expensive optional imports or
  array/bootstrap work; `bootstrap_ci` also validates `confidence_level`.
  Tests: `test_monte_carlo_simulate_rejects_unbounded_iterations`,
  `test_bootstrap_ci_rejects_unbounded_resamples`, and
  `test_bootstrap_ci_rejects_invalid_confidence_level`.
- **R-17** — `unsafe_nodes.classify` now flags `rss_feed_trigger` as
  caller-controlled network egress and flags `file_change_trigger` when its
  `source_type` is local or expression-driven; S3/GCS polling remains unflagged
  as fixed-provider credentialed access. RSS feed XML parsing now uses
  `defusedxml`. Tests: extended
  `test_unsafe_classifier_flags_new_fs_and_network_nodes` and added
  `test_rss_rejects_unsafe_xml_entities`.
- **R-18** — `unsafe_nodes.classify` now flags the model-serving endpoint probe,
  benchmark, and shadow-compare nodes as caller-controlled network egress.
  `model_endpoint_benchmark` now caps total requests and concurrency, and
  `shadow_compare_endpoint` caps prompt count and concurrency before loading
  `requests`. Tests:
  `test_endpoint_benchmark_rejects_unbounded_requests`,
  `test_endpoint_benchmark_rejects_unbounded_concurrency`, and
  `test_shadow_compare_rejects_unbounded_prompt_list`.
- **R-19** — `unsafe_nodes.classify` now flags additional credentialed or
  caller-hosted egress nodes (`mongodb_query`, `redis_command`,
  `elasticsearch_search`, `pinecone_*`, Teams webhook, Calendly/Jira/Shopify,
  and Git clone/pull). `git_clone`/`git_pull` now run through a bounded async
  subprocess helper that kills timed-out Git processes. Pinecone upsert/query
  now construct URLs from host-only `index_host` values and reject private or
  URL-like hosts before `requests.post`. Tests:
  extended `test_unsafe_classifier_flags_new_fs_and_network_nodes`,
  `test_pinecone_query_rejects_private_index_host`,
  `test_pinecone_upsert_rejects_url_like_index_host`,
  `test_git_clone_timeout_kills_process`, and
  `test_git_pull_timeout_kills_process`.
- **R-20** — legacy `ai_tool` execution now applies the same public-target URL
  guard as v2 AI HTTP tools, and legacy `ai_vector_retriever` now validates
  Pinecone `index_host` before building `https://.../query`. Qdrant vector store
  requests now validate the final target URL before sending API-key-authenticated
  requests. The deploy classifier now flags AI tool/vector/MCP supplier egress
  (`ai_tool`, `ai_http_tool`, `ai_vector_retriever`, `ai_qdrant_vector_store`,
  `mcp_tools`, `mcp_call_tool`, `mcp_list_tools`) and extends private-target
  detection to `graphql_request` and `ai_url_document_loader`. Tests:
  `test_legacy_ai_tool_blocks_private_targets`,
  `test_ai_vector_retriever_blocks_private_pinecone_host`,
  `test_qdrant_vector_store_blocks_private_targets`, and
  `test_mcp_session_blocks_private_target`.
- **R-6/R-7/R-8** — closed the low-severity node correctness/workload nits:
  currency normalization now distinguishes comma thousands from comma decimals,
  `sitemap_crawl` checks/marks seen sitemap URLs before fetching and avoids
  duplicate child queue entries, and `geospatial_distance` rejects excessive
  cross-products via a bounded `max_pairs` option. Tests:
  `test_currency_normalize_keeps_us_thousands_separator`,
  `test_sitemap_crawl_does_not_fetch_duplicate_nested_sitemaps`, and
  `test_geospatial_distance_rejects_excessive_cross_product`.
- **R-21** — local-path file reader nodes are now deploy-gated only when a
  workflow supplies a local `path` (artifact upload mode stays usable);
  `read_xml_file` and `xml_parse` use `defusedxml`/entity-disabled parsing and
  reject unsafe XML. Tests:
  `test_unsafe_classifier_flags_new_fs_and_network_nodes`,
  `test_read_xml_dataset_rejects_unsafe_entities`, and
  `test_xml_parse_rejects_unsafe_entities`.
- **R-22** — `pdf_generate` passes a WeasyPrint URL fetcher that rejects
  external/local resource loads from rendered HTML. Test:
  `test_pdf_generate_blocks_external_resource_fetches`.
- **R-23** — LLM-backed eval, synthetic data, RAG, and response-quality monitor
  nodes now cap row/sample/example counts and concurrency before provider calls.
  Tests cover excessive concurrency and uncapped large-dataset rejection in
  `test_llm_evals.py`, `test_synthetic_data.py`, `test_rag_lifecycle.py`, and
  `test_model_monitoring.py`.
- **R-24** — unsafe-node classifier now flags legacy arbitrary-host integration
  nodes (`discord_send_message`, `smtp_send_email`, `postgres_query`,
  `mysql_query`) and only flags legacy S3 nodes when `endpoint_url` is supplied
  for custom S3-compatible storage. Test:
  `test_unsafe_classifier_flags_new_fs_and_network_nodes`.
- **R-25** — `map_dataset` now enforces hard caps of 10k rows and 50 concurrent
  child workflow calls before materializing rows or scheduling tasks. Tests:
  `test_map_dataset_rejects_excessive_row_cap` and
  `test_map_dataset_rejects_excessive_concurrency`.
- **R-26** — persistent ML registry root now includes the artifact store
  `key_prefix` (`{org}/model-registry/...`) while preserving the single-tenant
  `model-registry/...` path when no prefix exists. Test:
  `test_model_registry_uses_artifact_key_prefix`.
- **R-27** — deploy classifier now conditionally flags AI supplier nodes only
  when they select custom endpoint classes (`openai_compatible`, `ollama`),
  Azure OpenAI, or explicit endpoint fields. Fixed-provider OpenAI/embedding
  suppliers remain ungated. Test:
  `test_unsafe_classifier_flags_new_fs_and_network_nodes`.
- **R-28** — `ai_file_document_loader` is now covered by the filesystem
  deploy gate. AI document loaders reject oversized text/local-file/URL bodies,
  recursive splitting enforces `max_chunks`, vector upsert enforces
  `max_documents`, and retriever/vector-store top-k is capped at 100. Tests:
  `test_text_document_loader_rejects_oversized_text`,
  `test_file_document_loader_rejects_oversized_file`,
  `test_recursive_text_splitter_rejects_excessive_chunks`,
  `test_vector_store_upsert_rejects_excessive_document_count`, and
  `test_retrieve_documents_caps_top_k`.
- **R-29** — chart inline list inputs now apply the same `_CHART_CAP` as
  DatasetRef materialization. Test: `test_chart_caps_inline_list_inputs`.
- **R-30** — built-in `map_items` and `map_group` now enforce hard caps of
  10k child-workflow calls and 50 concurrent calls before scheduling tasks.
  Tests: `test_map_items_rejects_excessive_item_count`,
  `test_map_items_rejects_excessive_concurrency`,
  `test_map_group_rejects_excessive_max_items`, and
  `test_map_group_rejects_excessive_concurrency`.

### Fix sketches (remaining open items)

**R-12** (`session.get` systemic): create a small tenant-safe load helper for
request handlers that need ID lookups on org-scoped models and reserve
`populate_existing=True` for pure read/load paths. Avoid a blanket replacement:
it can overwrite unflushed in-session edits.

**Geocode policy decision**: `geocode`/`reverse_geocode` call Nominatim rather
than arbitrary user-supplied hosts, so they were not added to R-3's raw egress
classifier. Decide whether that fixed-provider outbound HTTP should still be
blocked or approved under the same deploy-time policy in high-control installs.

---

## 3. Verified-clean this pass

- `security_automation.py` — `jwt_sign_verify` pins `algorithms=[algorithm]` (no alg-confusion); decode mode's `verify_signature: False` is explicit and labelled `verified: False`. RSA uses PSS. No secrets logged. (Network nodes are R-3 policy, not code bugs.)
- `data_quality.py` — schema/expectation/reconcile/outlier logic sound; JSON parse guarded; `_to_records` caps at 100k. R-6 currency heuristic fixed.
- `browser_automation.py` — **good hardening**: XML parsing uses `defusedxml` (`fromstring`/`ParseError`/`DefusedXmlException`) → XXE/billion-laughs safe. `sitemap_crawl` caps `max_urls` and sitemap count (≤25), and R-7 duplicate fetch/queue bug is fixed.
- Uncommitted backend fixes (`run_alerts`, `runner.cancel_run`, `mcp/tools`, `deployments`) — correct; also added `_validate_deployable` (rejects trigger-less deploys early) and run-level error persistence to `run_events` so MCP `get_run`/UI surface pre-exec failures. Matches the RED tests in `tests/test_review_bugs.py`.
- New nodes are correctly registered in `nodyra_nodes/__init__.py` (`__all__` + imports).
- Continuation pass: `environments.py`, `nodes.py`, and `ops.py` re-read.
  `nodes.py` user source and dynamic-option credential paths rely on fresh
  request sessions and ambient org filters; no practical identity-map priming
  path found beyond the already-documented R-12 systemic note. `ops.py` is
  read/metrics/dead-letter control; mutation/replay paths are RBAC-gated.
- Continuation pass: `credential_tests.py`, `events.py`, and `subworkflows.py`
  re-read. Credential tests are an intentional, `credential:test`-gated outbound
  probe surface (DB/API credential validation); document as operator-controlled
  SSRF-capable behavior, not a code bug. Event broker has Redis/in-process
  history caps and stale-buffer reaping. Subworkflow resolver inherits the root
  run org through `_execute_run` context and creates child runs with the parent
  org under `run_as_system()`.
- Continuation pass: `expr_preview_worker.py`, `process_isolation.py`, and
  `serialization.py` re-read. Expression preview remains out-of-process with a
  minimal protocol/env; process pool eviction is completion-aware; typed JSON
  serialization avoids pickle and caps persisted/display values.
- Continuation pass: `artifacts.py` and `datasets.py` re-read. Artifact writes
  already used `LocalArtifactStore._storage_key` and path traversal guards;
  DatasetRef reservations did not, producing R-15. The fix keeps normal artifact
  and dataset artifact key construction aligned.
- Continuation pass: `schemas.py` re-read. No schema-only production bug found:
  write permissions and cross-org references are enforced in routers/services,
  and `NodeRunInfo` strips `storage_key`/`storage_backend` recursively before
  run output is served.
- Continuation pass: `statistical_analysis.py` re-read. Prior TEST-1 lazy-import
  fixes remain correct and expression evaluation still uses the hardened AST
  validator. R-16 bounds the only newly found unbounded simulation/bootstrap
  workloads.
- Continuation pass: `integrations_v2/*` re-read for security posture. Generated
  node source is built only from trusted specs with identifier validation;
  provider transports use fixed base URLs, request timeouts, retry caps, debug
  URL scrubbing, and pagination caps. R-17 fixed the missed v2 trigger policy
  coverage and RSS XML parsing.
- Continuation pass: `model_serving.py` re-read. Deployment-spec generation only
  writes artifacts, but endpoint probe/benchmark/shadow nodes perform
  caller-supplied network egress and load generation, producing R-18.
- Continuation pass: `ai_extra.py`, `storage.py`, `cloud_devops.py`,
  `communication.py`, and `saas.py` re-read for arbitrary host/URL surfaces.
  Fixed-provider calls are left as normal integration behavior; caller-supplied
  DB/cache/search/webhook/SaaS/vector/Git hosts now hit the production unsafe
  gate, and Git/Pinecone gained runtime hardening under R-19.
- Continuation pass: legacy `llm.py` tool/retriever paths and AI v2
  `tools.py`, `mcp.py`, `vectorstores.py`, and URL document-loader surfaces
  re-read. V2 HTTP tools and MCP already had private-target runtime guards;
  R-20 aligned legacy AI tools, Qdrant, and deploy-time classifier coverage.
- Continuation pass: file reader/transform XML surfaces re-read. R-21 aligned
  local-path reader gating with artifact-upload mode and replaced unsafe XML
  parsing with `defusedxml`/entity-disabled parsing.
- Continuation pass: `document_intelligence.py` re-read for file/archive/PDF
  generation surfaces. R-22 blocks WeasyPrint local/remote resource fetches.
- Continuation pass: `llm_evals.py`, `synthetic_data.py`, `rag_lifecycle.py`,
  and `model_monitoring.py` re-read for provider fanout. R-23 bounds
  LLM-backed row/sample/example counts and concurrency.
- Continuation pass: `datasets.py`, `ml.py`, and legacy `integrations.py`
  re-read. R-24 covers hidden/deprecated arbitrary-host integrations at deploy
  time; R-25 bounds child-workflow fanout; R-26 namespaces persistent model
  registry storage by org key prefix.
- Continuation pass: AI v2 model/embedding supplier nodes re-read. R-27 adds
  deploy-time review for custom model-provider endpoints without blanket-gating
  fixed-provider OpenAI/Anthropic usage.
- Continuation pass: AI v2 document loaders, text splitter, vector stores, and
  retrievers re-read. R-28 adds filesystem gate coverage and bounded RAG
  ingestion/retrieval fanout.
- Continuation pass: `charts.py` re-read. Chart rendering escapes SVG text and
  caps width/height; R-29 applies the existing row cap to inline lists.
- Continuation pass: `builtin.py` sub-workflow map nodes re-read. R-30 aligns
  built-in map fanout and concurrency caps with `map_dataset`.

---

## 4. File-by-file checklist (resumable ledger)

Legend: ✅ reviewed this program · ⬜ pending · 🔁 needs re-review on this branch · 🐞 finding attached

### 4.1 API — routers (`apps/api/app/routers/`)
| File | Status | Notes |
|------|--------|-------|
| credentials.py | ✅ 🐞R-1/R-9 | `_load` identity-map IDOR fixed; OAuth org-in-state fixed |
| deployments.py | ✅ 🐞R-2/F-9 | F-9 fixed, `_validate_deployable` added; `error_workflow_id` org-check fixed |
| workflows.py | ✅ 🐞R-2 | `error_workflow_id` validation fixed |
| runs.py | ✅ | F-8/F-12 paths verified; WS ticket auth reviewed |
| runner_pools.py | ✅ | F-2 WS auth confirmed fixed |
| auth.py | ✅ | prior audit (rate-limit, TOCTOU, CSRF) |
| orgs.py | ✅ | **RBAC solid.** `_require_org_role` checks actor's role in the path/active org; owner-only transitions enforced (admins can't grant/remove owner or escalate); last-owner protected; `skip_org_filter` reads keyed on `(org_id, user_id)`. No findings. |
| environments.py | ✅ 🐞R-14 | create path now enforces dedicated-pool assignment validator; package usage/pool refs reviewed |
| artifacts.py | ✅ | path-traversal + `populate_existing` reviewed (ART-1) |
| webhooks.py / triggers | ✅ | HMAC + JWT alg-pin reviewed |
| mcp.py / chat_public.py | ✅ | Phase-7 signature fix verified |
| code_modules.py | ✅ 🐞R-11 | **No API-process RCE** — discovery is pure `ast.parse`, code only executes in the sandboxed runner. `_load` IDOR fixed (R-11). |
| internal.py | ✅ | constant-time shared-secret; blank-token documented + warned; only idempotent scheduler tick |
| expressions.py + services/expr_preview.py | ✅ | **Isolation confirmed** — preview runs in a subprocess with a minimal secret-free env, timeout kills+respawns, never in-process. Residual: no memory rlimit on the worker (LOW; isolated + killed on timeout + auth-gated). |
| nodes.py, ops.py | ✅ | source/dynamic-options and ops/dead-letter/metrics paths reviewed |

### 4.2 API — services (`apps/api/app/services/`)
| File | Status | Notes |
|------|--------|-------|
| run_alerts.py | ✅ 🐞R-2 | F-1 fixed; paired with R-2 validation fix |
| runner.py | ✅ | F-12 + run-error persistence verified; core reviewed prior |
| unsafe_nodes.py | ✅ 🐞R-3/R-17/R-18/R-19/R-20 | filesystem/private-target/network-egress classifier coverage expanded |
| remote_dispatch.py / providers/* | ✅ | RD-1/RD-2 prior; agent.py WS handler re-read |
| queue.py | ✅ | SKIP LOCKED + double-exec guard (prior) |
| sandbox_pool.py / process_isolation | ✅ | prior; SEC-1b accepted-with-mitigation |
| runtime_pool.py | 🐞 F-10 | zombie `proc.close()` open |
| container_runtime.py | 🐞 F-11 | no Docker build timeout open |
| metering.py / org_limits.py | 🐞 F-5 | quota `0`=unlimited + race (by design) |
| crypto.py / org_keys.py | ✅ | TOK-1, KEK reviewed |
| oauth.py | ✅ 🐞R-9 | org-in-state fixed (R-9) |
| ai_builder.py | ✅ | **Reviewed clean.** Runs on request-scoped org-filtered session (caller `_load`s wf first, permission-gated); LLM endpoints hardcoded (no SSRF); `_sanitize_params` strips secrets/cred-refs from LLM output; never executes the plan. `_find_credential` relies on the ambient org filter — correct. |
| provider_triggers.py | ✅ 🐞R-13 | bare-SessionLocal MT bug fixed; sync/activate paths reviewed |
| credential_tests.py, events.py, subworkflows.py | ✅ | credential probes are intentional operator-controlled egress; event buffer caps/reaping and subworkflow org inheritance reviewed |

### 4.3 API — core (`apps/api/app/`)
| File | Status | Notes |
|------|--------|-------|
| main.py | 🐞 F-13 | two-session startup gap open; CSRF/body-limit reviewed |
| tenancy.py | ✅ | org-filter hooks + `run_as_system` (central to PM-1/2) |
| security.py | ✅ | resolve_org/require_permission |
| models.py | ✅ | Runner confirmed no org_id; FK/constraint drift fixed (ALM-2) |
| schemas.py | ✅ | full re-read; request bounds and response redaction reviewed |
| config.py, db.py | ✅ | prior wave |

### 4.4 packages/core (`nodyra/`)
| File | Status | Notes |
|------|--------|-------|
| engine/* | ✅ | Phase 2/4 review; ENG-1 fixed |
| expr.py | ✅ | EXPR-1 sandbox escape fixed + grep-confirmed |
| expr_preview_worker.py | ✅ | preview eval confirmed out-of-process with minimal env/protocol |
| process_isolation.py, serialization.py | ✅ | completion-aware pool eviction; pickle-free typed JSON serialization reviewed |
| artifacts.py, datasets.py | ✅ 🐞R-15 | DatasetRef reservations now honor org key prefixes; artifact path safety/limits reviewed |

### 4.5 packages/nodes (`nodyra_nodes/`)
| File | Status | Notes |
|------|--------|-------|
| security_automation.py | ✅ 🐞R-3 | clean code; policy-gate gap only |
| geospatial.py | ✅ 🐞R-4/R-5/R-8 | R-4 fixed; R-5 mitigated by unsafe-node gate; R-8 cross-product cap added |
| data_quality.py | ✅ 🐞R-6 | currency thousands heuristic fixed |
| browser_automation.py | ✅ 🐞R-7 | defusedxml hardening good; duplicate sitemap fetch/queue fixed |
| statistical_analysis.py | ✅ 🐞R-16 | TEST-1 lazy-import fixes re-read; simulation/bootstrap workload caps added |
| integrations_v2/* | ✅ 🐞R-17 | security pass complete; v2 trigger gate/XML parser gap fixed |
| model_serving.py | ✅ 🐞R-18 | endpoint nodes now unsafe-gated; benchmark/shadow workload caps added |
| ai_extra.py, storage.py, cloud_devops.py, communication.py, saas.py | ✅ 🐞R-19 | arbitrary host/URL egress surfaces now unsafe-gated; Pinecone/Git runtime hardening added |
| llm.py, ai_v2/tools.py, ai_v2/mcp.py, ai_v2/vectorstores.py | ✅ 🐞R-20 | legacy AI tools/Pinecone and Qdrant runtime egress guards; AI/MCP deploy gate coverage |
| builtin.py HTTP/GraphQL/map surfaces, ai_v2/document_loaders.py URL surface | ✅ 🐞R-20/R-30 | runtime private-target guards verified; classifier coverage aligned for GraphQL/URL-loader; map fanout caps added |
| file_nodes.py, transform_extra.py | ✅ 🐞R-21 | local-path reader gate plus unsafe XML rejection |
| document_intelligence.py | ✅ 🐞R-22 | WeasyPrint external resource fetch blocked |
| llm_evals.py, synthetic_data.py, rag_lifecycle.py, model_monitoring.py | ✅ 🐞R-23 | LLM row/sample/example/concurrency caps added |
| integrations.py | ✅ 🐞R-24 | hidden/deprecated arbitrary-host nodes now unsafe-gated |
| datasets.py | ✅ 🐞R-25 | dataset SQL/code paths re-read; map fanout caps added |
| ml.py | ✅ 🐞R-26 | model registry now honors org key prefixes |
| ai_v2/models.py, ai_v2/embeddings.py, ai_v2/providers/*, ai_v2/model_options.py | ✅ 🐞R-27 | custom model-provider endpoints now unsafe-gated; fixed-provider behavior reviewed |
| ai_v2/document_loaders.py, ai_v2/text_splitters.py, ai_v2/vectorstores.py, ai_v2/retrievers.py | ✅ 🐞R-20/R-28 | URL/private-target guards plus local-file gate and RAG ingestion/top-k caps |
| charts.py | ✅ 🐞R-29 | SVG escaping/render size reviewed; inline list cap added |
| remaining node modules | ⬜ | bulk pending — triage by capability (network/fs/eval first) |

### 4.6 apps/web (`src/`)
| File | Status | Notes |
|------|--------|-------|
| api.ts | 🐞 F-14 | X-Org-Id omitted when orgId null (open) |
| editor/store/index.ts | 🔁 | 2236 LOC monolith (cosmetic debt, noted prior) |
| queries/* | 🔁 | nullable-id copy-paste (noted prior) |
| EditorPage.tsx | ✅ 🐞R-10 | export X-Org-Id fixed; no dangerouslySetInnerHTML/eval introduced |
| DeploymentsPage/EnvironmentsPage (heavy diff) | 🔁 | scanned for XSS/auth (clean); full functional re-review pending |
| RunnerPoolSelect (new) | ✅ | clean; minor a11y nits (radiogroup role, Space preventDefault) |
| NodeIcon (new icons) | ✅ | presentational; test updated |
| FE-1…FE-14 surfaces | ✅ | fixed in prior frontend deep pass |
| remaining ~110 components | ⬜ | bulk pending |

---

## 5. Recommended fix order before enabling `MULTI_TENANCY_ENABLED=true`

1. Reliability backlog (F-10/F-11/F-13) before any high-throughput tenant.
2. Decide whether to harden credential-test outbound probes for untrusted editors
   (private-IP denylist/proxy policy) or document them as admin-controlled egress.
3. Address the systemic R-12 `session.get` foot-gun with a central tenant-safe
   helper/pattern instead of scattershot `populate_existing=True`.
4. Continue the pending file passes in §4 (remaining node modules and the
   frontend bulk pass).

## 6. Verification commands
```bash
cd apps/api && pytest tests/test_review_bugs.py -v          # F-1/7/8/9/12 RED→GREEN
pytest tests/test_org_isolation_enforcement.py -v           # add R-1/R-2 cases here
cd packages/nodes && pytest tests/test_package_level_nodes.py -v

# Continuation verification run 2026-06-14:
D:\nodyra\.venv\Scripts\python.exe -m pytest apps/api/tests/test_org_isolation_enforcement.py -q
D:\nodyra\.venv\Scripts\python.exe -m pytest apps/api/tests/test_environments.py -q
D:\nodyra\.venv\Scripts\python.exe -m ruff check apps/api/app/routers/environments.py apps/api/app/services/isolation.py apps/api/tests/test_org_isolation_enforcement.py
D:\nodyra\.venv\Scripts\python.exe -m pytest packages/core/tests/test_datasets.py packages/core/tests/test_artifacts.py packages/core/tests/test_artifacts_extended.py -q
D:\nodyra\.venv\Scripts\python.exe -m ruff check packages/core/nodyra/datasets.py packages/core/tests/test_datasets.py
D:\nodyra\.venv\Scripts\python.exe -m pytest packages/nodes/tests/test_statistical_analysis.py -q
D:\nodyra\.venv\Scripts\python.exe -m ruff check packages/nodes/nodyra_nodes/statistical_analysis.py packages/nodes/tests/test_statistical_analysis.py
D:\nodyra\.venv\Scripts\python.exe -m pytest apps/api/tests/test_review_bugs.py packages/nodes/tests/test_rss_trigger.py packages/nodes/tests/test_filesystem_trigger.py -q
D:\nodyra\.venv\Scripts\python.exe -m ruff check apps/api/app/services/unsafe_nodes.py apps/api/tests/test_review_bugs.py packages/nodes/nodyra_nodes/integrations_v2/providers/rss/triggers.py packages/nodes/tests/test_rss_trigger.py
D:\nodyra\.venv\Scripts\python.exe -m pytest packages/nodes/tests/test_model_serving.py apps/api/tests/test_review_bugs.py -q
D:\nodyra\.venv\Scripts\python.exe -m ruff check packages/nodes/nodyra_nodes/model_serving.py packages/nodes/tests/test_model_serving.py apps/api/app/services/unsafe_nodes.py apps/api/tests/test_review_bugs.py
D:\nodyra\.venv\Scripts\python.exe -m pytest apps/api/tests/test_review_bugs.py packages/nodes/tests/test_ai_extra.py packages/nodes/tests/test_cloud_devops.py packages/nodes/tests/test_llm_nodes.py packages/nodes/tests/test_ai_v2_nodes.py packages/nodes/tests/test_ai_v2_mcp.py packages/nodes/tests/test_new_node_registration.py -q
D:\nodyra\.venv\Scripts\python.exe -m ruff check apps/api/app/services/unsafe_nodes.py apps/api/tests/test_review_bugs.py packages/nodes/nodyra_nodes/ai_extra.py packages/nodes/tests/test_ai_extra.py packages/nodes/nodyra_nodes/cloud_devops.py packages/nodes/tests/test_cloud_devops.py packages/nodes/nodyra_nodes/llm.py packages/nodes/tests/test_llm_nodes.py packages/nodes/nodyra_nodes/ai_v2/vectorstores.py packages/nodes/tests/test_ai_v2_nodes.py packages/nodes/tests/test_ai_v2_mcp.py
D:\nodyra\.venv\Scripts\python.exe -m pytest apps/api/tests/test_org_isolation_enforcement.py apps/api/tests/test_environments.py apps/api/tests/test_review_bugs.py packages/core/tests/test_datasets.py packages/core/tests/test_artifacts.py packages/core/tests/test_artifacts_extended.py packages/nodes/tests/test_statistical_analysis.py packages/nodes/tests/test_rss_trigger.py packages/nodes/tests/test_filesystem_trigger.py packages/nodes/tests/test_model_serving.py packages/nodes/tests/test_ai_extra.py packages/nodes/tests/test_cloud_devops.py packages/nodes/tests/test_llm_nodes.py packages/nodes/tests/test_ai_v2_nodes.py packages/nodes/tests/test_ai_v2_mcp.py packages/nodes/tests/test_new_node_registration.py -q

# Deep-continuation verification run 2026-06-14:
D:\nodyra\.venv\Scripts\python.exe -m pytest apps/api/tests/test_review_bugs.py packages/nodes/tests/test_file_nodes.py packages/nodes/tests/test_transform_extra.py -q
D:\nodyra\.venv\Scripts\python.exe -m ruff check apps/api/app/services/unsafe_nodes.py apps/api/tests/test_review_bugs.py packages/nodes/nodyra_nodes/file_nodes.py packages/nodes/tests/test_file_nodes.py packages/nodes/nodyra_nodes/transform_extra.py packages/nodes/tests/test_transform_extra.py
D:\nodyra\.venv\Scripts\python.exe -m pytest packages/nodes/tests/test_package_level_nodes.py packages/nodes/tests/test_browser_automation.py -q
D:\nodyra\.venv\Scripts\python.exe -m ruff check packages/nodes/nodyra_nodes/data_quality.py packages/nodes/nodyra_nodes/browser_automation.py packages/nodes/nodyra_nodes/geospatial.py packages/nodes/tests/test_package_level_nodes.py packages/nodes/tests/test_browser_automation.py
D:\nodyra\.venv\Scripts\python.exe -m pytest packages/nodes/tests/test_document_intelligence.py -q
D:\nodyra\.venv\Scripts\python.exe -m ruff check packages/nodes/nodyra_nodes/document_intelligence.py packages/nodes/tests/test_document_intelligence.py
D:\nodyra\.venv\Scripts\python.exe -m pytest packages/nodes/tests/test_llm_evals.py packages/nodes/tests/test_synthetic_data.py packages/nodes/tests/test_rag_lifecycle.py packages/nodes/tests/test_model_monitoring.py -q
D:\nodyra\.venv\Scripts\python.exe -m ruff check packages/nodes/nodyra_nodes/llm_evals.py packages/nodes/nodyra_nodes/synthetic_data.py packages/nodes/nodyra_nodes/rag_lifecycle.py packages/nodes/nodyra_nodes/model_monitoring.py packages/nodes/tests/test_llm_evals.py packages/nodes/tests/test_synthetic_data.py packages/nodes/tests/test_rag_lifecycle.py packages/nodes/tests/test_model_monitoring.py
D:\nodyra\.venv\Scripts\python.exe -m pytest apps/api/tests/test_review_bugs.py packages/nodes/tests/test_datasets.py packages/nodes/tests/test_ml.py -q
D:\nodyra\.venv\Scripts\python.exe -m ruff check apps/api/app/services/unsafe_nodes.py apps/api/tests/test_review_bugs.py packages/nodes/nodyra_nodes/datasets.py packages/nodes/tests/test_datasets.py packages/nodes/nodyra_nodes/ml.py packages/nodes/tests/test_ml.py
D:\nodyra\.venv\Scripts\python.exe -m pytest apps/api/tests/test_review_bugs.py -q
D:\nodyra\.venv\Scripts\python.exe -m ruff check apps/api/app/services/unsafe_nodes.py apps/api/tests/test_review_bugs.py
D:\nodyra\.venv\Scripts\python.exe -m pytest packages/nodes/tests/test_ai_v2_nodes.py apps/api/tests/test_review_bugs.py -q
D:\nodyra\.venv\Scripts\python.exe -m ruff check apps/api/app/services/unsafe_nodes.py apps/api/tests/test_review_bugs.py packages/nodes/nodyra_nodes/ai_v2/document_loaders.py packages/nodes/nodyra_nodes/ai_v2/text_splitters.py packages/nodes/nodyra_nodes/ai_v2/vectorstores.py packages/nodes/nodyra_nodes/ai_v2/retrievers.py packages/nodes/tests/test_ai_v2_nodes.py
D:\nodyra\.venv\Scripts\python.exe -m pytest packages/nodes/tests/test_ai_v2_nodes.py apps/api/tests/test_review_bugs.py packages/nodes/tests/test_charts.py -q
D:\nodyra\.venv\Scripts\python.exe -m ruff check apps/api/app/services/unsafe_nodes.py apps/api/tests/test_review_bugs.py packages/nodes/nodyra_nodes/ai_v2/document_loaders.py packages/nodes/nodyra_nodes/ai_v2/text_splitters.py packages/nodes/nodyra_nodes/ai_v2/vectorstores.py packages/nodes/nodyra_nodes/ai_v2/retrievers.py packages/nodes/tests/test_ai_v2_nodes.py packages/nodes/nodyra_nodes/charts.py packages/nodes/tests/test_charts.py
uv run pytest packages/nodes/tests/test_map_nodes.py packages/core/tests/test_org_caps.py -q
uv run ruff check packages/nodes/nodyra_nodes/builtin.py packages/nodes/tests/test_map_nodes.py
```

Latest targeted continuation verification result: `147 passed, 1 warning`.
Latest broader continuation regression result: `316 passed, 7 skipped, 1 warning`.
Latest deep-continuation targeted results: file/XML `50 passed, 1 warning`;
node nits `44 passed, 3 skipped, 1 warning`; document intelligence
`19 passed, 18 skipped, 1 warning`; LLM/RAG/monitoring fanout
`134 passed, 4 skipped, 1 warning`; classifier/dataset/ML registry
`51 passed, 6 skipped, 1 warning`; custom AI supplier classifier
`11 passed, 1 warning`; AI v2 RAG ingestion `76 passed, 1 warning`;
AI v2 + charts cap regression `86 passed, 1 warning`; built-in map fanout
`17 passed, 1 warning` plus Ruff clean.
The warning is the known Pydantic `NodyraItem.json` shadowing warning
intentionally left alone.

*Generated 2026-06-14. Branch `feat/arch-program-phase5`. Reviewer: Claude Opus 4.8; continuation update by Codex.*
