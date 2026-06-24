/**
 * Store-layer tests for core Canvas operations (T-02):
 * node add/remove, edge creation, metanode drill-in transition, undo/redo.
 *
 * These test the Zustand store directly, bypassing React and the ReactFlow
 * renderer, so they run fast without a DOM.
 */
import { afterEach, describe, expect, it } from "vitest";
import type { Edge } from "@xyflow/react";

import type { NodeManifest, PortSpec } from "../types";
import type { NoodleNode } from "./store";
import { useEditor } from "./store";

// ── test fixtures ─────────────────────────────────────────────────────────────

function port(name: string, data_kind: PortSpec["data_kind"] = "any"): PortSpec {
  return { name, description: "", data_kind };
}

function manifest(id: string, opts?: { inputs?: PortSpec[]; outputs?: PortSpec[] }): NodeManifest {
  return {
    id,
    name: id,
    category: "Core",
    version: "1",
    description: "",
    icon: null,
    inputs: opts?.inputs ?? [port("input")],
    outputs: opts?.outputs ?? [port("main")],
    params: [
      {
        name: "label",
        type: "string",
        required: false,
        default: id,
        description: "",
        placeholder: "",
        choices: null,
        multiline: false,
        key_value: false,
        credential: null,
      },
    ],
  };
}

function node(id: string, m: NodeManifest, x = 0, selected = false): NoodleNode {
  return {
    id,
    type: "noodle",
    selected,
    position: { x, y: 0 },
    data: {
      manifest: m,
      params: { label: id },
      disabled: false,
      outputsOverride: null,
      onError: "stop",
      retryOnFail: false,
      retries: 1,
      retryWaitSeconds: 0,
      retryBackoff: false,
      alwaysOutputData: false,
      timeoutSeconds: null,
    },
  };
}

function edge(id: string, source: string, target: string): Edge {
  return { id, source, sourceHandle: "main", target, targetHandle: "input" };
}

function resetEditor(): void {
  useEditor.setState({
    manifests: [],
    manifestsById: {},
    nodes: [],
    edges: [],
    selectedId: null,
    dirty: false,
    runId: null,
    running: false,
    runStatus: {},
    runOutputs: {},
    runMeta: {},
    runError: null,
    workflowId: null,
    pinned: {},
    clipboardNodeCount: 0,
    _past: [],
    _future: [],
    _clipboard: null,
    childWorkflows: {},
    drillStack: [],
  });
}

afterEach(resetEditor);

// ── node add ──────────────────────────────────────────────────────────────────

describe("addNode", () => {
  it("adds a node with the right manifest and default params", () => {
    resetEditor();
    const m = manifest("http_request");
    useEditor.setState({ manifests: [m], manifestsById: { http_request: m } });

    useEditor.getState().addNode("http_request", { x: 100, y: 200 });

    const { nodes } = useEditor.getState();
    expect(nodes).toHaveLength(1);
    expect(nodes[0].data.manifest.id).toBe("http_request");
    expect(nodes[0].position).toEqual({ x: 100, y: 200 });
  });

  it("marks the graph dirty after adding a node", () => {
    resetEditor();
    const m = manifest("code");
    useEditor.setState({ manifests: [m], manifestsById: { code: m } });

    useEditor.getState().addNode("code", { x: 0, y: 0 });

    expect(useEditor.getState().dirty).toBe(true);
  });

  it("records the pre-add state in undo history", () => {
    resetEditor();
    const m = manifest("wait");
    useEditor.setState({ manifests: [m], manifestsById: { wait: m } });

    useEditor.getState().addNode("wait", { x: 0, y: 0 });

    expect(useEditor.getState()._past).toHaveLength(1);
  });

  it("no-ops when manifest id is unknown", () => {
    resetEditor();
    useEditor.getState().addNode("nonexistent_node", { x: 0, y: 0 });
    expect(useEditor.getState().nodes).toHaveLength(0);
  });
});

// ── node remove ───────────────────────────────────────────────────────────────

describe("removeNode (via onNodesChange + onEdgesChange delete)", () => {
  it("removes the node; edge removal is triggered separately (ReactFlow pattern)", () => {
    // ReactFlow fires onNodesChange to remove the node, then fires onEdgesChange
    // for edges that were connected to the deleted node. The store handles each
    // independently — onNodesChange does NOT auto-prune edges.
    resetEditor();
    const ma = manifest("a");
    const mb = manifest("b");
    const na = node("a", ma, 0);
    const nb = node("b", mb, 300);
    useEditor.setState({
      manifests: [ma, mb],
      manifestsById: { a: ma, b: mb },
      nodes: [na, nb],
      edges: [edge("e-ab", "a", "b")],
    });

    // ReactFlow would fire both changes; simulate the pair.
    useEditor.getState().onNodesChange([{ type: "remove", id: "a" }]);
    useEditor.getState().onEdgesChange([{ type: "remove", id: "e-ab" }]);

    const state = useEditor.getState();
    expect(state.nodes).toHaveLength(1);
    expect(state.nodes[0].id).toBe("b");
    expect(state.edges).toHaveLength(0);
  });
});

// ── edge creation (onConnect) ─────────────────────────────────────────────────

describe("onConnect", () => {
  it("adds a valid edge between compatible ports", () => {
    resetEditor();
    const ma = manifest("source");
    const mb = manifest("target");
    useEditor.setState({
      manifests: [ma, mb],
      manifestsById: { source: ma, target: mb },
      nodes: [node("n-source", ma), node("n-target", mb)],
      edges: [],
    });

    const check = useEditor.getState().onConnect({
      source: "n-source",
      sourceHandle: "main",
      target: "n-target",
      targetHandle: "input",
    });

    const state = useEditor.getState();
    expect(check.ok).toBe(true);
    expect(state.edges).toHaveLength(1);
    expect(state.edges[0].source).toBe("n-source");
    expect(state.edges[0].target).toBe("n-target");
    expect(state.dirty).toBe(true);
  });
});

// ── undo / redo ───────────────────────────────────────────────────────────────

describe("undo / redo", () => {
  it("undo reverts a node add", () => {
    resetEditor();
    const m = manifest("llm");
    useEditor.setState({ manifests: [m], manifestsById: { llm: m } });

    useEditor.getState().addNode("llm", { x: 0, y: 0 });
    expect(useEditor.getState().nodes).toHaveLength(1);

    useEditor.getState().undo();
    expect(useEditor.getState().nodes).toHaveLength(0);
  });

  it("redo re-applies the reverted add", () => {
    resetEditor();
    const m = manifest("transform");
    useEditor.setState({ manifests: [m], manifestsById: { transform: m } });

    useEditor.getState().addNode("transform", { x: 0, y: 0 });
    useEditor.getState().undo();
    expect(useEditor.getState().nodes).toHaveLength(0);

    useEditor.getState().redo();
    expect(useEditor.getState().nodes).toHaveLength(1);
  });

  it("undo with no history is a no-op", () => {
    resetEditor();
    useEditor.getState().undo(); // should not throw
    expect(useEditor.getState().nodes).toHaveLength(0);
  });

  it("redo with no future is a no-op", () => {
    resetEditor();
    useEditor.getState().redo(); // should not throw
    expect(useEditor.getState().nodes).toHaveLength(0);
  });

  it("a new action after undo clears redo history", () => {
    resetEditor();
    const m = manifest("filter");
    useEditor.setState({ manifests: [m], manifestsById: { filter: m } });

    useEditor.getState().addNode("filter", { x: 0, y: 0 });
    useEditor.getState().undo();
    useEditor.getState().addNode("filter", { x: 50, y: 0 });

    // After a new action the future stack must be empty.
    expect(useEditor.getState()._future).toHaveLength(0);
  });

  it("undo/redo round-trips edges too", () => {
    resetEditor();
    const ma = manifest("src");
    const mb = manifest("dst");
    useEditor.setState({
      manifests: [ma, mb],
      manifestsById: { src: ma, dst: mb },
      nodes: [node("n-src", ma), node("n-dst", mb)],
      edges: [],
    });

    useEditor.getState().onConnect({
      source: "n-src",
      sourceHandle: "main",
      target: "n-dst",
      targetHandle: "input",
    });
    expect(useEditor.getState().edges).toHaveLength(1);

    useEditor.getState().undo();
    expect(useEditor.getState().edges).toHaveLength(0);

    useEditor.getState().redo();
    expect(useEditor.getState().edges).toHaveLength(1);
  });
});

// ── metanode drill-in ─────────────────────────────────────────────────────────

describe("metanode drill-in / drill-out (enterMetanode / exitMetanode)", () => {
  it("collapseToMetanode + enterMetanode pushes a frame onto drillStack", () => {
    // Create a simple A->B->C->D graph, collapse B+C into a metanode, then drill in.
    const m = manifest("code");
    const mk = (id: string) => ({
      id, type: "code", params: {}, position: { x: 0, y: 0 },
      disabled: false, outputs_override: null, on_error: "stop",
      retry_on_fail: false, retries: 1, retry_wait_seconds: 0, retry_backoff: false,
      always_output_data: false, timeout_seconds: null,
    });
    const ed = (s: string, t: string) => ({
      id: `${s}->${t}`, source: s, source_output: "main", target: t, target_input: "input",
    });

    useEditor.getState().loadGraph({ nodes: [], edges: [] });
    useEditor.getState().setManifests([m]);
    useEditor.getState().loadGraph({
      nodes: [mk("A"), mk("B"), mk("C"), mk("D")],
      edges: [ed("A", "B"), ed("B", "C"), ed("C", "D")],
    });

    const metaId = useEditor.getState().collapseToMetanode(["B", "C"]);
    expect(metaId).toBeTruthy();

    useEditor.getState().enterMetanode(metaId!);
    expect(useEditor.getState().drillStack).toHaveLength(1);
  });

  it("exitMetanode pops the drill stack", () => {
    const m = manifest("code");
    const mk = (id: string) => ({
      id, type: "code", params: {}, position: { x: 0, y: 0 },
      disabled: false, outputs_override: null, on_error: "stop",
      retry_on_fail: false, retries: 1, retry_wait_seconds: 0, retry_backoff: false,
      always_output_data: false, timeout_seconds: null,
    });
    const ed = (s: string, t: string) => ({
      id: `${s}->${t}`, source: s, source_output: "main", target: t, target_input: "input",
    });

    useEditor.getState().loadGraph({ nodes: [], edges: [] });
    useEditor.getState().setManifests([m]);
    useEditor.getState().loadGraph({
      nodes: [mk("A"), mk("B"), mk("C"), mk("D")],
      edges: [ed("A", "B"), ed("B", "C"), ed("C", "D")],
    });

    const metaId = useEditor.getState().collapseToMetanode(["B", "C"])!;
    useEditor.getState().enterMetanode(metaId);
    expect(useEditor.getState().drillStack).toHaveLength(1);

    useEditor.getState().exitMetanode();
    expect(useEditor.getState().drillStack).toHaveLength(0);
  });
});
