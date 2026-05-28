import { useCallback, useEffect, useState } from "react";
import { runnerPoolsApi } from "./api";
import { useCan } from "./permissions";
import type { RegistrationTokenResponse, RunnerInfo, RunnerPoolInfo } from "./types";

function statusDot(status: string) {
  const color =
    status === "online"
      ? "bg-green-500"
      : status === "busy"
        ? "bg-yellow-500"
        : "bg-gray-400";
  return <span className={`inline-block w-2 h-2 rounded-full ${color} mr-1`} />;
}

function PoolCard({
  pool,
  onDelete,
  canWrite,
}: {
  pool: RunnerPoolInfo;
  onDelete: () => void;
  canWrite: boolean;
}) {
  const [runners, setRunners] = useState<RunnerInfo[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [token, setToken] = useState<RegistrationTokenResponse | null>(null);
  const [loadingToken, setLoadingToken] = useState(false);

  const loadRunners = useCallback(async () => {
    try {
      const rs = await runnerPoolsApi.listRunners(pool.id);
      setRunners(rs);
    } catch {
      /* ignore */
    }
  }, [pool.id]);

  useEffect(() => {
    if (expanded) loadRunners();
  }, [expanded, loadRunners]);

  const generateToken = async () => {
    setLoadingToken(true);
    try {
      const t = await runnerPoolsApi.createRegistrationToken(pool.id);
      setToken(t);
      await loadRunners();
    } finally {
      setLoadingToken(false);
    }
  };

  return (
    <div className="border rounded-lg p-4 bg-white dark:bg-zinc-900 dark:border-zinc-700">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-semibold text-sm">{pool.name}</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            <span className="capitalize">{pool.provider}</span> ·{" "}
            {pool.online_count}/{pool.runner_count} online · max{" "}
            {pool.max_concurrent_runs} concurrent
          </p>
        </div>
        <div className="flex gap-2">
          <button
            className="text-xs underline text-muted-foreground"
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? "Hide" : "Show"} runners
          </button>
          {canWrite && (
            <button
              className="text-xs text-red-500 underline"
              onClick={onDelete}
            >
              Delete
            </button>
          )}
        </div>
      </div>

      {expanded && (
        <div className="mt-3 space-y-2">
          {runners.length === 0 && (
            <p className="text-xs text-muted-foreground">No runners registered yet.</p>
          )}
          {runners.map((r) => (
            <div
              key={r.id}
              className="flex items-center justify-between text-xs bg-zinc-50 dark:bg-zinc-800 rounded px-2 py-1"
            >
              <span>
                {statusDot(r.status)}
                {r.name}
              </span>
              <span className="text-muted-foreground">
                {r.current_runs}/{r.max_concurrent_runs} runs ·{" "}
                {r.last_seen_at
                  ? new Date(r.last_seen_at).toLocaleString()
                  : "never seen"}
              </span>
            </div>
          ))}

          {canWrite && (
            <div className="pt-2">
              <button
                className="text-xs bg-primary text-primary-foreground rounded px-3 py-1 disabled:opacity-50"
                disabled={loadingToken}
                onClick={generateToken}
              >
                {loadingToken ? "Generating…" : "Generate Registration Token"}
              </button>
              {token && (
                <div className="mt-2 p-2 bg-zinc-100 dark:bg-zinc-800 rounded text-xs font-mono break-all">
                  <p className="font-semibold mb-1 font-sans">
                    Install and register a new runner:
                  </p>
                  <pre className="whitespace-pre-wrap">{`pip install noodle-runner
noodle-runner register \\
  --api-url ${window.location.origin} \\
  --token ${token.token} \\
  --name my-runner
noodle-runner start`}</pre>
                  <p className="mt-1 text-muted-foreground font-sans">
                    Token expires:{" "}
                    {new Date(token.expires_at).toLocaleString()}
                  </p>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function RunnerPoolsPage() {
  const [pools, setPools] = useState<RunnerPoolInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newProvider, setNewProvider] = useState("agent");
  const [creating, setCreating] = useState(false);
  const canWrite = useCan("runner_pool:write");

  const load = useCallback(async () => {
    try {
      const ps = await runnerPoolsApi.list();
      setPools(ps);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      await runnerPoolsApi.create({ name: newName.trim(), provider: newProvider });
      setNewName("");
      setShowCreate(false);
      await load();
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (poolId: string) => {
    if (!confirm("Delete this runner pool and all its registered runners?")) return;
    await runnerPoolsApi.delete(poolId);
    await load();
  };

  return (
    <div className="max-w-3xl mx-auto py-8 px-4 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Runner Pools</h1>
        {canWrite && (
          <button
            className="text-sm bg-primary text-primary-foreground rounded px-3 py-1.5"
            onClick={() => setShowCreate((v) => !v)}
          >
            + New pool
          </button>
        )}
      </div>

      {showCreate && (
        <div className="border rounded-lg p-4 bg-white dark:bg-zinc-900 dark:border-zinc-700 space-y-3">
          <h2 className="font-semibold text-sm">New runner pool</h2>
          <div>
            <label className="text-xs text-muted-foreground block mb-1">Name</label>
            <input
              className="w-full border rounded px-2 py-1.5 text-sm dark:bg-zinc-800 dark:border-zinc-600"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="production-pool"
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground block mb-1">Provider</label>
            <select
              className="w-full border rounded px-2 py-1.5 text-sm dark:bg-zinc-800 dark:border-zinc-600"
              value={newProvider}
              onChange={(e) => setNewProvider(e.target.value)}
            >
              <option value="agent">Agent (VM / EC2)</option>
              <option value="docker">Docker</option>
              <option value="kubernetes">Kubernetes</option>
            </select>
          </div>
          <div className="flex gap-2">
            <button
              className="text-sm bg-primary text-primary-foreground rounded px-3 py-1.5 disabled:opacity-50"
              disabled={creating || !newName.trim()}
              onClick={handleCreate}
            >
              {creating ? "Creating…" : "Create"}
            </button>
            <button
              className="text-sm border rounded px-3 py-1.5"
              onClick={() => setShowCreate(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : pools.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No runner pools yet.{" "}
          {canWrite
            ? "Create one to start dispatching workflows to remote machines."
            : "Ask an admin to create one."}
        </p>
      ) : (
        <div className="space-y-3">
          {pools.map((pool) => (
            <PoolCard
              key={pool.id}
              pool={pool}
              canWrite={canWrite}
              onDelete={() => handleDelete(pool.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
