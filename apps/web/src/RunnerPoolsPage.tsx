import { useRef, useState } from "react";

import { useConfirm } from "./ConfirmProvider";
import { useEntitlements } from "./entitlements";
import { useCan } from "./permissions";
import {
  useCreateRunnerPoolMutation,
  useCreateRunnerRegistrationTokenMutation,
  useDeleteRunnerMutation,
  useDeleteRunnerPoolMutation,
  useEnvironments,
  useRunnerFleetHealth,
  useRunnerPoolRunners,
  useRunnerPools,
  useSshOnboardRunnerMutation,
  useUpdateRunnerMutation,
  useUpdateRunnerPoolMutation,
} from "./queries";
import { useModalA11y } from "./useModalA11y";
import { useTimeout } from "./hooks/useTimeout";
import { useMountedRef } from "./hooks/useMountedRef";
import type {
  RegistrationTokenResponse,
  RunnerFleetHealth,
  RunnerInfo,
  RunnerPoolHealth,
  RunnerPoolInfo,
} from "./types";

/** Relative "Ns/Nm/Nh ago" for runner last-seen + queue age. */
function relAgo(iso: string | null): string {
  if (!iso) return "never";
  const secs = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 0) return "just now";
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(iso).toLocaleDateString();
}

function relSecs(secs: number | null | undefined): string {
  if (secs == null) return "—";
  if (secs < 60) return `${Math.round(secs)}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ${Math.round(secs % 60)}s`;
  const hours = Math.floor(mins / 60);
  return `${hours}h ${mins % 60}m`;
}

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
  const createPool = useCreateRunnerPoolMutation();
  const updatePool = useUpdateRunnerPoolMutation();
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onClose);

  const handleSave = async () => {
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      if (editing) {
        await updatePool.mutateAsync({
          poolId: pool!.id,
          body: {
            name: name.trim(),
            max_concurrent_runs: maxConcurrent,
            provider_config: cleanConfig(config),
          },
        });
      } else {
        await createPool.mutateAsync({
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
      <div
        className="modal modal-wide"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="pool-dialog-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <h2 id="pool-dialog-title">{editing ? `Edit ${pool!.name}` : "New runner pool"}</h2>
          <button className="btn btn-sm btn-ghost" onClick={onClose} aria-label="Close">
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
  const sshOnboard = useSshOnboardRunnerMutation();
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onClose);

  const submit = async () => {
    if (!host.trim() || !username.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const labels = parseLabels(labelsText);
      const res = await sshOnboard.mutateAsync({
        poolId,
        body: {
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
        },
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
      <div
        className="modal modal-wide"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ssh-onboard-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <h2 id="ssh-onboard-title">Onboard a machine over SSH</h2>
          <button className="btn btn-sm btn-ghost" onClick={onClose} aria-label="Close">
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
  const scheduleTimeout = useTimeout();
  const mountedRef = useMountedRef();
  const createToken = useCreateRunnerRegistrationTokenMutation();
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onClose);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const labels = parseLabels(labelsText);
      const res = await createToken.mutateAsync({
        poolId,
        body: {
          name: name.trim() || undefined,
          max_concurrent_runs: Number(maxConcurrent) || 1,
          capabilities: Object.keys(labels).length ? labels : undefined,
        },
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
    ? `pip install --find-links ${token.api_url}/runner-pools/wheels/ noodle-runner
noodle-runner register \
  --api-url ${token.api_url} \
  --token ${token.token} \
  --name ${name.trim() || "my-runner"}
noodle-runner start`
    : "";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(installCmd);
      if (!mountedRef.current) return;
      setCopied(true);
      scheduleTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked */
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal modal-wide"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-machine-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <h2 id="add-machine-title">Add a machine to this pool</h2>
          <button className="btn btn-sm btn-ghost" onClick={onClose} aria-label="Close">
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
  const updateRunner = useUpdateRunnerMutation();
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onClose);

  const submit = async () => {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await updateRunner.mutateAsync({
        poolId,
        runnerId: runner.id,
        body: {
          name: name.trim(),
          max_concurrent_runs: Number(maxConcurrent) || 1,
          capabilities: parseLabels(labelsText),
        },
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
      <div
        className="modal modal-wide"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="edit-runner-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <h2 id="edit-runner-title">Edit {runner.name}</h2>
          <button className="btn btn-sm btn-ghost" onClick={onClose} aria-label="Close">
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

function HealthStrip({
  pool,
  health,
}: {
  pool: RunnerPoolInfo;
  health?: RunnerPoolHealth;
}) {
  const capUsed = health?.capacity_used ?? 0;
  const capTotal = health?.capacity_total ?? pool.max_concurrent_runs;
  const pct = capTotal > 0 ? Math.min(100, (capUsed / capTotal) * 100) : 0;
  const queue = health?.queue_depth ?? 0;
  const success = health?.success_24h ?? null;
  const reachable = health?.dispatcher_reachable ?? true;
  const queueStuck = queue > 0 && !reachable;
  return (
    <div className="pool-health">
      <div className="phc">
        <span className="phc-k">Capacity used</span>
        <span className="phc-v">
          {capUsed} <small>/ {capTotal} slots</small>
        </span>
        <div className="hbar">
          <i style={{ width: `${pct}%` }} />
        </div>
      </div>
      <div className="phc">
        <span className="phc-k">Queue (this pool)</span>
        <span className={`phc-v${queueStuck ? " phc-warn" : ""}`}>
          {queue} <small>{queueStuck ? "stuck" : "waiting"}</small>
        </span>
        {queue > 0 && (
          <span className="phc-sub">
            oldest {relSecs(health?.oldest_queued_seconds)}
          </span>
        )}
      </div>
      <div className="phc">
        <span className="phc-k">Success (24h)</span>
        <span className="phc-v">
          {success == null ? "—" : `${Math.round(success * 100)}%`}
        </span>
      </div>
      <div className="phc">
        <span className="phc-k">Dispatcher</span>
        <span className={`phc-v ${reachable ? "phc-ok" : "phc-warn"}`}>
          {reachable ? "● reachable" : "● none"}
        </span>
      </div>
    </div>
  );
}

function PoolCard({
  pool,
  health,
  onChanged,
  canWrite,
  boundEnvs,
}: {
  pool: RunnerPoolInfo;
  health?: RunnerPoolHealth;
  onChanged: () => void;
  canWrite: boolean;
  boundEnvs: string[];
}) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [sshOpen, setSshOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [editingRunner, setEditingRunner] = useState<RunnerInfo | null>(null);
  const confirm = useConfirm();
  const deletePool = useDeleteRunnerPoolMutation();
  const deleteRunner = useDeleteRunnerMutation();
  const runnersQuery = useRunnerPoolRunners(pool.id, {
    enabled: expanded,
    refetchInterval: expanded ? 5000 : undefined,
  });
  const runners = runnersQuery.data ?? [];

  const summary = poolConfigSummary(pool);
  const reachable = health?.dispatcher_reachable ?? true;
  const showDispatcherBanner =
    health != null &&
    !reachable &&
    (pool.online_count > 0 || (health.queue_depth ?? 0) > 0);
  const stuck = (health?.queue_depth ?? 0) > 0 && !reachable;
  const pillClass = stuck
    ? "status-run-error"
    : pool.online_count > 0
      ? "status-run-success"
      : "status-run-skipped";
  const pillText =
    pool.runner_count > 0
      ? `${pool.online_count}/${pool.runner_count} online${stuck ? " · stuck" : ""}`
      : "no runners";

  return (
    <div className="pool-card">
      <div className="pool-head">
        <div className="pool-main">
          <div className="pool-name">
            {pool.name}
            <span className="pool-provider-badge">{pool.provider}</span>
            <span className={`run-pill ${pillClass}`}>{pillText}</span>
          </div>
          <div className="pool-meta">
            max {pool.max_concurrent_runs} concurrent
            {summary && <> · {summary}</>}
          </div>
          <div className="pool-meta">
            {boundEnvs.length > 0 ? (
              <>environments: {boundEnvs.join(", ")}</>
            ) : (
              <span className="muted">
                no environments routed here — bind one on the Environments page
              </span>
            )}
          </div>
        </div>
        <div className="pool-actions">
          {pool.provider === "agent" && (
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? "Hide" : "Runners"}
            </button>
          )}
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
                const ok = await confirm({
                  title: "Delete runner pool?",
                  body: "This pool and all its registered runners will be removed.",
                });
                if (!ok) return;
                await deletePool.mutateAsync(pool.id);
                onChanged();
              }}
            >
              Delete
            </button>
          )}
        </div>
      </div>

      {showDispatcherBanner && (
        <div className="pool-banner" role="alert">
          <span className="status-dot offline" />
          <span>
            <strong>No dispatcher reachable for {pool.provider} pools.</strong>{" "}
            Runners are connected but nothing is leasing their runs
            {(health?.queue_depth ?? 0) > 0
              ? ` — ${health?.queue_depth} run${health?.queue_depth === 1 ? "" : "s"} queued and won't start`
              : ""}
            . Run the API replica with <code>DISPATCH_ROLE=control</code>.
          </span>
        </div>
      )}

      <HealthStrip pool={pool} health={health} />

      {expanded && pool.provider === "agent" && (
        <div className="pool-body">
          {runners.length === 0 && (
            <p className="muted">No runners registered yet.</p>
          )}
          {runners.length > 0 && (
            <div className="runner-table">
              <div className="runner-thead">
                <span>Runner</span>
                <span>Labels</span>
                <span>Capacity</span>
                <span>Cached envs</span>
                <span />
              </div>
              {runners.map((r) => {
                const labels = Object.entries(r.capabilities || {});
                const capPct =
                  r.max_concurrent_runs > 0
                    ? (r.current_runs / r.max_concurrent_runs) * 100
                    : 0;
                return (
                  <div key={r.id} className="runner-trow">
                    <span className="rt-name">
                      <span className={`status-dot ${r.status}`} />
                      {r.name}
                      <span className="muted rt-seen">
                        · {relAgo(r.last_seen_at)}
                      </span>
                    </span>
                    <span className="rt-labels">
                      {labels.length === 0 ? (
                        <span className="muted">no labels</span>
                      ) : (
                        labels.map(([k, v]) => (
                          <span key={k} className="runner-label-pill">
                            {k}={String(v)}
                          </span>
                        ))
                      )}
                    </span>
                    <span className="rt-cap">
                      <span className="cap-bar">
                        <i style={{ width: `${Math.min(100, capPct)}%` }} />
                      </span>
                      <span className="muted">
                        {r.current_runs}/{r.max_concurrent_runs}
                      </span>
                    </span>
                    <span className="muted">
                      {r.cached_env_ids.length} env
                      {r.cached_env_ids.length === 1 ? "" : "s"}
                    </span>
                    <span className="rt-act">
                      {canWrite && (
                        <button
                          type="button"
                          className="btn btn-sm btn-ghost"
                          onClick={() => setEditingRunner(r)}
                        >
                          Edit
                        </button>
                      )}
                      {canWrite && (
                        <button
                          type="button"
                          className="btn btn-sm btn-ghost"
                          onClick={async () => {
                            const ok = await confirm({
                              title: "Remove runner?",
                              body: `“${r.name}” will be removed from this pool.`,
                              confirmLabel: "Remove",
                            });
                            if (!ok) return;
                            await deleteRunner.mutateAsync({
                              poolId: pool.id,
                              runnerId: r.id,
                            });
                            onChanged();
                          }}
                        >
                          Remove
                        </button>
                      )}
                    </span>
                  </div>
                );
              })}
            </div>
          )}

          {canWrite && (
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
        </div>
      )}
      {expanded && pool.provider !== "agent" && (
        <div className="pool-body">
          <p className="muted">
            {pool.provider === "docker"
              ? "Docker pools have no registered runners — the API drives containers directly."
              : "Kubernetes pools spawn a single-run pod per run — no persistent runners."}
          </p>
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
            void runnersQuery.refetch();
            onChanged();
          }}
        />
      )}

      {addOpen && (
        <AddMachineDialog
          poolId={pool.id}
          onClose={() => setAddOpen(false)}
          onDone={() => {
            void runnersQuery.refetch();
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
            void runnersQuery.refetch();
          }}
        />
      )}
    </div>
  );
}

function FleetBar({ health }: { health: RunnerFleetHealth }) {
  const f = health.fleet;
  const degraded = f.providers_stuck.length > 0;
  return (
    <div className="fleet-bar">
      <div className="fleet-stat">
        <span className="fleet-k">Runners online</span>
        <span className="fleet-v">
          <span className="status-dot online" />
          {f.runners_online}
          <small>/ {f.runners_total} registered</small>
        </span>
      </div>
      <div className="fleet-stat">
        <span className="fleet-k">Queue depth</span>
        <span className="fleet-v">{f.queue_depth}</span>
      </div>
      <div className="fleet-stat">
        <span className="fleet-k">In-flight runs</span>
        <span className="fleet-v">{f.in_flight}</span>
      </div>
      <div className="fleet-stat">
        <span className="fleet-k">Dispatchers</span>
        <span className={`fleet-v ${degraded ? "fleet-warn" : ""}`}>
          <span className={`status-dot ${degraded ? "offline" : "online"}`} />
          {f.providers_dispatchable.length}
          <small>
            {degraded
              ? `${f.providers_stuck.join(", ")} stuck`
              : "all providers served"}
          </small>
        </span>
      </div>
    </div>
  );
}

export function RunnerPoolsPage() {
  const [creating, setCreating] = useState(false);
  const canWrite = useCan("runner_pool:write");
  const ent = useEntitlements();
  const poolsQuery = useRunnerPools({ refetchInterval: 5000 });
  const healthQuery = useRunnerFleetHealth({ refetchInterval: 5000 });
  const environmentsQuery = useEnvironments();
  const pools = poolsQuery.data ?? null;
  const environments = environmentsQuery.data ?? [];
  const health = healthQuery.data ?? null;
  const healthByPool = (health?.pools ?? []).reduce<
    Record<string, RunnerPoolHealth>
  >((acc, h) => {
    acc[h.pool_id] = h;
    return acc;
  }, {});
  const error =
    poolsQuery.isError && !poolsQuery.data
      ? poolsQuery.error.message
      : "";

  function refreshPools(): void {
    void poolsQuery.refetch();
    void healthQuery.refetch();
  }

  const envsByPool = environments.reduce<Record<string, string[]>>(
    (acc, env) => {
      if (env.runner_pool_id) {
        (acc[env.runner_pool_id] ??= []).push(env.name);
      }
      return acc;
    },
    {},
  );

  return (
    <div className="home">
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
              disabled={ent.atLimit("runners", pools?.length ?? 0)}
              title={
                ent.atLimit("runners", pools?.length ?? 0)
                  ? `Runner limit reached on the ${ent.edition} edition — upgrade to add more.`
                  : undefined
              }
            >
              + New pool
            </button>
          )}
        </div>

        {health && pools && pools.length > 0 && <FleetBar health={health} />}

        {error && <p className="error-text">{error}</p>}
        {!pools && !error && (
          <div className="pool-list" aria-label="Loading runner pools">
            {Array.from({ length: 3 }).map((_, index) => (
              <div className="pool-card skeleton-card" key={index}>
                <div className="pool-head">
                  <div className="pool-main">
                    <span className="skeleton-line short" />
                    <span className="skeleton-line" />
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

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
                health={healthByPool[pool.id]}
                canWrite={canWrite}
                onChanged={refreshPools}
                boundEnvs={envsByPool[pool.id] ?? []}
              />
            ))}
          </div>
        )}

        {creating && (
          <PoolDialog
            onClose={() => setCreating(false)}
            onSaved={() => {
              setCreating(false);
              refreshPools();
            }}
          />
        )}
      </main>
    </div>
  );
}
