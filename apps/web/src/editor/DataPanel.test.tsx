import { fireEvent, render, screen } from "@testing-library/react";
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

describe("DataPanel schema view", () => {
  it("shows type icon T for string fields", () => {
    render(
      <DataPanel
        title="Input"
        data={{ name: "Alice", age: 30 }}
        dragPrefix="$json"
      />,
    );
    // Switch to schema view — it's the default when dragPrefix is set and data is an object
    expect(screen.getByText("T")).toBeTruthy();
  });

  it("shows type icon # for number fields", () => {
    // Two keys required so unwrapSingleOutput does not collapse the object to
    // a bare primitive (which would bypass schema view entirely).
    render(
      <DataPanel
        title="Input"
        data={{ count: 42, _type: "num" }}
        dragPrefix="$json"
      />,
    );
    expect(screen.getByText("#")).toBeTruthy();
  });

  it("shows type icon ⊤ for boolean fields", () => {
    render(
      <DataPanel
        title="Input"
        data={{ active: true, _type: "bool" }}
        dragPrefix="$json"
      />,
    );
    expect(screen.getByText("⊤")).toBeTruthy();
  });

  it("shows inline value for primitive string fields", () => {
    render(
      <DataPanel
        title="Input"
        data={{ city: "London", _type: "str" }}
        dragPrefix="$json"
      />,
    );
    expect(screen.getByText("London")).toBeTruthy();
  });

  it("shows inline value for number fields", () => {
    render(
      <DataPanel
        title="Input"
        data={{ score: 99, _type: "num" }}
        dragPrefix="$json"
      />,
    );
    expect(screen.getByText("99")).toBeTruthy();
  });

  it("truncates long string values to 40 chars with ellipsis", () => {
    const longStr = "a".repeat(60);
    render(
      <DataPanel
        title="Input"
        data={{ note: longStr, _type: "str" }}
        dragPrefix="$json"
      />,
    );
    expect(screen.getByText("a".repeat(40) + "…")).toBeTruthy();
  });

  it("does not show inline value for object fields", () => {
    render(
      <DataPanel
        title="Input"
        data={{ meta: { x: 1 }, _type: "obj" }}
        dragPrefix="$json"
      />,
    );
    // The key "meta" appears but no inline value preview for objects
    expect(screen.getByText("meta")).toBeTruthy();
    expect(screen.queryByText('{"x":1}')).toBeNull();
  });
});

describe("DataPanel coerce preview (output panel)", () => {
  it("shows coerce selector when title starts with Output and data is present", () => {
    render(<DataPanel title="Output" data={{ score: "42" }} />);
    expect(screen.getByRole("combobox", { name: /coerce/i })).toBeTruthy();
  });

  it("does not show coerce selector when dragPrefix is set (input panel)", () => {
    render(
      <DataPanel title="Output" data={{ score: "42" }} dragPrefix="$json" />,
    );
    expect(screen.queryByRole("combobox", { name: /coerce/i })).toBeNull();
  });

  it("does not show coerce selector when data is empty", () => {
    render(<DataPanel title="Output" data={undefined} />);
    expect(screen.queryByRole("combobox", { name: /coerce/i })).toBeNull();
  });

  it("shows numeric coerced value when 'number' coerce is selected", async () => {
    render(<DataPanel title="Output" data="99.5" />);
    const sel = screen.getByRole("combobox", { name: /coerce/i });
    fireEvent.change(sel, { target: { value: "number" } });
    // The coerced value (99.5) should now appear in the view
    expect(screen.getByText("99.5")).toBeTruthy();
  });
});
