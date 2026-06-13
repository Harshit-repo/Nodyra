import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { NodeCard } from "./NodeCard";

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

describe("NodeCard", () => {
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
