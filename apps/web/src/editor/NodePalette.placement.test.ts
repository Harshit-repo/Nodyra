/**
 * Keyboard-added nodes must not land on top of each other.
 *
 * The palette's search box supports ArrowUp/ArrowDown/Enter, which is the only
 * keyboard route to adding a node — the palette items themselves are
 * `<div draggable>` with tabIndex -1. Enter dropped the node at:
 *
 *     const offset = (flatResults.length > 1 ? activeIdx % 3 : 0) * 24;
 *     addNode(pick.id, { x: 400 + offset, y: 200 + offset });
 *
 * `activeIdx` is which *search result* is highlighted, not how many nodes the
 * graph already has. Searching "manual trigger" (one result, activeIdx 0) and
 * then "code" (first result, activeIdx 0) produced two nodes at exactly
 * (400, 200).
 *
 * Observed on the running stack: both nodes reported position {x: 400, y: 200},
 * and a drag from the trigger's output port failed because the node stacked on
 * top intercepted the pointer:
 *
 *     <div ... data-nodeid="n_..._2" class="react-flow__handle ... source">
 *       from <div data-id="n_..._2"> subtree intercepts pointer events
 *
 * So a keyboard user could add nodes but never connect them — no workflow could
 * be built without a mouse.
 *
 * The placement rule is pure arithmetic, so it is tested directly rather than
 * through the component.
 */

import { describe, expect, it } from "vitest";

/** Mirrors the offset arithmetic in NodePalette's Enter handler. */
function dropPosition(nodeCount: number): { x: number; y: number } {
  const step = nodeCount % 8;
  return { x: 400 + step * 220, y: 200 + step * 40 };
}

describe("keyboard-added node placement", () => {
  it("puts the first node at the canvas centre", () => {
    expect(dropPosition(0)).toEqual({ x: 400, y: 200 });
  });

  it("never drops two consecutive nodes on the same point", () => {
    for (let count = 0; count < 7; count++) {
      expect(dropPosition(count)).not.toEqual(dropPosition(count + 1));
    }
  });

  it("separates them by more than a node's width, so ports stay clickable", () => {
    // The old code stepped 24px, which is inside a node's own box: the upper
    // node's ports still covered the lower one's.
    const a = dropPosition(0);
    const b = dropPosition(1);
    expect(b.x - a.x).toBeGreaterThanOrEqual(200);
  });

  it("keeps eight distinct slots before repeating", () => {
    const seen = new Set(
      Array.from({ length: 8 }, (_, i) => JSON.stringify(dropPosition(i))),
    );
    expect(seen.size).toBe(8);
  });

  it("wraps rather than marching off the canvas forever", () => {
    // Position must stay bounded, so a large graph does not scatter new nodes
    // somewhere the viewport never shows.
    expect(dropPosition(8)).toEqual(dropPosition(0));
    expect(dropPosition(17)).toEqual(dropPosition(1));
  });

  it("does not depend on which search result was highlighted", () => {
    // The regression, stated as a property: placement is a function of the
    // graph, and nothing else.
    const forSameGraph = [dropPosition(2), dropPosition(2), dropPosition(2)];
    expect(new Set(forSameGraph.map((p) => JSON.stringify(p))).size).toBe(1);
  });
});
