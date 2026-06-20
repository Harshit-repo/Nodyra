import { useRun } from "../../queries";
import type { RunInfo } from "../../types";

interface RunTimelineProps {
  selectedRunId: string | null;
}

export interface GanttBar {
  nodeId: string;
  status: string;
  leftPct: number;
  widthPct: number;
  durationMs: number | null;
}

export function buildGanttBars(run: RunInfo): GanttBar[] {
  const totalMs = run.node_runs.reduce((s, nr) => s + (nr.duration_ms ?? 0), 0);
  let cursor = 0;
  return run.node_runs.map((nr) => {
    const w = totalMs > 0 ? ((nr.duration_ms ?? 0) / totalMs) * 100 : 0;
    const bar: GanttBar = {
      nodeId: nr.node_id,
      status: nr.status,
      leftPct: cursor,
      widthPct: w,
      durationMs: nr.duration_ms ?? null,
    };
    cursor += w;
    return bar;
  });
}

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms}ms`;
}

function bottleneck(bars: GanttBar[]): GanttBar | null {
  return bars.reduce<GanttBar | null>((max, b) => {
    if (max == null || (b.durationMs ?? 0) > (max.durationMs ?? 0)) return b;
    return max;
  }, null);
}

export function RunTimeline({ selectedRunId }: RunTimelineProps) {
  const runQuery = useRun(selectedRunId);
  const run = runQuery.data as RunInfo | undefined;

  if (!selectedRunId) {
    return (
      <div style={{ padding: 16, color: "var(--ink-3)", fontSize: 12 }}>
        Select a run to view its timeline.
      </div>
    );
  }
  if (runQuery.isLoading || !run) {
    return <div style={{ padding: 16, color: "var(--ink-3)", fontSize: 12 }}>Loading…</div>;
  }

  const bars = buildGanttBars(run);
  const totalMs = run.node_runs.reduce((s, nr) => s + (nr.duration_ms ?? 0), 0);
  const neck = bottleneck(bars);

  return (
    <>
      <div className="sc-head">
        <span className="sc-head-title">Timeline</span>
        <span className="sc-head-badge">
          #{run.id.slice(0, 4)} · {fmtMs(totalMs)}
        </span>
      </div>
      <div className="sc-wf-body">
        <div className="sc-wf-ticks-row">
          <div className="sc-wf-tick-spacer" />
          <div className="sc-wf-ticks">
            <span className="sc-wf-tick" style={{ left: "0%" }}>
              0
            </span>
            <span className="sc-wf-tick" style={{ left: "50%" }}>
              {fmtMs(totalMs / 2)}
            </span>
            <span className="sc-wf-tick" style={{ left: "100%" }}>
              {fmtMs(totalMs)}
            </span>
          </div>
        </div>
        {bars.map((bar) => (
          <div key={bar.nodeId} className="sc-wf-row">
            <div className="sc-wf-name" title={bar.nodeId}>
              {bar.nodeId}
            </div>
            <div className="sc-wf-track">
              <div
                className={`sc-wf-bar ${bar.status === "success" ? "ok" : bar.status === "error" ? "error" : "skip"}`}
                style={{ left: `${bar.leftPct}%`, width: `${Math.max(bar.widthPct, 2)}%` }}
              >
                {bar.durationMs != null ? fmtMs(bar.durationMs) : ""}
              </div>
            </div>
          </div>
        ))}
        {neck && (
          <div className="sc-wf-critical">
            <div>
              Bottleneck:{" "}
              <span style={{ color: neck.status === "error" ? "var(--error)" : "var(--ink)" }}>
                {neck.nodeId}
              </span>
            </div>
            <div style={{ color: "var(--ink-3)" }}>{Math.round(neck.widthPct)}% of total time</div>
          </div>
        )}
      </div>
    </>
  );
}
