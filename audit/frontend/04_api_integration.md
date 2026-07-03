# 04 — Frontend API Integration & Client Security

Independent review, 2026-06-16. Files: `apps/web/src/api.ts`,
`apps/web/src/LoginPage.tsx`, `apps/web/src/authBootstrap.ts`,
`apps/web/index.html` (CSP).

## What is solid (verified)
- **Single centralized client** (`request<T>`): one place for headers, error
  shaping, 401 handling. No scattered `fetch` with ad-hoc error handling.
- **401 → `handleUnauthorized`**: clears token/user and notifies the app
  (`onUnauthorized`) so the SPA redirects to login; the run-stream `1008` close
  maps to the same path (FE-4).
- **Network failures normalized**: `safeFetch` turns `TypeError: Failed to fetch`
  into a clean `ApiError(0, "Could not reach the server…")`; **AbortError is
  re-thrown untouched**, so request cancellation works.
- **Error display hygiene**: `ApiError.toString()` strips the class-name prefix;
  `formatErrorDetail` turns FastAPI's `[{loc,msg,type}]` validation arrays into a
  sentence instead of dumping raw JSON at users.
- **CSRF double-submit** echoed from the `nodyra_csrf` cookie in cookie-auth mode;
  **CSP** meta tag present in `index.html`; markdown is rendered via DOMPurify
  (checked elsewhere). Org context (`X-Org-Id`) sent on every request.

## Findings

### FE-1 — Session token stored in `localStorage` (Bearer), not the httpOnly cookie (MEDIUM)
`LoginPage.tsx:48` calls `setToken(result.token)` → `localStorage["nodyra_token"]`,
and `authHeaders()` (`api.ts:195-206`) prefers `Authorization: Bearer <token>`
whenever a token is stored. So the SPA's primary auth credential lives in
JavaScript-readable storage.
- **Why it matters:** any XSS (a single unsanitized sink, a compromised
  dependency, a `dangerouslySetInnerHTML` slip) can read `localStorage` and
  exfiltrate the token = **full account takeover**. The token has a 24h TTL and
  **no server-side revocation** (see AUTH-2), so a stolen token is usable for up
  to a day and can't be killed by logout.
- **The fix is already built:** the backend supports httpOnly-cookie sessions +
  CSRF double-submit (C3). The httpOnly cookie is *already set* by `/auth/login`;
  the client just ignores it because it stores the Bearer token too. Stop calling
  `setToken()` on login (and registration) and let the cookie carry the session —
  `authHeaders()` already falls back to the CSRF header path. A JS-unreadable
  httpOnly cookie removes the XSS-token-theft vector entirely. Keep the
  localStorage path only for explicit personal API tokens, if needed.
- **Caveats:** verify the cookie's `SameSite`/`Secure` are correct for the deploy
  topology (config defaults `session_cookie_secure=True`, `samesite=lax`), and
  that WebSocket/artifact `?token=` flows still work (they have a separate
  ws-ticket mechanism, so they should).
- **Test:** after login, `localStorage.getItem("nodyra_token")` is null and
  authenticated requests still succeed via the cookie; logout clears the cookie.
- **Status:** Recommend fix (security hardening, low effort — infra exists).

### FE-2 — No automatic token refresh / silent re-auth (LOW)
Tokens are fixed-TTL (24h) with no refresh-token rotation; when one expires
mid-session the user is bounced to login. Acceptable for now, but pairs with
AUTH-2 (revocation) as a session-management improvement for SaaS.
- **Status:** Reviewed.

## Verdict
The client layer is **well-engineered** (centralized, cancellation-aware,
error-normalized, CSRF-ready). The one meaningful hardening is FE-1: move the SPA
to the httpOnly-cookie session the backend already provides, so the credential is
no longer reachable by JavaScript. High value, low effort.
