# Documentation Review

Independent review, 2026-06-16. Scope: `README.md` (591 lines), `SECURITY.md`,
`CONTRIBUTING.md`, `LICENSE`, `docs/` (~30 files).

## What is solid (verified)
- **README is comprehensive and honest**: capabilities, architecture, repo layout,
  runtime model, security model, Docker + local-dev + k8s deployment,
  configuration, operations, and testing — 591 lines that match the code.
- **Security model is stated up front and accurately** (README §"Security model:
  single-tenant, trusted authors"): it correctly says code nodes run arbitrary
  Python in the worker trust boundary and that multi-tenant/untrusted use needs
  per-run disposable isolation (the remote-runner seam). This is exactly the
  SAFE-4 finding — already documented, not a gap. Cross-links to `SECURITY.md`
  and `docs/architecture.md#security`.
- **All referenced docs exist**: `SECURITY.md`, `docs/architecture.md`,
  `docs/status-matrix.md`, `CONTRIBUTING.md`, `LICENSE` — no dangling links among
  the README's primary references.
- **Deployment env instructions are correct (verified, not assumed)**: the README
  tells operators to put `NODYRA_SECRET_KEY` / `INTERNAL_API_TOKEN` /
  `AUTH_REQUIRED=true` in `deploy/.env`. The field is `secret_key` (env
  `SECRET_KEY`, no alias) — which *looks* like a mismatch, but
  `deploy/docker-compose.yml` maps `SECRET_KEY: ${NODYRA_SECRET_KEY:-…}`, so the
  documented var is right **for the Docker path**. The compose default equals
  `DEFAULT_SECRET_KEY`, which the new AUTH-1 startup guard rejects under
  `auth_required`/production — doc + guard are consistent.
- **`status-matrix.md`** gives a per-component shipped/beta/scaffolded/planned
  breakdown — sets honest expectations for an evaluator.

## Findings

### DOC-1 — Local-dev (non-Docker) secret var name differs from Docker (LOW)
In the **local developer stack** the API reads `.env` directly via
pydantic-settings (`env_file=".env"`), so the variable is `SECRET_KEY` — *not*
`NODYRA_SECRET_KEY` (which only works because docker-compose renames it). An
operator copying the Docker `.env` snippet into a bare-metal/local run would set
an ignored var and (now) hit the AUTH-1 guard.
- **Fix:** in the local-dev section, show `SECRET_KEY=…` explicitly (or document
  that `NODYRA_SECRET_KEY` is a compose-only convenience mapping). One-line clarity
  fix.
- **Status:** Reviewed.

### DOC-2 — `docs/` mixes living docs with historical audit snapshots (LOW)
`docs/` contains both authoritative references (`architecture.md`, `nodes.md`,
`deployment.md`, `mcp.md`, `status-matrix.md`) and point-in-time artifacts
(`production-readiness-audit.md`, `production-review-2026-06-14*.md`,
`frontend-audit.md`, several `*-plan.md`). A reader can't tell which is current.
- **Fix:** add a short `docs/README.md` index that labels each file
  *reference* vs *historical/plan*, or move dated snapshots under `docs/history/`.
  (This is the same hazard the auto-memory notes: old `production-*` docs are
  historical only.)
- **Status:** Reviewed.

### DOC-3 — No top-level CHANGELOG / version-to-feature mapping (LOW)
At version 0.0.1 with 51+ migrations, there's no CHANGELOG. Fine pre-release, but
needed before the OSS/public launch so users can track breaking node-schema or
migration changes (ties to NODE-2 param-migration policy).
- **Status:** Reviewed.

### DOC-4 — Internal/licensing docs correctly excluded from VCS (verified clean)
`docs/licensing-internal.md` is present locally but `.gitignore` protects
`docs/licensing-internal.md`, `.secrets/`, and `*license*private*.pem`. Confirmed
these must never be committed; current ignores cover them.
- **Status:** Verified clean.

## Verdict
Documentation is **a strength** — the README is unusually complete and, crucially,
*honest about the security model* (the single most important thing to state
clearly for this class of tool). All primary links resolve and the documented
Docker secrets are correct. Findings are minor clarity items: one env-var note for
the non-Docker path, a docs index separating living vs historical files, and a
CHANGELOG before public launch.
