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
 * A definitive 404 means the endpoint isn't there (legacy backend): don't
 * retry, fall back to no-auth.
 */
export function shouldRetryAuthError(err: unknown): boolean {
  if (err instanceof ApiError) return err.status >= 500;
  return true; // network / fetch failure → API likely not ready yet
}
