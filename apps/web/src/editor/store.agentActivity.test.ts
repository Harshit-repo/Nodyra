import { beforeEach, describe, expect, it } from "vitest";

import type {
  GraphNode,
  NodeManifest,
  PortSpec,
  WorkflowGraph,
} from "../types";
import { useEditor } from "./store";

function port(name: string, data_kind: PortSpec["data_kind"] = "any"): PortSpec {
  return { name, description: "", data_kind };
}

function manifest(id: string, name = id): NodeManifest {
  return {
    id,
    name,
    category: "Core",
    version: "1",
    description: "",
    icon: null,
    inputs: [port("input")],
    outputs: [port("main")],
    params: [],
  };
}

function agentManifest(): NodeManifest {
  return {
    id: "ai_agent_v2",
    name: "Agent",
    category: "AI",
    version: "1",
    description: "",
    icon: null,
    inputs: [
      port("input"),
      port("model", "ai_language_model"),
      port("memory", "ai_memory"),
      port("tool", "ai_tool"),
    ],
    outputs: [port("main", "main")],
    params: [],
  };
}

function node(
  id: string,
  type: string,
  extra: Partial<GraphNode> = {},
): GraphNode {
  return {
    id,
    type,
    params: {},
    position: { x: 0, y: 0 },
    disabled: false,
    outputs_override: null,
    on_error: "stop",
    retry_on_fail: false,
    retries: 1,
    retry_wait_seconds: 0,
    retry_backoff: false,
    always_output_data: false,
    timeout_seconds: null,
    tool_mode: false,
    tool_name: null,
    tool_description: "",
    ...extra,
  };
}

// A trigger feeds the agent's main input, a model + memory feed the agent's AI
// ports, and a tool-mode node is wired to the agent's tool port.
const GRAPH: WorkflowGraph = {
  nodes: [
    node("trigger", "manual_trigger"),
    node("agent", "ai_agent_v2"),
    node("model", "model_chat"),
    node("memory", "ai_buffer_memory"),
    node("tool1", "http_request", {
      tool_mode: true,
      tool_name: "fetch",
      tool_description: "Fetch a URL",
    }),
  ],
  edges: [
    { id: "e1", source: "trigger", source_output: "main", target: "agent", target_input: "input" },
    { id: "e2", source: "model", source_output: "main", target: "agent", target_input: "model" },
    { id: "e3", source: "memory", source_output: "main", target: "agent", target_input: "memory" },
    { id: "e4", source: "tool1", source_output: "main", target: "agent", target_input: "tool" },
  ],
};

function load(): void {
  useEditor.getState().setManifests([
    manifest("manual_trigger"),
    agentManifest(),
    manifest("model_chat"),
    manifest("ai_buffer_memory"),
    manifest("http_request"),
  ]);
  useEditor.getState().loadGraph(GRAPH);
}

describe("store agent activity highlighting", () => {
  beforeEach(() => {
    load();
  });

  it("startRun marks plain nodes running but leaves agent sub-nodes idle", () => {
    useEditor.getState().startRun("run-1");
    const { runStatus, agentActive } = useEditor.getState();

    expect(runStatus.trigger).toBe("running");
    expect(runStatus.agent).toBe("running");
    // Model / memory / tool sub-nodes wait for live agent events.
    expect(runStatus.model).toBeUndefined();
    expect(runStatus.memory).toBeUndefined();
    expect(runStatus.tool1).toBeUndefined();
    expect(agentActive).toEqual({});
  });

  it("agent node_started lights up its model and memory providers", () => {
    useEditor.getState().startRun("run-1");
    useEditor.getState().applyRunEvent({ type: "node_started", node_id: "agent" });

    const { agentActive } = useEditor.getState();
    expect(agentActive.model).toBe("running");
    expect(agentActive.memory).toBe("running");
    expect(agentActive.tool1).toBeUndefined();
  });

  it("agent_tool_started resolves the tool node by name and marks it running", () => {
    useEditor.getState().startRun("run-1");
    useEditor.getState().applyRunEvent({
      type: "agent_tool_started",
      agent_node_id: "agent",
      tool_name: "fetch",
      tool_call_id: "call-1",
    });

    const { agentActive, agentToolCalls } = useEditor.getState();
    expect(agentActive.tool1).toBe("running");
    expect(agentToolCalls["call-1"]).toBe("tool1");
  });

  it("agent_action_requested lights pending tool calls before they start", () => {
    useEditor.getState().startRun("run-1");
    useEditor.getState().applyRunEvent({
      type: "agent_action_requested",
      agent_node_id: "agent",
      step: 0,
      tool_calls: [{ id: "call-1", name: "fetch" }],
    });

    const { agentActive, agentToolCalls } = useEditor.getState();
    expect(agentActive.tool1).toBe("running");
    expect(agentToolCalls["call-1"]).toBe("tool1");
  });

  it("agent_tool_finished marks the tool node done and clears the call", () => {
    useEditor.getState().startRun("run-1");
    useEditor.getState().applyRunEvent({
      type: "agent_tool_started",
      agent_node_id: "agent",
      tool_name: "fetch",
      tool_call_id: "call-1",
    });
    useEditor.getState().applyRunEvent({
      type: "agent_tool_finished",
      agent_node_id: "agent",
      tool_name: "fetch",
      tool_call_id: "call-1",
      status: "success",
    });

    const { agentActive, agentToolCalls } = useEditor.getState();
    expect(agentActive.tool1).toBe("done");
    expect(agentToolCalls["call-1"]).toBeUndefined();
  });

  it("agent_tool_finished surfaces an error status", () => {
    useEditor.getState().startRun("run-1");
    useEditor.getState().applyRunEvent({
      type: "agent_tool_started",
      agent_node_id: "agent",
      tool_name: "fetch",
      tool_call_id: "call-1",
    });
    useEditor.getState().applyRunEvent({
      type: "agent_tool_finished",
      agent_node_id: "agent",
      tool_name: "fetch",
      tool_call_id: "call-1",
      status: "error",
    });

    expect(useEditor.getState().agentActive.tool1).toBe("error");
  });

  it("keeps a tool tile running until all parallel calls finish (ref-counted)", () => {
    useEditor.getState().startRun("run-1");
    const start = (callId: string) =>
      useEditor.getState().applyRunEvent({
        type: "agent_tool_started",
        agent_node_id: "agent",
        tool_name: "fetch",
        tool_call_id: callId,
      });
    start("call-1");
    start("call-2");
    expect(useEditor.getState().agentActive.tool1).toBe("running");

    useEditor.getState().applyRunEvent({
      type: "agent_tool_finished",
      agent_node_id: "agent",
      tool_name: "fetch",
      tool_call_id: "call-1",
      status: "success",
    });
    // Still busy because call-2 is in flight.
    expect(useEditor.getState().agentActive.tool1).toBe("running");

    useEditor.getState().applyRunEvent({
      type: "agent_tool_finished",
      agent_node_id: "agent",
      tool_name: "fetch",
      tool_call_id: "call-2",
      status: "success",
    });
    expect(useEditor.getState().agentActive.tool1).toBe("done");
  });

  it("agent node_finished releases every connected sub-node", () => {
    useEditor.getState().startRun("run-1");
    useEditor.getState().applyRunEvent({ type: "node_started", node_id: "agent" });
    useEditor.getState().applyRunEvent({
      type: "agent_tool_started",
      agent_node_id: "agent",
      tool_name: "fetch",
      tool_call_id: "call-1",
    });

    useEditor.getState().applyRunEvent({
      type: "node_finished",
      node_id: "agent",
      status: "success",
      outputs: {},
    });

    const { agentActive, agentToolCalls } = useEditor.getState();
    expect(agentActive).toEqual({});
    expect(agentToolCalls).toEqual({});
  });

  it("terminal run events reset all agent activity", () => {
    useEditor.getState().startRun("run-1");
    useEditor.getState().applyRunEvent({ type: "node_started", node_id: "agent" });
    expect(useEditor.getState().agentActive.model).toBe("running");

    useEditor.getState().applyRunEvent({ type: "run_finished", status: "success" });

    expect(useEditor.getState().agentActive).toEqual({});
    expect(useEditor.getState().agentToolCalls).toEqual({});
  });

  it("clearRun resets agent activity", () => {
    useEditor.getState().startRun("run-1");
    useEditor.getState().applyRunEvent({ type: "node_started", node_id: "agent" });
    useEditor.getState().clearRun();

    expect(useEditor.getState().agentActive).toEqual({});
    expect(useEditor.getState().agentToolCalls).toEqual({});
  });
});
