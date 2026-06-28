import { Code } from "@phosphor-icons/react";
import { useMemo, useRef, useState } from "react";

import { EmptyState } from "./EmptyState";
import { errorMessage } from "./api";
import { useConfirm } from "./ConfirmProvider";
import {
  useCodeModules,
  useCreateCodeModuleMutation,
  useDeleteCodeModuleMutation,
  useEnvironments,
  usePreviewCodeModuleMutation,
  useUpdateCodeModuleMutation,
  useWorkflows,
} from "./queries";
import { useToast } from "./ToastProvider";
import { useModalA11y } from "./useModalA11y";
import type {
  CodeModule,
  CodeModuleFunctionPreview,
  Environment,
  WorkflowSummary,
} from "./types";

type Scope = "all" | "global" | "environment" | "workflow";

function scopeBadge(scope: string): string {
  if (scope === "global") return "Global";
  if (scope === "environment") return "Env";
  return "Flow";
}

function scopeLabel(scope: Scope): string {
  if (scope === "all") return "All";
  if (scope === "global") return "Global";
  if (scope === "environment") return "Environments";
  return "Workflows";
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
  const [tab, setTab] = useState<Scope>("all");
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<CodeModule | null>(null);
  const [creating, setCreating] = useState(false);
  const confirm = useConfirm();
  const { notify } = useToast();
  const modulesQuery = useCodeModules();
  const workflowsQuery = useWorkflows();
  const environmentsQuery = useEnvironments();
  const deleteModule = useDeleteCodeModuleMutation();
  const modules = modulesQuery.data ?? null;
  const workflows = workflowsQuery.data ?? [];
  const environments = environmentsQuery.data ?? [];
  const error =
    modulesQuery.isError && !modulesQuery.data
      ? errorMessage(modulesQuery.error)
      : "";

  const filtered = useMemo(() => {
    if (!modules) return null;
    const needle = query.trim().toLowerCase();
    return modules.filter((module) => {
      if (tab !== "all" && module.scope !== tab) return false;
      if (!needle) return true;
      const text = [
        module.name,
        module.scope,
        module.contents,
        scopeDetail(module),
      ]
        .join(" ")
        .toLowerCase();
      return text.includes(needle);
    });
  }, [modules, query, tab]);

  const stats = useMemo(() => {
    const rows = modules ?? [];
    return {
      all: rows.length,
      global: rows.filter((item) => item.scope === "global").length,
      environment: rows.filter((item) => item.scope === "environment").length,
      workflow: rows.filter((item) => item.scope === "workflow").length,
    };
  }, [modules]);

  const envName = (id: string | null): string =>
    environments.find((e) => e.id === id)?.name ?? "(unknown env)";
  const workflowName = (id: string | null): string =>
    workflows.find((w) => w.id === id)?.name ?? "(unknown workflow)";

  function scopeDetail(m: CodeModule): string {
    if (m.scope === "global") return "Global";
    if (m.scope === "environment") return `Environment · ${envName(m.environment_id)}`;
    return `Workflow · ${workflowName(m.workflow_id)}`;
  }

  function moduleSummary(module: CodeModule): {
    functions: number;
    imports: number;
    lines: number;
  } {
    const lines = module.contents.split(/\r?\n/);
    const functions = lines.filter((line) => /^\s*def\s+\w+/.test(line)).length;
    const imports = lines.filter((line) => /^\s*(from\s+\S+\s+import|import\s+\S+)/.test(line)).length;
    return { functions, imports, lines: lines.length };
  }

  async function remove(m: CodeModule): Promise<void> {
    const ok = await confirm({
      title: `Delete ${m.name}?`,
      body: "Graphs referencing its functions will error on next run.",
    });
    if (!ok) return;
    try {
      await deleteModule.mutateAsync(m.id);
      notify("Code file deleted.", "success");
    } catch (e) {
      notify(`Could not delete code file. ${errorMessage(e)}`, "error");
    }
  }

  return (
    <div className="home">
      <main className="home-main">
        <div className="home-bar">
          <div>
            <h1>
              Code Library
              {filtered && <span className="home-count">{filtered.length}</span>}
            </h1>
            <p className="muted">
              Reusable Python functions that become workflow nodes.
            </p>
          </div>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setCreating(true)}
          >
            New file
          </button>
        </div>

        {modules && (
          <div className="codelib-summary" aria-label="Code library summary">
            {(["all", "global", "environment", "workflow"] as Scope[]).map((scope) => (
              <button
                type="button"
                key={scope}
                className={tab === scope ? "is-selected" : ""}
                onClick={() => setTab(scope)}
              >
                <strong>{stats[scope]}</strong>
                <span>{scopeLabel(scope)}</span>
              </button>
            ))}
          </div>
        )}

        <div className="codelib-toolbar">
          <input
            className="field-input"
            aria-label="Search code files"
            placeholder="Search by filename, scope, import, or function..."
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
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
                  ? "Global"
                  : s === "environment"
                    ? "Environments"
                    : "Workflows"}
            </button>
          ))}
        </div>

        {error && <p className="error-text">{error}</p>}
        {!modules && !error && (
          <div className="codelib-list" aria-label="Loading files">
            {Array.from({ length: 5 }).map((_, index) => (
              <div className="codelib-row skeleton-row" key={index}>
                <div className="codelib-main">
                  <span className="skeleton-line short" />
                  <span className="skeleton-line" />
                </div>
              </div>
            ))}
          </div>
        )}

        {filtered && filtered.length === 0 && (
          <EmptyState
            icon={<Code size={48} />}
            title="No code files found"
            description={query.trim()
              ? "Try a different search or scope."
              : "Create a Python file to expose reusable functions as nodes."
            }
            action={
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setCreating(true)}
              >
                New file
              </button>
            }
          />
        )}

        {filtered && filtered.length > 0 && (
          <div className="codelib-list">
            {filtered.map((m) => {
              const summary = moduleSummary(m);
              return (
                <article className="codelib-row" key={m.id}>
                  <span className={`codelib-badge scope-${m.scope}`} title={m.scope}>
                    {scopeBadge(m.scope)}
                  </span>
                  <div className="codelib-main">
                    <div className="codelib-name">{m.name}</div>
                    <div className="codelib-meta">
                      {scopeDetail(m)} · updated {relativeTime(m.updated_at)}
                    </div>
                    <div className="codelib-metrics">
                      <span>{summary.functions} function{summary.functions === 1 ? "" : "s"}</span>
                      <span>{summary.imports} import{summary.imports === 1 ? "" : "s"}</span>
                      <span>{summary.lines} lines</span>
                      {m.include_undecorated && <span>undecorated enabled</span>}
                    </div>
                  </div>
                  <div className="codelib-actions">
                    <button
                      type="button"
                      className="btn btn-sm"
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
                </article>
              );
            })}
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
              void modulesQuery.refetch();
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
  const [moduleId, setModuleId] = useState(initial?.id ?? null);
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
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState("");
  const { notify } = useToast();
  const createModule = useCreateCodeModuleMutation();
  const updateModule = useUpdateCodeModuleMutation();
  const previewModule = usePreviewCodeModuleMutation();
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
      if (moduleId) {
        saved = await updateModule.mutateAsync({
          id: moduleId,
          body: { name: name.trim(), contents },
        });
      } else {
        saved = await createModule.mutateAsync({
          scope,
          environment_id: scope === "environment" ? environmentId : null,
          workflow_id: scope === "workflow" ? workflowId : null,
          name: name.trim(),
          contents,
        });
        setModuleId(saved.id);
      }
      const p = await previewModule.mutateAsync(saved.id);
      setPreview(p);
      notify("Code file saved.", "success");
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  async function previewSaved(): Promise<void> {
    if (!moduleId || previewing) return;
    setPreviewing(true);
    setError("");
    try {
      setPreview(await previewModule.mutateAsync(moduleId));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPreviewing(false);
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
          <div>
            <h2 id="code-module-dialog-title">
              {moduleId ? `Edit ${name || "code file"}` : "New code file"}
            </h2>
            <p className="muted">
              Define Python functions here, then preview which functions become nodes.
            </p>
          </div>
          <button className="btn btn-sm btn-ghost" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        <div className="modal-body">
          {error && <p className="error-text">{error}</p>}

          {!moduleId && (
            <div className="field">
              <div className="field-label">
                <span className="field-name">Scope</span>
              </div>
              <select
                className="field-input"
                value={scope}
                onChange={(e) => setScope(e.target.value as CodeModule["scope"])}
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
              className="field-input field-code codelib-source"
              rows={18}
              value={contents}
              onChange={(e) => setContents(e.target.value)}
              spellCheck={false}
            />
          </div>

          {preview && (
            <div className="functions-preview codelib-preview">
              {preview.syntax_error ? (
                <p className="error-text">
                  Syntax error: {preview.syntax_error}
                </p>
              ) : (
                <>
                  <p className="muted">
                    Registered nodes: {preview.registered.length}
                    {preview.environment_name
                      ? ` · environment ${preview.environment_name}`
                      : ""}
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
                  {preview.imports.length > 0 && (
                    <p className="muted">
                      Imports: {preview.imports.join(", ")}
                    </p>
                  )}
                  {preview.missing_in_env.length > 0 && (
                    <p className="warn-text">
                      Missing in environment: {preview.missing_in_env.join(", ")}
                    </p>
                  )}
                </>
              )}
            </div>
          )}
        </div>
        <footer className="modal-foot">
          <button className="btn btn-ghost" onClick={onClose} disabled={saving}>
            Close
          </button>
          {moduleId && (
            <button
              className="btn btn-ghost"
              onClick={() => void previewSaved()}
              disabled={saving || previewing}
            >
              {previewing ? "Previewing..." : "Preview functions"}
            </button>
          )}
          <button className="btn btn-primary" onClick={() => void save()} disabled={saving}>
            {saving ? "Saving..." : moduleId ? "Save and preview" : "Create and preview"}
          </button>
        </footer>
      </div>
    </div>
  );
}
