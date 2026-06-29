import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { ReactFlowProvider } from "@xyflow/react";
import { GraphDiffView } from "../WorkflowDiffView";
import type { WorkflowGraph, GraphNode } from "../../types";

function makeNode(id: string, type: string, x: number, y: number, label: string): GraphNode {
  return {
    id, type, position: { x, y },
    params: { name: label },
    disabled: false, outputs_override: null, on_error: "stop",
    retry_on_fail: false, retries: 0, retry_wait_seconds: 0,
    retry_backoff: false, always_output_data: false, timeout_seconds: null,
  };
}

const baseGraph: WorkflowGraph = {
  nodes: [makeNode("a", "http_request", 0, 0, "HTTP"), makeNode("b", "code", 200, 0, "Code")],
  edges: [{ id: "a->b", source: "a", source_output: "main", target: "b", target_input: "main" }],
};

const compareGraph: WorkflowGraph = {
  nodes: [makeNode("a", "http_request", 0, 0, "HTTP (modified)"), makeNode("c", "slack", 400, 0, "Slack")],
  edges: [{ id: "a->c", source: "a", source_output: "main", target: "c", target_input: "main" }],
};

function renderWithProvider(ui: React.ReactElement) {
  return render(<ReactFlowProvider>{ui}</ReactFlowProvider>);
}

describe("GraphDiffView", () => {
  it("renders without crashing", () => {
    const { container } = renderWithProvider(
      <GraphDiffView baseGraph={baseGraph} compareGraph={compareGraph} />,
    );
    // ReactFlow renders a .react-flow container
    expect(container.querySelector(".react-flow")).toBeTruthy();
  });

  it("shows diff summary bar with change counts", () => {
    renderWithProvider(
      <GraphDiffView baseGraph={baseGraph} compareGraph={compareGraph} />,
    );
    // DiffSummaryBar renders the number of added/removed/changed items
    expect(document.body.textContent).toMatch(/added/i);
    expect(document.body.textContent).toMatch(/removed/i);
  });

  it("renders close button when onClose provided", () => {
    const onClose = () => {};
    renderWithProvider(
      <GraphDiffView baseGraph={baseGraph} compareGraph={compareGraph} onClose={onClose} />,
    );
    expect(document.body.textContent).toMatch(/×/);
  });

  it("handles identical graphs (no changes)", () => {
    renderWithProvider(
      <GraphDiffView baseGraph={baseGraph} compareGraph={baseGraph} />,
    );
    // Should still render without error
    const reactFlow = document.querySelector(".react-flow");
    expect(reactFlow).toBeTruthy();
  });
});
