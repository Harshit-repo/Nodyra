import { describe, expect, it } from "vitest";

import { useEditor } from "./store";

describe("live output streaming", () => {
  it("accumulates node_chunk deltas per node", () => {
    const s = useEditor.getState();
    s.clearRun();
    s.applyRunEvent({ type: "node_started", node_id: "llm" });
    s.applyRunEvent({ type: "node_chunk", node_id: "llm", delta: "Hel" });
    s.applyRunEvent({ type: "node_chunk", node_id: "llm", delta: "lo" });
    expect(useEditor.getState().runChunks["llm"]).toBe("Hello");
  });

  it("clears streamed text when the node restarts and when it finishes", () => {
    const s = useEditor.getState();
    s.clearRun();
    s.applyRunEvent({ type: "node_started", node_id: "llm" });
    s.applyRunEvent({ type: "node_chunk", node_id: "llm", delta: "partial" });
    expect(useEditor.getState().runChunks["llm"]).toBe("partial");

    // The final output supersedes the streamed preview.
    s.applyRunEvent({
      type: "node_finished", node_id: "llm", status: "success",
      outputs: { text: "Hello" },
    });
    expect(useEditor.getState().runChunks["llm"]).toBeUndefined();
  });

  it("a fresh node_started wipes a previous run's streamed text", () => {
    const s = useEditor.getState();
    s.clearRun();
    s.applyRunEvent({ type: "node_chunk", node_id: "llm", delta: "stale" });
    s.applyRunEvent({ type: "node_started", node_id: "llm" });
    expect(useEditor.getState().runChunks["llm"]).toBeUndefined();
  });

  it("clearRun resets streamed text", () => {
    const s = useEditor.getState();
    s.applyRunEvent({ type: "node_chunk", node_id: "x", delta: "z" });
    useEditor.getState().clearRun();
    expect(useEditor.getState().runChunks).toEqual({});
  });
});
