import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NodeIcon } from "./NodeIcon";

describe("NodeIcon", () => {
  it("renders brand icons as local inline SVGs with brand colors", () => {
    for (const [name, fill] of [
      ["brand:slack", "#e040fb"],
      ["brand:microsoftoutlook", "#0078D4"],
      ["brand:amazons3", "#569A31"],
      ["brand:airtable", "#18BFFF"],
      ["brand:stripe", "#635BFF"],
      ["brand:openai", "#9b87e8"],
      ["brand:github", "#d0d0d0"],
      ["brand:notion", "#d0d0d0"],
    ] as const) {
      const { container, unmount } = render(<NodeIcon name={name} size={16} />);

      const svg = container.querySelector("svg.node-brand-icon");
      expect(svg, `${name} should render an svg`).not.toBeNull();
      expect(container.querySelector("img"), `${name} should not render an img`).toBeNull();
      expect(svg?.querySelector("path")?.getAttribute("fill")).toBe(fill);

      unmount();
    }
  });

  it("renders unknown brand icons as Phosphor fallbacks", () => {
    const { container } = render(<NodeIcon name="brand:amazonsns" size={16} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("svg")).not.toBeNull();
  });

  it("renders non-brand icons from the ICON_MAP", () => {
    const { container } = render(<NodeIcon name="play" size={16} />);
    expect(container.querySelector("svg")).not.toBeNull();
  });

  it("renders planned package-level icons from Lucide", () => {
    const { container } = render(<NodeIcon name="camera" size={16} />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(svg?.classList.contains("lucide-camera")).toBe(true);
  });

  it("renders CircleDashed for unknown non-brand icon names", () => {
    const { container } = render(<NodeIcon name="not-a-real-icon" size={16} />);
    expect(container.querySelector("svg")).not.toBeNull();
  });
});
