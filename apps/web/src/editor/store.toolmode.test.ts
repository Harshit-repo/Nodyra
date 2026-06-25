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

function manifest(
  id: string,
  params: ParamSpec[] = [param("url", "https://default"), param("method", "GET")],
): NodeManifest {
  return {
    id, name: id, category: "Core", version: "1", description: "", icon: null,
    inputs: [port("input")], outputs: [port("main")],
    params,
  };
}

function aiMemoryManifest(): NodeManifest {
  return {
    id: "ai_buffer_memory",
    name: "AI Buffer Memory",
    category: "AI",
    version: "1",
    description: "",
    icon: null,
    inputs: [],
    outputs: [port("memory", "ai_memory")],
    params: [],
  };
}

function aiAgentManifest(): NodeManifest {
  return {
    id: "ai_agent_v2",
    name: "Agent",
    category: "AI",
    version: "1",
    description: "",
    icon: null,
    inputs: [port("memory", "ai_memory"), port("tool", "ai_tool")],
    outputs: [port("main", "main")],
    params: [],
  };
}

function aiToolManifest(): NodeManifest {
  return {
    id: "ai_http_tool",
    name: "AI HTTP Tool",
    category: "AI",
    version: "1",
    description: "",
    icon: null,
    role: "tool",
    inputs: [],
    outputs: [port("tool", "ai_tool")],
    params: [],
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

  it("enabling tool mode seeds blank core params as From-AI arguments", () => {
    useEditor.getState().setManifests([
      manifest("execute_command", [param("command", ""), param("method", "GET")]),
    ]);
    useEditor.getState().loadGraph({
      ...GRAPH,
      nodes: [
        {
          ...GRAPH.nodes[0],
          type: "execute_command",
          disabled: true,
          tool_mode: false,
          params: { command: "", method: "GET" },
        },
      ],
    });

    useEditor.getState().updateNodeSettings("n1", { toolMode: true });

    const node = useEditor.getState().toGraph().nodes[0];
    expect(node.params.command).toContain("$fromAI('command'");
    expect(node.params.method).toBe("GET");
    expect(node.disabled).toBe(false);
  });

  it("connecting a disabled AI supplier to an Agent AI port re-enables it", () => {
    useEditor.getState().setManifests([aiMemoryManifest(), aiAgentManifest()]);
    useEditor.getState().loadGraph({
      nodes: [
        {
          ...GRAPH.nodes[0],
          id: "memory",
          type: "ai_buffer_memory",
          disabled: true,
          tool_mode: false,
          params: {},
        },
        {
          ...GRAPH.nodes[0],
          id: "agent",
          type: "ai_agent_v2",
          disabled: false,
          tool_mode: false,
          params: {},
        },
      ],
      edges: [],
    });

    const result = useEditor.getState().onConnect({
      source: "memory",
      sourceHandle: "memory",
      target: "agent",
      targetHandle: "memory",
    });

    expect(result.ok).toBe(true);
    const memoryNode = useEditor.getState().toGraph().nodes.find((node) => node.id === "memory");
    expect(memoryNode?.disabled).toBe(false);
  });

  it("allows multiple tools to connect to an Agent tool port", () => {
    useEditor.getState().setManifests([aiToolManifest(), aiAgentManifest()]);
    useEditor.getState().loadGraph({
      nodes: [
        {
          ...GRAPH.nodes[0],
          id: "toolA",
          type: "ai_http_tool",
          disabled: false,
          tool_mode: false,
          params: {},
        },
        {
          ...GRAPH.nodes[0],
          id: "toolB",
          type: "ai_http_tool",
          disabled: false,
          tool_mode: false,
          params: {},
        },
        {
          ...GRAPH.nodes[0],
          id: "agent",
          type: "ai_agent_v2",
          disabled: false,
          tool_mode: false,
          params: {},
        },
      ],
      edges: [],
    });

    expect(useEditor.getState().onConnect({
      source: "toolA",
      sourceHandle: "tool",
      target: "agent",
      targetHandle: "tool",
    }).ok).toBe(true);
    expect(useEditor.getState().onConnect({
      source: "toolB",
      sourceHandle: "tool",
      target: "agent",
      targetHandle: "tool",
    }).ok).toBe(true);

    const edges = useEditor.getState().toGraph().edges.filter(
      (edge) => edge.target === "agent" && edge.target_input === "tool",
    );
    expect(edges.map((edge) => edge.source).sort()).toEqual(["toolA", "toolB"]);
  });

  it("self-heals existing disabled Agent AI dependencies", () => {
    useEditor.getState().setManifests([aiMemoryManifest(), aiAgentManifest()]);
    useEditor.getState().loadGraph({
      nodes: [
        {
          ...GRAPH.nodes[0],
          id: "memory",
          type: "ai_buffer_memory",
          disabled: true,
          tool_mode: false,
          params: {},
        },
        {
          ...GRAPH.nodes[0],
          id: "agent",
          type: "ai_agent_v2",
          disabled: false,
          tool_mode: false,
          params: {},
        },
      ],
      edges: [
        {
          id: "memory-agent",
          source: "memory",
          source_output: "memory",
          target: "agent",
          target_input: "memory",
        },
      ],
    });

    // Loading normalizes this invariant once; the canvas no longer rescans the
    // full graph after every position update.
    const memoryNode = useEditor.getState().toGraph().nodes.find((node) => node.id === "memory");
    expect(memoryNode?.disabled).toBe(false);
    expect(useEditor.getState().autoEnableAgentDependencies()).toBe(0);
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
