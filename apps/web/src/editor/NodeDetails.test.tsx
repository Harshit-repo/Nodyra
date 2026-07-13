import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Edge } from "@xyflow/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { NodeManifest, PortSpec } from "../types";
import { NodeDetails } from "./NodeDetails";
import { type NodyraNode, useEditor } from "./store";

const apiMocks = vi.hoisted(() => ({
  testNode: vi.fn(),
}));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      testNode: apiMocks.testNode,
    },
  };
});

vi.mock("../hooks/useServerPlatform", () => ({
  useServerPlatform: () => "windows",
}));

function port(name: string): PortSpec {
  return { name, description: "", data_kind: "main" };
}

function manifest(
  id: string,
  opts: { inputs?: PortSpec[]; outputs?: PortSpec[] } = {},
): NodeManifest {
  return {
    id,
    name: id,
    category: "Core",
    version: "1",
    description: "",
    icon: null,
    inputs: opts.inputs ?? [port("input")],
    outputs: opts.outputs ?? [port("main")],
    params: [],
  };
}

function node(
  id: string,
  nodeManifest: NodeManifest,
  params: Record<string, unknown> = {},
): NodyraNode {
  return {
    id,
    type: "nodyra",
    position: { x: 0, y: 0 },
    data: {
      manifest: nodeManifest,
      params,
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
    envId: null,
    envName: null,
    envPackages: [],
    environmentsList: [],
    applyEnvSwitch: null,
  });
}

afterEach(() => {
  apiMocks.testNode.mockReset();
  resetEditor();
});

describe("NodeDetails node testing", () => {
  it("tests the selected node with latest upstream input and stores the result", async () => {
    const triggerManifest = manifest("manual_trigger", {
      inputs: [],
      outputs: [port("main")],
    });
    const codeManifest = manifest("code");
    const edge: Edge = {
      id: "e1",
      source: "t",
      sourceHandle: "main",
      target: "c",
      targetHandle: "input",
    };

    useEditor.setState({
      workflowId: "wf-1",
      manifests: [triggerManifest, codeManifest],
      manifestsById: {
        manual_trigger: triggerManifest,
        code: codeManifest,
      },
      nodes: [
        node("t", triggerManifest, { data: { n: 0 } }),
        node("c", codeManifest, { code: "output = input['n'] + 1" }),
      ],
      edges: [edge],
      runOutputs: { t: { main: { n: 41 } } },
    });
    apiMocks.testNode.mockResolvedValue({
      workflow_id: "wf-1",
      node_id: "c",
      status: "success",
      output: { main: 42 },
      error: null,
      logs: ["done"],
      debug: { variables: [{ name: "output", value: 42 }] },
      started_at: 12,
      finished_at: 12.025,
      duration_ms: 25,
      cached_node_ids: ["t"],
    });

    render(<NodeDetails nodeId="c" />);
    fireEvent.click(screen.getByRole("button", { name: /test this node/i }));

    await waitFor(() =>
      expect(apiMocks.testNode).toHaveBeenCalledWith("wf-1", "c", {
        inputs: { input: { n: 41 } },
        use_pinned: true,
        use_draft: true,
      }),
    );
    await waitFor(() =>
      expect(useEditor.getState().runOutputs.c).toEqual({ main: 42 }),
    );
    expect(useEditor.getState().runStatus.c).toBe("success");
    expect(useEditor.getState().runMeta.c).toMatchObject({
      logs: ["done"],
      error: null,
      durationMs: 25,
      startedAt: 12,
      finishedAt: 12.025,
    });
  });
});
