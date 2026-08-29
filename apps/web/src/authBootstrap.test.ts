import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { NO_AUTH_FALLBACK, shouldRetryAuthError } from "./authBootstrap";

describe("shouldRetryAuthError", () => {
  it("retries on a network/fetch failure (API not ready)", () => {
    expect(shouldRetryAuthError(new TypeError("Failed to fetch"))).toBe(true);
  });

  it("retries on 5xx (server starting/unhealthy)", () => {
    expect(shouldRetryAuthError(new ApiError(503, "503 unavailable", null))).toBe(true);
    expect(shouldRetryAuthError(new ApiError(500, "500 error", null))).toBe(true);
  });

  it("does not retry on a definitive 404 (endpoint absent)", () => {
    expect(shouldRetryAuthError(new ApiError(404, "404 not found", null))).toBe(false);
  });

  it("fails closed and retries on non-404 4xx", () => {
    expect(shouldRetryAuthError(new ApiError(400, "400 bad", null))).toBe(true);
    expect(shouldRetryAuthError(new ApiError(401, "401 unauthorized", null))).toBe(true);
    expect(shouldRetryAuthError(new ApiError(403, "403 forbidden", null))).toBe(true);
  });

  it("exposes a no-auth fallback shape", () => {
    expect(NO_AUTH_FALLBACK).toEqual({
      auth_required: false,
      signed_in: false,
      registration_open: false,
      multi_tenancy: false,
      user: null,
    });
  });
});
