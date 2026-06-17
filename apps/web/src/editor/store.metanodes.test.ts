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
  it("inserts a node between an existing edge", () => {
    load(chainGraph());
    const result = useEditor.getState().insertNodeBetweenEdge("A->B", "code", { x: 120, y: 80 });
    expect(result.ok).toBe(true);

    const state = useEditor.getState();
    const inserted = state.nodes.find((node) => node.id === result.nodeId);
    expect(inserted).toBeTruthy();
    expect(inserted!.data.manifest.id).toBe("code");
    expect(inserted!.position).toEqual({ x: 120, y: 80 });
    expect(state.selectedId).toBe(result.nodeId);
    expect(state.edges.some((edge) => edge.id === "A->B")).toBe(false);
    expect(state.edges).toEqual(expect.arrayContaining([
      expect.objectContaining({
        source: "A",
        sourceHandle: "main",
        target: result.nodeId,
        targetHandle: "input",
      }),
      expect.objectContaining({
        source: result.nodeId,
        sourceHandle: "main",
        target: "B",
        targetHandle: "input",
      }),
    ]));
  });

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

    // inspector-editable params: name + execution (transparent | isolated)
    const pnames = meta.data.manifest.params.map((p) => p.name);
    expect(pnames).toContain("name");
    expect(pnames).toContain("execution");
    const exec = meta.data.manifest.params.find((p) => p.name === "execution")!;
    expect(exec.choices).toEqual(["transparent", "isolated"]);
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

  it("a metanode survives save + reload (synthetic manifest)", () => {
    load(chainGraph());
    const id = useEditor.getState().collapseToMetanode(["B", "C"])!;
    const saved = useEditor.getState().toGraph();

    // reload with only the "code" manifest registered (no meta_node from backend)
    useEditor.getState().loadGraph({ nodes: [], edges: [] });
    useEditor.getState().setManifests([codeManifest()]);
    useEditor.getState().loadGraph(saved);

    const meta = useEditor.getState().nodes.find((n) => n.id === id);
    expect(meta).toBeTruthy();
    expect(meta!.data.manifest.id).toBe("meta_node");
    expect(meta!.data.manifest.inputs).toHaveLength(1);
    expect(meta!.data.manifest.outputs).toHaveLength(1);

    // and it can still be ungrouped after a reload
    useEditor.getState().ungroupMetanode(id);
    const out = useEditor.getState().toGraph();
    expect(out.nodes.map((n) => n.id).sort()).toEqual(["A", "B", "C", "D"]);
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

  it("round-trips ungroup of a metanode whose subgraph contains a nested metanode", () => {
    load(chainGraph());
    // collapse B,C -> meta1; then collapse meta1 + A into meta2
    const meta1 = useEditor.getState().collapseToMetanode(["B", "C"])!;
    const meta2 = useEditor.getState().collapseToMetanode([meta1, "A"])!;
    expect(meta2).toBeTruthy();
    // ungroup meta2 — the nested meta1 must be restored as a meta_node, not dropped
    useEditor.getState().ungroupMetanode(meta2);
    const ids = useEditor.getState().nodes.map((n) => n.id);
    expect(ids).toContain(meta1);
    const restored = useEditor.getState().nodes.find((n) => n.id === meta1)!;
    expect(restored.data.manifest.id).toBe("meta_node");
  });
});
