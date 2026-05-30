import { useCallback, useEffect, useState } from "react";

import { runnerPoolsApi } from "./api";
import { HomeHeader } from "./HomeHeader";
import { useCan } from "./permissions";
import type { RegistrationTokenResponse, RunnerInfo, RunnerPoolInfo } from "./types";

type Config = Record<string, unknown>;

/** Drop empty-string / empty-array values so the stored config stays clean. */
function cleanConfig(config: Config): Config {
  const out: Config = {};
  for (const [key, value] of Object.entries(config)) {
    if (value === "" || value === null || value === undefined) continue;
    if (Array.isArray(value) && value.length === 0) continue;
    out[key] = value;
  }
  return out;
}

function poolConfigSummary(pool: RunnerPoolInfo): string | null {
  const cfg = pool.provider_config || {};
  if (pool.provider === "docker") {
    return `host ${(cfg.docker_host as string) || "local socket"}`;
  }
  if (pool.provider === "kubernetes") {
    return `ns ${(cfg.namespace as string) || "noodle"}`;
  }
  if (cfg.cloud_provider === "aws") {
    return `auto-scale AWS ≤ ${(cfg.max_instances as number) ?? "?"}`;
  }
  return null;
}

function Field({
  label,
  desc,
  children,
}: {
  label: string;
  desc?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="field">
      <div className="field-label">
        <span className="field-name">{label}</span>
      </div>
      {desc && <p className="field-desc">{desc}</p>}
      {children}
    </div>
  );
}

function TextField({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  desc,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  desc?: string;
}) {
  return (
    <Field label={label} desc={desc}>
      <input
        className="field-input"
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </Field>
  );
}

/** Provider-specific configuration inputs that read/write into `config`. */
function ProviderConfigFields({
  provider,
  config,
  onChange,
}: {
  provider: string;
  config: Config;
  onChange: (next: Config) => void;
}) {
  const set = (key: string, value: unknown) =>
    onChange({ ...config, [key]: value });
  const str = (key: string) => (config[key] as string) ?? "";

  if (provider === "docker") {
    return (
      <>
        <TextField
          label="Docker host"
          desc="Blank uses the local Docker socket."
          value={str("docker_host")}
          onChange={(v) => set("docker_host", v)}
          placeholder="tcp://dockerd:2375"
        />
        <TextField
          label="Network"
          value={str("network") || "bridge"}
          onChange={(v) => set("network", v)}
          placeholder="bridge"
        />
      </>
    );
  }

  if (provider === "kubernetes") {
    return (
      <>
        <TextField
          label="Namespace"
          value={str("namespace") || "noodle"}
          onChange={(v) => set("namespace", v)}
          placeholder="noodle"
        />
        <TextField
          label="Image registry"
          desc="Blank uses in-cluster images."
          value={str("image_registry")}
          onChange={(v) => set("image_registry", v)}
          placeholder="registry.example.com/noodle"
        />
        <Field
          label="Kubeconfig YAML"
          desc="Blank uses the in-cluster service account."
        >
          <textarea
            className="field-input field-textarea"
            value={str("kubeconfig_yaml")}
            onChange={(e) => set("kubeconfig_yaml", e.target.value)}
            placeholder="apiVersion: v1&#10;clusters: ..."
          />
        </Field>
      </>
    );
  }

  // agent — optional AWS auto-scaling
  const cloudOn = config.cloud_provider === "aws";
  return (
    <>
      <label className="cloud-toggle">
        <input
          type="checkbox"
          checked={cloudOn}
          onChange={(e) => {
            if (e.target.checked) {
              set("cloud_provider", "aws");
            } else {
              const next = { ...config };
              delete next.cloud_provider;
              onChange(next);
            }
          }}
        />
        Auto-scale with AWS (provision EC2 runners on demand)
      </label>
      {cloudOn && (
        <div className="cloud-fields">
          <div className="field-row">
            <TextField
              label="Region"
              value={str("region") || "us-east-1"}
              onChange={(v) => set("region", v)}
            />
            <TextField
              label="Instance type"
              value={str("instance_type") || "t3.medium"}
              onChange={(v) => set("instance_type", v)}
            />
          </div>
          <TextField
            label="AMI id"
            value={str("ami_id")}
            onChange={(v) => set("ami_id", v)}
            placeholder="ami-0123456789abcdef0"
          />
          <TextField
            label="Key pair"
            desc="Optional."
            value={str("key_pair")}
            onChange={(v) => set("key_pair", v)}
          />
          <TextField
            label="Security group ids"
            desc="Comma-separated."
            value={
              Array.isArray(config.security_group_ids)
                ? (config.security_group_ids as string[]).join(", ")
                : ""
            }
            onChange={(v) =>
              set(
                "security_group_ids",
                v.split(",").map((s) => s.trim()).filter(Boolean),
              )
            }
            placeholder="sg-abc123, sg-def456"
          />
          <div className="field-row">
            <TextField
              label="Max instances"
              type="number"
              value={String((config.max_instances as number) ?? 3)}
              onChange={(v) => set("max_instances", Number(v) || 0)}
            />
            <TextField
              label="Idle terminate (s)"
              type="number"
              value={String((config.idle_terminate_seconds as number) ?? 300)}
              onChange={(v) => set("idle_terminate_seconds", Number(v) || 0)}
            />
          </div>
          <TextField
            label="AWS access key id"
            value={str("aws_access_key_id")}
            onChange={(v) => set("aws_access_key_id", v)}
          />
          <TextField
            label="AWS secret access key"
            type="password"
            desc="Stored in the pool config. Blank uses the API host's IAM role."
            value={str("aws_secret_access_key")}
            onChange={(v) => set("aws_secret_access_key", v)}
          />
        </div>
      )}
    </>
  );
}

/** Serialize a labels map to lines (``key=value``) for the textarea input. */
function labelsToText(labels: Record<string, unknown>): string {
  return Object.entries(labels)
    .map(([k, v]) => `${k}=${String(v)}`)
    .join("\n");
}

/** Parse a ``key=value`` (one per line) textarea back into a labels map.
 * Blank lines and lines without ``=`` are silently dropped so the field stays
 * forgiving when the operator pastes in messy YAML-ish input. */
function parseLabels(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const eq = line.indexOf("=");
    if (eq < 0) continue;
    const k = line.slice(0, eq).trim();
    const v = line.slice(eq + 1).trim();
    if (k) out[k] = v;
  }
  return out;
}

/** Reusable machine-details panel: friendly name, max concurrent, labels.
 * Shared by the add-machine, SSH-onboard, and edit-runner dialogs so every
 * surface that touches a runner row collects the same metadata. */
function MachineDetailsFields({
  name,
  setName,
  maxConcurrent,
  setMaxConcurrent,
  labelsText,
  setLabelsText,
  nameLabel = "Machine name",
  namePlaceholder = "ci-worker-3",
  nameDesc = "Friendly name shown in the runners list (e.g. host or role).",
}: {
  name: string;
  setName: (v: string) => void;
  maxConcurrent: string;
  setMaxConcurrent: (v: string) => void;
  labelsText: string;
  setLabelsText: (v: string) => void;
  nameLabel?: string;
  namePlaceholder?: string;
  nameDesc?: string;
}) {
  return (
    <>
      <TextField
        label={nameLabel}
        desc={nameDesc}
        value={name}
        onChange={setName}
        placeholder={namePlaceholder}
      />
      <TextField
        label="Max concurrent runs"
        desc="How many workflow runs this single machine will accept in parallel."
        type="number"
        value={maxConcurrent}
        onChange={setMaxConcurrent}
      />
      <Field
        label="Labels"
        desc="Free-form key=value lines (e.g. region=eu, gpu=a100). Used by future label-aware dispatch."
      >
        <textarea
          className="field-input field-textarea"
          value={labelsText}
          onChange={(e) => setLabelsText(e.target.value)}
          placeholder={"region=eu\ngpu=a100"}
        />
      </Field>
    </>
  );
}

/** Shared create/edit modal. `pool` set = edit mode (provider is fixed). */
function PoolDialog({
  pool,
  onClose,
  onSaved,
}: {
  pool?: RunnerPoolInfo;
  onClose: () => void;
  onSaved: () => void;
}) {
  const editing = !!pool;
  const [name, setName] = useState(pool?.name ?? "");
  const [provider, setProvider] = useState(pool?.provider ?? "agent");
  const [maxConcurrent, setMaxConcurrent] = useState(
    pool?.max_concurrent_runs ?? 4,
  );
  const [config, setConfig] = useState<Config>(pool?.provider_config ?? {});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      if (editing) {
        await runnerPoolsApi.update(pool!.id, {
          name: name.trim(),
          max_concurrent_runs: maxConcurrent,
          provider_config: cleanConfig(config),
        });
      } else {
        await runnerPoolsApi.create({
          name: name.trim(),
          provider,
          max_concurrent_runs: maxConcurrent,
          provider_config: cleanConfig(config),
        });
      }
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save pool");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <h2>{editing ? `Edit ${pool!.name}` : "New runner pool"}</h2>
          <button className="btn btn-sm btn-ghost" onClick={onClose}>
            ✕
          </button>
        </header>
        <div className="modal-body">
          {error && <p className="error-text">{error}</p>}

          <TextField
            label="Name"
            value={name}
            onChange={setName}
            placeholder="production-pool"
          />

          <div className="field-row">
            <Field label="Provider">
              {editing ? (
                <input
                  className="field-input"
                  value={provider}
                  disabled
                  style={{ textTransform: "capitalize" }}
                />
              ) : (
                <select
                  className="field-input"
                  value={provider}
                  onChange={(e) => {
                    setProvider(e.target.value);
                    setConfig({});
                  }}
                >
                  <option value="agent">Agent (VM / EC2)</option>
                  <option value="docker">Docker</option>
                  <option value="kubernetes">Kubernetes</option>
                </select>
              )}
            </Field>
            <TextField
              label="Max concurrent"
              type="number"
              value={String(maxConcurrent)}
              onChange={(v) => setMaxConcurrent(Number(v) || 1)}
            />
          </div>

          <ProviderConfigFields
            provider={provider}
            config={config}
            onChange={setConfig}
          />
        </div>
        <footer className="modal-foot">
          <button className="btn btn-sm btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn btn-sm btn-primary"
            disabled={saving || !name.trim()}
            onClick={handleSave}
          >
            {saving ? "Saving…" : editing ? "Save changes" : "Create pool"}
          </button>
        </footer>
      </div>
    </div>
  );
}

function SSHOnboardDialog({
  poolId,
  onClose,
  onDone,
}: {
  poolId: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const [host, setHost] = useState("");
  const [port, setPort] = useState("22");
  const [username, setUsername] = useState("");
  const [authMethod, setAuthMethod] = useState<"key" | "password">("key");
  const [password, setPassword] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [apiUrl, setApiUrl] = useState(window.location.origin);
  const [useSystemd, setUseSystemd] = useState(true);
  const [name, setName] = useState("");
  const [maxConcurrent, setMaxConcurrent] = useState("1");
  const [labelsText, setLabelsText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [log, setLog] = useState<string | null>(null);

  const submit = async () => {
    if (!host.trim() || !username.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const labels = parseLabels(labelsText);
      const res = await runnerPoolsApi.sshOnboard(poolId, {
        host: host.trim(),
        port: Number(port) || 22,
        username: username.trim(),
        auth_method: authMethod,
        password: authMethod === "password" ? password : undefined,
        private_key: authMethod === "key" ? privateKey : undefined,
        passphrase: authMethod === "key" ? passphrase || undefined : undefined,
        api_url: apiUrl.trim() || undefined,
        use_systemd: useSystemd,
        name: name.trim() || undefined,
        max_concurrent_runs: Number(maxConcurrent) || 1,
        capabilities: Object.keys(labels).length ? labels : undefined,
      });
      setLog(res.install_log || "Onboarded.");
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "SSH onboarding failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <h2>Onboard a machine over SSH</h2>
          <button className="btn btn-sm btn-ghost" onClick={onClose}>
            ✕
          </button>
        </header>
        <div className="modal-body">
          {error && <p className="error-text">{error}</p>}
          {log ? (
            <Field label="Install log">
              <pre className="runner-install">{log}</pre>
            </Field>
          ) : (
            <>
              <div className="field-row">
                <TextField label="Host" value={host} onChange={setHost} placeholder="10.0.0.5" />
                <TextField label="Port" type="number" value={port} onChange={setPort} />
              </div>
              <TextField label="Username" value={username} onChange={setUsername} placeholder="ubuntu" />
              <Field label="Authentication">
                <select
                  className="field-input"
                  value={authMethod}
                  onChange={(e) => setAuthMethod(e.target.value as "key" | "password")}
                >
                  <option value="key">Private key</option>
                  <option value="password">Password</option>
                </select>
              </Field>
              {authMethod === "key" ? (
                <>
                  <Field label="Private key" desc="PEM/OpenSSH private key contents.">
                    <textarea
                      className="field-input field-textarea"
                      value={privateKey}
                      onChange={(e) => setPrivateKey(e.target.value)}
                      placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
                    />
                  </Field>
                  <TextField
                    label="Passphrase"
                    type="password"
                    desc="Optional, if the key is encrypted."
                    value={passphrase}
                    onChange={setPassphrase}
                  />
                </>
              ) : (
                <TextField
                  label="Password"
                  type="password"
                  value={password}
                  onChange={setPassword}
                />
              )}
              <TextField
                label="API URL"
                desc="The URL the runner connects back to (must be reachable from the host)."
                value={apiUrl}
                onChange={setApiUrl}
              />
              <label className="cloud-toggle">
                <input
                  type="checkbox"
                  checked={useSystemd}
                  onChange={(e) => setUseSystemd(e.target.checked)}
                />
                Install as a systemd service (survives reboot; falls back to nohup)
              </label>

              <MachineDetailsFields
                name={name}
                setName={setName}
                maxConcurrent={maxConcurrent}
                setMaxConcurrent={setMaxConcurrent}
                labelsText={labelsText}
                setLabelsText={setLabelsText}
                nameLabel="Machine name (optional)"
                namePlaceholder="ssh-<host>"
                nameDesc="Defaults to ssh-<host>. Override to give the runner a friendlier name."
              />
            </>
          )}
        </div>
        <footer className="modal-foot">
          <button className="btn btn-sm btn-ghost" onClick={onClose}>
            {log ? "Close" : "Cancel"}
          </button>
          {!log && (
            <button
              className="btn btn-sm btn-primary"
              disabled={busy || !host.trim() || !username.trim()}
              onClick={submit}
            >
              {busy ? "Onboarding…" : "Onboard"}
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}

function AddMachineDialog({
  poolId,
  onClose,
  onDone,
}: {
  poolId: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const [name, setName] = useState("");
  const [maxConcurrent, setMaxConcurrent] = useState("1");
  const [labelsText, setLabelsText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState<RegistrationTokenResponse | null>(null);
  const [copied, setCopied] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const labels = parseLabels(labelsText);
      const res = await runnerPoolsApi.createRegistrationToken(poolId, {
        name: name.trim() || undefined,
        max_concurrent_runs: Number(maxConcurrent) || 1,
        capabilities: Object.keys(labels).length ? labels : undefined,
      });
      setToken(res);
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to mint token");
    } finally {
      setBusy(false);
    }
  };

  const installCmd = token
    ? `pip install noodle-runner
noodle-runner register \\
  --api-url ${window.location.origin} \\
  --token ${token.token} \\
  --name ${name.trim() || "my-runner"}
noodle-runner start`
    : "";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(installCmd);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked */
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <h2>Add a machine to this pool</h2>
          <button className="btn btn-sm btn-ghost" onClick={onClose}>
            ✕
          </button>
        </header>
        <div className="modal-body">
          {error && <p className="error-text">{error}</p>}
          {token ? (
            <>
              <p className="muted">
                Run the snippet below on the machine you want to register. The
                token expires{" "}
                {new Date(token.expires_at).toLocaleString()}.
              </p>
              <div className="runner-install">
                <div className="runner-install-head">
                  <strong>Install and register</strong>
                  <button
                    type="button"
                    className="btn btn-sm btn-ghost"
                    onClick={copy}
                  >
                    {copied ? "Copied!" : "Copy"}
                  </button>
                </div>
                <pre>{installCmd}</pre>
              </div>
            </>
          ) : (
            <MachineDetailsFields
              name={name}
              setName={setName}
              maxConcurrent={maxConcurrent}
              setMaxConcurrent={setMaxConcurrent}
              labelsText={labelsText}
              setLabelsText={setLabelsText}
            />
          )}
        </div>
        <footer className="modal-foot">
          <button className="btn btn-sm btn-ghost" onClick={onClose}>
            {token ? "Done" : "Cancel"}
          </button>
          {!token && (
            <button
              className="btn btn-sm btn-primary"
              disabled={busy}
              onClick={submit}
            >
              {busy ? "Minting…" : "Mint install token"}
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}

function EditRunnerDialog({
  poolId,
  runner,
  onClose,
  onSaved,
}: {
  poolId: string;
  runner: RunnerInfo;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(runner.name);
  const [maxConcurrent, setMaxConcurrent] = useState(
    String(runner.max_concurrent_runs),
  );
  const [labelsText, setLabelsText] = useState(labelsToText(runner.capabilities));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await runnerPoolsApi.updateRunner(poolId, runner.id, {
        name: name.trim(),
        max_concurrent_runs: Number(maxConcurrent) || 1,
        capabilities: parseLabels(labelsText),
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save runner");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <h2>Edit {runner.name}</h2>
          <button className="btn btn-sm btn-ghost" onClick={onClose}>
            ✕
          </button>
        </header>
        <div className="modal-body">
          {error && <p className="error-text">{error}</p>}
          <MachineDetailsFields
            name={name}
            setName={setName}
            maxConcurrent={maxConcurrent}
            setMaxConcurrent={setMaxConcurrent}
            labelsText={labelsText}
            setLabelsText={setLabelsText}
          />
        </div>
        <footer className="modal-foot">
          <button className="btn btn-sm btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn btn-sm btn-primary"
            disabled={busy || !name.trim()}
            onClick={submit}
          >
            {busy ? "Saving…" : "Save"}
          </button>
        </footer>
      </div>
    </div>
  );
}

function PoolCard({
  pool,
  onChanged,
  canWrite,
}: {
  pool: RunnerPoolInfo;
  onChanged: () => void;
  canWrite: boolean;
}) {
  const [runners, setRunners] = useState<RunnerInfo[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [sshOpen, setSshOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [editingRunner, setEditingRunner] = useState<RunnerInfo | null>(null);

  const loadRunners = useCallback(async () => {
    try {
      setRunners(await runnerPoolsApi.listRunners(pool.id));
    } catch {
      /* ignore */
    }
  }, [pool.id]);

  useEffect(() => {
    if (expanded) void loadRunners();
  }, [expanded, loadRunners]);

  const summary = poolConfigSummary(pool);
  const pillClass =
    pool.online_count > 0 ? "status-run-success" : "status-run-skipped";
  const pillText =
    pool.runner_count > 0
      ? `${pool.online_count}/${pool.runner_count} online`
      : "no runners";

  return (
    <div className="pool-card">
      <div className="pool-head">
        <div className="pool-main">
          <div className="pool-name">
            {pool.name}
            <span className={`run-pill ${pillClass}`}>{pillText}</span>
          </div>
          <div className="pool-meta">
            {pool.provider} · max {pool.max_concurrent_runs} concurrent
            {summary && <> · {summary}</>}
          </div>
        </div>
        <div className="pool-actions">
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? "Hide" : "Runners"}
          </button>
          {canWrite && (
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={() => setEditing(true)}
            >
              Edit
            </button>
          )}
          {canWrite && (
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={async () => {
                if (
                  !confirm(
                    "Delete this runner pool and all its registered runners?",
                  )
                )
                  return;
                await runnerPoolsApi.delete(pool.id);
                onChanged();
              }}
            >
              Delete
            </button>
          )}
        </div>
      </div>

      {expanded && (
        <div className="pool-body">
          {runners.length === 0 && (
            <p className="muted">No runners registered yet.</p>
          )}
          {runners.map((r) => {
            const labels = Object.entries(r.capabilities || {});
            return (
              <div key={r.id} className="runner-row">
                <span>
                  <span className={`status-dot ${r.status}`} />
                  {r.name}
                </span>
                <span className="runner-row-meta">
                  {r.current_runs}/{r.max_concurrent_runs} runs ·{" "}
                  {r.cached_env_ids.length} envs ·{" "}
                  {r.last_seen_at
                    ? new Date(r.last_seen_at).toLocaleString()
                    : "never seen"}
                  {labels.length > 0 && (
                    <>
                      {" · "}
                      {labels.map(([k, v]) => (
                        <span key={k} className="runner-label-pill">
                          {k}={String(v)}
                        </span>
                      ))}
                    </>
                  )}
                  {canWrite && (
                    <>
                      {" "}
                      <button
                        type="button"
                        className="btn btn-sm btn-ghost"
                        onClick={() => setEditingRunner(r)}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm btn-ghost"
                        onClick={async () => {
                          if (!confirm(`Remove runner "${r.name}"?`)) return;
                          await runnerPoolsApi.deleteRunner(pool.id, r.id);
                          await loadRunners();
                          onChanged();
                        }}
                      >
                        Remove
                      </button>
                    </>
                  )}
                </span>
              </div>
            );
          })}

          {canWrite && pool.provider === "agent" && (
            <div className="pool-onboard-actions">
              <button
                type="button"
                className="btn btn-sm btn-primary"
                onClick={() => setAddOpen(true)}
              >
                + Add machine
              </button>
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                onClick={() => setSshOpen(true)}
              >
                Onboard via SSH
              </button>
            </div>
          )}
          {pool.provider !== "agent" && (
            <p className="muted">
              {pool.provider === "docker"
                ? "Docker pools have no registered runners — the API drives containers directly."
                : "Kubernetes pools spawn a single-run pod per run — no persistent runners."}
            </p>
          )}
        </div>
      )}

      {editing && (
        <PoolDialog
          pool={pool}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            onChanged();
          }}
        />
      )}

      {sshOpen && (
        <SSHOnboardDialog
          poolId={pool.id}
          onClose={() => setSshOpen(false)}
          onDone={() => {
            void loadRunners();
            onChanged();
          }}
        />
      )}

      {addOpen && (
        <AddMachineDialog
          poolId={pool.id}
          onClose={() => setAddOpen(false)}
          onDone={() => {
            void loadRunners();
            onChanged();
          }}
        />
      )}

      {editingRunner && (
        <EditRunnerDialog
          poolId={pool.id}
          runner={editingRunner}
          onClose={() => setEditingRunner(null)}
          onSaved={() => {
            setEditingRunner(null);
            void loadRunners();
          }}
        />
      )}
    </div>
  );
}

export function RunnerPoolsPage() {
  const [pools, setPools] = useState<RunnerPoolInfo[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const canWrite = useCan("runner_pool:write");

  const load = useCallback(async () => {
    try {
      setPools(await runnerPoolsApi.list());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="home">
      <HomeHeader />
      <main className="home-main">
        <div className="home-bar">
          <h1>
            Runner Pools
            {pools && <span className="home-count">{pools.length}</span>}
          </h1>
          {canWrite && (
            <button
              type="button"
              className="btn"
              onClick={() => setCreating(true)}
            >
              + New pool
            </button>
          )}
        </div>

        {error && <p className="error-text">{error}</p>}
        {!pools && !error && <p className="muted">Loading…</p>}

        {pools && pools.length === 0 && (
          <div className="empty-state">
            <h2>No runner pools yet</h2>
            <p className="muted">
              {canWrite
                ? "Create one to dispatch workflows to remote machines, Docker, or Kubernetes."
                : "Ask an admin to create one."}
            </p>
            {canWrite && (
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setCreating(true)}
              >
                Create runner pool
              </button>
            )}
          </div>
        )}

        {pools && pools.length > 0 && (
          <div className="pool-list">
            {pools.map((pool) => (
              <PoolCard
                key={pool.id}
                pool={pool}
                canWrite={canWrite}
                onChanged={() => void load()}
              />
            ))}
          </div>
        )}

        {creating && (
          <PoolDialog
            onClose={() => setCreating(false)}
            onSaved={() => {
              setCreating(false);
              void load();
            }}
          />
        )}
      </main>
    </div>
  );
}
