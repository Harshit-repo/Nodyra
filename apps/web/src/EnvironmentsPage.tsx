import { useEffect, useMemo, useState } from "react";

import { api, runnerPoolsApi } from "./api";
import { ConfirmDialog } from "./ConfirmDialog";
import { HomeHeader } from "./HomeHeader";
import { PackageDrawer } from "./PackageDrawer";
import type { Environment, RunnerPoolInfo, SystemSettings } from "./types";

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

  async function submit() {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      const pool = packPool(mode, fixedSize, elasticMin, elasticMax, spawnMax);
      await api.createEnvironment({
        name: name.trim(),
        python_version: python,
        description: description.trim(),
        runner_pool_id: poolId,
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
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>New environment</h2>
        <p className="muted">A custom Python venv your workflows can run in.</p>

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

  async function save() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const pool = packPool(mode, fixedSize, elasticMin, elasticMax, spawnMax);
      await api.updateEnvironment(env.id, {
        name: name.trim(),
        description: description.trim(),
        runner_pool_id: poolId,
        runner_pool_set: true,
        ...pool,
      });
      onSaved();
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Edit environment</h2>
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
  const effectiveWorkers = env.effective_pool_max || env.runner_pool_size || 1;
  const maxRam =
    env.worker_rss_estimate_bytes && env.worker_rss_estimate_bytes > 0
      ? env.worker_rss_estimate_bytes * effectiveWorkers
      : null;

  async function rebuild() {
    await api.rebuildEnvironment(env.id);
    onChanged();
  }

  async function del() {
    setDeleteBusy(true);
    try {
      await api.deleteEnvironment(env.id);
      setConfirmDelete(false);
      onChanged();
    } finally {
      setDeleteBusy(false);
    }
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
        >
          <span>Packages</span>
          <strong>{env.packages.length} ›</strong>
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

      {env.status_detail && (env.status === "error" || env.status === "building") && (
        <pre className="env-log">{env.status_detail}</pre>
      )}

      <div className="env-actions">
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
  const [environments, setEnvironments] = useState<Environment[] | null>(null);
  const [systemSettings, setSystemSettings] = useState<SystemSettings | null>(
    null,
  );
  const [pools, setPools] = useState<RunnerPoolInfo[]>([]);
  const [error, setError] = useState("");
  const [modal, setModal] = useState(false);

  function load() {
    api
      .listEnvironments()
      .then(setEnvironments)
      .catch((err) => setError(String(err)));
  }

  useEffect(load, []);
  useEffect(() => {
    runnerPoolsApi
      .list()
      .then(setPools)
      .catch(() => {
        // Runner pools are optional context; absence just means local-only.
      });
  }, []);
  useEffect(() => {
    api.getSystemSettings().then(setSystemSettings).catch(() => {
      // Workspace settings are best-effort context; missing is fine.
    });
  }, []);

  // Poll while any environment is still building.
  useEffect(() => {
    if (!environments?.some((e) => e.status === "pending" || e.status === "building")) {
      return;
    }
    const timer = window.setTimeout(load, 2500);
    return () => window.clearTimeout(timer);
  }, [environments]);

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
                  onChanged={load}
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
            load();
          }}
          workspaceCap={workspaceCap}
          rssSoftBudget={rssSoftBudget}
          pools={pools}
        />
      )}
    </div>
  );
}
