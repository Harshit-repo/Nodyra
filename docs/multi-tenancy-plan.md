# Nodyra Multi-Tenancy Plan

> Status: design + phased migration plan. Not yet implemented.
>
> Scope: what it takes to turn Nodyra from a single-tenant, trusted-author
> deployment into a multi-tenant system — covering both *soft* multi-tenancy
> (multiple trusted teams in one instance) and *hard* multi-tenancy (untrusted
> users on shared infra, i.e. a SaaS). Every recommendation is grounded in the
> current code: `apps/api/app/models.py`, `security.py`,
> `services/runtime_pool.py`, `services/queue.py`, `services/crypto.py`,
> `services/remote_dispatch.py`.
>
> Honesty note: this is a re-architecture, not a feature. The current schema has
> **no tenancy dimension at all** — every tenant-owned table is flat and global.
> Threading a tenant boundary through the data layer, auth, execution, secrets,
> storage, and scheduling is multi-month work.
>
> **Decided scope (this revision):** build the **full A–C foundation now, with
> RLS, per-org KEK (E), and storage isolation (F) pulled forward** — not the
> trimmed "soft-only" subset. The goal is that **Phase D (untrusted execution
> isolation) bolts onto an already-hardened data/secret/storage substrate**
> rather than forcing a retrofit across every table under launch pressure. D and
> per-`(org,env)` warm pools remain deferred until the cloud product is funded,
> but everything D depends on (DB-enforced isolation, per-tenant keys, namespaced
> storage, compute metering) ships in the A–C+E+F bundle. A **mandatory Postgres
> CI lane** is part of this scope from commit one — SQLite cannot exercise RLS,
> so isolation is unproven without it.

## 0. Two definitions — decide which you are building

| | Soft multi-tenancy (workspaces) | Hard multi-tenancy (SaaS) |
|---|---|---|
| Tenants | Multiple **trusted** teams in one self-hosted instance | **Untrusted** strangers run arbitrary Python on shared infra |
| Trust boundary | Per team, data isolation only | Per run, full execution sandbox |
| Phases required | A, B, C | A, B, C, D, E, F, G |
| Performance cost | Low | High — erodes the warm-pool wedge |
| Effort | Bounded (months) | Large + new infra discipline (the wall) |
| Monetization | Open-core enterprise feature | Managed cloud product |

**Recommendation (decided):** build the **A–C + E + F bundle now**, with RLS and
per-org KEK from day one, and a **Postgres test lane** proving isolation. This is
a legitimate paid enterprise feature for self-hosted teams *and* it lays a
hardened foundation so the cloud product (Phase D) is an additive bolt-on, not a
risky retrofit. Only **Phase D** (the execution sandbox + per-`(org,env)` warm
pools) and **Phase G** abuse/billing operations are deferred until the cloud
build is funded — and even those depend only on substrate that A–C+E+F already
build.

> Why pull E and F forward (they were "later" in earlier drafts): per-org KEK and
> storage namespacing are **additive to the `org_id` columns A introduces and
> non-disruptive to add now, but expensive and dangerous to retrofit** once real
> tenant secrets/artifacts exist. Doing them while the data is still
> single-"default"-org means no re-encryption sweep and no artifact key
> migration later. That is the whole point of "strong back for D."

## 1. Current-state inventory (what makes this hard)

Confirmed from the code:

- **No tenant column anywhere.** `Workflow`, `Credential`, `Environment`, `Run`,
  `Deployment`, `CodeModule`, `PinnedData`, `RunnerPool`, `WorkflowVersion`,
  `RunQueueEntry`, `AuditEvent` are all global (`models.py`).
- **Single global identity.** `User.role` is one global RBAC role
  (`models.py:218`); `security.py` resolves `Bearer token → User → role` with no
  org/membership concept. `_PERMISSION_MIN_ROLE` is instance-wide.
- **One singleton settings row.** `SystemSetting(id="singleton")` holds
  instance-wide concurrency/retention/caps (`models.py:659`).
- **One global run queue.** `RunQueueEntry` is leased FIFO-by-priority across the
  whole instance (`models.py:696`, `services/queue.py`) — no per-tenant fairness.
- **One shared warm pool.** `runtime_pool.py` keys long-lived `nodyra_runtime`
  subprocesses by `environment_id` only. Arbitrary tenant Python runs in the host
  trust boundary. Sub-workflows run **in-process on the host with no env
  isolation** (`runtime_pool.py` module docstring) — a cross-tenant hole.
- **One master KEK.** Credentials use a per-credential DEK wrapped by a single
  master key (`Credential.encrypted_dek`, `services/crypto.py`). Good primitive,
  but one key for all tenants.
- **Isolation seam already exists.** `RunnerPool.provider` supports `docker` and
  `kubernetes` (`models.py:65-92`) and `remote_dispatch` can route runs to them.
  This is the intended hook for execution isolation — not starting from zero.
- **Two new amplification vectors since this plan was first drafted: map & loop.**
  - **Map nodes** (`map_dataset`, `map_items` — `packages/nodes/nodyra_nodes/datasets.py`,
    `_map.py`) call a **child workflow once per row** via `workflow_caller`
    (the sub-workflow path), up to `concurrency` at a time (default 5) and
    `max_rows` rows (default 10000). A single map node can therefore spawn
    thousands of child executions through the exact in-process sub-workflow
    seam called out above — it is the existing cross-tenant hole, amplified.
  - **Loop nodes** (`loop_start`/`loop_end` — `builtin.py`, driven by the core
    engine's loop driver in `packages/core/nodyra/engine.py`) run the loop body
    **inside one engine execution, in the same `nodyra_runtime` subprocess** —
    no host callback, no subprocess-per-row. They are *safe-by-construction*
    for execution isolation (they inherit the parent run's boundary) but they
    amplify work **inside a single run**: a 10k-row loop with an N-node body
    writes one `NodeRun` per `(node_id, iteration_path)`, and nested loops
    multiply in-flight iterations (`C_outer × C_inner`). A run-count quota
    cannot see any of this.

  Net effect on this plan: map drives **Phase C fan-out fairness** and **Phase D
  sub-workflow isolation**; loop drives **Phase A backfill volume**, **Phase C
  per-run (not per-run-count) metering**, and **Phase F storage**.

## 2. Phase A — Data isolation (foundation, unavoidable)

Goal: every tenant-owned row carries a tenant id and **no query can return
another tenant's rows**.

### A1. New tables

```text
organizations         id, name, slug, plan, status, created_at
memberships           id, org_id, user_id, role, created_at   (unique org_id+user_id)
```

`User` becomes global identity only (email, password_hash, name). Role moves
**off** `User` and onto `Membership` (a user can be admin in org X, viewer in
org Y). Keep `User.role` temporarily for migration, then drop it.

### A2. Add `tenant_id` (org_id) to every tenant-owned table

Add a non-null `org_id String(32) ForeignKey("organizations.id", ondelete="CASCADE")`,
indexed, to: `workflows`, `credentials`, `environments`, `deployments`,
`code_modules`, `pinned_data`, `runner_pools`, `workflow_versions`, `runs`,
`run_batches`, `run_queue`, `provider_trigger_subscriptions`, `schedule_state`,
`audit_events`. Child tables (`node_runs`, `run_events`, `run_approvals`,
`artifacts`) inherit the tenant via their parent `run` — do **not** denormalize
unless a query needs it without the join.

`SystemSetting` changes from a singleton to **per-org**: `id` becomes `org_id`
(PK). Instance-wide infra knobs (e.g. `RUNTIME_MODE`, queue backend) stay in
`config.py` env vars; per-tenant policy (concurrency cap, retention, output
caps, map-width / loop-iteration caps, **per-org KEK reference (E)**, storage
bytes quota (F)) moves onto the per-org settings row.

### A3. Enforcement — do NOT hand-write `WHERE org_id=` everywhere

Hand-scoping every query is how cross-tenant leaks happen. Pick one of:

- **Layer 1 — Postgres Row-Level Security (the hard backstop, DECIDED).** Per
  request, `SET LOCAL app.current_org = :org_id`; RLS policies on every tenant
  table enforce `org_id = current_setting('app.current_org')`. The database
  refuses to leak even if application code forgets a filter. SQLite (dev/tests)
  doesn't support RLS — so RLS is dialect-gated and the **Postgres CI lane (A6)
  is what proves it works**; never trust the SQLite suite to catch a missed
  scope.
- **Layer 2 — SQLAlchemy session scoping (ergonomics + the SQLite path).** A
  request-scoped `org_id` plus a global `with_loader_criteria` applied to every
  model that has `org_id`. Works on SQLite, but a raw `session.get(Model, id)` or
  a `text()` query bypasses it — which is exactly why Layer 1 exists underneath.

**Decision: do both, from day one.** RLS as the DB-enforced backstop (the
foundation Phase D's untrusted execution will rely on) and session scoping for
ergonomics on every backend. This is non-negotiable in the decided scope — it is
the single property that makes the later hard-MT bolt-on safe instead of a
re-audit of every query.

### A4. Request context (routing DECIDED: header)

Add an `org_id` to the auth context. **Decision: `X-Org-Id` header**, validated
against the caller's memberships — least router churn, and subdomain/path can be
layered on later without schema changes. The dependency that resolves it must
verify the user is a member of that org **before any data access**, and set the
request-scoped `org_id` ContextVar that both enforcement layers read.

### A5. Migration (backfill)

One Alembic migration: create `organizations` + `memberships`, insert a single
`"default"` org, backfill `org_id = "default"` on every table, add the FKs/RLS
last. **Watch the revision-id ≤32-char limit** (known Postgres-upgrade crash;
SQLite tests won't catch it). Backfill must be batched on large `runs` tables.

> **Loop-node impact on backfill volume.** `node_runs` is now the largest tenant
> table by row count, not `runs`: the loop driver persists one `NodeRun` per
> `(node_id, iteration_path)` (see the `iteration_path` column + composite index
> added for loops). A handful of looped workflows can dwarf the `runs` row count,
> so the batched backfill **must** target `node_runs` (and `run_events`) first,
> not just `runs`. The same ≤32-char revision-id footgun already bit the
> `iteration_path` migration — reuse that discipline here.

### A6. Mandatory Postgres test lane (non-negotiable in the decided scope)

RLS is invisible to the SQLite suite, so isolation is **unproven** without a
Postgres lane. Required from commit one:

- **CI service:** a Postgres container in CI; a `pytest` marker (e.g.
  `@pytest.mark.postgres`) for tenancy tests that must run there.
- **Negative isolation test:** set `app.current_org = A`, attempt to read/update/
  delete org-B rows via **raw SQL** (bypassing session scoping) — assert empty /
  refused. This is the test that proves RLS, not the ORM.
- **Session-scoping test (all backends):** set org A in the ContextVar, query via
  the ORM, assert zero org-B rows — runs on both SQLite and Postgres.
- **Migration test on Postgres:** the A-migration (and the per-org KEK / storage
  migrations from E/F) apply cleanly on Postgres, incl. RLS enable + the
  ≤32-char revision-id check (which SQLite can't catch).
- **`SET LOCAL` scoping test:** confirm the GUC is reset between requests/sessions
  so a pooled connection can't leak a previous request's org.
- **Flag-off parity:** with `multi_tenancy_enabled=false`, the full existing
  suite passes unchanged.

## 3. Phase B — Identity & auth

Building on A1:

- Replace global-role checks in `security.py`: `require_permission` resolves the
  caller's role **within the request's org** via `Membership`, not `User.role`.
- Org-scoped invitation flow (invite by email into an org with a role).
- Org switching in the UI; "owner of org" vs the old instance-owner concept.
- **SSO/SAML/OIDC** — effectively table stakes for the B2B buyers who pay for
  multi-tenant/enterprise. Current auth is single-realm local; this is a
  meaningful add (per-org IdP config).
- Service tokens / API keys become org-scoped.

## 4. Phase C — Resource governance & fair scheduling

Today one noisy tenant can monopolize the global queue and warm pool.

- **Per-org quotas** on the per-org settings row: max concurrent runs,
  executions/day, env count, storage bytes, max artifact size.
- **Fair scheduling in `services/queue.py`.** The lease query currently orders by
  `(priority DESC, available_at ASC)` globally. Add tenant fairness — e.g.
  per-org concurrency accounting in the lease predicate, or weighted round-robin
  across orgs — so one tenant's backlog can't starve others. The
  `ix_run_queue_lease` index will need to account for `org_id`.
- **Per-org metering** (run counts, compute seconds) — needed for billing and
  abuse detection. Emit from the queue completion path.
- Surface quota state in the backpressure UI (extend `queue_reason` with
  `org_quota_exceeded`).

### C-amp. Amplification governance — map fan-out vs loop intra-run work

The queue and quota model must police **two different amplification shapes**, or
one tenant slips past run-count limits:

1. **Map fan-out (new child runs).** Each map row becomes a child workflow call
   through `workflow_caller`. With `concurrency=5` (default) and `max_rows=10000`
   a single node can enqueue/dispatch thousands of child executions on one
   tenant's behalf. Governance:
   - Count child runs against the **same per-org concurrent-run and
     executions/day quotas** as top-level runs — a child run is a run.
   - Cap **total in-flight child runs per org** (the `subworkflow_slot`
     soft throttle in `runtime_pool.py` is global today — it must become
     per-org, or fan-out from one tenant starves the shared spawn budget).
   - Cap **map width per org** (max rows a single map may fan out) on the
     per-org settings row; reject above it rather than silently dispatching.
   - Fair scheduling must treat a map's children as belonging to the parent's
     org so a wide map can't jump the queue ahead of other tenants.
2. **Loop intra-run work (no new runs).** A loop does all its work **inside one
   run** — the run-count and concurrent-run quotas never fire. Governance moves
   to the run itself:
   - **Per-run compute metering** (wall-clock + CPU seconds), not just a run
     count, emitted from the completion path — otherwise a 10k-iteration loop
     is billed and rate-limited as "one run".
   - **Per-org caps on loop size** (max iterations) and **max total in-flight
     iterations** (bounds nested `C_outer × C_inner`), enforced by the engine's
     loop driver against the per-org settings row.
   - **Per-run `NodeRun` row caps** tie into storage metering (Phase F): a loop
     can emit tens of thousands of `node_runs` rows per run.

## 5. Phase D — Execution isolation (the wall) 🚨

This is what decides whether *hard* multi-tenancy is possible. It is also where
the warm-pool performance wedge and the multi-tenant requirement directly
conflict.

### The conflict

`runtime_pool.py` runs arbitrary tenant Python in **long-lived host-trust
subprocesses keyed only by `environment_id`**. That is fast (warm imports, warm
model/DB handles) and is the product's differentiator — and it is exactly what
cannot be shared across untrusted tenants. Disposable per-run isolation removes
the warm-pool benefit (cold start every run).

### What untrusted execution requires

1. **Disposable per-run isolation** via the existing `RunnerPool` `docker` /
   `kubernetes` seam (`models.py:65-92`, `remote_dispatch`): container-per-run,
   gVisor, Firecracker microVMs, or K8s Jobs with a restrictive PodSecurity /
   seccomp profile. No host mounts, dropped capabilities, read-only rootfs.
2. **Resource limits** per run: CPU, memory, PIDs, wall-clock, disk.
3. **Egress control / network policy.** A tenant's code must not reach: other
   tenants' runners, the Nodyra DB/Redis, the host metadata endpoint
   (`169.254.169.254`), or internal services. SSRF controls on HTTP nodes too.
4. **Close the sub-workflow hole — and note map rides directly on it.**
   Sub-workflows currently execute in-process on the host (`runtime_pool.py`
   docstring) — under hard multi-tenancy they must run in the same isolated
   boundary as their parent. **Map nodes** (`map_dataset`, `map_items`) are the
   highest-volume consumer of this seam: every mapped row is a child call via
   `workflow_caller`, so closing the hole automatically isolates map, but it
   also means map's fan-out (thousands of children) hits the sandbox spawn path
   the hardest. Per-tenant child-run isolation must scale to map width, and the
   per-org `subworkflow_slot` cap from Phase C-amp is what keeps one map from
   exhausting a tenant's sandbox budget.
   **Loop nodes need no work here:** the loop driver runs the body inside the
   parent run's own engine/subprocess (no host callback, no subprocess-per-row),
   so loops inherit the parent's isolation boundary by construction. The only
   loop concern under hard MT is bounding nested-loop concurrency
   (`C_outer × C_inner`) so a single sandboxed run can't exhaust its own CPU/PID
   limits — covered by the per-run resource limits in item 2 plus the loop caps
   in Phase C-amp.
5. **Filesystem + secret isolation** per run (no shared `ENVS_DIR` / `ARTIFACTS_DIR`
   reachable across tenants).

### Keeping some of the wedge: per-tenant warm pools

To avoid losing warm-pool performance entirely, isolate **between** tenants but
stay warm **within** a tenant: dedicate a warm pool (or a warm sandbox) per
`(org_id, environment_id)`. This preserves warm imports for a given tenant while
keeping tenants apart — at the cost of higher idle infra per tenant (more RAM,
more processes). This is the realistic middle path, and it changes the pool key
in `runtime_pool.py` from `environment_id` to `(org_id, environment_id)`.

> Honest call: Phase D is a different infrastructure discipline (sandbox
> hardening, egress policy, per-tenant pools) and is where funded competitors
> spent person-years. Defer it to the funded cloud build. The payoff of the
> decided scope is that A–C + E + F already deliver the substrate D needs \u2014
> DB-enforced (RLS) isolation, per-org KEKs, namespaced storage, and compute
> metering \u2014 so D becomes an **additive** sandbox layer rather than a retrofit
> across the whole data and secret layer.

## 6. Phase E — Secrets isolation (IN SCOPE NOW — do it before real tenant secrets exist)

> **Why now, not later:** re-wrapping every existing DEK under a new per-org KEK
> is a live-data re-encryption sweep. Doing it while everything is still the
> single `"default"` org is a no-op migration; doing it after tenants have real
> secrets is a risky, audited operation. Pull it forward.

- Current: per-credential DEK wrapped by a single master KEK
  (`services/crypto.py`). Good envelope design.
- Add a **per-org KEK** (each tenant's DEKs wrapped by that org's KEK), ideally
  backed by a real KMS / Vault rather than a single env-var master key. A
  master-key compromise or one tenant's bug must not expose another's secrets.
  Keep a pluggable KEK provider so self-hosted can use an env/file master key
  while cloud uses KMS/Vault — same envelope, different backend.
- Per-org KEK reference lives on the per-org settings row / `organizations`.
- KEK rotation per org (rewrap that org's DEKs only).
- **This is the secret half of "strong back for D":** untrusted execution must
  never let one tenant's run reach another's key material — per-org KEK is the
  precondition.

## 7. Phase F — Storage isolation (IN SCOPE NOW — namespacing is cheap before data piles up)

> **Why now, not later:** prefixing `storage_key` with `org_id/` is free on an
> empty/single-org store and a bulk object-rename migration once artifacts exist.
> Establish the namespace before tenants generate real artifacts.

- Artifact backends (`services/artifact_backends.py`, `s3_artifact_backend.py`)
  must namespace keys by org: prefix every `storage_key` with `org_id/` (and
  optionally per-org buckets for the strongest isolation).
- Signed/download URLs scoped so they cannot be guessed/replayed across tenants.
- Per-org retention and storage quotas (ties into Phase C).
- **Map & loop amplify per-tenant storage** — size quotas, not just counts:
  - A **loop** with `output_mode=dataset` (and each mapped row when
    `map_dataset` writes a dataset) reserves Parquet artifacts via
    `reserve_artifact_path`; a wide loop/map can write thousands of artifacts
    and intermediate datasets under one org in one run.
  - **`node_runs` row growth** from per-iteration persistence is a storage cost
    too — per-org retention must prune iteration rows, and the storage-bytes
    quota should count both artifact bytes and run/iteration metadata.

## 8. Phase G — Operational

- Per-org audit log (`AuditEvent` gains `org_id`; `routers/audit.py` scopes by
  org).
- Per-org observability / metrics labels; noisy-neighbor alerting.
- Abuse handling: suspend an org, freeze its runs, revoke tokens.
- Billing/metering integration consuming Phase C meters.

## 9. Recommended sequencing

> Decided build order: the **A–C + E + F bundle** ships as the foundation, with
> RLS and a Postgres test lane from commit one. Only **D** (execution sandbox +
> per-`(org,env)` warm pools) and the billing/abuse parts of **G** are deferred
> to the funded cloud build — and they depend only on substrate this bundle
> already lays down.

1. **Phase A** behind a `multi_tenancy_enabled` flag. Enforcement = **RLS
   (Postgres) + session scoping (all backends)** from day one (A3), header
   routing (A4), and the **mandatory Postgres CI lane (A6)** landing with the
   first tenancy code — not bolted on later. Single `"default"` org until B.
2. **Phase E + F early — while data is still single-`"default"`-org.** Per-org KEK
   and `org_id/` storage namespacing are no-op migrations now and live-data
   re-encryption / object-rename sweeps later. Do them right after A's columns
   exist, **before** B opens real multi-team usage.
3. **Phase B** — memberships, org-scoped RBAC, invitations, org switching.
   (A + E + F + B = workspaces teams can actually set up and use.)
4. **Phase C** — quotas, fair scheduling, metering. Include **C-amp**:
   compute/iteration metering (not just run counts), a **per-org**
   `subworkflow_slot` cap, and per-org caps on map width / loop iterations /
   in-flight child runs. (A–C + E + F = sellable, hardened self-hosted
   multi-tenancy with a strong back for D.)
5. **Ship & validate.** Real multi-team self-hosted usage on the hardened base.
6. **Phase D** — funded cloud build only. Because A–C+E+F already give
   DB-enforced isolation, per-org keys, and namespaced storage, D is **additive**:
   the `docker`/`kubernetes` runner-pool isolation path + per-`(org, env)` warm
   pools, then egress hardening, then close the sub-workflow hole (which also
   isolates map). Loops need no Phase-D work — they already run inside the
   parent's boundary.
7. **Phase G** throughout (per-org audit/observability now; billing/abuse with D).

## 10. Risks & honest caveats

- **Cross-tenant leak risk is highest in Phase A.** One missed scope = data
  breach. This is why RLS (DB-enforced) is strongly preferred over hand-written
  filters for any untrusted use.
- **The warm-pool wedge and hard multi-tenancy fight each other.** You can keep
  some warmth with per-tenant pools, but you pay in idle infra per tenant.
- **Effort is dominated by Phase A's blast radius** (touches every table, query,
  and service) and Phase D's new infra discipline. B, C, E, F, G are bounded.
- **Single-maintainer reality:** A–C + E + F is a focused multi-month project; D
  is the kind of thing that stalls solo efforts. The decided scope deliberately
  front-loads the *additive-but-painful-to-retrofit* pieces (RLS, per-org KEK,
  storage namespacing) so D, when funded, is a clean bolt-on rather than a
  re-audit of every query, a re-encryption sweep, and an artifact re-key.
- **RLS is unprovable on SQLite.** The Postgres CI lane (A6) is the only thing
  that demonstrates the DB-enforced backstop actually works; treat a green
  SQLite-only suite as *not* having tested isolation.
- **Migration constraint:** Alembic revision ids must be ≤32 chars (Postgres
  upgrade crashes otherwise; SQLite tests don't catch it). Backfill migrations on
  `runs`/`node_runs` must be batched — and `node_runs` is now the dominant table
  thanks to per-iteration loop rows, so size the backfill around it.
- **Run-count quotas are insufficient on their own.** Map fans out into many
  child runs (visible to run-count quotas) while loop does heavy work inside a
  single run (invisible to them). Phase C must meter compute/iterations, not
  just runs, or a loop-heavy tenant evades governance entirely.
- **The `subworkflow_slot` throttle is global today.** Until it is made per-org
  (Phase C-amp), one tenant's wide map can exhaust the shared sub-workflow spawn
  budget and degrade every tenant — a noisy-neighbor hole specific to map.

## 11. Decision checklist before starting

Decisions locked for this revision (✓) and still-open gates (□):

- [x] **Scope:** full **A–C + E + F** foundation now (RLS, per-org KEK, storage
      namespacing); **D** + billing/abuse-G deferred to the funded cloud build.
- [x] **Enforcement:** **RLS + session scoping**, both, from day one (A3).
- [x] **Tenant routing:** **`X-Org-Id` header**, validated against memberships (A4).
- [x] **Postgres CI lane** mandatory from commit one (A6) — isolation unproven
      without it.
- [x] **E + F pulled forward** while data is single-`"default"`-org to avoid
      re-encryption / object-rename sweeps later.
- [x] **Quota model** meters compute/iterations, not just run counts (C-amp), so
      loop intra-run work and map fan-out are both governed.
- [x] **`subworkflow_slot` made per-org** as part of C-amp, before map is exposed
      to multiple tenants.
- [ ] License resolved (blocks any release; AGPL vs BSL — see name/license notes)?
- [ ] Is there demand/funding that justifies starting **Phase D**, or stop at the
      hardened self-hosted bundle for now?
