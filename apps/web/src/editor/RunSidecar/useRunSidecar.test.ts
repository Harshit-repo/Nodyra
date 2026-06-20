import { renderHook, act } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useRunSidecar } from "./useRunSidecar";

describe("useRunSidecar", () => {
  it("initialises with runs tab and no selection", () => {
    const { result } = renderHook(() => useRunSidecar());
    expect(result.current.activeTab).toBe("runs");
    expect(result.current.selectedRunId).toBeNull();
    expect(result.current.pinnedNodeId).toBeNull();
    expect(result.current.diffPair).toBeNull();
  });

  it("updates activeTab", () => {
    const { result } = renderHook(() => useRunSidecar());
    act(() => result.current.setActiveTab("diff"));
    expect(result.current.activeTab).toBe("diff");
  });

  it("updates selectedRunId and clears pinnedNodeId", () => {
    const { result } = renderHook(() => useRunSidecar());
    act(() => result.current.setPinnedNodeId("node-1"));
    act(() => result.current.setSelectedRunId("run-abc"));
    expect(result.current.selectedRunId).toBe("run-abc");
    expect(result.current.pinnedNodeId).toBeNull();
  });

  it("stores diffPair", () => {
    const { result } = renderHook(() => useRunSidecar());
    act(() => result.current.setDiffPair(["run-a", "run-b"]));
    expect(result.current.diffPair).toEqual(["run-a", "run-b"]);
  });
});
