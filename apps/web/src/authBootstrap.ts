import { ApiError } from "./api";
import type { AuthState } from "./types";

// Used when the backend genuinely has no /auth/required endpoint (legacy / 404)
// — proceed as an open, no-auth instance.
export const NO_AUTH_FALLBACK: AuthState = {
  auth_required: false,
  signed_in: false,
  registration_open: false,
  multi_tenancy: false,
  user: null,
};

/**
 * Decide whether a failed `/auth/required` bootstrap should be retried.
 *
 * Transient failures — the API still starting up (network/fetch error) or a
 * 5xx — should retry so the app waits for the backend instead of falling
 * through and rendering against a dead API (which shows a black screen).
 * Only a definitive 404 means the endpoint isn't there (legacy backend).
 * Every other failure must remain fail-closed and retry; treating a 401/403,
 * proxy error, or malformed response as "no auth" can expose the application.
 */
export function shouldRetryAuthError(err: unknown): boolean {
  return !(err instanceof ApiError && err.status === 404);
}
