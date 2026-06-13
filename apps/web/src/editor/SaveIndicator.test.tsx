import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SaveIndicator } from "./SaveIndicator";

describe("SaveIndicator", () => {
  it("shows Saving… while saving", () => {
    render(<SaveIndicator state="saving" />);
    expect(screen.getByText(/saving…/i)).toBeInTheDocument();
  });

  it("shows saved text when saved", () => {
    render(<SaveIndicator state="saved" />);
    expect(screen.getByText(/all changes saved/i)).toBeInTheDocument();
  });

  it("shows unsaved text when there are pending edits", () => {
    render(<SaveIndicator state="unsaved" />);
    expect(screen.getByText(/unsaved/i)).toBeInTheDocument();
  });

  it("shows a retry button on error and calls onRetry", async () => {
    const onRetry = vi.fn();
    render(<SaveIndicator state="error" onRetry={onRetry} />);
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
