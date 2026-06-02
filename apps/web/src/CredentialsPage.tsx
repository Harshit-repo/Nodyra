import { useEffect, useMemo, useState } from "react";

import { api } from "./api";
import { HomeHeader } from "./HomeHeader";
import { useToast } from "./ToastProvider";
import type { Credential, CredentialTestResponse } from "./types";

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
      "Stores a provider API key plus optional OpenAI-compatible or Azure OpenAI endpoint details.",
    fields: [
      {
        key: "provider",
        label: "Provider",
        kind: "select",
        defaultValue: "openai",
        options: [
          { label: "OpenAI", value: "openai" },
          { label: "Anthropic", value: "anthropic" },
          { label: "OpenAI-compatible", value: "openai_compatible" },
          { label: "Ollama", value: "ollama" },
          { label: "Azure OpenAI", value: "azure_openai" },
        ],
      },
      {
        key: "api_key",
        label: "API key",
        placeholder: "Paste provider key",
        kind: "password",
      },
      {
        key: "base_url",
        label: "Base URL",
        placeholder: "https://api.openai.com/v1 or http://localhost:11434/v1",
      },
      {
        key: "organization",
        label: "Organization",
        placeholder: "Optional OpenAI organization",
      },
      {
        key: "azure_endpoint",
        label: "Azure endpoint",
        placeholder: "https://resource.openai.azure.com",
      },
      {
        key: "azure_api_version",
        label: "Azure API version",
        placeholder: "2024-02-15-preview",
      },
      {
        key: "deployment",
        label: "Deployment",
        placeholder: "Azure deployment name",
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
const PRESET_BY_ID = new Map(CREDENTIAL_PRESETS.map((preset) => [preset.id, preset]));

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

function credentialTypeLabel(type: string): string {
  return PRESET_BY_TYPE.get(type)?.label ?? type;
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
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [presetId, setPresetId] = useState(CREDENTIAL_PRESETS[0].id);
  const [name, setName] = useState(CREDENTIAL_PRESETS[0].label);
  const [scope, setScope] = useState<CredentialScope>("global");
  const [workflowId, setWorkflowId] = useState("");
  const [environmentId, setEnvironmentId] = useState("");
  const [runnerPoolId, setRunnerPoolId] = useState("");
  const [description, setDescription] = useState("");
  const [values, setValues] = useState<Record<string, string>>(
    presetInitialValues(CREDENTIAL_PRESETS[0]),
  );
  const [customFields, setCustomFields] = useState<Field[]>([{ key: "", value: "" }]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");

  const preset = PRESET_BY_ID.get(presetId) ?? CREDENTIAL_PRESETS[0];
  const groups = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const visible = CREDENTIAL_PRESETS.filter((item) => {
      if (!needle) return true;
      return `${item.label} ${item.type} ${item.group} ${item.summary}`
        .toLowerCase()
        .includes(needle);
    });
    return visible.reduce<Record<string, CredentialPreset[]>>((acc, item) => {
      acc[item.group] = [...(acc[item.group] ?? []), item];
      return acc;
    }, {});
  }, [search]);

  function selectPreset(nextId: string) {
    const next = PRESET_BY_ID.get(nextId) ?? CREDENTIAL_PRESETS[0];
    const previous = PRESET_BY_ID.get(presetId);
    setPresetId(next.id);
    setValues(presetInitialValues(next));
    setCustomFields([{ key: "", value: "" }]);
    setError("");
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

  async function submit() {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError("");
    const data: Record<string, string> = {};
    if (preset.fields.length) {
      for (const field of preset.fields) {
        const value = values[field.key] ?? "";
        if (field.required && !value.trim()) {
          setError(`Enter ${field.label}.`);
          setBusy(false);
          return;
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
        setBusy(false);
        return;
      }
    } else {
      for (const field of customFields) {
        if (field.key.trim()) data[field.key.trim()] = field.value;
      }
      if (Object.keys(data).length === 0) {
        setError("Add at least one custom field.");
        setBusy(false);
        return;
      }
    }
    try {
      if (scope === "workflow" && !workflowId.trim()) {
        setError("Enter Workflow ID.");
        setBusy(false);
        return;
      }
      if (scope === "environment" && !environmentId.trim()) {
        setError("Enter Environment ID.");
        setBusy(false);
        return;
      }
      if (scope === "runner_pool" && !runnerPoolId.trim()) {
        setError("Enter Runner pool ID.");
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
        onClick={(e) => e.stopPropagation()}
      >
        <div className="credential-modal-head">
          <div>
            <h2>New credential</h2>
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

            {preset.fields.length > 0 ? (
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
          <button
            className="btn btn-primary"
            onClick={() => void submit()}
            disabled={busy}
          >
            {busy ? "Saving..." : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function CredentialsPage() {
  const [credentials, setCredentials] = useState<Credential[] | null>(null);
  const [error, setError] = useState("");
  const [modal, setModal] = useState(false);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [scopeFilter, setScopeFilter] = useState("all");
  const [testing, setTesting] = useState<Record<string, boolean>>({});
  const [testResults, setTestResults] = useState<
    Record<string, CredentialTestResponse>
  >({});
  const { notify } = useToast();

  const filteredCredentials = useMemo(() => {
    if (!credentials) return [];
    const needle = query.trim().toLowerCase();
    return credentials.filter((cred) => {
      const preset = PRESET_BY_TYPE.get(cred.type);
      const text = `${cred.name} ${cred.type} ${preset?.label ?? ""} ${
        cred.description ?? ""
      } ${cred.keys.join(" ")}`.toLowerCase();
      if (needle && !text.includes(needle)) return false;
      if (typeFilter !== "all" && cred.type !== typeFilter) return false;
      if (scopeFilter !== "all" && cred.scope !== scopeFilter) return false;
      return true;
    });
  }, [credentials, query, scopeFilter, typeFilter]);

  const availableTypes = useMemo(() => {
    const types = new Set(credentials?.map((cred) => cred.type) ?? []);
    return Array.from(types).sort((a, b) =>
      credentialTypeLabel(a).localeCompare(credentialTypeLabel(b)),
    );
  }, [credentials]);

  function load() {
    api
      .listCredentials()
      .then(setCredentials)
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
                    {credentialTypeLabel(type)}
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
                <div className="env-card-head">
                  <div className="env-title">
                    <h3>{cred.name}</h3>
                  </div>
                  <span className="cred-type">{credentialTypeLabel(cred.type)}</span>
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
              </article>
              ))}
            </div>
          </>
        )}
      </main>

      {modal && (
        <CreateCredentialModal
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
