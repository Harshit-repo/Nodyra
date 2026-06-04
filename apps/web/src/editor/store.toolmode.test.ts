import { describe, expect, it } from "vitest";

import type { NodeManifest, ParamSpec, PortSpec, WorkflowGraph } from "../types";
import { useEditor } from "./store";

function port(name: string, data_kind: PortSpec["data_kind"] = "any"): PortSpec {
  return { name, description: "", data_kind };
}

function param(name: string, def: unknown): ParamSpec {
  return {
    name, type: "string", required: false, default: def, description: "",
    placeholder: "", choices: null, multiline: false, key_value: false,
  };
}

function manifest(id: string): NodeManifest {
  return {
    id, name: id, category: "Core", version: "1", description: "", icon: null,
    inputs: [port("input")], outputs: [port("main")],
    params: [param("url", "https://default"), param("method", "GET")],
  };
}

const GRAPH: WorkflowGraph = {
  nodes: [
    {
      id: "n1", type: "http_request", params: { url: "https://x" },
      position: { x: 0, y: 0 }, disabled: false, outputs_override: null,
      on_error: "stop", retry_on_fail: false, retries: 1,
      retry_wait_seconds: 0, retry_backoff: false, always_output_data: false,
      timeout_seconds: null,
      tool_mode: true, tool_name: "fetch", tool_description: "Fetch a URL",
    },
  ],
  edges: [],
};

describe("store tool-mode round-trip", () => {
  it("loadGraph → toGraph preserves tool_mode fields", () => {
    useEditor.getState().setManifests([manifest("http_request")]);
    useEditor.getState().loadGraph(GRAPH);

    const out = useEditor.getState().toGraph();
    const node = out.nodes[0];
    expect(node.tool_mode).toBe(true);
    expect(node.tool_name).toBe("fetch");
    expect(node.tool_description).toBe("Fetch a URL");
  });

  it("updateNodeSettings can toggle tool_mode", () => {
    useEditor.getState().setManifests([manifest("http_request")]);
    useEditor.getState().loadGraph({
      ...GRAPH,
      nodes: [{ ...GRAPH.nodes[0], tool_mode: false, tool_name: null, tool_description: "" }],
    });

    useEditor.getState().updateNodeSettings("n1", {
      toolMode: true, toolName: "fetch", toolDescription: "Fetch a URL",
    });

    const node = useEditor.getState().toGraph().nodes[0];
    expect(node.tool_mode).toBe(true);
    expect(node.tool_name).toBe("fetch");
  });

  it("toggling tool mode OFF reverts From-AI params to their defaults", () => {
    useEditor.getState().setManifests([manifest("http_request")]);
    useEditor.getState().loadGraph({
      ...GRAPH,
      nodes: [
        {
          ...GRAPH.nodes[0],
          tool_mode: true,
          params: {
            url: "{{ $fromAI('url', 'target', 'string') }}",
            method: "POST",
          },
        },
      ],
    });

    useEditor.getState().updateNodeSettings("n1", { toolMode: false });

    const node = useEditor.getState().toGraph().nodes[0];
    expect(node.tool_mode).toBe(false);
    // From-AI param reverts to its spec default; a plain param is untouched.
    expect(node.params.url).toBe("https://default");
    expect(node.params.method).toBe("POST");
  });
});
