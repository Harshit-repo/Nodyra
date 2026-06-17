import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WorkflowSettingsModal } from "./WorkflowSettingsModal";

const baseProps = {
  runTimeout: "",
  onRunTimeoutChange: vi.fn(),
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
});
