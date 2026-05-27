import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import { categoryColor } from "../categories";
import { NodeIcon } from "../NodeIcon";
import { useToast } from "../ToastProvider";
import type { Credential, ParamSpec } from "../types";
import { TimezoneSelect } from "./fields/TimezoneSelect";
import { useEditor } from "./store";

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
};

const CRED_TYPE_LABELS: Record<string, string> = {
  http_basic: "Basic Auth",
  http_header: "Header Auth",
  http_query: "Query Auth",
  webhook_secret: "Webhook Secret",
  openai: "OpenAI API Key",
  anthropic: "Anthropic API Key",
  slack_bot: "Slack Bot Token",
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
  onCreated: (id: string, key: string) => void;
}) {
  const displayLabel = CRED_TYPE_LABELS[credType] ?? typeLabel;
  const [name, setName] = useState(displayLabel);
  const [fieldValues, setFieldValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map((f) => [f, ""])),
  );
  const [scope, setScope] = useState<"workflow" | "global">(
    workflowId ? "workflow" : "global",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { notify } = useToast();

  const refKey = fields.length > 1 ? "*" : (fields[0] ?? credType);

  async function handleCreate(): Promise<void> {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError("");
    const data: Record<string, string> = {};
    for (const f of fields) {
      if (fieldValues[f]?.trim()) data[f] = fieldValues[f].trim();
    }
    try {
      const created = await api.createCredential({
        name: name.trim(),
        type: credType,
        scope,
        workflow_id: scope === "workflow" ? workflowId : null,
        description: displayLabel,
        data,
      });
      notify("Credential created.", "success");
      onCreated(created.id, refKey);
    } catch (err) {
      setError(String(err));
      setBusy(false);
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

        {fields.map((field) => (
          <label key={field} className="credential-form-field">
            <span>{CRED_FIELD_LABELS[field] ?? field}</span>
            <input
              className="field-input"
              type={isSecretField(field) ? "password" : "text"}
              placeholder={CRED_FIELD_LABELS[field] ?? field}
              value={fieldValues[field] ?? ""}
              onChange={(e) =>
                setFieldValues({ ...fieldValues, [field]: e.target.value })
              }
              onKeyDown={(e) => {
                if (e.key === "Enter") void handleCreate();
                if (e.key === "Escape") onClose();
              }}
            />
          </label>
        ))}

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

  const matching = credentials.filter((cred) => {
    if (
      meta?.type &&
      cred.type !== meta.type &&
      !["generic", "apiKey", "oauth2"].includes(cred.type)
    ) {
      return false;
    }
    if (meta?.multi) {
      return fields.every((field) => cred.keys.includes(field));
    }
    return cred.keys.includes(targetKey);
  });

  const selectedValue = selected ? `${selected.id}:${selected.key}` : "";
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

      {modalOpen && meta && (
        <CredentialCreateModal
          credType={meta.type}
          fields={fields}
          typeLabel={meta.label || spec.name}
          workflowId={workflowId}
          onClose={() => setModalOpen(false)}
          onCreated={(id, key) => {
            onChange(makeCredentialRef(id, key));
            setModalOpen(false);
            load();
          }}
        />
      )}
    </div>
  );
}

export function ParamField({
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
        <textarea
          className="field-input field-code"
          rows={7}
          spellCheck={false}
          placeholder={spec.placeholder}
          value={current}
          onChange={(e) => onChange(e.target.value)}
          {...drop}
        />
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
            Listen for test event
          </button>
        ) : (
          <button className="btn btn-sm btn-listening" onClick={stop}>
            <span className="node-spinner" />
            Listening… (Stop)
          </button>
        )}
        {received && (
          <span className="muted">Request captured — see Output panel.</span>
        )}
      </div>

      {error && <p className="error-text">{error}</p>}
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
  const color = categoryColor(manifest.category);

  const setParam = (name: string, value: unknown) => {
    updateParams(node.id, { ...params, [name]: value });
  };

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
            <span className="inspector-glyph" style={{ color }}>
              <NodeIcon name={manifest.icon} size={20} />
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
        </div>
      )}

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
          .filter((spec) => !webhookHiddenParam(manifest.id, spec.name, params))
          .map((spec) => {
            const value = params[spec.name];
            const fx = typeof value === "string" && /\{\{.+?\}\}/s.test(value);
            const displayLabel =
              webhookParamLabel(manifest.id, spec.name, params) ??
              formatParamLabel(spec.name);

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
                {spec.description && (
                  <p className="field-desc">{spec.description}</p>
                )}
                {isWebhookAuthType ? (
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
    </>
  );
}
