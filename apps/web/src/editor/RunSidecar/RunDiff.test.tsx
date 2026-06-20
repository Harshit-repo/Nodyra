import { describe, expect, it } from "vitest";
import { diffNodeRows } from "./RunDiff";
import type { RunInfo } from "../../types";

function makeRun(
  nodeRuns: Array<{ node_id: string; status: string; duration_ms: number | null }>,
): RunInfo {
  return {
    id: "r",
    workflow_id: "wf",
    workflow_version: 1,
    mode: "manual",
    status: "success",
    trigger_type: "manual",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T00:00:01Z",
    node_runs: nodeRuns.map((n) => ({ ...n, output: null, error: null })),
  };
}

describe("diffNodeRows", () => {
  it("marks a node that went from error to success as improved", () => {
    const a = makeRun([{ node_id: "email", status: "error", duration_ms: 920 }]);
    const b = makeRun([{ node_id: "email", status: "success", duration_ms: 650 }]);
    const rows = diffNodeRows(a, b);
    expect(rows[0].change).toBe("improved");
  });

  it("marks a node that went from success to error as worse", () => {
    const a = makeRun([{ node_id: "smtp", status: "success", duration_ms: 100 }]);
    const b = makeRun([{ node_id: "smtp", status: "error", duration_ms: 200 }]);
    const rows = diffNodeRows(a, b);
    expect(rows[0].change).toBe("worse");
  });

  it("marks a node with same status but 2x slower duration as changed", () => {
    const a = makeRun([{ node_id: "fetch", status: "success", duration_ms: 100 }]);
    const b = makeRun([{ node_id: "fetch", status: "success", duration_ms: 210 }]);
    const rows = diffNodeRows(a, b);
    expect(rows[0].change).toBe("changed");
  });

  it("marks unchanged nodes as none", () => {
    const a = makeRun([{ node_id: "webhook", status: "success", duration_ms: 80 }]);
    const b = makeRun([{ node_id: "webhook", status: "success", duration_ms: 82 }]);
    const rows = diffNodeRows(a, b);
    expect(rows[0].change).toBe("none");
  });
});
