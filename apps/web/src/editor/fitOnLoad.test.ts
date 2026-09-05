import { describe, expect, it } from "vitest";

import { shouldFitOnLoad } from "./fitOnLoad";

/**
 * Opening a workflow showed only part of its graph.
 *
 * <ReactFlow fitView> fits once, on the render where it mounts. The graph is
 * fetched after that, so the fit ran against an empty or partly-loaded node
 * list. With maxZoom={2} the view then sat at 2x centred on nothing, and
 * onlyRenderVisibleElements culled everything outside it.
 *
 * Measured in a browser on a shipped 3-node template at 1920x1080: two nodes
 * rendered, and the viewport transform was translate(636px, ...) scale(2) -
 * exactly (1416 - 144) / 2, the canvas centred on a single node. Clicking
 * "fit view" afterwards did not help, because by then React Flow considered
 * the view already fitted.
 *
 * So the canvas has to fit again when a workflow's nodes first arrive - once
 * per workflow, never again, or it would yank the viewport away from someone
 * mid-edit every time they add a node.
 */
describe("shouldFitOnLoad", () => {
  it("fits when a workflow's nodes first arrive", () => {
    expect(shouldFitOnLoad({ workflowId: "wf1", fittedFor: null }, "wf1", 3, true)).toBe(true);
  });

  it("does not fit while the graph is still empty", () => {
    expect(shouldFitOnLoad({ workflowId: "wf1", fittedFor: null }, "wf1", 0, true)).toBe(false);
  });

  it("does not fit a second time for the same workflow", () => {
    expect(shouldFitOnLoad({ workflowId: "wf1", fittedFor: "wf1" }, "wf1", 3, true)).toBe(false);
  });

  it("does not re-fit when the user adds a node", () => {
    // The case that makes a naive "fit whenever the count changes" unusable:
    // the viewport would jump on every single edit.
    expect(shouldFitOnLoad({ workflowId: "wf1", fittedFor: "wf1" }, "wf1", 4, true)).toBe(false);
    expect(shouldFitOnLoad({ workflowId: "wf1", fittedFor: "wf1" }, "wf1", 12, true)).toBe(false);
  });

  it("waits until React Flow has measured the nodes", () => {
    // The reason the first attempt at this fix did nothing: firing on the node
    // count alone runs before measurement, so fitView still sees one node and
    // computes the same wrong viewport.
    expect(shouldFitOnLoad({ workflowId: "wf1", fittedFor: null }, "wf1", 3, false)).toBe(false);
  });

  it("fits again after navigating to a different workflow", () => {
    expect(shouldFitOnLoad({ workflowId: "wf2", fittedFor: "wf1" }, "wf2", 5, true)).toBe(true);
  });

  it("does nothing before a workflow is selected", () => {
    expect(shouldFitOnLoad({ workflowId: null, fittedFor: null }, null, 0, true)).toBe(false);
    expect(shouldFitOnLoad({ workflowId: null, fittedFor: null }, null, 3, true)).toBe(false);
  });
});
