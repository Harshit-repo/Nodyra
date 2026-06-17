import { describe, expect, it } from "vitest";

import type { NodeManifest, ParamSpec, PortSpec, WorkflowGraph } from "../types";
import { isTriggerNode, pickEditorRunTrigger, useEditor } from "./store";

function port(name: string, data_kind: PortSpec["data_kind"] = "any"): PortSpec {
  return { name, description: "", data_kind };
}

function param(name: string, def: unknown): ParamSpec {
  return {
    name, type: "string", required: false, default: def, description: "",
    placeholder: "", choices: null, multiline: false, key_value: false,
  };
}

function apiManifest(): NodeManifest {
  return {
    id: "api_endpoint", name: "API Endpoint", category: "Triggers", version: "1",
    description: "", icon: null,
    role: "trigger",
    inputs: [], outputs: [port("main")],
    params: [param("base_path", ""), param("routes", [])],
  };
}

const ROUTES = [
  { method: "GET", path: "/", output: "list" },
  { method: "GET", path: "/{id}", output: "read" },
];

function graphWithRoutes(routes: unknown): WorkflowGraph {
  return {
    nodes: [
      {
        id: "api", type: "api_endpoint", params: { base_path: "customers", routes },
        position: { x: 0, y: 0 }, disabled: false, outputs_override: null,
        on_error: "stop", retry_on_fail: false, retries: 1,
        retry_wait_seconds: 0, retry_backoff: false, always_output_data: false,
        timeout_seconds: null,
        tool_mode: false, tool_name: null, tool_description: "",
      },
    ],
    edges: [],
  };
}

describe("store api_endpoint outputs", () => {
  it("loadGraph derives one output handle per route", () => {
    useEditor.getState().setManifests([apiManifest()]);
    useEditor.getState().loadGraph(graphWithRoutes(ROUTES));

    const node = useEditor.getState().nodes[0];
    expect(node.data.outputsOverride).toEqual(["list", "read"]);
  });

  it("updateParams recomputes handles and prunes edges to removed routes", () => {
    useEditor.getState().setManifests([apiManifest()]);
    useEditor.getState().loadGraph(graphWithRoutes(ROUTES));
    // Wire an edge from the (to-be-removed) `read` output.
    useEditor.setState({
      edges: [
        { id: "e1", source: "api", sourceHandle: "read", target: "x", targetHandle: "input" },
        { id: "e2", source: "api", sourceHandle: "list", target: "y", targetHandle: "input" },
      ],
    });

    // Drop the `read` route.
    useEditor.getState().updateParams("api", {
      base_path: "customers",
      routes: [{ method: "GET", path: "/", output: "list" }],
    });

    const node = useEditor.getState().nodes[0];
    expect(node.data.outputsOverride).toEqual(["list"]);
    const handles = useEditor.getState().edges.map((e) => e.sourceHandle);
    expect(handles).toEqual(["list"]);
  });

  it("falls back to a single main output when there are no routes", () => {
    useEditor.getState().setManifests([apiManifest()]);
    useEditor.getState().loadGraph(graphWithRoutes([]));

    const node = useEditor.getState().nodes[0];
    expect(node.data.outputsOverride).toEqual(["main"]);
  });

  it("treats API Endpoint as a trigger", () => {
    useEditor.getState().setManifests([apiManifest()]);
    useEditor.getState().loadGraph(graphWithRoutes(ROUTES));

    const node = useEditor.getState().nodes[0];
    expect(isTriggerNode(node)).toBe(true);
    expect(pickEditorRunTrigger(useEditor.getState().nodes)?.id).toBe("api");
  });
});
