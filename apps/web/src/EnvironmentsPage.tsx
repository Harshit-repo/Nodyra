import { useEffect, useMemo, useRef, useState } from "react";

import { errorMessage } from "./api";
import { ConfirmDialog } from "./ConfirmDialog";
import { HomeHeader } from "./HomeHeader";
import { PackageDrawer } from "./PackageDrawer";
import {
  useCreateEnvironmentMutation,
  useDeleteEnvironmentMutation,
  useEnvironments,
  useRebuildEnvironmentMutation,
  useRunnerPools,
  useSystemSettings,
  useUpdateEnvironmentMutation,
} from "./queries";
import { useToast } from "./ToastProvider";
import { useModalA11y } from "./useModalA11y";
import type { Environment, RunnerPoolInfo } from "./types";

const BACKEND_BADGE: Record<string, { label: string; color: string }> = {
  venv:   { label: "venv",   color: "#22c55e" },
  conda:  { label: "conda",  color: "#3b82f6" },
  pixi:   { label: "pixi",   color: "#14b8a6" },
  docker: { label: "docker", color: "#a855f7" },
};

type BackendTab = "venv" | "conda" | "pixi";

const BUILD_POLL_TIMEOUT_MS = 20 * 60 * 1000;

const DESCRIPTION_HELP =
  "Optional notes for your team — what this environment is for, who owns it, gotchas. Shown in the env card.";

const FIXED_HELP =
  "Always keep this many warm worker processes alive. Queues extra runs. Each warm worker re-imports the env's packages, so RAM cost is roughly (pool size) × (env footprint). Default for steady, predictable load.";

const ELASTIC_HELP =
  "Keep a small floor warm. Burst extra workers on demand up to the maximum, and let surplus die after idle. Best of both worlds: fast bursts without paying RAM 24/7.";

const SPAWN_HELP =
  "No warm workers. Every run spawns its own interpreter and the worker dies the moment it finishes. Saves RAM but adds cold-start latency on every run.";

const RUNNER_POOL_HELP =
  "Where workflows using this environment execute. Local (in-process) runs on the API host. Bind a remote runner pool to offload execution to registered agent/Docker/Kubernetes runners. A deployment or workflow-level pool override still takes precedence.";

function PoolSelect({
  pools,
  value,
  onChange,
}: {
  pools: RunnerPoolInfo[];
  value: string | null;
  onChange: (v: string | null) => void;
}) {
  return (
    <>
      <label className="field-label">
        Execution target <InfoTip text={RUNNER_POOL_HELP} />
      </label>
      <select
        className="field-input"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
      >
        <option value="">Local (in-process)</option>
        {pools.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name} ({p.provider} · {p.online_count}/{p.runner_count} online)
          </option>
        ))}
      </select>
    </>
  );
}

type PoolMode = "fixed" | "elastic" | "spawn";

function LogIcon({ envName, log }: { envName: string; log: string }) {
  const [show, setShow] = useState(false);
  const tail = log.split("\n").slice(-14).join("\n");

  function download() {
    const blob = new Blob([log], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${envName.replace(/[^a-z0-9_-]/gi, "_")}-build.log`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  return (
    <span
      className="env-log-icon"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      <button type="button" className="env-log-btn" onClick={download} title="Click to download full log">
        ≡
      </button>
      {show && (
        <div className="env-log-tooltip">
          <pre>{tail}</pre>
          <p className="env-log-tooltip-hint">Click to download full log</p>
        </div>
      )}
    </span>
  );
}

function InfoTip({ text }: { text: string }) {
  return (
    <span className="info-tip" title={text} aria-label={text}>
      ⓘ
    </span>
  );
}

function modeFor(env: Environment): PoolMode {
  if (env.runner_pool_size === 0) return "spawn";
  if (env.runner_pool_max != null && env.runner_pool_max > env.runner_pool_size)
    return "elastic";
  return "fixed";
}

function formatBytes(value: number | null | undefined): string {
  if (!value || value <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = value;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

function scrollToEnvironment(envId: string): void {
  const el = document.getElementById(`env-card-${envId}`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.add("env-card--flash");
  window.setTimeout(() => el.classList.remove("env-card--flash"), 1800);
}

function PoolModeFields({
  mode,
  setMode,
  fixedSize,
  setFixedSize,
  elasticMin,
  setElasticMin,
  elasticMax,
  setElasticMax,
  spawnMax,
  setSpawnMax,
  workspaceCap,
  rssEstimate,
  rssSoftBudget,
}: {
  mode: PoolMode;
  setMode: (mode: PoolMode) => void;
  fixedSize: number;
  setFixedSize: (n: number) => void;
  elasticMin: number;
  setElasticMin: (n: number) => void;
  elasticMax: number;
  setElasticMax: (n: number) => void;
  spawnMax: number;
  setSpawnMax: (n: number) => void;
  workspaceCap: number | null;
  rssEstimate: number | null | undefined;
  rssSoftBudget: number;
}) {
  const effectiveMax =
    mode === "fixed" ? fixedSize : mode === "elastic" ? elasticMax : spawnMax;
  const overCap = workspaceCap != null && effectiveMax > workspaceCap;
  const totalRssBytes =
    rssEstimate && rssEstimate > 0 ? rssEstimate * effectiveMax : 0;
  const overBudget = rssSoftBudget > 0 && totalRssBytes > rssSoftBudget;

  return (
    <>
      <label className="field-label">Pool mode</label>
      <div className="pool-mode-radios">
        <label className="pool-mode-radio">
          <input
            type="radio"
            checked={mode === "fixed"}
            onChange={() => setMode("fixed")}
          />
          <span>Fixed</span>
          <InfoTip text={FIXED_HELP} />
        </label>
        <label className="pool-mode-radio">
          <input
            type="radio"
            checked={mode === "elastic"}
            onChange={() => setMode("elastic")}
          />
          <span>Elastic</span>
          <InfoTip text={ELASTIC_HELP} />
        </label>
        <label className="pool-mode-radio">
          <input
            type="radio"
            checked={mode === "spawn"}
            onChange={() => setMode("spawn")}
          />
          <span>Spawn-per-run</span>
          <InfoTip text={SPAWN_HELP} />
        </label>
      </div>

      {mode === "fixed" && (
        <>
          <label className="field-label">Pool size</label>
          <input
            className="field-input"
            type="number"
            min={1}
            max={32}
            value={fixedSize}
            onChange={(e) => setFixedSize(Number(e.target.value))}
          />
        </>
      )}

      {mode === "elastic" && (
        <div className="pool-mode-pair">
          <div>
            <label className="field-label">Warm minimum</label>
            <input
              className="field-input"
              type="number"
              min={1}
              max={32}
              value={elasticMin}
              onChange={(e) => setElasticMin(Number(e.target.value))}
            />
          </div>
          <div>
            <label className="field-label">Burst maximum</label>
            <input
              className="field-input"
              type="number"
              min={Math.max(1, elasticMin)}
              max={64}
              value={elasticMax}
              onChange={(e) => setElasticMax(Number(e.target.value))}
            />
          </div>
        </div>
      )}

      {mode === "spawn" && (
        <>
          <label className="field-label">Concurrent run limit</label>
          <input
            className="field-input"
            type="number"
            min={1}
            max={64}
            value={spawnMax}
            onChange={(e) => setSpawnMax(Number(e.target.value))}
          />
        </>
      )}

      {rssEstimate && rssEstimate > 0 && (
        <p className="muted env-ram-estimate">
          Estimated max RAM at burst: {effectiveMax} workers ×{" "}
          {formatBytes(rssEstimate)} ≈ {formatBytes(totalRssBytes)}
        </p>
      )}
      {overCap && workspaceCap != null && (
        <p className="warn-text">
          ⚠ Workspace cap of {workspaceCap} will limit this env's effective
          concurrency to {workspaceCap}.
        </p>
      )}
      {overBudget && (
        <p className="warn-text">
          ⚠ This pool could use up to {formatBytes(totalRssBytes)} at full
          burst, more than the workspace soft budget of{" "}
          {formatBytes(rssSoftBudget)}. Consider lowering the maximum or
          moving heavy packages into a separate env.
        </p>
      )}
    </>
  );
}

interface PoolPayload {
  runner_pool_size: number;
  runner_pool_max: number | null;
}

function packPool(
  mode: PoolMode,
  fixedSize: number,
  elasticMin: number,
  elasticMax: number,
  spawnMax: number,
): PoolPayload {
  if (mode === "fixed") {
    const size = Math.max(1, Math.min(32, Math.floor(fixedSize) || 1));
    return { runner_pool_size: size, runner_pool_max: null };
  }
  if (mode === "elastic") {
    const min = Math.max(1, Math.min(32, Math.floor(elasticMin) || 1));
    const max = Math.max(min, Math.min(64, Math.floor(elasticMax) || min));
    return { runner_pool_size: min, runner_pool_max: max };
  }
  const max = Math.max(1, Math.min(64, Math.floor(spawnMax) || 1));
  return { runner_pool_size: 0, runner_pool_max: max };
}

function CreateEnvModal({
  onClose,
  onCreated,
  workspaceCap,
  rssSoftBudget,
  pools,
}: {
  onClose: () => void;
  onCreated: () => void;
  workspaceCap: number | null;
  rssSoftBudget: number;
  pools: RunnerPoolInfo[];
}) {
  const [name, setName] = useState("");
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onClose);
  const [python, setPython] = useState("3.12");
  const [description, setDescription] = useState("");
  const [poolId, setPoolId] = useState<string | null>(null);
  const [mode, setMode] = useState<PoolMode>("fixed");
  const [fixedSize, setFixedSize] = useState(1);
  const [elasticMin, setElasticMin] = useState(1);
  const [elasticMax, setElasticMax] = useState(4);
  const [spawnMax, setSpawnMax] = useState(4);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [backendTab, setBackendTab] = useState<BackendTab>("venv");
  const [channelInput, setChannelInput] = useState("conda-forge");
  const [indexUrlInput, setIndexUrlInput] = useState("");
  const createEnvironment = useCreateEnvironmentMutation();

  async function submit() {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      const pool = packPool(mode, fixedSize, elasticMin, elasticMax, spawnMax);
      const channelList = channelInput.split(",").map((c) => c.trim()).filter(Boolean);
      const indexUrlList = indexUrlInput.split(",").map((u) => u.trim()).filter(Boolean);
      const backend_config =
        backendTab === "conda" || backendTab === "pixi"
          ? { channels: channelList }
          : backendTab === "venv" && indexUrlList.length > 0
          ? { index_urls: indexUrlList }
          : {};
      await createEnvironment.mutateAsync({
        name: name.trim(),
        python_version: python,
        description: description.trim(),
        runner_pool_id: poolId,
        backend: backendTab,
        backend_config,
        ...pool,
      });
      onCreated();
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-env-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="create-env-title">New environment</h2>

        <div className="backend-tabs">
          {(["venv", "conda", "pixi"] as BackendTab[]).map((b) => (
            <button
              key={b}
              type="button"
              className={`backend-tab${backendTab === b ? " backend-tab--active" : ""}`}
              onClick={() => setBackendTab(b)}
            >
              {b === "venv" ? "uv + venv" : b}
            </button>
          ))}
        </div>
        <div className="backend-coming-soon">Docker backend coming soon</div>

        <label className="field-label">Name</label>
        <input
          className="field-input"
          autoFocus
          placeholder="Environment name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void submit()}
        />

        <label className="field-label">Python version</label>
        <select
          className="field-input"
          value={python}
          onChange={(e) => setPython(e.target.value)}
        >
          <option value="3.11">Python 3.11</option>
          <option value="3.12">Python 3.12</option>
          <option value="3.13">Python 3.13</option>
        </select>

        <label className="field-label">
          Description <InfoTip text={DESCRIPTION_HELP} />
        </label>
        <textarea
          className="field-input"
          rows={2}
          placeholder="What is this env for?"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />

        {(backendTab === "conda" || backendTab === "pixi") && (
          <>
            <label className="field-label">
              Channels <span className="muted">(comma-separated)</span>
            </label>
            <input
              className="field-input"
              placeholder="conda-forge, defaults"
              value={channelInput}
              onChange={(e) => setChannelInput(e.target.value)}
            />
            {backendTab === "pixi" && (
              <p className="field-hint muted">
                Suffix packages with <code>@ pypi</code> to install from PyPI.
              </p>
            )}
          </>
        )}

        {backendTab === "venv" && (
          <>
            <label className="field-label">
              Extra index URLs <span className="muted">(comma-separated, optional)</span>
            </label>
            <input
              className="field-input"
              placeholder="https://download.pytorch.org/whl/cu121"
              value={indexUrlInput}
              onChange={(e) => setIndexUrlInput(e.target.value)}
            />
          </>
        )}

        <PoolSelect pools={pools} value={poolId} onChange={setPoolId} />

        <PoolModeFields
          mode={mode}
          setMode={setMode}
          fixedSize={fixedSize}
          setFixedSize={setFixedSize}
          elasticMin={elasticMin}
          setElasticMin={setElasticMin}
          elasticMax={elasticMax}
          setElasticMax={setElasticMax}
          spawnMax={spawnMax}
          setSpawnMax={setSpawnMax}
          workspaceCap={workspaceCap}
          rssEstimate={null}
          rssSoftBudget={rssSoftBudget}
        />

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

function EditEnvModal({
  env,
  onClose,
  onSaved,
  workspaceCap,
  rssSoftBudget,
  pools,
}: {
  env: Environment;
  onClose: () => void;
  onSaved: () => void;
  workspaceCap: number | null;
  rssSoftBudget: number;
  pools: RunnerPoolInfo[];
}) {
  const initialMode = modeFor(env);
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onClose);
  const [name, setName] = useState(env.name);
  const [description, setDescription] = useState(env.description || "");
  const [poolId, setPoolId] = useState<string | null>(env.runner_pool_id);
  const [mode, setMode] = useState<PoolMode>(initialMode);
  const [fixedSize, setFixedSize] = useState(
    initialMode === "fixed" ? env.runner_pool_size || 1 : 1,
  );
  const [elasticMin, setElasticMin] = useState(
    initialMode === "elastic" ? env.runner_pool_size || 1 : 1,
  );
  const [elasticMax, setElasticMax] = useState(
    initialMode === "elastic" && env.runner_pool_max
      ? env.runner_pool_max
      : Math.max(4, env.runner_pool_size || 1),
  );
  const [spawnMax, setSpawnMax] = useState(
    initialMode === "spawn" && env.runner_pool_max ? env.runner_pool_max : 4,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const updateEnvironment = useUpdateEnvironmentMutation();

  async function save() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const pool = packPool(mode, fixedSize, elasticMin, elasticMax, spawnMax);
      await updateEnvironment.mutateAsync({
        id: env.id,
        body: {
          name: name.trim(),
          description: description.trim(),
          runner_pool_id: poolId,
          runner_pool_set: true,
          ...pool,
        },
      });
      onSaved();
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="edit-env-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="edit-env-title">Edit environment</h2>
        <label className="field-label">Name</label>
        <input
          className="field-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <label className="field-label">
          Description <InfoTip text={DESCRIPTION_HELP} />
        </label>
        <textarea
          className="field-input"
          rows={2}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />

        <PoolSelect pools={pools} value={poolId} onChange={setPoolId} />

        <PoolModeFields
          mode={mode}
          setMode={setMode}
          fixedSize={fixedSize}
          setFixedSize={setFixedSize}
          elasticMin={elasticMin}
          setElasticMin={setElasticMin}
          elasticMax={elasticMax}
          setElasticMax={setElasticMax}
          spawnMax={spawnMax}
          setSpawnMax={setSpawnMax}
          workspaceCap={workspaceCap}
          rssEstimate={env.worker_rss_estimate_bytes}
          rssSoftBudget={rssSoftBudget}
        />

        <p className="muted">
          Pool changes apply when the API restarts (or this env's pool is
          first created after the change).
        </p>
        {error && <p className="error-text">{error}</p>}
        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn btn-primary"
            onClick={() => void save()}
            disabled={busy}
          >
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function poolLabel(env: Environment): string {
  const mode = modeFor(env);
  if (mode === "fixed") return `pool ${env.runner_pool_size || 1}`;
  if (mode === "elastic")
    return `pool ${env.runner_pool_size}→${env.runner_pool_max}`;
  return `spawn-per-run (≤ ${env.runner_pool_max || env.effective_pool_max})`;
}

function EnvCard({
  env,
  onChanged,
  workspaceCap,
  rssSoftBudget,
  pools,
}: {
  env: Environment;
  onChanged: () => void;
  workspaceCap: number | null;
  rssSoftBudget: number;
  pools: RunnerPoolInfo[];
}) {
  const [showPackages, setShowPackages] = useState(false);
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const rebuildEnvironment = useRebuildEnvironmentMutation();
  const deleteEnvironment = useDeleteEnvironmentMutation();
  const effectiveWorkers = env.effective_pool_max || env.runner_pool_size || 1;
  const maxRam =
    env.worker_rss_estimate_bytes && env.worker_rss_estimate_bytes > 0
      ? env.worker_rss_estimate_bytes * effectiveWorkers
      : null;

  async function rebuild() {
    await rebuildEnvironment.mutateAsync(env.id);
    onChanged();
  }

  async function del() {
    setDeleteBusy(true);
    try {
      await deleteEnvironment.mutateAsync(env.id);
      setConfirmDelete(false);
      onChanged();
    } finally {
      setDeleteBusy(false);
    }
  }

  return (
    <article className="env-card" id={`env-card-${env.id}`} tabIndex={-1}>
      <div className="env-card-head">
        <div className="env-title">
          <h3>{env.name}</h3>
          {env.is_global && <span className="env-global">global</span>}
          {(() => {
            const b = BACKEND_BADGE[env.backend] ?? { label: env.backend, color: "#6b7280" };
            return (
              <span className="env-backend-badge" style={{ background: b.color }}>
                {b.label}
              </span>
            );
          })()}
        </div>
        <div className="env-card-head-right">
          {env.status_detail && <LogIcon envName={env.name} log={env.status_detail} />}
          <span className={`env-status status-${env.status}`}>{env.status.toUpperCase()}</span>
        </div>
      </div>
      <div className="env-meta">
        Python {env.python_version} · {poolLabel(env)} ·{" "}
        <span title="Where runs of this environment execute">
          {env.runner_pool_id
            ? `→ ${env.runner_pool_name ?? "runner pool"}`
            : "→ local (in-process)"}
        </span>
      </div>
      <div className="env-health-grid">
        <div>
          <span>Workers</span>
          <strong>{effectiveWorkers}</strong>
        </div>
        <button
          type="button"
          className="env-health-tile-btn"
          onClick={() => setShowPackages(true)}
          title="Add, remove or import packages"
        >
          <span>Packages ›</span>
          <strong>{env.packages.length}</strong>
        </button>
        <div>
          <span>Worker RAM</span>
          <strong>{formatBytes(env.worker_rss_estimate_bytes)}</strong>
        </div>
        <div>
          <span>Max RAM</span>
          <strong>{formatBytes(maxRam)}</strong>
        </div>
      </div>
      {env.description && <p className="env-description">{env.description}</p>}

      <div className="env-actions">
        <button className="btn btn-sm btn-primary" onClick={() => setShowPackages(true)}>
          Manage packages
        </button>
        <button className="btn btn-sm" onClick={() => setEditing(true)}>
          Edit
        </button>
        <button className="btn btn-sm" onClick={() => void rebuild()}>
          Rebuild
        </button>
        {!env.is_global && (
          <button
            className="btn btn-sm btn-ghost"
            onClick={() => setConfirmDelete(true)}
          >
            Delete
          </button>
        )}
      </div>

      {editing && (
        <EditEnvModal
          env={env}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            onChanged();
          }}
          workspaceCap={workspaceCap}
          rssSoftBudget={rssSoftBudget}
          pools={pools}
        />
      )}
      {showPackages && (
        <PackageDrawer
          env={env}
          onClose={() => setShowPackages(false)}
          onChanged={onChanged}
        />
      )}
      {confirmDelete && (
        <ConfirmDialog
          title="Delete environment"
          body={`Delete "${env.name}"? Workflows that use this environment will need a new run environment.`}
          busy={deleteBusy}
          onCancel={() => setConfirmDelete(false)}
          onConfirm={() => void del()}
        />
      )}
    </article>
  );
}

export function EnvironmentsPage() {
  const [modal, setModal] = useState(false);
  const { notify } = useToast();
  // Track previous statuses to fire toasts on transitions
  const prevStatuses = useRef<Record<string, string>>({});
  const buildPollStarted = useRef<Record<string, number>>({});
  const buildPollTimedOut = useRef<Set<string>>(new Set());
  const environmentsQuery = useEnvironments({
    refetchInterval: (query) => {
      const building =
        query.state.data?.filter(
          (env) => env.status === "pending" || env.status === "building",
        ) ?? [];
      if (building.length === 0) return false;
      return building.every((env) => buildPollTimedOut.current.has(env.id))
        ? false
        : 2500;
    },
  });
  const poolsQuery = useRunnerPools();
  const settingsQuery = useSystemSettings();
  const environments = environmentsQuery.data ?? null;
  const systemSettings = settingsQuery.data ?? null;
  const pools = poolsQuery.data ?? [];
  const error =
    environmentsQuery.isError && !environmentsQuery.data
      ? errorMessage(environmentsQuery.error)
      : "";

  function refreshEnvironments() {
    void environmentsQuery.refetch();
  }

  // Fire toasts when env build status transitions.
  useEffect(() => {
    if (!environments) return;
    const prev = prevStatuses.current;
    for (const env of environments) {
      const was = prev[env.id];
      const is = env.status;
      const wasBuilding = was === "pending" || was === "building";
      const isBuilding = is === "pending" || is === "building";
      if ((!was || was === "ready" || was === "error") && isBuilding) {
        notify(`Building ${env.name}…`, "info", {
          label: "View logs",
          onClick: () => scrollToEnvironment(env.id),
        });
      } else if (wasBuilding && is === "ready") {
        notify(`${env.name} is ready.`, "success");
      } else if (wasBuilding && is === "error") {
        notify(`${env.name} failed to build — check the logs.`, "error", {
          label: "View logs",
          onClick: () => scrollToEnvironment(env.id),
        });
      }
      prev[env.id] = is;
    }
  }, [environments, notify]);

  // Poll while any environment is still building.
  useEffect(() => {
    const building = environments?.filter((e) => e.status === "pending" || e.status === "building") ?? [];
    if (building.length === 0) {
      buildPollStarted.current = {};
      buildPollTimedOut.current.clear();
      return;
    }

    const now = Date.now();
    const activeIds = new Set(building.map((env) => env.id));
    for (const id of Object.keys(buildPollStarted.current)) {
      if (!activeIds.has(id)) {
        delete buildPollStarted.current[id];
        buildPollTimedOut.current.delete(id);
      }
    }
    for (const env of building) {
      buildPollStarted.current[env.id] ??= now;
      if (
        now - buildPollStarted.current[env.id] > BUILD_POLL_TIMEOUT_MS &&
        !buildPollTimedOut.current.has(env.id)
      ) {
        buildPollTimedOut.current.add(env.id);
        notify(`${env.name} is still building after 20 minutes.`, "error", {
          label: "View logs",
          onClick: () => scrollToEnvironment(env.id),
        });
      }
    }
  }, [environments, notify]);

  const workspaceCap = systemSettings?.max_concurrent_runs ?? null;
  const rssSoftBudget = systemSettings?.worker_rss_soft_budget_bytes ?? 0;
  const health = useMemo(() => {
    const rows = environments ?? [];
    return {
      ready: rows.filter((env) => env.status === "ready").length,
      building: rows.filter(
        (env) => env.status === "pending" || env.status === "building",
      ).length,
      errors: rows.filter((env) => env.status === "error").length,
      packages: rows.reduce((total, env) => total + env.packages.length, 0),
    };
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
        {!environments && !error && (
          <div className="env-grid" aria-label="Loading environments">
            {Array.from({ length: 3 }).map((_, index) => (
              <div className="env-card skeleton-card" key={index}>
                <span className="skeleton-line title" />
                <span className="skeleton-line" />
                <span className="skeleton-line" />
                <span className="skeleton-line tiny" />
              </div>
            ))}
          </div>
        )}

        {environments && (
          <>
            <div className="env-health-summary">
              <div>
                <strong>{health.ready}</strong>
                <span>Ready</span>
              </div>
              <div>
                <strong>{health.building}</strong>
                <span>Building</span>
              </div>
              <div>
                <strong>{health.errors}</strong>
                <span>Errors</span>
              </div>
              <div>
                <strong>{health.packages}</strong>
                <span>Installed packages</span>
              </div>
            </div>
            <div className="env-grid">
              {environments.map((env) => (
                <EnvCard
                  key={env.id}
                  env={env}
                  onChanged={refreshEnvironments}
                  workspaceCap={workspaceCap}
                  rssSoftBudget={rssSoftBudget}
                  pools={pools}
                />
              ))}
            </div>
          </>
        )}
      </main>

      {modal && (
        <CreateEnvModal
          onClose={() => setModal(false)}
          onCreated={() => {
            setModal(false);
            refreshEnvironments();
          }}
          workspaceCap={workspaceCap}
          rssSoftBudget={rssSoftBudget}
          pools={pools}
        />
      )}
    </div>
  );
}
