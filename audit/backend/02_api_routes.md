# 02 — API Routes

Independent review, 2026-06-16. Files: `apps/api/app/main.py` (middleware stack),
all 24 routers in `apps/api/app/routers/`, `apps/api/app/security.py`,
`apps/api/app/tenancy.py`. 24 routers, ~7.2k LOC, 69 GET endpoints.

## Auth/z posture — VERIFIED CLEAN (this was the main thing to check)
The routers gate **writes** with `require_permission(...)` RBAC and leave most
**reads** without a per-route auth dependency. That looked alarming at first
(e.g. `export.py`, `expressions.py`, `GET /workflows` take only `get_session`),
but authentication is enforced **globally** by middleware, so it is not a gap:

- **`auth_gate` middleware** (`main.py:563-596`): when `auth_required=True`, every
  request is rejected with 401 unless it carries a valid Bearer token, a valid
  `nodyra_session` cookie, or a valid `?token=` — except the explicit exempt set
  (`/auth/*`, `/health/*`, webhook ingress). So `export`/`expressions`/read
  endpoints **do** require authentication in production. Confirmed by reading the
  middleware end-to-end.
- **`_csrf_gate` middleware** (`main.py:515-560`): cookie-auth state-changing
  requests need a matching `X-CSRF-Token` (double-submit); Bearer is exempt
  (not forgeable via cookie injection). Sound.
- **RBAC** on writes via `require_permission` → role-minimum map; `user:manage`
  forces an authenticated actor even when auth is globally off.
- **Tenant isolation** is layered (`tenancy.py`): a `do_orm_execute` hook appends
  `org_id = :current_org` to every ORM SELECT on org-scoped models (so reads are
  org-bound even without per-route checks), **plus** Postgres RLS via the
  transaction-scoped `app.current_org` GUC. Defence in depth.
- **Structured errors**: the global `Exception` handler (`main.py:399`) returns
  `{"detail":"Internal server error","request_id"}` — **no stack trace leaked**;
  the trace is logged server-side with the request id.
- **DoS guards**: 10 MiB `Content-Length` body cap (`_body_size_limit`); login/
  register rate limiting; pagination caps (`list_workflows` uses
  `limit: int = Query(50, ge=1, le=500)` + offset, returns a `PageResponse` with
  total). Good.

## Findings

### API-1 — Confirm list `total` counts are org-scoped (LOW, verify)
`list_workflows` computes `total` via
`select(func.count()).select_from(Workflow)` (`workflows.py:255`). The tenancy
`with_loader_criteria` *should* apply to this (it targets the `Workflow` entity,
and Postgres RLS backs it regardless), so the count is org-scoped — but a bare
`func.count()` is exactly the shape where an ORM criteria can silently not apply.
- **Impact (if it doesn't apply):** a cross-org **count** leak (the number only,
  not data) — `items` stay scoped but `total` would reflect all orgs.
- **Fix/Action:** add a multi-tenant test asserting `total` matches the count of
  the caller's org only; if it leaks, count via `select(func.count(Workflow.id))`
  through the entity or filter explicitly.
- **Status:** Needs verification (likely already correct via RLS).

### API-2 — Chunked bodies bypass the size cap (INFO, accepted)
`_body_size_limit` checks the `Content-Length` header only; a chunked
transfer-encoding request with no length slips past (bounded by OS/TCP + client).
Documented trade-off in the code. Fine for now; revisit if abuse appears.
- **Status:** Reviewed (accepted).

### API-3 — Read endpoints are authn-gated but not authz-gated (INFO, by design)
Any authenticated user (even `viewer`) can read any workflow/run **in their org**
(reads have no role minimum). This is a deliberate model (RBAC governs writes;
org-scoping governs tenant isolation). Call it out in docs so operators don't
assume viewer is read-restricted within an org.
- **Status:** Doc.

## Verdict
The API surface is **well-architected**: global authn middleware + CSRF +
layered tenant isolation (ORM criteria + RLS) + structured errors + body/rate
caps + paginated lists. No authz hole found in this pass. Only action item is the
API-1 count-scoping test (cheap, high-confidence-it's-fine).
