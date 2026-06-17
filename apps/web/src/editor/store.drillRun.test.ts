import { describe, expect, it, vi } from "vitest";

import type { NodeManifest, PortSpec, WorkflowGraph } from "../types";
import { useEditor } from "./store";
import { META_BAR_INPUT_ID, META_BAR_OUTPUT_ID } from "./store/drillSlice";

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

describe("step-run inside a metanode", () => {
  it("runKeyFor namespaces interior ids by the drill path", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    expect(useEditor.getState().runKeyFor("B")).toBe("B"); // root
    useEditor.getState().enterMetanode(meta);
    expect(useEditor.getState().runKeyFor("B")).toBe(`${meta}/B`);
  });

  it("runFromNode inside a transparent metanode targets the namespaced id", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    const handler = vi.fn().mockResolvedValue(undefined);
    useEditor.getState().setRunHandler(handler);
    useEditor.getState().enterMetanode(meta);
    useEditor.getState().runFromNode("B");
    expect(handler).toHaveBeenCalledWith([`${meta}/B`], expect.objectContaining({ reuseUpstream: true }));
  });

  it("step-run is disabled inside an isolated metanode", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    // mark isolated
    useEditor.getState().updateParams(meta, {
      ...(useEditor.getState().nodes.find((n) => n.id === meta)!.data.params as object),
      execution: "isolated",
    } as Record<string, unknown>);
    useEditor.getState().enterMetanode(meta);
    expect(useEditor.getState().drillStepRunDisabledReason()).toMatch(/isolated/i);
  });
});

describe("startRun inside a metanode", () => {
  it("strips the drill prefix from targets for the ancestor walk", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);
    const prefix = useEditor.getState().drillStack.length > 0
      ? useEditor.getState().drillStack.map((f) => f.metaId).join("/") + "/"
      : "";
    expect(prefix).toBe(`${meta}/`);
    useEditor.getState().startRun("test-run-id", [`${meta}/C`]);
    const status = useEditor.getState().runStatus;
    expect(status).toHaveProperty("B");
    expect(status).not.toHaveProperty(META_BAR_INPUT_ID);
    expect(status).not.toHaveProperty(META_BAR_OUTPUT_ID);
  });

  it("excludes proxy edges and bars from the ancestor walk", () => {
    load(chainGraph4());
    const meta = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(meta);
    useEditor.getState().startRun("test-run-id", [`${meta}/C`]);
    const status = useEditor.getState().runStatus;
    expect(status).toHaveProperty("B");
    expect(status).toHaveProperty("C");
  });
});
