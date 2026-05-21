import { useEffect, useState } from "react";

import { api } from "./api";
import { HomeHeader } from "./HomeHeader";
import type { Credential } from "./types";

interface Field {
  key: string;
  value: string;
}

function CreateCredentialModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState("apiKey");
  const [fields, setFields] = useState<Field[]>([{ key: "", value: "" }]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function update(index: number, patch: Partial<Field>) {
    setFields(fields.map((f, i) => (i === index ? { ...f, ...patch } : f)));
  }

  async function submit() {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError("");
    const data: Record<string, string> = {};
    for (const field of fields) {
      if (field.key.trim()) data[field.key.trim()] = field.value;
    }
    try {
      await api.createCredential({ name: name.trim(), type, data });
      onCreated();
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>New credential</h2>
        <p className="muted">Secret values are encrypted at rest.</p>
        <input
          className="field-input"
          autoFocus
          placeholder="Credential name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <select
          className="field-input"
          value={type}
          onChange={(e) => setType(e.target.value)}
        >
          <option value="apiKey">API Key</option>
          <option value="httpAuth">HTTP Auth</option>
          <option value="oauth2">OAuth2</option>
          <option value="generic">Generic</option>
        </select>
        <div className="cred-fields">
          {fields.map((field, i) => (
            <div className="cred-field-row" key={i}>
              <input
                className="field-input"
                placeholder="key"
                value={field.key}
                onChange={(e) => update(i, { key: e.target.value })}
              />
              <input
                className="field-input"
                placeholder="value"
                type="password"
                value={field.value}
                onChange={(e) => update(i, { value: e.target.value })}
              />
            </div>
          ))}
          <button
            className="btn btn-sm btn-ghost"
            onClick={() => setFields([...fields, { key: "", value: "" }])}
          >
            + Add field
          </button>
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
            {busy ? "Saving…" : "Create"}
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

  function load() {
    api
      .listCredentials()
      .then(setCredentials)
      .catch((err) => setError(String(err)));
  }

  useEffect(load, []);

  async function remove(id: string, name: string) {
    if (!window.confirm(`Delete credential “${name}”?`)) return;
    await api.deleteCredential(id);
    load();
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
          <div className="env-grid">
            {credentials.map((cred) => (
              <article className="env-card" key={cred.id}>
                <div className="env-card-head">
                  <div className="env-title">
                    <h3>{cred.name}</h3>
                  </div>
                  <span className="cred-type">{cred.type}</span>
                </div>
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
                    className="btn btn-sm btn-ghost"
                    onClick={() => void remove(cred.id, cred.name)}
                  >
                    Delete
                  </button>
                </div>
              </article>
            ))}
          </div>
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
