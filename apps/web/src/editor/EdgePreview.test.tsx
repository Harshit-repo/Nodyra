import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  EdgePreview,
  edgePreviewValue,
  formatEdgePreviewValue,
} from "./EdgePreview";

describe("edgePreviewValue", () => {
  it("extracts the requested source output port", () => {
    expect(edgePreviewValue({ main: { id: 1 }, false: "branch" }, "false")).toEqual({
      hasValue: true,
      value: "branch",
    });
  });

  it("uses the main port by default", () => {
    expect(edgePreviewValue({ main: [1, 2] }, null)).toEqual({
      hasValue: true,
      value: [1, 2],
    });
  });

  it("returns an empty preview when the port has no captured output", () => {
    expect(edgePreviewValue({ other: 1 }, "main")).toEqual({
      hasValue: false,
      value: undefined,
    });
  });
});

describe("formatEdgePreviewValue", () => {
  it("formats objects as readable JSON", () => {
    expect(formatEdgePreviewValue({ id: 1, ok: true })).toContain('"ok": true');
  });

  it("truncates large values", () => {
    const text = formatEdgePreviewValue("x".repeat(80), 24);
    expect(text).toContain("... truncated (80 chars)");
    expect(text.length).toBeLessThan(60);
  });
});

describe("EdgePreview", () => {
  it("opens the source output panel from the action", () => {
    const onOpen = vi.fn();
    render(
      <EdgePreview
        sourceLabel="fetch.main"
        typeLabel="object"
        value={{ count: 2 }}
        onOpen={onOpen}
      />,
    );

    expect(screen.getByText("fetch.main")).toBeTruthy();
    expect(screen.getByText("object")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Open output" }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
