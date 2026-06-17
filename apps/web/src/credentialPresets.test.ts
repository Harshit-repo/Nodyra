import { describe, expect, it } from "vitest";

import {
  CREDENTIAL_PRESETS,
  authMethodLabel,
  credentialTypePreset,
  fieldInputType,
  mergedCredentialPresets,
  presetInitialValues,
} from "./credentialPresets";
import type { CredentialTypeInfo } from "./types";

function typeInfo(overrides: Partial<CredentialTypeInfo>): CredentialTypeInfo {
  return {
    id: "acme",
    name: "Acme",
    provider: "Acme Inc",
    auth_method: "api_key",
    fields: [],
    oauth: null,
    test_service: null,
    documentation_url: "",
    default_scopes: [],
    ...overrides,
  };
}

describe("mergedCredentialPresets", () => {
  it("returns the built-in presets unchanged when no backend types load", () => {
    expect(mergedCredentialPresets(null)).toBe(CREDENTIAL_PRESETS);
  });

  it("adds backend types and overrides built-ins of the same id", () => {
    const merged = mergedCredentialPresets([
      typeInfo({ id: "openai", name: "OpenAI (backend)", provider: "OpenAI" }),
      typeInfo({ id: "acme", name: "Acme", provider: "Acme Inc" }),
    ]);
    const openai = merged.find((p) => p.type === "openai");
    expect(openai?.label).toBe("OpenAI (backend)");
    expect(merged.some((p) => p.type === "acme")).toBe(true);
  });

  it("sorts presets by group then label", () => {
    const merged = mergedCredentialPresets([
      typeInfo({ id: "z", name: "Zebra", provider: "Aardvark" }),
      typeInfo({ id: "a", name: "Apple", provider: "Aardvark" }),
    ]);
    const groupItems = merged.filter((p) => p.group === "Aardvark");
    expect(groupItems.map((p) => p.label)).toEqual(["Apple", "Zebra"]);
  });
});

describe("credentialTypePreset", () => {
  it("flags oauth2 types and synthesizes manual token fields when none are declared", () => {
    const preset = credentialTypePreset(
      typeInfo({
        id: "google_drive",
        auth_method: "oauth2",
        default_scopes: ["drive.readonly"],
      }),
    );
    expect(preset.authMethod).toBe("oauth2");
    expect(preset.defaultScopes).toEqual(["drive.readonly"]);
    expect(preset.fields.map((f) => f.key)).toContain("access_token");
  });

  it("maps secret fields to password and json fields to textarea", () => {
    const preset = credentialTypePreset(
      typeInfo({
        fields: [
          { key: "api_key", label: "Key", secret: true, required: true, placeholder: "", help: "" },
          { key: "config_json", label: "Config", secret: false, required: false, placeholder: "", help: "" },
        ],
      }),
    );
    const byKey = Object.fromEntries(preset.fields.map((f) => [f.key, f.kind]));
    expect(byKey.api_key).toBe("password");
    expect(byKey.config_json).toBe("textarea");
  });
});

describe("presetInitialValues", () => {
  it("seeds each field with its default value or empty string", () => {
    const values = presetInitialValues({
      id: "smtp",
      type: "smtp",
      label: "SMTP",
      group: "Messaging",
      summary: "",
      description: "",
      fields: [
        { key: "host", label: "Host" },
        { key: "port", label: "Port", defaultValue: "587" },
      ],
    });
    expect(values).toEqual({ host: "", port: "587" });
  });
});

describe("fieldInputType", () => {
  it("maps field kinds to input types", () => {
    expect(fieldInputType({ key: "p", label: "P", kind: "password" })).toBe("password");
    expect(fieldInputType({ key: "n", label: "N", kind: "number" })).toBe("number");
    expect(fieldInputType({ key: "t", label: "T" })).toBe("text");
  });
});

describe("authMethodLabel", () => {
  it("humanizes known auth methods and passes through unknown ones", () => {
    expect(authMethodLabel("api_key")).toBe("API key");
    expect(authMethodLabel("oauth2")).toBe("OAuth2");
    expect(authMethodLabel("service_account")).toBe("Service account");
    expect(authMethodLabel("weird")).toBe("weird");
  });
});
