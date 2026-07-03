# Security Review (Synthesis)

Independent audit, 2026-06-16, `feat/arch-program-phase5`. Synthesizes the
auth/secrets (05), execution-safety (09), node-system (08), API (02), database
(04), and frontend (01/04) area reviews.

## Threat model (as designed)
Nodyra's stated and correct posture: **single-tenant, trusted authors**. Anyone
who can add a Code node runs arbitrary Python in the worker trust boundary. The
README states this plainly. So the security bar is: protect the *control plane*
(auth, secrets, tenant isolation, the API) and keep the *execution plane* honest
within that trusted-author assumption — while the remote-runner seam exists to add
per-run isolation later for untrusted/multi-tenant use.

## Strengths (verified)
- **Envelope encryption** done right: DEK ← org KEK ← master KEK; credentials
  encrypted at rest; `test_crypto` covers token tamper/signature.
- **Defense-in-depth auth**: global `auth_gate` + `_csrf_gate` middleware reject
  unauthenticated/forged requests *before* routers — verified the suspected
  unauthenticated `export.py`/`expressions.py` routes are in fact gated (a
  false-positive I disproved by reading `main.py:563`).
- **Multi-tenant isolation** enforced in depth: `do_orm_execute` +
  `with_loader_criteria` + Postgres RLS GUC, with cross-org tests
  (`test_org_isolation_enforcement`, `test_cross_org_loops`).
- **Secret redaction** scrubs known credential values from logs, expression
  previews, run events, and persisted run data — closes the usual node-output leak.
- **Static-vs-exec node split**: API manifest discovery is AST-only (no exec);
  code execution is confined to the isolated runtime / subprocess pool.

## Issues found and dispositioned
| ID | Severity | Issue | Status |
|---|---|---|---|
| AUTH-1 | **High** | Default `SECRET_KEY` only warned (and warning suppressed in shipped compose) → token forgery + credential decryption | **Fixed** — `security_startup_errors()` guard refuses boot on default secret under `auth_required`/MT/production; compose sets `RUNTIME_MODE=production`; tests added |
| AUTH-3 | Medium | Blank `internal_api_token` left `/internal/*` open in split topology | **Fixed** — folded into the startup guard |
| FE-1 | Medium | Session token in `localStorage` (XSS-exfiltratable) | **Fixed** — login no longer persists token; httpOnly-cookie path preferred |
| SAFE-3 / NODE-1 | Medium | In-process code exec path (`use_subprocess_runner=False`) execs uploaded code in host process, bypassing the sandbox | **Fixed** — `enforce_sandbox_policy` refuses boot when the in-process runner is set under MT or any non-`off` sandbox; the runner's in-process branch is now unreachable in those modes (4 new tests, TEST-6) |
| SAFE-2 | Medium | SSRF check is literal-IP + deploy-time only | **Hardened** — startup guard now requires a dedicated `sandbox_network` under MT so run containers can't reach postgres/redis/minio on the default bridge; deploy-time IP check remains advisory |
| AUTH-2 | Medium | No token revocation (stateless 24h HMAC) | Decision — add a token version/`jti` denylist if forced-logout is required |
| AUTH-4 | Low | PBKDF2 200k < OWASP 600k | Reviewed — bump or move to Argon2id |
| AUTH-5 | Low-Med | Login rate-limit in-process only | Reviewed — move to Redis behind replicas |
| SAFE-1 | Low | Expression alias substitution is textual `.replace()` | Reviewed |

No **Critical** (RCE-escape beyond the documented trusted-author boundary,
silent cross-tenant leak, or credential exposure) was found in this pass.

## Top security recommendations (priority order)
1. ~~Close SAFE-3/NODE-1 as an enforced invariant~~ — **Done this pass.**
   `enforce_sandbox_policy()` now fails closed: `use_subprocess_runner=False` is
   rejected under multi-tenancy or any non-`off` sandbox, so the in-process exec
   path is unreachable in those modes (TEST-6 added).
2. **Make sandbox network egress a default-deny allowlist** (SAFE-2, follow-up):
   the startup guard now *requires* the dedicated `sandbox_network` under MT (no
   reachability to postgres/redis/minio). The remaining work is true internet
   egress control — an egress proxy/allowlist — since `internal: true` would break
   legitimate HTTP/LLM nodes. Treat the deploy-time IP check as advisory only.
3. **Decide token revocation** (AUTH-2): a key-version field on tokens gives
   instant global invalidation on KEK rotation/forced logout — cheap insurance.
4. **Bump KDF + distribute rate-limit** (AUTH-4/5): Argon2id (or 600k PBKDF2) and
   Redis-backed login throttling for multi-replica deployments.

## Verdict
Security engineering is **above the bar for this class of tool**: envelope
encryption, middleware-level auth/CSRF gating, RLS-backed tenant isolation, real
secret redaction, and an honest documented threat model. The one **High**
(default-secret enforcement) is fixed this pass, as is the highest-value Medium —
SAFE-3/NODE-1 is now an enforced, tested startup invariant (in-process execution
cannot be configured under MT/sandbox). The remaining items are Medium hardening,
the main one being true egress allowlisting (SAFE-2 follow-up) before any move
toward untrusted multi-tenant use.
