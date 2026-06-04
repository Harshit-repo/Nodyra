import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NodeIcon } from "./NodeIcon";

describe("NodeIcon", () => {
  it("renders brand icons from Simple Icons and falls back when loading fails", () => {
    const { container } = render(<NodeIcon name="brand:slack" size={16} />);

    const img = container.querySelector("img.node-brand-icon");
    expect(img).not.toBeNull();
    expect(img?.getAttribute("src")).toContain("cdn.simpleicons.org/slack");
    expect(img?.getAttribute("loading")).toBeNull();

    fireEvent.error(img as HTMLImageElement);

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("svg")).not.toBeNull();
  });
});
