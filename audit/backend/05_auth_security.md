# 05 — Authentication, Authorization & Secrets

Independent review, 2026-06-16. Files: `apps/api/app/security.py`,
`apps/api/app/services/crypto.py`, `apps/api/app/config.py`,
`apps/api/app/routers/auth.py`, `apps/api/app/main.py`,
`deploy/docker-compose.yml`.

## What is solid (verified)
- **Envelope encryption** (`crypto.py`): per-credential DEK ← per-org KEK ←
  master KEK; rotation re-wraps keys without re-encrypting data; one credential's
  exposure doesn't leak others. Fernet (AES-CBC+HMAC). Good design.
- **Signed tokens** are `typ`-discriminated (`create_token` sets `typ:"session"`;
  `verify_token` rejects anything else) — the TOK-1 fix stops a runner/OAuth/k8s
  purpose-token being replayed as a session. Constant-time `hmac.compare_digest`
  on signature, password, and CSRF compares.
- **RBAC** (`security.py`): role ranks + permission→min-role map; `user:manage`
  forces an authenticated actor even when `auth_required=False` (`_REQUIRES_AUTHENTICATED`)
  so an anonymous local instance can't create/delete owners.
- **Multi-tenant org resolution**: membership checked per request; ContextVar set
  *before* the membership query (documented ordering subtlety); anonymous callers
  limited to the default org.
- **Dual-mode auth + CSRF**: bearer (CSRF-exempt) or httpOnly cookie (CSRF
  double-submit enforced on state-changing methods); WS one-time tickets keep
  tokens out of access logs. Login/register have per-IP sliding-window rate limits.

## Findings

### AUTH-1 — Default `SECRET_KEY` is not enforced; the warning is suppressed in the shipped deploy (HIGH → Critical if shipped)
`config.py:244` defaults `secret_key="nodyra-dev-secret-change-me-in-production"`.
The only guard is `runtime_warnings()` (`config.py:353`), which:
1. **never aborts** — it's advisory, surfaced only via `GET /ops/runtime-mode`
   (`routers/ops.py:88`); and
2. **returns `[]` unless `runtime_mode == "production"`** (`config.py:315`).

`deploy/docker-compose.yml` (the documented deploy path) **does not set
`RUNTIME_MODE`** (confirmed: no match in the file), so it runs in the `local`
default — the default-secret warning is never even emitted — while it *does*
default `SECRET_KEY: ${NODYRA_SECRET_KEY:-nodyra-dev-secret-change-me-in-production}`.

- **Exploit:** with the public default secret, the token-signing HMAC key is
  known, so anyone can mint a valid `typ:"session"` token for any `user_id` →
  **full authentication bypass / account takeover**. Separately, the master KEK
  is `sha256(secret_key)` (`crypto.py:36`), so all stored credentials are
  **decryptable by anyone with DB/backup access**.
- **Why it matters:** the single highest-impact misconfiguration is silent in the
  exact path users will run. Compare: `dispatch_topology_errors()` *does* abort
  (`main.py:205`, `worker_main.py:41`) — the same fail-closed treatment should
  apply to the secret.
- **Fix (recommended, conservative):** at startup, **raise** when
  `secret_key == <default>` and (`auth_required` or `multi_tenancy_enabled`),
  unless `runtime_allow_insecure=True`. Keep auth-disabled local dev working. Also
  set `RUNTIME_MODE: production` in `deploy/docker-compose.yml` so the advisory
  warnings actually surface.
- **Test:** boot with default secret + `auth_required=True` → expect RuntimeError;
  with `runtime_allow_insecure=True` → boots with a logged warning.
- **Status:** Fixed this pass (see commit) — guard added + compose updated.

### AUTH-2 — No token revocation / server-side session invalidation (MEDIUM)
Tokens are stateless HMAC blobs with only `exp` (default TTL 24h,
`auth_token_ttl_seconds=86_400`). There is no deny-list / token-version, so:
logout cannot invalidate an outstanding bearer token; a compromised token is
valid until expiry; disabling/deleting a user or changing their role does not
revoke already-issued tokens until they expire.
- **Fix:** add a `token_version`/`session_epoch` column on `User` included in the
  token payload and checked in `verify_token` (bump to revoke all sessions), or a
  short TTL + refresh-token rotation. Minimum: shorten default TTL and document
  the revocation gap.
- **Status:** Needs decision.

### AUTH-3 — `internal_api_token` empty default disables `/internal/*` auth (MEDIUM, documented)
`config.py:248`: blank means **no check** on `/internal/*` (worker-level access).
Compose requires it (good), but a non-compose deployment that forgets it exposes
privileged endpoints unauthenticated.
- **Fix:** fold into the AUTH-1 startup guard — refuse blank `internal_api_token`
  when `dispatch_role != "inline"` or `runtime_mode=production` (and not
  `runtime_allow_insecure`).
- **Status:** Reviewed.

### AUTH-4 — PBKDF2 work factor below current guidance (LOW)
`crypto.py:21` uses PBKDF2-HMAC-SHA256 at 200k rounds; OWASP's 2023 baseline is
600k for SHA256 (or migrate to Argon2id/scrypt). Salts are random per-password
and verification is constant-time — only the iteration count is dated.
- **Fix:** raise to ≥600k, or move to Argon2id (`argon2-cffi`); store the
  algorithm/params in the hash string for transparent upgrade-on-login.
- **Status:** Reviewed.

### AUTH-5 — Login/register rate limit is in-process only (LOW–MEDIUM)
`routers/auth.py:65` `_in_process_rate_limit` is per-process. Behind N API
replicas the effective per-IP limit is N×, and it resets on restart.
- **Fix:** back the limiter with Redis (already a dependency in production
  topologies) when `queue_backend=redis`.
- **Status:** Reviewed.

## Tests to add
- AUTH-1 startup guard (above). Token forgery regression: a token signed with a
  *different* key is rejected. Role-change does not currently revoke tokens
  (document/lock behaviour for AUTH-2). CSRF rejection on cookie-auth POST without
  `X-CSRF-Token`.
