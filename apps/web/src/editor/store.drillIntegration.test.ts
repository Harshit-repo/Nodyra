import { describe, expect, it } from "vitest";

import type { NodeManifest, PortSpec, WorkflowGraph } from "../types";
import { useEditor } from "./store";
import { isMetaBar, META_BAR_INPUT_ID, META_BAR_OUTPUT_ID } from "./store/drillSlice";

function port(name: string, data_kind: PortSpec["data_kind"] = "any"): PortSpec {
  return { name, description: "", data_kind };
}

function codeManifest(): NodeManifest {
  return {
    id: "code", name: "Code", category: "Core", version: "1", description: "",
    icon: null, inputs: [port("input")], outputs: [port("main")], params: [],
  };
}

function chainGraph4(): WorkflowGraph {
  const mk = (id: string) => ({
    id, type: "code", params: {}, position: { x: 0, y: 0 },
    disabled: false, outputs_override: null, on_error: "stop",
    retry_on_fail: false, retries: 1, retry_wait_seconds: 0, retry_backoff: false,
    always_output_data: false, timeout_seconds: null,
  });
  const ed = (s: string, t: string) => ({
    id: `${s}->${t}`, source: s, source_output: "main", target: t, target_input: "input",
  });
  return { nodes: [mk("A"), mk("B"), mk("C"), mk("D")], edges: [ed("A", "B"), ed("B", "C"), ed("C", "D")] };
}

function load(graph: WorkflowGraph) {
  useEditor.getState().loadGraph({ nodes: [], edges: [] });
  useEditor.getState().setManifests([codeManifest()]);
  useEditor.getState().loadGraph(graph);
}

describe("drill-in integration: edit inside, exit, verify", () => {
  it("adds a node inside, connects it, exits, and serializes correctly", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);

    // Add a node E inside
    useEditor.getState().addNode("code", { x: 600, y: 0 });
    const eNode = useEditor.getState().nodes.find((n) => n.id !== META_BAR_INPUT_ID && n.id !== META_BAR_OUTPUT_ID && !["B", "C"].includes(n.id))!;
    expect(eNode).toBeDefined();

    // Connect B → E and E → output bar
    const outPort = (useEditor.getState().nodes.find((n) => n.id === META_BAR_OUTPUT_ID)!
      .data as unknown as { ports: { id: string }[] }).ports[0].id;
    useEditor.getState().onConnect({ source: "B", sourceHandle: "main", target: eNode.id, targetHandle: "input" });
    useEditor.getState().onConnect({ source: eNode.id, sourceHandle: "main", target: META_BAR_OUTPUT_ID, targetHandle: outPort });
    expect(useEditor.getState().edges.length).toBeGreaterThan(0);

    // Exit and verify the subgraph includes E
    useEditor.getState().exitMetanode();
    useEditor.getState().enterMetanode(meta);
    const interior = useEditor.getState().nodes.filter((n) => !isMetaBar(n));
    expect(interior.map((n) => n.id)).toContain(eNode.id);
  });

  it("deleteSelection inside drill removes interior nodes only", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);

    // Select node C and delete
    useEditor.getState().setSelected("C");
    expect(useEditor.getState().deleteSelection()).toBe(1);
    const interior = useEditor.getState().nodes.filter((n) => !isMetaBar(n));
    expect(interior.map((n) => n.id)).toEqual(["B"]);
    expect(useEditor.getState().nodes.filter((n) => isMetaBar(n))).toHaveLength(2);
  });

  it("copySelection inside drill copies only interior nodes (no bars)", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);

    // Select all interior nodes
    useEditor.getState().selectAll();
    const copied = useEditor.getState().copySelection();
    expect(copied.nodeCount).toBe(2);
    expect(copied.nodeCount).not.toBe(4); // bars are excluded
  });

  it("pasteSelection inside drill inserts pasted nodes into the interior", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);

    // Copy B
    useEditor.getState().setSelected("B");
    useEditor.getState().copySelection();
    const pasted = useEditor.getState().pasteSelection();
    expect(pasted.nodeCount).toBe(1);
    expect(pasted.edgeCount).toBe(0);

    // Pasted node is inside (not a bar)
    const interior = useEditor.getState().nodes.filter((n) => !isMetaBar(n));
    expect(interior).toHaveLength(3);
  });

  it("duplicateSelection inside drill duplicates interior nodes with internal edges", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);

    // Select B and C
    for (const node of useEditor.getState().nodes) {
      if (node.id === "B" || node.id === "C") {
        useEditor.getState().onNodesChange([{ type: "select", id: node.id, selected: true }]);
      }
    }
    const count = useEditor.getState().duplicateSelection();
    expect(count).toBe(2);

    const interior = useEditor.getState().nodes.filter((n) => !isMetaBar(n));
    expect(interior).toHaveLength(4); // B, C, + 2 duplicates
    expect(interior.filter((n) => n.selected)).toHaveLength(2);
  });

  it("selectAll inside drill selects interior nodes but not bar nodes", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);

    useEditor.getState().selectAll();
    const selected = useEditor.getState().nodes.filter((n) => n.selected);
    expect(selected.length).toBe(2); // B and C only
    expect(selected.every((n) => !isMetaBar(n))).toBe(true);
  });

  it("bar nodes are never included in clipboard or selection", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);

    // Try to select a bar node directly via setSelected
    useEditor.getState().setSelected(META_BAR_INPUT_ID);
    // setSelected only sets selectedId, not node.selected — the operations
    // should still filter out bar nodes via selectedNodes()
    const copied = useEditor.getState().copySelection();
    expect(copied.nodeCount).toBe(0);
  });

  it("startRun inside drill starts interior nodes with namespaced keys", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);
    // Target C; startRun walks ancestors so B (ancestor of C) is also planned
    useEditor.getState().startRun("test-run-id", ["C"]);
    const status = useEditor.getState().runStatus;
    expect(status).toHaveProperty("B");
    expect(status).toHaveProperty("C");
    // Bar nodes should not be in run status
    expect(status).not.toHaveProperty(META_BAR_INPUT_ID);
    expect(status).not.toHaveProperty(META_BAR_OUTPUT_ID);
  });

  it("runKeyFor prefixes interior ids with drill path", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    expect(useEditor.getState().runKeyFor("B")).toBe("B");
    useEditor.getState().enterMetanode(meta);
    expect(useEditor.getState().runKeyFor("B")).toBe(`${meta}/B`);
    useEditor.getState().exitMetanode();
    expect(useEditor.getState().runKeyFor("B")).toBe("B");
  });

  it("undo scoped to drill level — edits inside don't affect root history", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);

    // Delete C inside
    useEditor.getState().setSelected("C");
    useEditor.getState().deleteSelection();
    expect(useEditor.getState().nodes.filter((n) => !isMetaBar(n)).map((n) => n.id)).toEqual(["B"]);

    // Undo inside
    useEditor.getState().undo();
    expect(useEditor.getState().nodes.filter((n) => !isMetaBar(n)).map((n) => n.id)).toEqual(["B", "C"]);

    // Exit — root history should be intact
    useEditor.getState().exitMetanode();
    const rootNodes = useEditor.getState().nodes.filter((n) => n.id !== meta).sort();
    expect(rootNodes.map((n) => n.id)).toEqual(["A", "D"]);
  });
});
