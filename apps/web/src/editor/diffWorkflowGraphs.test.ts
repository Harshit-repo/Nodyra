import { describe, it, expect } from "vitest";
import { diffWorkflowGraphs } from "./diffWorkflowGraphs";
import type { WorkflowGraph } from "../types";

function graph(nodes: object[], edges: object[] = []): WorkflowGraph {
  return { nodes, edges } as unknown as WorkflowGraph;
}

const nodeA = { id: "a", type: "http", position: { x: 0, y: 0 }, params: { url: "https://x.com" } };
const nodeB = { id: "b", type: "http", position: { x: 100, y: 0 }, params: { url: "https://y.com" } };
const nodeC = { id: "c", type: "set", position: { x: 200, y: 0 }, params: { value: 1 } };

describe("diffWorkflowGraphs", () => {
  it("returns empty diff for identical graphs", () => {
    const result = diffWorkflowGraphs(graph([nodeA]), graph([nodeA]));
    expect(result.added).toEqual([]);
    expect(result.removed).toEqual([]);
    expect(result.changed).toEqual([]);
    expect(result.unchanged).toEqual(["a"]);
    expect(result.removedNodes).toEqual([]);
  });

  it("detects added node", () => {
    const result = diffWorkflowGraphs(graph([nodeA]), graph([nodeA, nodeB]));
    expect(result.added).toEqual(["b"]);
    expect(result.unchanged).toEqual(["a"]);
    expect(result.removed).toEqual([]);
  });

  it("detects removed node and includes full node object in removedNodes", () => {
    const result = diffWorkflowGraphs(graph([nodeA, nodeB]), graph([nodeA]));
    expect(result.removed).toEqual(["b"]);
    expect(result.removedNodes).toHaveLength(1);
    expect(result.removedNodes[0].id).toBe("b");
    expect(result.unchanged).toEqual(["a"]);
  });

  it("detects changed node params", () => {
    const nodeAMod = { ...nodeA, params: { url: "https://changed.com" } };
    const result = diffWorkflowGraphs(graph([nodeA]), graph([nodeAMod]));
    expect(result.changed).toEqual(["a"]);
    expect(result.changedParams["a"]).toHaveLength(1);
    expect(result.changedParams["a"][0]).toMatchObject({
      key: "url",
      before: "https://x.com",
      after: "https://changed.com",
    });
  });

  it("position-only change is NOT a change", () => {
    const nodeAMoved = { ...nodeA, position: { x: 999, y: 999 } };
    const result = diffWorkflowGraphs(graph([nodeA]), graph([nodeAMoved]));
    expect(result.changed).toEqual([]);
    expect(result.unchanged).toEqual(["a"]);
  });

  it("key-order-only param difference is NOT a change (deep equal, not JSON.stringify)", () => {
    const base = { id: "a", type: "set", position: { x: 0, y: 0 }, params: { a: 1, b: 2 } };
    const compare = { id: "a", type: "set", position: { x: 0, y: 0 }, params: { b: 2, a: 1 } };
    const result = diffWorkflowGraphs(graph([base]), graph([compare]));
    expect(result.changed).toEqual([]);
    expect(result.unchanged).toEqual(["a"]);
  });

  it("type change on same ID is remove + add, not changed", () => {
    const nodeADiffType = { ...nodeA, type: "email" };
    const result = diffWorkflowGraphs(graph([nodeA]), graph([nodeADiffType]));
    expect(result.removed).toEqual(["a"]);
    expect(result.added).toEqual(["a"]);
    expect(result.changed).toEqual([]);
  });

  it("handles empty graphs without crash", () => {
    const result = diffWorkflowGraphs(graph([]), graph([]));
    expect(result.added).toEqual([]);
    expect(result.removed).toEqual([]);
    expect(result.changed).toEqual([]);
    expect(result.unchanged).toEqual([]);
  });

  it("handles null nodes/edges gracefully", () => {
    const result = diffWorkflowGraphs(
      { nodes: null, edges: null } as unknown as WorkflowGraph,
      graph([nodeA]),
    );
    expect(result.added).toEqual(["a"]);
    expect(result.removed).toEqual([]);
  });

  it("detects added edge", () => {
    const edge1 = { id: "e1", source: "a", target: "b" };
    const result = diffWorkflowGraphs(graph([nodeA, nodeB], []), graph([nodeA, nodeB], [edge1]));
    expect(result.addedEdges).toEqual(["e1"]);
    expect(result.removedEdges).toEqual([]);
  });

  it("detects removed edge", () => {
    const edge1 = { id: "e1", source: "a", target: "b" };
    const result = diffWorkflowGraphs(graph([nodeA, nodeB], [edge1]), graph([nodeA, nodeB], []));
    expect(result.removedEdges).toEqual(["e1"]);
    expect(result.addedEdges).toEqual([]);
  });

  it("all-same graph returns all unchanged", () => {
    const result = diffWorkflowGraphs(graph([nodeA, nodeB, nodeC]), graph([nodeA, nodeB, nodeC]));
    expect(result.unchanged).toHaveLength(3);
    expect(result.changed).toEqual([]);
    expect(result.added).toEqual([]);
    expect(result.removed).toEqual([]);
  });

  it("changedParams only lists differing params, not equal ones", () => {
    const base = { id: "a", type: "set", position: { x: 0, y: 0 }, params: { x: 1, y: 2 } };
    const compare = { id: "a", type: "set", position: { x: 0, y: 0 }, params: { x: 1, y: 99 } };
    const result = diffWorkflowGraphs(graph([base]), graph([compare]));
    expect(result.changedParams["a"]).toHaveLength(1);
    expect(result.changedParams["a"][0].key).toBe("y");
  });
});
