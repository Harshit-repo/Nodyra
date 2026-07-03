import type { Edge } from "@xyflow/react";
import { describe, expect, it } from "vitest";

import { computeLoopFrames } from "./loopFrames";
import type { NodyraNode } from "./store";

function node(
  id: string,
  manifestId: string,
  x: number,
  y: number,
  params: Record<string, unknown> = {},
): NodyraNode {
  return {
    id,
    type: "nodyra",
    position: { x, y },
    width: 80,
    height: 100,
    data: { manifest: { id: manifestId } as never, params },
  } as unknown as NodyraNode;
}

function edge(source: string, target: string): Edge {
  return { id: `${source}->${target}`, source, target };
}

describe("computeLoopFrames", () => {
  it("frames the body between a paired loop_start and loop_end", () => {
    const nodes = [
      node("s", "loop_start", 100, 100, { mode: "each" }),
      node("b", "code", 300, 100),
      node("e", "loop_end", 500, 100, { loop_start_id: "s" }),
      node("outside", "code", 900, 900), // not in the region
    ];
    const edges = [edge("s", "b"), edge("b", "e")];
    const frames = computeLoopFrames(nodes, edges);

    expect(frames).toHaveLength(1);
    const f = frames[0];
    expect(f.id).toBe("loopframe:s");
    expect(f.data.label).toBe("↻ each");
    // left/top sit above-left of the Loop Start; box excludes the outside node
    expect(f.position.x).toBeLessThan(100);
    expect(f.position.y).toBeLessThan(100);
    // width spans s(100)..e(500+80) plus padding, but not the far outside node
    expect(f.style.width).toBeLessThan(700);
    expect(f.style.width).toBeGreaterThan(480);
  });

  it("emits no frame for an unpaired loop_start", () => {
    const nodes = [node("s", "loop_start", 0, 0), node("b", "code", 200, 0)];
    expect(computeLoopFrames(nodes, [edge("s", "b")])).toHaveLength(0);
  });

  it("produces a frame per loop and labels the mode", () => {
    const nodes = [
      node("s1", "loop_start", 0, 0, { mode: "batch", batch_size: 2 }),
      node("e1", "loop_end", 200, 0, { loop_start_id: "s1" }),
      node("s2", "loop_start", 0, 400, { mode: "while" }),
      node("e2", "loop_end", 200, 400, { loop_start_id: "s2" }),
    ];
    const edges = [edge("s1", "e1"), edge("s2", "e2")];
    const frames = computeLoopFrames(nodes, edges);
    const labels = frames.map((f) => f.data.label).sort();
    expect(labels).toEqual(["↻ batch 2", "↻ while"]);
  });
});
