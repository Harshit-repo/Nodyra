import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WorkflowSettingsModal } from "./WorkflowSettingsModal";

const baseProps = {
  runTimeout: "",
  onRunTimeoutChange: vi.fn(),
  artifactRetentionDays: "",
  onArtifactRetentionDaysChange: vi.fn(),
  executionMode: "inherit" as const,
  onExecutionModeChange: vi.fn(),
  sandboxResources: {},
  onSandboxResourcesChange: vi.fn(),
  requirementsText: "",
  onRequirementsTextChange: vi.fn(),
  mcpEnabled: false,
  onMcpEnabledChange: vi.fn(),
  mcpToolName: "",
  onMcpToolNameChange: vi.fn(),
  mcpDescription: "",
  onMcpDescriptionChange: vi.fn(),
  onClose: vi.fn(),
};

describe("WorkflowSettingsModal", () => {
  it("shows the run timeout field", () => {
    render(<WorkflowSettingsModal {...baseProps} />);
    expect(screen.getByLabelText(/run timeout/i)).toBeInTheDocument();
  });

  it("updates artifact retention days", () => {
    const onArtifactRetentionDaysChange = vi.fn();
    render(
      <WorkflowSettingsModal
        {...baseProps}
        onArtifactRetentionDaysChange={onArtifactRetentionDaysChange}
      />,
    );
    fireEvent.change(screen.getByLabelText(/artifact retention/i), {
      target: { value: "7" },
    });
    expect(onArtifactRetentionDaysChange).toHaveBeenCalledWith("7");
  });

  it("calls onMcpEnabledChange when MCP toggled", async () => {
    const onMcpEnabledChange = vi.fn();
    render(<WorkflowSettingsModal {...baseProps} onMcpEnabledChange={onMcpEnabledChange} />);
    await userEvent.click(screen.getByLabelText(/expose as mcp tool/i));
    expect(onMcpEnabledChange).toHaveBeenCalledWith(true);
  });

  it("hides MCP tool name/description until MCP is enabled", () => {
    const { rerender } = render(<WorkflowSettingsModal {...baseProps} mcpEnabled={false} />);
    expect(screen.queryByLabelText(/tool name/i)).toBeNull();
    rerender(<WorkflowSettingsModal {...baseProps} mcpEnabled={true} />);
    expect(screen.getByLabelText(/tool name/i)).toBeInTheDocument();
  });

  it("calls onExecutionModeChange when isolation mode changes", async () => {
    const onExecutionModeChange = vi.fn();
    render(
      <WorkflowSettingsModal
        {...baseProps}
        onExecutionModeChange={onExecutionModeChange}
      />,
    );
    await userEvent.selectOptions(screen.getByLabelText(/execution isolation/i), "sandboxed");
    expect(onExecutionModeChange).toHaveBeenCalledWith("sandboxed");
  });

  it("shows sandbox resources and updates memory", async () => {
    const onSandboxResourcesChange = vi.fn();
    render(
      <WorkflowSettingsModal
        {...baseProps}
        executionMode="sandboxed"
        onSandboxResourcesChange={onSandboxResourcesChange}
      />,
    );
    fireEvent.change(screen.getByLabelText(/sandbox memory/i), {
      target: { value: "2048" },
    });
    expect(onSandboxResourcesChange).toHaveBeenLastCalledWith({ memory_mb: 2048 });
  });

  it("updates workflow requirements text", () => {
    const onRequirementsTextChange = vi.fn();
    render(
      <WorkflowSettingsModal
        {...baseProps}
        onRequirementsTextChange={onRequirementsTextChange}
      />,
    );
    fireEvent.change(screen.getByLabelText(/python requirements/i), {
      target: { value: "pandas>=2.0\nrequests" },
    });
    expect(onRequirementsTextChange).toHaveBeenCalledWith("pandas>=2.0\nrequests");
  });
});
