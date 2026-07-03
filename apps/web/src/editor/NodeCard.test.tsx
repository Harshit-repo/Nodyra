import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Edge } from "@xyflow/react";
import { NodeCard, hasTriggerUpstream } from "./NodeCard";
import { type NodyraNode, useEditor } from "./store";

vi.mock("@xyflow/react", async () => {
  const React = await import("react");
  return {
    Position: {
      Bottom: "bottom",
      Left: "left",
      Right: "right",
      Top: "top",
    },
    Handle: (props: {
      id?: string;
      title?: string;
      position: string;
      className?: string;
      style?: Record<string, unknown>;
    }) =>
      React.createElement("div", {
        "data-testid": `handle-${props.id ?? "default"}`,
        "data-position": props.position,
        className: props.className,
        style: props.style,
        title: props.title,
      }),
    useUpdateNodeInternals: () => () => {},
  };
});

vi.mock("../hooks/useServerPlatform", () => ({
  useServerPlatform: () => "windows",
}));

function toolModeNodeData() {
  return {
    disabled: false,
    label: "Execute Command",
    outputsOverride: undefined,
    params: {},
    toolMode: true,
    manifest: {
      id: "execute_command",
      name: "Execute Command",
      category: "System",
      icon: "terminal",
      inputs: [{ name: "input", data_kind: "main" }],
      outputs: [{ name: "main", data_kind: "main" }],
      params: [],
      requirements: [],
      param_output_kinds: {},
    },
  };
}

function standardNodeData() {
  return {
    disabled: false,
    label: "Set",
    outputsOverride: undefined,
    params: {},
    toolMode: false,
    manifest: {
      id: "set",
      name: "Set",
      category: "Data",
      icon: "pencil",
      inputs: [{ name: "input", data_kind: "main" }],
      outputs: [{ name: "main", data_kind: "main" }],
      params: [],
      requirements: [],
      param_output_kinds: {},
    },
  };
}

describe("NodeCard", () => {
  it("finds an upstream trigger through a large iterative graph walk", () => {
    const count = 2_000;
    const nodes = Array.from({ length: count }, (_, index) => ({
      id: `n${index}`,
      type: "nodyra",
      position: { x: index, y: 0 },
      data: {
        ...standardNodeData(),
        manifest: {
          ...standardNodeData().manifest,
          id: index === 0 ? "manual_trigger" : "set",
          category: index === 0 ? "Triggers" : "Data",
        },
      },
    })) as unknown as NodyraNode[];
    const edges: Edge[] = Array.from({ length: count - 1 }, (_, index) => ({
      id: `e${index}`,
      source: `n${index}`,
      target: `n${index + 1}`,
    }));

    expect(hasTriggerUpstream(nodes, edges, `n${count - 1}`)).toBe(true);
    expect(hasTriggerUpstream(nodes, edges.slice(1), `n${count - 1}`)).toBe(false);
  });

  it("shows a loop iteration badge when a node ran multiple iterations", () => {
    useEditor.setState({
      runStatus: { loopbody: "running" },
      runIterations: { loopbody: { index: 11, count: 12 } },
    });
    const props = {
      id: "loopbody",
      data: standardNodeData(),
      selected: false,
    } as unknown as Parameters<typeof NodeCard>[0];
    render(<NodeCard {...props} />);

    expect(screen.getByText("×12")).toBeTruthy();
    useEditor.getState().clearRun();
  });

  it("shows streamed token text while a node is running", () => {
    useEditor.setState({
      runStatus: { llm: "running" },
      runChunks: { llm: "Hello wor" },
    });
    const props = {
      id: "llm",
      data: standardNodeData(),
      selected: false,
    } as unknown as Parameters<typeof NodeCard>[0];
    render(<NodeCard {...props} />);

    expect(screen.getByText("Hello wor")).toBeTruthy();
    useEditor.getState().clearRun();
  });

  it("renders tool-mode output on the top without a visible port tag", () => {
    const props = {
      id: "cmd",
      data: toolModeNodeData(),
      selected: false,
    } as unknown as Parameters<typeof NodeCard>[0];
    render(<NodeCard {...props} />);

    const handle = screen.getByTestId("handle-tool");
    expect(handle.getAttribute("data-position")).toBe("top");
    expect(handle).toHaveClass("handle-ai-tools");
    expect(handle).toHaveStyle({ left: "50%", top: "-5px" });
    expect(screen.queryByText("tool")).toBeNull();
  });
});
