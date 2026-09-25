import { afterEach, describe, expect, it, vi } from "vitest";

import { setOrgId, setToken } from "./api";
import { mcpApprovalApi } from "./mcpApprovalApi";

const approval = {
  id: "a".repeat(32), tool_name: "create_workflow", target: {}, arguments: { name: "Example" },
  status: "approved", expires_at: new Date(Date.now() + 900_000).toISOString(),
};

afterEach(() => {
  vi.unstubAllGlobals();
  setToken(null);
  setOrgId(null);
  document.cookie = "nodyra_csrf=; Max-Age=0; path=/";
});

describe("MCP browser approval transport", () => {
  it("uses the browser session and CSRF even when a legacy bearer token is stored", async () => {
    setToken("must-not-be-sent");
    setOrgId("workspace-1");
    document.cookie = "nodyra_csrf=review-token; path=/";
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(approval), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    await mcpApprovalApi.decide(approval.id, "approve");
    const [url, request] = fetch.mock.calls[0];
    expect(url).toBe(`/api/mcp-approvals/${approval.id}/decision`);
    expect(request.credentials).toBe("same-origin");
    expect(request.headers["X-CSRF-Token"]).toBe("review-token");
    expect(request.headers["X-Org-Id"]).toBe("workspace-1");
    expect(request.headers.Authorization).toBeUndefined();
    expect(JSON.parse(request.body)).toEqual({ decision: "approve" });
  });

  it("rejects malformed success responses before rendering actionable controls", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("[]", { status: 200 })));
    await expect(mcpApprovalApi.get(approval.id)).rejects.toThrow("could not be read");
  });

  it("explains a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(mcpApprovalApi.get(approval.id)).rejects.toThrow("Could not reach Nodyra");
  });
});
