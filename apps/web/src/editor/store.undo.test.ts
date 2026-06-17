import { describe, expect, it } from "vitest";

import type { NodeManifest, PortSpec, WorkflowGraph } from "../types";
import { useEditor } from "./store";

function port(name: string, data_kind: PortSpec["data_kind"] = "any"): PortSpec {
  return { name, description: "", data_kind };
}
function codeManifest(): NodeManifest {
  return {
    id: "code", name: "Code", category: "Core", version: "1", description: "",
    icon: null, inputs: [port("input")], outputs: [port("main")], params: [],
  };
}
function chainGraph(): WorkflowGraph {
  const mk = (id: string) => ({
    id, type: "code", params: {}, position: { x: 0, y: 0 },
    disabled: false, outputs_override: null, on_error: "stop",
    retry_on_fail: false, retries: 1, retry_wait_seconds: 0, retry_backoff: false,
    always_output_data: false, timeout_seconds: null,
  });
  const ed = (s: string, t: string) => ({
    id: `${s}->${t}`, source: s, source_output: "main", target: t, target_input: "input",
  });
  return { nodes: [mk("A"), mk("B"), mk("C")], edges: [ed("A", "B"), ed("B", "C")] };
}
function load(graph: WorkflowGraph) {
  useEditor.getState().loadGraph({ nodes: [], edges: [] });
  useEditor.getState().setManifests([codeManifest()]);
  useEditor.getState().loadGraph(graph);
}

describe("undo restores nodes and their edges together", () => {
  it("single undo brings back a deleted node AND its edges (edges removed first)", () => {
    load(chainGraph());
    // React Flow splits a node delete into two same-tick change calls.
    useEditor.getState().onEdgesChange([
      { type: "remove", id: "A->B" },
      { type: "remove", id: "B->C" },
    ]);
    useEditor.getState().onNodesChange([{ type: "remove", id: "B" }]);

    let s = useEditor.getState();
    expect(s.nodes.map((n) => n.id).sort()).toEqual(["A", "C"]);
    expect(s.edges.map((e) => e.id).sort()).toEqual([]);

    useEditor.getState().undo();
    s = useEditor.getState();
    expect(s.nodes.map((n) => n.id).sort()).toEqual(["A", "B", "C"]);
    expect(s.edges.map((e) => e.id).sort()).toEqual(["A->B", "B->C"]);
  });

  it("works when the node removal arrives before the edge removals", () => {
    load(chainGraph());
    useEditor.getState().onNodesChange([{ type: "remove", id: "B" }]);
    useEditor.getState().onEdgesChange([
      { type: "remove", id: "A->B" },
      { type: "remove", id: "B->C" },
    ]);
    useEditor.getState().undo();
    const s = useEditor.getState();
    expect(s.nodes.map((n) => n.id).sort()).toEqual(["A", "B", "C"]);
    expect(s.edges.map((e) => e.id).sort()).toEqual(["A->B", "B->C"]);
  });

  it("an edge-only delete is restored by one undo without over-restoring", () => {
    load(chainGraph());
    useEditor.getState().onEdgesChange([{ type: "remove", id: "A->B" }]);
    expect(useEditor.getState().edges.map((e) => e.id).sort()).toEqual(["B->C"]);
    useEditor.getState().undo();
    expect(useEditor.getState().edges.map((e) => e.id).sort()).toEqual(["A->B", "B->C"]);
  });
});
