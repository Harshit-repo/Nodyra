import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("workflow collection pagination", () => {
  it("loads every server page so dashboard totals are not capped at 50", async () => {
    const makeItem = (index: number) => ({
      id: `workflow-${index}`,
      name: `Workflow ${index}`,
      active: false,
      node_count: 0,
      version: 1,
      published_version: 1,
      has_unpublished_changes: false,
      last_run_status: null,
      last_run_started_at: null,
      updated_at: "2026-06-22T00:00:00Z",
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const offset = Number(new URL(url, "http://localhost").searchParams.get("offset"));
      const items = offset === 0
        ? Array.from({ length: 500 }, (_, index) => makeItem(index))
        : [makeItem(500)];
      return new Response(JSON.stringify({ items, total: 501, limit: 500, offset }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const workflows = await api.listWorkflows();

    expect(workflows).toHaveLength(501);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("limit=500&offset=0");
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain("limit=500&offset=500");
  });
});
