import { afterEach, describe, expect, it, vi } from "vitest";

import { api, encodeWebhookPath } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("webhook path URLs", () => {
  it("preserves nested path separators while encoding path segments", () => {
    expect(encodeWebhookPath("beta-qa-20260705-021741/order intake")).toBe(
      "beta-qa-20260705-021741/order%20intake",
    );
  });

  it("calls the listen endpoint with nested paths intact", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ listening: true, ttl_seconds: 600 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.startListen("beta-qa-20260705-021741/order-intake");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/webhook-test/beta-qa-20260705-021741/order-intake/listen",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
