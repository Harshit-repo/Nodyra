import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useAutosave } from "./useAutosave";

describe("useAutosave", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("fires onSave after the debounce once dirty", () => {
    const onSave = vi.fn();
    renderHook(() => useAutosave({ enabled: true, dirty: true, delayMs: 1500, onSave }));
    expect(onSave).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1500);
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it("does not fire when not dirty", () => {
    const onSave = vi.fn();
    renderHook(() => useAutosave({ enabled: true, dirty: false, delayMs: 1500, onSave }));
    vi.advanceTimersByTime(5000);
    expect(onSave).not.toHaveBeenCalled();
  });

  it("does not fire when disabled", () => {
    const onSave = vi.fn();
    renderHook(() => useAutosave({ enabled: false, dirty: true, delayMs: 1500, onSave }));
    vi.advanceTimersByTime(5000);
    expect(onSave).not.toHaveBeenCalled();
  });

  it("resets the timer when dirty toggles (coalesces rapid edits)", () => {
    const onSave = vi.fn();
    const { rerender } = renderHook(
      ({ d }) => useAutosave({ enabled: true, dirty: d, delayMs: 1500, onSave }),
      { initialProps: { d: true } },
    );
    vi.advanceTimersByTime(1000);
    rerender({ d: false }); // a save cleaned it
    rerender({ d: true });  // new edit
    vi.advanceTimersByTime(1000);
    expect(onSave).not.toHaveBeenCalled(); // not yet — timer restarted
    vi.advanceTimersByTime(500);
    expect(onSave).toHaveBeenCalledTimes(1);
  });
});
