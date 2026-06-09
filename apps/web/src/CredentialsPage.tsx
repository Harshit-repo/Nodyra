import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "./api";
import { HomeHeader } from "./HomeHeader";
import {
  LLM_PROVIDER_VARIANTS,
  getLlmVariant,
  visibleCredentialFields,
} from "./llmProviders";
import { useToast } from "./ToastProvider";
import { useModalA11y } from "./useModalA11y";
import type { Credential, CredentialTestResponse, CredentialTypeInfo } from "./types";

interface Field {
  key: string;
  value: string;
}

type CredentialScope = "global" | "environment" | "workflow" | "runner_pool";

interface CredentialFormField {
  key: string;
  label: string;
  placeholder?: string;
  kind?: "text" | "password" | "number" | "textarea" | "select";
  required?: boolean;
  help?: string;
  options?: { label: string; value: string }[];
  defaultValue?: string;
}

interface CredentialPreset {
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

const CREDENTIAL_PRESETS: CredentialPreset[] = [
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

const PRESET_BY_TYPE = new Map(CREDENTIAL_PRESETS.map((preset) => [preset.type, preset]));

// Field definitions for the provider-aware llm_provider form. Which of these
// are shown is decided per provider by `visibleCredentialFields`.
const LLM_FIELD_DEFS: Record<string, CredentialFormField> = {
  api_key: { key: "api_key", label: "API key", kind: "password", placeholder: "Paste provider key" },
  base_url: { key: "base_url", label: "Base URL", placeholder: "https://…" },
  organization: { key: "organization", label: "Organization", placeholder: "Optional OpenAI org" },
  site_url: { key: "site_url", label: "Site URL", placeholder: "Optional OpenRouter HTTP-Referer" },
  app_name: { key: "app_name", label: "App name", placeholder: "Optional OpenRouter X-Title" },
  azure_endpoint: { key: "azure_endpoint", label: "Azure endpoint", placeholder: "https://resource.openai.azure.com" },
  azure_api_version: { key: "azure_api_version", label: "Azure API version", placeholder: "2024-02-15-preview" },
  deployment: { key: "deployment", label: "Deployment", placeholder: "Azure deployment name" },
};

function authMethodLabel(method: string): string {
  if (method === "api_key") return "API key";
  if (method === "oauth2") return "OAuth2";
  if (method === "service_account") return "Service account";
  if (method === "basic") return "Basic auth";
  if (method === "connection_string") return "Connection string";
  return method;
}

function credentialTypePreset(spec: CredentialTypeInfo): CredentialPreset {
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

function mergedCredentialPresets(types: CredentialTypeInfo[] | null): CredentialPreset[] {
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

function presetInitialValues(preset: CredentialPreset): Record<string, string> {
  return Object.fromEntries(
    preset.fields.map((field) => [field.key, field.defaultValue ?? ""]),
  );
}

function fieldInputType(field: CredentialFormField): string {
  if (field.kind === "password") return "password";
  if (field.kind === "number") return "number";
  return "text";
}

function credentialTypeLabel(
  type: string,
  presetsByType: Map<string, CredentialPreset> = PRESET_BY_TYPE,
): string {
  return presetsByType.get(type)?.label ?? type;
}

function credentialScopeLabel(cred: Credential): string {
  if (cred.scope === "workflow" && cred.workflow_id) {
    return `Workflow ${cred.workflow_id.slice(0, 8)}`;
  }
  if (cred.scope === "environment" && cred.environment_id) {
    return `Environment ${cred.environment_id.slice(0, 8)}`;
  }
  if (cred.scope === "runner_pool" && cred.runner_pool_id) {
    return `Runner pool ${cred.runner_pool_id}`;
  }
  return "Global";
}

function CreateCredentialModal({
  presets,
  onClose,
  onCreated,
}: {
  presets: CredentialPreset[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const { notify } = useToast();
  const firstPreset = presets[0] ?? CREDENTIAL_PRESETS[0];
  const [presetId, setPresetId] = useState(firstPreset.id);
  const [name, setName] = useState(firstPreset.label);
  const [scope, setScope] = useState<CredentialScope>("global");
  const [workflowId, setWorkflowId] = useState("");
  const [environmentId, setEnvironmentId] = useState("");
  const [runnerPoolId, setRunnerPoolId] = useState("");
  const [description, setDescription] = useState("");
  const [values, setValues] = useState<Record<string, string>>(
    presetInitialValues(firstPreset),
  );
  const [customFields, setCustomFields] = useState<Field[]>([{ key: "", value: "" }]);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [oauthStarted, setOauthStarted] = useState("");
  const oauthPopupRef = useRef<Window | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onClose);

  // Listen for popup postMessage and call onCreated on success
  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin) return;
      if (!event.data || typeof event.data !== "object") return;
      const { type, message: msg } = event.data as {
        type?: string;
        message?: string;
      };
      if (type === "noodle_oauth_success") {
        setOauthStarted("");
        notify(`Connected${msg ? ` — ${msg.replace(/^Connected — /, "")}` : ""}`, "success");
        oauthPopupRef.current = null;
        onCreated();
      } else if (type === "noodle_oauth_error") {
        setOauthStarted("");
        setError(msg ?? "OAuth failed.");
        notify(msg ?? "OAuth failed.", "error");
        oauthPopupRef.current = null;
      }
    }
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [notify, onCreated]);

  const presetsById = useMemo(
    () => new Map(presets.map((preset) => [preset.id, preset])),
    [presets],
  );
  const preset = presetsById.get(presetId) ?? firstPreset;
  const isOAuthPreset = preset.authMethod === "oauth2";

  useEffect(() => {
    if (presetsById.has(presetId)) return;
    setPresetId(firstPreset.id);
    setName(firstPreset.label);
    setValues(presetInitialValues(firstPreset));
    setDescription(firstPreset.description);
  }, [firstPreset, presetId, presetsById]);

  const groups = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const visible = presets.filter((item) => {
      if (!needle) return true;
      return `${item.label} ${item.type} ${item.group} ${item.summary}`
        .toLowerCase()
        .includes(needle);
    });
    return visible.reduce<Record<string, CredentialPreset[]>>((acc, item) => {
      acc[item.group] = [...(acc[item.group] ?? []), item];
      return acc;
    }, {});
  }, [presets, search]);

  function selectPreset(nextId: string) {
    const next = presetsById.get(nextId) ?? firstPreset;
    const previous = presetsById.get(presetId);
    setPresetId(next.id);
    setValues(presetInitialValues(next));
    setCustomFields([{ key: "", value: "" }]);
    setError("");
    setOauthStarted("");
    if (!name.trim() || name === previous?.label) {
      setName(next.label);
    }
    if (!description.trim() || description === previous?.description) {
      setDescription(next.description);
    }
  }

  function updateCustom(index: number, patch: Partial<Field>) {
    setCustomFields((current) =>
      current.map((field, i) => (i === index ? { ...field, ...patch } : field)),
    );
  }

  function validateScopeInputs(): boolean {
    if (scope === "workflow" && !workflowId.trim()) {
      setError("Enter Workflow ID.");
      return false;
    }
    if (scope === "environment" && !environmentId.trim()) {
      setError("Enter Environment ID.");
      return false;
    }
    if (scope === "runner_pool" && !runnerPoolId.trim()) {
      setError("Enter Runner pool ID.");
      return false;
    }
    return true;
  }

  async function startOAuth() {
    if (!name.trim() || busy || !isOAuthPreset) return;
    setBusy(true);
    setError("");
    setOauthStarted("");
    try {
      if (!validateScopeInputs()) {
        setBusy(false);
        return;
      }
      const started = await api.startCredentialOAuth({
        credential_type: preset.type,
        name: name.trim(),
        scope,
        workflow_id: scope === "workflow" ? workflowId.trim() : null,
        environment_id: scope === "environment" ? environmentId.trim() : null,
        runner_pool_id: scope === "runner_pool" ? runnerPoolId.trim() : null,
        description: description.trim(),
        scopes: preset.defaultScopes ?? [],
      });
      const popup = window.open(
        started.authorization_url,
        "noodle_oauth",
        "width=600,height=720,resizable=yes,scrollbars=yes",
      );
      if (!popup) {
        window.location.assign(started.authorization_url);
        return;
      }
      oauthPopupRef.current = popup;
      setOauthStarted(
        "Complete authorization in the popup window. This page will update automatically.",
      );
      notify("OAuth authorization opened.", "success");
    } catch (err) {
      setError(String(err));
      notify("Could not start OAuth authorization.", "error");
    } finally {
      setBusy(false);
    }
  }

  function renderField(field: CredentialFormField) {
    const value = values[field.key] ?? "";
    if (field.kind === "select") {
      return (
        <label className="credential-form-field" key={field.key}>
          <span>
            {field.label}
            {field.required ? " *" : ""}
          </span>
          <select
            className="field-input"
            value={value}
            onChange={(e) => setValues({ ...values, [field.key]: e.target.value })}
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
          <span>
            {field.label}
            {field.required ? " *" : ""}
          </span>
          <textarea
            className="field-input"
            placeholder={field.placeholder}
            value={value}
            onChange={(e) => setValues({ ...values, [field.key]: e.target.value })}
          />
          {field.help && <small>{field.help}</small>}
        </label>
      );
    }
    return (
      <label className="credential-form-field" key={field.key}>
        <span>
          {field.label}
          {field.required ? " *" : ""}
        </span>
        <input
          className="field-input"
          type={fieldInputType(field)}
          placeholder={field.placeholder}
          value={value}
          onChange={(e) => setValues({ ...values, [field.key]: e.target.value })}
        />
        {field.help && <small>{field.help}</small>}
      </label>
    );
  }

  const isLlm = preset.type === "llm_provider";
  const llmVariant = isLlm ? getLlmVariant(values.provider) : null;
  const llmFields = isLlm
    ? visibleCredentialFields(values.provider, showAdvanced)
    : [];

  function onProviderChange(next: string) {
    const v = getLlmVariant(next);
    setValues((cur) => ({
      ...cur,
      provider: next,
      base_url: cur.base_url?.trim() ? cur.base_url : (v.baseUrlDefault ?? ""),
    }));
    setShowAdvanced(false);
  }

  /** Build the credential data dict for submit/test. Returns null and sets an
   *  error message when a required field is missing. */
  function collectData(): Record<string, string> | null {
    const data: Record<string, string> = {};
    if (isLlm) {
      data.provider = values.provider || "openai";
      if (llmVariant?.apiKey === "required" && !values.api_key?.trim()) {
        setError("Enter API key.");
        return null;
      }
      // Persist only the chosen provider's fields (advanced included if filled).
      for (const key of visibleCredentialFields(values.provider, true)) {
        if (values[key]?.trim()) data[key] = values[key].trim();
      }
      return data;
    }
    if (preset.fields.length) {
      for (const field of preset.fields) {
        const value = values[field.key] ?? "";
        if (field.required && !value.trim()) {
          setError(`Enter ${field.label}.`);
          return null;
        }
        if (value.trim() || field.defaultValue !== undefined) {
          data[field.key] = value.trim();
        }
      }
      if (
        preset.type === "google_sheets" &&
        !data.api_key &&
        !data.access_token
      ) {
        setError("Enter either API key or OAuth access token.");
        return null;
      }
      return data;
    }
    for (const field of customFields) {
      if (field.key.trim()) data[field.key.trim()] = field.value;
    }
    if (Object.keys(data).length === 0) {
      setError("Add at least one custom field.");
      return null;
    }
    return data;
  }

  async function handleTest() {
    if (testing) return;
    setTesting(true);
    setError("");
    const data = collectData();
    if (data === null) {
      setTesting(false);
      return;
    }
    try {
      const res = await api.testCredentialDraft({
        type: preset.type,
        data,
        context: {},
      });
      notify(
        res.ok ? "Credential connected." : res.message,
        res.ok ? "success" : "error",
      );
      if (!res.ok) setError(res.message);
    } catch (err) {
      notify(String(err), "error");
    } finally {
      setTesting(false);
    }
  }

  async function submit() {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError("");
    const data = collectData();
    if (data === null) {
      setBusy(false);
      return;
    }
    try {
      if (!validateScopeInputs()) {
        setBusy(false);
        return;
      }
      await api.createCredential({
        name: name.trim(),
        type: preset.type,
        scope,
        workflow_id: scope === "workflow" ? workflowId.trim() : null,
        environment_id: scope === "environment" ? environmentId.trim() : null,
        runner_pool_id: scope === "runner_pool" ? runnerPoolId.trim() : null,
        description: description.trim(),
        data,
      });
      onCreated();
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal credential-modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-credential-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="credential-modal-head">
          <div>
            <h2 id="new-credential-title">New credential</h2>
            <p className="muted">
              Choose the service first. Noodle only asks for fields this
              credential type uses.
            </p>
          </div>
          <span className="cred-type">{preset.type}</span>
        </div>

        <div className="credential-builder">
          <aside className="credential-type-picker">
            <input
              className="field-input"
              autoFocus
              placeholder="Search credential type"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <div className="credential-type-list">
              {Object.entries(groups).map(([group, items]) => (
                <div className="credential-type-group" key={group}>
                  <h3>{group}</h3>
                  {items.map((item) => (
                    <button
                      type="button"
                      className={`credential-type-option ${
                        item.id === preset.id ? "is-selected" : ""
                      }`}
                      key={item.id}
                      onClick={() => selectPreset(item.id)}
                    >
                      <strong>{item.label}</strong>
                      <span>{item.summary}</span>
                    </button>
                  ))}
                </div>
              ))}
            </div>
          </aside>

          <section className="credential-form">
            <div className="credential-form-grid">
              <label className="credential-form-field">
                <span>Name *</span>
                <input
                  className="field-input"
                  placeholder="Credential name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              <label className="credential-form-field">
                <span>Scope</span>
                <select
                  className="field-input"
                  value={scope}
                  onChange={(e) => setScope(e.target.value as CredentialScope)}
                >
                  <option value="global">Global</option>
                  <option value="environment">Environment</option>
                  <option value="workflow">Workflow</option>
                  <option value="runner_pool">Runner pool</option>
                </select>
              </label>
            </div>

            {scope === "workflow" && (
              <label className="credential-form-field">
                <span>Workflow ID *</span>
                <input
                  className="field-input"
                  placeholder="Workflow ID"
                  value={workflowId}
                  onChange={(e) => setWorkflowId(e.target.value)}
                />
              </label>
            )}
            {scope === "environment" && (
              <label className="credential-form-field">
                <span>Environment ID *</span>
                <input
                  className="field-input"
                  placeholder="Environment ID"
                  value={environmentId}
                  onChange={(e) => setEnvironmentId(e.target.value)}
                />
              </label>
            )}
            {scope === "runner_pool" && (
              <label className="credential-form-field">
                <span>Runner pool ID *</span>
                <input
                  className="field-input"
                  placeholder="Runner pool ID"
                  value={runnerPoolId}
                  onChange={(e) => setRunnerPoolId(e.target.value)}
                />
              </label>
            )}

            <label className="credential-form-field">
              <span>Description</span>
              <input
                className="field-input"
                placeholder="Optional note"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
              <small>{preset.description}</small>
            </label>

            <div className="credential-section-title">
              <h3>{preset.label}</h3>
              <span>{preset.group}</span>
            </div>

            {isOAuthPreset && (
              <div className="credential-oauth-panel">
                <div>
                  <h3>Provider authorization</h3>
                  <p>
                    {preset.defaultScopes?.length
                      ? preset.defaultScopes.join(" ")
                      : "OAuth scopes configured by the provider."}
                  </p>
                </div>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => void startOAuth()}
                  disabled={busy}
                >
                  {busy ? "Opening..." : "Connect OAuth"}
                </button>
                {oauthStarted && <small>{oauthStarted}</small>}
              </div>
            )}

            {isLlm ? (
              <div className="credential-form-grid">
                <label className="credential-form-field">
                  <span>Provider</span>
                  <select
                    className="field-input"
                    value={values.provider || "openai"}
                    onChange={(e) => onProviderChange(e.target.value)}
                  >
                    {LLM_PROVIDER_VARIANTS.map((v) => (
                      <option key={v.value} value={v.value}>
                        {v.label}
                      </option>
                    ))}
                  </select>
                </label>
                {llmFields.map((key) => {
                  const def = LLM_FIELD_DEFS[key];
                  if (!def) return null;
                  const required =
                    key === "api_key" && llmVariant?.apiKey === "required";
                  return renderField({ ...def, required });
                })}
                {llmVariant &&
                  llmVariant.advancedFields.length > 0 &&
                  llmVariant.value !== "azure_openai" && (
                    <button
                      type="button"
                      className="btn btn-sm btn-ghost"
                      onClick={() => setShowAdvanced((v) => !v)}
                    >
                      {showAdvanced ? "Hide advanced" : "Advanced options"}
                    </button>
                  )}
              </div>
            ) : preset.fields.length > 0 ? (
              <div className="credential-form-grid">
                {preset.fields.map((field) => renderField(field))}
              </div>
            ) : (
              <div className="cred-fields">
                {customFields.map((field, i) => (
                  <div className="cred-field-row" key={i}>
                    <input
                      className="field-input"
                      placeholder="key"
                      value={field.key}
                      onChange={(e) => updateCustom(i, { key: e.target.value })}
                    />
                    <input
                      className="field-input"
                      placeholder="value"
                      type="password"
                      value={field.value}
                      onChange={(e) => updateCustom(i, { value: e.target.value })}
                    />
                  </div>
                ))}
                <button
                  className="btn btn-sm btn-ghost"
                  onClick={() =>
                    setCustomFields([...customFields, { key: "", value: "" }])
                  }
                >
                  + Add field
                </button>
              </div>
            )}

            <div className="credential-security-note">
              Secret values are encrypted at rest and are not returned by the
              API after creation.
            </div>
          </section>
        </div>

        {error && <p className="error-text">{error}</p>}
        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          {!isOAuthPreset && (
            <button
              className="btn btn-ghost"
              onClick={() => void handleTest()}
              disabled={testing}
            >
              {testing ? "Testing..." : "Test connection"}
            </button>
          )}
          <button
            className="btn btn-primary"
            onClick={() => void submit()}
            disabled={busy}
          >
            {busy ? "Saving..." : isOAuthPreset ? "Save manual" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function CredentialsPage() {
  const [credentials, setCredentials] = useState<Credential[] | null>(null);
  const [credentialTypes, setCredentialTypes] = useState<CredentialTypeInfo[] | null>(null);
  const [error, setError] = useState("");
  const [modal, setModal] = useState(false);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [scopeFilter, setScopeFilter] = useState("all");
  const [testing, setTesting] = useState<Record<string, boolean>>({});
  const [refreshing, setRefreshing] = useState<Record<string, boolean>>({});
  const [testResults, setTestResults] = useState<
    Record<string, CredentialTestResponse>
  >({});
  const { notify } = useToast();
  const credentialPresets = useMemo(
    () => mergedCredentialPresets(credentialTypes),
    [credentialTypes],
  );
  const presetsByType = useMemo(
    () => new Map(credentialPresets.map((preset) => [preset.type, preset])),
    [credentialPresets],
  );

  const filteredCredentials = useMemo(() => {
    if (!credentials) return [];
    const needle = query.trim().toLowerCase();
    return credentials.filter((cred) => {
      const preset = presetsByType.get(cred.type);
      const text = `${cred.name} ${cred.type} ${preset?.label ?? ""} ${
        cred.description ?? ""
      } ${cred.keys.join(" ")}`.toLowerCase();
      if (needle && !text.includes(needle)) return false;
      if (typeFilter !== "all" && cred.type !== typeFilter) return false;
      if (scopeFilter !== "all" && cred.scope !== scopeFilter) return false;
      return true;
    });
  }, [credentials, presetsByType, query, scopeFilter, typeFilter]);

  const availableTypes = useMemo(() => {
    const types = new Set(credentials?.map((cred) => cred.type) ?? []);
    return Array.from(types).sort((a, b) =>
      credentialTypeLabel(a, presetsByType).localeCompare(
        credentialTypeLabel(b, presetsByType),
      ),
    );
  }, [credentials, presetsByType]);

  function load() {
    Promise.allSettled([api.listCredentials(), api.listCredentialTypes()])
      .then(([credentialsResult, typesResult]) => {
        if (credentialsResult.status === "fulfilled") {
          setCredentials(credentialsResult.value);
        } else {
          setError(String(credentialsResult.reason));
        }
        if (typesResult.status === "fulfilled") {
          setCredentialTypes(typesResult.value);
        } else {
          setCredentialTypes(null);
        }
      })
      .catch((err) => setError(String(err)));
  }

  useEffect(load, []);

  async function remove(id: string, name: string) {
    if (!window.confirm(`Delete credential “${name}”?`)) return;
    try {
      await api.deleteCredential(id);
      notify("Credential deleted.", "success");
      load();
    } catch (err) {
      setError(String(err));
      notify("Could not delete credential.", "error");
    }
  }

  async function testCredential(cred: Credential): Promise<void> {
    setTesting((current) => ({ ...current, [cred.id]: true }));
    try {
      const result = await api.testCredential(cred.id, {
        workflow_id: cred.workflow_id,
        environment_id: cred.environment_id,
        runner_pool_id: cred.runner_pool_id,
        context: {},
      });
      setTestResults((current) => ({ ...current, [cred.id]: result }));
      notify(
        result.ok ? "Credential connected." : result.message,
        result.ok ? "success" : "error",
      );
    } catch (err) {
      notify(String(err), "error");
    } finally {
      setTesting((current) => ({ ...current, [cred.id]: false }));
    }
  }

  async function refreshCredential(cred: Credential): Promise<void> {
    setRefreshing((current) => ({ ...current, [cred.id]: true }));
    try {
      await api.refreshCredential(cred.id);
      notify("Credential refreshed.", "success");
      load();
    } catch (err) {
      notify(String(err), "error");
    } finally {
      setRefreshing((current) => ({ ...current, [cred.id]: false }));
    }
  }

  return (
    <div className="home">
      <HomeHeader />
      <main className="home-main">
        <div className="home-bar">
          <h1>
            Credentials
            {credentials && (
              <span className="home-count">{credentials.length}</span>
            )}
          </h1>
          <button className="btn btn-primary" onClick={() => setModal(true)}>
            New credential
          </button>
        </div>

        {error && <p className="error-text">{error}</p>}
        {!credentials && !error && <p className="muted">Loading…</p>}

        {credentials && credentials.length === 0 && (
          <div className="empty-state">
            <h2>No credentials yet</h2>
            <p className="muted">
              Store API keys and secrets here — they are encrypted at rest and
              never shown again.
            </p>
            <button className="btn btn-primary" onClick={() => setModal(true)}>
              New credential
            </button>
          </div>
        )}

        {credentials && credentials.length > 0 && (
          <>
            <div className="home-filters credential-filters">
              <input
                className="field-input"
                placeholder="Search credentials"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <select
                className="field-input"
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value)}
              >
                <option value="all">All types</option>
                {availableTypes.map((type) => (
                  <option key={type} value={type}>
                    {credentialTypeLabel(type, presetsByType)}
                  </option>
                ))}
              </select>
              <select
                className="field-input"
                value={scopeFilter}
                onChange={(e) => setScopeFilter(e.target.value)}
              >
                <option value="all">All scopes</option>
                <option value="global">Global</option>
                <option value="environment">Environment</option>
                <option value="workflow">Workflow</option>
                <option value="runner_pool">Runner pool</option>
              </select>
            </div>
            {filteredCredentials.length === 0 && (
              <p className="muted">No credentials match the current filters.</p>
            )}
            <div className="env-grid">
              {filteredCredentials.map((cred) => (
              <article className="env-card" key={cred.id}>
                {(() => {
                  const preset = presetsByType.get(cred.type);
                  const isOAuthCredential = preset?.authMethod === "oauth2";
                  return (
                <>
                <div className="env-card-head">
                  <div className="env-title">
                    <h3>{cred.name}</h3>
                  </div>
                  <span className="cred-type">
                    {credentialTypeLabel(cred.type, presetsByType)}
                  </span>
                </div>
                <div className="env-meta">
                  {credentialScopeLabel(cred)}
                  {cred.last_used_at ? ` · used ${new Date(cred.last_used_at).toLocaleString()}` : ""}
                </div>
                {cred.description && (
                  <p className="field-desc">{cred.description}</p>
                )}
                <div className="env-packages">
                  {cred.keys.length === 0 && (
                    <span className="muted">No fields</span>
                  )}
                  {cred.keys.map((key) => (
                    <span className="pkg-chip" key={key}>
                      {key}
                      <span className="cred-dots">••••</span>
                    </span>
                  ))}
                </div>
                <div className="env-actions">
                  <button
                    className="btn btn-sm"
                    onClick={() => void testCredential(cred)}
                    disabled={Boolean(testing[cred.id])}
                  >
                    {testing[cred.id] ? "Testing..." : "Test"}
                  </button>
                  {isOAuthCredential && (
                    <button
                      className="btn btn-sm"
                      onClick={() => void refreshCredential(cred)}
                      disabled={Boolean(refreshing[cred.id])}
                    >
                      {refreshing[cred.id] ? "Refreshing..." : "Refresh"}
                    </button>
                  )}
                  <button
                    className="btn btn-sm btn-ghost"
                    onClick={() => void remove(cred.id, cred.name)}
                  >
                    Delete
                  </button>
                </div>
                {testResults[cred.id] && (
                  <p
                    className={`credential-test-result ${
                      testResults[cred.id].ok ? "is-ok" : "is-error"
                    }`}
                  >
                    {testResults[cred.id].message} ·{" "}
                    {testResults[cred.id].latency_ms} ms
                  </p>
                )}
                </>
                  );
                })()}
              </article>
              ))}
            </div>
          </>
        )}
      </main>

      {modal && (
        <CreateCredentialModal
          presets={credentialPresets}
          onClose={() => setModal(false)}
          onCreated={() => {
            setModal(false);
            load();
          }}
        />
      )}
    </div>
  );
}
