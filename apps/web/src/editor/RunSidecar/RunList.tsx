import { useState } from "react";
import { useRuns, useRerunRunMutation } from "../../queries";
import { useEditor, isTriggerManifest } from "../store";
import type { RunInfo } from "../../types";
import type { SidecarTab } from "./useRunSidecar";

interface RunListProps {
  workflowId: string;
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  pinnedNodeId: string | null;
  onPinNode: (nodeId: string) => void;
  onSwitchTab: (tab: SidecarTab) => void;
}

type Filter = "all" | "errors" | "manual";

function fmt(isoString: string): string {
  return new Date(isoString).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function durMs(run: RunInfo): string {
  if (!run.finished_at) return "…";
  const ms = new Date(run.finished_at).getTime() - new Date(run.started_at).getTime();
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

const statusColor: Record<string, string> = {
  success: "var(--ok)",
  error: "var(--error)",
  running: "var(--accent)",
};

export function RunList({
  workflowId,
  selectedRunId,
  onSelectRun,
  pinnedNodeId,
  onPinNode,
  onSwitchTab,
}: RunListProps) {
  const [filter, setFilter] = useState<Filter>("all");
  const runsQuery = useRuns(workflowId, { staleTime: 5000 });
  const runs: RunInfo[] = (runsQuery.data ?? []) as RunInfo[];

  const rerunMutation = useRerunRunMutation();

  const selectedRun = runs.find((r) => r.id === selectedRunId) ?? null;

  const filtered = runs.filter((r) => {
    if (filter === "errors") return r.status === "error";
    if (filter === "manual") return r.trigger_type === "manual";
    return true;
  });

  function selectRun(run: RunInfo) {
    onSelectRun(run.id);
  }

  function copyInputs() {
    if (!selectedRun) return;
    const { nodes } = useEditor.getState();
    const triggerId = nodes.find((n) => isTriggerManifest(n.data.manifest))?.id;
    const triggerOut =
      selectedRun.node_runs.find((nr) => nr.node_id === triggerId)?.output ??
      selectedRun.node_runs[0]?.output ??
      {};
    void navigator.clipboard.writeText(JSON.stringify(triggerOut, null, 2));
  }

  return (
    <>
      <div className="sc-head">
        <span className="sc-head-title">Run history</span>
        <span className="sc-head-badge">{runs.length} runs</span>
      </div>
      <div className="sc-filter-row">
        {(["all", "errors", "manual"] as const).map((f) => (
          <button
            key={f}
            className={`sc-filter-chip${filter === f ? " on" : ""}`}
            onClick={() => setFilter(f)}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>
      <div className="sc-run-list">
        {runsQuery.isLoading && (
          <div style={{ padding: "12px", color: "var(--ink-3)", fontSize: 12 }}>Loading…</div>
        )}
        {!runsQuery.isLoading && filtered.length === 0 && (
          <div style={{ padding: "12px", color: "var(--ink-3)", fontSize: 12 }}>No runs yet.</div>
        )}
        {filtered.map((r) => (
          <div
            key={r.id}
            className={`sc-run-row${selectedRunId === r.id ? " selected" : ""}`}
            onClick={() => selectRun(r)}
          >
            <div className="sc-run-top">
              <span className={`run-pill status-run-${r.status}`}>{r.status}</span>
              <span className="sc-run-time">{fmt(r.started_at)}</span>
              <span className="sc-run-dur">{durMs(r)}</span>
            </div>
            <div className="sc-run-meta">
              <span className="sc-trigger-chip">{r.trigger_type}</span>
              {new Date(r.started_at).toLocaleDateString()} · #{r.id.slice(0, 4)}
            </div>
          </div>
        ))}
      </div>

      {selectedRun && (
        <div className="sc-inspect">
          <div className="sc-insp-head">
            <span className="sc-insp-label">Node results</span>
            <button
              className="sc-insp-timeline-btn"
              onClick={() => onSwitchTab("timeline")}
              title="View timing waterfall"
            >
              ◫ Timeline
            </button>
          </div>
          {selectedRun.node_runs.map((nr) => (
            <div
              key={nr.node_id}
              className={`sc-node-row${pinnedNodeId === nr.node_id ? " pinned" : ""}`}
              onClick={() => onPinNode(nr.node_id)}
            >
              <div
                className="sc-node-dot"
                style={{ background: statusColor[nr.status] ?? "var(--border)" }}
              />
              <span className="sc-node-name">{nr.node_id}</span>
              <span
                className="sc-node-status"
                style={{ color: statusColor[nr.status] ?? "var(--ink-3)" }}
              >
                {nr.status === "success" ? "✓" : nr.status === "error" ? "✗" : "–"}
              </span>
              <span className="sc-node-ms">
                {nr.duration_ms != null ? `${nr.duration_ms}ms` : "—"}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="sc-actions">
        <button
          className="sc-action-btn"
          disabled={!selectedRunId || rerunMutation.isPending}
          onClick={() => selectedRunId && rerunMutation.mutate(selectedRunId)}
        >
          ↺ Re-run
        </button>
        <button className="sc-action-btn primary" disabled={!selectedRun} onClick={copyInputs}>
          Copy inputs →
        </button>
      </div>
    </>
  );
}
