import { describe, expect, it } from "vitest";

import { buildLoadOptionsParams, mergeOptions } from "./NodeDetails";

describe("buildLoadOptionsParams", () => {
  it("includes credential_id + provider/base_url from params, skips empties", () => {
    const ref = { __noodle_credential__: true, id: "cred1", key: "*" };
    const out = buildLoadOptionsParams(ref, {
      provider: "ollama",
      base_url: "",
      model: "x",
    });
    expect(out).toEqual({ credential_id: "cred1", provider: "ollama" });
  });

  it("returns provider-only when no credential is selected", () => {
    const out = buildLoadOptionsParams(null, { provider: "openai" });
    expect(out).toEqual({ provider: "openai" });
  });
});

describe("mergeOptions", () => {
  it("keeps a typed free-text value that is not in the fetched list", () => {
    const merged = mergeOptions(["gpt-4o", "gpt-4o-mini"], "my-custom-model");
    expect(merged[0]).toBe("my-custom-model");
    expect(merged).toContain("gpt-4o");
  });

  it("does not duplicate a current value already present", () => {
    const merged = mergeOptions(["gpt-4o"], "gpt-4o");
    expect(merged.filter((m) => m === "gpt-4o")).toHaveLength(1);
  });
});
