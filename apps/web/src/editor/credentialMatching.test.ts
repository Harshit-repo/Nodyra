import { describe, expect, it } from "vitest";

import type { Credential, CredentialParamSpec } from "../types";
import { credentialMatchesParam } from "./NodeDetails";

function cred(partial: Partial<Credential>): Credential {
  return {
    id: "c1",
    name: "Cred",
    type: "llm_provider",
    auth_method: null,
    scope: "global",
    workflow_id: null,
    environment_id: null,
    runner_pool_id: null,
    description: "",
    keys: [],
    oauth_scopes: [],
    oauth_expires_at: null,
    last_used_at: null,
    created_at: "",
    updated_at: "",
    ...partial,
  };
}

// The AI Chat node's "Credentials" picker declares all the fields any provider
// might use, but a given credential only stores the ones with values.
const LLM_META: CredentialParamSpec = {
  type: "llm_provider",
  key: "*",
  label: "LLM provider credential",
  fields: [
    "provider",
    "api_key",
    "base_url",
    "organization",
    "azure_endpoint",
    "azure_api_version",
    "deployment",
  ],
  multi: true,
};

describe("credentialMatchesParam — multi-field pickers", () => {
  it("matches an Ollama credential that has no api_key", () => {
    // Regression: previously required every declared field, so a partially
    // filled credential showed as "Missing".
    const ollama = cred({ keys: ["base_url", "provider"] });
    expect(credentialMatchesParam(ollama, LLM_META, "*")).toBe(true);
  });

  it("matches an OpenAI credential that only has api_key", () => {
    const openai = cred({ keys: ["api_key"] });
    expect(credentialMatchesParam(openai, LLM_META, "*")).toBe(true);
  });

  it("rejects a credential carrying none of the declared fields", () => {
    const unrelated = cred({ type: "llm_provider", keys: ["webhook_url"] });
    expect(credentialMatchesParam(unrelated, LLM_META, "*")).toBe(false);
  });

  it("rejects a credential of an unrelated type", () => {
    const slack = cred({ type: "slack_bot", keys: ["api_key"] });
    expect(credentialMatchesParam(slack, LLM_META, "*")).toBe(false);
  });

  it("accepts generic catch-all credential types", () => {
    const generic = cred({ type: "generic", keys: ["api_key"] });
    expect(credentialMatchesParam(generic, LLM_META, "*")).toBe(true);
  });
});

describe("credentialMatchesParam — single-field pickers", () => {
  const meta: CredentialParamSpec = {
    type: "openai",
    key: "api_key",
    label: "OpenAI API Key",
    fields: ["api_key"],
    multi: false,
  };

  it("matches when the credential carries the target key", () => {
    expect(credentialMatchesParam(cred({ type: "openai", keys: ["api_key"] }), meta, "api_key")).toBe(true);
  });

  it("does not match when the target key is absent", () => {
    expect(credentialMatchesParam(cred({ type: "openai", keys: ["token"] }), meta, "api_key")).toBe(false);
  });
});
