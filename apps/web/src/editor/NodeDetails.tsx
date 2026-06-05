import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import { categoryColor } from "../categories";
import {
  LLM_PROVIDER_VARIANTS,
  getLlmVariant,
  visibleCredentialFields,
} from "../llmProviders";
import { isBrandIconName, NodeIcon } from "../NodeIcon";
import { useToast } from "../ToastProvider";
import type {
  Credential,
  CredentialParamSpec,
  NodeManifest,
  NodeSource,
  ParamSpec,
} from "../types";
import { DataPanel } from "./DataPanel";
import { TimezoneSelect } from "./fields/TimezoneSelect";
import { fromAiExpr, isFromAiExpr, paramArgType } from "./toolParam";
import { missingFor } from "./missingPackages";
import { useEditor } from "./store";
import { useServerPlatform } from "../hooks/useServerPlatform";

interface CredentialRef {
  __noodle_credential__: true;
  id: string;
  key: string;
}

function isCredentialRef(value: unknown): value is CredentialRef {
  return (
    Boolean(value) &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (value as Record<string, unknown>).__noodle_credential__ === true &&
    typeof (value as Record<string, unknown>).id === "string"
  );
}

function makeCredentialRef(id: string, key: string): CredentialRef {
  return { __noodle_credential__: true, id, key };
}

// Catch-all credential types accepted by any picker regardless of declared type.
const GENERIC_CRED_TYPES = ["generic", "apiKey", "oauth2"];

/** Decide whether a stored credential is eligible for a node param's picker.
 *
 *  A credential qualifies when its type lines up with the param's declared
 *  credential type (or it is a generic catch-all) AND it carries the field(s)
 *  the node will read.
 *
 *  Multi-field credentials — the single "Credentials" picker, `key === "*"` —
 *  legitimately store only a *subset* of the declared fields: an Ollama
 *  `llm_provider` credential has no `api_key`, a non-Azure one has no
 *  `azure_endpoint`, and so on. Requiring every declared field hides every
 *  partially-filled credential and surfaces it as "⚠ Missing", so a multi-field
 *  credential matches when it carries *at least one* declared field. */
export function credentialMatchesParam(
  cred: Pick<Credential, "type" | "keys">,
  meta: CredentialParamSpec | null | undefined,
  targetKey: string,
): boolean {
  if (
    meta?.type &&
    cred.type !== meta.type &&
    !GENERIC_CRED_TYPES.includes(cred.type)
  ) {
    return false;
  }
  if (meta?.multi) {
    const fields = meta.fields?.length ? meta.fields : [meta.key];
    return fields.some((field) => cred.keys.includes(field));
  }
  return cred.keys.includes(targetKey);
}

/** Query params for a dynamic-options fetch: the selected credential's id plus
 *  any provider/base_url/workflow context the loader uses, skipping empties. */
export function buildLoadOptionsParams(
  credential: { id: string } | null,
  params: Record<string, unknown>,
): Record<string, string> {
  const out: Record<string, string> = {};
  if (credential?.id) out.credential_id = credential.id;
  for (const key of ["provider", "base_url", "workflow_id"]) {
    const value = params[key];
    if (typeof value === "string" && value.trim()) out[key] = value.trim();
  }
  return out;
}

/** Combine fetched options with the current free-text value so a typed,
 *  unlisted value is never lost and never duplicated. */
export function mergeOptions(fetched: string[], current: string): string[] {
  const list = [...fetched];
  if (current && !list.includes(current)) list.unshift(current);
  return list;
}

function credentialScopeLabel(cred: Credential): string {
  if (cred.scope === "workflow" && cred.workflow_id) {
    return `workflow ${cred.workflow_id.slice(0, 8)}`;
  }
  if (cred.scope === "environment" && cred.environment_id) {
    return `environment ${cred.environment_id.slice(0, 8)}`;
  }
  if (cred.scope === "runner_pool" && cred.runner_pool_id) {
    return `runner ${cred.runner_pool_id}`;
  }
  return cred.scope;
}

function JsonField({
  value,
  onChange,
}: {
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const [text, setText] = useState(() =>
    value === null || value === undefined ? "" : JSON.stringify(value, null, 2),
  );
  const [invalid, setInvalid] = useState(false);

  return (
    <textarea
      className={`field-input field-json${invalid ? " field-invalid" : ""}`}
      value={text}
      spellCheck={false}
      rows={4}
      placeholder='{ "key": "value" }'
      onChange={(e) => {
        const next = e.target.value;
        setText(next);
        if (next.trim() === "") {
          setInvalid(false);
          onChange(null);
          return;
        }
        try {
          onChange(JSON.parse(next));
          setInvalid(false);
        } catch {
          setInvalid(true);
        }
      }}
    />
  );
}

interface KvRow {
  key: string;
  value: string;
}

function rowsFromValue(value: unknown): KvRow[] {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const entries = Object.entries(value as Record<string, unknown>).map(
      ([k, v]) => ({
        key: k,
        value: typeof v === "string" ? v : JSON.stringify(v),
      }),
    );
    return entries.length > 0 ? entries : [{ key: "", value: "" }];
  }
  return [{ key: "", value: "" }];
}

function rowsToDict(rows: KvRow[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (key) out[key] = row.value;
  }
  return out;
}

function KeyValueField({
  value,
  onChange,
  valueChoices,
}: {
  value: unknown;
  onChange: (v: unknown) => void;
  valueChoices?: unknown[] | null;
}) {
  const initialMode: "fields" | "json" =
    value && typeof value === "object" && !Array.isArray(value)
      ? "fields"
      : value === null || value === undefined
        ? "fields"
        : "json";
  const [mode, setMode] = useState<"fields" | "json">(initialMode);
  const [rows, setRows] = useState<KvRow[]>(() => rowsFromValue(value));
  const [jsonText, setJsonText] = useState(() =>
    value === null || value === undefined ? "" : JSON.stringify(value, null, 2),
  );
  const [invalid, setInvalid] = useState(false);

  function commitRows(next: KvRow[]): void {
    setRows(next);
    onChange(rowsToDict(next));
  }

  function switchToJson(): void {
    setJsonText(JSON.stringify(rowsToDict(rows), null, 2));
    setInvalid(false);
    setMode("json");
  }

  function switchToFields(): void {
    if (!jsonText.trim()) {
      setRows([{ key: "", value: "" }]);
      onChange({});
      setMode("fields");
      return;
    }
    try {
      const parsed: unknown = JSON.parse(jsonText);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        const next = rowsFromValue(parsed);
        setRows(next);
        onChange(parsed);
        setInvalid(false);
        setMode("fields");
      }
    } catch {
      /* keep JSON mode if not parseable */
    }
  }

  return (
    <div className="kv-field">
      <div className="kv-toolbar">
        <button
          type="button"
          className={`kv-mode-btn${mode === "fields" ? " is-active" : ""}`}
          onClick={() => {
            if (mode !== "fields") switchToFields();
          }}
        >
          Fields
        </button>
        <button
          type="button"
          className={`kv-mode-btn${mode === "json" ? " is-active" : ""}`}
          onClick={() => {
            if (mode !== "json") switchToJson();
          }}
        >
          Raw JSON
        </button>
      </div>

      {mode === "fields" ? (
        <KvRowList
          rows={rows}
          commitRows={commitRows}
          valueChoices={valueChoices}
        />
      ) : (
        <textarea
          className={`field-input field-json${invalid ? " field-invalid" : ""}`}
          rows={5}
          spellCheck={false}
          placeholder='{ "key": "value" }'
          value={jsonText}
          onChange={(e) => {
            const next = e.target.value;
            setJsonText(next);
            if (next.trim() === "") {
              setInvalid(false);
              onChange(null);
              return;
            }
            try {
              onChange(JSON.parse(next));
              setInvalid(false);
            } catch {
              setInvalid(true);
            }
          }}
        />
      )}
    </div>
  );
}

const KV_DRAG_TYPE = "application/x-noodle-kv-row";

function KvRowList({
  rows,
  commitRows,
  valueChoices,
}: {
  rows: KvRow[];
  commitRows: (next: KvRow[]) => void;
  valueChoices?: unknown[] | null;
}) {
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);
  const choices = valueChoices?.map((choice) => String(choice)) ?? [];

  function moveRow(from: number, to: number): void {
    if (from === to || to < 0 || to >= rows.length) return;
    const next = rows.slice();
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    commitRows(next);
  }

  return (
    <div className="kv-rows">
      {rows.map((row, i) => (
        <div
          className={`kv-row${dragOver === i ? " kv-row-drop" : ""}`}
          key={i}
          onDragOver={(e) => {
            // Only respond to our own row drags — text/expression drops on
            // the value input still pass through via exprDropHandlers.
            if (!e.dataTransfer.types.includes(KV_DRAG_TYPE)) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = "move";
            setDragOver(i);
          }}
          onDragLeave={() => {
            if (dragOver === i) setDragOver(null);
          }}
          onDrop={(e) => {
            if (!e.dataTransfer.types.includes(KV_DRAG_TYPE)) return;
            e.preventDefault();
            const from = parseInt(
              e.dataTransfer.getData(KV_DRAG_TYPE) || "-1",
              10,
            );
            if (Number.isFinite(from) && from >= 0) moveRow(from, i);
            setDragOver(null);
            setDragFrom(null);
          }}
        >
          <span
            className="kv-handle"
            title="Drag to reorder"
            draggable
            onDragStart={(e) => {
              e.dataTransfer.setData(KV_DRAG_TYPE, String(i));
              e.dataTransfer.effectAllowed = "move";
              setDragFrom(i);
            }}
            onDragEnd={() => {
              setDragFrom(null);
              setDragOver(null);
            }}
          >
            ⠿
          </span>
          <input
            className="field-input"
            placeholder="key"
            value={row.key}
            onChange={(e) =>
              commitRows(
                rows.map((r, j) =>
                  j === i ? { ...r, key: e.target.value } : r,
                ),
              )
            }
          />
          {choices.length > 0 ? (
            <select
              className="field-input kv-value-select"
              value={row.value}
              onChange={(e) =>
                commitRows(
                  rows.map((r, j) =>
                    j === i ? { ...r, value: e.target.value } : r,
                  ),
                )
              }
            >
              <option value="" disabled>
                Select type
              </option>
              {row.value && !choices.includes(row.value) && (
                <option value={row.value}>{row.value}</option>
              )}
              {choices.map((choice) => (
                <option key={choice} value={choice}>
                  {choice}
                </option>
              ))}
            </select>
          ) : (
            <input
              className="field-input"
              placeholder="value"
              value={row.value}
              onChange={(e) =>
                commitRows(
                  rows.map((r, j) =>
                    j === i ? { ...r, value: e.target.value } : r,
                  ),
                )
              }
              {...exprDropHandlers(row.value, (next) =>
                commitRows(
                  rows.map((r, j) => (j === i ? { ...r, value: next } : r)),
                ),
              )}
            />
          )}
          <button
            type="button"
            className="kv-remove"
            aria-label="Remove"
            onClick={() => {
              const filtered = rows.filter((_, j) => j !== i);
              commitRows(
                filtered.length > 0 ? filtered : [{ key: "", value: "" }],
              );
            }}
          >
            ×
          </button>
        </div>
      ))}
      <button
        type="button"
        className="btn btn-sm btn-ghost"
        onClick={() => commitRows([...rows, { key: "", value: "" }])}
      >
        + Add field
      </button>
      {dragFrom !== null && (
        <p className="muted kv-drag-hint">Drop on a row to reorder.</p>
      )}
    </div>
  );
}

function exprDropHandlers(
  current: string,
  onChange: (v: string) => void,
): {
  onDragOver: React.DragEventHandler<HTMLInputElement | HTMLTextAreaElement>;
  onDrop: React.DragEventHandler<HTMLInputElement | HTMLTextAreaElement>;
} {
  return {
    onDragOver: (e) => {
      // Only accept text-like drags. Calling preventDefault flips the cursor
      // and lets the drop fire.
      const types = Array.from(e.dataTransfer.types);
      if (
        types.includes("application/x-noodle-expression") ||
        types.includes("text/plain")
      ) {
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
      }
    },
    onDrop: (e) => {
      const expr =
        e.dataTransfer.getData("application/x-noodle-expression") ||
        e.dataTransfer.getData("text/plain");
      if (!expr) return;
      e.preventDefault();
      const el = e.currentTarget;
      const start = el.selectionStart ?? current.length;
      const end = el.selectionEnd ?? current.length;
      const next = current.slice(0, start) + expr + current.slice(end);
      onChange(next);
      // Move caret after the inserted expression on the next tick (after
      // React has applied the controlled value).
      window.setTimeout(() => {
        const pos = start + expr.length;
        try {
          el.setSelectionRange(pos, pos);
        } catch {
          /* ignore */
        }
        el.focus();
      }, 0);
    },
  };
}

const CRED_FIELD_LABELS: Record<string, string> = {
  api_key: "API Key",
  username: "Username",
  password: "Password",
  token: "Token",
  value: "Value",
  name: "Name",
  url: "URL",
  uri: "URI",
  connection_string: "Connection String",
  bot_token: "Bot Token",
  webhook_url: "Webhook URL",
  private_key: "Private Key (PEM)",
  index_host: "Index Host",
  aws_access_key_id: "Access Key ID",
  aws_secret_access_key: "Secret Access Key",
  host: "Host",
  port: "Port",
  database: "Database",
  access_token: "Access Token",
  provider: "Provider",
  base_url: "Base URL",
  organization: "Organization",
  site_url: "Site URL",
  app_name: "App name",
  azure_endpoint: "Azure Endpoint",
  azure_api_version: "Azure API Version",
  deployment: "Deployment",
};

const CRED_TYPE_LABELS: Record<string, string> = {
  http_basic: "Basic Auth",
  http_header: "Header Auth",
  http_query: "Query Auth",
  webhook_secret: "Webhook Secret",
  openai: "OpenAI API Key",
  anthropic: "Anthropic API Key",
  llm_provider: "LLM Provider",
  cohere: "Cohere API Key",
  deepl: "DeepL API Key",
  slack_bot: "Slack Bot Token",
  smtp: "SMTP Credentials",
  github: "GitHub Token",
  aws: "AWS Credentials",
  ssh: "SSH Credentials",
  pinecone: "Pinecone Credentials",
  mongodb: "MongoDB URI",
  redis: "Redis URL",
  elasticsearch: "Elasticsearch Credentials",
  azure_blob: "Azure Blob Connection String",
  postgres: "Postgres Connection",
  mysql: "MySQL Account",
};

// Acronyms preserved in uppercase when auto-titling snake_case param names.
const PARAM_LABEL_ACRONYMS = new Set([
  "http", "https", "url", "uri", "id", "aws", "api", "sms", "ip", "ips",
  "json", "xml", "os", "csv", "jwt", "oauth", "oauth2", "sql", "cors",
  "tls", "ssl", "ssh", "tcp", "udp", "dns", "ai", "ldap", "smtp", "ftp",
  "sftp", "gcs", "s3", "rss", "uuid", "md5", "sha", "html", "css", "rgb",
  "cli", "io", "cdn", "cpu", "ram", "gpu", "rgb",
]);

export function formatParamLabel(name: string): string {
  if (!name) return "";
  return name
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((word) => {
      const lower = word.toLowerCase();
      if (PARAM_LABEL_ACRONYMS.has(lower)) return lower.toUpperCase();
      return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
    })
    .join(" ");
}

function isSecretField(name: string): boolean {
  return /password|secret|key|token|private|uri|url|connection/i.test(name);
}

function CredentialCreateModal({
  credType,
  fields,
  typeLabel,
  workflowId,
  onClose,
  onCreated,
}: {
  credType: string;
  fields: string[];
  typeLabel: string;
  workflowId: string | null;
  onClose: () => void;
  onCreated: (id: string, key: string, credential: Credential) => void;
}) {
  const displayLabel = CRED_TYPE_LABELS[credType] ?? typeLabel;
  const isLlm = credType === "llm_provider";
  const [name, setName] = useState(displayLabel);
  const [fieldValues, setFieldValues] = useState<Record<string, string>>(() => {
    const init = Object.fromEntries(fields.map((f) => [f, ""]));
    if (isLlm) {
      init.provider = "openai";
      init.base_url = getLlmVariant("openai").baseUrlDefault ?? "";
    }
    return init;
  });
  const [scope, setScope] = useState<"workflow" | "global">(
    workflowId ? "workflow" : "global",
  );
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState("");
  const { notify } = useToast();

  const refKey = fields.length > 1 ? "*" : (fields[0] ?? credType);
  const variant = isLlm ? getLlmVariant(fieldValues.provider) : null;
  // The set of credential fields actually rendered (provider-aware for LLM).
  const renderedFields = isLlm
    ? visibleCredentialFields(fieldValues.provider, showAdvanced)
    : fields;

  function setField(key: string, value: string): void {
    setFieldValues((cur) => ({ ...cur, [key]: value }));
  }

  function onProviderChange(next: string): void {
    const v = getLlmVariant(next);
    setFieldValues((cur) => ({
      ...cur,
      provider: next,
      base_url: cur.base_url?.trim() ? cur.base_url : (v.baseUrlDefault ?? ""),
    }));
    setShowAdvanced(false);
  }

  function collectData(): Record<string, string> {
    const data: Record<string, string> = {};
    if (isLlm && fieldValues.provider) data.provider = fieldValues.provider;
    for (const f of renderedFields) {
      if (fieldValues[f]?.trim()) data[f] = fieldValues[f].trim();
    }
    return data;
  }

  async function handleCreate(): Promise<void> {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      const created = await api.createCredential({
        name: name.trim(),
        type: credType,
        scope,
        workflow_id: scope === "workflow" ? workflowId : null,
        description: displayLabel,
        data: collectData(),
      });
      notify("Credential created.", "success");
      onCreated(created.id, refKey, created);
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  }

  async function handleTest(): Promise<void> {
    if (testing) return;
    setTesting(true);
    setError("");
    try {
      const result = await api.testCredentialDraft({
        type: credType,
        data: collectData(),
        context: {},
      });
      notify(
        result.ok ? "Credential connected." : result.message,
        result.ok ? "success" : "error",
      );
      if (!result.ok) setError(result.message);
    } catch (err) {
      setError(String(err));
      notify(String(err), "error");
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal cred-quick-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="cred-quick-modal-head">
          <div>
            <h2>New credential</h2>
            <p className="muted">{displayLabel}</p>
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm cred-quick-close"
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <label className="credential-form-field">
          <span>Name</span>
          <input
            className="field-input"
            placeholder="My credential"
            value={name}
            autoFocus
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleCreate();
              if (e.key === "Escape") onClose();
            }}
          />
        </label>

        {isLlm && (
          <label className="credential-form-field">
            <span>Provider</span>
            <select
              className="field-input"
              value={fieldValues.provider}
              onChange={(e) => onProviderChange(e.target.value)}
            >
              {LLM_PROVIDER_VARIANTS.map((v) => (
                <option key={v.value} value={v.value}>
                  {v.label}
                </option>
              ))}
            </select>
          </label>
        )}

        {renderedFields.map((field) => (
          <label key={field} className="credential-form-field">
            <span>
              {CRED_FIELD_LABELS[field] ?? field}
              {isLlm &&
              field === "api_key" &&
              variant?.apiKey === "required"
                ? " *"
                : ""}
            </span>
            <input
              className="field-input"
              type={isSecretField(field) ? "password" : "text"}
              placeholder={CRED_FIELD_LABELS[field] ?? field}
              value={fieldValues[field] ?? ""}
              onChange={(e) => setField(field, e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void handleCreate();
                if (e.key === "Escape") onClose();
              }}
            />
          </label>
        ))}

        {isLlm &&
          variant !== null &&
          variant.advancedFields.length > 0 &&
          variant.value !== "azure_openai" && (
            <button
              type="button"
              className="btn btn-ghost btn-sm cred-advanced-toggle"
              onClick={() => setShowAdvanced((v) => !v)}
            >
              {showAdvanced ? "Hide advanced" : "Advanced options"}
            </button>
          )}

        {workflowId && (
          <div className="cred-quick-scope">
            <label>
              <input
                type="radio"
                checked={scope === "workflow"}
                onChange={() => setScope("workflow")}
              />
              This workflow only
            </label>
            <label>
              <input
                type="radio"
                checked={scope === "global"}
                onChange={() => setScope("global")}
              />
              Global (all workflows)
            </label>
          </div>
        )}

        {error && <p className="error-text">{error}</p>}

        <div className="modal-actions">
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={testing}
            onClick={() => void handleTest()}
          >
            {testing ? "Testing…" : "Test connection"}
          </button>
          <button
            type="button"
            className="btn btn-sm"
            disabled={busy || !name.trim()}
            onClick={() => void handleCreate()}
          >
            {busy ? "Creating…" : "Create credential"}
          </button>
        </div>
      </div>
    </div>
  );
}

function CredentialParamField({
  spec,
  value,
  onChange,
  credentialContext,
}: {
  spec: ParamSpec;
  value: unknown;
  onChange: (v: unknown) => void;
  credentialContext?: Record<string, unknown>;
}) {
  const workflowId = useEditor((s) => s.workflowId);
  const meta = spec.credential;
  const selected = isCredentialRef(value) ? value : null;
  const inlineValue = typeof value === "string" && value.trim() ? value : "";
  const fields = meta?.fields?.length ? meta.fields : [meta?.key ?? spec.name];
  const refKey = meta?.multi ? "*" : (meta?.key || spec.name);
  const targetKey = selected?.key || refKey;
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState("");
  const { notify } = useToast();

  function load(): void {
    setLoading(true);
    api
      .listCredentials()
      .then((items) => {
        setCredentials(items);
        setError("");
      })
      .catch((err) => setError(String(err)))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  const matching = credentials.filter((cred) =>
    credentialMatchesParam(cred, meta, targetKey),
  );

  const selectedValue = selected ? `${selected.id}:${selected.key}` : "";
  const selectedCredential = selected
    ? credentials.find((cred) => cred.id === selected.id)
    : undefined;
  const requiredScopes = spec.required_scopes ?? [];
  const credentialScopes = new Set(selectedCredential?.oauth_scopes ?? []);
  const missingScopes =
    selectedCredential && requiredScopes.length > 0
      ? requiredScopes.filter((scope) => !credentialScopes.has(scope))
      : [];
  const selectedMissing =
    selected &&
    !matching.some((cred) => {
      if (cred.id !== selected.id) return false;
      // Multi-field picker stores ``key: "*"`` — the credential's actual
      // ``keys`` are field names like ["username", "password"], so a
      // plain ``includes("*")`` always lies. Being in the filtered
      // ``matching`` list is enough proof the credential is still valid.
      if (selected.key === "*") return true;
      return cred.keys.includes(selected.key);
    });

  async function moveInline(): Promise<void> {
    if (!inlineValue || !meta) return;
    setBusy(true);
    setError("");
    try {
      const created = await api.createCredential({
        name: meta.label || spec.name,
        type: meta.type,
        scope: workflowId ? "workflow" : "global",
        workflow_id: workflowId,
        description: meta.label,
        data: { [meta.key || spec.name]: inlineValue },
      });
      onChange(makeCredentialRef(created.id, refKey));
      notify("Moved to credential store.", "success");
      load();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function testSelectedCredential(): Promise<void> {
    if (!selected) return;
    setTesting(true);
    setError("");
    try {
      const result = await api.testCredential(selected.id, {
        workflow_id: workflowId,
        context: credentialContext ?? {},
      });
      notify(
        result.ok ? "Credential connected." : result.message,
        result.ok ? "success" : "error",
      );
      if (!result.ok) setError(result.message);
    } catch (err) {
      setError(String(err));
      notify(String(err), "error");
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="credential-param">
      <div className="credential-select-row">
        <select
          className="field-input"
          value={selectedValue}
          disabled={loading}
          onChange={(e) => {
            const val = e.target.value;
            if (!val) { onChange(""); return; }
            const colonIdx = val.indexOf(":");
            const id = val.slice(0, colonIdx);
            const key = val.slice(colonIdx + 1);
            onChange(id && key ? makeCredentialRef(id, key) : "");
          }}
        >
          <option value="">
            {loading
              ? "Loading…"
              : matching.length === 0
                ? "No matching credentials — add one →"
                : "— Select credential —"}
          </option>
          {selectedMissing && selected && (
            <option value={selectedValue}>
              ⚠ Missing: {selected.id.slice(0, 8)}
            </option>
          )}
          {matching.map((cred) => (
            <option
              key={`${cred.id}:${targetKey}`}
              value={`${cred.id}:${targetKey}`}
            >
              {cred.name}
              {cred.scope !== "global"
                ? ` · ${credentialScopeLabel(cred)}`
                : ""}
            </option>
          ))}
        </select>

        <button
          type="button"
          className="btn btn-sm btn-ghost cred-add-btn"
          title="Add new credential"
          onClick={() => setModalOpen(true)}
        >
          +
        </button>

        {selected && (
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            disabled={testing}
            onClick={() => void testSelectedCredential()}
          >
            {testing ? "…" : "Test"}
          </button>
        )}
      </div>

      {inlineValue && !selected && (
        <div className="credential-inline-warning">
          <span>Inline secret in workflow — move to credential store.</span>
          <button
            type="button"
            className="btn btn-sm"
            disabled={busy}
            onClick={() => void moveInline()}
          >
            Move
          </button>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={() => onChange("")}
          >
            Clear
          </button>
        </div>
      )}

      {error && <p className="error-text">{error}</p>}

      {missingScopes.length > 0 && (
        <div className="credential-inline-warning">
          <span>Missing OAuth scopes: {missingScopes.join(", ")}</span>
        </div>
      )}

      {modalOpen && meta && (
        <CredentialCreateModal
          credType={meta.type}
          fields={fields}
          typeLabel={meta.label || spec.name}
          workflowId={workflowId}
          onClose={() => setModalOpen(false)}
          onCreated={(id, key, created) => {
            setCredentials((items) => [
              created,
              ...items.filter((item) => item.id !== created.id),
            ]);
            onChange(makeCredentialRef(id, key));
            setModalOpen(false);
            load();
          }}
        />
      )}
    </div>
  );
}

export interface ExprContext {
  json?: unknown;
  inputs?: Record<string, unknown>;
  nodes?: Record<string, unknown>;
}

const EXPR_RE = /\{\{[\s\S]+?\}\}/;
const EXPR_RE_GLOBAL = /\{\{[\s\S]+?\}\}/g;

function formatResultText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

type PreviewPart =
  | { kind: "text"; value: string }
  | { kind: "expr"; raw: string; value: unknown }
  | { kind: "error"; raw: string; error: string };

type ResultView = "text" | "html";

/** Textarea that paints `{{ }}` blocks in accent green using the
 *  mirror-overlay technique: a styled <div> renders the highlighted text under
 *  a transparent <textarea> that handles caret + editing. Scroll position stays
 *  in sync. */
// ---------------------------------------------------------------------------
// Expression autocomplete helpers
// ---------------------------------------------------------------------------

function getTokenBeforeCursor(value: string, cursorPos: number): string {
  const before = value.slice(0, cursorPos);
  // Grab the last contiguous token that starts with $
  const m = before.match(/\$[\w.\["\]]*$/);
  return m ? m[0] : "";
}

export function computeSuggestions(
  value: string,
  cursorPos: number,
  ctx?: ExprContext,
): string[] {
  const token = getTokenBeforeCursor(value, cursorPos);
  if (!token) return [];

  const results: string[] = [];

  if (token.startsWith("$json.")) {
    const prefix = token.slice("$json.".length);
    const keys =
      ctx?.json && typeof ctx.json === "object" && ctx.json !== null
        ? Object.keys(ctx.json as Record<string, unknown>)
        : [];
    for (const k of keys) {
      if (k.startsWith(prefix)) results.push(`$json.${k}`);
    }
  } else if (token.startsWith("$json")) {
    results.push("$json.");
    const keys =
      ctx?.json && typeof ctx.json === "object" && ctx.json !== null
        ? Object.keys(ctx.json as Record<string, unknown>)
        : [];
    for (const k of keys) results.push(`$json.${k}`);
  } else if (token.startsWith("$node")) {
    const nodeIds = Object.keys(ctx?.nodes ?? {});
    for (const id of nodeIds) results.push(`$node["${id}"].`);
  } else if (token.startsWith("$env")) {
    results.push("$env.KEY");
  } else if (token.startsWith("$run")) {
    results.push("$run.id", "$run.status", "$run.startedAt");
  } else if (token.startsWith("$")) {
    results.push('$json.', '$node["', "$env.", "$run.");
  }

  return results.slice(0, 10);
}

// ---------------------------------------------------------------------------

function HighlightedTextarea({
  value,
  onChange,
  className = "",
  placeholder,
  autoFocus,
  spellCheck = false,
  rows,
  onDrop,
  onDragOver,
  onKeyDown,
  taRef: taRefProp,
}: {
  value: string;
  onChange: (v: string) => void;
  className?: string;
  placeholder?: string;
  autoFocus?: boolean;
  spellCheck?: boolean;
  rows?: number;
  onDrop?: React.DragEventHandler<HTMLTextAreaElement>;
  onDragOver?: React.DragEventHandler<HTMLTextAreaElement>;
  onKeyDown?: React.KeyboardEventHandler<HTMLTextAreaElement>;
  taRef?: React.RefObject<HTMLTextAreaElement>;
}) {
  const taRefInternal = useRef<HTMLTextAreaElement>(null);
  const taRef = taRefProp ?? taRefInternal;
  const mirrorRef = useRef<HTMLDivElement>(null);

  const segments = (() => {
    const out: Array<{ text: string; expr: boolean }> = [];
    let lastIndex = 0;
    EXPR_RE_GLOBAL.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = EXPR_RE_GLOBAL.exec(value)) !== null) {
      if (m.index > lastIndex) {
        out.push({ text: value.slice(lastIndex, m.index), expr: false });
      }
      out.push({ text: m[0], expr: true });
      lastIndex = m.index + m[0].length;
    }
    if (lastIndex < value.length) {
      out.push({ text: value.slice(lastIndex), expr: false });
    }
    return out;
  })();

  const syncScroll = () => {
    const ta = taRef.current;
    const m = mirrorRef.current;
    if (!ta || !m) return;
    m.scrollTop = ta.scrollTop;
    m.scrollLeft = ta.scrollLeft;
  };

  return (
    <div className={`hl-ta-wrap ${className}`}>
      <div ref={mirrorRef} className="hl-ta-mirror" aria-hidden>
        {segments.map((s, i) =>
          s.expr ? (
            <span key={i} className="hl-ta-expr">
              {s.text}
            </span>
          ) : (
            <span key={i}>{s.text}</span>
          ),
        )}
        {/* Trailing newline ensures the mirror grows when the textarea does. */}
        {value.endsWith("\n") && "\n"}
        {/* Non-breaking space keeps empty lines/empty content rendering. */}
        {value === "" && " "}
      </div>
      <textarea
        ref={taRef}
        className="hl-ta-input"
        value={value}
        rows={rows}
        placeholder={placeholder}
        spellCheck={spellCheck}
        autoFocus={autoFocus}
        onChange={(e) => onChange(e.target.value)}
        onScroll={syncScroll}
        onKeyDown={onKeyDown}
        onDrop={onDrop}
        onDragOver={onDragOver}
      />
    </div>
  );
}

/** n8n-style expand modal: editor on the left, live Result preview on the right.
 *  Result evaluates faithfully via the backend on a debounce, so HTML/text bodies
 *  are visible as they will be at runtime — no need to execute the workflow. */
function ExpressionEditorModal({
  label,
  value,
  onChange,
  ctx,
  onClose,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  ctx?: ExprContext;
  onClose: () => void;
}) {
  const [view, setView] = useState<ResultView>("text");
  const [state, setState] = useState<{
    result?: unknown;
    error?: string | null;
    parts?: PreviewPart[];
    loading: boolean;
  }>({ loading: false });

  // Autocomplete state
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [selectedSuggestion, setSelectedSuggestion] = useState(0);
  const taRef = useRef<HTMLTextAreaElement>(null);

  function updateSuggestions(val: string) {
    const pos = taRef.current?.selectionStart ?? val.length;
    const s = computeSuggestions(val, pos, ctx);
    setSuggestions(s);
    setSelectedSuggestion(0);
  }

  function applySuggestion(suggestion: string) {
    const ta = taRef.current;
    const pos = ta?.selectionStart ?? value.length;
    const token = getTokenBeforeCursor(value, pos);
    const before = value.slice(0, pos - token.length);
    const after = value.slice(pos);
    const newVal = before + suggestion + after;
    onChange(newVal);
    setSuggestions([]);
    // Move cursor to end of inserted text
    setTimeout(() => {
      if (ta) {
        const newPos = before.length + suggestion.length;
        ta.setSelectionRange(newPos, newPos);
        ta.focus();
      }
    }, 0);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (suggestions.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedSuggestion((s) => Math.min(s + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedSuggestion((s) => Math.max(s - 1, 0));
    } else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      applySuggestion(suggestions[selectedSuggestion]);
    } else if (e.key === "Escape") {
      setSuggestions([]);
    }
  }

  const hasData =
    ctx !== undefined &&
    (ctx.json !== undefined ||
      Object.keys(ctx.nodes ?? {}).length > 0 ||
      Object.keys(ctx.inputs ?? {}).length > 0);
  const hasExpr = EXPR_RE.test(value);

  useEffect(() => {
    // No expression to evaluate → result is just the literal value.
    if (!hasExpr) {
      setState({
        result: value,
        error: null,
        parts: value ? [{ kind: "text", value }] : [],
        loading: false,
      });
      return;
    }
    if (!hasData) {
      setState({ result: undefined, error: null, parts: [], loading: false });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, loading: true }));
    const handle = setTimeout(() => {
      api
        .previewExpression({
          value,
          json: ctx?.json,
          inputs: ctx?.inputs,
          nodes: ctx?.nodes,
        })
        .then((res) => {
          if (!cancelled)
            setState({ ...res, parts: res.parts as PreviewPart[], loading: false });
        })
        .catch((err) => {
          if (!cancelled)
            setState({
              error: String(err),
              result: undefined,
              parts: [],
              loading: false,
            });
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [value, ctx, hasExpr, hasData]);

  const resultText = formatResultText(state.result);
  const parts = state.parts ?? [];

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal modal-wide expr-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <h2>
            Editing <span className="expr-modal-label">{label}</span>
          </h2>
          <button className="btn btn-sm btn-ghost" onClick={onClose}>
            ✕
          </button>
        </header>
        <div className="expr-modal-body">
          <section className="expr-modal-pane">
            <div className="expr-modal-pane-head">
              <span>Expression</span>
              <span className="muted expr-modal-hint">
                Anything inside <code>{"{{ }}"}</code> is evaluated
              </span>
            </div>
            <div className="expr-modal-editor-wrap">
              <HighlightedTextarea
                className="expr-modal-editor"
                value={value}
                onChange={(v) => { onChange(v); updateSuggestions(v); }}
                autoFocus
                taRef={taRef}
                onKeyDown={handleKeyDown}
              />
              {suggestions.length > 0 && (
                <ul className="expr-autocomplete">
                  {suggestions.map((s, i) => (
                    <li
                      key={s}
                      className={i === selectedSuggestion ? "active" : ""}
                      onMouseDown={(e) => { e.preventDefault(); applySuggestion(s); }}
                      onMouseEnter={() => setSelectedSuggestion(i)}
                    >
                      {s}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
          <section className="expr-modal-pane">
            <div className="expr-modal-pane-head">
              <span>Result</span>
              <div className="expr-modal-tabs">
                <button
                  type="button"
                  className={view === "text" ? "active" : ""}
                  onClick={() => setView("text")}
                >
                  Text
                </button>
                <button
                  type="button"
                  className={view === "html" ? "active" : ""}
                  onClick={() => setView("html")}
                >
                  HTML
                </button>
              </div>
            </div>
            <div className="expr-modal-result">
              {state.loading && <p className="muted">Evaluating…</p>}
              {!state.loading && hasExpr && !hasData && (
                <p className="muted">
                  Run the workflow once to feed this preview with real input
                  data — until then, only the literal text is shown.
                </p>
              )}
              {!state.loading && state.error && (
                <p className="expr-preview-error">⚠ {state.error}</p>
              )}
              {!state.loading &&
                !state.error &&
                state.result !== undefined &&
                (view === "html" ? (
                  <iframe
                    title="HTML preview"
                    sandbox=""
                    srcDoc={resultText}
                    className="expr-modal-iframe"
                  />
                ) : (
                  <pre className="expr-modal-text">
                    {parts.length === 0
                      ? resultText
                      : parts.map((part, i) => {
                          if (part.kind === "text") {
                            return <span key={i}>{part.value}</span>;
                          }
                          if (part.kind === "error") {
                            return (
                              <span
                                key={i}
                                className="expr-part-error"
                                title={part.error}
                              >
                                {part.raw}
                              </span>
                            );
                          }
                          return (
                            <span key={i} className="expr-part-resolved">
                              {formatResultText(part.value)}
                            </span>
                          );
                        })}
                  </pre>
                ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

/** Searchable dropdown for params with a `load_options` loader. Lazy-fetches
 *  the provider's catalogue; falls back to curated `choices`. Supports free-
 *  text entry — typed values not in the list are accepted as-is. */
function LoadOptionsField({
  spec,
  value,
  onChange,
  params,
}: {
  spec: ParamSpec;
  value: unknown;
  onChange: (v: unknown) => void;
  params: Record<string, unknown>;
}) {
  const current = String(value ?? "");
  const credential = (() => {
    for (const dep of spec.depends_on ?? []) {
      const v = params[dep];
      if (isCredentialRef(v)) return v;
    }
    return null;
  })();
  const curated = (spec.choices ?? []).map(String);
  const [fetched, setFetched] = useState<string[]>(curated);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [query, setQuery] = useState(current);
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const wrapRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  // Keep the input text in sync with external value changes (e.g. preset selects).
  useEffect(() => { setQuery(current); }, [current]);

  function fetchOptions(): void {
    if (!spec.load_options) return;
    setLoading(true);
    setErr("");
    api
      .dynamicOptions(spec.load_options, buildLoadOptionsParams(credential, params))
      .then((res) => setFetched(res.options.map((o) => o.value)))
      .catch(() => setErr("Couldn't load list — type a value or retry."))
      .finally(() => setLoading(false));
  }

  // Auto-fetch on mount and whenever an input the loader keys off changes:
  // the credential, the selected provider, or a custom base_url. Without the
  // provider dependency the model list would stay stale after switching e.g.
  // openai → openrouter. Public catalogues (OpenRouter) load even with no
  // credential; keyed ones fall back to the curated list until a key is set.
  const credentialId = credential?.id;
  const providerKey = typeof params.provider === "string" ? params.provider : "";
  const baseUrlKey = typeof params.base_url === "string" ? params.base_url : "";
  useEffect(() => {
    fetchOptions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [credentialId, providerKey, baseUrlKey]);

  const options = mergeOptions(fetched, current);
  const filtered = query.trim()
    ? options.filter((o) => o.toLowerCase().includes(query.trim().toLowerCase()))
    : options;

  // Close when clicking outside the component.
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  // Scroll highlighted row into view.
  useEffect(() => {
    if (!open || !listRef.current) return;
    listRef.current.querySelector<HTMLLIElement>(".lo-item-hi")?.scrollIntoView({ block: "nearest" });
  }, [highlight, open]);

  function select(opt: string): void {
    onChange(opt);
    setQuery(opt);
    setOpen(false);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>): void {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open) { setOpen(true); setHighlight(0); return; }
      setHighlight((h) => Math.min(h + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      if (open && filtered[highlight]) { e.preventDefault(); select(filtered[highlight]); }
      else setOpen(false);
    } else if (e.key === "Escape") {
      setOpen(false);
      setQuery(current);
    }
  }

  return (
    <div className="load-options-field" ref={wrapRef}>
      <div className="load-options-row">
        <div className="lo-combo">
          <input
            className="field-input lo-input"
            placeholder={spec.placeholder || "Select or type a value"}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              onChange(e.target.value);
              setOpen(true);
              setHighlight(0);
            }}
            onFocus={() => { setOpen(true); setHighlight(0); }}
            onKeyDown={handleKeyDown}
          />
          <button
            type="button"
            className="lo-chevron"
            tabIndex={-1}
            onMouseDown={(e) => { e.preventDefault(); setOpen((o) => !o); setHighlight(0); }}
            aria-label="Toggle options"
          >
            ▾
          </button>
          {open && (
            <ul className="lo-dropdown" ref={listRef} role="listbox">
              {filtered.length === 0 ? (
                <li className="lo-empty">
                  {loading ? "Loading…" : "No matches — value saved as-is"}
                </li>
              ) : (
                filtered.map((opt, i) => (
                  <li
                    key={opt}
                    role="option"
                    aria-selected={opt === current}
                    className={[
                      "lo-item",
                      i === highlight ? "lo-item-hi" : "",
                      opt === current ? "lo-item-sel" : "",
                    ].filter(Boolean).join(" ")}
                    onMouseDown={(e) => { e.preventDefault(); select(opt); }}
                    onMouseEnter={() => setHighlight(i)}
                  >
                    {opt}
                  </li>
                ))
              )}
            </ul>
          )}
        </div>
        <button
          type="button"
          className="btn btn-sm btn-ghost"
          title="Refresh list"
          disabled={loading}
          onClick={() => fetchOptions()}
        >
          {loading ? "…" : "↻"}
        </button>
      </div>
      {err && <p className="field-desc">{err}</p>}
    </div>
  );
}

interface RouteRow {
  method: string;
  path: string;
  output: string;
}

const ROUTE_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];

function routeRowsFromValue(value: unknown): RouteRow[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter(
      (r): r is Record<string, unknown> => Boolean(r) && typeof r === "object",
    )
    .map((r) => ({
      method: typeof r.method === "string" ? r.method : "GET",
      path: typeof r.path === "string" ? r.path : "",
      output: typeof r.output === "string" ? r.output : "",
    }));
}

// Editor for the API Endpoint node's `routes` param: one row per route
// (method + sub-path template → named output branch). Each row's `output`
// becomes a handle on the canvas (see `deriveApiEndpointOutputs` in store.ts).
function RoutesField({
  value,
  onChange,
}: {
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const [rows, setRows] = useState<RouteRow[]>(() => routeRowsFromValue(value));

  function commit(next: RouteRow[]): void {
    setRows(next);
    onChange(
      next.map((r) => ({ method: r.method, path: r.path, output: r.output })),
    );
  }
  function update(index: number, patch: Partial<RouteRow>): void {
    commit(rows.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  return (
    <div className="routes-field">
      {rows.length === 0 && (
        <p className="routes-empty">
          No routes yet. Add one to create an endpoint branch.
        </p>
      )}
      {rows.map((row, i) => (
        <div className="routes-row" key={i}>
          <select
            className="field-input routes-method"
            value={row.method}
            onChange={(e) => update(i, { method: e.target.value })}
          >
            {ROUTE_METHODS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          <input
            className="field-input routes-path"
            placeholder="/{id}"
            value={row.path}
            onChange={(e) => update(i, { path: e.target.value })}
          />
          <span className="routes-arrow" aria-hidden>
            →
          </span>
          <input
            className="field-input routes-output"
            placeholder="branch name"
            value={row.output}
            onChange={(e) => update(i, { output: e.target.value })}
          />
          <button
            type="button"
            className="routes-remove"
            aria-label="Remove route"
            onClick={() => commit(rows.filter((_, idx) => idx !== i))}
          >
            ×
          </button>
        </div>
      ))}
      <button
        type="button"
        className="routes-add"
        onClick={() =>
          commit([
            ...rows,
            { method: "GET", path: "/", output: `route${rows.length + 1}` },
          ])
        }
      >
        + Add route
      </button>
    </div>
  );
}

export function ParamField({
  spec,
  value,
  onChange,
  credentialContext,
  exprContext,
}: {
  spec: ParamSpec;
  value: unknown;
  onChange: (v: unknown) => void;
  credentialContext?: Record<string, unknown>;
  exprContext?: ExprContext;
}) {
  const [expanderOpen, setExpanderOpen] = useState(false);
  if (spec.credential) {
    return (
      <CredentialParamField
        spec={spec}
        value={value}
        onChange={onChange}
        credentialContext={credentialContext}
      />
    );
  }
  if (spec.load_options) {
    return (
      <LoadOptionsField
        spec={spec}
        value={value}
        onChange={onChange}
        params={credentialContext ?? {}}
      />
    );
  }
  // Soft convention: a string param literally named `tz` is treated as an
  // IANA timezone field. Future widget-metadata work can replace this name
  // sniff with an explicit param-spec field.
  if (spec.name === "tz" && spec.type === "string" && !spec.choices?.length) {
    return (
      <TimezoneSelect
        value={String(value ?? "")}
        onChange={(next) => onChange(next)}
        placeholder={spec.placeholder}
      />
    );
  }
  if (spec.widget === "routes_table") {
    return <RoutesField value={value} onChange={onChange} />;
  }
  if (spec.key_value) {
    return (
      <KeyValueField
        value={value}
        onChange={onChange}
        valueChoices={spec.choices}
      />
    );
  }
  if (spec.choices && spec.choices.length > 0) {
    const simpleChoices = spec.choices.filter(
      (choice): choice is string | number =>
        typeof choice === "string" || typeof choice === "number",
    );
    if (simpleChoices.length === spec.choices.length && simpleChoices.length <= 3) {
      return (
        <div className="segmented-field" role="group" aria-label={spec.name}>
          {simpleChoices.map((choice) => {
            const stringChoice = String(choice);
            const active = String(value ?? "") === stringChoice;
            return (
              <button
                type="button"
                key={stringChoice}
                className={active ? "active" : ""}
                onClick={() => onChange(stringChoice)}
              >
                {formatParamLabel(stringChoice)}
              </button>
            );
          })}
        </div>
      );
    }
    return (
      <select
        className="field-input"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
      >
        {spec.choices.map((choice) => (
          <option key={String(choice)} value={String(choice)}>
            {String(choice)}
          </option>
        ))}
      </select>
    );
  }

  if (spec.type === "boolean") {
    return (
      <label className="field-toggle">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span className="field-toggle-track" />
        <span className="field-toggle-text">{value ? "true" : "false"}</span>
      </label>
    );
  }

  if (spec.type === "integer" || spec.type === "number") {
    return (
      <input
        className="field-input"
        type="number"
        placeholder={spec.placeholder}
        value={value === null || value === undefined ? "" : String(value)}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === "") return onChange(null);
          const parsed = spec.type === "integer" ? parseInt(raw, 10) : Number(raw);
          onChange(Number.isNaN(parsed) ? null : parsed);
        }}
      />
    );
  }

  if (spec.type === "string") {
    const current = String(value ?? "");
    const drop = exprDropHandlers(current, onChange);
    if (spec.multiline) {
      return (
        <div className="textarea-wrap">
          <HighlightedTextarea
            className="field-code"
            rows={7}
            placeholder={spec.placeholder}
            value={current}
            onChange={(v) => onChange(v)}
            onDrop={drop.onDrop}
            onDragOver={drop.onDragOver}
          />
          <button
            type="button"
            className="textarea-expand"
            title="Expand editor with live result preview"
            onClick={() => setExpanderOpen(true)}
          >
            ⤢
          </button>
          {expanderOpen && (
            <ExpressionEditorModal
              label={spec.name}
              value={current}
              onChange={onChange}
              ctx={exprContext}
              onClose={() => setExpanderOpen(false)}
            />
          )}
        </div>
      );
    }
    const isExpr = /\{\{[\s\S]+?\}\}/.test(current);
    const toggleFx = () => {
      if (isExpr) {
        // Strip the outermost {{ }} pair only — keeps inner braces intact.
        const stripped = current.replace(/^\s*\{\{\s*([\s\S]*?)\s*\}\}\s*$/, "$1");
        onChange(stripped === current ? "" : stripped);
      } else {
        onChange(`{{ ${current} }}`);
      }
    };
    const isCredField = spec.credential != null;
    return (
      <div className={`field-wrap${isExpr ? " field-wrap-expr" : ""}`}>
        <input
          className={`field-input${isExpr ? " field-input-expr" : ""}`}
          type="text"
          placeholder={spec.placeholder}
          value={current}
          onChange={(e) => onChange(e.target.value)}
          {...drop}
        />
        <button
          type="button"
          className={`fx-toggle${isExpr ? " fx-toggle-on" : ""}`}
          onClick={toggleFx}
          title={isExpr ? "Switch to fixed value" : "Switch to expression"}
          tabIndex={-1}
        >
          ƒx
        </button>
        {!isCredField && (
          <div className="expr-tokens-hint">
            <span className="expr-tokens-label">tokens:</span>
            {["$json.field", '$node["id"].main', "$env.KEY", "$run.id"].map((token) => (
              <code
                key={token}
                className="expr-token"
                title={`Click to insert ${token}`}
                onClick={() => onChange(`{{ ${token} }}`)}
              >
                {token}
              </code>
            ))}
          </div>
        )}
      </div>
    );
  }

  return <JsonField value={value} onChange={onChange} />;
}

// ---- Webhook trigger: conditional param display ----

export const WEBHOOK_AUTH_TYPE_OPTIONS = [
  { value: "none", label: "None" },
  { value: "basic", label: "Basic Auth" },
  { value: "header", label: "Header Auth" },
  { value: "query", label: "Query Auth" },
];

const WEBHOOK_CRED_BY_AUTH: Record<
  string,
  { type: string; fields: string[]; label: string }
> = {
  basic: {
    type: "http_basic",
    fields: ["username", "password"],
    label: "Basic Auth",
  },
  header: {
    type: "http_header",
    fields: ["name", "value"],
    label: "Header Auth",
  },
  query: {
    type: "http_query",
    fields: ["name", "value"],
    label: "Query Auth",
  },
};

function webhookAuthLabel(authType: string): string {
  return (
    WEBHOOK_AUTH_TYPE_OPTIONS.find((o) => o.value === authType)?.label ?? "None"
  );
}

// Build a synthetic ParamSpec for the auth_credentials picker whose
// credential type/fields swap based on the chosen auth_type.
export function webhookCredentialSpec(
  baseSpec: ParamSpec,
  authType: string,
): ParamSpec {
  const cfg = WEBHOOK_CRED_BY_AUTH[authType];
  if (!cfg) return baseSpec;
  return {
    ...baseSpec,
    credential: {
      type: cfg.type,
      key: "*",
      label: cfg.label,
      fields: cfg.fields,
      multi: true,
    },
  };
}

const WEBHOOK_LABEL_OVERRIDES: Record<string, string> = {
  auth_type: "Authentication",
  hmac_verification: "HMAC verification",
  ip_allowlist: "IP allowlist",
  trust_proxy: "Trust X-Forwarded-For",
  dedup: "Deduplicate",
  dedup_key: "Dedup key",
  raw_body: "Capture raw body",
  response_data: "Response data",
};

export function webhookParamLabel(
  manifestId: string,
  paramName: string,
  params: Record<string, unknown>,
): string | null {
  if (manifestId !== "webhook_trigger") return null;
  if (paramName === "auth_credentials") {
    const authType = String(params.auth_type || "none").toLowerCase();
    const label = webhookAuthLabel(authType);
    return `Credential for ${label}`;
  }
  return WEBHOOK_LABEL_OVERRIDES[paramName] ?? null;
}

// Generic optional-parameter grouping. A param's manifest `group` marks it as
// an optional "Add option" field the inspector tucks behind a chip; params with
// no group are core and always shown. Works for every node — webhook is just
// the first heavy adopter.
export function paramGroup(spec: ParamSpec): string | null {
  return spec.group ? String(spec.group) : null;
}

// A group should auto-expand (rather than show as a chip) when a saved workflow
// already holds a non-default value for any param in it — so existing configs
// never hide their settings.
export function groupActiveByValue(
  specs: ParamSpec[],
  params: Record<string, unknown>,
): boolean {
  return specs.some((spec) => {
    const value = params[spec.name];
    if (value === undefined || value === null || value === "") return false;
    return value !== spec.default;
  });
}

export function webhookHiddenParam(
  manifestId: string,
  paramName: string,
  params: Record<string, unknown>,
): boolean {
  if (manifestId !== "webhook_trigger") return false;
  // Legacy fields that exist on old graphs but are no longer surfaced.
  if (
    paramName === "auth_username" ||
    paramName === "auth_password" ||
    paramName === "auth_header_name" ||
    paramName === "auth_header_value" ||
    paramName === "auth_query_name" ||
    paramName === "auth_query_value"
  ) {
    return true;
  }
  if (paramName === "auth_credentials") {
    const authType = String(params.auth_type || "none").toLowerCase();
    return authType === "none";
  }
  // JWT header only applies to auth_type=jwt.
  if (paramName === "auth_jwt_header") {
    return String(params.auth_type || "none").toLowerCase() !== "jwt";
  }
  // HMAC signature fields appear only when verification is on.
  if (
    paramName === "hmac_header" ||
    paramName === "hmac_algorithm" ||
    paramName === "hmac_prefix"
  ) {
    return String(params.hmac_verification || "off").toLowerCase() !== "on";
  }
  // Trusting X-Forwarded-For only matters when an allowlist is configured.
  if (paramName === "trust_proxy") {
    return String(params.ip_allowlist || "").trim() === "";
  }
  // The dedup key only applies when dedup is on.
  if (paramName === "dedup_key") {
    return String(params.dedup || "off").toLowerCase() !== "on";
  }
  // Response shaping is an On Received concern; the custom body/headers
  // reveal only when response_data=Custom.
  if (
    paramName === "response_data" ||
    paramName === "response_body" ||
    paramName === "response_headers"
  ) {
    const mode = String(params.response_mode || "On Received");
    if (mode !== "On Received") return true;
    if (paramName === "response_body" || paramName === "response_headers") {
      return String(params.response_data || "") !== "Custom";
    }
    return false;
  }
  return false;
}

function UrlRow({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="webhook-url">
      <code>{url}</code>
      <button
        className="btn btn-ghost btn-sm"
        onClick={() => {
          void navigator.clipboard.writeText(url);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

export function ChatTriggerPanel({
  params,
  onParamChange,
}: {
  params: Record<string, unknown>;
  onParamChange?: (key: string, value: unknown) => void;
}) {
  const origin = window.location.origin;
  const workflowId = useEditor((s) => s.workflowId);
  if (!workflowId) return null;

  const isPublic = Boolean(params.public_access);
  const requireLogin = params.require_login !== false; // default true
  const chatToken = typeof params.chat_token === "string" ? params.chat_token : "";

  const baseUrl = `${origin}/chat/${workflowId}`;
  const secretUrl = chatToken ? `${baseUrl}?token=${encodeURIComponent(chatToken)}` : "";

  function generateToken(): void {
    onParamChange?.("chat_token", crypto.randomUUID());
  }

  if (!isPublic) {
    return (
      <div className="inspector-section chat-trigger-panel">
        <div className="inspector-section-head">Chat page</div>
        <p className="field-desc">
          Enable <strong>Public access</strong> in the Options above to get a
          shareable chat URL.
        </p>
      </div>
    );
  }

  return (
    <div className="inspector-section chat-trigger-panel">
      <div className="inspector-section-head">Chat page</div>
      {requireLogin ? (
        <>
          <p className="field-desc">
            <strong>Login required.</strong> Only users signed into this Noodle
            instance can use this chat page.
          </p>
          <UrlRow url={baseUrl} />
        </>
      ) : secretUrl ? (
        <>
          <p className="field-desc">
            <strong>Secret link.</strong> Anyone with this URL can chat — keep it
            private. Regenerate to invalidate old links.
          </p>
          <UrlRow url={secretUrl} />
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            style={{ marginTop: 4 }}
            onClick={generateToken}
          >
            Regenerate link
          </button>
        </>
      ) : (
        <>
          <p className="field-desc">
            <strong>Secret link mode.</strong> Generate a secret URL to share
            with trusted users — no Noodle account required.
          </p>
          <button
            type="button"
            className="btn btn-sm"
            onClick={generateToken}
          >
            Generate secret link
          </button>
        </>
      )}
    </div>
  );
}

export function WebhookPanel({
  path,
  nodeId,
}: {
  path: string;
  nodeId?: string;
}) {
  const slug = path.trim() || "noodle";
  const origin = window.location.origin;
  const [received, setReceived] = useState(false);
  const [listening, setListening] = useState(false);
  const [error, setError] = useState("");
  const timerRef = useRef<number | null>(null);
  const setNodeOutput = useEditor((s) => s.setNodeOutput);

  function stop(): void {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    setListening(false);
  }

  useEffect(() => stop, []);
  useEffect(() => {
    // Stop listening when the user switches to a different webhook node.
    return stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  async function listen(): Promise<void> {
    if (listening) return;
    setError("");
    setReceived(false);
    if (nodeId) setNodeOutput(nodeId, undefined);
    try {
      await api.clearWebhook(slug);
    } catch (err) {
      setError(String(err));
      return;
    }
    setListening(true);
    timerRef.current = window.setInterval(async () => {
      try {
        const data = await api.lastWebhook(slug);
        if (data !== null && data !== undefined) {
          setReceived(true);
          if (nodeId) setNodeOutput(nodeId, { main: data }, "success");
          stop();
        }
      } catch (err) {
        setError(String(err));
        stop();
      }
    }, 1300);
  }

  return (
    <div className="inspector-section webhook-panel">
      <div className="inspector-section-head">Webhook URLs</div>
      <p className="field-desc">Test URL — captures requests while you build.</p>
      <UrlRow url={`${origin}/api/webhook-test/${slug}`} />
      <p className="field-desc">
        Production URL — runs this workflow when it is active.
      </p>
      <UrlRow url={`${origin}/api/webhook/${slug}`} />

      <div className="webhook-listen">
        {!listening ? (
          <button className="btn btn-sm" onClick={() => void listen()}>
            ▶ Listen for test event
          </button>
        ) : (
          <div className="webhook-listening">
            <div className="webhook-waves" aria-hidden="true">
              <span className="webhook-wave" />
              <span className="webhook-wave" />
              <span className="webhook-wave" />
              <span className="webhook-wave-dot" />
            </div>
            <p className="webhook-listening-label">
              Listening for a test event…
            </p>
            <button className="btn btn-sm" onClick={stop}>
              Stop
            </button>
          </div>
        )}
        {received && (
          <span className="muted">Request captured — see Output panel.</span>
        )}
      </div>

      {error && <p className="error-text">{error}</p>}
    </div>
  );
}

function pickModuleManifest(
  manifests: NodeManifest[],
  moduleId: string,
  funcName: string,
): NodeManifest | null {
  const prefix = `user:${moduleId}:`;
  const candidates = manifests.filter((m) => m.id.startsWith(prefix));
  return (
    candidates.find((m) => m.id === `${prefix}${funcName}`) ??
    candidates[0] ??
    null
  );
}

/** Drop any leading decorator lines (e.g. ``@node(...)``) so the function can
 *  be registered as a plain code-module node. ``inspect.getsource`` returns
 *  only the function, so everything before the first top-level ``def`` /
 *  ``async def`` is decorator/blank lines and is safe to remove. */
function stripLeadingDecorators(src: string): string {
  const lines = src.split("\n");
  const idx = lines.findIndex((l) => /^(async\s+)?def\s/.test(l));
  return idx > 0 ? lines.slice(idx).join("\n") : src;
}

/** Full-screen code editor for a node's Python source. The left pane shows the
 *  last upstream input (when available) so columns/fields can be dragged into
 *  the code as ``{{ $json.field }}`` expressions; the middle pane is the editor;
 *  the right pane shows the code with every ``{{ }}`` expression resolved
 *  against the input (Text / HTML / JSON), mirroring the code node editor. */
function CodeEditorModal({
  label,
  editable,
  draft,
  onChange,
  inputData,
  saving,
  onSaveReplace,
  onSaveCreate,
  defaultName,
  onClose,
}: {
  label: string;
  editable: boolean;
  draft: string;
  onChange: (v: string) => void;
  inputData?: Record<string, unknown>;
  saving: boolean;
  onSaveReplace: () => void;
  onSaveCreate: (name: string) => void;
  defaultName: string;
  onClose: () => void;
}) {
  const [name, setName] = useState(defaultName);
  const [view, setView] = useState<"text" | "html" | "json">("text");
  const drop = exprDropHandlers(draft, onChange);
  const hasInput = inputData !== undefined && Object.keys(inputData).length > 0;

  // The first upstream input becomes ``$json``; the whole map is ``$input``.
  const firstInput = inputData ? Object.values(inputData)[0] : undefined;
  const ctx: ExprContext = { json: firstInput, inputs: inputData };

  const [state, setState] = useState<{
    result?: unknown;
    error?: string | null;
    parts?: PreviewPart[];
    loading: boolean;
  }>({ loading: false });

  const hasExpr = EXPR_RE.test(draft);

  useEffect(() => {
    if (!hasExpr) {
      // No expressions → the "resolved" code is just the code itself.
      setState({
        result: draft,
        error: null,
        parts: draft ? [{ kind: "text", value: draft }] : [],
        loading: false,
      });
      return;
    }
    if (!hasInput) {
      setState({ result: undefined, error: null, parts: [], loading: false });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, loading: true }));
    const handle = setTimeout(() => {
      api
        .previewExpression({
          value: draft,
          json: ctx.json,
          inputs: ctx.inputs,
        })
        .then((res) => {
          if (!cancelled)
            setState({
              ...res,
              parts: res.parts as PreviewPart[],
              loading: false,
            });
        })
        .catch((err) => {
          if (!cancelled)
            setState({
              error: String(err),
              result: undefined,
              parts: [],
              loading: false,
            });
        });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, hasExpr, hasInput, JSON.stringify(inputData)]);

  const resultText = formatResultText(state.result);
  const parts = state.parts ?? [];

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal modal-wide code-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <h2>
            Editing code <span className="expr-modal-label">{label}</span>
          </h2>
          <button className="btn btn-sm btn-ghost" onClick={onClose}>
            ✕
          </button>
        </header>
        <div className="code-modal-body">
          <section className="code-modal-input">
            <div className="expr-modal-pane-head">
              <span>Input</span>
              <span className="muted expr-modal-hint">drag fields into the code</span>
            </div>
            <div className="code-modal-input-data">
              {hasInput ? (
                <DataPanel
                  title="Input"
                  data={inputData}
                  emptyMessage="No upstream data yet."
                  dragPrefix="$json"
                />
              ) : (
                <p className="muted" style={{ padding: 12 }}>
                  Run the workflow once to see the last node input here for
                  drag-and-drop.
                </p>
              )}
            </div>
          </section>
          <section className="code-modal-editor-pane">
            <div className="expr-modal-pane-head">
              <span>Python</span>
              <span className="muted expr-modal-hint">
                the <code>@node(...)</code> decorator is stripped on save
              </span>
            </div>
            <textarea
              className="field-input field-code code-modal-editor"
              value={draft}
              spellCheck={false}
              onChange={(e) => onChange(e.target.value)}
              onDrop={drop.onDrop}
              onDragOver={drop.onDragOver}
            />
            <div className="code-modal-actions">
              <input
                className="field-input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Node name"
              />
              {editable && (
                <button
                  className="btn btn-sm btn-primary"
                  disabled={saving}
                  onClick={onSaveReplace}
                >
                  Replace this node
                </button>
              )}
              <button
                className="btn btn-sm"
                disabled={saving}
                onClick={() => onSaveCreate(name)}
              >
                Create new node
              </button>
            </div>
          </section>
          <section className="code-modal-result-pane">
            <div className="expr-modal-pane-head">
              <span>Result</span>
              <div className="expr-modal-tabs">
                <button
                  type="button"
                  className={view === "text" ? "active" : ""}
                  onClick={() => setView("text")}
                >
                  Text
                </button>
                <button
                  type="button"
                  className={view === "html" ? "active" : ""}
                  onClick={() => setView("html")}
                >
                  HTML
                </button>
                <button
                  type="button"
                  className={view === "json" ? "active" : ""}
                  onClick={() => setView("json")}
                >
                  JSON
                </button>
              </div>
            </div>
            <div className="code-modal-result">
              {state.loading && <p className="muted">Evaluating…</p>}
              {!state.loading && hasExpr && !hasInput && (
                <p className="muted">
                  Run the workflow once to feed this preview with real input
                  data — drag a column in and the resolved value shows here.
                </p>
              )}
              {!state.loading && state.error && (
                <p className="expr-preview-error">⚠ {state.error}</p>
              )}
              {!state.loading &&
                !state.error &&
                state.result !== undefined &&
                (view === "html" ? (
                  <iframe
                    title="HTML preview"
                    sandbox=""
                    srcDoc={resultText}
                    className="expr-modal-iframe"
                  />
                ) : view === "json" ? (
                  <pre className="expr-modal-text">
                    {(() => {
                      try {
                        return JSON.stringify(
                          typeof state.result === "string"
                            ? JSON.parse(state.result)
                            : state.result,
                          null,
                          2,
                        );
                      } catch {
                        return resultText;
                      }
                    })()}
                  </pre>
                ) : (
                  <pre className="expr-modal-text code-modal-result-text">
                    {parts.length === 0
                      ? resultText
                      : parts.map((part, i) => {
                          if (part.kind === "text") {
                            return <span key={i}>{part.value}</span>;
                          }
                          if (part.kind === "error") {
                            return (
                              <span
                                key={i}
                                className="expr-part-error"
                                title={part.error}
                              >
                                {part.raw}
                              </span>
                            );
                          }
                          return (
                            <span key={i} className="expr-part-resolved">
                              {formatResultText(part.value)}
                            </span>
                          );
                        })}
                  </pre>
                ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

export function NodeCodePanel({
  nodeId,
  manifest,
  inputData,
  onClose,
}: {
  nodeId: string;
  manifest: NodeManifest;
  inputData?: Record<string, unknown>;
  onClose: () => void;
}) {
  const toast = useToast();
  const workflowId = useEditor((s) => s.workflowId);
  const setManifests = useEditor((s) => s.setManifests);
  const replaceNodeManifest = useEditor((s) => s.replaceNodeManifest);

  const [info, setInfo] = useState<NodeSource | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [askSave, setAskSave] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [newName, setNewName] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .nodeSource(manifest.id)
      .then((res) => {
        if (cancelled) return;
        setInfo(res);
        // Show the real node source (decorators included) for transparency.
        // Decorators are stripped automatically at save time.
        setDraft(res.source);
        setNewName(`${manifest.name} (custom)`);
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [manifest.id, manifest.name]);

  async function refreshManifests(): Promise<NodeManifest[]> {
    const [builtins, custom] = await Promise.all([
      api.nodes(),
      workflowId
        ? api.workflowCustomNodeManifests(workflowId)
        : Promise.resolve([] as NodeManifest[]),
    ]);
    const merged = [...builtins, ...custom];
    setManifests(merged);
    return merged;
  }

  async function saveAs(mode: "replace" | "create", nameOverride?: string): Promise<void> {
    if (!info || !workflowId) {
      toast.notify("Open a saved workflow first.", "error");
      return;
    }
    setSaving(true);
    try {
      const name = (nameOverride ?? newName).trim() || `${manifest.name} (custom)`;
      // Code modules must be plain functions — strip any @node decorator the
      // user is viewing/editing before persisting.
      const contents = stripLeadingDecorators(draft);
      let moduleId: string;
      if (mode === "replace" && info.editable && info.module_id) {
        // Editable custom node → update its module in place.
        await api.updateCodeModule(info.module_id, { contents });
        moduleId = info.module_id;
      } else {
        // Built-in (or "create") → a brand-new workflow-scoped custom node.
        const created = await api.createCodeModule({
          scope: "workflow",
          workflow_id: workflowId,
          name,
          contents,
        });
        moduleId = created.id;
      }
      const merged = await refreshManifests();
      if (mode === "replace") {
        const next = pickModuleManifest(merged, moduleId, info.func_name);
        if (next) {
          replaceNodeManifest(nodeId, next);
          toast.notify("This node now runs your code.", "success");
        } else {
          toast.notify(
            "Saved, but no node was registered — check for syntax errors.",
            "error",
          );
        }
      } else {
        toast.notify("New node created — find it in the palette.", "success");
      }
      setAskSave(false);
      onClose();
    } catch (err) {
      toast.notify(`Save failed: ${err}`, "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="inspector-section node-code-section">
      <div className="inspector-section-head">
        Node code
        <button className="btn btn-sm btn-ghost" onClick={onClose}>
          Hide
        </button>
      </div>
      {loading && <p className="muted">Loading source…</p>}
      {error && <p className="expr-preview-error">⚠ {error}</p>}
      {info && (
        <>
          <p className="field-desc">
            {info.editable
              ? "This is a custom node. Edit the Python and save to update it."
              : "Built-in node source. Edit it to create your own customizable copy."}
          </p>
          <div className="node-code-editor-wrap">
            <textarea
              className="field-input field-code node-code-editor"
              value={draft}
              spellCheck={false}
              rows={18}
              onChange={(e) => setDraft(e.target.value)}
              {...exprDropHandlers(draft, setDraft)}
            />
            <button
              type="button"
              className="node-code-expand"
              title="Open full editor with input data"
              onClick={() => setExpanded(true)}
            >
              ✎
            </button>
          </div>
          {!info.editable && (
            <p className="field-desc muted">
              Note: the <code>@node(...)</code> decorator is stripped
              automatically on save, and private helpers used by built-ins
              aren't included — you may need to add them for the node to run.
            </p>
          )}
          {!askSave ? (
            <button
              className="btn btn-sm"
              style={{ marginTop: 8 }}
              disabled={saving}
              onClick={() => setAskSave(true)}
            >
              Save…
            </button>
          ) : (
            <div className="node-code-save">
              <div className="field-label">
                <span className="field-name">Node name</span>
              </div>
              <input
                className="field-input"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
              />
              <p className="field-desc">How would you like to save?</p>
              <div className="node-code-save-actions">
                <button
                  className="btn btn-sm btn-primary"
                  disabled={saving}
                  onClick={() => void saveAs("replace")}
                >
                  Replace this node
                </button>
                <button
                  className="btn btn-sm"
                  disabled={saving}
                  onClick={() => void saveAs("create")}
                >
                  Create new node
                </button>
                <button
                  className="btn btn-sm btn-ghost"
                  disabled={saving}
                  onClick={() => setAskSave(false)}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </>
      )}
      {expanded && info && (
        <CodeEditorModal
          label={manifest.name}
          editable={info.editable}
          draft={draft}
          onChange={setDraft}
          inputData={inputData}
          saving={saving}
          defaultName={newName || `${manifest.name} (custom)`}
          onSaveReplace={() => void saveAs("replace")}
          onSaveCreate={(name) => void saveAs("create", name)}
          onClose={() => setExpanded(false)}
        />
      )}
    </div>
  );
}

/**
 * "Use as tool" section: an animated on/off toggle that turns a normal node
 * into a tool an AI Agent can call. When on, the node's main input/output is
 * replaced by a single `tool` port (see NodeCard) and each parameter gains a
 * Fixed/From-AI control. Rendered identically in the right inspector and the
 * NDV modal so the two never drift.
 */
export function ToolModeSection({ nodeId }: { nodeId: string }) {
  const node = useEditor((s) => s.nodes.find((n) => n.id === nodeId));
  const updateNodeSettings = useEditor((s) => s.updateNodeSettings);
  if (!node || !node.data.manifest.usable_as_tool) return null;
  const { manifest } = node.data;
  const on = Boolean(node.data.toolMode);
  return (
    <div className="inspector-section tool-mode-section">
      <label className="field-toggle tool-mode-toggle">
        <input
          type="checkbox"
          checked={on}
          onChange={(e) => updateNodeSettings(nodeId, { toolMode: e.target.checked })}
        />
        <span className="field-toggle-track" />
        <span className="field-toggle-label">Use as tool</span>
      </label>
      <p className="field-desc">
        Expose this node as a tool an AI Agent can call. Its{" "}
        <strong>tool</strong> output replaces the normal input/output — the
        Agent decides when to invoke it and supplies any "From AI" arguments.
      </p>
      {on && (
        <>
          <div className="field">
            <div className="field-label">
              <span className="field-name">Tool name</span>
            </div>
            <input
              className="field-input"
              value={node.data.toolName ?? ""}
              placeholder={manifest.id}
              onChange={(e) => updateNodeSettings(nodeId, { toolName: e.target.value })}
            />
          </div>
          <div className="field">
            <div className="field-label">
              <span className="field-name">Tool description</span>
            </div>
            <textarea
              className="field-input"
              rows={2}
              value={node.data.toolDescription ?? ""}
              placeholder={manifest.description || manifest.name}
              onChange={(e) =>
                updateNodeSettings(nodeId, { toolDescription: e.target.value })
              }
            />
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Per-parameter Fixed/From-AI segmented control, shown only in tool mode for
 * non-credential params. "From AI" stores a `$fromAI(...)` expression the
 * engine resolves at tool-call time. Returns null when not applicable.
 */
export function FromAiParamControl({
  nodeId,
  spec,
  value,
  onSetParam,
}: {
  nodeId: string;
  spec: ParamSpec;
  value: unknown;
  onSetParam: (name: string, value: unknown) => void;
}) {
  const node = useEditor((s) => s.nodes.find((n) => n.id === nodeId));
  if (!node?.data.toolMode || spec.type === "credential") return null;
  const fromAi = isFromAiExpr(value);
  return (
    <div className="from-ai-toggle" role="group" aria-label="Parameter source">
      <button
        type="button"
        className={fromAi ? "" : "active"}
        onClick={() => onSetParam(spec.name, spec.default ?? "")}
      >
        Fixed
      </button>
      <button
        type="button"
        className={fromAi ? "active" : ""}
        onClick={() =>
          onSetParam(
            spec.name,
            fromAiExpr(spec.name, spec.description || "", paramArgType(spec.type)),
          )
        }
      >
        From AI
      </button>
    </div>
  );
}

export function NodeDetails({
  nodeId,
  showHeader = true,
}: {
  nodeId: string;
  showHeader?: boolean;
}) {
  const node = useEditor((s) => s.nodes.find((n) => n.id === nodeId));
  const updateParams = useEditor((s) => s.updateParams);
  const runStatus = useEditor((s) => s.runStatus[nodeId]);
  const runOutput = useEditor((s) => s.runOutputs[nodeId]);
  const runMeta = useEditor((s) => s.runMeta[nodeId]);
  const runOutputs = useEditor((s) => s.runOutputs);
  const edges = useEditor((s) => s.edges);
  const workflowId = useEditor((s) => s.workflowId);
  const pinned = useEditor((s) => s.pinned[nodeId]);
  const setPinnedFor = useEditor((s) => s.setPinnedFor);
  const envId = useEditor((s) => s.envId);
  const envName = useEditor((s) => s.envName);
  const envPackages = useEditor((s) => s.envPackages);
  const environmentsList = useEditor((s) => s.environmentsList);
  const applyEnvSwitch = useEditor((s) => s.applyEnvSwitch);
  const setEnvPackages = useEditor((s) => s.setEnvPackages);
  const platform = useServerPlatform();
  const activeEnv = environmentsList.find((e) => e.id === envId);

  const [mode, setMode] = useState<"inspector" | "python">("inspector");
  const [pkgBusy, setPkgBusy] = useState(false);
  const { notify } = useToast();
  useEffect(() => setMode("inspector"), [nodeId]);

  async function pin(): Promise<void> {
    if (!workflowId || runOutput === undefined) return;
    try {
      await api.pinNode(workflowId, nodeId, runOutput);
      setPinnedFor(nodeId, runOutput);
    } catch {
      /* ignore */
    }
  }

  async function unpin(): Promise<void> {
    if (!workflowId) return;
    try {
      await api.unpinNode(workflowId, nodeId);
      setPinnedFor(nodeId, null);
    } catch {
      /* ignore */
    }
  }

  if (!node) {
    return (
      <div className="inspector-empty">
        <p>This node is no longer in the workflow.</p>
      </div>
    );
  }

  const { manifest, params, disabled } = node.data;
  const hasBrandIcon = isBrandIconName(manifest.icon);
  const color = categoryColor(manifest.category);

  const setParam = (name: string, value: unknown) => {
    updateParams(node.id, { ...params, [name]: value });
  };

  // Packages this node needs that the workflow's env doesn't have.
  const missingPkgs = missingFor(manifest.requirements ?? [], envPackages, platform ?? undefined);
  const satisfyingEnvs = environmentsList.filter(
    (e) => e.id !== envId && missingFor(missingPkgs, e.packages, platform ?? undefined).length === 0,
  );

  async function addMissingToEnv(): Promise<void> {
    if (!envId || pkgBusy) return;
    setPkgBusy(true);
    try {
      const updated = [...envPackages, ...missingPkgs];
      await api.setPackages(envId, updated);
      setEnvPackages(updated);
      notify(`Added ${missingPkgs.join(", ")} to ${envName ?? "environment"}.`, "success");
    } catch {
      notify("Failed to install packages — check the environment.", "error");
    } finally {
      setPkgBusy(false);
    }
  }

  // Compute the data flowing into this node from upstream node outputs.
  const incomingInputs: Record<string, unknown> = {};
  for (const edge of edges) {
    if (edge.target !== node.id) continue;
    const upstream = runOutputs[edge.source];
    if (!upstream || typeof upstream !== "object") continue;
    const sourceHandle = edge.sourceHandle ?? "main";
    const value = (upstream as Record<string, unknown>)[sourceHandle];
    if (value !== undefined) {
      incomingInputs[edge.targetHandle ?? "input"] = value;
    }
  }
  const hasIncomingInputs = Object.keys(incomingInputs).length > 0;

  return (
    <>
      {showHeader && (
        <div className="inspector-node">
          <div className="inspector-node-head">
            <span
              className={`inspector-glyph${hasBrandIcon ? " has-brand-icon" : ""}`}
              style={{ color }}
            >
              <NodeIcon name={manifest.icon} size={hasBrandIcon ? 30 : 20} />
            </span>
            <span className="inspector-node-name">{manifest.name}</span>
            {disabled && <span className="cred-type">disabled</span>}
          </div>
          <div className="inspector-meta">
            <span className="mono-tag" style={{ color }}>
              {manifest.category}
            </span>
            <span className="mono-tag">{manifest.id}</span>
          </div>
          {manifest.description && (
            <p className="inspector-desc">{manifest.description}</p>
          )}
          <div
            className="ndv-mode-toggle"
            role="tablist"
            aria-label="Inspector or Python"
          >
            <button
              type="button"
              role="tab"
              aria-selected={mode === "inspector"}
              className={mode === "inspector" ? "active" : ""}
              onClick={() => setMode("inspector")}
            >
              Inspector
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "python"}
              className={mode === "python" ? "active" : ""}
              onClick={() => setMode("python")}
            >
              Python
            </button>
          </div>
        </div>
      )}

      {missingPkgs.length > 0 && envId && (
        <div className="ndv-missing-pkgs warn-text">
          <p>
            This node needs <strong>{missingPkgs.join(", ")}</strong>, not
            installed in <strong>{envName ?? "this environment"}</strong>.
          </p>
          <div className="ndv-missing-actions">
            <button
              type="button"
              className="btn btn-sm btn-primary"
              disabled={pkgBusy}
              onClick={() => void addMissingToEnv()}
            >
              {pkgBusy ? "Adding…" : `Add to ${envName ?? "env"}`}
            </button>
            {satisfyingEnvs.length > 0 && applyEnvSwitch && (
              <select
                className="field-input"
                value=""
                onChange={(e) =>
                  e.target.value && applyEnvSwitch(e.target.value)
                }
              >
                <option value="">Switch environment…</option>
                {satisfyingEnvs.map((env) => (
                  <option key={env.id} value={env.id}>
                    {env.name}
                  </option>
                ))}
              </select>
            )}
          </div>
        </div>
      )}

      {manifest.system_requirements && manifest.system_requirements.length > 0 && (
        <div className="node-details-section ndv-sysreq">
          <div className="node-details-section-title inspector-section-head">System dependencies</div>
          {manifest.system_requirements.map((sr) => (
            <div key={sr.name} className="sysreq-item">
              <div className="sysreq-name">{sr.name}</div>
              {activeEnv?.backend === "docker" ? (
                sr.dockerfile_hint ? (
                  <div className="sysreq-hint">
                    <span className="sysreq-label">Add to Dockerfile:</span>
                    <code className="sysreq-code">{sr.dockerfile_hint}</code>
                  </div>
                ) : (
                  <div className="sysreq-hint">Must be included in your Docker image.</div>
                )
              ) : (
                <div className="sysreq-hint">
                  <span className="sysreq-label">Must be installed on the server.</span>
                  {sr.apt && <div><strong>Linux:</strong> <code>apt install {sr.apt}</code></div>}
                  {sr.brew && <div><strong>macOS:</strong> <code>brew install {sr.brew}</code></div>}
                  {sr.windows && (
                    <div>
                      <strong>Windows:</strong>{" "}
                      {sr.windows.startsWith("http") ? (
                        <a href={sr.windows} target="_blank" rel="noreferrer">{sr.windows}</a>
                      ) : (
                        <span>{sr.windows}</span>
                      )}
                    </div>
                  )}
                  {sr.note && <div className="sysreq-note">{sr.note}</div>}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {mode === "python" ? (
        <NodeCodePanel
          nodeId={node.id}
          manifest={manifest}
          inputData={hasIncomingInputs ? incomingInputs : undefined}
          onClose={() => setMode("inspector")}
        />
      ) : (
        <>

      <ToolModeSection nodeId={node.id} />

      <div className="inspector-section">
        <div className="inspector-section-head">Parameters</div>
        <p className="field-desc expr-hint">
          Use <code>{"{{ $json.field }}"}</code> or{" "}
          <code>{'{{ $node["nodeId"].main.field }}'}</code> in string fields to
          reference upstream data.
        </p>
        {manifest.params.length === 0 && (
          <p className="muted">This node has no parameters.</p>
        )}
        {manifest.params
          .filter((spec) => spec.widget !== "hidden")
          .filter((spec) => !webhookHiddenParam(manifest.id, spec.name, params))
          .map((spec) => {
            const value = params[spec.name];
            const fx = typeof value === "string" && /\{\{.+?\}\}/s.test(value);
            const displayLabel =
              webhookParamLabel(manifest.id, spec.name, params) ??
              (spec.display_name || formatParamLabel(spec.name));

            // Webhook auth_type renders as a friendly-labelled dropdown,
            // and auth_credentials uses a synthetic credential spec whose
            // type matches the chosen auth method.
            const isWebhookAuthType =
              manifest.id === "webhook_trigger" && spec.name === "auth_type";
            const isWebhookCreds =
              manifest.id === "webhook_trigger" &&
              spec.name === "auth_credentials";

            let renderSpec = spec;
            if (isWebhookCreds) {
              const at = String(params.auth_type || "none").toLowerCase();
              renderSpec = webhookCredentialSpec(spec, at);
            }

            return (
              <div className="field" key={`${node.id}:${spec.name}`}>
                <div className="field-label">
                  <span className="field-name">{displayLabel}</span>
                  {fx && <span className="fx-badge" title="Contains expression">fx</span>}
                  {spec.required && <span className="field-req">required</span>}
                </div>
                <FromAiParamControl
                  nodeId={node.id}
                  spec={renderSpec}
                  value={value}
                  onSetParam={setParam}
                />
                {spec.description && (
                  <p className="field-desc">{spec.description}</p>
                )}
                {isFromAiExpr(value) ? (
                  <p className="from-ai-note">↯ The model supplies this argument.</p>
                ) : isWebhookAuthType ? (
                  <select
                    className="field-input"
                    value={String(value ?? "none")}
                    onChange={(e) => {
                      // Atomic param update: merge BOTH changes into one
                      // updateParams call. Two separate setParam calls use
                      // the same render-time params snapshot, so the second
                      // would overwrite the first.
                      updateParams(node.id, {
                        ...params,
                        [spec.name]: e.target.value,
                        auth_credentials: "",
                      });
                    }}
                  >
                    {WEBHOOK_AUTH_TYPE_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                ) : (
                  <ParamField
                    spec={renderSpec}
                    value={value}
                    onChange={(v) => setParam(spec.name, v)}
                    credentialContext={params}
                  />
                )}
              </div>
            );
          })}
      </div>

      {hasIncomingInputs && (
        <div className="inspector-section input-section">
          <div className="inspector-section-head">Input</div>
          <p className="field-desc">From upstream nodes' most recent run.</p>
          <pre className="run-output">
            {JSON.stringify(incomingInputs, null, 2)}
          </pre>
        </div>
      )}

      {runStatus && (
        <div className="inspector-section run-section">
          <div className="inspector-section-head">
            Last run
            <span className={`run-pill status-run-${runStatus}`}>
              {runStatus}
            </span>
          </div>
          {runMeta?.error && (
            <div className="ndv-node-error" role="alert">
              <span>Node error</span>
              <pre>{runMeta.error}</pre>
            </div>
          )}
          {runOutput !== undefined ? (
            <pre className="run-output">
              {JSON.stringify(runOutput, null, 2)}
            </pre>
          ) : (
            <p className="muted">No output captured.</p>
          )}
          {runOutput !== undefined && !pinned && (
            <button
              className="btn btn-sm"
              style={{ marginTop: 8 }}
              onClick={() => void pin()}
            >
              📌 Pin this output
            </button>
          )}
        </div>
      )}

      {pinned !== undefined && (
        <div className="inspector-section pin-section">
          <div className="inspector-section-head">
            Pinned
            <span className="run-pill status-run-success">pinned</span>
          </div>
          <p className="field-desc">
            Runs use this value instead of executing the node.
          </p>
          <pre className="run-output">{JSON.stringify(pinned, null, 2)}</pre>
          <button
            className="btn btn-sm btn-ghost"
            style={{ marginTop: 8 }}
            onClick={() => void unpin()}
          >
            Unpin
          </button>
        </div>
      )}

      {manifest.id === "webhook_trigger" && (
        <WebhookPanel
          path={String(params.path ?? "noodle")}
          nodeId={node.id}
        />
      )}

      {manifest.id === "chat_trigger" && (
        <ChatTriggerPanel
          params={params}
          onParamChange={(key, value) => setParam(key, value)}
        />
      )}
        </>
      )}
    </>
  );
}
