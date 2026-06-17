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

describe("metanode drill-in: enter / exit", () => {
  it("enter shows interior nodes + two bars; exit with no edits round-trips", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    const before = JSON.stringify(useEditor.getState().toGraph());

    useEditor.getState().enterMetanode(meta);
    const s = useEditor.getState();
    expect(s.drillStack).toHaveLength(1);
    const interior = s.nodes.filter((n) => !isMetaBar(n));
    expect(interior.map((n) => n.id).sort()).toEqual(["B", "C"]);
    expect(s.nodes.filter((n) => isMetaBar(n))).toHaveLength(2);

    useEditor.getState().exitMetanode();
    expect(useEditor.getState().drillStack).toHaveLength(0);
    expect(JSON.stringify(useEditor.getState().toGraph())).toEqual(before);
  });

  it("edits inside survive exit (delete C inside, exit, ungroup ⇒ only B left)", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);
    useEditor.getState().onNodesChange([{ type: "remove", id: "C" }]);
    useEditor.getState().exitMetanode();
    useEditor.getState().ungroupMetanode(meta);
    const ids = useEditor.getState().nodes.map((n) => n.id).sort();
    expect(ids).toContain("B");
    expect(ids).not.toContain("C");
  });
});

describe("metanode drill-in: toGraph folds to root while drilled", () => {
  it("toGraph mid-drill returns the root workflow with edits folded in", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);
    useEditor.getState().onNodesChange([{ type: "remove", id: "C" }]);

    const g = useEditor.getState().toGraph();
    const ids = g.nodes.map((n) => n.id).sort();
    expect(ids).toContain("A");
    expect(ids).toContain("D");
    expect(ids).toContain(meta);
    expect(ids).not.toContain("C");
    expect(g.nodes.some((n) => n.type === "metaBar")).toBe(false);
    const metaNode = g.nodes.find((n) => n.id === meta)!;
    const sub = (metaNode.params as { subgraph: { nodes: { id: string }[] } }).subgraph;
    expect(sub.nodes.map((n) => n.id)).toEqual(["B"]);
  });

  it("nested drill folds both levels in toGraph", () => {
    load(chainGraph4());
    const meta1 = useEditor.getState().collapseToMetanode(["B", "C"])!;
    const meta2 = useEditor.getState().collapseToMetanode([meta1, "A"])!;
    useEditor.getState().enterMetanode(meta2);
    useEditor.getState().enterMetanode(meta1);
    expect(useEditor.getState().drillStack).toHaveLength(2);
    const g = useEditor.getState().toGraph();
    expect(g.nodes.map((n) => n.id)).toContain(meta2);
    expect(g.nodes.some((n) => n.type === "metaBar")).toBe(false);
  });
});

describe("metanode drill-in: port lifecycle", () => {
  it("add an input port inside ⇒ surfaces as an unconnected port on the parent", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);
    const inBar = useEditor.getState().nodes.find((n) => n.id === META_BAR_INPUT_ID)!;
    const before = (inBar.data as unknown as { ports: unknown[] }).ports.length;

    useEditor.getState().addMetaPort("input");
    const after = useEditor.getState().nodes.find((n) => n.id === META_BAR_INPUT_ID)!;
    expect((after.data as unknown as { ports: unknown[] }).ports.length).toBe(before + 1);

    useEditor.getState().exitMetanode();
    const metaNode = useEditor.getState().nodes.find((n) => n.id === meta)!;
    const ports = (metaNode.data.params as { ports: { inputs: unknown[] } }).ports;
    expect(ports.inputs.length).toBe(before + 1);
    const handles = useEditor.getState().edges.filter((e) => e.target === meta).map((e) => e.targetHandle);
    expect(handles.length).toBe(before);
  });

  it("a chosen port kind persists on the parent even while unwired", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);
    useEditor.getState().addMetaPort("input", "dataset");
    useEditor.getState().exitMetanode();
    const metaNode = useEditor.getState().nodes.find((n) => n.id === meta)!;
    const ports = (metaNode.data.params as { ports: { inputs: { data_kind?: string }[] } }).ports;
    expect(ports.inputs.at(-1)!.data_kind).toBe("dataset");
    // the synthetic manifest exposes the typed port too
    expect(metaNode.data.manifest.inputs.at(-1)!.data_kind).toBe("dataset");
  });

  it("removing an input port drops its parent edge on exit", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    const inputPort = (useEditor.getState().nodes.find((n) => n.id === meta)!
      .data.params as { ports: { inputs: { port: string }[] } }).ports.inputs[0].port;
    expect(useEditor.getState().edges.some((e) => e.target === meta && e.targetHandle === inputPort)).toBe(true);

    useEditor.getState().enterMetanode(meta);
    useEditor.getState().removeMetaPort("input", inputPort);
    useEditor.getState().exitMetanode();
    expect(useEditor.getState().edges.some((e) => e.target === meta && e.targetHandle === inputPort)).toBe(false);
  });

  it("output bar accepts only one incoming wire per port", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);
    const outPort = (useEditor.getState().nodes.find((n) => n.id === META_BAR_OUTPUT_ID)!
      .data as unknown as { ports: { id: string }[] }).ports[0].id;
    useEditor.getState().onConnect({ source: "B", sourceHandle: "main", target: META_BAR_OUTPUT_ID, targetHandle: outPort });
    useEditor.getState().onConnect({ source: "C", sourceHandle: "main", target: META_BAR_OUTPUT_ID, targetHandle: outPort });
    const into = useEditor.getState().edges.filter((e) => e.target === META_BAR_OUTPUT_ID && e.targetHandle === outPort);
    expect(into).toHaveLength(1);
    expect(into[0].source).toBe("C");
  });

  it("never leaks bar nodes or proxy edges into the serialized graph", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);
    const g = useEditor.getState().toGraph();
    expect(g.nodes.some((n) => n.type === "metaBar")).toBe(false);
    expect(
      g.edges.some((e) => e.source.startsWith("__meta_") || e.target.startsWith("__meta_")),
    ).toBe(false);
  });

  it("switching workflow (loadGraph) exits any drill", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);
    expect(useEditor.getState().drillStack).toHaveLength(1);
    useEditor.getState().loadGraph({ nodes: [], edges: [] });
    expect(useEditor.getState().drillStack).toHaveLength(0);
  });

  it("fan-out input port: one bar handle → multiple internal targets round-trips", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);
    const portId = (useEditor.getState().nodes.find((n) => n.id === META_BAR_INPUT_ID)!
      .data as unknown as { ports: { id: string }[] }).ports[0].id;
    useEditor.getState().onConnect({
      source: META_BAR_INPUT_ID, sourceHandle: portId,
      target: "B", targetHandle: "input",
    });
    useEditor.getState().onConnect({
      source: META_BAR_INPUT_ID, sourceHandle: portId,
      target: "C", targetHandle: "input",
    });
    useEditor.getState().exitMetanode();
    useEditor.getState().enterMetanode(meta);
    const proxyEdges = useEditor.getState().edges.filter(
      (e) => e.source === META_BAR_INPUT_ID && e.sourceHandle === portId,
    );
    expect(proxyEdges).toHaveLength(2);
  });

  it("output port with multiple wires is deduped on fold (last wire wins)", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);
    const outPort = (useEditor.getState().nodes.find((n) => n.id === META_BAR_OUTPUT_ID)!
      .data as unknown as { ports: { id: string }[] }).ports[0].id;
    useEditor.getState().onConnect({ source: "B", sourceHandle: "main", target: META_BAR_OUTPUT_ID, targetHandle: outPort });
    useEditor.getState().onConnect({ source: "C", sourceHandle: "main", target: META_BAR_OUTPUT_ID, targetHandle: outPort });
    useEditor.getState().exitMetanode();
    const out = ((useEditor.getState().nodes.find((n) => n.id === meta)!
      .data.params as { ports: { inputs: unknown[]; outputs: { port: string; source: string }[] } }).ports).outputs;
    expect(out).toHaveLength(1);
    expect(out[0].source).toBe("C");
  });
});
