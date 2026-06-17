import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DataPanel } from "./DataPanel";

describe("DataPanel", () => {
  // Regression: opening a node before it has run leaves both the Input and
  // Output panels with `data === undefined`. The Copy payload is computed on
  // every render via `pretty(display)`, and `JSON.stringify(undefined)` returns
  // `undefined` (not a throw) — so reading `.length` on it crashed the render
  // and tripped the ErrorBoundary ("Something went wrong") on every node open.
  it("renders the empty state without throwing when data is undefined", () => {
    expect(() =>
      render(
        <DataPanel
          title="Output"
          data={undefined}
          emptyMessage="This node has not run yet."
        />,
      ),
    ).not.toThrow();
    expect(screen.getByText("This node has not run yet.")).toBeTruthy();
  });

  it("renders the empty state for null data", () => {
    expect(() =>
      render(<DataPanel title="Input" data={null} emptyMessage="No data." />),
    ).not.toThrow();
    expect(screen.getByText("No data.")).toBeTruthy();
  });

  it("renders an object payload as a key/value table", () => {
    // Two keys so `unwrapSingleOutput` doesn't collapse it to a single value.
    render(<DataPanel title="Output" data={{ greeting: "hello", count: 2 }} />);
    expect(screen.getByText("greeting")).toBeTruthy();
    expect(screen.getByText("hello")).toBeTruthy();
  });
});
