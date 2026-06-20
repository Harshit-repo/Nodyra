import { describe, expect, it } from "vitest";
import { buildGanttBars } from "./RunTimeline";
import type { RunInfo } from "../../types";

function makeRun(overrides: Partial<RunInfo> = {}): RunInfo {
  return {
    id: "r1",
    workflow_id: "wf",
    workflow_version: 1,
    mode: "manual",
    status: "success",
    trigger_type: "manual",
    started_at: "2026-01-01T00:00:00.000Z",
    finished_at: "2026-01-01T00:00:01.000Z",
    node_runs: [
      { node_id: "webhook", status: "success", output: null, error: null, duration_ms: 100 },
      { node_id: "fetch", status: "success", output: null, error: null, duration_ms: 400 },
      { node_id: "email", status: "error", output: null, error: "timeout", duration_ms: 500 },
    ],
    ...overrides,
  };
}

describe("buildGanttBars", () => {
  it("returns one bar per node_run", () => {
    expect(buildGanttBars(makeRun())).toHaveLength(3);
  });

  it("bar widths sum close to 100 when total = sum of durations", () => {
    const bars = buildGanttBars(makeRun());
    const total = bars.reduce((s, b) => s + b.widthPct, 0);
    expect(total).toBeCloseTo(100, 0);
  });

  it("assigns status to each bar", () => {
    const bars = buildGanttBars(makeRun());
    expect(bars.map((b) => b.status)).toEqual(["success", "success", "error"]);
  });

  it("handles missing duration_ms gracefully with zero width", () => {
    const run = makeRun();
    run.node_runs[0].duration_ms = null;
    const bars = buildGanttBars(run);
    expect(bars[0].widthPct).toBe(0);
  });
});
