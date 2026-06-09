import { fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { describe, expect, it, vi } from "vitest";

import { useModalA11y } from "./useModalA11y";

function Dialog({
  onClose,
  trapFocus,
  enabled,
}: {
  onClose: () => void;
  trapFocus?: boolean;
  enabled?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useModalA11y(ref, onClose, { trapFocus, enabled });
  return (
    <div ref={ref} role="dialog" tabIndex={-1}>
      <button>first</button>
      <button>last</button>
    </div>
  );
}

describe("useModalA11y", () => {
  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(<Dialog onClose={onClose} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("moves focus into the dialog on open", () => {
    render(<Dialog onClose={() => {}} />);
    expect(document.activeElement).toBe(screen.getByText("first"));
  });

  it("restores focus to the previously focused element on close", () => {
    const trigger = document.createElement("button");
    document.body.appendChild(trigger);
    trigger.focus();
    expect(document.activeElement).toBe(trigger);

    const { unmount } = render(<Dialog onClose={() => {}} />);
    expect(document.activeElement).not.toBe(trigger);
    unmount();
    expect(document.activeElement).toBe(trigger);
    trigger.remove();
  });

  it("wraps Tab from the last focusable back to the first", () => {
    render(<Dialog onClose={() => {}} />);
    const first = screen.getByText("first");
    const last = screen.getByText("last");
    last.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(first);
  });

  it("is inert when enabled is false (no Esc, no focus move)", () => {
    const trigger = document.createElement("button");
    document.body.appendChild(trigger);
    trigger.focus();
    const onClose = vi.fn();
    render(<Dialog onClose={onClose} enabled={false} />);
    // Focus stays on the trigger; Escape does nothing.
    expect(document.activeElement).toBe(trigger);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
    trigger.remove();
  });
});
