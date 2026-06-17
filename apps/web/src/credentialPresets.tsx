// Shared credential-type presets and form helpers.
//
// These were originally local to `CredentialsPage.tsx`. They are the single
// source of truth for which fields, placeholders, help text, and OAuth
// behaviour each credential type uses, and are consumed both by the full
// credentials page modal and the locked-type "New credential" modal inside the
// node detail view (NDV). Keep additions here so both stay in sync.
import { LLM_PROVIDER_VARIANTS } from "./llmProviders";
import type { CredentialTypeInfo } from "./types";

export type CredentialScope =
  | "global"
  | "environment"
  | "workflow"
  | "runner_pool";

export interface CredentialFormField {
  key: string;
  label: string;
  placeholder?: string;
  kind?: "text" | "password" | "number" | "textarea" | "select";
  required?: boolean;
  help?: string;
  options?: { label: string; value: string }[];
  defaultValue?: string;
}

export interface CredentialPreset {
  id: string;
  type: string;
  label: string;
  group: string;
  summary: string;
  fields: CredentialFormField[];
  description: string;
  authMethod?: string;
  defaultScopes?: string[];
  documentationUrl?: string;
}

export const CREDENTIAL_PRESETS: CredentialPreset[] = [
  {
    id: "openai",
    type: "openai",
    label: "OpenAI API key",
    group: "AI",
    summary: "Use OpenAI model and embeddings nodes.",
    description: "Read-only test calls the models endpoint.",
    fields: [
      {
        key: "api_key",
        label: "API key",
        placeholder: "sk-...",
        kind: "password",
        required: true,
      },
    ],
  },
  {
    id: "anthropic",
    type: "anthropic",
    label: "Anthropic API key",
    group: "AI",
    summary: "Use Anthropic Claude nodes.",
    description: "Read-only test calls Anthropic's models endpoint.",
    fields: [
      {
        key: "api_key",
        label: "API key",
        placeholder: "sk-ant-...",
        kind: "password",
        required: true,
      },
    ],
  },
  {
    id: "llm_provider",
    type: "llm_provider",
    label: "LLM provider",
    group: "AI",
    summary: "Use AI Chat, agents, RAG, and structured-output nodes.",
    description:
      "Stores a provider API key plus optional OpenRouter, OpenAI-compatible, or Azure OpenAI endpoint details.",
    // Only `provider` lives in the preset; the rest of the fields are rendered
    // provider-aware at form time (see `visibleCredentialFields`). `submit`
    // also special-cases llm_provider so only the chosen provider's fields are
    // persisted.
    fields: [
      {
        key: "provider",
        label: "Provider",
        kind: "select",
        defaultValue: "openai",
        options: LLM_PROVIDER_VARIANTS.map((v) => ({ label: v.label, value: v.value })),
      },
    ],
  },
  {
    id: "cohere",
    type: "cohere",
    label: "Cohere API key",
    group: "AI",
    summary: "Use Cohere embedding nodes.",
    description: "Stores a Cohere API key for embedding workflows.",
    fields: [
      {
        key: "api_key",
        label: "API key",
        placeholder: "Paste Cohere key",
        kind: "password",
        required: true,
      },
    ],
  },
  {
    id: "deepl",
    type: "deepl",
    label: "DeepL API key",
    group: "AI",
    summary: "Translate text with DeepL.",
    description: "Free-tier keys ending in :fx use the free DeepL endpoint.",
    fields: [
      {
        key: "api_key",
        label: "API key",
        placeholder: "Paste DeepL key",
        kind: "password",
        required: true,
      },
    ],
  },
  {
    id: "pinecone",
    type: "pinecone",
    label: "Pinecone index",
    group: "AI",
    summary: "Use vector retriever and Pinecone query/upsert nodes.",
    description: "Store the API key and index host without https://.",
    fields: [
      {
        key: "api_key",
        label: "API key",
        placeholder: "Paste Pinecone key",
        kind: "password",
        required: true,
      },
      {
        key: "index_host",
        label: "Index host",
        placeholder: "my-index-xxxx.svc.region.pinecone.io",
        required: true,
      },
    ],
  },
  {
    id: "slack_bot",
    type: "slack_bot",
    label: "Slack bot token",
    group: "Messaging",
    summary: "Post messages to Slack channels.",
    description: "Use a bot token such as xoxb-... with chat permissions.",
    fields: [
      {
        key: "bot_token",
        label: "Bot token",
        placeholder: "xoxb-...",
        kind: "password",
        required: true,
      },
    ],
  },
  {
    id: "discord_webhook",
    type: "discord_webhook",
    label: "Discord webhook",
    group: "Messaging",
    summary: "Send messages through a Discord webhook.",
    description: "This is one of the few credentials that intentionally stores a URL.",
    fields: [
      {
        key: "webhook_url",
        label: "Webhook URL",
        placeholder: "https://discord.com/api/webhooks/...",
        kind: "password",
        required: true,
      },
    ],
  },
  {
    id: "smtp",
    type: "smtp",
    label: "SMTP account",
    group: "Messaging",
    summary: "Send email through Gmail, Outlook, or any SMTP server.",
    description: "Store host details with the account so the Test action can connect.",
    fields: [
      { key: "host", label: "Host", placeholder: "smtp.gmail.com", required: true },
      {
        key: "port",
        label: "Port",
        placeholder: "587",
        kind: "number",
        defaultValue: "587",
      },
      { key: "username", label: "Username", placeholder: "user@example.com" },
      {
        key: "password",
        label: "Password or app password",
        kind: "password",
        required: true,
      },
      {
        key: "use_tls",
        label: "TLS",
        kind: "select",
        defaultValue: "true",
        options: [
          { label: "Use STARTTLS", value: "true" },
          { label: "Plain connection", value: "false" },
        ],
      },
    ],
  },
  {
    id: "github",
    type: "github",
    label: "GitHub token",
    group: "Developer",
    summary: "Create issues, read repos, and call GitHub APIs.",
    description: "Use a fine-grained or classic personal access token.",
    fields: [
      { key: "token", label: "Token", placeholder: "ghp_...", kind: "password", required: true },
    ],
  },
  {
    id: "notion",
    type: "notion",
    label: "Notion integration token",
    group: "Apps",
    summary: "Read and update Notion pages/databases.",
    description: "Create an internal Notion integration and share pages with it.",
    fields: [
      {
        key: "token",
        label: "Integration token",
        placeholder: "secret_...",
        kind: "password",
        required: true,
      },
    ],
  },
  {
    id: "stripe",
    type: "stripe",
    label: "Stripe secret key",
    group: "Apps",
    summary: "Read Stripe account and customer data.",
    description: "Use a restricted key when possible.",
    fields: [
      { key: "api_key", label: "Secret key", placeholder: "sk_live_...", kind: "password", required: true },
    ],
  },
  {
    id: "airtable",
    type: "airtable",
    label: "Airtable token",
    group: "Apps",
    summary: "Read and update Airtable bases.",
    description: "Use a personal access token with the minimum scopes needed.",
    fields: [
      { key: "token", label: "Token", placeholder: "pat...", kind: "password", required: true },
    ],
  },
  {
    id: "google_sheets",
    type: "google_sheets",
    label: "Google Sheets",
    group: "Google",
    summary: "Use an API key or OAuth access token for Sheets nodes.",
    description: "Add either an API key for public/readable sheets or an access token.",
    fields: [
      { key: "api_key", label: "API key", placeholder: "AIza...", kind: "password" },
      {
        key: "access_token",
        label: "OAuth access token",
        placeholder: "ya29...",
        kind: "password",
        help: "Optional. Use this instead of API key for private Sheets access.",
      },
    ],
  },
  {
    id: "oauth2",
    type: "oauth2",
    label: "OAuth2 access token",
    group: "Generic",
    summary: "Store an already-issued OAuth token.",
    description:
      "Manual token storage for now. Provider endpoint fields can wait until browser OAuth is added.",
    fields: [
      {
        key: "access_token",
        label: "Access token",
        placeholder: "Paste access token",
        kind: "password",
        required: true,
      },
      {
        key: "refresh_token",
        label: "Refresh token",
        placeholder: "Optional refresh token",
        kind: "password",
      },
      {
        key: "expires_at",
        label: "Expires at",
        placeholder: "2026-06-01T12:00:00Z",
      },
      { key: "scope", label: "Scopes", placeholder: "read write" },
    ],
  },
  {
    id: "apiKey",
    type: "apiKey",
    label: "Generic API key",
    group: "Generic",
    summary: "Store one reusable API key.",
    description: "Use for custom HTTP/API workflows that only need a single secret.",
    fields: [
      { key: "api_key", label: "API key", placeholder: "Paste key", kind: "password", required: true },
    ],
  },
  {
    id: "httpAuth",
    type: "httpAuth",
    label: "HTTP basic auth",
    group: "Generic",
    summary: "Store a username and password pair.",
    description: "Use for APIs that rely on basic authentication.",
    fields: [
      { key: "username", label: "Username", required: true },
      { key: "password", label: "Password", kind: "password", required: true },
    ],
  },
  {
    id: "http_basic",
    type: "http_basic",
    label: "Webhook · Basic Auth",
    group: "Webhook auth",
    summary: "Username + password for inbound webhooks using HTTP Basic.",
    description: "Used by the Webhook trigger node when Authentication = Basic Auth.",
    fields: [
      { key: "username", label: "Username", required: true },
      { key: "password", label: "Password", kind: "password", required: true },
    ],
  },
  {
    id: "http_header",
    type: "http_header",
    label: "Webhook · Header Auth",
    group: "Webhook auth",
    summary: "Header name + expected value for inbound webhooks.",
    description: "Used by the Webhook trigger node when Authentication = Header Auth.",
    fields: [
      { key: "name", label: "Header name", placeholder: "X-API-Key", required: true },
      { key: "value", label: "Expected value", kind: "password", required: true },
    ],
  },
  {
    id: "http_query",
    type: "http_query",
    label: "Webhook · Query Auth",
    group: "Webhook auth",
    summary: "Query parameter name + expected value for inbound webhooks.",
    description: "Used by the Webhook trigger node when Authentication = Query Auth.",
    fields: [
      { key: "name", label: "Query parameter name", placeholder: "token", required: true },
      { key: "value", label: "Expected value", kind: "password", required: true },
    ],
  },
  {
    id: "postgres",
    type: "postgres",
    label: "Postgres connection",
    group: "Database",
    summary: "Run Postgres query nodes.",
    description: "The connection string is stored encrypted and never shown again.",
    fields: [
      {
        key: "connection_url",
        label: "Connection URL",
        placeholder: "postgresql://user:password@host:5432/db",
        kind: "password",
        required: true,
      },
    ],
  },
  {
    id: "mysql",
    type: "mysql",
    label: "MySQL account",
    group: "Database",
    summary: "Run MySQL query nodes.",
    description: "Store host, database, and login fields for MySQL nodes.",
    fields: [
      { key: "host", label: "Host", required: true },
      { key: "port", label: "Port", placeholder: "3306", kind: "number", defaultValue: "3306" },
      { key: "database", label: "Database", required: true },
      { key: "username", label: "Username", required: true },
      { key: "password", label: "Password", kind: "password", required: true },
    ],
  },
  {
    id: "aws",
    type: "aws",
    label: "AWS / S3 keys",
    group: "Cloud",
    summary: "Read and write S3 objects.",
    description: "Leave keys blank later if a runner uses instance profile auth.",
    fields: [
      { key: "aws_access_key_id", label: "Access key ID", kind: "password" },
      { key: "aws_secret_access_key", label: "Secret access key", kind: "password" },
      { key: "region_name", label: "Region", placeholder: "us-east-1" },
      { key: "endpoint_url", label: "Custom endpoint", placeholder: "Optional S3-compatible endpoint" },
    ],
  },
  {
    id: "generic",
    type: "generic",
    label: "Custom fields",
    group: "Generic",
    summary: "Store arbitrary key/value secrets.",
    description: "Use this only when no official credential preset fits.",
    fields: [],
  },
];

export const PRESET_BY_TYPE = new Map(
  CREDENTIAL_PRESETS.map((preset) => [preset.type, preset]),
);

// Field definitions for the provider-aware llm_provider form. Which of these
// are shown is decided per provider by `visibleCredentialFields`.
export const LLM_FIELD_DEFS: Record<string, CredentialFormField> = {
  api_key: { key: "api_key", label: "API key", kind: "password", placeholder: "Paste provider key" },
  base_url: { key: "base_url", label: "Base URL", placeholder: "https://…" },
  organization: { key: "organization", label: "Organization", placeholder: "Optional OpenAI org" },
  site_url: { key: "site_url", label: "Site URL", placeholder: "Optional OpenRouter HTTP-Referer" },
  app_name: { key: "app_name", label: "App name", placeholder: "Optional OpenRouter X-Title" },
  azure_endpoint: { key: "azure_endpoint", label: "Azure endpoint", placeholder: "https://resource.openai.azure.com" },
  azure_api_version: { key: "azure_api_version", label: "Azure API version", placeholder: "2024-02-15-preview" },
  deployment: { key: "deployment", label: "Deployment", placeholder: "Azure deployment name" },
};

export function authMethodLabel(method: string): string {
  if (method === "api_key") return "API key";
  if (method === "oauth2") return "OAuth2";
  if (method === "service_account") return "Service account";
  if (method === "basic") return "Basic auth";
  if (method === "connection_string") return "Connection string";
  return method;
}

export function credentialTypePreset(spec: CredentialTypeInfo): CredentialPreset {
  const oauthScopes = spec.default_scopes.length
    ? ` Default scopes: ${spec.default_scopes.join(" ")}`
    : "";
  const fields: CredentialFormField[] = spec.fields.map((field) => ({
    key: field.key,
    label: field.label,
    placeholder: field.placeholder,
    kind: field.key.includes("json") ? "textarea" : field.secret ? "password" : "text",
    required: field.required,
    help: field.help,
  }));
  const manualOAuthFields: CredentialFormField[] =
    spec.auth_method === "oauth2" && fields.length === 0
      ? [
          {
            key: "access_token",
            label: "Access token",
            placeholder: "Paste access token for manual setup",
            kind: "password",
            required: true,
          },
          {
            key: "refresh_token",
            label: "Refresh token",
            placeholder: "Optional refresh token",
            kind: "password",
          },
          {
            key: "expires_at",
            label: "Expires at",
            placeholder: "2026-06-01T12:00:00Z",
          },
          {
            key: "scope",
            label: "Scopes",
            placeholder: spec.default_scopes.join(" "),
            defaultValue: spec.default_scopes.join(" "),
          },
        ]
      : [];
  return {
    id: spec.id,
    type: spec.id,
    label: spec.name,
    group: spec.provider,
    summary: `${authMethodLabel(spec.auth_method)} credential for ${spec.provider}.`,
    description:
      spec.auth_method === "oauth2"
        ? `Backend OAuth type.${oauthScopes}`
        : spec.documentation_url
          ? `Backend credential type. Docs: ${spec.documentation_url}`
          : "Backend credential type.",
    fields: fields.length > 0 ? fields : manualOAuthFields,
    authMethod: spec.auth_method,
    defaultScopes: spec.default_scopes,
    documentationUrl: spec.documentation_url,
  };
}

export function mergedCredentialPresets(
  types: CredentialTypeInfo[] | null,
): CredentialPreset[] {
  if (!types) return CREDENTIAL_PRESETS;
  const byType = new Map(CREDENTIAL_PRESETS.map((preset) => [preset.type, preset]));
  for (const spec of types) {
    byType.set(spec.id, credentialTypePreset(spec));
  }
  return Array.from(byType.values()).sort((a, b) => {
    const group = a.group.localeCompare(b.group);
    return group || a.label.localeCompare(b.label);
  });
}

export function presetInitialValues(
  preset: CredentialPreset,
): Record<string, string> {
  return Object.fromEntries(
    preset.fields.map((field) => [field.key, field.defaultValue ?? ""]),
  );
}

export function fieldInputType(field: CredentialFormField): string {
  if (field.kind === "password") return "password";
  if (field.kind === "number") return "number";
  return "text";
}

/** Render a single credential form field (text/password/number/select/textarea)
 *  with its label, required marker, and optional help text. Shared by the
 *  credentials page modal and the NDV "New credential" modal. */
export function CredentialFieldInput({
  field,
  value,
  onChange,
}: {
  field: CredentialFormField;
  value: string;
  onChange: (value: string) => void;
}) {
  const labelText = (
    <span>
      {field.label}
      {field.required ? " *" : ""}
    </span>
  );
  if (field.kind === "select") {
    return (
      <label className="credential-form-field" key={field.key}>
        {labelText}
        <select
          className="field-input"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        >
          {(field.options ?? []).map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        {field.help && <small>{field.help}</small>}
      </label>
    );
  }
  if (field.kind === "textarea") {
    return (
      <label className="credential-form-field" key={field.key}>
        {labelText}
        <textarea
          className="field-input"
          placeholder={field.placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        {field.help && <small>{field.help}</small>}
      </label>
    );
  }
  return (
    <label className="credential-form-field" key={field.key}>
      {labelText}
      <input
        className="field-input"
        type={fieldInputType(field)}
        placeholder={field.placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      {field.help && <small>{field.help}</small>}
    </label>
  );
}
