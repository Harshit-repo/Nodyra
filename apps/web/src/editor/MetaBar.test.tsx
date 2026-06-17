import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReactFlowProvider } from "@xyflow/react";

import { MetaBar } from "./MetaBar";

function renderBar(bar: "input" | "output", ports: { id: string; label: string }[]) {
  const props = {
    id: bar,
    type: "metaBar",
    data: { bar, ports },
    selected: false,
    zIndex: 0,
    isConnectable: true,
  } as unknown as Parameters<typeof MetaBar>[0];
  return render(
    <ReactFlowProvider>
      <MetaBar {...props} />
    </ReactFlowProvider>,
  );
}

describe("MetaBar", () => {
  it("renders one labelled port per entry and an add button", () => {
    renderBar("input", [{ id: "in_0", label: "in_0" }, { id: "in_1", label: "in_1" }]);
    expect(screen.getByText("in_0")).toBeTruthy();
    expect(screen.getByText("in_1")).toBeTruthy();
    expect(screen.getByTitle(/add input port/i)).toBeTruthy();
  });

  it("shows the data-kind label for typed ports", () => {
    renderBar("output", [{ id: "out_0", label: "out_0", data_kind: "dataset" } as never]);
    expect(screen.getByText("DatasetRef")).toBeTruthy();
  });
});
