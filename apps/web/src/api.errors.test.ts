import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, errorMessage, formatErrorDetail } from "./api";

describe("formatErrorDetail", () => {
  it("passes a plain string detail through unchanged", () => {
    expect(formatErrorDetail("Workflow not found")).toBe("Workflow not found");
  });

  it("joins FastAPI validation arrays into readable text (no raw JSON)", () => {
    const detail = [
      { loc: ["body", "name"], msg: "field required", type: "value_error.missing" },
      { loc: ["body", "email"], msg: "value is not a valid email", type: "value_error.email" },
    ];
    const out = formatErrorDetail(detail);
    expect(out).toBe("field required; value is not a valid email");
    expect(out).not.toContain("loc");
    expect(out).not.toContain("{");
  });

  it("extracts a message field from an object detail", () => {
    expect(formatErrorDetail({ message: "Quota exceeded", code: "quota" })).toBe(
      "Quota exceeded",
    );
  });

  it("falls back to JSON for an unrecognised object shape", () => {
    expect(formatErrorDetail({ foo: "bar" })).toBe('{"foo":"bar"}');
  });
});

describe("ApiError display", () => {
  it("does not leak the class name when stringified (String(err))", () => {
    const err = new ApiError(403, "403 You don't have permission", "You don't have permission");
    expect(String(err)).not.toContain("ApiError");
    expect(String(err)).toBe("403 You don't have permission");
  });

  it("keeps status and detail accessible for callers that branch on them", () => {
    const err = new ApiError(409, "409 conflict", { findings: [] });
    expect(err.status).toBe(409);
    expect(err.detail).toEqual({ findings: [] });
  });
});

describe("errorMessage", () => {
  it("strips the class-name prefix from a plain Error", () => {
    expect(errorMessage(new TypeError("Failed to fetch"))).toBe("Failed to fetch");
  });

  it("returns the clean message for an ApiError", () => {
    expect(errorMessage(new ApiError(500, "500 boom", "boom"))).toBe("500 boom");
  });

  it("passes strings through and stringifies other values", () => {
    expect(errorMessage("nope")).toBe("nope");
    expect(errorMessage(42)).toBe("42");
  });
});

describe("network failure handling", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("converts a fetch network failure into a clean ApiError (no class leak)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );
    try {
      await api.nodes();
      throw new Error("expected request to reject");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(0);
      expect(String(err)).not.toContain("TypeError");
      expect(String(err)).toContain("Could not reach the server");
    }
  });

  it("re-throws an intentional AbortError untouched", async () => {
    const abort = new DOMException("aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abort));
    await expect(api.nodes()).rejects.toBe(abort);
  });
});
