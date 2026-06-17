import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider, useToast } from "./ToastProvider";

function Trigger({ tone }: { tone: "success" | "error" | "info" }) {
  const { notify } = useToast();
  return (
    <button onClick={() => notify(`${tone} message`, tone)}>fire</button>
  );
}

function setup(tone: "success" | "error" | "info") {
  return render(
    <ToastProvider>
      <Trigger tone={tone} />
    </ToastProvider>,
  );
}

describe("ToastProvider", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it("auto-dismisses a success toast after its TTL", () => {
    setup("success");
    fireEvent.click(screen.getByText("fire"));
    expect(screen.getByText("success message")).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(screen.queryByText("success message")).toBeNull();
  });

  it("keeps an error toast sticky (manual dismiss only)", () => {
    setup("error");
    fireEvent.click(screen.getByText("fire"));
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.getByText("error message")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss notification" }));
    expect(screen.queryByText("error message")).toBeNull();
  });

  it("pauses the countdown while hovered", () => {
    setup("success");
    fireEvent.click(screen.getByText("fire"));
    const toast = screen.getByText("success message").closest(".toast")!;
    fireEvent.mouseEnter(toast);
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    // Still present despite passing the TTL — the timer was cleared on hover.
    expect(screen.getByText("success message")).toBeTruthy();
  });
});
