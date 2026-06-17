import { describe, expect, it, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { useEditor } from "./store";
import { MetanodeBreadcrumb } from "./MetanodeBreadcrumb";
import type { DrillFrame } from "./store/drillSlice";

beforeEach(() => {
  useEditor.setState({ drillStack: [], drillOrig: {}, drillPortSeq: 1 });
});

function frame(metaId: string, name: string): DrillFrame {
  return { metaId, name, nodes: [], edges: [], _past: [], _future: [], orig: {}, portSeq: 1 };
}

describe("MetanodeBreadcrumb", () => {
  it("renders Workflow + each level and pops on click", () => {
    useEditor.setState({ drillStack: [frame("A", "Alpha"), frame("B", "Beta")] });
    render(<MetanodeBreadcrumb />);
    expect(screen.getByText("Workflow")).toBeTruthy();
    expect(screen.getByText("Alpha")).toBeTruthy();
    expect(screen.getByText("Beta")).toBeTruthy();
    fireEvent.click(screen.getByText("Workflow"));
    expect(useEditor.getState().drillStack).toHaveLength(0);
  });
});
