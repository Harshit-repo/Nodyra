import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useTimeout } from "./useTimeout";

afterEach(() => {
  vi.useRealTimers();
});

describe("useTimeout", () => {
  it("runs scheduled work while the component remains mounted", () => {
    vi.useFakeTimers();
    const callback = vi.fn();
    const { result } = renderHook(() => useTimeout());

    act(() => {
      result.current(callback, 100);
      vi.advanceTimersByTime(100);
    });

    expect(callback).toHaveBeenCalledOnce();
  });

  it("cancels scheduled work when the component unmounts", () => {
    vi.useFakeTimers();
    const callback = vi.fn();
    const { result, unmount } = renderHook(() => useTimeout());

    act(() => {
      result.current(callback, 100);
    });
    unmount();
    act(() => vi.advanceTimersByTime(100));

    expect(callback).not.toHaveBeenCalled();
  });
});
