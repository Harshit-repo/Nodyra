/**
 * The edge selector must not return freshly-built objects.
 *
 * `NodyraEdge` selected from the editor store with:
 *
 *   useEditor(useShallow((s) => ({
 *     edgeType: deriveEdgeType(...),   // returns a NEW object every call
 *     preview: edgePreviewValue(...),  // returns a NEW object every call
 *   })))
 *
 * `useShallow` compares the returned object one level deep, so it compared
 * `edgeType` and `preview` **by reference**. Both are new on every call, so the
 * comparison never reported equality: zustand saw a state change on every
 * render, re-rendered, produced two more new objects, and looped.
 *
 * React stopped it with "Maximum update depth exceeded" and the editor fell
 * through to the error boundary — "Something went wrong. The page hit an
 * unexpected error." — on the *first* workflow a new user opens, because the
 * activation checklist links straight there.
 *
 * These tests pin the property that broke rather than the symptom: the value a
 * store selector returns must be referentially stable while the store is
 * unchanged.
 */

import { describe, expect, it } from "vitest";

import { deriveEdgeType } from "./NodyraEdge";
import { edgePreviewValue } from "./EdgePreview";

describe("the helpers really do allocate (why useShallow could not save us)", () => {
  it("deriveEdgeType returns a new object each call", () => {
    // Guard the guard: if these ever become memoised, the selector shape that
    // caused the loop would look safe and this suite would lose its point.
    expect(deriveEdgeType("x")).not.toBe(deriveEdgeType("x"));
    expect(deriveEdgeType("x")).toEqual(deriveEdgeType("x"));
  });

  it("edgePreviewValue returns a new object each call", () => {
    const outputs = { main: 42 };
    expect(edgePreviewValue(outputs, "main")).not.toBe(
      edgePreviewValue(outputs, "main"),
    );
    expect(edgePreviewValue(outputs, "main")).toEqual(
      edgePreviewValue(outputs, "main"),
    );
  });
});

/**
 * A faithful stand-in for zustand's `useShallow` comparison, so the test does
 * not depend on the store or on React rendering to demonstrate the loop.
 */
function shallowEqual(a: Record<string, unknown>, b: Record<string, unknown>) {
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  return ka.every((k) => Object.is(a[k], b[k]));
}

describe("edge selector stability", () => {
  const state = { runOutputs: { n1: { main: [1, 2, 3] } } as Record<string, unknown> };

  it("the old selector shape never settles, which is the infinite loop", () => {
    const oldSelector = (s: typeof state) => {
      const outputs = s.runOutputs.n1;
      const previewValue = edgePreviewValue(outputs, "main");
      return {
        edgeType: deriveEdgeType(previewValue.hasValue ? previewValue.value : undefined),
        preview: previewValue,
      };
    };

    // Same store, two reads. useShallow still reports "changed", so zustand
    // schedules another render, forever.
    expect(shallowEqual(oldSelector(state), oldSelector(state))).toBe(false);
  });

  it("selecting the stored value instead is stable across reads", () => {
    // The fix: pull the value the store already holds — a stable reference —
    // and derive the display objects outside the selector, under useMemo.
    const newSelector = (s: typeof state) => s.runOutputs.n1;

    expect(Object.is(newSelector(state), newSelector(state))).toBe(true);
  });

  it("the stable selector still changes when the run output actually changes", () => {
    // Stability must not become blindness: a real new output has to be seen.
    const before = { runOutputs: { n1: { main: [1] } } as Record<string, unknown> };
    const after = { runOutputs: { n1: { main: [1, 2] } } as Record<string, unknown> };
    const select = (s: typeof before) => s.runOutputs.n1;

    expect(Object.is(select(before), select(after))).toBe(false);
  });

  it("a missing source node selects undefined, stably", () => {
    const select = (s: typeof state) => s.runOutputs.missing;
    expect(select(state)).toBeUndefined();
    expect(Object.is(select(state), select(state))).toBe(true);
  });
});
