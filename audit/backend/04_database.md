# 04 — Database: Models, Migrations, Secrets-at-rest

Independent review, 2026-06-16. Files: `apps/api/app/models.py` (25 models),
`apps/api/alembic/versions/` (51 migrations), `.github/workflows/ci.yml`
(migration checks), `apps/api/app/services/crypto.py` (secrets-at-rest).

## What is solid (verified)
- **Referential integrity is deliberate**: 45 of 46 FKs declare `ondelete`
  (35 `CASCADE`, 10 `SET NULL`). The split is correct: org → memberships/
  workflows/runs CASCADE; run → node_runs/run_events/run_approvals CASCADE
  (children die with the run); workflow/version/deployment → run are `SET NULL`
  so **run history survives** deleting the workflow that produced it. This is
  better FK hygiene than most production codebases.
- **Hot-path indexes are present and correct**:
  - `ix_run_queue_lease (status, priority, available_at)` exactly matches the
    SKIP-LOCKED lease `WHERE status=? AND available_at<=? ORDER BY priority DESC,
    available_at ASC` — index-only candidate location, no extra row locks
    (the code comments even explain the contention rationale).
  - `ix_run_queue_status_lease_expires` backs the expired-lease reaper;
    `ix_run_queue_org_lease` backs org-fair leasing.
  - `ix_runs_workflow_id_started_at`, `ix_runs_status_started_at`,
    `ix_node_runs_run_id_node_id`, `ix_run_events_run_id_sequence/_ts`,
    `ix_run_approvals_run_id_status` cover the run/event/approval read paths.
  - `users.email` and `organizations.slug` are `unique=True, index=True`.
- **No plaintext secrets at rest**: credentials use envelope encryption
  (`encrypted_data` + per-credential `encrypted_dek`, wrapped by per-org KEK →
  master KEK); `password_hash` is PBKDF2; runner `token_hash` is a digest; SSH
  credentials are Fernet-encrypted; `wrapped_org_kek` stores only the wrapped KEK.
  Verified against `crypto.py`.
- **Migrations are CI-guarded**: the Postgres lane runs `alembic upgrade head`
  **and `alembic check`** (model/metadata drift fails the build) — so the schema
  and ORM can't silently diverge.
- **Timestamps**: `created_at`/`updated_at` with `server_default=func.now()` +
  `onupdate` across mutable tables; `DateTime(timezone=True)`.
- **Tenant isolation at the DB**: org-scoped models carry `org_id` (indexed),
  enforced by both the ORM `do_orm_execute` criteria and Postgres RLS GUC.

## Findings

### DB-1 — `Deployment.environment_id` FK has no `ondelete` (LOW)
`models.py:738`: `environment_id: ForeignKey("environments.id")` — the only FK of
46 without an `ondelete`, while the *adjacent* `workflow_version_id` uses
`SET NULL`. Deleting an environment still referenced by a deployment then hits the
DB default (NO ACTION/RESTRICT on Postgres with FKs enforced) and **errors**,
rather than nulling the reference.
- **Impact:** an operator can't delete an environment that any deployment ever
  pointed at; the failure is a raw FK violation, not a friendly message.
- **Fix:** make it explicit — `ondelete="SET NULL"` for consistency (deployment
  falls back to the global env), or `RESTRICT` if blocking is intended, with a
  pre-check that returns a clean 409. Add the matching Alembic migration.
- **Status:** Reviewed — easy fix + migration.

### DB-2 — 51 migrations on a 0.0.1 project; squash before OSS release (LOW)
Fast schema churn means a fresh install replays 51 migrations. Not a correctness
issue (`alembic check` passes), but new contributors and CI pay for it, and the
history exposes a lot of intermediate churn.
- **Fix:** before the public release, squash to a single baseline migration
  (keep the old ones in an `archive/` or a tagged commit for existing deploys).
- **Status:** Reviewed.

### DB-3 — Confirm destructive migrations are reversible / data-safe (LOW, verify)
With 51 migrations there are likely column drops/renames. Spot-check that any
data-dropping migration is intentional and that `downgrade()` exists where
feasible (or is explicitly a no-op with a comment). Not reviewed file-by-file in
this pass.
- **Status:** Needs verification.

## Verdict
The data layer is **production-grade**: deliberate cascades, correct hot-path
indexes (especially the queue), encrypted secrets at rest, and CI-enforced
migration integrity. Only DB-1 is a concrete (low) fix; DB-2/-3 are
release-hygiene items.
