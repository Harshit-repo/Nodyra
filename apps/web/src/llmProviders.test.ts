import { describe, expect, it } from "vitest";

import { getLlmVariant, visibleCredentialFields } from "./llmProviders";

describe("llmProviders", () => {
  it("OpenRouter asks for api_key + base_url default, advanced site/app", () => {
    const v = getLlmVariant("openrouter");
    expect(v.apiKey).toBe("required");
    expect(v.baseUrlDefault).toBe("https://openrouter.ai/api/v1");
    expect(v.advancedFields).toEqual(["site_url", "app_name"]);
  });

  it("Ollama hides api_key and defaults the base url", () => {
    const v = getLlmVariant("ollama");
    expect(v.apiKey).toBe("hidden");
    expect(v.baseUrlDefault).toBe("http://localhost:11434");
    expect(visibleCredentialFields("ollama", false)).not.toContain("api_key");
    expect(visibleCredentialFields("ollama", false)).toContain("base_url");
  });

  it("Azure forces its advanced fields into the visible set", () => {
    const fields = visibleCredentialFields("azure_openai", false);
    expect(fields).toContain("azure_endpoint");
    expect(fields).toContain("deployment");
  });

  it("OpenAI default view is just provider + api_key", () => {
    expect(visibleCredentialFields("openai", false)).toEqual(["api_key"]);
  });

  it("falls back to an openai-shaped variant for unknown providers", () => {
    expect(getLlmVariant("totally-unknown").apiKey).toBe("required");
  });
});
