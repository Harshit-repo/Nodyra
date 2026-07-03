import type { Edge } from "@xyflow/react";
import { describe, expect, it } from "vitest";

import type { NodeManifest, PortSpec } from "../types";
import { deriveEdgeType, getAgentEdgeFlowClass } from "./NodyraEdge";
import type { AgentActivityStatus, NodyraNode } from "./store";

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

function node(id: string, nodeManifest: NodeManifest, toolMode = false): NodyraNode {
  return {
    id,
    type: "nodyra",
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

describe("deriveEdgeType", () => {
  it("returns string icon for a string value", () => {
    expect(deriveEdgeType("hello")).toEqual({ icon: "T", label: "string" });
  });

  it("returns number icon for a number value", () => {
    expect(deriveEdgeType(42)).toEqual({ icon: "#", label: "number" });
  });

  it("returns boolean icon for a boolean value", () => {
    expect(deriveEdgeType(true)).toEqual({ icon: "⊤", label: "boolean" });
  });

  it("returns array icon with count for arrays", () => {
    expect(deriveEdgeType([1, 2, 3])).toEqual({ icon: "[]", label: "array · 3" });
  });

  it("returns object icon for plain objects", () => {
    expect(deriveEdgeType({ a: 1 })).toEqual({ icon: "{}", label: "object" });
  });

  it("returns null icon for null", () => {
    expect(deriveEdgeType(null)).toEqual({ icon: "∅", label: "null" });
  });

  it("returns empty string when value is undefined (no run yet)", () => {
    expect(deriveEdgeType(undefined)).toEqual({ icon: "", label: "" });
  });
});

describe("agent edge flow class", () => {
  it("rejects ordinary data edges without scanning graph state", () => {
    const inaccessible = new Proxy([], {
      get() {
        throw new Error("ordinary edges must not inspect graph collections");
      },
    });

    expect(getAgentEdgeFlowClass({
      agentActive: {},
      agentToolCalls: {},
      edges: inaccessible as Edge[],
      nodes: inaccessible as NodyraNode[],
      runStatus: {},
    }, {
      id: "ordinary",
      source: "source",
      target: "target",
      targetHandleId: "input",
    })).toBeUndefined();
  });

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
