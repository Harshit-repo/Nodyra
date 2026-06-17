import { describe, expect, it } from "vitest";

import { loopBadgeText, metaBadgeText } from "./NodeCard";

describe("metaBadgeText", () => {
  it("counts nodes and marks isolated execution", () => {
    expect(metaBadgeText({ subgraph: { nodes: [{}, {}, {}] } })).toBe("▣ 3 nodes");
    expect(metaBadgeText({ subgraph: { nodes: [{}] } })).toBe("▣ 1 node");
    expect(metaBadgeText({ subgraph: { nodes: [{}, {}] }, execution: "isolated" })).toBe(
      "▣ 2 nodes · isolated",
    );
    expect(metaBadgeText({})).toBe("▣ 0 nodes");
  });
});

describe("loopBadgeText", () => {
  it("labels Loop End", () => {
    expect(loopBadgeText("loop_end", {})).toBe("↻ loop end");
  });

  it("defaults to each mode", () => {
    expect(loopBadgeText("loop_start", {})).toBe("↻ each");
  });

  it("shows batch/window size and group key and range count", () => {
    expect(loopBadgeText("loop_start", { mode: "batch", batch_size: 3 })).toBe("↻ batch 3");
    expect(loopBadgeText("loop_start", { mode: "window", batch_size: 2 })).toBe("↻ window 2");
    expect(loopBadgeText("loop_start", { mode: "group", group_key: "customer" })).toBe(
      "↻ group customer",
    );
    expect(loopBadgeText("loop_start", { mode: "range", count: 5 })).toBe("↻ range 5");
  });

  it("marks while/until and reduce", () => {
    expect(loopBadgeText("loop_start", { mode: "while" })).toBe("↻ while");
    expect(loopBadgeText("loop_start", { mode: "each", accumulate: true })).toBe(
      "↻ each · reduce",
    );
  });
});
