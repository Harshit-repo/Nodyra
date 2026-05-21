import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "./api";
import { HomeHeader } from "./HomeHeader";
import { Logo } from "./Logo";
import type { WorkflowSummary } from "./types";

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  const mins = Math.round(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

function CreateModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [name, setName] = useState("Untitled workflow");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const created = await api.createWorkflow(name.trim() || "Untitled workflow");
      onCreated(created.id);
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>New workflow</h2>
        <p className="muted">Give your automation a name. You can rename it later.</p>
        <input
          className="field-input"
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void submit()}
        />
        {error && <p className="error-text">{error}</p>}
        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? "Creating…" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function WorkflowsPage() {
  const [workflows, setWorkflows] = useState<WorkflowSummary[] | null>(null);
  const [error, setError] = useState("");
  const [modal, setModal] = useState(false);
  const navigate = useNavigate();

  function load() {
    api
      .listWorkflows()
      .then(setWorkflows)
      .catch((err) => setError(String(err)));
  }

  useEffect(load, []);

  async function remove(id: string, name: string) {
    if (!window.confirm(`Delete “${name}”? This cannot be undone.`)) return;
    try {
      await api.deleteWorkflow(id);
      load();
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <div className="home">
      <HomeHeader />

      <main className="home-main">
        <div className="home-bar">
          <h1>
            Workflows
            {workflows && <span className="home-count">{workflows.length}</span>}
          </h1>
          <button className="btn btn-primary" onClick={() => setModal(true)}>
            New workflow
          </button>
        </div>

        {error && <p className="error-text">{error}</p>}

        {!workflows && !error && <p className="muted">Loading…</p>}

        {workflows && workflows.length === 0 && (
          <div className="empty-state">
            <Logo size={44} />
            <h2>No workflows yet</h2>
            <p className="muted">
              Create your first automation and start wiring Python nodes together.
            </p>
            <button className="btn btn-primary" onClick={() => setModal(true)}>
              New workflow
            </button>
          </div>
        )}

        {workflows && workflows.length > 0 && (
          <div className="wf-grid">
            {workflows.map((wf) => (
              <article
                key={wf.id}
                className="wf-card"
                onClick={() => navigate(`/workflows/${wf.id}`)}
              >
                <div className="wf-card-top">
                  <span className={`wf-status ${wf.active ? "on" : "off"}`}>
                    {wf.active ? "active" : "inactive"}
                  </span>
                  <button
                    className="wf-delete"
                    title="Delete workflow"
                    onClick={(e) => {
                      e.stopPropagation();
                      void remove(wf.id, wf.name);
                    }}
                  >
                    ×
                  </button>
                </div>
                <h3 className="wf-name">{wf.name}</h3>
                <div className="wf-meta">
                  <span>
                    {wf.node_count} node{wf.node_count === 1 ? "" : "s"}
                  </span>
                  <span className="dot-sep" />
                  <span>v{wf.version}</span>
                  <span className="dot-sep" />
                  <span>{relativeTime(wf.updated_at)}</span>
                </div>
              </article>
            ))}
          </div>
        )}
      </main>

      {modal && (
        <CreateModal
          onClose={() => setModal(false)}
          onCreated={(id) => navigate(`/workflows/${id}`)}
        />
      )}
    </div>
  );
}
