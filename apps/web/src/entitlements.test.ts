import { describe, expect, it } from "vitest";

import { computeEntitlements } from "./entitlements";

describe("computeEntitlements", () => {
  it("defaults to community with no limits at zero", () => {
    const e = computeEntitlements({});
    expect(e.edition).toBe("community");
    expect(e.has("sandbox")).toBe(false);
    expect(e.atLimit("environments", 999)).toBe(false); // unknown cap = 0 = unlimited
  });

  it("atLimit true when a finite cap is reached", () => {
    const e = computeEntitlements({
      edition: "community",
      entitlements: [],
      limits: { environments: 3 },
      license_notice: null,
    });
    expect(e.atLimit("environments", 3)).toBe(true);
    expect(e.atLimit("environments", 2)).toBe(false);
  });

  it("atLimit false when the cap is 0 (unlimited)", () => {
    const e = computeEntitlements({
      edition: "enterprise",
      limits: { environments: 0 },
    });
    expect(e.atLimit("environments", 1000)).toBe(false);
  });

  it("has() reflects entitlements", () => {
    const e = computeEntitlements({ edition: "pro", entitlements: ["sandbox"] });
    expect(e.has("sandbox")).toBe(true);
    expect(e.has("sso")).toBe(false);
  });

  it("exposes the license notice", () => {
    const e = computeEntitlements({ license_notice: "License expired" });
    expect(e.notice).toBe("License expired");
  });
});
