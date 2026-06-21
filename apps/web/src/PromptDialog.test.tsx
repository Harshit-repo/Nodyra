import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PromptDialog } from "./PromptDialog";

const base = {
  title: "New organization",
  label: "Name",
  placeholder: "Acme Inc.",
  onCancel: vi.fn(),
  onConfirm: vi.fn(),
};

describe("PromptDialog", () => {
  it("renders title, label, input, Cancel and Confirm buttons", () => {
    render(<PromptDialog {...base} />);
    expect(screen.getByText("New organization")).toBeTruthy();
    expect(screen.getByLabelText("Name")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeTruthy();
  });

  it("has role=dialog and aria-modal=true", () => {
    render(<PromptDialog {...base} />);
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
  });

  it("calls onConfirm with typed value when Confirm is clicked", () => {
    const onConfirm = vi.fn();
    render(<PromptDialog {...base} onConfirm={onConfirm} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Acme" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onConfirm).toHaveBeenCalledWith("Acme");
  });

  it("calls onConfirm with typed value when Enter is pressed in input", () => {
    const onConfirm = vi.fn();
    render(<PromptDialog {...base} onConfirm={onConfirm} />);
    const input = screen.getByLabelText("Name");
    fireEvent.change(input, { target: { value: "Acme" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onConfirm).toHaveBeenCalledWith("Acme");
  });

  it("calls onCancel when Cancel is clicked", () => {
    const onCancel = vi.fn();
    render(<PromptDialog {...base} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("renders optional body text when provided", () => {
    render(<PromptDialog {...base} body="Enter a unique name." />);
    expect(screen.getByText("Enter a unique name.")).toBeTruthy();
  });

  it("uses custom confirmLabel when provided", () => {
    render(<PromptDialog {...base} confirmLabel="Create" />);
    expect(screen.getByRole("button", { name: "Create" })).toBeTruthy();
  });
});
