import { useEffect, useState } from "react";

import { api } from "./api";
import { HomeHeader } from "./HomeHeader";
import type { Environment } from "./types";

function CreateEnvModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [python, setPython] = useState("3.12");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.createEnvironment({ name: name.trim(), python_version: python });
      onCreated();
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>New environment</h2>
        <p className="muted">A custom Python venv your workflows can run in.</p>
        <input
          className="field-input"
          autoFocus
          placeholder="Environment name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void submit()}
        />
        <select
          className="field-input"
          value={python}
          onChange={(e) => setPython(e.target.value)}
        >
          <option value="3.11">Python 3.11</option>
          <option value="3.12">Python 3.12</option>
          <option value="3.13">Python 3.13</option>
        </select>
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
            {busy ? "Creating…" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}

function EnvCard({
  env,
  onChanged,
}: {
  env: Environment;
  onChanged: () => void;
}) {
  const [pkg, setPkg] = useState("");
  const [busy, setBusy] = useState(false);

  async function add() {
    if (!pkg.trim() || busy) return;
    setBusy(true);
    try {
      await api.addPackage(env.id, pkg.trim());
      setPkg("");
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function remove(name: string) {
    await api.removePackage(env.id, name);
    onChanged();
  }

  async function rebuild() {
    await api.rebuildEnvironment(env.id);
    onChanged();
  }

  async function del() {
    if (!window.confirm(`Delete environment “${env.name}”?`)) return;
    await api.deleteEnvironment(env.id);
    onChanged();
  }

  return (
    <article className="env-card">
      <div className="env-card-head">
        <div className="env-title">
          <h3>{env.name}</h3>
          {env.is_global && <span className="env-global">global</span>}
        </div>
        <span className={`env-status status-${env.status}`}>{env.status}</span>
      </div>
      <div className="env-meta">Python {env.python_version}</div>

      <div className="env-packages">
        {env.packages.length === 0 && (
          <span className="muted">No extra packages</span>
        )}
        {env.packages.map((p) => (
          <span className="pkg-chip" key={p}>
            {p}
            <button onClick={() => void remove(p)} aria-label={`remove ${p}`}>
              ×
            </button>
          </span>
        ))}
      </div>

      <div className="env-add">
        <input
          className="field-input"
          placeholder="Add a package, e.g. pandas"
          value={pkg}
          onChange={(e) => setPkg(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void add()}
        />
        <button className="btn btn-sm" onClick={() => void add()} disabled={busy}>
          Add
        </button>
      </div>

      {env.status === "error" && env.status_detail && (
        <pre className="env-log">{env.status_detail}</pre>
      )}

      <div className="env-actions">
        <button className="btn btn-sm" onClick={() => void rebuild()}>
          Rebuild
        </button>
        {!env.is_global && (
          <button className="btn btn-sm btn-ghost" onClick={() => void del()}>
            Delete
          </button>
        )}
      </div>
    </article>
  );
}

export function EnvironmentsPage() {
  const [environments, setEnvironments] = useState<Environment[] | null>(null);
  const [error, setError] = useState("");
  const [modal, setModal] = useState(false);

  function load() {
    api
      .listEnvironments()
      .then(setEnvironments)
      .catch((err) => setError(String(err)));
  }

  useEffect(load, []);

  // Poll while any environment is still building.
  useEffect(() => {
    if (!environments?.some((e) => e.status === "pending" || e.status === "building")) {
      return;
    }
    const timer = window.setTimeout(load, 2500);
    return () => window.clearTimeout(timer);
  }, [environments]);

  return (
    <div className="home">
      <HomeHeader />
      <main className="home-main">
        <div className="home-bar">
          <h1>
            Environments
            {environments && (
              <span className="home-count">{environments.length}</span>
            )}
          </h1>
          <button className="btn btn-primary" onClick={() => setModal(true)}>
            New environment
          </button>
        </div>

        {error && <p className="error-text">{error}</p>}
        {!environments && !error && <p className="muted">Loading…</p>}

        {environments && (
          <div className="env-grid">
            {environments.map((env) => (
              <EnvCard key={env.id} env={env} onChanged={load} />
            ))}
          </div>
        )}
      </main>

      {modal && (
        <CreateEnvModal
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
