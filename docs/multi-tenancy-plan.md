# Noodle Multi-Tenancy Plan

> Status: design + phased migration plan. Not yet implemented.
>
> Scope: what it takes to turn Noodle from a single-tenant, trusted-author
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
> storage, and scheduling is multi-month work. Phases A–C are bounded and
> sellable on their own; Phase D (untrusted execution isolation) is the real
> wall and is deliberately deferred.

## 0. Two definitions — decide which you are building

| | Soft multi-tenancy (workspaces) | Hard multi-tenancy (SaaS) |
|---|---|---|
| Tenants | Multiple **trusted** teams in one self-hosted instance | **Untrusted** strangers run arbitrary Python on shared infra |
| Trust boundary | Per team, data isolation only | Per run, full execution sandbox |
| Phases required | A, B, C | A, B, C, D, E, F, G |
| Performance cost | Low | High — erodes the warm-pool wedge |
| Effort | Bounded (months) | Large + new infra discipline (the wall) |
| Monetization | Open-core enterprise feature | Managed cloud product |

**Recommendation:** build A–C first (workspaces). It is a legitimate paid
enterprise feature, and it is the prerequisite for everything else, so none of
the work is wasted. Defer D until there is revenue, users, or funding that
justifies it.

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
- **One shared warm pool.** `runtime_pool.py` keys long-lived `noodle_runtime`
  subprocesses by `environment_id` only. Arbitrary tenant Python runs in the host
  trust boundary. Sub-workflows run **in-process on the host with no env
  isolation** (`runtime_pool.py` module docstring) — a cross-tenant hole.
- **One master KEK.** Credentials use a per-credential DEK wrapped by a single
  master key (`Credential.encrypted_dek`, `services/crypto.py`). Good primitive,
  but one key for all tenants.
- **Isolation seam already exists.** `RunnerPool.provider` supports `docker` and
  `kubernetes` (`models.py:65-92`) and `remote_dispatch` can route runs to them.
  This is the intended hook for execution isolation — not starting from zero.

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
caps) moves onto the per-org settings row.

### A3. Enforcement — do NOT hand-write `WHERE org_id=` everywhere

Hand-scoping every query is how cross-tenant leaks happen. Pick one of:

- **Option 1 (recommended for SaaS): Postgres Row-Level Security.** Per request,
  `SET LOCAL app.current_org = :org_id`; RLS policies on every tenant table
  enforce `org_id = current_setting('app.current_org')`. The database refuses to
  leak even if application code forgets a filter. Cost: SQLite (dev/tests)
  doesn't support RLS, so you need a Postgres-only enforcement layer + a
  belt-and-braces app-level filter for the SQLite path.
- **Option 2 (lighter, for soft multi-tenancy only): SQLAlchemy session scoping.**
  A request-scoped `org_id` plus a global `with_loader_criteria` / query filter
  applied to every model that has `org_id`. Easier, works on SQLite, but a raw
  `session.get(Model, id)` or a `text()` query bypasses it — weaker guarantee.

For a credible SaaS, do **both**: RLS as the hard backstop, session scoping for
ergonomics. For workspaces-only, Option 2 is acceptable.

### A4. Request context

Add an `org_id` to the auth context. Two delivery options: subdomain/path
(`/{org_slug}/...`) or a `X-Org-Id` header validated against the caller's
memberships. The dependency that resolves it must verify the user is a member of
that org before any data access.

### A5. Migration (backfill)

One Alembic migration: create `organizations` + `memberships`, insert a single
`"default"` org, backfill `org_id = "default"` on every table, add the FKs/RLS
last. **Watch the revision-id ≤32-char limit** (known Postgres-upgrade crash;
SQLite tests won't catch it). Backfill must be batched on large `runs` tables.

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
   tenants' runners, the Noodle DB/Redis, the host metadata endpoint
   (`169.254.169.254`), or internal services. SSRF controls on HTTP nodes too.
4. **Close the sub-workflow hole.** Sub-workflows currently execute in-process on
   the host (`runtime_pool.py` docstring) — under hard multi-tenancy they must
   run in the same isolated boundary as their parent.
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
> spent person-years. For a solo project, do not start D until A–C are live and
> there is demand that justifies it.

## 6. Phase E — Secrets isolation

- Current: per-credential DEK wrapped by a single master KEK
  (`services/crypto.py`). Good envelope design.
- Add a **per-org KEK** (each tenant's DEKs wrapped by that org's KEK), ideally
  backed by a real KMS / Vault rather than a single env-var master key. A
  master-key compromise or one tenant's bug must not expose another's secrets.
- KEK rotation per org.

## 7. Phase F — Storage isolation

- Artifact backends (`services/artifact_backends.py`, `s3_artifact_backend.py`)
  must namespace keys by org: prefix every `storage_key` with `org_id/` (and
  optionally per-org buckets for the strongest isolation).
- Signed/download URLs scoped so they cannot be guessed/replayed across tenants.
- Per-org retention and storage quotas (ties into Phase C).

## 8. Phase G — Operational

- Per-org audit log (`AuditEvent` gains `org_id`; `routers/audit.py` scopes by
  org).
- Per-org observability / metrics labels; noisy-neighbor alerting.
- Abuse handling: suspend an org, freeze its runs, revoke tokens.
- Billing/metering integration consuming Phase C meters.

## 9. Recommended sequencing

1. **Phase A** behind a feature flag, enforcement via RLS (Postgres) + session
   scoping (SQLite/dev) from day one. Single default org until B lands.
2. **Phase B** — memberships, org-scoped RBAC, invitations. (A + B = workspaces
   usable internally.)
3. **Phase C** — quotas, fair scheduling, metering. (A–C = sellable soft
   multi-tenancy / enterprise feature.)
4. **Stop and validate.** Ship workspaces; get real multi-team usage.
5. **Phase E + F** — per-org keys and storage namespacing (cheap, do alongside).
6. **Phase D** — only with demand/funding. Start with the `docker`/`kubernetes`
   runner-pool isolation path + per-`(org, env)` warm pools, then egress
   hardening, then close the sub-workflow hole.
7. **Phase G** throughout.

## 10. Risks & honest caveats

- **Cross-tenant leak risk is highest in Phase A.** One missed scope = data
  breach. This is why RLS (DB-enforced) is strongly preferred over hand-written
  filters for any untrusted use.
- **The warm-pool wedge and hard multi-tenancy fight each other.** You can keep
  some warmth with per-tenant pools, but you pay in idle infra per tenant.
- **Effort is dominated by Phase A's blast radius** (touches every table, query,
  and service) and Phase D's new infra discipline. B, C, E, F, G are bounded.
- **Single-maintainer reality:** A–C is a focused multi-month project; D is the
  kind of thing that stalls solo efforts. Scope to A–C unless the business case
  for D is proven.
- **Migration constraint:** Alembic revision ids must be ≤32 chars (Postgres
  upgrade crashes otherwise; SQLite tests don't catch it). Backfill migrations on
  `runs`/`node_runs` must be batched.

## 11. Decision checklist before starting

- [ ] Soft (workspaces) or hard (SaaS) multi-tenancy? (Sets scope: A–C vs A–G.)
- [ ] Enforcement: RLS + session scoping, or session scoping only?
- [ ] Tenant routing: subdomain, path prefix, or header?
- [ ] License resolved (blocks any release; AGPL vs BSL — see name/license notes)?
- [ ] Is there demand that justifies Phase D, or stop at workspaces?
