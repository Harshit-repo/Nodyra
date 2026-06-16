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

function loopStartManifest(): NodeManifest {
  return {
    id: "loop_start", name: "Loop Start", category: "Logic", version: "1",
    description: "", icon: "repeat",
    inputs: [port("input")], outputs: [port("item"), port("index")],
    params: [param("concurrency", 1), param("on_error", "fail"), param("max_rows", 10000)],
  };
}

function loopEndManifest(): NodeManifest {
  return {
    id: "loop_end", name: "Loop End", category: "Logic", version: "1",
    description: "", icon: "repeat",
    inputs: [port("input")], outputs: [port("results"), port("errors")],
    params: [param("loop_start_id", ""), param("output_mode", "records")],
  };
}

function reset() {
  useEditor.getState().loadGraph({ nodes: [], edges: [] });
  useEditor.getState().setManifests([loopStartManifest(), loopEndManifest()]);
}

describe("loop node authoring", () => {
  it("dropping loop_start also drops a paired loop_end", () => {
    reset();
    useEditor.getState().addNode("loop_start", { x: 100, y: 100 });

    const nodes = useEditor.getState().nodes;
    const start = nodes.find((n) => n.data.manifest?.id === "loop_start");
    const end = nodes.find((n) => n.data.manifest?.id === "loop_end");
    expect(start).toBeTruthy();
    expect(end).toBeTruthy();
    expect(end!.data.params.loop_start_id).toBe(start!.id);
    // end sits to the right of start, selection lands on the start
    expect(end!.position.x).toBeGreaterThan(start!.position.x);
    expect(useEditor.getState().selectedId).toBe(start!.id);
  });

  it("deleting loop_start unpairs the surviving loop_end", () => {
    reset();
    useEditor.getState().addNode("loop_start", { x: 0, y: 0 });
    const start = useEditor.getState().nodes.find((n) => n.data.manifest?.id === "loop_start")!;

    useEditor.getState().deleteNode(start.id);

    const nodes = useEditor.getState().nodes;
    expect(nodes.some((n) => n.id === start.id)).toBe(false);
    const end = nodes.find((n) => n.data.manifest?.id === "loop_end");
    expect(end).toBeTruthy();
    expect(end!.data.params.loop_start_id).toBe("");
  });

  it("deleteSelection unpairs loop_end when the loop_start is removed", () => {
    reset();
    useEditor.getState().addNode("loop_start", { x: 0, y: 0 });
    const start = useEditor.getState().nodes.find((n) => n.data.manifest?.id === "loop_start")!;
    // select only the start
    useEditor.setState({ selectedId: start.id });

    useEditor.getState().deleteSelection();

    const end = useEditor.getState().nodes.find((n) => n.data.manifest?.id === "loop_end");
    expect(end).toBeTruthy();
    expect(end!.data.params.loop_start_id).toBe("");
  });

  it("tracks iteration count for a loop body node from iteration_path", () => {
    reset();
    const s = useEditor.getState();
    s.clearRun();
    s.applyRunEvent({ type: "node_started", node_id: "body", iteration_path: [0] });
    s.applyRunEvent({
      type: "node_finished", node_id: "body", status: "success",
      outputs: { main: 0 }, iteration_path: [0],
    });
    s.applyRunEvent({ type: "node_started", node_id: "body", iteration_path: [1] });
    s.applyRunEvent({
      type: "node_finished", node_id: "body", status: "success",
      outputs: { main: 1 }, iteration_path: [1],
    });
    s.applyRunEvent({ type: "node_started", node_id: "body", iteration_path: [2] });

    const iter = useEditor.getState().runIterations["body"];
    expect(iter).toEqual({ index: 2, count: 3 });
  });

  it("keeps the prior loop-body output visible between iterations (no flicker)", () => {
    reset();
    const s = useEditor.getState();
    s.clearRun();
    s.applyRunEvent({ type: "node_started", node_id: "body", iteration_path: [0] });
    s.applyRunEvent({
      type: "node_finished", node_id: "body", status: "success",
      outputs: { main: "iter0" }, iteration_path: [0],
    });
    // The next iteration starting must NOT wipe the last visible output.
    s.applyRunEvent({ type: "node_started", node_id: "body", iteration_path: [1] });

    expect(useEditor.getState().runOutputs["body"]).toEqual({ main: "iter0" });
    expect(useEditor.getState().runStatus["body"]).toBe("running");
  });

  it("a top-level (non-loop) node_started still clears its prior output", () => {
    reset();
    const s = useEditor.getState();
    s.clearRun();
    s.applyRunEvent({
      type: "node_finished", node_id: "n", status: "success", outputs: { main: 1 },
    });
    s.applyRunEvent({ type: "node_started", node_id: "n" });

    expect(useEditor.getState().runOutputs["n"]).toBeUndefined();
    expect(useEditor.getState().runIterations["n"]).toBeUndefined();
  });

  it("clearRun resets iteration tracking", () => {
    reset();
    const s = useEditor.getState();
    s.applyRunEvent({ type: "node_started", node_id: "body", iteration_path: [0] });
    expect(useEditor.getState().runIterations["body"]).toBeTruthy();
    useEditor.getState().clearRun();
    expect(useEditor.getState().runIterations).toEqual({});
  });

  it("a while Loop Start round-trips its mode/condition params via toGraph()", () => {
    reset();
    const graph: WorkflowGraph = {
      nodes: [
        {
          id: "s", type: "loop_start",
          params: {
            mode: "while",
            initial: { count: 0 },
            condition: "{{ state.count < 10 }}",
            max_iterations: 50,
            on_max_iterations: "stop",
          },
          position: { x: 0, y: 0 }, disabled: false, outputs_override: null,
          on_error: "stop", retry_on_fail: false, retries: 1,
          retry_wait_seconds: 0, retry_backoff: false, always_output_data: false,
          timeout_seconds: null,
        },
      ],
      edges: [],
    };
    useEditor.getState().loadGraph(graph);

    const node = useEditor.getState().toGraph().nodes[0];
    expect(node.params.mode).toBe("while");
    expect(node.params.condition).toBe("{{ state.count < 10 }}");
    expect(node.params.initial).toEqual({ count: 0 });
    expect(node.params.max_iterations).toBe(50);
    expect(node.params.on_max_iterations).toBe("stop");
  });
});
