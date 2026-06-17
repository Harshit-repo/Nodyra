import { describe, expect, it } from "vitest";

import { applyResourceOperation, matchesDisplayWhen } from "./NodeDetails";
import type { NodeManifest } from "../types";

describe("matchesDisplayWhen", () => {
  it("treats absent/non-object display_when as always visible", () => {
    expect(matchesDisplayWhen(null, {})).toBe(true);
    expect(matchesDisplayWhen(undefined, {})).toBe(true);
  });

  it("matches a single param value and values[] form", () => {
    expect(matchesDisplayWhen({ param: "op", value: "send" }, { op: "send" })).toBe(true);
    expect(matchesDisplayWhen({ param: "op", value: "send" }, { op: "read" })).toBe(false);
    expect(
      matchesDisplayWhen({ param: "op", values: ["a", "b"] }, { op: "b" }),
    ).toBe(true);
  });

  it("requires every condition (AND)", () => {
    const dw = {
      conditions: [
        { param: "resource", value: "message" },
        { param: "operation", value: "send" },
      ],
    };
    expect(matchesDisplayWhen(dw, { resource: "message", operation: "send" })).toBe(true);
    expect(matchesDisplayWhen(dw, { resource: "message", operation: "delete" })).toBe(false);
  });

  it("matches at least one group (OR of AND groups)", () => {
    const dw = {
      any: [
        {
          conditions: [
            { param: "resource", value: "message" },
            { param: "operation", value: "send" },
          ],
        },
        {
          conditions: [
            { param: "resource", value: "message" },
            { param: "operation", value: "update" },
          ],
        },
      ],
    };
    expect(matchesDisplayWhen(dw, { resource: "message", operation: "update" })).toBe(true);
    expect(matchesDisplayWhen(dw, { resource: "message", operation: "delete" })).toBe(false);
  });
});

describe("applyResourceOperation", () => {
  const manifest = {
    id: "slack",
    params: [
      { name: "channel", display_when: { param: "resource", value: "message" } },
      { name: "reaction_name", display_when: { param: "resource", value: "reaction" } },
      { name: "credentials" }, // no display_when → always kept
    ],
  } as unknown as NodeManifest;

  it("sets the new selection and prunes params hidden under it", () => {
    const next = applyResourceOperation(
      manifest,
      { resource: "message", operation: "send", channel: "C1", credentials: { id: "c" } },
      "reaction",
      "add",
    );
    expect(next.resource).toBe("reaction");
    expect(next.operation).toBe("add");
    // channel belongs to the message resource → pruned.
    expect("channel" in next).toBe(false);
    // credential has no display_when → preserved.
    expect(next.credentials).toEqual({ id: "c" });
  });
});
