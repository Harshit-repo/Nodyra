import type { Edge } from "@xyflow/react";
import { describe, expect, it } from "vitest";

import type { NodeManifest, PortSpec } from "../types";
import { getAgentEdgeFlowClass } from "./NoodleEdge";
import type { AgentActivityStatus, NoodleNode } from "./store";

function port(name: string, data_kind: PortSpec["data_kind"] = "any"): PortSpec {
  return { name, description: "", data_kind };
}

function manifest(id: string, inputs: PortSpec[], outputs: PortSpec[]): NodeManifest {
  return {
    id,
    name: id,
    category: "Core",
    version: "1",
    description: "",
    icon: null,
    inputs,
    outputs,
    params: [],
  };
}

const modelManifest = manifest("openai_chat_model", [], [port("main", "ai_language_model")]);
const memoryManifest = manifest("memory", [], [port("main", "ai_memory")]);
const toolManifest = manifest("http_request", [port("input")], [port("main")]);
const agentManifest = manifest("ai_agent", [
  port("model", "ai_language_model"),
  port("memory", "ai_memory"),
  port("tool", "ai_tool"),
], [port("main")]);

function node(id: string, nodeManifest: NodeManifest, toolMode = false): NoodleNode {
  return {
    id,
    type: "noodle",
    position: { x: 0, y: 0 },
    data: {
      manifest: nodeManifest,
      params: {},
      disabled: false,
      outputsOverride: null,
      onError: "stop",
      retryOnFail: false,
      retries: 1,
      retryWaitSeconds: 0,
      retryBackoff: false,
      alwaysOutputData: false,
      timeoutSeconds: null,
      toolMode,
    },
  };
}

const nodes = [
  node("model", modelManifest),
  node("memory", memoryManifest),
  node("tool", toolManifest, true),
  node("agent", agentManifest),
];

function state(overrides: {
  agentActive?: Record<string, AgentActivityStatus>;
  agentToolCalls?: Record<string, string>;
  edges?: Edge[];
  runStatus?: Record<string, string>;
}) {
  return {
    agentActive: overrides.agentActive ?? {},
    agentToolCalls: overrides.agentToolCalls ?? {},
    edges: overrides.edges ?? [],
    nodes,
    runStatus: overrides.runStatus ?? {},
  };
}

describe("agent edge flow class", () => {
  it("animates model and memory provider edges while the agent node is running", () => {
    const edges: Edge[] = [
      { id: "model-agent", source: "model", target: "agent", targetHandle: "model" },
      { id: "memory-agent", source: "memory", target: "agent", targetHandle: "memory" },
    ];

    expect(getAgentEdgeFlowClass(state({ edges, runStatus: { agent: "running" } }), {
      id: "model-agent",
      source: "model",
      target: "agent",
      targetHandleId: "model",
    })).toBe("edge-agent-flow");
    expect(getAgentEdgeFlowClass(state({ edges, runStatus: { agent: "running" } }), {
      id: "memory-agent",
      source: "memory",
      target: "agent",
      targetHandleId: "memory",
    })).toBe("edge-agent-flow");
  });

  it("animates tool provider edges only while that tool call is active", () => {
    const edges: Edge[] = [
      { id: "tool-agent", source: "tool", target: "agent", sourceHandle: "tool", targetHandle: "tool" },
    ];

    expect(getAgentEdgeFlowClass(state({
      agentToolCalls: { call_1: "tool" },
      edges,
      runStatus: { agent: "running" },
    }), {
      id: "tool-agent",
      source: "tool",
      target: "agent",
      targetHandleId: "tool",
    })).toBe("edge-agent-flow is-tool");
    expect(getAgentEdgeFlowClass(state({ edges, runStatus: { agent: "running" } }), {
      id: "tool-agent",
      source: "tool",
      target: "agent",
      targetHandleId: "tool",
    })).toBeUndefined();
  });
});
