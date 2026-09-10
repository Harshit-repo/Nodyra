import { describe, expect, it } from "vitest";

import { formatTimelineSummary, formatTimelineType } from "./ExecutionsPage";
import type { RunTimelineEvent } from "./api";

function event(type: string, data: Record<string, unknown>): RunTimelineEvent {
  return { type, ts: "2026-09-10T00:00:00Z", data } as RunTimelineEvent;
}

describe("run timeline rendering", () => {
  it("names a retry rather than falling back to the raw event type", () => {
    expect(formatTimelineType("node_retrying")).toBe("Node retrying");
  });

  it("shows which attempt a retry is", () => {
    // Without the attempt the row says only that the node retried, which is
    // the least useful half of the fact.
    const summary = formatTimelineSummary(
      event("node_retrying", {
        node_id: "fetch",
        attempt: 2,
        of: 4,
        error: "upstream returned 503",
      }),
    );

    expect(summary).toContain("fetch");
    expect(summary).toContain("attempt 2 of 4");
    expect(summary).toContain("upstream returned 503");
  });

  it("copes with an attempt that has no known total", () => {
    const summary = formatTimelineSummary(
      event("node_retrying", { node_id: "fetch", attempt: 3 }),
    );

    expect(summary).toContain("attempt 3");
    expect(summary).not.toContain("of");
  });

  it("leaves ordinary node events unchanged", () => {
    const summary = formatTimelineSummary(
      event("node_finished", { node_id: "transform", duration_ms: 12 }),
    );

    expect(summary).toBe("transform · 12ms");
  });

  it("still falls back for an event type it does not know", () => {
    expect(formatTimelineType("some_future_event")).toBe("some future event");
  });
});
