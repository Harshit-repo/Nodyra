import { describe, expect, it } from "vitest";
import type { Edge } from "@xyflow/react";

import type { NodeManifest, PortSpec } from "../types";
import type { NoodleNode } from "./store";
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
): NoodleNode {
  return {
    id,
    type: "noodle",
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
});
