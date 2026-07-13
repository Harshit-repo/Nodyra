import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkflowCheckCase, WorkflowCheckInfo } from "../types";
import { WorkflowChecksPanel } from "./WorkflowChecksPanel";

const apiMocks = vi.hoisted(() => ({
  listWorkflowChecks: vi.fn(),
  generateWorkflowTests: vi.fn(),
  saveWorkflowChecks: vi.fn(),
  runWorkflowChecks: vi.fn(),
  runWorkflowCheck: vi.fn(),
  deleteWorkflowCheck: vi.fn(),
}));

vi.mock("../api", () => ({
  api: apiMocks,
  userFriendlyError: (error: unknown) =>
    error instanceof Error ? error.message : String(error),
}));

const generatedCase: WorkflowCheckCase = {
  name: "Happy path",
  input_data: { customer: "Ada" },
  expected_outputs: { "send.main": { ok: true } },
  assertions: ["status == success"],
};

const savedCheck: WorkflowCheckInfo = {
  ...generatedCase,
  id: "check-1",
  workflow_id: "wf-1",
  status: "untested",
  last_result: null,
  last_run_at: null,
  created_at: "2026-07-03T09:00:00.000Z",
  updated_at: "2026-07-03T09:00:00.000Z",
};

function renderPanel() {
  return render(<WorkflowChecksPanel workflowId="wf-1" onClose={vi.fn()} />);
}

describe("WorkflowChecksPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.listWorkflowChecks.mockResolvedValue([]);
    apiMocks.generateWorkflowTests.mockResolvedValue({ tests: [generatedCase] });
    apiMocks.saveWorkflowChecks.mockResolvedValue([savedCheck]);
    apiMocks.runWorkflowChecks.mockResolvedValue([
      {
        check: {
          ...savedCheck,
          status: "passed",
          last_run_at: "2026-07-03T09:05:00.000Z",
          last_result: {
            passed: true,
            status: "success",
            failures: [],
            node_outputs: { send: { main: { ok: true } } },
          },
        },
        passed: true,
        status: "success",
        failures: [],
        node_outputs: { send: { main: { ok: true } } },
      },
    ]);
  });

  it("generates and saves workflow checks", async () => {
    renderPanel();

    expect(await screen.findByText("No workflow checks saved.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /generate tests/i }));

    expect(await screen.findByText("Happy path")).toBeInTheDocument();
    expect(screen.getByText("1 generated check ready.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /save generated checks/i }));

    await waitFor(() =>
      expect(apiMocks.saveWorkflowChecks).toHaveBeenCalledWith("wf-1", {
        checks: [generatedCase],
        replace: false,
      }),
    );
    expect(await screen.findByText("1 check saved.")).toBeInTheDocument();
    expect(screen.getByText("1 saved")).toBeInTheDocument();
  });

  it("runs saved workflow checks and shows pass status", async () => {
    apiMocks.listWorkflowChecks.mockResolvedValue([savedCheck]);
    renderPanel();

    expect(await screen.findByText("Happy path")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /run checks/i }));

    await waitFor(() =>
      expect(apiMocks.runWorkflowChecks).toHaveBeenCalledWith("wf-1"),
    );
    expect(await screen.findByText("1 check passed.")).toBeInTheDocument();
    expect(screen.getByText("passed")).toBeInTheDocument();
  });
});
