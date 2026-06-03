export type ApiKeyMode = "required" | "optional" | "hidden";

export interface LlmProviderVariant {
  value: string;
  label: string;
  apiKey: ApiKeyMode;
  baseUrlDefault?: string;
  /** Fields revealed under the "Advanced" disclosure for this provider. */
  advancedFields: string[];
  /** Whether this provider needs a base_url field shown at all. */
  showBaseUrl: boolean;
  /** Per-provider curated model fallback shown before/without a fetch. */
  curatedModels?: string[];
  docsUrl?: string;
}

export const LLM_PROVIDER_VARIANTS: LlmProviderVariant[] = [
  {
    value: "openai",
    label: "OpenAI",
    apiKey: "required",
    advancedFields: ["organization"],
    showBaseUrl: false,
    curatedModels: ["gpt-4.1-mini", "gpt-4.1", "gpt-4o", "gpt-4o-mini"],
    docsUrl: "https://platform.openai.com/api-keys",
  },
  {
    value: "anthropic",
    label: "Anthropic",
    apiKey: "required",
    advancedFields: [],
    showBaseUrl: false,
    curatedModels: ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"],
    docsUrl: "https://console.anthropic.com/settings/keys",
  },
  {
    value: "openrouter",
    label: "OpenRouter",
    apiKey: "required",
    baseUrlDefault: "https://openrouter.ai/api/v1",
    advancedFields: ["site_url", "app_name"],
    showBaseUrl: true,
    curatedModels: ["openai/gpt-4.1-mini", "anthropic/claude-3.7-sonnet"],
    docsUrl: "https://openrouter.ai/settings/keys",
  },
  {
    value: "openai_compatible",
    label: "OpenAI-compatible",
    apiKey: "optional",
    advancedFields: ["organization"],
    showBaseUrl: true,
    curatedModels: ["gpt-4.1-mini"],
  },
  {
    value: "ollama",
    label: "Ollama",
    apiKey: "hidden",
    baseUrlDefault: "http://localhost:11434",
    advancedFields: [],
    showBaseUrl: true,
    curatedModels: ["llama3.1", "qwen2.5", "gemma2"],
  },
  {
    value: "azure_openai",
    label: "Azure OpenAI",
    apiKey: "required",
    advancedFields: ["azure_endpoint", "azure_api_version", "deployment"],
    showBaseUrl: false,
    curatedModels: ["gpt-4o-mini", "gpt-4o"],
  },
];

const BY_VALUE = new Map(LLM_PROVIDER_VARIANTS.map((v) => [v.value, v]));

export function getLlmVariant(provider: string): LlmProviderVariant {
  return BY_VALUE.get(provider) ?? LLM_PROVIDER_VARIANTS[0];
}

/** Ordered list of credential field keys to show for a provider.
 *  When `showAdvanced` is false, advanced fields are omitted UNLESS the variant
 *  requires them (Azure), in which case they are always part of the core set. */
export function visibleCredentialFields(
  provider: string,
  showAdvanced: boolean,
): string[] {
  const v = getLlmVariant(provider);
  const fields: string[] = [];
  if (v.apiKey !== "hidden") fields.push("api_key");
  if (v.showBaseUrl) fields.push("base_url");
  const advancedRequired = v.value === "azure_openai";
  if (showAdvanced || advancedRequired) fields.push(...v.advancedFields);
  return fields;
}
