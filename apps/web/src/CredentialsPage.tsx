import { Key } from "@phosphor-icons/react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useQueryClient } from "@tanstack/react-query";

import { EmptyState } from "./EmptyState";

import { api, userFriendlyError } from "./api";
import { recordActivationEvent } from "./activation";
import { useConfirm } from "./ConfirmProvider";
import {
  CredentialFieldInput,
  CREDENTIAL_PRESETS,
  LLM_FIELD_DEFS,
  PRESET_BY_TYPE,
  mergedCredentialPresets,
  presetInitialValues,
  type CredentialFormField,
  type CredentialPreset,
  type CredentialScope,
} from "./credentialPresets";
import {
  LLM_PROVIDER_VARIANTS,
  getLlmVariant,
  visibleCredentialFields,
} from "./llmProviders";
import {
  queryKeys,
  useCredentialTypes,
  useCredentials,
  useDeleteCredentialMutation,
  useRefreshCredentialMutation,
  useTestCredentialMutation,
} from "./queries";
import { useToast } from "./ToastProvider";
import { useModalA11y } from "./useModalA11y";
import type { Credential, CredentialTestResponse, CredentialTypeInfo } from "./types";

interface Field {
  key: string;
  value: string;
}


function credentialTypeLabel(
  type: string,
  presetsByType: Map<string, CredentialPreset> = PRESET_BY_TYPE,
): string {
  return presetsByType.get(type)?.label ?? type;
}

function credTestStatusColor(
  credId: string,
  testResults: Record<string, CredentialTestResponse>,
): string {
  const result = testResults[credId];
  if (!result) return "var(--color-warning)";
  return result.ok ? "var(--color-success)" : "var(--color-error)";
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
      if (type === "nodyra_oauth_success") {
        setOauthStarted("");
        recordActivationEvent("credential_connected");
        notify(`Connected${msg ? ` — ${msg.replace(/^Connected — /, "")}` : ""}`, "success");
        oauthPopupRef.current = null;
        onCreated();
      } else if (type === "nodyra_oauth_error") {
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
        "nodyra_oauth",
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
      // OAuth start happens inside the create form → inline error near the form.
      setError(userFriendlyError(err));
    } finally {
      setBusy(false);
    }
  }

  function renderField(field: CredentialFormField) {
    return (
      <CredentialFieldInput
        key={field.key}
        field={field}
        value={values[field.key] ?? ""}
        onChange={(next) => setValues({ ...values, [field.key]: next })}
      />
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
      notify(userFriendlyError(err), "error");
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
      recordActivationEvent("credential_connected");
      onCreated();
    } catch (err) {
      setError(userFriendlyError(err));
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
              Choose the service first. Nodyra only asks for fields this
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
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const credentialsQuery = useCredentials();
  const credentialTypesQuery = useCredentialTypes();
  const deleteCredentialMutation = useDeleteCredentialMutation();
  const refreshCredentialMutation = useRefreshCredentialMutation();
  const testCredentialMutation = useTestCredentialMutation();
  const credentials = credentialsQuery.data ?? null;
  const credentialTypes: CredentialTypeInfo[] | null =
    credentialTypesQuery.data ?? null;
  const error =
    credentialsQuery.isError && !credentialsQuery.data
      ? userFriendlyError(credentialsQuery.error)
      : "";
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

  async function remove(id: string, name: string) {
    const ok = await confirm({
      title: "Delete credential?",
      body: `“${name}” will be permanently removed. Nodes using it will fail until reconfigured.`,
    });
    if (!ok) return;
    try {
      await deleteCredentialMutation.mutateAsync(id);
      notify("Credential deleted.", "success");
    } catch (err) {
      notify(`Could not delete credential. ${userFriendlyError(err)}`, "error");
    }
  }

  async function testCredential(cred: Credential): Promise<void> {
    setTesting((current) => ({ ...current, [cred.id]: true }));
    try {
      const result = await testCredentialMutation.mutateAsync({
        id: cred.id,
        body: {
          workflow_id: cred.workflow_id,
          environment_id: cred.environment_id,
          runner_pool_id: cred.runner_pool_id,
          context: {},
        },
      });
      setTestResults((current) => ({ ...current, [cred.id]: result }));
      notify(
        result.ok ? "Credential connected." : result.message,
        result.ok ? "success" : "error",
      );
    } catch (err) {
      notify(userFriendlyError(err), "error");
    } finally {
      setTesting((current) => ({ ...current, [cred.id]: false }));
    }
  }

  async function refreshCredential(cred: Credential): Promise<void> {
    setRefreshing((current) => ({ ...current, [cred.id]: true }));
    try {
      await refreshCredentialMutation.mutateAsync(cred.id);
      notify("Credential refreshed.", "success");
    } catch (err) {
      notify(userFriendlyError(err), "error");
    } finally {
      setRefreshing((current) => ({ ...current, [cred.id]: false }));
    }
  }

  return (
    <div className="home">
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
        {!credentials && !error && (
          <div className="env-grid" aria-label="Loading credentials">
            {Array.from({ length: 6 }).map((_, index) => (
              <article className="env-card skeleton-card" key={index}>
                <span className="skeleton-line short" />
                <span className="skeleton-line title" />
                <span className="skeleton-line" />
              </article>
            ))}
          </div>
        )}

        {credentials && credentials.length === 0 && (
          <EmptyState
            icon={<Key size={48} />}
            title="No credentials yet"
            description="Store API keys and secrets here — they are encrypted at rest and never shown again."
            action={
              <button className="btn btn-primary" onClick={() => setModal(true)}>
                New credential
              </button>
            }
          />
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
                aria-label="Filter credentials by type"
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
                aria-label="Filter credentials by scope"
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
                    <span
                      className="credential-status-dot"
                      style={{ background: credTestStatusColor(cred.id, testResults) }}
                    />
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
            void queryClient.invalidateQueries({ queryKey: queryKeys.credentials });
          }}
        />
      )}
    </div>
  );
}
