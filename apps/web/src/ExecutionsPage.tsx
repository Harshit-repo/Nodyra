import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  api,
  type QueueStats,
  type RunStreamHandle,
  type RuntimeModeStatus,
  type RunTimeline,
  subscribeToRunEvents,
} from "./api";
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

function formatAge(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function OpsDashboard() {
  const [runtime, setRuntime] = useState<RuntimeModeStatus | null>(null);
  const [queue, setQueue] = useState<QueueStats | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const [r, q] = await Promise.all([api.runtimeMode(), api.queueStats()]);
        if (cancelled) return;
        setRuntime(r);
        setQueue(q);
        setErr("");
      } catch (e) {
        if (!cancelled) setErr(String(e));
      }
    }
    tick();
    const t = window.setInterval(tick, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, []);

  if (err && !runtime && !queue) {
    return <p className="error-text">Ops: {err}</p>;
  }
  if (!runtime || !queue) {
    return <p className="muted">Loading ops…</p>;
  }

  const inFlight = queue.leased + queue.running;
  const oldestLabel = formatAge(queue.oldest_queued_age_seconds);
  const oldestWarn =
    queue.oldest_queued_age_seconds !== null &&
    queue.oldest_queued_age_seconds > 60;

  return (
    <section className="ops-dash">
      <div className="ops-cards">
        <div className="ops-card">
          <div className="ops-card-label">Queue depth</div>
          <div className="ops-card-value">{queue.queued}</div>
          <div className="ops-card-sub">
            {queue.dead_lettered} dead-lettered · {queue.failed} failed
          </div>
        </div>
        <div className="ops-card">
          <div className="ops-card-label">Oldest queued</div>
          <div className={`ops-card-value${oldestWarn ? " warn" : ""}`}>
            {oldestLabel}
          </div>
          <div className="ops-card-sub">
            {oldestWarn ? (
              <span
                className="backpressure-link"
                title="This run waited in the queue because the concurrency limit was reached. Check Settings to adjust max_concurrent_runs."
                style={{ cursor: "pointer", textDecoration: "underline dotted" }}
                onClick={() => window.location.assign("/settings")}
              >
                ⚠ backpressure
              </span>
            ) : (
              "fresh"
            )}
          </div>
        </div>
        <div className="ops-card">
          <div className="ops-card-label">In-flight</div>
          <div className="ops-card-value">{inFlight}</div>
          <div className="ops-card-sub">
            {queue.leased} leased · {queue.running} running
          </div>
        </div>
        <div className="ops-card">
          <div className="ops-card-label">Runtime</div>
          <div className="ops-card-value small">
            {runtime.mode}
            {runtime.allow_insecure ? " (insecure)" : ""}
          </div>
          <div className="ops-card-sub">
            db={runtime.database_dialect} · queue={runtime.queue_backend} ·
            artifacts={runtime.artifact_backend}
          </div>
        </div>
        <div className="ops-card">
          <div className="ops-card-label">Topology</div>
          <div className="ops-card-value small">
            sched={runtime.scheduler_role} · web={runtime.webhook_role}
          </div>
          <div className="ops-card-sub">
            providers:{" "}
            {runtime.runner_providers.length
              ? runtime.runner_providers.join(", ")
              : "none"}
          </div>
        </div>
      </div>
      {runtime.warnings.length > 0 && (
        <ul className="ops-warnings">
          {runtime.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function RunTimelinePanel({ runId }: { runId: string }) {
  const [timeline, setTimeline] = useState<RunTimeline | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    setTimeline(null);
    setErr("");
    api
      .runTimeline(runId)
      .then(setTimeline)
      .catch((e) => setErr(String(e)));
  }, [runId]);

  if (err) return <p className="error-text">Timeline: {err}</p>;
  if (!timeline) return <p className="muted">Loading timeline…</p>;
  if (timeline.events.length === 0) {
    return <p className="muted">No timeline events yet.</p>;
  }
  return (
    <ol className="run-timeline">
      {timeline.events.map((e, i) => {
        const summary = formatTimelineSummary(e);
        return (
          <li key={i} className={`run-timeline-event evt-${e.type}`}>
            <span className="run-timeline-type">{e.type}</span>
            {summary && <span className="run-timeline-summary">{summary}</span>}
            {e.ts && (
              <span className="run-timeline-ts">
                {new Date(e.ts).toLocaleTimeString()}
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function formatTimelineSummary(e: RunTimelineEvent): string {
  const d = e.data as Record<string, unknown>;
  if (typeof d.node_id === "string") {
    const parts: string[] = [d.node_id as string];
    if (typeof d.duration_ms === "number") {
      parts.push(`${d.duration_ms}ms`);
    }
    if (typeof d.error === "string") parts.push(d.error as string);
    return parts.join(" · ");
  }
  if (typeof d.attempt === "number") return `attempt ${d.attempt}`;
  if (typeof d.reason === "string") return d.reason as string;
  return "";
}

type RunTimelineEvent = RunTimeline["events"][number];

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

        <OpsDashboard />

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
                onJump={(id) => selectRun(id)}
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
  onJump,
}: {
  runId: string;
  onClose: () => void;
  onJump: (id: string) => void;
}) {
  const [run, setRun] = useState<RunInfo | null>(null);
  const [error, setError] = useState("");
  const [actionPending, setActionPending] = useState<"rerun" | "retry" | null>(
    null,
  );
  const wsRef = useRef<RunStreamHandle | null>(null);

  useEffect(() => {
    setRun(null);
    setError("");
    setActionPending(null);
    api
      .getRun(runId)
      .then(setRun)
      .catch((err) => setError(String(err)));
  }, [runId]);

  async function rerun(): Promise<void> {
    setActionPending("rerun");
    try {
      const { run_id } = await api.rerunRun(runId);
      onJump(run_id);
    } catch (e) {
      setError(String(e));
    } finally {
      setActionPending(null);
    }
  }

  async function retry(): Promise<void> {
    setActionPending("retry");
    try {
      const { run_id } = await api.retryRun(runId);
      onJump(run_id);
    } catch (e) {
      setError(String(e));
    } finally {
      setActionPending(null);
    }
  }

  // Re-fetch on each live event while the run is in flight. Uses the
  // shared reconnect helper so transient WebSocket drops are recovered
  // automatically instead of leaving the panel stuck on stale data.
  useEffect(() => {
    if (!run || run.status !== "running") return;
    const handle = subscribeToRunEvents(runId, {
      onMessage: () => {
        api
          .getRun(runId)
          .then(setRun)
          .catch(() => {
            /* keep current state */
          });
      },
    });
    wsRef.current = handle;
    return () => {
      handle.close();
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
        <div className="exec-detail-actions">
          {run && run.status !== "running" && (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => void rerun()}
              disabled={actionPending !== null}
              title="Run the same workflow again with the same trigger input"
            >
              {actionPending === "rerun" ? "Starting…" : "↻ Re-run"}
            </button>
          )}
          {run && (run.status === "error" || run.status === "failed") && (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => void retry()}
              disabled={actionPending !== null}
              title="Re-run only the failed node + downstream, reusing prior successful outputs"
            >
              {actionPending === "retry" ? "Starting…" : "↺ Retry from failure"}
            </button>
          )}
          {run && (run.status === "error" || run.status === "failed") && (
            <a
              className="btn btn-sm"
              href={`/workflows/${run.workflow_id}?debug_run=${run.id}`}
              title="Open this run in the editor with the failed node selected and upstream outputs pinned"
            >
              🐞 Debug in editor
            </a>
          )}
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            onClick={onClose}
          >
            ✕
          </button>
        </div>
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

      {run && (
        <details className="exec-timeline-wrap">
          <summary>Lifecycle timeline</summary>
          <RunTimelinePanel runId={runId} />
        </details>
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
