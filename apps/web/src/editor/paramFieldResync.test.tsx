import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { ParamField } from "./NodeDetails";
import type { ParamSpec } from "../types";

// FE-11: JsonField / KeyValueField / RoutesField seed local state once. A node
// *switch* remounts them (ParamField is keyed by node.id:spec.name), but an
// *external* value change to the same mounted node (undo/redo, AI fix, pin
// restore) must resync the field — without clobbering in-progress typing.

const routesSpec: ParamSpec = {
  name: "routes",
  type: "array",
  widget: "routes_table",
} as unknown as ParamSpec;

const jsonSpec: ParamSpec = {
  name: "config",
  type: "object",
} as unknown as ParamSpec;

describe("ParamField external value resync (FE-11)", () => {
  it("RoutesField reflects an external value change (e.g. undo)", () => {
    const { rerender } = render(
      <ParamField
        spec={routesSpec}
        value={[{ method: "GET", path: "/a", output: "r1" }]}
        onChange={() => {}}
      />,
    );
    expect(screen.getByDisplayValue("/a")).toBeTruthy();

    // Simulate an undo: the same mounted field receives a new upstream value.
    rerender(
      <ParamField
        spec={routesSpec}
        value={[{ method: "POST", path: "/b", output: "r1" }]}
        onChange={() => {}}
      />,
    );
    expect(screen.getByDisplayValue("/b")).toBeTruthy();
    expect(screen.queryByDisplayValue("/a")).toBeNull();
  });

  it("JsonField reflects an external value change", () => {
    const { rerender } = render(
      <ParamField spec={jsonSpec} value={{ a: 1 }} onChange={() => {}} />,
    );
    const ta = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(ta.value).toContain('"a": 1');

    rerender(<ParamField spec={jsonSpec} value={{ b: 2 }} onChange={() => {}} />);
    expect(ta.value).toContain('"b": 2');
    expect(ta.value).not.toContain('"a": 1');
  });

  it("JsonField does NOT clobber the buffer while the user is typing in it", () => {
    // Controlled host that mirrors the field's onChange back into value — the
    // real ParamField wiring. Typing must not be reset by the resync effect.
    function Host() {
      const [val, setVal] = useState<unknown>({ a: 1 });
      return <ParamField spec={jsonSpec} value={val} onChange={setVal} />;
    }
    render(<Host />);
    const ta = screen.getByRole("textbox") as HTMLTextAreaElement;
    ta.focus();
    // Type a valid-JSON edit; onChange round-trips a new object into value.
    fireEvent.change(ta, { target: { value: '{ "a": 99 }' } });
    expect(ta.value).toBe('{ "a": 99 }');
  });
});
