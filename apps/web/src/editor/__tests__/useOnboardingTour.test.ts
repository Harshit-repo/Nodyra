import { describe, it, expect, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useOnboardingTour, TOUR_STEPS } from "../useOnboardingTour";

beforeEach(() => {
  localStorage.clear();
});

describe("useOnboardingTour", () => {
  it("starts active when no localStorage key is set", () => {
    const { result } = renderHook(() => useOnboardingTour());
    expect(result.current.isActive).toBe(true);
    expect(result.current.currentStep).toBe(0);
  });

  it("starts inactive when localStorage key is set", () => {
    localStorage.setItem("noodle-editor-tour-v1", "true");
    const { result } = renderHook(() => useOnboardingTour());
    expect(result.current.isActive).toBe(false);
  });

  it("advances to next step", () => {
    const { result } = renderHook(() => useOnboardingTour());
    act(() => result.current.next());
    expect(result.current.currentStep).toBe(1);
  });

  it("completes tour on last step next()", () => {
    const { result } = renderHook(() => useOnboardingTour());
    for (let i = 0; i < TOUR_STEPS.length - 1; i++) {
      act(() => result.current.next());
    }
    expect(result.current.isLastStep).toBe(true);
    act(() => result.current.next());
    expect(result.current.isActive).toBe(false);
    expect(localStorage.getItem("noodle-editor-tour-v1")).toBe("true");
  });

  it("goes to previous step", () => {
    const { result } = renderHook(() => useOnboardingTour());
    act(() => result.current.next());
    act(() => result.current.prev());
    expect(result.current.currentStep).toBe(0);
  });

  it("does not go below step 0", () => {
    const { result } = renderHook(() => useOnboardingTour());
    act(() => result.current.prev());
    expect(result.current.currentStep).toBe(0);
  });

  it("skip dismisses permanently", () => {
    const { result } = renderHook(() => useOnboardingTour());
    act(() => result.current.skip());
    expect(result.current.isActive).toBe(false);
    expect(localStorage.getItem("noodle-editor-tour-v1")).toBe("true");
  });

  it("restart clears localStorage and resets", () => {
    localStorage.setItem("noodle-editor-tour-v1", "true");
    const { result } = renderHook(() => useOnboardingTour());
    expect(result.current.isActive).toBe(false);
    act(() => result.current.restart());
    expect(result.current.isActive).toBe(true);
    expect(result.current.currentStep).toBe(0);
    expect(localStorage.getItem("noodle-editor-tour-v1")).toBeNull();
  });

  it("TOUR_STEPS has 5 steps", () => {
    expect(TOUR_STEPS).toHaveLength(5);
  });

  it("exposes first/last step flags", () => {
    const { result } = renderHook(() => useOnboardingTour());
    expect(result.current.isFirstStep).toBe(true);
    expect(result.current.isLastStep).toBe(false);
  });
});
