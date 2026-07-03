import { afterEach, describe, expect, it, vi } from "vitest";
import type { Edge } from "@xyflow/react";

import type { NodeManifest, PortSpec } from "../types";
import type { NodyraNode } from "./store";
import { useEditor } from "./store";

function port(name: string, data_kind: PortSpec["data_kind"] = "any"): PortSpec {
  return { name, description: "", data_kind };
}

function manifest(id: string): NodeManifest {
  return {
    id,
    name: id,
    category: "Core",
    version: "1",
    description: "",
    icon: null,
    inputs: [port("input")],
    outputs: [port("main")],
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

function node(
  id: string,
  m: NodeManifest,
  x: number,
  selected = false,
): NodyraNode {
  return {
    id,
    type: "nodyra",
    selected,
    position: { x, y: 40 },
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
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("editor clipboard", () => {
  it("copies selected nodes and pastes remapped internal edges", () => {
    resetEditor();
    const sourceManifest = manifest("source");
    const targetManifest = manifest("target");
    const outsideManifest = manifest("outside");
    const internalEdge: Edge = {
      id: "e_source_target",
      source: "source",
      sourceHandle: "main",
      target: "target",
      targetHandle: "input",
    };
    const outsideEdge: Edge = {
      id: "e_target_outside",
      source: "target",
      sourceHandle: "main",
      target: "outside",
      targetHandle: "input",
    };
    useEditor.setState({
      manifests: [sourceManifest, targetManifest, outsideManifest],
      manifestsById: {
        source: sourceManifest,
        target: targetManifest,
        outside: outsideManifest,
      },
      nodes: [
        node("source", sourceManifest, 0, true),
        node("target", targetManifest, 280, true),
        node("outside", outsideManifest, 560),
      ],
      edges: [internalEdge, outsideEdge],
    });

    const copied = useEditor.getState().copySelection();
    const pasted = useEditor.getState().pasteSelection();
    const state = useEditor.getState();

    expect(copied).toEqual({ nodeCount: 2, edgeCount: 1 });
    expect(pasted).toEqual({ nodeCount: 2, edgeCount: 1 });
    expect(state.nodes).toHaveLength(5);
    expect(state.edges).toHaveLength(3);
    expect(state.clipboardNodeCount).toBe(2);
    expect(state._past).toHaveLength(1);

    const pastedNodes = state.nodes.filter((item) => item.selected);
    expect(pastedNodes).toHaveLength(2);
    expect(pastedNodes.map((item) => item.id)).not.toContain("source");
    expect(pastedNodes.map((item) => item.position)).toEqual([
      { x: 48, y: 88 },
      { x: 328, y: 88 },
    ]);

    const pastedIds = new Set(pastedNodes.map((item) => item.id));
    const pastedEdge = state.edges.find(
      (edge) => pastedIds.has(edge.source) && pastedIds.has(edge.target),
    );
    expect(pastedEdge).toEqual(
      expect.objectContaining({
        sourceHandle: "main",
        targetHandle: "input",
      }),
    );
    expect(state.edges).not.toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          source: expect.stringMatching(/^n_/),
          target: "outside",
        }),
      ]),
    );
  });

  it("cuts selected nodes and keeps the clipboard pasteable", () => {
    resetEditor();
    const sourceManifest = manifest("source");
    const targetManifest = manifest("target");
    const outsideManifest = manifest("outside");
    useEditor.setState({
      manifests: [sourceManifest, targetManifest, outsideManifest],
      manifestsById: {
        source: sourceManifest,
        target: targetManifest,
        outside: outsideManifest,
      },
      nodes: [
        node("source", sourceManifest, 0, true),
        node("target", targetManifest, 280, true),
        node("outside", outsideManifest, 560),
      ],
      edges: [
        {
          id: "e_source_target",
          source: "source",
          sourceHandle: "main",
          target: "target",
          targetHandle: "input",
        },
        {
          id: "e_target_outside",
          source: "target",
          sourceHandle: "main",
          target: "outside",
          targetHandle: "input",
        },
      ],
    });

    const cut = useEditor.getState().cutSelection();
    expect(cut).toEqual({ nodeCount: 2, edgeCount: 1 });
    expect(useEditor.getState().nodes.map((item) => item.id)).toEqual(["outside"]);
    expect(useEditor.getState().edges).toHaveLength(0);

    const pasted = useEditor.getState().pasteSelection();
    expect(pasted).toEqual({ nodeCount: 2, edgeCount: 1 });
    expect(useEditor.getState().nodes).toHaveLength(3);
    expect(useEditor.getState().edges).toHaveLength(1);
  });

  it("falls back to a cycle-safe clone when structuredClone rejects a value", () => {
    resetEditor();
    vi.stubGlobal("structuredClone", vi.fn(() => {
      throw new DOMException("Value could not be cloned", "DataCloneError");
    }));
    const sourceManifest = manifest("source");
    const sourceNode = node("source", sourceManifest, 0, true);
    const circular: Record<string, unknown> = { label: "source" };
    circular.self = circular;
    sourceNode.data.params = circular;
    useEditor.setState({ nodes: [sourceNode] });

    expect(useEditor.getState().copySelection()).toEqual({ nodeCount: 1, edgeCount: 0 });
    expect(useEditor.getState().pasteSelection()).toEqual({ nodeCount: 1, edgeCount: 0 });

    const pasted = useEditor.getState().nodes.find((item) => item.id !== "source");
    expect(pasted).toBeDefined();
    expect(pasted?.data.params).not.toBe(circular);
    expect(pasted?.data.params.self).toBe(pasted?.data.params);
  });

  it("deletes all selected nodes in one history entry", () => {
    resetEditor();
    const sourceManifest = manifest("source");
    const targetManifest = manifest("target");
    const outsideManifest = manifest("outside");
    useEditor.setState({
      nodes: [
        node("source", sourceManifest, 0, true),
        node("target", targetManifest, 280, true),
        node("outside", outsideManifest, 560),
      ],
      edges: [
        {
          id: "e_source_target",
          source: "source",
          sourceHandle: "main",
          target: "target",
          targetHandle: "input",
        },
      ],
    });

    expect(useEditor.getState().deleteSelection()).toBe(2);
    const state = useEditor.getState();
    expect(state.nodes.map((item) => item.id)).toEqual(["outside"]);
    expect(state.edges).toHaveLength(0);
    expect(state._past).toHaveLength(1);
  });
});
