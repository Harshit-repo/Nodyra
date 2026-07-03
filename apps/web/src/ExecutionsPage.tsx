import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { MagnifyingGlass } from "@phosphor-icons/react";

import { SkeletonRows } from "./Skeleton";

import {
  errorMessage,
  type QueueStats,
  type RuntimeModeStatus,
  type RunTimeline,
  subscribeToRunEvents,
} from "./api";
import {
  queryKeys,
  useAllRuns,
  useQueueStats,
  useReplayRunMutation,
  useRetryRunMutation,
  useRerunRunMutation,
  useRun,
  useRuntimeMode,
  useRunTimeline,
  useWorkflows,
} from "./queries";
import { RunApprovalsPanel } from "./RunApprovalsPanel";
import type {
  NodeRunResult,
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
  const runtimeQuery = useRuntimeMode({ refetchInterval: 5000 });
  const queueQuery = useQueueStats({ refetchInterval: 5000 });
  const runtime: RuntimeModeStatus | null = runtimeQuery.data ?? null;
  const queue: QueueStats | null = queueQuery.data ?? null;
  const err =
    runtimeQuery.isError && !runtimeQuery.data
      ? errorMessage(runtimeQuery.error)
      : queueQuery.isError && !queueQuery.data
      ? errorMessage(queueQuery.error)
      : "";

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
            {queue.waiting} waiting · {queue.dead_lettered} dead-lettered ·{" "}
            {queue.failed} failed
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
        {queue.by_org && Object.keys(queue.by_org).length > 0 && (
          <div className="ops-card">
            <div className="ops-card-label">By organization</div>
            <div className="ops-card-value small">
              {Object.keys(queue.by_org).length} active
            </div>
            <div className="ops-card-sub">
              {Object.entries(queue.by_org)
                .slice(0, 4)
                .map(([orgId, counts]) => {
                  const parked = counts.quota_parked ?? 0;
                  const active = (counts.leased ?? 0) + (counts.running ?? 0);
                  return (
                    <span
                      key={orgId}
                      title={
                        parked
                          ? `${parked} runs parked by this org's concurrency quota`
                          : undefined
                      }
                      style={{ marginRight: 8 }}
                    >
                      {orgId}: {active} active
                      {counts.queued ? ` · ${counts.queued} queued` : ""}
                      {parked ? ` · ⚠ ${parked} at quota` : ""}
                    </span>
                  );
                })}
            </div>
          </div>
        )}
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
  const timelineQuery = useRunTimeline(runId);
  const timeline: RunTimeline | null = timelineQuery.data ?? null;
  const err =
    timelineQuery.isError && !timelineQuery.data
      ? errorMessage(timelineQuery.error)
      : "";

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
            <span className="run-timeline-type">{formatTimelineType(e.type)}</span>
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
  if (e.type.startsWith("agent_")) {
    return formatAgentTimelineSummary(e.type, d);
  }
  if (e.type.startsWith("guardrail_")) {
    return formatGuardrailTimelineSummary(e.type, d);
  }
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

function formatTimelineType(type: string): string {
  const labels: Record<string, string> = {
    agent_action_requested: "Agent action",
    agent_tool_started: "Agent tool started",
    agent_tool_approval_required: "Agent approval required",
    agent_tool_auto_approved: "Agent auto-approved",
    agent_tool_finished: "Agent tool finished",
    agent_action_completed: "Agent resumed",
    agent_tool_approval_decided: "Agent approval decided",
    agent_resume_prepared: "Agent resume prepared",
    provider_trigger_received: "Provider trigger",
    guardrail_blocked: "Guardrail blocked",
    guardrail_redacted: "Guardrail redacted",
    node_started: "Node started",
    node_finished: "Node finished",
    queue_failed: "Queue failed",
    dead_lettered: "Dead lettered",
  };
  return labels[type] || type.replaceAll("_", " ");
}

function formatAgentTimelineSummary(
  type: string,
  data: Record<string, unknown>,
): string {
  const parts: string[] = [];
  if (typeof data.agent_node_id === "string") parts.push(data.agent_node_id);
  if (typeof data.tool_name === "string") parts.push(data.tool_name);
  if (typeof data.step === "number") parts.push(`step ${data.step}`);
  if (typeof data.status === "string") parts.push(data.status);
  if (typeof data.message === "string") parts.push(data.message);
  if (type === "agent_action_requested" && Array.isArray(data.tool_calls)) {
    const count = data.tool_calls.length;
    parts.push(`${count} tool call${count === 1 ? "" : "s"}`);
  }
  if (type === "agent_resume_prepared") {
    const cached = Array.isArray(data.cached_node_ids)
      ? data.cached_node_ids.length
      : 0;
    const skipped = Array.isArray(data.skipped_cache_nodes)
      ? data.skipped_cache_nodes.length
      : 0;
    parts.push(`${cached} cached`);
    if (skipped > 0) parts.push(`${skipped} recomputed`);
  }
  if (type === "provider_trigger_received") {
    if (typeof data.provider === "string") parts.push(data.provider);
    if (typeof data.event === "string") parts.push(data.event);
    if (typeof data.repository === "string") parts.push(data.repository);
    if (typeof data.delivery_id === "string") parts.push(data.delivery_id);
    if (typeof data.response_status === "number") {
      parts.push(`HTTP ${data.response_status}`);
    }
    if (typeof data.latency_ms === "number") parts.push(`${data.latency_ms}ms`);
  }
  const toolResult = data.tool_result;
  if (
    type === "agent_tool_finished" &&
    toolResult &&
    typeof toolResult === "object" &&
    "is_error" in toolResult &&
    (toolResult as { is_error?: unknown }).is_error === true
  ) {
    parts.push("error");
  }
  return parts.join(" · ");
}

function formatGuardrailTimelineSummary(
  type: string,
  data: Record<string, unknown>,
): string {
  const parts: string[] = [];
  if (typeof data.node_id === "string") parts.push(data.node_id);
  if (typeof data.reason === "string") parts.push(data.reason.replaceAll("_", " "));
  if (typeof data.replacement_count === "number") {
    parts.push(`${data.replacement_count} replacement${data.replacement_count === 1 ? "" : "s"}`);
  }
  if (type === "guardrail_blocked" && typeof data.observed_length === "number") {
    parts.push(`${data.observed_length} chars`);
  }
  return parts.join(" · ");
}

type RunTimelineEvent = RunTimeline["events"][number];

export function ExecutionsPage() {
  const [params, setParams] = useSearchParams();
  const selectedRunId = params.get("run");
  const workflowId = params.get("workflow_id") || "";
  const statusFilter = params.get("status") || "";
  const triggerType = params.get("trigger_type") || "";

  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setQuery(searchInput), 300);
    return () => clearTimeout(timer);
  }, [searchInput]);
  const [dateRange, setDateRange] = useState("all");

  const since = useMemo(() => {
    if (dateRange === "all") return undefined;
    const now = Date.now();
    const ms =
      dateRange === "24h" ? 86_400_000
      : dateRange === "7d" ? 604_800_000
      : 2_592_000_000; // 30d
    return new Date(now - ms).toISOString();
  }, [dateRange]);

  const runFilters = useMemo(
    () => ({
      workflow_id: workflowId || undefined,
      status: statusFilter || undefined,
      trigger_type: triggerType || undefined,
      since,
    }),
    [workflowId, statusFilter, triggerType, since],
  );
  const workflowsQuery = useWorkflows();
  const runsQuery = useAllRuns(runFilters, {
    refetchInterval: (query) =>
      query.state.data?.some((run) => run.status === "running") ? 3000 : false,
  });
  const workflows = workflowsQuery.data ?? [];
  const runs = runsQuery.data ?? null;
  const error =
    runsQuery.isError && !runsQuery.data ? errorMessage(runsQuery.error) : "";

  const visible = useMemo(() => {
    const rows = runs ?? [];
    if (!query.trim()) return rows;
    const q = query.trim().toLowerCase();
    return rows.filter(
      (r) =>
        (r.workflow_name ?? "").toLowerCase().includes(q) ||
        r.id.toLowerCase().includes(q),
    );
  }, [runs, query]);

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

  const filtered = workflowId || statusFilter || triggerType || dateRange !== "all";

  function clearFilters(): void {
    setParams({});
    setDateRange("all");
    setSearchInput("");
    setQuery("");
  }

  return (
    <div className="home">
      <main className="home-main">
        <div className="home-bar">
          <h1>
            Executions
            {runs && <span className="home-count">{runs.length}</span>}
          </h1>
        </div>

        <OpsDashboard />

        <div className="home-filters">
          <div className="home-search-row">
            <div className="search-input-wrap">
              <MagnifyingGlass size={16} className="search-icon" aria-hidden="true" />
              <input
                className="field-input"
                aria-label="Search executions"
                placeholder="Search by workflow name or run ID..."
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
              />
            </div>
            <select
              className="field-input"
              aria-label="Time range"
              value={dateRange}
              onChange={(e) => setDateRange(e.target.value)}
              style={{ width: 150, minWidth: 150 }}
            >
              <option value="all">All time</option>
              <option value="24h">Last 24 hours</option>
              <option value="7d">Last 7 days</option>
              <option value="30d">Last 30 days</option>
            </select>
          </div>
          <div className="home-filter-chips">
            {[
              { label: "All", value: "", cls: "chip-all" },
              { label: "Running", value: "running", cls: "chip-running" },
              { label: "Success", value: "success", cls: "chip-success" },
              { label: "Failed", value: "error", cls: "chip-failed" },
              { label: "Waiting", value: "waiting", cls: "chip-waiting" },
              { label: "Cancelled", value: "cancelled", cls: "chip-cancelled" },
            ].map((chip) => (
              <button
                key={chip.value}
                type="button"
                className={`home-status-chip ${chip.cls}${statusFilter === chip.value ? " is-selected" : ""}`}
                aria-pressed={statusFilter === chip.value}
                onClick={() => setFilter("status", statusFilter === chip.value ? "" : chip.value)}
              >
                {chip.label}
              </button>
            ))}
            <select
              className="exec-filter"
              aria-label="Filter by workflow"
              value={workflowId}
              onChange={(e) => setFilter("workflow_id", e.target.value)}
              style={{ marginLeft: "auto" }}
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
              aria-label="Filter by trigger type"
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
                onClick={clearFilters}
              >
                Clear filters
              </button>
            )}
          </div>
        </div>

        {error && <p className="error-text">{error}</p>}
        {!runs && !error && (
          <div className="exec-table-wrap" aria-label="Loading runs">
            <SkeletonRows count={8} />
          </div>
        )}
        {runs && runs.length === 0 && (
          <p className="muted">No runs match these filters yet.</p>
        )}

        {visible && visible.length === 0 && runs && runs.length > 0 && (
          <div className="empty-state">
            <h2>No matches</h2>
            <p className="muted">Adjust search or filters.</p>
            <button className="btn" type="button" onClick={clearFilters}>
              Reset filters
            </button>
          </div>
        )}

        {visible && visible.length > 0 && (
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
                  {visible.map((r) => (
                    <tr
                      key={r.id}
                      className={selectedRunId === r.id ? "selected" : ""}
                      onClick={() => selectRun(r.id)}
                    >
                      <td>
                        <span
                          className="exec-wf-name"
                          title={r.workflow_name ?? undefined}
                        >
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
  const queryClient = useQueryClient();
  const runQuery = useRun(runId);
  const run = runQuery.data ?? null;
  const [actionError, setActionError] = useState("");
  const [actionPending, setActionPending] = useState<
    "rerun" | "retry" | "replay" | null
  >(null);
  const [replayNodeId, setReplayNodeId] = useState<string | null>(null);
  const rerunMutation = useRerunRunMutation();
  const retryMutation = useRetryRunMutation();
  const replayMutation = useReplayRunMutation();
  const error =
    actionError ||
    (runQuery.isError && !runQuery.data ? errorMessage(runQuery.error) : "");

  useEffect(() => {
    setActionPending(null);
    setReplayNodeId(null);
    setActionError("");
  }, [runId]);

  async function rerun(): Promise<void> {
    setActionPending("rerun");
    try {
      const { run_id } = await rerunMutation.mutateAsync(runId);
      onJump(run_id);
    } catch (e) {
      setActionError(errorMessage(e));
    } finally {
      setActionPending(null);
    }
  }

  async function replayFromNode(fromNodeId: string): Promise<void> {
    setActionPending("replay");
    setReplayNodeId(fromNodeId);
    try {
      const { run_id } = await replayMutation.mutateAsync({ runId, fromNodeId });
      onJump(run_id);
    } catch (e) {
      setActionError(errorMessage(e));
    } finally {
      setActionPending(null);
      setReplayNodeId(null);
    }
  }

  async function retry(): Promise<void> {
    setActionPending("retry");
    try {
      const { run_id } = await retryMutation.mutateAsync(runId);
      onJump(run_id);
    } catch (e) {
      setActionError(errorMessage(e));
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
        void queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
        void queryClient.invalidateQueries({
          queryKey: queryKeys.runTimeline(runId),
        });
      },
    });
    return () => {
      handle.close();
    };
  }, [queryClient, runId, run?.status]);

  function refreshRun(): void {
    void queryClient.invalidateQueries({ queryKey: queryKeys.run(runId) });
  }

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
            aria-label="Close"
          >
            ✕
          </button>
        </div>
      </header>

      {error && <p className="error-text">{error}</p>}
      {!run && !error && (
        <div className="exec-nodes" aria-label="Loading run">
          {Array.from({ length: 4 }).map((_, index) => (
            <div className="exec-node skeleton-row" key={index}>
              <div className="exec-node-head">
                <span className="skeleton-line short" />
              </div>
            </div>
          ))}
        </div>
      )}

      {run && (
        <RunApprovalsPanel
          runId={runId}
          runStatus={run.status}
          onChanged={refreshRun}
        />
      )}

      {run && (
        <div className="exec-nodes">
          {run.node_runs.length === 0 ? (
            <p className="muted">No node runs recorded yet.</p>
          ) : (
            run.node_runs.map((n, i) => (
              <NodeRunRow
                key={
                  n.iteration_path && n.iteration_path.length > 0
                    ? `${n.node_id}#${n.iteration_path.join(".")}`
                    : `${n.node_id}#${i}`
                }
                node={n}
                canReplay={run.status === "error" || run.status === "failed"}
                disabled={actionPending !== null}
                replayPending={
                  actionPending === "replay" && replayNodeId === n.node_id
                }
                onReplayFromNode={(nodeId) => void replayFromNode(nodeId)}
              />
            ))
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

function NodeRunRow({
  node,
  canReplay,
  disabled,
  replayPending,
  onReplayFromNode,
}: {
  node: NodeRunResult;
  canReplay: boolean;
  disabled: boolean;
  replayPending: boolean;
  onReplayFromNode: (nodeId: string) => void;
}) {
  const [open, setOpen] = useState(node.status === "error");
  const hasLogs = Boolean(node.logs && node.logs.length > 0);
  const isReplayableFailure = canReplay && ["error", "failed"].includes(node.status);
  return (
    <div className={`exec-node${open ? " open" : ""}`}>
      <header className="exec-node-head" onClick={() => setOpen(!open)}>
        <span className="exec-node-toggle">{open ? "▾" : "▸"}</span>
        <span className="exec-node-id">{node.node_id}</span>
        {node.iteration_path && node.iteration_path.length > 0 && (
          <span className="exec-node-iteration" title="Loop iteration">
            {node.iteration_path.length === 1
              ? `iter ${node.iteration_path[0]}`
              : `iter [${node.iteration_path.join(", ")}]`}
          </span>
        )}
        <span className={`run-pill status-run-${node.status}`}>
          {node.status}
        </span>
        {typeof node.duration_ms === "number" && (
          <span className="exec-node-duration">{node.duration_ms}ms</span>
        )}
        {hasLogs && (
          <span className="exec-node-logs-count">{node.logs!.length} logs</span>
        )}
        {isReplayableFailure && (
          <button
            type="button"
            className="btn btn-sm exec-node-replay"
            onClick={(event) => {
              event.stopPropagation();
              onReplayFromNode(node.node_id);
            }}
            disabled={disabled}
            title="Replay from this node and reuse successful upstream outputs"
          >
            {replayPending ? "Starting..." : "Replay from here"}
          </button>
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
