import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "./api";
import { HomeHeader } from "./HomeHeader";
import { useModalA11y } from "./useModalA11y";
import type {
  CodeModule,
  CodeModuleFunctionPreview,
  Environment,
  WorkflowSummary,
} from "./types";

type Scope = "all" | "global" | "environment" | "workflow";

function scopeBadge(scope: string): string {
  if (scope === "global") return "🌐";
  if (scope === "environment") return "🐍";
  return "📎";
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.round(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return new Date(iso).toLocaleString();
}

export function CodeLibraryPage() {
  const [modules, setModules] = useState<CodeModule[] | null>(null);
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [tab, setTab] = useState<Scope>("all");
  const [editing, setEditing] = useState<CodeModule | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  async function refresh(): Promise<void> {
    try {
      const list = await api.listCodeModules();
      setModules(list);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    void refresh();
    api.listWorkflows().then(setWorkflows).catch(() => {});
    api.listEnvironments().then(setEnvironments).catch(() => {});
  }, []);

  const filtered = useMemo(() => {
    if (!modules) return null;
    if (tab === "all") return modules;
    return modules.filter((m) => m.scope === tab);
  }, [modules, tab]);

  const envName = (id: string | null): string =>
    environments.find((e) => e.id === id)?.name ?? "(unknown env)";
  const workflowName = (id: string | null): string =>
    workflows.find((w) => w.id === id)?.name ?? "(unknown workflow)";

  function scopeDetail(m: CodeModule): string {
    if (m.scope === "global") return "Global";
    if (m.scope === "environment") return `Environment · ${envName(m.environment_id)}`;
    return `Workflow · ${workflowName(m.workflow_id)}`;
  }

  async function remove(m: CodeModule): Promise<void> {
    if (!confirm(`Delete ${m.name}? Graphs referencing its functions will error on next run.`)) {
      return;
    }
    try {
      await api.deleteCodeModule(m.id);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="home">
      <HomeHeader />
      <main className="home-main">
        <div className="home-bar">
          <h1>
            Code Library
            {filtered && <span className="home-count">{filtered.length}</span>}
          </h1>
          <button
            type="button"
            className="btn"
            onClick={() => setCreating(true)}
          >
            + New file
          </button>
        </div>

        <div className="codelib-tabs">
          {(["all", "global", "environment", "workflow"] as Scope[]).map((s) => (
            <button
              key={s}
              type="button"
              className={tab === s ? "active" : ""}
              onClick={() => setTab(s)}
            >
              {s === "all"
                ? "All"
                : s === "global"
                  ? "🌐 Global"
                  : s === "environment"
                    ? "🐍 Environments"
                    : "📎 Workflows"}
            </button>
          ))}
        </div>

        {error && <p className="error-text">{error}</p>}
        {!modules && !error && <p className="muted">Loading…</p>}

        {filtered && filtered.length === 0 && (
          <p className="muted">
            No files in this scope. Click <strong>+ New file</strong> to add one.
          </p>
        )}

        {filtered && filtered.length > 0 && (
          <div className="codelib-list">
            {filtered.map((m) => (
              <div className="codelib-row" key={m.id}>
                <span className="codelib-badge" title={m.scope}>
                  {scopeBadge(m.scope)}
                </span>
                <div className="codelib-main">
                  <div className="codelib-name">{m.name}</div>
                  <div className="codelib-meta">
                    {scopeDetail(m)} · updated {relativeTime(m.updated_at)}
                  </div>
                </div>
                <div className="codelib-actions">
                  <button
                    type="button"
                    className="btn btn-sm btn-ghost"
                    onClick={() => setEditing(m)}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-ghost"
                    onClick={() => void remove(m)}
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {(editing || creating) && (
          <CodeModuleDialog
            initial={editing}
            workflows={workflows}
            environments={environments}
            onClose={() => {
              setEditing(null);
              setCreating(false);
            }}
            onSaved={() => {
              setEditing(null);
              setCreating(false);
              void refresh();
            }}
          />
        )}
      </main>
    </div>
  );
}

function CodeModuleDialog({
  initial,
  workflows,
  environments,
  onClose,
  onSaved,
}: {
  initial: CodeModule | null;
  workflows: WorkflowSummary[];
  environments: Environment[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "functions.py");
  const [contents, setContents] = useState(
    initial?.contents ?? "def my_node(x: int = 0) -> int:\n    return x + 1\n",
  );
  const [scope, setScope] = useState(initial?.scope ?? "global");
  const [environmentId, setEnvironmentId] = useState(
    initial?.environment_id ?? "",
  );
  const [workflowId, setWorkflowId] = useState(initial?.workflow_id ?? "");
  const [preview, setPreview] = useState<CodeModuleFunctionPreview | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const dialogRef = useRef<HTMLDivElement>(null);
  // trapFocus:false — the body embeds a code textarea that owns Tab.
  useModalA11y(dialogRef, onClose, { trapFocus: false });

  async function save(): Promise<void> {
    setError("");
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    if (scope === "environment" && !environmentId) {
      setError("Pick an environment for environment-scoped modules.");
      return;
    }
    if (scope === "workflow" && !workflowId) {
      setError("Pick a workflow for workflow-scoped modules.");
      return;
    }
    setSaving(true);
    try {
      let saved: CodeModule;
      if (initial) {
        saved = await api.updateCodeModule(initial.id, { name, contents });
      } else {
        saved = await api.createCodeModule({
          scope,
          environment_id: scope === "environment" ? environmentId : null,
          workflow_id: scope === "workflow" ? workflowId : null,
          name,
          contents,
        });
      }
      const p = await api.previewCodeModule(saved.id);
      setPreview(p);
      onSaved();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal modal-wide"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="code-module-dialog-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <h2 id="code-module-dialog-title">{initial ? `Edit ${initial.name}` : "New file"}</h2>
          <button className="btn btn-sm btn-ghost" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        <div className="modal-body">
          {error && <p className="error-text">{error}</p>}

          {!initial && (
            <div className="field">
              <div className="field-label">
                <span className="field-name">Scope</span>
              </div>
              <select
                className="field-input"
                value={scope}
                onChange={(e) => setScope(e.target.value)}
              >
                <option value="global">Global — every workflow</option>
                <option value="environment">Environment</option>
                <option value="workflow">Workflow</option>
              </select>
              {scope === "environment" && (
                <select
                  className="field-input"
                  style={{ marginTop: 8 }}
                  value={environmentId ?? ""}
                  onChange={(e) => setEnvironmentId(e.target.value)}
                >
                  <option value="">Pick an environment…</option>
                  {environments.map((env) => (
                    <option key={env.id} value={env.id}>
                      {env.name}
                    </option>
                  ))}
                </select>
              )}
              {scope === "workflow" && (
                <select
                  className="field-input"
                  style={{ marginTop: 8 }}
                  value={workflowId ?? ""}
                  onChange={(e) => setWorkflowId(e.target.value)}
                >
                  <option value="">Pick a workflow…</option>
                  {workflows.map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.name}
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}

          <div className="field">
            <div className="field-label">
              <span className="field-name">Name</span>
            </div>
            <input
              className="field-input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="functions.py"
            />
          </div>

          <div className="field">
            <div className="field-label">
              <span className="field-name">Source</span>
            </div>
            <textarea
              className="field-input field-code"
              rows={16}
              value={contents}
              onChange={(e) => setContents(e.target.value)}
              spellCheck={false}
            />
          </div>

          {preview && (
            <div className="functions-preview">
              {preview.syntax_error ? (
                <p className="error-text">
                  Syntax error: {preview.syntax_error}
                </p>
              ) : (
                <>
                  <p className="muted">
                    Registered nodes: {preview.registered.length}
                  </p>
                  {preview.registered.length > 0 && (
                    <ul className="functions-list-funcs">
                      {preview.registered.map((fn) => (
                        <li key={fn}>
                          <code>{fn}</code>
                        </li>
                      ))}
                    </ul>
                  )}
                  {preview.skipped.length > 0 && (
                    <p className="muted">
                      Skipped:{" "}
                      {preview.skipped
                        .map((s) => `${s.name} (${s.reason})`)
                        .join(", ")}
                    </p>
                  )}
                </>
              )}
            </div>
          )}
        </div>
        <footer className="modal-foot">
          <button className="btn btn-ghost" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button className="btn" onClick={() => void save()} disabled={saving}>
            {saving ? "Saving…" : initial ? "Save changes" : "Create file"}
          </button>
        </footer>
      </div>
    </div>
  );
}
