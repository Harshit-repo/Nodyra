import { describe, expect, it } from "vitest";
import { nodeReliability } from "./RunStats";
import type { RunInfo } from "../../types";

function makeRun(id: string, nodeStatuses: Record<string, string>): RunInfo {
  return {
    id,
    workflow_id: "wf",
    workflow_version: 1,
    mode: "manual",
    status: "success",
    trigger_type: "manual",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T00:00:01Z",
    node_runs: Object.entries(nodeStatuses).map(([node_id, status]) => ({
      node_id,
      status,
      output: null,
      error: null,
      duration_ms: null,
    })),
  };
}

describe("nodeReliability", () => {
  it("counts successes and errors across runs", () => {
    const runs = [
      makeRun("r1", { email: "success" }),
      makeRun("r2", { email: "error" }),
      makeRun("r3", { email: "success" }),
    ];
    const rel = nodeReliability(runs, "email");
    expect(rel).toEqual({ ok: 2, err: 1, skip: 0 });
  });

  it("counts skipped nodes", () => {
    const runs = [makeRun("r1", { slack: "skipped" }), makeRun("r2", { slack: "success" })];
    const rel = nodeReliability(runs, "slack");
    expect(rel).toEqual({ ok: 1, err: 0, skip: 1 });
  });

  it("returns zeros for a node not present in any run", () => {
    const runs = [makeRun("r1", { webhook: "success" })];
    expect(nodeReliability(runs, "missing-node")).toEqual({ ok: 0, err: 0, skip: 0 });
  });
});
