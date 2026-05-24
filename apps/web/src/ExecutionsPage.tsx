import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api, runEventsUrl } from "./api";
import { HomeHeader } from "./HomeHeader";
import type {
  NodeRunResult,
  RunInfo,
  RunListItem,
  WorkflowSummary,
} from "./types";

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.round(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return new Date(iso).toLocaleString();
}

function formatDuration(startedAt: string, finishedAt: string | null): string {
  if (!finishedAt) return "—";
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime();
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60000);
  const seconds = Math.round((ms % 60000) / 1000);
  return `${minutes}m ${seconds}s`;
}

export function ExecutionsPage() {
  const [params, setParams] = useSearchParams();
  const selectedRunId = params.get("run");
  const workflowId = params.get("workflow_id") || "";
  const statusFilter = params.get("status") || "";
  const triggerType = params.get("trigger_type") || "";

  const [runs, setRuns] = useState<RunListItem[] | null>(null);
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .listWorkflows()
      .then(setWorkflows)
      .catch(() => {
        /* non-fatal */
      });
  }, []);

  useEffect(() => {
    setRuns(null);
    api
      .listAllRuns({
        workflow_id: workflowId || undefined,
        status: statusFilter || undefined,
        trigger_type: triggerType || undefined,
      })
      .then(setRuns)
      .catch((err) => setError(String(err)));
  }, [workflowId, statusFilter, triggerType]);

  // Poll while any in-flight run exists, so the list reflects fresh statuses.
  const hasRunning = useMemo(
    () => runs?.some((r) => r.status === "running") ?? false,
    [runs],
  );
  useEffect(() => {
    if (!hasRunning) return;
    const timer = window.setInterval(() => {
      api
        .listAllRuns({
          workflow_id: workflowId || undefined,
          status: statusFilter || undefined,
          trigger_type: triggerType || undefined,
        })
        .then(setRuns)
        .catch(() => {
          /* keep last state on transient failures */
        });
    }, 3000);
    return () => window.clearInterval(timer);
  }, [hasRunning, workflowId, statusFilter, triggerType]);

  function setFilter(key: string, value: string): void {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  }

  function selectRun(id: string | null): void {
    const next = new URLSearchParams(params);
    if (id) next.set("run", id);
    else next.delete("run");
    setParams(next);
  }

  const filtered = workflowId || statusFilter || triggerType;

  return (
    <div className="home">
      <HomeHeader />
      <main className="home-main">
        <div className="home-bar">
          <h1>
            Executions
            {runs && <span className="home-count">{runs.length}</span>}
          </h1>
        </div>

        <div className="exec-filters">
          <select
            className="exec-filter"
            value={workflowId}
            onChange={(e) => setFilter("workflow_id", e.target.value)}
          >
            <option value="">All workflows</option>
            {workflows.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
          <select
            className="exec-filter"
            value={statusFilter}
            onChange={(e) => setFilter("status", e.target.value)}
          >
            <option value="">Any status</option>
            <option value="success">success</option>
            <option value="error">error</option>
            <option value="running">running</option>
            <option value="cancelled">cancelled</option>
          </select>
          <select
            className="exec-filter"
            value={triggerType}
            onChange={(e) => setFilter("trigger_type", e.target.value)}
          >
            <option value="">Any trigger</option>
            <option value="manual">manual</option>
            <option value="webhook">webhook</option>
            <option value="schedule">schedule</option>
          </select>
          {filtered && (
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={() => setParams({})}
            >
              Clear filters
            </button>
          )}
        </div>

        {error && <p className="error-text">{error}</p>}
        {!runs && !error && <p className="muted">Loading…</p>}
        {runs && runs.length === 0 && (
          <p className="muted">No runs match these filters yet.</p>
        )}

        {runs && runs.length > 0 && (
          <div className="exec-layout">
            <div className="exec-table-wrap">
              <table className="exec-table">
                <thead>
                  <tr>
                    <th>Workflow</th>
                    <th>Status</th>
                    <th>Trigger</th>
                    <th>Started</th>
                    <th>Duration</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => (
                    <tr
                      key={r.id}
                      className={selectedRunId === r.id ? "selected" : ""}
                      onClick={() => selectRun(r.id)}
                    >
                      <td>
                        <span className="exec-wf-name">
                          {r.workflow_name ?? "(deleted workflow)"}
                        </span>
                        <span className="exec-wf-version">
                          v{r.workflow_version}
                        </span>
                      </td>
                      <td>
                        <span className={`run-pill status-run-${r.status}`}>
                          {r.status}
                        </span>
                      </td>
                      <td>
                        <span className="exec-trigger">{r.trigger_type}</span>
                      </td>
                      <td title={r.started_at}>{relativeTime(r.started_at)}</td>
                      <td>{formatDuration(r.started_at, r.finished_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {selectedRunId && (
              <RunDetailPanel
                runId={selectedRunId}
                onClose={() => selectRun(null)}
              />
            )}
          </div>
        )}
      </main>
    </div>
  );
}

function RunDetailPanel({
  runId,
  onClose,
}: {
  runId: string;
  onClose: () => void;
}) {
  const [run, setRun] = useState<RunInfo | null>(null);
  const [error, setError] = useState("");
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    setRun(null);
    setError("");
    api
      .getRun(runId)
      .then(setRun)
      .catch((err) => setError(String(err)));
  }, [runId]);

  // Re-fetch on each live event while the run is in flight.
  useEffect(() => {
    if (!run || run.status !== "running") return;
    const ws = new WebSocket(runEventsUrl(runId));
    wsRef.current = ws;
    ws.onmessage = () => {
      api
        .getRun(runId)
        .then(setRun)
        .catch(() => {
          /* keep current state */
        });
    };
    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [runId, run?.status]);

  return (
    <aside className="exec-detail">
      <header className="exec-detail-head">
        <div>
          <h2>Run {runId.slice(0, 8)}</h2>
          {run && (
            <div className="exec-detail-sub">
              <span className={`run-pill status-run-${run.status}`}>
                {run.status}
              </span>
              <span className="muted"> · {run.trigger_type} · {run.mode}</span>
            </div>
          )}
        </div>
        <button
          type="button"
          className="btn btn-sm btn-ghost"
          onClick={onClose}
        >
          ✕
        </button>
      </header>

      {error && <p className="error-text">{error}</p>}
      {!run && !error && <p className="muted">Loading…</p>}

      {run && (
        <div className="exec-nodes">
          {run.node_runs.length === 0 ? (
            <p className="muted">No node runs recorded yet.</p>
          ) : (
            run.node_runs.map((n) => <NodeRunRow key={n.node_id} node={n} />)
          )}
        </div>
      )}
    </aside>
  );
}

function NodeRunRow({ node }: { node: NodeRunResult }) {
  const [open, setOpen] = useState(node.status === "error");
  const hasLogs = Boolean(node.logs && node.logs.length > 0);
  return (
    <div className={`exec-node${open ? " open" : ""}`}>
      <header className="exec-node-head" onClick={() => setOpen(!open)}>
        <span className="exec-node-toggle">{open ? "▾" : "▸"}</span>
        <span className="exec-node-id">{node.node_id}</span>
        <span className={`run-pill status-run-${node.status}`}>
          {node.status}
        </span>
        {typeof node.duration_ms === "number" && (
          <span className="exec-node-duration">{node.duration_ms}ms</span>
        )}
        {hasLogs && (
          <span className="exec-node-logs-count">{node.logs!.length} logs</span>
        )}
      </header>
      {open && (
        <div className="exec-node-body">
          {node.error && (
            <div className="exec-node-section">
              <div className="exec-node-section-label">Error</div>
              <pre className="data-json">{node.error}</pre>
            </div>
          )}
          {node.output !== undefined && node.output !== null && (
            <div className="exec-node-section">
              <div className="exec-node-section-label">Output</div>
              <pre className="data-json">
                {JSON.stringify(node.output, null, 2)}
              </pre>
            </div>
          )}
          {hasLogs && (
            <div className="exec-node-section">
              <div className="exec-node-section-label">Logs</div>
              <pre className="data-json data-logs">
                {node.logs!.join("\n")}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
