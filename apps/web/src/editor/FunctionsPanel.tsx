import { useEffect, useState } from "react";

import { api } from "../api";
import type {
  CodeModule,
  CodeModuleFunctionPreview,
  Environment,
  WorkflowGraph,
} from "../types";

/** Side drawer for managing the workflow-scoped Python files whose
 *  functions show up as draggable nodes in the palette.
 *
 *  Save / Delete fire ``onChanged`` so the editor can refresh its
 *  custom-node manifests and re-render the palette.
 */
export function FunctionsPanel({
  workflowId,
  onClose,
  onChanged,
  onApplyStarterGraph,
}: {
  workflowId: string;
  onClose: () => void;
  onChanged: () => void;
  onApplyStarterGraph: (graph: WorkflowGraph) => void;
}) {
  const [modules, setModules] = useState<CodeModule[] | null>(null);
  const [selected, setSelected] = useState<CodeModule | null>(null);
  const [name, setName] = useState("");
  const [contents, setContents] = useState("");
  const [newScope, setNewScope] = useState<"workflow" | "global" | "environment">(
    "workflow",
  );
  const [newScopeEnvId, setNewScopeEnvId] = useState<string>("");
  const [preview, setPreview] = useState<CodeModuleFunctionPreview | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [installTargetEnv, setInstallTargetEnv] = useState<string>("");
  const [installing, setInstalling] = useState<string | null>(null);

  async function refresh(): Promise<void> {
    try {
      const list = await api.listCodeModules({
        visible_to_workflow: workflowId,
      });
      setModules(list);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    void refresh();
    api
      .listEnvironments()
      .then(setEnvironments)
      .catch(() => {
        /* env picker stays empty; non-fatal */
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId]);

  // When the preview tells us which env the workflow uses, default the
  // install dropdown to that one (the user can override).
  useEffect(() => {
    if (preview?.environment_id && !installTargetEnv) {
      setInstallTargetEnv(preview.environment_id);
    }
  }, [preview?.environment_id, installTargetEnv]);

  async function installPackage(pkg: string): Promise<void> {
    if (!installTargetEnv) {
      setError("Pick an environment to install into first.");
      return;
    }
    setInstalling(pkg);
    setError("");
    try {
      await api.addPackage(installTargetEnv, pkg);
      if (selected) {
        // Refresh the preview so the package drops out of missing_in_env.
        const p = await api.previewCodeModule(selected.id);
        setPreview(p);
      }
    } catch (e) {
      setError(`Failed to install ${pkg}: ${e}`);
    } finally {
      setInstalling(null);
    }
  }

  function openModule(m: CodeModule): void {
    setSelected(m);
    setName(m.name);
    setContents(m.contents);
    setPreview(null);
    setError("");
  }

  function startNew(): void {
    setSelected(null);
    setName("functions.py");
    setContents("def my_node(x: int = 0) -> int:\n    return x + 1\n");
    setPreview(null);
    setError("");
    setNewScope("workflow");
    setNewScopeEnvId("");
  }

  async function save(): Promise<void> {
    setSaving(true);
    setError("");
    try {
      let saved: CodeModule;
      if (selected) {
        saved = await api.updateCodeModule(selected.id, { name, contents });
      } else {
        if (newScope === "environment" && !newScopeEnvId) {
          throw new Error("Pick an environment for environment-scoped modules.");
        }
        saved = await api.createCodeModule({
          scope: newScope,
          workflow_id: newScope === "workflow" ? workflowId : null,
          environment_id: newScope === "environment" ? newScopeEnvId : null,
          name,
          contents,
        });
      }
      const p = await api.previewCodeModule(saved.id);
      setPreview(p);
      setSelected(saved);
      await refresh();
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function remove(m: CodeModule): Promise<void> {
    if (!confirm(`Delete ${m.name}? Nodes in the graph that reference its functions will error on the next run.`)) {
      return;
    }
    try {
      await api.deleteCodeModule(m.id);
      if (selected?.id === m.id) {
        setSelected(null);
        setName("");
        setContents("");
        setPreview(null);
      }
      await refresh();
      onChanged();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="functions-panel" role="dialog" aria-label="Workflow functions">
      <header className="functions-panel-head">
        <h2>Functions</h2>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onClose}>
          ✕
        </button>
      </header>

      <div className="functions-panel-warn">
        Uploaded Python runs in this workflow's environment. Only upload code
        you trust.
      </div>

      <div className="functions-panel-body">
        <aside className="functions-list">
          <button
            type="button"
            className="btn btn-sm functions-new"
            onClick={startNew}
          >
            + New file
          </button>
          {modules === null && <p className="muted">Loading…</p>}
          {modules?.length === 0 && (
            <p className="muted">No files yet. Click + New file to add one.</p>
          )}
          {modules?.map((m) => {
            const envName =
              m.scope === "environment"
                ? environments.find((e) => e.id === m.environment_id)?.name ??
                  "env"
                : "";
            const badge =
              m.scope === "global"
                ? "🌐"
                : m.scope === "environment"
                  ? "🐍"
                  : "📎";
            const badgeTitle =
              m.scope === "global"
                ? "Global — visible to every workflow"
                : m.scope === "environment"
                  ? `Environment scope (${envName})`
                  : "Workflow scope";
            return (
              <div
                key={m.id}
                className={`functions-item${selected?.id === m.id ? " active" : ""}`}
              >
                <span className="functions-item-scope" title={badgeTitle}>
                  {badge}
                </span>
                <button
                  type="button"
                  className="functions-item-name"
                  onClick={() => openModule(m)}
                >
                  {m.name}
                </button>
                <button
                  type="button"
                  className="functions-item-del"
                  onClick={() => void remove(m)}
                  title="Delete"
                >
                  ✕
                </button>
              </div>
            );
          })}
        </aside>

        <section className="functions-editor">
          {(selected || name || contents) ? (
            <>
              {!selected && (
                <div className="functions-scope-row">
                  <label className="muted" htmlFor="new-scope">
                    Scope
                  </label>
                  <select
                    id="new-scope"
                    className="field-input"
                    value={newScope}
                    onChange={(e) =>
                      setNewScope(
                        e.target.value as "workflow" | "global" | "environment",
                      )
                    }
                  >
                    <option value="workflow">
                      Workflow — only this workflow's palette
                    </option>
                    <option value="global">
                      Global — every workflow's palette
                    </option>
                    <option value="environment">
                      Environment — every workflow on that env
                    </option>
                  </select>
                  {newScope === "environment" && (
                    <select
                      className="field-input"
                      value={newScopeEnvId}
                      onChange={(e) => setNewScopeEnvId(e.target.value)}
                    >
                      <option value="">Pick an environment…</option>
                      {environments.map((env) => (
                        <option key={env.id} value={env.id}>
                          {env.name}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              )}
              <input
                className="field-input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="functions.py"
              />
              <textarea
                className="field-input field-code"
                value={contents}
                onChange={(e) => setContents(e.target.value)}
                spellCheck={false}
              />

              {error && <p className="error-text">{error}</p>}

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
                      <p className="muted functions-rule-hint">
                        Every parameter shows up as both a wired input port
                        (drag from upstream) and an inspector field (set a
                        literal or expression). The edge wins if both are
                        set. Required parameters (no default) must have at
                        least one of the two.
                      </p>
                      {preview.functions.length > 0 && (
                        <ul className="functions-list-funcs">
                          {preview.functions.map((fn) => (
                            <li key={fn.name}>
                              <code>{fn.name}</code>
                              {fn.inputs.length > 0 && (
                                <span className="fn-shape fn-inputs">
                                  inputs: {fn.inputs.join(", ")}
                                </span>
                              )}
                              {fn.params.length > 0 && (
                                <span className="fn-shape fn-params">
                                  params: {fn.params.join(", ")}
                                </span>
                              )}
                              {fn.inputs.length === 0 &&
                                fn.params.length === 0 && (
                                  <span className="fn-shape muted">
                                    (no parameters)
                                  </span>
                                )}
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

                  {preview.missing_in_env.length > 0 && (
                    <div className="functions-missing">
                      <p>
                        <strong>Missing imports</strong>
                        {preview.environment_name ? (
                          <> not installed in <code>{preview.environment_name}</code>:</>
                        ) : (
                          <> — this workflow has no environment assigned yet:</>
                        )}
                      </p>
                      <div className="functions-env-picker">
                        <label className="muted" htmlFor="install-env">
                          Install into
                        </label>
                        <select
                          id="install-env"
                          className="field-input"
                          value={installTargetEnv}
                          onChange={(e) => setInstallTargetEnv(e.target.value)}
                        >
                          <option value="">Select an environment…</option>
                          {environments.map((env) => (
                            <option key={env.id} value={env.id}>
                              {env.name}
                              {env.id === preview.environment_id
                                ? " (workflow env)"
                                : ""}
                            </option>
                          ))}
                        </select>
                      </div>
                      <ul className="functions-missing-list">
                        {preview.missing_in_env.map((pkg) => (
                          <li key={pkg}>
                            <code>{pkg}</code>
                            <button
                              type="button"
                              className="btn btn-sm"
                              onClick={() => void installPackage(pkg)}
                              disabled={installing === pkg || !installTargetEnv}
                            >
                              {installing === pkg ? "Installing…" : "Install"}
                            </button>
                          </li>
                        ))}
                      </ul>
                      <p className="muted functions-missing-hint">
                        If the pip name differs from the import (e.g.{" "}
                        <code>cv2</code> → <code>opencv-python</code>), edit the
                        package in the Environments page after install.
                      </p>
                    </div>
                  )}
                </div>
              )}

              <div className="functions-actions">
                {selected && preview && preview.registered.length > 0 && (
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={async () => {
                      if (
                        !confirm(
                          "Replace the current workflow graph with a starter graph derived from this file?",
                        )
                      ) {
                        return;
                      }
                      try {
                        const graph = await api.starterGraph(selected.id);
                        onApplyStarterGraph(graph);
                        onClose();
                      } catch (e) {
                        setError(String(e));
                      }
                    }}
                  >
                    ✨ Build starter graph
                  </button>
                )}
                <button
                  type="button"
                  className="btn"
                  onClick={() => void save()}
                  disabled={saving}
                >
                  {saving ? "Saving…" : selected ? "Save changes" : "Upload"}
                </button>
              </div>
            </>
          ) : (
            <div className="functions-empty muted">
              <p>
                Select a file on the left, or click <strong>+ New file</strong>
                {" "}to upload Python. Each top-level function becomes a node
                you can drag from the palette's Custom group.
              </p>
              <pre className="data-json">
                {`def my_node(x: int = 0) -> int:
    """Description shows up in the inspector."""
    return x + 1`}
              </pre>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
