# Nodyra 10/10 Production Execution Plan

**Plan owner:** Nodyra maintainers

**Re-baselined:** 2026-07-13

**Baseline commit:** `093d5e34` on `fable5-nodyra-full-test-bugfix-production-plan`

**Status:** Active — Phase 0 complete; Phase 1 implemented with external evidence gates pending

**Companion evidence:** `FABLE5_NODYRA_FULL_TEST_BUGFIX_AND_PRODUCTION_IMPROVEMENT_PLAN.md`

This is the canonical implementation plan for taking Nodyra from a strong technical beta to a production-grade, broadly adoptable product. It replaces the previous incomplete master plan and consolidates the architecture, engine, artifact, UX, security, operations, performance, documentation, ecosystem, licensing, and adoption recommendations into one dependency-ordered program.

“10/10” is defined by measurable release gates. It does not mean defect-free software; it means every critical capability has an accountable owner, enforced acceptance criteria, observable failure modes, documented recovery, and evidence from production-like testing.

---

## 1. Executive outcome

Nodyra should become the trusted Python-native automation control plane for platform, operations, data, and AI engineering teams that have outgrown scripts but do not want an opaque no-code system.

The product reaches the target state when:

- A new evaluator reaches a successful, inspectable workflow in under 10 minutes without real credentials.
- Local mode never claims production readiness, and production readiness is machine-verifiable.
- Runs are never silently lost or duplicated across worker loss, retry, cancellation, timeout, and restart scenarios.
- The engine has deterministic failure aggregation, bounded scheduling behavior, supervised worker tasks, and scale benchmarks.
- Artifact storage is integrity-checked, stream-capable, observable, and backend-extensible.
- The editor exposes a curated core catalog first while retaining the full advanced catalog on demand.
- The UI meets WCAG 2.2 AA, defined interaction latency budgets, and desktop/tablet responsive contracts.
- Community users can evaluate safe execution and useful observability before encountering commercial gates.
- Releases follow SemVer, include signed artifacts/SBOMs, compatibility notes, upgrade tests, and restore evidence.
- Activation, retention, reliability, and ecosystem health are measured with privacy-preserving telemetry.

---

## 2. Verified baseline and recent-change review

### 2.1 Verification performed for this re-baseline

| Verification | Result |
|---|---|
| Recent-change Python suite: MCP, runs, queue, demo scripts, deadlines, engine | **213 passed, 3 skipped** |
| Affected web suites: Executions, Readiness, publishing status | **14 passed** |
| Web typecheck | Required in Phase 0 gate |
| Ruff | One existing import-order failure in `apps/api/tests/test_sandbox_compose.py` |
| Worktree at review start | Clean |

### 2.4 Current implementation snapshot

| Verification | Result |
|---|---|
| Complete Python monorepo suite | **3,210 passed, 98 skipped, 0 failed** in 15m29s |
| Complete web suite | **486 passed, 1 skipped** |
| Web typecheck and production build | **Passed** |
| Web dependency audit | **0 vulnerabilities** |
| Repository Ruff and diff integrity | **Passed** |
| Docker Compose configuration render | **Passed** |
| Live container recovery drill | Pending CI runner with Docker daemon |
| Helm render/lint | Pending CI runner with Helm |

The implementation is locally green. Phase 1 is not release-certified until the
new Linux/Windows/container lanes and restore drill have passed five consecutive
required CI runs and attached their evidence artifacts.

### 2.2 Recent commits reviewed

| Commit | Change | Review status | Follow-up |
|---|---|---|---|
| `8c495dea` | Operator documentation | Accepted | Add link validation, versioned upgrade matrix, and automated restore drill |
| `41b56888` | Demo seed path | Accepted with hardening | Bind demo exposure to loopback, generate ephemeral secrets, and add teardown/idempotency E2E |
| `be4b94dc` | Proof-point demos | Accepted | Connect proofs to public templates and release smoke tests |
| `0c554ad3` | Queue coverage floor and PostgreSQL chaos cases | Accepted | Add process/container-loss end-to-end chaos and soak evidence |
| `ef1af8bb` | Explicit `timed_out` run status | Accepted with correctness follow-up | Define deterministic precedence between `error` and `timed_out` and test mixed parallel failures |
| `9ecedf83` | Queue trend chart | Accepted as a session sparkline | Persist/query real time-series history; fix single-sample copy and stale/error semantics |
| `d2ced5bb` | MCP workflow descriptor paging | Accepted with follow-up | Add deterministic secondary ordering, mutation-safe cursor behavior, and collision-aware totals |
| `093d5e34` | Production architecture ADRs | Accepted | Add ADR checks to review template and link operational evidence back to decisions |

### 2.3 Already completed or materially advanced

- [x] Plain-Python execution, deterministic DAG scheduling, loops, metanodes, partial runs, caching, deadlines, and output caps.
- [x] Durable queue with leases, retry, dead-letter, replay, fairness, labels, and PostgreSQL `SKIP LOCKED` coverage.
- [x] Local and S3 artifact backends with checksum metadata, retention, ranges, org scoping, and browser uploads.
- [x] Split API/worker deployment, Docker Compose, Helm, Redis/PostgreSQL production posture, tracing hooks, and signed container releases.
- [x] First demo seed path, operator guide, proof-point demos, timeout status, queue sparkline, MCP descriptor paging, and architecture ADRs.
- [x] Never-published workflow now uses `Publish`; dirty published workflow uses `Publish changes`.
- [x] Community node registry foundation and one-click installation path.

---

## 3. Non-negotiable engineering protocol

Every work item must follow this loop:

1. Add or identify a failing regression/acceptance test.
2. Make the smallest safe change within the item’s declared scope.
3. Update user/operator documentation in the same change.
4. Run targeted tests, lint, typecheck where applicable, and `git diff --check`.
5. Record real verification output in the implementation ledger in §15.
6. Ship behind a feature flag when rollback cannot be completed by reverting one release.
7. Observe canary metrics before expanding rollout.

Global rules:

- No security-sensitive fallback may silently weaken isolation, tenancy, authentication, authorization, encryption, or SSRF controls.
- Every public endpoint must define permission, organization scope, rate limit, input limit, error contract, audit behavior, and pagination.
- Every background loop must define ownership, idempotency, shutdown, retry, jitter, observability, and stuck-work recovery.
- Every new persistent field requires forward/backward migration tests and a downgrade/rollback statement.
- User-visible errors must explain what happened and the next safe action.
- Secrets and node data must pass through the existing redaction boundary before logs, events, audit records, or UI errors.
- UI components require loading, empty, error, retry, disabled, focus, keyboard, reduced-motion, and narrow-layout behavior.
- No task is complete based only on mocked success-path tests.

---

## 4. Program gates and target service levels

| Dimension | GA gate |
|---|---|
| Availability | 99.9% monthly control-plane SLO for the supported reference topology |
| Run durability | Zero lost or duplicate terminal runs in the 24-hour soak and worker-loss chaos suite |
| Queue latency | p95 admission-to-lease <5 seconds at documented reference load |
| API performance | p95 read <250 ms and write <500 ms excluding long-running operations |
| Editor performance | p95 common interaction <100 ms; workflow open <2 seconds for a 500-node reference graph |
| Web delivery | Initial authenticated shell <250 kB gzip; editor route <350 kB gzip excluding on-demand Plotly/Monaco |
| Accessibility | WCAG 2.2 AA; zero axe critical/serious findings on Tier-1 journeys |
| Security | Zero known critical/high dependency findings; threat model and abuse cases reviewed per release |
| Recovery | Verified PostgreSQL + artifact restore with RPO ≤5 min and RTO ≤60 min for reference deployment |
| Test health | Required lanes green five consecutive runs; flake rate <0.2%; no retry-hidden failures |
| Activation | p50 first successful workflow <5 min; p90 <10 min; ≥60% evaluator completion |
| Retention | Define baseline, then improve activated-workspace week-4 retention for three consecutive releases |

---

## 5. Dependency-ordered delivery phases

| Phase | Goal | Exit gate |
|---|---|---|
| **0 — Baseline integrity** | Fix contradictory trust states and make the branch green | P0 tests/lint/typecheck green; no known false readiness/auth state |
| **1 — Failure-proof execution** | Prove engine, queue, sandbox, and restore behavior under failure | Chaos + soak + restore drills pass repeatedly |
| **2 — Activation and onboarding** | Reduce time-to-value and cognitive load | New-user p90 first success ≤10 min |
| **3 — Architecture and scale** | Remove orchestration concentration and enforce performance bounds | Engine/API benchmarks and architecture contracts green |
| **4 — Artifact and data platform** | Make artifacts portable, streamed, verified, and operable | Backend conformance + integrity + lifecycle tests green |
| **5 — Frontend quality** | Reach accessibility, responsiveness, and interaction budgets | Tier-1 UI journeys pass visual/a11y/performance gates |
| **6 — Security and operations GA** | Make deployment, upgrade, recovery, and isolation auditable | Production checklist generates verified evidence bundle |
| **7 — Ecosystem and adoption** | Create repeatable acquisition, migration, and community loops | Template/registry/hosted-evaluation funnels measured |
| **8 — GA and continuous excellence** | Release with explicit support and compatibility commitments | GA scorecard approved and monitored |

---

## 6. Phase 0 — Baseline integrity and trust

### P0-TRUST-01 — Correct readiness semantics

**Status:** Completed in the first tranche

**Files:** `apps/web/src/ReadinessPanel.tsx`, `ReadinessPanel.test.tsx`, `settings.css`; optionally `/ops/runtime-mode` schema in a later API iteration.

**Implementation:** Represent `local`, `production with issues`, and `production ready` as separate states. Local mode must say it is optimized for development and must never use success styling or “Production-ready” copy.

**Acceptance:** Local + empty warnings renders “Local development mode”; only production + zero warnings + replica-safe renders “Production-ready”; insecure override is always visible.

**Rollback:** Frontend-only revert; no persisted data.

### P0-TRUST-02 — Make auth-disabled MCP connections usable

**Status:** Completed in the first tranche

**Files:** `apps/api/app/routers/mcp_connections.py`, `apps/api/tests/test_mcp_connections.py`.

**Implementation:** Remove the redundant strict `current_user` dependency and rely on `require_permission`, which already allows the default single-tenant workspace when auth is disabled and enforces authentication when enabled.

**Acceptance:** Auth-disabled/no-token list returns 200; auth-required/no-token returns 401; insufficient authenticated role returns 403; org scoping remains intact.

**Rollback:** Route dependency revert.

### P0-ENG-01 — Deterministic mixed-failure precedence

**Status:** Completed in the first tranche

**Files:** `packages/core/nodyra/engine/scheduler.py`, `packages/core/tests/test_engine.py`, deadline tests.

**Implementation:** Give `error` and `timed_out` an explicit deterministic precedence and document why. Recommended: explicit node error outranks timeout so a concrete failure is not masked; timeout remains the result when no higher-priority error exists.

**Acceptance:** `error + timed_out` returns the same aggregate in both argument orders and parallel completion orders.

### P0-QUALITY-01 — Restore a clean local quality gate

**Status:** Completed

**Files:** `apps/api/tests/test_sandbox_compose.py`, Vite configuration.

**Implementation:** Fix the import-order failure and remove incorrect global asyncio test markers. Align Vitest/Vite/plugin-react versions so tests no longer emit deprecated `esbuild`/`optimizeDeps.esbuildOptions` configuration warnings.

**Acceptance:** `uv run ruff check .`, web typecheck, affected Vitest, and `git diff --check` pass without deprecation warnings owned by Nodyra config.

### P0-RECENT-01 — Harden MCP descriptor paging

**Status:** Completed

**Implementation:** Add `Workflow.id` as a stable secondary sort, reject invalid/oversized cursors, test concurrent updates, and ensure skipped name collisions do not create false totals or empty terminal pages. Prefer an opaque keyset cursor for large installations.

**Acceptance:** No duplicate/omitted descriptors across a stable dataset; bounded query/materialization; collision case covered.

### P0-RECENT-02 — Make the demo path safe by construction

**Status:** Implemented; container E2E evidence pending CI

**Implementation:** Use generated ephemeral secrets, bind API/web exposure to loopback in the demo override, display a clear non-production banner, add `make demo-down`, and E2E-test two consecutive idempotent seeds plus teardown.

**Acceptance:** Demo is unreachable from non-loopback interfaces by default; no static credential is reused; teardown removes demo resources without touching named production volumes.

### Phase 0 gate

- [x] P0 trust and correctness tests green.
- [x] Ruff, typecheck, targeted web tests, and diff check green.
- [x] No raw 401 shown in the auth-disabled editor.
- [x] Local Settings cannot display production-ready semantics.

---

## 7. Phase 1 — Failure-proof engine, queue, sandbox, and recovery

### ENG-02 — Supervise scheduler workers

**Status:** Implemented and locally verified

Replace ad hoc worker futures around the scheduler queue with structured supervision. A fatal callback, metanode, loop, or unexpected internal exception must cancel the worker group, drain/cancel outstanding work, emit one run-level error, and return without hanging `queue.join()`.

**Acceptance:** Fault-injection tests for event callback, loop expansion, and metanode adapter exceptions complete within two seconds and leave no live tasks.

### ENG-03 — Bound and benchmark graph planning

**Status:** Implemented and locally verified; CI trend history pending

- Replace ready-list `pop(0)`/re-sorting with a deterministic heap or deque strategy.
- Precompute loop-region ownership rather than repeatedly scanning regions.
- Benchmark linear, 10k-node wide, deep, fan-in/fan-out, nested-loop, cancellation, and large-output graphs.
- Add budgets to a non-flaky benchmark lane with trend reporting.

### RUN-01 — End-to-end chaos and soak

**Status:** Harness and nightly lane implemented; container evidence pending CI

Test real PostgreSQL, Redis, API, worker, and execution processes for burst dispatch, worker kill, lease reclaim, retry exhaustion, dead-letter replay, cancellation, API restart, Redis interruption, PostgreSQL transient loss, and graceful drain. Nightly soak: 24 hours or a documented equivalent accelerated workload.

**Acceptance:** No lost/duplicate runs, no orphan processes/containers, queue reconciliation matches run state, and recovery time is measured.

### SBX-01 — Make sandbox tests platform-stable

**Status:** Implemented; Windows CI evidence pending

Diagnose the current fake-container creation timing failures on Windows. Remove wall-clock races, add deterministic readiness signaling, and run the sandbox pool suite on Linux and Windows. Preserve fail-closed behavior.

### SBX-02 — Runtime heartbeat and wedge detection

**Status:** Implemented and locally verified

Add protocol heartbeats between runtime and sandbox/runner host, a bounded no-progress timeout, actionable failure messages, and container/process cleanup assertions.

### OPS-RECOVERY-01 — Automated backup/restore drill

**Status:** Harness and CI drill implemented; live restore evidence pending Docker CI

Create a disposable environment, seed workflows/runs/artifacts/credentials, back up PostgreSQL and object storage, restore into a clean deployment, rotate endpoints safely, and verify checksums plus decryptability. Run nightly or before release.

### Phase 1 gate

- [ ] Required failure scenarios pass five consecutive CI runs.
- [ ] Windows and Linux sandbox suites pass.
- [ ] Restore evidence meets RPO/RTO targets.
- [x] Engine scheduler cannot hang under injected internal exceptions.

---

## 8. Phase 2 — Activation, onboarding, and product clarity

### ONB-01 — One activation journey

Replace overlapping workspace wizard, template modal, editor tour, and canvas starters with one persistent checklist:

1. Choose a credential-free template.
2. Run it with sample data.
3. Inspect node output and artifact.
4. Make one edit and rerun.
5. Publish.
6. Optionally connect a real credential.

The checklist must resume across sessions, be dismissible, and never block expert use.

### ONB-02 — Curated Core catalog

Show a curated Core 30–50 node set by default, organized by user intent. Preserve global search, recently used, favorites, and an explicit “All nodes” view. Load advanced, ML, provider, and community packs progressively.

**Acceptance:** First-time users see no more than eight top-level categories; expert search still reaches every installed node; palette remains responsive with 1,000 manifests.

### ONB-03 — Credential-free templates and public examples

Ship 10–15 deterministic templates using sample data/practice APIs. Each template declares prerequisites, expected output, runtime, permissions, network egress, and artifact behavior. Keep proof-point scripts and templates generated from one source of truth.

### UX-ART-01 — Actionable artifact empty state

Add upload, run-a-template, retention-doc, and supported-format actions. After first artifact creation, guide users to preview, download, query, checksum, lineage, and retention controls.

### UX-STATE-01 — Semantic state contract

Centralize user-facing run, workflow, deployment, environment, queue, and license states. Every state defines label, explanation, color/icon, next actions, terminality, retryability, and accessibility text. Generate frontend types/fixtures from the contract where practical.

### METRIC-01 — Privacy-preserving activation telemetry

Track install completed, first page, template selected, first run, first successful run, first artifact inspected, first publish, first return, and failure reason. Default self-hosted telemetry must be opt-in, documented, inspectable, and free of workflow data/secrets.

### Phase 2 gate

- [ ] Five moderated first-time-user sessions complete the core journey without intervention.
- [ ] p90 first success ≤10 minutes in evaluation telemetry.
- [ ] Credential/template/error drop-off is visible in a funnel report.

---

## 9. Phase 3 — Architecture, API, and scale

### BE-01 — Split run lifecycle orchestration

Decompose `_execute_run_impl` into tested services for admission, execution selection, event collection, persistence/finalization, retry/error dispatch, and cleanup. Model lifecycle transitions explicitly and reject invalid transitions.

**Acceptance:** The coordinator becomes orchestration-only; each service has contract tests; existing run behavior and events remain backward compatible.

### BE-02 — Formal control-plane/execution-plane contracts

Define versioned dispatch envelopes, capabilities, event schemas, heartbeat/lease contracts, compatibility negotiation, idempotency keys, and maximum payload sizes. Generate runner/client fixtures from the contract.

### ENG-04 — Stabilize engine configuration

Replace the growing execution keyword surface with immutable `ExecutionOptions` and explicit adapters/protocols for subworkflows, metanodes, loops, events, artifacts, and isolation. Maintain a compatibility wrapper for one major release.

### ENG-05 — Remove module-cycle pressure

Invert loop/metanode dependencies behind protocols and move shared planning types into a dependency-neutral module. Add an architecture/import-cycle test.

### API-01 — API consistency and generated client coverage

Standardize cursor pagination, errors, request IDs, idempotency, filtering, sort contracts, date formats, and async-operation responses. Publish OpenAPI compatibility diffs and generate typed client smoke tests.

### DATA-01 — Database scale program

Add representative data-volume fixtures, query plans, slow-query logging, keyset pagination, connection-pool dashboards, retention pressure tests, and PostgreSQL-only correctness lanes. Document SQLite as local/lightweight only.

### Phase 3 gate

- [ ] Architecture boundaries are enforced by tests.
- [ ] 500-node editor and 10k-node planner benchmarks meet budgets.
- [ ] OpenAPI compatibility diff contains no unexplained breaking change.

---

## 10. Phase 4 — Artifact and data platform

### ART-01 — Streaming and multipart artifact I/O

Implement bounded-memory upload/download streaming, multipart S3 transfers, cancellation, retry, content-length enforcement, and cleanup of abandoned multipart uploads.

### ART-02 — Backend conformance and portability

Publish the artifact backend protocol and a conformance suite. Add Azure Blob and GCS implementations or officially supported plugins. Test local, S3/MinIO, Azure emulator, and GCS emulator behavior where feasible.

### ART-03 — Integrity and lifecycle health

Verify checksums on ingest and optional download/audit, surface corrupt/missing/orphaned objects, show storage usage and retention forecasts, and alert on failed rehoming/deletion. Add a repair/reconcile command with dry-run.

### ART-04 — Lineage, provenance, and safe querying

Expose producing workflow/version/run/node, schema, size, checksum, retention deadline, encryption/backend, and downstream consumers. Keep dataset querying read-only with explicit row/byte/time limits and audit records.

### ART-05 — Optional deduplication

Evaluate content-addressable storage behind a feature flag. Do not enable until tenant isolation, deletion semantics, encryption boundaries, and cost benefit are proven.

### Phase 4 gate

- [ ] Backend conformance passes for every supported backend.
- [ ] 10 GiB reference transfer stays within documented memory bounds.
- [ ] Reconcile detects injected corruption/orphans and repairs safely.

---

## 11. Phase 5 — Frontend architecture, accessibility, and performance

### FE-01 — Palette and editor performance

Virtualize node lists, cache normalized search indexes, memoize high-cost cards/details, lazy-load advanced editors, and move heavy syntax/plot packages behind user action. Add React profiler fixtures and interaction budgets.

### FE-02 — Bundle and CSS budgets

Generate a used-icon manifest or consolidate icon delivery, split editor/settings CSS by route/feature, remove duplicated raw colors in favor of semantic tokens, and retain Plotly/Monaco as on-demand chunks. Enforce gzip budgets in CI.

### FE-03 — Reduce component complexity

Split `NodeDetails`, credential modal, `NodePalette`, `NodeCard`, and highlighted textarea into state machines/hooks plus focused presentational components. Add Storybook-like fixture routes or equivalent component harnesses for states.

### A11Y-01 — WCAG 2.2 AA Tier-1 certification

Automate axe checks for onboarding, workflow creation/run/publish, credentials, executions, artifacts, runners, settings, and error recovery. Add keyboard-only and screen-reader smoke scripts; verify contrast in both themes and high-contrast mode.

### RESP-01 — Explicit device contract

Support desktop authoring, tablet inspection/light editing, and phone monitoring/trigger/approval. Do not force the full canvas onto a phone. Test at defined widths, zoom 200%, landscape, touch, and long localized strings.

### MOTION-01 — Interaction and motion cleanup

Remove bounce easing and layout-property transitions, preserve reduced-motion alternatives, and replace decorative side accents/custom scrollbars where they reinvent standard affordances.

### I18N-01 — Localization readiness

Extract user-facing strings, support pluralization/date/number/time-zone formatting, pseudo-localize, and test 30–50% text expansion before selecting initial locales.

### Phase 5 gate

- [ ] Zero serious/critical axe findings on Tier-1 journeys.
- [ ] Keyboard-only journey succeeds end to end.
- [ ] Bundle and interaction budgets enforced in CI.
- [ ] Supported viewport matrix passes visual regression.

---

## 12. Phase 6 — Security, deployment, and operations GA

### SEC-01 — Production posture attestation

Extend readiness from warnings to structured checks with severity, evidence, remediation, last verified time, and machine-readable pass/fail. Include auth, CORS, secrets, database, Redis, artifact backend, TLS/proxy assumptions, sandbox, backups, KMS, tracing, replicas, queue, webhooks, and license posture.

### SEC-02 — Sandbox as a safe evaluation default

Include at least one sandboxed execution path in Community. Do not make safe Python evaluation a paid prerequisite. Monetize governance, HA, enterprise identity, retention, policy, support, and managed operation.

### SEC-03 — Supply chain and plugin trust

Require signed release artifacts, SBOM and provenance, dependency policy, community-package signatures, permission/egress manifests, vulnerability status, compatibility ranges, and quarantine/revocation. Existing Cosign signing remains required.

### DEP-01 — Compose and Helm end-to-end smoke

On every release candidate, build images, migrate a fresh database, run a workflow through API+worker, upload/download an artifact, scrape metrics, exercise readiness, upgrade from the previous supported version, and tear down cleanly.

### OPS-01 — Observability productization

Persist queue/run/worker time-series metrics in Prometheus-compatible form. The current browser-only queue sparkline remains a convenience, while dashboards and alerts use durable metrics. Add golden signals, SLO burn alerts, run/trace correlation, capacity forecasts, and actionable runbooks.

### OPS-02 — Upgrade, rollback, and compatibility matrix

Document supported PostgreSQL/Redis/object-store/Python/browser/Kubernetes versions, N-1 upgrade path, rollback limitations, schema forward compatibility, runner/client compatibility, and data export escape hatch.

### OPS-03 — Operational evidence bundle

Provide one command that outputs redacted configuration posture, migrations, dependency versions, health, queue state, storage checks, backup age, license, and recent errors for support/security review.

### Phase 6 gate

- [ ] Reference deployment passes production attestation with no override.
- [ ] Upgrade from N-1 and restore drill pass.
- [ ] Threat model and penetration test findings have no unresolved high severity.

---

## 13. Phase 7 — Ecosystem, licensing, and adoption

### ECO-01 — First-class community registry

Surface search, install, upgrade, rollback, trust status, permissions, maintainer, compatibility, downloads, health, examples, and security advisories in-product. Version providers independently from core, with lifecycle states and deprecation policy.

### ECO-02 — Public workflow template gallery

Create searchable, versioned, importable templates with screenshots, expected results, prerequisites, trust metadata, creator attribution, verification badges, ratings, and automated compatibility tests. Connect the gallery to the in-product activation journey.

### MIG-01 — Migration and import paths

Deliver supported imports for Python scripts and common n8n/Prefect/Airflow patterns. Produce a compatibility report that clearly marks exact, transformed, manual, and unsupported behavior before import.

### GTM-01 — Hosted evaluation

Offer a disposable hosted sandbox or managed control plane so evaluators can experience value without operating PostgreSQL, Redis, object storage, and workers first. Set strict quotas, egress policy, abuse controls, deletion guarantees, and cost budgets.

### GTM-02 — Positioning and proof

Focus public messaging on inspectable Python-native internal automation. Lead with concrete operational proof: deterministic execution, artifacts, isolation, versioned releases, debugging, GitOps, MCP, and self-hosted data control—not raw node count.

### DOC-01 — Learning and adoption content

Maintain a five-minute quickstart, architecture chooser, production checklist, troubleshooting decision tree, short videos/GIFs, copy-paste recipes, academy-style exercises with practice APIs, and public release/compatibility notes.

### LIC-01 — Adoption-aligned Community tier

Test a more generous Community posture: at least five seats, ten active deployments or unlimited local drafts, one sandboxed runner, and basic observability. Keep enterprise value in SSO/SCIM, multi-tenancy, policy/governance, HA, advanced audit retention, KMS, support, and managed services. Validate changes with conversion and retention data rather than intuition.

### Phase 7 gate

- [ ] Hosted evaluation and self-hosted quickstart both reach the activation target.
- [ ] Registry/template supply, install success, and retention are measured.
- [ ] Pricing experiment has documented adoption and conversion results.

---

## 14. Phase 8 — Release maturity and continuous excellence

### REL-01 — SemVer and support policy

Move beyond `0.0.1` with explicit alpha/beta/RC/GA criteria, public changelog, deprecation window, supported-version policy, security support policy, and LTS decision. Keep package, API, runner, runtime, and web versions aligned or publish their compatibility matrix.

### REL-02 — Release evidence

Every release must attach test summaries, migration/upgrade evidence, SBOMs, signatures/provenance, vulnerability scan, bundle/benchmark deltas, known issues, rollback notes, and documentation version.

### REL-03 — Canary and staged rollout

Use internal/demo, pilot, canary, and broad stages with automatic rollback thresholds for error rate, queue latency, worker churn, artifact failures, and UI/API regressions.

### GOV-01 — Product scorecard review

Review the scorecard monthly. A category reaches 10/10 only when its automated gates, operational evidence, discoverability, failure recovery, and user outcome metrics are all met. Reopen scores when evidence regresses.

---

## 15. Implementation ledger

Append one row per completed change. Never record expected results as actual results.

| Date | Item | Commit/PR | Verification | Result | Rollout notes |
|---|---|---|---|---|---|
| 2026-07-13 | Recent-change re-baseline | `093d5e34` baseline | Focused Python + web suites | 213 passed/3 skipped; 14 passed | Four follow-ups recorded in §2.2 |
| 2026-07-13 | P0-TRUST-01 | Working tree | Readiness Vitest + typecheck | 10 affected web tests passed; typecheck passed | Local mode is neutral and cannot claim production readiness |
| 2026-07-13 | P0-TRUST-02 | Working tree | MCP connection integration tests | Auth-disabled 200 and auth-required 401 covered | Redundant strict dependency removed from all connection routes |
| 2026-07-13 | P0-ENG-01 | Working tree | Engine + deadline tests | Mixed failure precedence passes in both orders | Explicit errors outrank concurrent timeout status |
| 2026-07-13 | P0-QUALITY-01 | Working tree | Full Vitest, typecheck, build, audit, Ruff, diff check | Web 486 passed/1 skipped; typecheck/build passed; audit 0; Ruff/diff passed | Vite/Vitest/plugin upgraded; worker cap prevents Windows resource exhaustion |
| 2026-07-13 | P0-RECENT-01 | Working tree | MCP paging and connection suites | Keyset cursor v2, strict cursor rejection, stable collision paging passed | Legacy cursor remains readable for rolling compatibility |
| 2026-07-13 | P0-RECENT-02 | Working tree | Demo generator tests and Compose config render | Ephemeral env generation, loopback ports, idempotent seed contract, and teardown target passed | Live Compose E2E runs in CI because local Docker daemon is unavailable |
| 2026-07-13 | ENG-02 | Working tree | Scheduler supervision and engine fault injection | Fatal worker/callback failures terminate without leaked tasks; full suite green | Structured cancellation observes every scheduler task |
| 2026-07-13 | ENG-03 | Working tree | 10k-node planning benchmark and unit tests | Heap planning and pre-indexed loop ownership remain inside CI budget | Benchmark lane records JSON trend evidence |
| 2026-07-13 | SBX-01 / SBX-02 | Working tree | Runtime, sandbox-pool, container-runtime, and server tests | Heartbeat, total deadline, no-progress, cleanup, and callback-drain cases passed | Linux/Windows required lanes configured; Windows result pending CI |
| 2026-07-13 | RUN-01 | Working tree | Chaos/soak harness tests | API/worker restart, Redis/Postgres pause, drain, retry, duplicate/loss checks covered | Nightly Compose run uploads measured JSON evidence |
| 2026-07-13 | OPS-RECOVERY-01 | Working tree | Recovery fixture tests and CI workflow validation | Seed/copy/restore/verify contract, checksums, credential decryptability, redacted evidence passed | Live pg_dump/object restore pending Docker CI evidence |
| 2026-07-13 | OPS metrics freshness | Working tree | Hot-cache metrics regression + reordered subset | 63 passed; live safety counters visible on every scrape | Database gauges retain 30-second cache; process counters never do |
| 2026-07-13 | Integrated repository gate | Working tree | `uv run pytest -q`; `uv run ruff check .`; `git diff --check` | 3,210 passed/98 skipped/0 failed; Ruff and diff clean | 13 third-party/platform deprecation/resource warnings recorded for later cleanup |

---

## 16. Immediate execution order

The production-critical implementation tranche is complete. The next release
sequence is evidence-first:

1. Run the required Linux and Windows lanes on the branch.
2. Run the Compose chaos/soak and recovery jobs with artifact upload enabled.
3. Repeat every required lane for five consecutive commits/reruns with no hidden retries.
4. Review recovery RPO/RTO, duplicate/loss counters, benchmark deltas, and sandbox cleanup evidence.
5. Only after those gates pass, advance Phase 1 to certified and begin the Phase 2 activation rollout.
