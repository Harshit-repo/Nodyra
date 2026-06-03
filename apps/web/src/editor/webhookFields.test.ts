import { describe, expect, it } from "vitest";

import type { ParamSpec } from "../types";
import {
  groupActiveByValue,
  paramGroup,
  webhookHiddenParam,
  webhookParamLabel,
} from "./NodeDetails";

const ID = "webhook_trigger";

function spec(
  name: string,
  group: string | null = null,
  def: unknown = "",
): ParamSpec {
  return {
    name,
    type: "string",
    required: false,
    default: def,
    description: "",
    placeholder: "",
    choices: null,
    multiline: false,
    key_value: false,
    group,
  };
}

describe("webhookHiddenParam — conditional Unit-2 fields", () => {
  it("hides HMAC dependent fields unless hmac_verification is on", () => {
    for (const name of ["hmac_header", "hmac_algorithm", "hmac_prefix"]) {
      expect(webhookHiddenParam(ID, name, { hmac_verification: "off" })).toBe(
        true,
      );
      expect(webhookHiddenParam(ID, name, { hmac_verification: "on" })).toBe(
        false,
      );
    }
    // The toggle itself is always visible.
    expect(webhookHiddenParam(ID, "hmac_verification", {})).toBe(false);
  });

  it("hides dedup_key unless dedup is on", () => {
    expect(webhookHiddenParam(ID, "dedup_key", { dedup: "off" })).toBe(true);
    expect(webhookHiddenParam(ID, "dedup_key", { dedup: "on" })).toBe(false);
  });

  it("hides trust_proxy unless an ip_allowlist is set", () => {
    expect(webhookHiddenParam(ID, "trust_proxy", { ip_allowlist: "" })).toBe(
      true,
    );
    expect(
      webhookHiddenParam(ID, "trust_proxy", { ip_allowlist: "10.0.0.0/8" }),
    ).toBe(false);
  });

  it("hides auth_jwt_header unless auth_type is jwt", () => {
    expect(webhookHiddenParam(ID, "auth_jwt_header", { auth_type: "bearer" })).toBe(
      true,
    );
    expect(webhookHiddenParam(ID, "auth_jwt_header", { auth_type: "jwt" })).toBe(
      false,
    );
  });

  it("reveals response_body/response_headers only for Custom response_data", () => {
    for (const name of ["response_body", "response_headers"]) {
      expect(
        webhookHiddenParam(ID, name, {
          response_mode: "On Received",
          response_data: "All Entries",
        }),
      ).toBe(true);
      expect(
        webhookHiddenParam(ID, name, {
          response_mode: "On Received",
          response_data: "Custom",
        }),
      ).toBe(false);
    }
  });

  it("hides response shaping fields outside On Received mode", () => {
    for (const name of ["response_data", "response_body", "response_headers"]) {
      expect(
        webhookHiddenParam(ID, name, { response_mode: "Last Node" }),
      ).toBe(true);
    }
    expect(
      webhookHiddenParam(ID, "response_data", { response_mode: "On Received" }),
    ).toBe(false);
  });

  it("leaves non-webhook manifests untouched", () => {
    expect(webhookHiddenParam("code", "dedup_key", {})).toBe(false);
  });
});

describe("paramGroup — generic optional grouping", () => {
  it("returns the spec's group, or null for a core param", () => {
    expect(paramGroup(spec("ip_allowlist", "Security"))).toBe("Security");
    expect(paramGroup(spec("path"))).toBeNull();
    expect(paramGroup(spec("x", ""))).toBeNull();
  });
});

describe("groupActiveByValue — auto-expand groups with saved values", () => {
  const specs = [
    spec("hmac_verification", "Security", "off"),
    spec("ip_allowlist", "Security", ""),
  ];

  it("is inactive when every grouped param is at its default/empty", () => {
    expect(groupActiveByValue(specs, { hmac_verification: "off" })).toBe(false);
    expect(groupActiveByValue(specs, {})).toBe(false);
  });

  it("is active when any grouped param holds a non-default value", () => {
    expect(groupActiveByValue(specs, { hmac_verification: "on" })).toBe(true);
    expect(groupActiveByValue(specs, { ip_allowlist: "10.0.0.0/8" })).toBe(true);
  });
});

describe("webhookParamLabel — friendly labels", () => {
  it("labels the new toggles", () => {
    expect(webhookParamLabel(ID, "hmac_verification", {})).toBe(
      "HMAC verification",
    );
    expect(webhookParamLabel(ID, "ip_allowlist", {})).toBe("IP allowlist");
    expect(webhookParamLabel(ID, "raw_body", {})).toBe("Capture raw body");
  });
});
