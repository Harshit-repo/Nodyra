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
    id, type: "code", params: { code: `# ${id}` }, position: { x: 0, y: 0 },
    disabled: false, outputs_override: null, on_error: "stop",
    retry_on_fail: false, retries: 1, retry_wait_seconds: 0, retry_backoff: false,
    always_output_data: false, timeout_seconds: null,
  });
  const ed = (s: string, t: string) => ({
    id: `${s}->${t}`, source: s, source_output: "main", target: t, target_input: "input",
  });
  return {
    nodes: [mk("A"), mk("B"), mk("C"), mk("D")],
    edges: [ed("A", "B"), ed("B", "C"), ed("C", "D")],
  };
}

function load(graph: WorkflowGraph) {
  useEditor.getState().loadGraph({ nodes: [], edges: [] });
  useEditor.getState().setManifests([codeManifest()]);
  useEditor.getState().loadGraph(graph);
}

describe("metanode collapse / ungroup", () => {
  it("collapses a selection into a metanode with derived boundary ports", () => {
    load(chainGraph());
    const id = useEditor.getState().collapseToMetanode(["B", "C"]);
    expect(id).toBeTruthy();

    const s = useEditor.getState();
    const ids = s.nodes.map((n) => n.id);
    expect(ids).toContain("A");
    expect(ids).toContain("D");
    expect(ids).toContain(id!);
    expect(ids).not.toContain("B");
    expect(ids).not.toContain("C");

    const meta = s.nodes.find((n) => n.id === id)!;
    expect(meta.data.manifest.id).toBe("meta_node");
    expect(meta.data.params.execution).toBe("transparent");
    const sub = meta.data.params.subgraph as { nodes: { id: string }[]; edges: unknown[] };
    expect(sub.nodes.map((n) => n.id).sort()).toEqual(["B", "C"]);
    expect(sub.edges).toHaveLength(1); // internal B->C
    expect(meta.data.manifest.inputs).toHaveLength(1);
    expect(meta.data.manifest.outputs).toHaveLength(1);

    // external edges now run through the metanode
    const pairs = s.edges.map((e) => [e.source, e.target]);
    expect(pairs).toContainEqual(["A", id!]);
    expect(pairs).toContainEqual([id!, "D"]);
  });

  it("ungroup is the inverse of collapse (round-trip)", () => {
    load(chainGraph());
    const id = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().ungroupMetanode(id);

    const out = useEditor.getState().toGraph();
    expect(out.nodes.map((n) => n.id).sort()).toEqual(["A", "B", "C", "D"]);
    const pairs = out.edges.map((e) => `${e.source}->${e.target}`).sort();
    expect(pairs).toEqual(["A->B", "B->C", "C->D"]);
  });

  it("rejects a collapse that would create a cycle", () => {
    // A -> X -> C, select {A, C}: metanode would feed X and be fed by X => cycle.
    const mk = (id: string) => ({
      id, type: "code", params: {}, position: { x: 0, y: 0 },
      disabled: false, outputs_override: null, on_error: "stop",
      retry_on_fail: false, retries: 1, retry_wait_seconds: 0, retry_backoff: false,
      always_output_data: false, timeout_seconds: null,
    });
    const ed = (s: string, t: string) => ({
      id: `${s}->${t}`, source: s, source_output: "main", target: t, target_input: "input",
    });
    load({ nodes: [mk("A"), mk("X"), mk("C")], edges: [ed("A", "X"), ed("X", "C")] });

    const id = useEditor.getState().collapseToMetanode(["A", "C"]);
    expect(id).toBeNull();
    // graph unchanged
    expect(useEditor.getState().nodes.map((n) => n.id).sort()).toEqual(["A", "C", "X"]);
  });
});
