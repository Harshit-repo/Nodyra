import { fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";

import { MCPApprovalPage } from "./MCPApprovalPage";
import { mcpApprovalApi, type MCPCommandApproval } from "./mcpApprovalApi";

const request: MCPCommandApproval = {
  id: "a".repeat(32), tool_name: "publish_workflow", status: "pending",
  arguments: { workflow_id: "workflow-1", notes: "Reviewed release" },
  arguments_digest: "b".repeat(64), target: { workflow_name: "Customer notifications", graph_revision: 12 },
  actor_id: "user-1", org_id: "default", correlation_id: "correlation-1",
  expires_at: new Date(Date.now() + 900_000).toISOString(), created_at: new Date().toISOString(),
};

function mount() {
  const router = createMemoryRouter([{ path: "/mcp-approvals/:id", element: <MCPApprovalPage /> }], {
    initialEntries: [`/mcp-approvals/${request.id}`],
  });
  return render(<RouterProvider router={router} />);
}

afterEach(() => vi.restoreAllMocks());

describe("MCP approval review", () => {
  it("requires reviewing the exact request before approval and distinguishes approval from execution", async () => {
    vi.spyOn(mcpApprovalApi, "get").mockResolvedValue(request);
    const decide = vi.spyOn(mcpApprovalApi, "decide").mockResolvedValue({ ...request, status: "approved" });
    mount();
    await screen.findByText("Customer notifications");
    expect(screen.getByRole("button", { name: "Approve this action" })).toBeDisabled();
    expect(screen.getByLabelText("Requested arguments as JSON")).toHaveTextContent("Reviewed release");
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Approve this action" }));
    await screen.findByText(/Approval does not mean the action has executed/);
    expect(decide).toHaveBeenCalledWith(request.id, "approve");
    expect(screen.queryByRole("button", { name: "Approve this action" })).not.toBeInTheDocument();
  });

  it("lets the user deny without acknowledging approval", async () => {
    vi.spyOn(mcpApprovalApi, "get").mockResolvedValue(request);
    const decide = vi.spyOn(mcpApprovalApi, "decide").mockResolvedValue({ ...request, status: "denied" });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Deny request" }));
    await screen.findByText(/Request denied/);
    expect(decide).toHaveBeenCalledWith(request.id, "deny");
  });

  it("never offers approval for an expired request", async () => {
    vi.spyOn(mcpApprovalApi, "get").mockResolvedValue({ ...request, expires_at: "2020-01-01T00:00:00Z" });
    mount();
    await screen.findByText(/This request expired/);
    expect(screen.queryByRole("button", { name: "Approve this action" })).not.toBeInTheDocument();
  });

  it("explains authentication or workspace failures", async () => {
    vi.spyOn(mcpApprovalApi, "get").mockRejectedValue(new Error("Sign in to Nodyra in your browser to review this action."));
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("Sign in to Nodyra");
    expect(screen.getByText(/select its workspace/)).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    vi.spyOn(mcpApprovalApi, "get").mockResolvedValue(request);
    const { container } = mount();
    await screen.findByText("Customer notifications");
    expect((await axe(container)).violations).toEqual([]);
  });
});
