import { Eye, EyeSlash, Info, MagnifyingGlass, Plus, PushPin, WarningCircle, X } from "@phosphor-icons/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, errorMessage, uploadArtifact } from "../api";
import { formatBytes } from "./artifactValues";
import { categoryColor } from "../categories";
import {
  LLM_PROVIDER_VARIANTS,
  getLlmVariant,
  visibleCredentialFields,
} from "../llmProviders";
import {
  LLM_FIELD_DEFS,
  mergedCredentialPresets,
  type CredentialPreset,
  type CredentialScope,
} from "../credentialPresets";
import { isBrandIconName, NodeIcon } from "../NodeIcon";
import { useCredentialTypes } from "../queries";
import { useModalA11y } from "../useModalA11y";
import { useToast } from "../ToastProvider";
import { safeGetItem, safeSetItem } from "../safeStorage";
import { useTimeout } from "../hooks/useTimeout";
import type {
  Credential,
  LintDiagnostic,
  NodeManifest,
  NodeSource,
  ParamSpec,
} from "../types";
import { DataPanel } from "./DataPanel";
import { ConditionsField } from "./ConditionsField";
import { VariablePickerPopover } from "./VariablePickerPopover";
import { TimezoneSelect } from "./fields/TimezoneSelect";
import { fromAiExpr, isFromAiExpr, paramArgType } from "./toolParam";
import { missingFor } from "./missingPackages";
import { useEditor } from "./store";
import { useServerPlatform } from "../hooks/useServerPlatform";
import { credentialMatchesParam } from "./node-details/credentials";
import { getUpstreamNodes } from "./node-details/upstreamFields";
import {
  computeCodeSuggestions,
  getIdentPrefix,
  tokenizePython,
} from "./node-details/pythonHighlight";
import {
  buildLoadOptionsParams,
  matchesDisplayWhen,
} from "./node-details/displayRules";
import {
  computeSuggestions,
  EXPR_RE,
  EXPR_RE_GLOBAL,
  type ExprContext,
  formatResultText,
  getTokenBeforeCursor,
  type PreviewPart,
  type ResultView,
} from "./node-details/expressions";
import { formatParamLabel, isSecretField } from "./node-details/labels";
import { mergeOptions } from "./node-details/options";
import {
  PackageInstallPanel,
  SystemRequirementsPanel,
} from "./node-details/PackageInstallPanel";
import { ResourceOperationSelector } from "./node-details/ResourceOperationSelector";
import {
  WEBHOOK_AUTH_TYPE_OPTIONS,
  webhookCredentialSpec,
  webhookHiddenParam,
  webhookParamLabel,
} from "./node-details/webhookRules";

export { credentialMatchesParam } from "./node-details/credentials";
export {
  applyResourceOperation,
  buildLoadOptionsParams,
  matchesDisplayWhen,
} from "./node-details/displayRules";
export { computeSuggestions, type ExprContext } from "./node-details/expressions";
export { formatParamLabel } from "./node-details/labels";
export { mergeOptions } from "./node-details/options";
export { ResourceOperationSelector } from "./node-details/ResourceOperationSelector";
export {
  groupActiveByValue,
  paramGroup,
  WEBHOOK_AUTH_TYPE_OPTIONS,
  webhookCredentialSpec,
  webhookHiddenParam,
  webhookParamLabel,
} from "./node-details/webhookRules";

const PACKAGE_INSTALL_TIMEOUT_MS = 10 * 60 * 1000;
const PACKAGE_INSTALL_POLL_MS = 2000;

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

/**
 * Re-seed a field's local editing buffer when the upstream `value` changes
 * *externally* — undo/redo, an applied AI fix, or a pin restore — without
 * clobbering the user's in-progress typing. Fields like `JsonField` /
 * `KeyValueField` / `RoutesField` keep local state (parsed text, row arrays)
 * seeded once at mount; `ParamField` is keyed by `node.id:spec.name` so a
 * node *switch* remounts them, but a value change to the *same* mounted node
 * would otherwise leave the buffer stale (FE-11).
 *
 * `apply` runs only when the new value differs from what this field last saw
 * AND the field isn't currently focused (so a value update caused by the
 * field's own `onChange` while typing is absorbed, never reapplied).
 */
function useExternalValueSync(
  value: unknown,
  isFocused: () => boolean,
  apply: () => void,
): void {
  const serialized = JSON.stringify(value ?? null);
  const lastSeen = useRef(serialized);
  useEffect(() => {
    if (serialized === lastSeen.current) return;
    lastSeen.current = serialized;
    if (isFocused()) return;
    apply();
    // `apply`/`isFocused` are recreated each render; `serialized` is the trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serialized]);
}

function formatPinnedAt(value: string | null): string {
  if (!value) return "time unknown";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

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

function credentialTypeLabel(type: string): string {
  return CRED_TYPE_LABELS[type] ?? formatParamLabel(type);
}

function credentialFieldLabel(field: string): string {
  return CRED_FIELD_LABELS[field] ?? formatParamLabel(field);
}


function credentialMatchesSearch(cred: Credential, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [
    cred.name,
    cred.type,
    credentialTypeLabel(cred.type),
    cred.description,
    cred.scope,
    credentialScopeLabel(cred),
    ...cred.keys,
    ...cred.keys.map(credentialFieldLabel),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase()
    .includes(needle);
}

const CRED_TYPE_COLORS: Record<string, string> = {
  openai: "var(--cred-dot-ai)",
  anthropic: "var(--cred-dot-ai)",
  llm_provider: "var(--cred-dot-ai)",
  cohere: "var(--cred-dot-ai)",
  deepl: "var(--cred-dot-ai)",
  slack_bot: "var(--cred-dot-messaging)",
  discord_webhook: "var(--cred-dot-messaging)",
  smtp: "var(--cred-dot-messaging)",
  postgres: "var(--cred-dot-db)",
  mysql: "var(--cred-dot-db)",
  mongodb: "var(--cred-dot-db)",
  redis: "var(--cred-dot-db)",
  elasticsearch: "var(--cred-dot-db)",
  aws: "var(--cred-dot-cloud)",
  azure_blob: "var(--cred-dot-cloud)",
  github: "var(--cred-dot-dev)",
};

function credTypeDotColor(type: string): string {
  return CRED_TYPE_COLORS[type] ?? "var(--accent)";
}

function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "never used";
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 0) return "just now";
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? "" : "s"} ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} month${months === 1 ? "" : "s"} ago`;
  return `${Math.floor(months / 12)}y ago`;
}

function getDaysUntilExpiry(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - Date.now();
  return Math.ceil(ms / 86400000);
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
  const taRef = useRef<HTMLTextAreaElement>(null);
  useExternalValueSync(
    value,
    () => document.activeElement === taRef.current,
    () => {
      setText(value === null || value === undefined ? "" : JSON.stringify(value, null, 2));
      setInvalid(false);
    },
  );

  return (
    <textarea
      ref={taRef}
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
  const wrapRef = useRef<HTMLDivElement>(null);
  // Resync from an external value change (undo/redo, AI fix) — FE-11.
  useExternalValueSync(
    value,
    () => Boolean(wrapRef.current?.contains(document.activeElement)),
    () => {
      setRows(rowsFromValue(value));
      setJsonText(value === null || value === undefined ? "" : JSON.stringify(value, null, 2));
      setInvalid(false);
    },
  );

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
    <div className="kv-field" ref={wrapRef}>
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

function CredentialModalField({
  field,
  value,
  onChange,
  error,
}: {
  field: import("../credentialPresets").CredentialFormField;
  value: string;
  onChange: (v: string) => void;
  error?: string;
}) {
  const [showPassword, setShowPassword] = useState(false);
  const isPassword = field.kind === "password";
  const hasError = Boolean(error);

  if (field.kind === "select") {
    return (
      <div className="cred-field">
        <div className="cred-field-label">
          <span className={`cred-field-label-text${hasError ? " has-error" : ""}`}>{field.label}</span>
          {field.required && <span className="cred-field-required">required</span>}
        </div>
        <div className="cred-field-input-wrap">
          <select
            className="cred-field-input cred-field-select"
            value={value}
            onChange={(e) => onChange(e.target.value)}
          >
            {(field.options ?? []).map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
          <span className="cred-field-select-caret" aria-hidden="true">▾</span>
        </div>
        {field.help && <span className="cred-field-help">{field.help}</span>}
      </div>
    );
  }

  if (field.kind === "textarea") {
    return (
      <div className="cred-field">
        <div className="cred-field-label">
          <span className={`cred-field-label-text${hasError ? " has-error" : ""}`}>{field.label}</span>
          {field.required && <span className="cred-field-required">required</span>}
        </div>
        <textarea
          className={`cred-field-input cred-field-textarea${hasError ? " has-error" : ""}`}
          placeholder={field.placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        {error && <span className="cred-field-error">{error}</span>}
        {field.help && <span className="cred-field-help">{field.help}</span>}
      </div>
    );
  }

  return (
    <div className="cred-field">
      <div className="cred-field-label">
        <span className={`cred-field-label-text${hasError ? " has-error" : ""}`}>{field.label}</span>
        {field.required && <span className="cred-field-required">required</span>}
      </div>
      <div className="cred-field-input-wrap">
        <input
          className={`cred-field-input${isPassword ? " has-toggle" : ""}${hasError ? " has-error" : ""}`}
          type={isPassword && !showPassword ? "password" : field.kind === "number" ? "number" : "text"}
          placeholder={field.placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        {isPassword && (
          <button
            type="button"
            className="cred-field-show-toggle"
            onClick={() => setShowPassword((s) => !s)}
            aria-label={showPassword ? "Hide" : "Show"}
          >
            {showPassword ? <EyeSlash size={11} /> : <Eye size={11} />}
            {showPassword ? "Hide" : "Show"}
          </button>
        )}
      </div>
      {error && <span className="cred-field-error">{error}</span>}
      {field.help && !error && <span className="cred-field-help">{field.help}</span>}
    </div>
  );
}

function CredentialCreateModal({
  credType,
  fields,
  typeLabel,
  workflowId,
  credentialContext,
  onClose,
  onCreated,
}: {
  credType: string;
  fields: string[];
  typeLabel: string;
  workflowId: string | null;
  credentialContext?: Record<string, unknown>;
  onClose: () => void;
  onCreated: (id: string, key: string, credential: Credential | null) => void;
}) {
  const displayLabel = CRED_TYPE_LABELS[credType] ?? typeLabel;
  const isLlm = credType === "llm_provider";
  const credentialTypesQuery = useCredentialTypes();
  const credentialTypes = credentialTypesQuery.data ?? null;

  // The credential ref key is dictated by the node, independent of which preset
  // fields render: multi-field pickers store ``*``; single-field pickers store
  // the one field name the node reads.
  const refKey = fields.length > 1 ? "*" : (fields[0] ?? credType);

  // Fallback preset built from the node's declared fields, used when no
  // built-in or backend preset matches this credential type.
  const fallbackPreset = useMemo<CredentialPreset>(
    () => ({
      id: credType,
      type: credType,
      label: displayLabel,
      group: "",
      summary: "",
      description: displayLabel,
      fields: fields.map((f) => ({
        key: f,
        label: credentialFieldLabel(f),
        kind: isSecretField(f) ? "password" : "text",
      })),
    }),
    [credType, displayLabel, fields],
  );

  // Prefer the shared preset (rich placeholders/help/field kinds and OAuth
  // detection) so the NDV form matches the credentials page for this type.
  const preset = useMemo<CredentialPreset>(() => {
    const merged = mergedCredentialPresets(credentialTypes);
    const found = merged.find((p) => p.type === credType);
    if (!found) return fallbackPreset;
    // A matched preset with no fields (e.g. the "generic" catch-all) must not
    // hide the fields the node actually declares — keep the node's fields
    // unless this is an OAuth type whose form is driven by the connect flow.
    if (found.fields.length === 0 && found.authMethod !== "oauth2" && fields.length > 0) {
      return { ...found, fields: fallbackPreset.fields };
    }
    return found;
  }, [credentialTypes, credType, fallbackPreset, fields.length]);

  const isOAuth = preset.authMethod === "oauth2";

  const [name, setName] = useState(displayLabel);
  const [scope, setScope] = useState<CredentialScope>(
    workflowId ? "workflow" : "global",
  );
  const [environmentId, setEnvironmentId] = useState("");
  const [runnerPoolId, setRunnerPoolId] = useState("");
  const [fieldValues, setFieldValues] = useState<Record<string, string>>(() => {
    const init = Object.fromEntries(fields.map((f) => [f, ""]));
    if (isLlm) {
      init.provider = "openai";
      init.base_url = getLlmVariant("openai").baseUrlDefault ?? "";
    }
    return init;
  });
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [oauthStarted, setOauthStarted] = useState("");
  const oauthPopupRef = useRef<Window | null>(null);
  const { notify } = useToast();
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onClose);

  const variant = isLlm ? getLlmVariant(fieldValues.provider) : null;
  // The set of credential fields actually rendered (provider-aware for LLM).
  const renderedFields = isLlm
    ? visibleCredentialFields(fieldValues.provider, showAdvanced)
    : preset.fields.map((f) => f.key);

  // Seed default values for preset fields that arrive after the first render
  // (e.g. backend-only credential types loaded asynchronously) without
  // clobbering anything the user has already typed.
  useEffect(() => {
    if (isLlm) return;
    setFieldValues((cur) => {
      let changed = false;
      const next = { ...cur };
      for (const field of preset.fields) {
        if (!(field.key in next)) {
          next[field.key] = field.defaultValue ?? "";
          changed = true;
        }
      }
      return changed ? next : cur;
    });
  }, [preset, isLlm]);

  // Auto-select the credential created by the OAuth popup flow.
  useEffect(() => {
    if (!isOAuth) return;
    function handleMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin) return;
      if (!event.data || typeof event.data !== "object") return;
      const { type, message: msg, credentialId } = event.data as {
        type?: string;
        message?: string;
        credentialId?: string;
      };
      if (type === "noodle_oauth_success") {
        setOauthStarted("");
        oauthPopupRef.current = null;
        notify("Credential connected.", "success");
        if (credentialId) onCreated(credentialId, refKey, null);
      } else if (type === "noodle_oauth_error") {
        setOauthStarted("");
        oauthPopupRef.current = null;
        setError(msg ?? "OAuth failed.");
      }
    }
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [isOAuth, notify, onCreated, refKey]);

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

  function validateScopeInputs(): boolean {
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

  /** Build the credential data dict. Returns null and sets per-field errors when
   *  required fields are missing. Non-field errors (network, scope) still use setError. */
  function collectData(): Record<string, string> | null {
    const newErrors: Record<string, string> = {};
    const data: Record<string, string> = {};
    if (isLlm) {
      data.provider = fieldValues.provider || "openai";
      if (variant?.apiKey === "required" && !fieldValues.api_key?.trim()) {
        newErrors.api_key = "API key is required.";
        setFieldErrors(newErrors);
        return null;
      }
      // Persist only the chosen provider's fields (advanced included if filled).
      for (const key of visibleCredentialFields(fieldValues.provider, true)) {
        if (fieldValues[key]?.trim()) data[key] = fieldValues[key].trim();
      }
      setFieldErrors({});
      return data;
    }
    for (const field of preset.fields) {
      const value = fieldValues[field.key] ?? "";
      if (field.required && !value.trim()) {
        newErrors[field.key] = `${field.label} is required.`;
      } else if (value.trim() || field.defaultValue !== undefined) {
        data[field.key] = value.trim();
      }
    }
    if (credType === "google_sheets" && !data.api_key && !data.access_token) {
      newErrors.api_key = "Enter either API key or OAuth access token.";
      newErrors.access_token = "Enter either API key or OAuth access token.";
    }
    if (Object.keys(newErrors).length > 0) {
      setFieldErrors(newErrors);
      return null;
    }
    setFieldErrors({});
    return data;
  }

  function scopeIds() {
    return {
      workflow_id: scope === "workflow" ? workflowId : null,
      environment_id: scope === "environment" ? environmentId.trim() : null,
      runner_pool_id: scope === "runner_pool" ? runnerPoolId.trim() : null,
    };
  }

  async function handleCreate(): Promise<void> {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError("");
    const data = collectData();
    if (data === null) {
      setBusy(false);
      return;
    }
    if (!validateScopeInputs()) {
      setBusy(false);
      return;
    }
    try {
      const created = await api.createCredential({
        name: name.trim(),
        type: credType,
        scope,
        ...scopeIds(),
        description: displayLabel,
        data,
      });
      setFieldErrors({});
      notify("Credential created.", "success");
      onCreated(created.id, refKey, created);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  async function handleTest(): Promise<void> {
    if (testing) return;
    setTesting(true);
    setError("");
    const data = collectData();
    if (data === null) {
      setTesting(false);
      return;
    }
    try {
      const result = await api.testCredentialDraft({
        type: credType,
        data,
        context: credentialContext ?? {},
      });
      notify(
        result.ok ? "Credential connected." : result.message,
        result.ok ? "success" : "error",
      );
      if (!result.ok) setError(result.message);
    } catch (err) {
      // Inside the credential modal/field → inline error (toast would duplicate).
      setError(errorMessage(err));
    } finally {
      setTesting(false);
    }
  }

  async function startOAuth(): Promise<void> {
    if (!name.trim() || busy || !isOAuth) return;
    setBusy(true);
    setError("");
    setOauthStarted("");
    if (!validateScopeInputs()) {
      setBusy(false);
      return;
    }
    try {
      const started = await api.startCredentialOAuth({
        credential_type: credType,
        name: name.trim(),
        scope,
        ...scopeIds(),
        description: displayLabel,
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
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal cred-quick-modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="cred-quick-modal-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        {/* single-row header */}
        <div className="cred-modal-header">
          <span
            className="credential-type-dot"
            style={{ background: credTypeDotColor(credType) }}
          />
          <h2 id="cred-quick-modal-title" className="cred-modal-title">
            New credential
          </h2>
          {preset.documentationUrl && (
            <a
              className="cred-modal-doc-link"
              href={preset.documentationUrl}
              target="_blank"
              rel="noreferrer"
              title="Documentation"
            >
              <Info size={13} />
              Docs
            </a>
          )}
          <span className="cred-modal-type-pill">{credType}</span>
          <button
            type="button"
            className="cred-modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={13} weight="bold" />
          </button>
        </div>

        {/* name field */}
        <div className="cred-field">
          <div className="cred-field-label">
            <span className="cred-field-label-text">Name</span>
            <span className="cred-field-required">required</span>
          </div>
          <input
            className="cred-field-input"
            placeholder="My credential"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !isOAuth) void handleCreate();
              if (e.key === "Escape") onClose();
            }}
          />
        </div>

        {/* scope card tiles */}
        <div>
          <div style={{ fontSize: "11px", fontWeight: 600, color: "var(--ink-2)", marginBottom: 8 }}>
            Scope
          </div>
          <div className="cred-scope-tiles">
            <button
              type="button"
              className={`cred-scope-tile${scope === "global" ? " is-selected" : ""}`}
              onClick={() => setScope("global")}
            >
              <span className="cred-scope-tile-label">Global</span>
              <span className="cred-scope-tile-sub">All workflows</span>
            </button>
            {workflowId && (
              <button
                type="button"
                className={`cred-scope-tile${scope === "workflow" ? " is-selected" : ""}`}
                onClick={() => setScope("workflow")}
              >
                <span className="cred-scope-tile-label">This workflow</span>
                <span className="cred-scope-tile-sub">Scoped only here</span>
              </button>
            )}
            <button
              type="button"
              className={`cred-scope-tile${scope === "environment" ? " is-selected" : ""}`}
              onClick={() => setScope("environment")}
            >
              <span className="cred-scope-tile-label">Environment</span>
              <span className="cred-scope-tile-sub">Enter ID below</span>
            </button>
            <button
              type="button"
              className={`cred-scope-tile${scope === "runner_pool" ? " is-selected" : ""}`}
              onClick={() => setScope("runner_pool")}
            >
              <span className="cred-scope-tile-label">Runner pool</span>
              <span className="cred-scope-tile-sub">Enter ID below</span>
            </button>
          </div>
        </div>

        {scope === "environment" && (
          <div className="cred-field">
            <div className="cred-field-label">
              <span className="cred-field-label-text">Environment ID</span>
              <span className="cred-field-required">required</span>
            </div>
            <input
              className="cred-field-input"
              placeholder="Environment ID"
              value={environmentId}
              onChange={(e) => setEnvironmentId(e.target.value)}
            />
          </div>
        )}
        {scope === "runner_pool" && (
          <div className="cred-field">
            <div className="cred-field-label">
              <span className="cred-field-label-text">Runner pool ID</span>
              <span className="cred-field-required">required</span>
            </div>
            <input
              className="cred-field-input"
              placeholder="Runner pool ID"
              value={runnerPoolId}
              onChange={(e) => setRunnerPoolId(e.target.value)}
            />
          </div>
        )}

        {isOAuth && (
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
              className="btn btn-primary btn-sm"
              onClick={() => void startOAuth()}
              disabled={busy || !name.trim()}
            >
              {busy ? "Opening…" : "Connect OAuth"}
            </button>
            {oauthStarted && <small>{oauthStarted}</small>}
          </div>
        )}

        {/* credential form fields */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {isLlm && (
            <div className="cred-field">
              <div className="cred-field-label">
                <span className="cred-field-label-text">Provider</span>
              </div>
              <div className="cred-field-input-wrap">
                <select
                  className="cred-field-input cred-field-select"
                  value={fieldValues.provider}
                  onChange={(e) => onProviderChange(e.target.value)}
                >
                  {LLM_PROVIDER_VARIANTS.map((v) => (
                    <option key={v.value} value={v.value}>
                      {v.label}
                    </option>
                  ))}
                </select>
                <span className="cred-field-select-caret" aria-hidden="true">▾</span>
              </div>
            </div>
          )}

          {isLlm
            ? renderedFields.map((key) => {
                const def = LLM_FIELD_DEFS[key];
                if (!def) return null;
                const required = key === "api_key" && variant?.apiKey === "required";
                return (
                  <CredentialModalField
                    key={key}
                    field={{ ...def, required }}
                    value={fieldValues[key] ?? ""}
                    onChange={(next) => setField(key, next)}
                    error={fieldErrors[key]}
                  />
                );
              })
            : preset.fields.map((field) => (
                <CredentialModalField
                  key={field.key}
                  field={field}
                  value={fieldValues[field.key] ?? ""}
                  onChange={(next) => setField(field.key, next)}
                  error={fieldErrors[field.key]}
                />
              ))}
        </div>

        {isLlm &&
          variant !== null &&
          variant.advancedFields.length > 0 &&
          variant.value !== "azure_openai" && (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              style={{ alignSelf: "flex-start" }}
              onClick={() => setShowAdvanced((v) => !v)}
            >
              {showAdvanced ? "Hide advanced" : "Advanced options"}
            </button>
          )}

        <p className="cred-security-note">
          🔒 Secret values are encrypted at rest and are never returned by the API after creation.
        </p>

        {error && <p className="error-text">{error}</p>}

        <div className="cred-modal-footer">
          <button
            type="button"
            className="cred-btn-cancel"
            onClick={onClose}
          >
            Cancel
          </button>
          {!isOAuth && (
            <button
              type="button"
              className="cred-btn-test"
              disabled={testing}
              onClick={() => void handleTest()}
            >
              {testing ? "Testing…" : "Test connection"}
            </button>
          )}
          <button
            type="button"
            className="cred-btn-create"
            disabled={busy || !name.trim()}
            onClick={() => void handleCreate()}
          >
            {busy ? "Creating…" : isOAuth ? "Save manual" : "Create credential"}
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
  const [pickerOpen, setPickerOpen] = useState(false);
  const [credentialQuery, setCredentialQuery] = useState("");
  const [focusedIndex, setFocusedIndex] = useState(-1);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState("");
  const pickerRef = useRef<HTMLDivElement>(null);
  const { notify } = useToast();

  function load(): void {
    setLoading(true);
    api
      .listCredentials()
      .then((items) => {
        setCredentials(items);
        setError("");
      })
      .catch((err) => setError(errorMessage(err)))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  useEffect(() => {
    if (!pickerOpen) {
      setFocusedIndex(-1);
      return;
    }
    function closeOnOutside(event: MouseEvent): void {
      if (
        event.target instanceof Node &&
        pickerRef.current &&
        !pickerRef.current.contains(event.target)
      ) {
        setPickerOpen(false);
      }
    }
    function closeOnEscape(event: KeyboardEvent): void {
      if (event.key === "Escape") setPickerOpen(false);
    }
    document.addEventListener("mousedown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [pickerOpen]);

  useEffect(() => {
    setFocusedIndex(-1);
  }, [credentialQuery]);

  const matching = credentials.filter((cred) =>
    credentialMatchesParam(cred, meta, targetKey),
  );
  const visibleMatching = matching.filter((cred) =>
    credentialMatchesSearch(cred, credentialQuery),
  );

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
      setError(errorMessage(err));
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
      // Inside the credential modal/field → inline error (toast would duplicate).
      setError(errorMessage(err));
    } finally {
      setTesting(false);
    }
  }

  function selectCredential(cred: Credential): void {
    onChange(makeCredentialRef(cred.id, targetKey));
    setPickerOpen(false);
    setCredentialQuery("");
    setError("");
  }

  function openCreateModal(): void {
    setPickerOpen(false);
    setModalOpen(true);
    setError("");
  }

  const daysUntilExpiry = getDaysUntilExpiry(selectedCredential?.oauth_expires_at);
  const isExpiringSoon = daysUntilExpiry !== null && daysUntilExpiry <= 7;
  const isExpired = daysUntilExpiry !== null && daysUntilExpiry <= 0;

  function handlePickerKeyDown(e: React.KeyboardEvent<HTMLDivElement>): void {
    if (!pickerOpen) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setFocusedIndex((i) => Math.min(i + 1, visibleMatching.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setFocusedIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && focusedIndex >= 0 && focusedIndex < visibleMatching.length) {
      e.preventDefault();
      selectCredential(visibleMatching[focusedIndex]);
    } else if (e.key === "Tab") {
      setPickerOpen(false);
    }
  }

  return (
    <div className="credential-param">
      <div className="credential-select-row" ref={pickerRef}>
        <div className="credential-picker">
          <button
            type="button"
            className={[
              "credential-picker-trigger",
              selectedCredential ? "has-selection" : !selected ? "is-unset" : "",
              (selectedMissing || isExpiringSoon) ? "is-warning" : "",
            ].filter(Boolean).join(" ")}
            disabled={loading}
            aria-haspopup="listbox"
            aria-expanded={pickerOpen}
            onClick={() => setPickerOpen((open) => !open)}
          >
            {/* type-colored dot */}
            <span
              className={`credential-type-dot${selectedMissing ? " is-hollow" : ""}`}
              style={selectedCredential ? { background: credTypeDotColor(selectedCredential.type) } : undefined}
            />

            {/* name or placeholder */}
            <span className={`credential-picker-name${!selectedCredential && !selectedMissing ? " is-placeholder" : ""}`}>
              {loading
                ? "Loading…"
                : selectedCredential
                  ? selectedCredential.name
                  : selectedMissing && selected
                    ? "Credential unavailable"
                    : "Choose a credential…"}
            </span>

            {/* scope pill (only when credential is found) */}
            {selectedCredential && (
              <span className={`credential-scope-pill${isExpiringSoon ? " is-warning" : ""}`}>
                {isExpired
                  ? "expired"
                  : isExpiringSoon
                    ? `⚠ ${daysUntilExpiry}d`
                    : credentialScopeLabel(selectedCredential)}
              </span>
            )}

            {/* missing badge */}
            {selectedMissing && selected && (
              <span className="credential-scope-pill is-warning">missing</span>
            )}

            {/* unset +New pill */}
            {!selected && !loading && (
              <button
                type="button"
                className="credential-new-pill"
                onClick={(e) => {
                  e.stopPropagation();
                  openCreateModal();
                }}
              >
                + New
              </button>
            )}

            {/* inline Test chip (only when credential exists) */}
            {selectedCredential && (
              <button
                type="button"
                className="credential-test-chip"
                disabled={testing}
                onClick={(e) => {
                  e.stopPropagation();
                  void testSelectedCredential();
                }}
              >
                {testing ? "…" : "✓ Test"}
              </button>
            )}

            <span className="credential-picker-caret" aria-hidden="true">▾</span>
          </button>

          {pickerOpen && (
            <div
              className="credential-picker-menu"
              role="listbox"
              onKeyDown={handlePickerKeyDown}
            >
              {/* search */}
              <div className="credential-picker-search">
                <MagnifyingGlass size={13} aria-hidden="true" />
                <input
                  autoFocus
                  placeholder="Search credentials…"
                  value={credentialQuery}
                  onChange={(e) => setCredentialQuery(e.target.value)}
                  aria-label="Search credentials"
                />
              </div>

              {/* list */}
              <div className="credential-picker-list">
                {visibleMatching.length > 0 ? (
                  visibleMatching.map((cred, idx) => {
                    const isSelected = selected?.id === cred.id;
                    const isFocused = idx === focusedIndex;
                    return (
                      <button
                        type="button"
                        key={`${cred.id}:${targetKey}`}
                        className={[
                          "credential-picker-option",
                          isSelected ? "is-selected" : "",
                          isFocused ? "is-keyboard-focused" : "",
                        ].filter(Boolean).join(" ")}
                        role="option"
                        aria-selected={isSelected}
                        onClick={() => selectCredential(cred)}
                        onMouseEnter={() => setFocusedIndex(idx)}
                      >
                        <span className="credential-picker-check" aria-hidden="true">
                          {isSelected ? "✓" : ""}
                        </span>
                        <span
                          className="credential-type-dot"
                          style={{ background: credTypeDotColor(cred.type) }}
                        />
                        <span className="credential-picker-option-body">
                          <span className="credential-picker-option-name">{cred.name}</span>
                          <span className="credential-picker-option-meta">
                            {credentialTypeLabel(cred.type)} · {formatRelativeTime(cred.last_used_at)}
                          </span>
                        </span>
                        <span className="credential-scope-pill">
                          {credentialScopeLabel(cred)}
                        </span>
                      </button>
                    );
                  })
                ) : matching.length === 0 ? (
                  <div className="credential-picker-empty">
                    <span className="credential-picker-empty-icon">🔑</span>
                    <strong>No {credentialTypeLabel(meta?.type ?? spec.name)} credentials yet</strong>
                    <span>Create one to connect this node.</span>
                    <button
                      type="button"
                      className="btn btn-sm btn-primary"
                      onClick={openCreateModal}
                    >
                      <Plus size={12} weight="bold" /> New credential
                    </button>
                  </div>
                ) : (
                  <div className="credential-picker-empty">
                    <strong>No results for "{credentialQuery}"</strong>
                    <span>Try a different name, type, or scope.</span>
                  </div>
                )}
              </div>

              {/* footer */}
              <div className="credential-picker-foot">
                {selected && (
                  <button
                    type="button"
                    className="credential-picker-foot-clear"
                    onClick={() => {
                      onChange("");
                      setPickerOpen(false);
                    }}
                  >
                    Clear
                  </button>
                )}
                <span className="credential-picker-foot-spacer" />
                <button
                  type="button"
                  className="btn btn-sm btn-primary"
                  onClick={openCreateModal}
                >
                  <Plus size={12} weight="bold" /> New credential
                </button>
                <a
                  className="credential-picker-foot-manage"
                  href="/credentials"
                  target="_blank"
                  rel="noreferrer"
                >
                  Manage →
                </a>
              </div>
            </div>
          )}
        </div>
      </div>

      {selectedMissing && selected && (
        <div className="credential-warning-banner">
          <WarningCircle size={14} weight="fill" />
          <span style={{ flex: 1 }}>
            This credential was deleted or is no longer visible to this workflow.
          </span>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={() => { onChange(""); setError(""); }}
          >
            Clear
          </button>
        </div>
      )}

      {inlineValue && !selected && (
        <div className="credential-warning-banner">
          <WarningCircle size={14} weight="fill" />
          <span style={{ flex: 1 }}>Inline secret in workflow — move to credential store.</span>
          <button
            type="button"
            className="btn btn-sm btn-primary"
            disabled={busy}
            onClick={() => void moveInline()}
          >
            {busy ? "Moving…" : "Move"}
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
        <div className="credential-warning-banner">
          <WarningCircle size={14} weight="fill" />
          <span>Missing OAuth scopes: {missingScopes.join(", ")}</span>
        </div>
      )}

      {modalOpen && meta && (
        <CredentialCreateModal
          credType={meta.type}
          fields={fields}
          typeLabel={meta.label || spec.name}
          workflowId={workflowId}
          credentialContext={credentialContext}
          onClose={() => setModalOpen(false)}
          onCreated={(id, key, created) => {
            // Manual create returns the new credential; OAuth connect returns
            // null (only the id), so we rely on load() to refetch the list.
            if (created) {
              setCredentials((items) => [
                created,
                ...items.filter((item) => item.id !== created.id),
              ]);
            }
            onChange(makeCredentialRef(id, key));
            setModalOpen(false);
            load();
          }}
        />
      )}
    </div>
  );
}

/** Textarea that paints `{{ }}` blocks in accent green using the
 *  mirror-overlay technique: a styled <div> renders the highlighted text under
 *  a transparent <textarea> that handles caret + editing. Scroll position stays
 *  in sync. */

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
  language,
  lineNumbers = false,
  lint = false,
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
  /** Enable Python token colouring + Tab-to-indent in the highlight mirror. */
  language?: "python";
  /** Render a scroll-synced line-number gutter (code mode). */
  lineNumbers?: boolean;
  /** Debounced ruff lint with gutter markers + a diagnostics strip (code). */
  lint?: boolean;
}) {
  const taRefInternal = useRef<HTMLTextAreaElement>(null);
  const taRef = taRefProp ?? taRefInternal;
  const mirrorRef = useRef<HTMLDivElement>(null);
  const gutterRef = useRef<HTMLDivElement>(null);

  const codeMode = language === "python";
  // Caret offset + scroll, tracked so the active-line band and the autocomplete
  // popover can be positioned without re-measuring the DOM each render.
  const [caret, setCaret] = useState(0);
  const [scroll, setScroll] = useState({ top: 0, left: 0 });
  // Editor font metrics, measured once: line-height/padding from computed style,
  // char width from a canvas (monospace ⇒ uniform advance).
  const [metrics, setMetrics] = useState<{
    lh: number;
    pt: number;
    pl: number;
    cw: number;
  } | null>(null);
  // A2 autocomplete state.
  const [acItems, setAcItems] = useState<string[]>([]);
  const [acSel, setAcSel] = useState(0);
  const [acOpen, setAcOpen] = useState(false);
  const scheduleTimeout = useTimeout();
  // A3 lint diagnostics.
  const [diags, setDiags] = useState<LintDiagnostic[]>([]);

  useEffect(() => {
    const ta = taRef.current;
    if (!ta || !codeMode) return;
    const cs = getComputedStyle(ta);
    let cw = 7.8;
    try {
      const ctx = document.createElement("canvas").getContext("2d");
      if (ctx) {
        ctx.font = `${cs.fontSize} ${cs.fontFamily}`;
        cw = ctx.measureText("M").width || cw;
      }
    } catch {
      /* canvas unavailable — fall back to the estimate */
    }
    setMetrics({
      lh: parseFloat(cs.lineHeight) || 19.5,
      pt: parseFloat(cs.paddingTop) || 10,
      pl: parseFloat(cs.paddingLeft) || 12,
      cw,
    });
    // taRef is a stable ref; codeMode is the only meaningful trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [codeMode]);

  // A3: debounced lint via ruff (backend). Empty/non-code editors stay quiet.
  useEffect(() => {
    if (!lint || !codeMode || !value.trim()) {
      setDiags([]);
      return;
    }
    let cancelled = false;
    const handle = window.setTimeout(() => {
      api
        .lintCode(value)
        .then((res) => {
          if (!cancelled) setDiags(res.diagnostics);
        })
        .catch(() => {
          /* lint is best-effort — never block editing */
        });
    }, 500);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [value, lint, codeMode]);

  const caretLine = codeMode
    ? value.slice(0, caret).split("\n").length - 1
    : 0;
  const caretCol = codeMode
    ? caret - (value.lastIndexOf("\n", caret - 1) + 1)
    : 0;

  // Worst severity per 1-based line, for gutter markers.
  const diagByLine = new Map<number, LintDiagnostic>();
  for (const d of diags) {
    const ex = diagByLine.get(d.line);
    if (!ex || (ex.severity !== "error" && d.severity === "error")) {
      diagByLine.set(d.line, d);
    }
  }

  const updateCaret = () => {
    const ta = taRef.current;
    if (ta) setCaret(ta.selectionStart ?? 0);
  };

  const refreshAc = (pos: number, val: string) => {
    if (!codeMode) return;
    const prefix = getIdentPrefix(val, pos);
    if (prefix.length < 1) {
      setAcOpen(false);
      setAcItems([]);
      return;
    }
    const items = computeCodeSuggestions(val, pos);
    setAcItems(items);
    setAcSel(0);
    setAcOpen(items.length > 0);
  };

  const acceptAc = (word: string) => {
    const ta = taRef.current;
    const pos = ta?.selectionStart ?? caret;
    const prefix = getIdentPrefix(value, pos);
    const start = pos - prefix.length;
    const next = value.slice(0, start) + word + value.slice(pos);
    onChange(next);
    setAcOpen(false);
    const c = start + word.length;
    requestAnimationFrame(() => {
      const el = taRef.current;
      if (el) {
        el.focus();
        el.setSelectionRange(c, c);
        setCaret(c);
      }
    });
  };

  const jumpToLine = (line: number) => {
    const ta = taRef.current;
    if (!ta || !metrics) return;
    const lines = value.split("\n");
    let pos = 0;
    for (let i = 0; i < line - 1 && i < lines.length; i++) {
      pos += lines[i].length + 1;
    }
    ta.focus();
    ta.setSelectionRange(pos, pos);
    setCaret(pos);
    ta.scrollTop = Math.max(0, (line - 1) * metrics.lh - metrics.lh * 3);
    syncScroll();
  };

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

  // Render one segment: {{ }} expressions win; otherwise Python-colour the run
  // when a language is set, else emit it plain. Every character is preserved so
  // the mirror stays aligned with the caret.
  const renderSegment = (s: { text: string; expr: boolean }, key: number) => {
    if (s.expr) {
      return (
        <span key={key} className="hl-ta-expr">
          {s.text}
        </span>
      );
    }
    if (language === "python") {
      return (
        <span key={key}>
          {tokenizePython(s.text).map((t, j) =>
            t.cls ? (
              <span key={j} className={`hl-py-${t.cls}`}>
                {t.text}
              </span>
            ) : (
              <span key={j}>{t.text}</span>
            ),
          )}
        </span>
      );
    }
    return <span key={key}>{s.text}</span>;
  };

  const lineCount = lineNumbers ? value.split("\n").length : 0;

  const syncScroll = () => {
    const ta = taRef.current;
    if (mirrorRef.current && ta) {
      mirrorRef.current.scrollTop = ta.scrollTop;
      mirrorRef.current.scrollLeft = ta.scrollLeft;
    }
    if (gutterRef.current && ta) {
      gutterRef.current.scrollTop = ta.scrollTop;
    }
    if (ta && codeMode) setScroll({ top: ta.scrollTop, left: ta.scrollLeft });
  };

  // Tab indents instead of leaving the field; Shift+Tab dedents two spaces.
  const handleTabIndent = (
    e: React.KeyboardEvent<HTMLTextAreaElement>,
  ): boolean => {
    if (e.key !== "Tab" || !language) return false;
    const ta = taRef.current;
    if (!ta) return false;
    e.preventDefault();
    const start = ta.selectionStart;
    const end = ta.selectionEnd;
    if (e.shiftKey) {
      const lineStart = value.lastIndexOf("\n", start - 1) + 1;
      const lead = value.slice(lineStart).match(/^ {1,2}/)?.[0].length ?? 0;
      if (lead === 0) return true;
      const next = value.slice(0, lineStart) + value.slice(lineStart + lead);
      onChange(next);
      requestAnimationFrame(() => {
        ta.selectionStart = ta.selectionEnd = Math.max(lineStart, start - lead);
      });
      return true;
    }
    const next = value.slice(0, start) + "  " + value.slice(end);
    onChange(next);
    requestAnimationFrame(() => {
      ta.selectionStart = ta.selectionEnd = start + 2;
    });
    return true;
  };

  const wrapClass = [
    "hl-ta-wrap",
    className,
    language ? "hl-ta-wrap--code" : "",
    lineNumbers ? "hl-ta-wrap--gutter" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <>
    <div className={wrapClass}>
      {lineNumbers && (
        <div ref={gutterRef} className="hl-ta-gutter" aria-hidden>
          {Array.from({ length: lineCount }, (_, i) => {
            const d = diagByLine.get(i + 1);
            const cls = [
              codeMode && i === caretLine ? "is-active" : "",
              d ? `has-${d.severity}` : "",
            ]
              .filter(Boolean)
              .join(" ");
            return (
              <span key={i} className={cls || undefined} title={d?.message}>
                {i + 1}
              </span>
            );
          })}
        </div>
      )}
      <div ref={mirrorRef} className="hl-ta-mirror" aria-hidden>
        {segments.map(renderSegment)}
        {/* Trailing newline ensures the mirror grows when the textarea does. */}
        {value.endsWith("\n") && "\n"}
        {/* Non-breaking space keeps empty lines/empty content rendering. */}
        {value === "" && " "}
      </div>
      {/* A1: active-line band — sits above the mirror (so its text shows
          through the translucent fill) and below the transparent textarea. */}
      {codeMode && metrics && (
        <div
          className="hl-ta-activeline"
          aria-hidden
          style={{
            top: metrics.pt + caretLine * metrics.lh - scroll.top,
            height: metrics.lh,
          }}
        />
      )}
      <textarea
        ref={taRef}
        className="hl-ta-input"
        value={value}
        rows={rows}
        placeholder={placeholder}
        spellCheck={spellCheck}
        autoFocus={autoFocus}
        onChange={(e) => {
          onChange(e.target.value);
          const pos = e.target.selectionStart ?? e.target.value.length;
          setCaret(pos);
          refreshAc(pos, e.target.value);
        }}
        onSelect={updateCaret}
        onScroll={syncScroll}
        onBlur={() => scheduleTimeout(() => setAcOpen(false), 120)}
        onKeyDown={(e) => {
          if (codeMode && acOpen && acItems.length > 0) {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setAcSel((s) => (s + 1) % acItems.length);
              return;
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setAcSel((s) => (s - 1 + acItems.length) % acItems.length);
              return;
            }
            if (e.key === "Enter" || e.key === "Tab") {
              e.preventDefault();
              acceptAc(acItems[acSel]);
              return;
            }
            if (e.key === "Escape") {
              e.preventDefault();
              setAcOpen(false);
              return;
            }
          }
          if (handleTabIndent(e)) return;
          onKeyDown?.(e);
        }}
        onDrop={onDrop}
        onDragOver={onDragOver}
      />
      {/* A2: identifier autocomplete, anchored to the caret. */}
      {codeMode && acOpen && acItems.length > 0 && metrics && (
        <ul
          className="hl-ta-ac"
          role="listbox"
          style={{
            top: metrics.pt + (caretLine + 1) * metrics.lh - scroll.top + 2,
            left: Math.max(4, metrics.pl + caretCol * metrics.cw - scroll.left),
          }}
        >
          {acItems.map((s, i) => (
            <li
              key={s}
              role="option"
              aria-selected={i === acSel}
              className={`hl-ta-ac-item${i === acSel ? " sel" : ""}`}
              onMouseDown={(e) => {
                e.preventDefault();
                acceptAc(s);
              }}
              onMouseEnter={() => setAcSel(i)}
            >
              {s}
            </li>
          ))}
        </ul>
      )}
    </div>
      {/* A3: diagnostics strip — click a row to jump to the offending line. */}
      {lint && codeMode && diags.length > 0 && (
        <ul className="hl-ta-diags">
          {diags.map((d, i) => (
            <li
              key={`${d.line}:${d.column}:${i}`}
              className={`hl-ta-diag is-${d.severity}`}
              onClick={() => jumpToLine(d.line)}
            >
              <span className="hl-ta-diag-loc">
                {d.line}:{d.column}
              </span>
              {d.code && <span className="hl-ta-diag-code">{d.code}</span>}
              <span className="hl-ta-diag-msg">{d.message}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

const EXPR_HISTORY_KEY = "noodle_expr_history";

function readExprHistory(fieldKey: string): string[] {
  try {
    const data = JSON.parse(safeGetItem(EXPR_HISTORY_KEY) ?? "{}") as Record<string, unknown>;
    const arr = data[fieldKey];
    return Array.isArray(arr) ? (arr as string[]) : [];
  } catch {
    return [];
  }
}

function appendExprHistory(fieldKey: string, value: string): void {
  if (!value.trim()) return;
  try {
    const data = JSON.parse(safeGetItem(EXPR_HISTORY_KEY) ?? "{}") as Record<string, string[]>;
    const existing = Array.isArray(data[fieldKey]) ? data[fieldKey] : [];
    const deduped = [value, ...existing.filter((v) => v !== value)].slice(0, 10);
    safeSetItem(EXPR_HISTORY_KEY, JSON.stringify({ ...data, [fieldKey]: deduped }));
  } catch {
    /* ignore */
  }
}

function formatExpression(value: string): string {
  return value
    .replace(/\{\{\s*([\s\S]*?)\s*\}\}/g, (_, inner) => `{{ ${inner.trim()} }}`)
    .trim();
}

/** n8n-style expand modal: editor on the left, live Result preview on the right.
 *  Result evaluates faithfully via the backend on a debounce, so HTML/text bodies
 *  are visible as they will be at runtime — no need to execute the workflow. */
function ExpressionEditorModal({
  label,
  value,
  onChange,
  ctx,
  nodeId,
  onClose,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  ctx?: ExprContext;
  nodeId?: string;
  onClose: () => void;
}) {
  const [view, setView] = useState<ResultView>("text");
  const [state, setState] = useState<{
    result?: unknown;
    error?: string | null;
    parts?: PreviewPart[];
    loading: boolean;
  }>({ loading: false });

  const taRef = useRef<HTMLTextAreaElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  // trapFocus:false — the editor + autocomplete drive their own Tab handling.
  useModalA11y(dialogRef, onClose, { trapFocus: false });
  // useModalA11y grabs the first focusable (the Format button) on open; for this
  // modal the user wants to type, so move focus into the editor once on open.
  useEffect(() => {
    taRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sidebar state
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    return safeGetItem("noodle_expr_sidebar_collapsed") === "1";
  });
  const allNodes = useEditor((s) => s.nodes);
  const allEdges = useEditor((s) => s.edges);
  const runOutputs = useEditor((s) => s.runOutputs);
  const upstreamNodes = useMemo(
    () => nodeId ? getUpstreamNodes(nodeId, allNodes, allEdges, runOutputs) : [],
    [nodeId, allNodes, allEdges, runOutputs],
  );
  const [selectedUpstreamId, setSelectedUpstreamId] = useState<string | null>(
    upstreamNodes[0]?.id ?? null,
  );
  const selectedUpstreamNode = upstreamNodes.find((n) => n.id === selectedUpstreamId) ?? upstreamNodes[0];

  // Build the eval context straight from the live store. The modal already
  // subscribes to runOutputs + edges (that's what powers the sidebar), so it
  // can resolve {{ }} on its own — no dependency on the caller threading `ctx`
  // all the way down (which is brittle and was silently arriving undefined).
  // `$json`/`$input` come from the wired inputs, `$node[...]` from every run
  // output. The `ctx` prop is kept as a fallback for callers without a nodeId.
  const effectiveCtx = useMemo<ExprContext>(() => {
    if (!nodeId) return ctx ?? {};
    const incoming: Record<string, unknown> = {};
    for (const edge of allEdges) {
      if (edge.target !== nodeId) continue;
      const upstream = runOutputs[edge.source];
      if (!upstream || typeof upstream !== "object") continue;
      const handle = edge.sourceHandle ?? "main";
      const v = (upstream as Record<string, unknown>)[handle];
      if (v !== undefined) incoming[edge.targetHandle ?? "input"] = v;
    }
    if (Object.keys(runOutputs).length === 0) return ctx ?? {};
    return {
      json: Object.values(incoming)[0] ?? ctx?.json,
      inputs: Object.keys(incoming).length ? incoming : ctx?.inputs,
      nodes: runOutputs,
    };
  }, [nodeId, allEdges, runOutputs, ctx]);

  useEffect(() => {
    if (upstreamNodes.length > 0 && !upstreamNodes.find((n) => n.id === selectedUpstreamId)) {
      setSelectedUpstreamId(upstreamNodes[0]?.id ?? null);
    }
  }, [upstreamNodes, selectedUpstreamId]);

  function toggleSidebar() {
    setSidebarCollapsed((c) => {
      const next = !c;
      safeSetItem("noodle_expr_sidebar_collapsed", next ? "1" : "0");
      return next;
    });
  }

  const startFieldDrag = useCallback((e: React.DragEvent<HTMLElement>, expression: string) => {
    e.dataTransfer.setData("text/plain", expression);
    e.dataTransfer.setData("application/x-noodle-expression", expression);
    e.dataTransfer.effectAllowed = "copy";
    const ghost = document.createElement("div");
    ghost.className = "expr-drag-ghost";
    ghost.textContent = expression;
    document.body.appendChild(ghost);
    e.dataTransfer.setDragImage(ghost, 12, 12);
    window.setTimeout(() => ghost.remove(), 0);
  }, []);

  const historyKey = `${nodeId ?? ""}:${label}`;
  const [historyOpen, setHistoryOpen] = useState(false);
  const history = readExprHistory(historyKey);

  const latestValue = useRef(value);
  latestValue.current = value;
  useEffect(() => {
    return () => {
      appendExprHistory(historyKey, latestValue.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historyKey]);

  const [pickerOpen, setPickerOpen] = useState(false);
  const dollarPosRef = useRef<number | null>(null);

  function insertExpression(expression: string) {
    const ta = taRef.current;
    const pos = ta?.selectionStart ?? value.length;

    if (dollarPosRef.current !== null) {
      const dollarPos = dollarPosRef.current;
      dollarPosRef.current = null;
      const inner = expression.replace(/^\{\{\s*/, "").replace(/\s*\}\}$/, "");
      onChange(value.slice(0, dollarPos - 1) + inner + value.slice(dollarPos));
      return;
    }

    const before = value.slice(0, pos);
    const opens = (before.match(/\{\{/g) ?? []).length;
    const closes = (before.match(/\}\}/g) ?? []).length;
    const insideExpr = opens > closes;
    const toInsert = insideExpr
      ? expression.replace(/^\{\{\s*/, "").replace(/\s*\}\}$/, "")
      : expression;
    onChange(value.slice(0, pos) + toInsert + value.slice(pos));
    setPickerOpen(false);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === " " && (e.ctrlKey || e.metaKey) && nodeId) {
      e.preventDefault();
      dollarPosRef.current = null;
      setPickerOpen(true);
    }
  }

  const hasData =
    effectiveCtx.json !== undefined ||
    Object.keys(effectiveCtx.nodes ?? {}).length > 0 ||
    Object.keys(effectiveCtx.inputs ?? {}).length > 0;
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
          json: effectiveCtx.json,
          inputs: effectiveCtx.inputs,
          nodes: effectiveCtx.nodes,
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
  }, [value, effectiveCtx, hasExpr, hasData]);

  const resultText = formatResultText(state.result);
  const parts = state.parts ?? [];

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal modal-wide expr-modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="expr-modal-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <h2 id="expr-modal-title">
            Editing <span className="expr-modal-label">{label}</span>
          </h2>
          <div className="expr-modal-header-actions">
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={() => onChange(formatExpression(value))}
              title="Normalise {{ }} spacing"
            >
              Format
            </button>
            <div className="expr-history-wrap">
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                onClick={() => setHistoryOpen((o) => !o)}
                title="Recent values for this field"
                disabled={history.length === 0}
              >
                History {history.length > 0 ? `(${history.length})` : ""}
              </button>
              {historyOpen && history.length > 0 && (
                <ul className="expr-history-dropdown">
                  {history.map((entry, i) => (
                    <li key={i}>
                      <button
                        type="button"
                        onClick={() => {
                          onChange(entry);
                          setHistoryOpen(false);
                        }}
                      >
                        {entry.slice(0, 60)}{entry.length > 60 ? "…" : ""}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <button className="btn btn-sm btn-ghost" onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
        </header>
        <div className="expr-modal-body">
          {upstreamNodes.length > 0 && (
            <aside className={`expr-sidebar${sidebarCollapsed ? " expr-sidebar--collapsed" : ""}`}>
              <div className="expr-sidebar-head">
                {!sidebarCollapsed && <span className="expr-sidebar-title">Variables</span>}
                <button
                  type="button"
                  className="expr-sidebar-collapse"
                  title={sidebarCollapsed ? "Expand variable panel" : "Collapse variable panel"}
                  onClick={toggleSidebar}
                >
                  {sidebarCollapsed ? "›" : "‹"}
                </button>
              </div>
              {!sidebarCollapsed && (
                <div className="expr-sidebar-body">
                  {upstreamNodes.length > 1 && (
                    <div className="expr-sidebar-node-select">
                      <select
                        className="expr-sidebar-dropdown"
                        value={selectedUpstreamId ?? ""}
                        onChange={(e) => setSelectedUpstreamId(e.target.value)}
                      >
                        {upstreamNodes.map((n) => (
                          <option key={n.id} value={n.id}>
                            {n.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                  {upstreamNodes.length === 1 && (
                    <div className="expr-sidebar-node-single">{upstreamNodes[0].label}</div>
                  )}
                  <div className="expr-sidebar-fields">
                    {(selectedUpstreamNode?.fields.length ?? 0) === 0 && (
                      <p className="expr-sidebar-empty">No output data yet.</p>
                    )}
                    {selectedUpstreamNode?.fields.map((field) => (
                      <div
                        key={field.path}
                        className="expr-sidebar-field"
                        draggable
                        onDragStart={(e) => startFieldDrag(e, field.expression)}
                        title={`Drag to insert ${field.expression}`}
                      >
                        <span className="expr-sidebar-grip">⠿</span>
                        <span className="expr-sidebar-name">{field.path}</span>
                        <span className="expr-sidebar-type">{field.type}</span>
                        <span className="expr-sidebar-preview">{field.valuePreview}</span>
                      </div>
                    ))}
                  </div>
                  <div className="expr-sidebar-meta">
                    {[
                      { path: "$run.id", expr: "{{ $run.id }}" },
                      { path: "$run.status", expr: "{{ $run.status }}" },
                      { path: "$env.KEY", expr: "{{ $env.KEY }}" },
                    ].map((m) => (
                      <div
                        key={m.path}
                        className="expr-sidebar-field expr-sidebar-field--meta"
                        draggable
                        onDragStart={(e) => startFieldDrag(e, m.expr)}
                        title={`Drag to insert ${m.expr}`}
                      >
                        <span className="expr-sidebar-grip">⠿</span>
                        <span className="expr-sidebar-name">{m.path}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </aside>
          )}
          <section className="expr-modal-pane" style={{ position: "relative" }}>
            <div className="expr-modal-pane-head">
              <span>Expression</span>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span className="muted expr-modal-hint">
                  Anything inside <code>{"{{ }}"}</code> is evaluated
                </span>
                {nodeId && (
                  <button
                    type="button"
                    className="btn btn-xs expr-pick-btn"
                    title="Pick a variable (Ctrl+Space)"
                    onClick={() => { dollarPosRef.current = null; setPickerOpen((o) => !o); }}
                  >
                    $ Pick variable
                  </button>
                )}
              </div>
            </div>
            <div className="expr-modal-editor-wrap">
              <HighlightedTextarea
                className="expr-modal-editor"
                language="python"
                lineNumbers
                value={value}
                onChange={(v) => {
                  onChange(v);
                  const pos = taRef.current?.selectionStart ?? v.length;
                  const before = v.slice(0, pos);
                  const opens = (before.match(/\{\{/g) ?? []).length;
                  const closes = (before.match(/\}\}/g) ?? []).length;
                  const insideExpr = opens > closes;
                  if (insideExpr && v[pos - 1] === "$") {
                    dollarPosRef.current = pos;
                    setPickerOpen(true);
                  }
                }}
                autoFocus
                taRef={taRef}
                onKeyDown={(e) => {
                  if (e.key === "Escape" && pickerOpen) {
                    setPickerOpen(false);
                    dollarPosRef.current = null;
                    return;
                  }
                  handleKeyDown(e);
                }}
              />
            </div>
            {state.parts && state.parts.some((p) => p.kind === "expr") && (
              <div className="expr-parts-bar">
                <span className="expr-parts-label">Resolved:</span>
                {state.parts
                  .filter((p) => p.kind === "expr" || p.kind === "error")
                  .map((p, i) =>
                    p.kind === "error" ? (
                      <span key={i} className="expr-part-chip expr-part-chip--error" title={p.error}>
                        <span className="expr-part-chip-raw">{p.raw}</span>
                        <span className="expr-part-chip-arrow">→</span>
                        <span className="expr-part-chip-val">⚠ error</span>
                      </span>
                    ) : (
                      <span key={i} className="expr-part-chip">
                        <span className="expr-part-chip-raw">
                          {p.raw.replace(/^\{\{\s*/, "").replace(/\s*\}\}$/, "").slice(0, 20)}
                        </span>
                        <span className="expr-part-chip-arrow">→</span>
                        <span className="expr-part-chip-val">
                          {String(p.value === null || p.value === undefined ? "null" : p.value).slice(0, 18)}
                        </span>
                      </span>
                    ),
                  )}
              </div>
            )}
            {pickerOpen && nodeId && (
              <div className="expr-picker-anchor">
                <VariablePickerPopover
                  nodeId={nodeId}
                  onInsert={insertExpression}
                  onClose={() => { setPickerOpen(false); dollarPosRef.current = null; }}
                />
              </div>
            )}
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
                  No upstream data yet — run the workflow once and this preview
                  will resolve each <code>{"{{ }}"}</code> against live values.
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
  const requestGenerationRef = useRef(0);

  // Keep the input text in sync with external value changes (e.g. preset selects).
  useEffect(() => { setQuery(current); }, [current]);

  const loader = spec.load_options ?? "";
  const loadParamsKey = JSON.stringify(
    buildLoadOptionsParams(credential, params, spec.depends_on ?? []),
  );
  const fetchOptions = useCallback((): void => {
    if (!loader) return;
    const generation = ++requestGenerationRef.current;
    setLoading(true);
    setErr("");
    api
      .dynamicOptions(
        loader,
        JSON.parse(loadParamsKey) as Record<string, string>,
      )
      .then((res) => {
        if (requestGenerationRef.current === generation) {
          setFetched(res.options.map((o) => o.value));
        }
      })
      .catch(() => {
        if (requestGenerationRef.current === generation) {
          setErr("Couldn't load list — type a value or retry.");
        }
      })
      .finally(() => {
        if (requestGenerationRef.current === generation) setLoading(false);
      });
  }, [loader, loadParamsKey]);

  // Auto-fetch on mount and whenever an input the loader keys off changes:
  // the credential, the selected provider, or a custom base_url. Without the
  // provider dependency the model list would stay stale after switching e.g.
  // openai → openrouter. Public catalogues (OpenRouter) load even with no
  // credential; keyed ones fall back to the curated list until a key is set.
  useEffect(() => {
    fetchOptions();
    return () => {
      requestGenerationRef.current += 1;
    };
  }, [fetchOptions]);

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
  const wrapRef = useRef<HTMLDivElement>(null);
  // Resync from an external value change (undo/redo, AI fix) — FE-11.
  useExternalValueSync(
    value,
    () => Boolean(wrapRef.current?.contains(document.activeElement)),
    () => setRows(routeRowsFromValue(value)),
  );

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
    <div className="routes-field" ref={wrapRef}>
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

function FileUploadField({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [filename, setFilename] = useState<string | null>(null);
  const [sizeBytes, setSizeBytes] = useState<number | null>(null);
  const { notify } = useToast();

  // Resolve filename + size from the artifact store on mount / when the saved
  // artifact_id changes (e.g. after a page reload the local state is gone but
  // the artifact is still pinned in the workflow params).
  useEffect(() => {
    if (!value) {
      setFilename(null);
      setSizeBytes(null);
      return;
    }
    let cancelled = false;
    api.getArtifact(value).then((info) => {
      if (cancelled) return;
      setFilename(info.name);
      setSizeBytes(info.size_bytes ?? null);
    }).catch(() => {
      // artifact may have been deleted — leave label as the raw id
    });
    return () => { cancelled = true; };
  }, [value]);

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    try {
      const info = await uploadArtifact(file);
      onChange(info.id);
      setFilename(info.name);
      setSizeBytes(info.size_bytes ?? null);
    } catch (err) {
      notify(String(err), "error");
    } finally {
      setBusy(false);
      e.target.value = "";
    }
  }

  return (
    <div className="file-upload-field">
      {value && (
        <div className="file-upload-current">
          <span className="file-upload-name">{filename ?? value}</span>
          {sizeBytes != null && (
            <span className="file-upload-size">{formatBytes(sizeBytes)}</span>
          )}
          <span
            className="file-upload-pinned"
            title="File is saved and will be reused across runs without re-uploading"
          >
            pinned
          </span>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={() => {
              onChange("");
              setFilename(null);
              setSizeBytes(null);
            }}
            aria-label="Remove file"
          >
            ×
          </button>
        </div>
      )}
      <label className={`btn btn-sm${busy ? " btn-disabled" : ""}`}>
        {busy ? "Uploading…" : value ? "Replace file" : "Choose file"}
        <input
          type="file"
          style={{ display: "none" }}
          disabled={busy}
          onChange={handleFile}
        />
      </label>
    </div>
  );
}

/**
 * Resolves a field's `{{ }}` template against the live expression context and
 * shows the *fully substituted* result inline (e.g. the complete URL with the
 * variable filled in), so users don't have to open the expand modal to check.
 */
function InlineExprPreview({
  value,
  ctx,
}: {
  value: string;
  ctx?: ExprContext;
}) {
  const [state, setState] = useState<{
    result?: unknown;
    error: string | null;
    loading: boolean;
  }>({ result: undefined, error: null, loading: false });

  const hasExpr = EXPR_RE.test(value);
  const hasData =
    !!ctx &&
    (ctx.json !== undefined ||
      Object.keys(ctx.nodes ?? {}).length > 0 ||
      Object.keys(ctx.inputs ?? {}).length > 0);

  useEffect(() => {
    if (!hasExpr || !hasData || !ctx) {
      setState({ result: undefined, error: null, loading: false });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, loading: true }));
    const handle = window.setTimeout(() => {
      api
        .previewExpression({
          value,
          json: ctx.json,
          inputs: ctx.inputs,
          nodes: ctx.nodes,
        })
        .then((res) => {
          if (!cancelled)
            setState({ result: res.result, error: res.error, loading: false });
        })
        .catch((err) => {
          if (!cancelled)
            setState({ result: undefined, error: String(err), loading: false });
        });
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [value, ctx, hasExpr, hasData]);

  if (!hasExpr || !hasData) return null;
  if (state.loading) {
    return <div className="expr-inline-preview is-loading">→ resolving…</div>;
  }
  if (state.error) {
    return (
      <div className="expr-inline-preview is-error" title={state.error}>
        <span className="eip-arrow">→</span>
        <span className="eip-val">⚠ {state.error}</span>
      </div>
    );
  }
  const text = formatResultText(state.result);
  if (!text) return null;
  return (
    <div className="expr-inline-preview" title={text}>
      <span className="eip-arrow">→</span>
      <span className="eip-val">{text}</span>
    </div>
  );
}

// Single-line string input with expression autocomplete. When the caret sits
// just after a `$…` token, it offers a dropdown of completions ($json keys,
// upstream node ids, $env/$run helpers) driven by computeSuggestions.
function ExprAutocompleteInput({
  value,
  onChange,
  placeholder,
  className,
  ctx,
  dropHandlers,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
  ctx?: ExprContext;
  dropHandlers: {
    onDragOver: React.DragEventHandler<HTMLInputElement>;
    onDrop: React.DragEventHandler<HTMLInputElement>;
  };
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [sel, setSel] = useState(0);
  const [open, setOpen] = useState(false);

  const refresh = (cursorPos: number, val: string) => {
    const token = getTokenBeforeCursor(val, cursorPos);
    if (!token || !token.startsWith("$")) {
      setOpen(false);
      setSuggestions([]);
      return;
    }
    const next = computeSuggestions(val, cursorPos, ctx).slice(0, 8);
    setSuggestions(next);
    setSel(0);
    setOpen(next.length > 0);
  };

  const accept = (suggestion: string) => {
    const input = inputRef.current;
    const cursorPos = input?.selectionStart ?? value.length;
    const token = getTokenBeforeCursor(value, cursorPos);
    const start = cursorPos - token.length;
    const next = value.slice(0, start) + suggestion + value.slice(cursorPos);
    onChange(next);
    setOpen(false);
    const caret = start + suggestion.length;
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (el) {
        el.focus();
        el.setSelectionRange(caret, caret);
      }
    });
  };

  return (
    <div className="expr-ac-wrap">
      <input
        ref={inputRef}
        className={className}
        type="text"
        placeholder={placeholder}
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          refresh(e.target.selectionStart ?? e.target.value.length, e.target.value);
        }}
        onKeyDown={(e) => {
          if (!open || suggestions.length === 0) return;
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setSel((s) => (s + 1) % suggestions.length);
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setSel((s) => (s - 1 + suggestions.length) % suggestions.length);
          } else if (e.key === "Enter" || e.key === "Tab") {
            e.preventDefault();
            accept(suggestions[sel]);
          } else if (e.key === "Escape") {
            e.preventDefault();
            setOpen(false);
          }
        }}
        onKeyUp={(e) => {
          if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
            refresh(e.currentTarget.selectionStart ?? value.length, value);
          }
        }}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        {...dropHandlers}
      />
      {open && suggestions.length > 0 && (
        <ul className="expr-ac" role="listbox">
          {suggestions.map((s, i) => (
            <li
              key={s}
              role="option"
              aria-selected={i === sel}
              className={`expr-ac-item${i === sel ? " sel" : ""}`}
              onMouseDown={(e) => {
                e.preventDefault();
                accept(s);
              }}
              onMouseEnter={() => setSel(i)}
            >
              {s}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function ParamField({
  spec,
  value,
  onChange,
  credentialContext,
  exprContext,
  nodeId,
}: {
  spec: ParamSpec;
  value: unknown;
  onChange: (v: unknown) => void;
  credentialContext?: Record<string, unknown>;
  exprContext?: ExprContext;
  nodeId?: string;
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
  if (spec.widget === "file_upload") {
    return (
      <FileUploadField
        value={String(value ?? "")}
        onChange={onChange}
      />
    );
  }
  if (spec.widget === "routes_table") {
    return <RoutesField value={value} onChange={onChange} />;
  }
  if (spec.widget === "conditions_builder") {
    return (
      <ConditionsField
        value={value}
        onChange={onChange}
        allParams={credentialContext}
      />
    );
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
              nodeId={nodeId}
              onClose={() => setExpanderOpen(false)}
            />
          )}
          {exprContext && <InlineExprPreview value={current} ctx={exprContext} />}
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
        <div className="field-input-row">
          <ExprAutocompleteInput
            className={`field-input${isExpr ? " field-input-expr" : ""}`}
            placeholder={spec.placeholder}
            value={current}
            onChange={onChange}
            ctx={exprContext}
            dropHandlers={drop}
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
        </div>
        {exprContext && <InlineExprPreview value={current} ctx={exprContext} />}
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

function UrlRow({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  const scheduleTimeout = useTimeout();
  return (
    <div className="webhook-url">
      <code>{url}</code>
      <button
        className="btn btn-ghost btn-sm"
        onClick={() => {
          void navigator.clipboard.writeText(url);
          setCopied(true);
          scheduleTimeout(() => setCopied(false), 1500);
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
    void api.stopListen(slug).catch(() => undefined);
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
      await api.startListen(slug);
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
  const dialogRef = useRef<HTMLDivElement>(null);
  // trapFocus:false — the code editor owns Tab for indentation.
  useModalA11y(dialogRef, onClose, { trapFocus: false });
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
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="code-modal-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <h2 id="code-modal-title">
            Editing code <span className="expr-modal-label">{label}</span>
          </h2>
          <button className="btn btn-sm btn-ghost" onClick={onClose} aria-label="Close">
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
            <HighlightedTextarea
              className="code-modal-editor"
              language="python"
              lineNumbers
              lint
              spellCheck={false}
              value={draft}
              onChange={onChange}
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
      // Format on save (best-effort): tidy the Python with ruff before
      // persisting. Never blocks the save — falls through on any error.
      let sourceToSave = draft;
      try {
        const fmt = await api.formatCode(draft);
        if (!fmt.error) {
          sourceToSave = fmt.code;
          if (fmt.changed) setDraft(fmt.code);
        }
      } catch {
        /* formatter unavailable — save as typed */
      }
      // Code modules must be plain functions — strip any @node decorator the
      // user is viewing/editing before persisting.
      const contents = stripLeadingDecorators(sourceToSave);
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
            <HighlightedTextarea
              className="node-code-editor"
              language="python"
              lineNumbers
              lint
              rows={18}
              spellCheck={false}
              value={draft}
              onChange={setDraft}
              onDrop={exprDropHandlers(draft, setDraft).onDrop}
              onDragOver={exprDropHandlers(draft, setDraft).onDragOver}
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
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <button
                className="btn btn-sm"
                disabled={saving}
                onClick={() => setAskSave(true)}
              >
                Save…
              </button>
              <button
                className="btn btn-sm btn-ghost"
                disabled={saving}
                title="Format with ruff"
                onClick={async () => {
                  try {
                    const fmt = await api.formatCode(draft);
                    if (fmt.error) {
                      toast.notify(`Format: ${fmt.error}`, "error");
                    } else {
                      if (fmt.changed) setDraft(fmt.code);
                      toast.notify(
                        fmt.changed ? "Formatted." : "Already formatted.",
                        "success",
                      );
                    }
                  } catch (e) {
                    toast.notify(`Format failed: ${e}`, "error");
                  }
                }}
              >
                Format
              </button>
            </div>
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
  if (!node || !node.data.manifest?.usable_as_tool) return null;
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
  const [pkgElapsed, setPkgElapsed] = useState(0);
  const [pkgDone, setPkgDone] = useState(false);
  const [pickerParam, setPickerParam] = useState<string | null>(null);
  const { notify } = useToast();
  useEffect(() => setMode("inspector"), [nodeId]);
  // Guards the imperative install poller (addMissingToEnv) — it can run for up
  // to PACKAGE_INSTALL_TIMEOUT_MS, well past an NDV close / node switch (FE-12).
  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  async function pin(): Promise<void> {
    if (!workflowId || runOutput === undefined) return;
    try {
      const saved = await api.pinNode(workflowId, nodeId, runOutput);
      setPinnedFor(nodeId, saved.payload, saved.updated_at);
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

  if (!node.data.manifest) {
    return null;
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
    setPkgElapsed(0);
    setPkgDone(false);

    // Tick elapsed seconds while installing
    const startedAt = Date.now();
    const ticker = window.setInterval(() => {
      setPkgElapsed(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);

    try {
      const updated = [...envPackages, ...missingPkgs];
      await api.setPackages(envId, updated);
      setEnvPackages(updated);

      // Poll until env is ready, errored, or clearly stuck.
      while (Date.now() - startedAt < PACKAGE_INSTALL_TIMEOUT_MS) {
        await delay(PACKAGE_INSTALL_POLL_MS);
        // The NDV closed / node switched mid-install — stop polling and
        // ticking rather than leaking calls + setState on an unmounted tree.
        if (!aliveRef.current) {
          clearInterval(ticker);
          return;
        }
        const env = await api.getEnvironment(envId);
        if (env.status === "ready") {
          clearInterval(ticker);
          setPkgBusy(false);
          setPkgDone(true);
          window.setTimeout(() => setPkgDone(false), 3000);
          return;
        }
        if (env.status === "error") {
          clearInterval(ticker);
          notify("Package installation failed — check the environment logs.", "error");
          setPkgBusy(false);
          return;
        }
      }
      clearInterval(ticker);
      notify("Package installation is still building. Check the environment logs.", "error");
      setPkgBusy(false);
    } catch {
      clearInterval(ticker);
      notify("Failed to install packages — check the environment.", "error");
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

  // Live evaluation context for the expression preview. The store already holds
  // each upstream node's most recent run output (that's what powers the Pick-
  // variable sidebar), so the preview can resolve {{ }} against real data with
  // no extra run — `$json`/`$input` from the wired inputs, `$node[...]` from
  // every node's output. Mirrors noodle.expr.build_context on the backend.
  const exprContext: ExprContext = {
    json: Object.values(incomingInputs)[0],
    inputs: incomingInputs,
    nodes: runOutputs,
  };

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

      <PackageInstallPanel
        missingPkgs={missingPkgs}
        envId={envId}
        envName={envName}
        pkgBusy={pkgBusy}
        pkgDone={pkgDone}
        pkgElapsed={pkgElapsed}
        satisfyingEnvs={satisfyingEnvs}
        applyEnvSwitch={applyEnvSwitch}
        onInstall={() => void addMissingToEnv()}
      />

      <SystemRequirementsPanel
        requirements={manifest.system_requirements}
        activeEnv={activeEnv}
      />

      {mode === "python" ? (
        <NodeCodePanel
          nodeId={node.id}
          manifest={manifest}
          inputData={hasIncomingInputs ? incomingInputs : undefined}
          onClose={() => setMode("inspector")}
        />
      ) : (
        <>

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
        {manifest.integration && (
          <ResourceOperationSelector
            manifest={manifest}
            params={params}
            onChange={(next) => updateParams(node.id, next)}
          />
        )}
        {manifest.params
          .filter((spec) => spec.widget !== "hidden")
          .filter((spec) => !webhookHiddenParam(manifest.id, spec.name, params))
          .filter((spec) => matchesDisplayWhen(spec.display_when, params))
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
                  {spec.description && (
                    <span className="param-info-icon" aria-label={spec.description}>
                      <Info size={12} weight="bold" />
                      <span className="param-info-tooltip">{spec.description}</span>
                    </span>
                  )}
                  {fx && <span className="fx-badge" title="Contains expression">fx</span>}
                  {spec.required && <span className="field-req">required</span>}
                  {(spec.type === "string" || spec.type === "expression") &&
                    !spec.credential &&
                    spec.widget !== "hidden" && (
                      <button
                        type="button"
                        className="var-pick-inline-btn"
                        title="Pick a variable from upstream nodes"
                        onClick={() =>
                          setPickerParam((p) => (p === spec.name ? null : spec.name))
                        }
                      >
                        $
                      </button>
                    )}
                </div>
                <FromAiParamControl
                  nodeId={node.id}
                  spec={renderSpec}
                  value={value}
                  onSetParam={setParam}
                />
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
                    exprContext={exprContext}
                    nodeId={node.id}
                  />
                )}
                {pickerParam === spec.name && (
                  <div className="var-pick-inline-wrap">
                    <VariablePickerPopover
                      nodeId={node.id}
                      onInsert={(expr) => {
                        const current = String(params[spec.name] ?? "");
                        const opens = (current.match(/\{\{/g) ?? []).length;
                        const closes = (current.match(/\}\}/g) ?? []).length;
                        const insideExpr = opens > closes;
                        const toInsert = insideExpr
                          ? expr.replace(/^\{\{\s*/, "").replace(/\s*\}\}$/, "")
                          : expr;
                        setParam(spec.name, current + toInsert);
                        setPickerParam(null);
                      }}
                      onClose={() => setPickerParam(null)}
                    />
                  </div>
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
            <p className="muted">
              {disabled
                ? "This node is disabled, so it will not produce output."
                : runStatus === "skipped"
                  ? "This node was skipped in the last run."
                  : runStatus
                    ? "No output was captured for this node in the last run."
                    : "This node has not run yet."}
            </p>
          )}
          {runOutput !== undefined && !pinned && (
            <button
              className="btn btn-sm"
              style={{ marginTop: 8 }}
              onClick={() => void pin()}
            >
              <PushPin size={13} /> Pin this output
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
            Runs use this value instead of executing the node. Pinned {formatPinnedAt(pinned.updatedAt)}.
          </p>
          <pre className="run-output">{JSON.stringify(pinned.payload, null, 2)}</pre>
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
