import { useRuns, useRun } from "../../queries";
import type { RunInfo } from "../../types";

interface RunDiffProps {
  workflowId: string;
  diffPair: [string, string] | null;
  onChangePair: (pair: [string, string]) => void;
}

export type RowChange = "improved" | "worse" | "changed" | "none";

export interface DiffRow {
  nodeId: string;
  aStatus: string | null;
  bStatus: string | null;
  aDurationMs: number | null;
  bDurationMs: number | null;
  change: RowChange;
}

export function diffNodeRows(a: RunInfo, b: RunInfo): DiffRow[] {
  const allIds = Array.from(
    new Set([...a.node_runs.map((n) => n.node_id), ...b.node_runs.map((n) => n.node_id)]),
  );
  return allIds.map((nodeId) => {
    const aNode = a.node_runs.find((n) => n.node_id === nodeId) ?? null;
    const bNode = b.node_runs.find((n) => n.node_id === nodeId) ?? null;

    const aStatus = aNode?.status ?? null;
    const bStatus = bNode?.status ?? null;
    const aDurationMs = aNode?.duration_ms ?? null;
    const bDurationMs = bNode?.duration_ms ?? null;

    let change: RowChange = "none";
    if (aStatus !== bStatus) {
      if (aStatus === "error" && bStatus === "success") change = "improved";
      else if (aStatus === "success" && bStatus === "error") change = "worse";
      else change = "changed";
    } else if (aDurationMs != null && bDurationMs != null && bDurationMs > aDurationMs * 1.5) {
      change = "changed";
    }
    return { nodeId, aStatus, bStatus, aDurationMs, bDurationMs, change };
  });
}

function statusSymbol(s: string | null): string {
  if (s === "success") return "✓";
  if (s === "error") return "✗";
  if (s == null) return "—";
  return s.slice(0, 1);
}

function statusColor(s: string | null): string {
  if (s === "success") return "var(--ok)";
  if (s === "error") return "var(--error)";
  return "var(--ink-3)";
}

function fmtMs(ms: number | null): string {
  return ms != null ? `${ms}ms` : "—";
}

export function RunDiff({ workflowId, diffPair, onChangePair }: RunDiffProps) {
  const runsQuery = useRuns(workflowId, { staleTime: 30_000 });
  const runs: RunInfo[] = (runsQuery.data ?? []) as RunInfo[];

  const [aId, bId] = diffPair ?? [runs[0]?.id ?? "", runs[1]?.id ?? ""];
  const aQuery = useRun(aId || null);
  const bQuery = useRun(bId || null);
  const aRun = aQuery.data as RunInfo | undefined;
  const bRun = bQuery.data as RunInfo | undefined;

  const rows = aRun && bRun ? diffNodeRows(aRun, bRun) : [];

  const improved = rows.filter((r) => r.change === "improved").length;
  const worse = rows.filter((r) => r.change === "worse").length;
  const changed = rows.filter((r) => r.change === "changed").length;

  return (
    <>
      <div className="sc-head">
        <span className="sc-head-title">Run diff</span>
      </div>
      <div className="sc-diff-selectors">
        <select
          className="sc-diff-select"
          value={aId}
          onChange={(e) => onChangePair([e.target.value, bId])}
        >
          {runs.map((r) => (
            <option key={r.id} value={r.id}>
              #{r.id.slice(0, 6)} · {r.status}
            </option>
          ))}
        </select>
        <span style={{ color: "var(--border)" }}>vs</span>
        <select
          className="sc-diff-select"
          value={bId}
          onChange={(e) => onChangePair([aId, e.target.value])}
        >
          {runs.map((r) => (
            <option key={r.id} value={r.id}>
              #{r.id.slice(0, 6)} · {r.status}
            </option>
          ))}
        </select>
      </div>
      <div className="sc-diff-body">
        <div style={{ display: "flex", flexDirection: "column", width: "100%" }}>
          <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
            <div className="sc-diff-col">
              <div className="sc-diff-col-head">
                <div
                  className="sc-node-dot"
                  style={{ background: aRun?.status === "success" ? "var(--ok)" : "var(--error)" }}
                />
                #{aId.slice(0, 6)} · {aRun?.status ?? "—"}
              </div>
              {rows.map((row) => (
                <div
                  key={row.nodeId}
                  className={`sc-diff-row${row.change !== "none" ? ` ${row.change}` : ""}`}
                >
                  <div className="sc-node-dot" style={{ background: statusColor(row.aStatus) }} />
                  <span className="sc-diff-node-name">{row.nodeId}</span>
                  <span className="sc-diff-status" style={{ color: statusColor(row.aStatus) }}>
                    {statusSymbol(row.aStatus)}
                  </span>
                  <span className="sc-diff-ms">{fmtMs(row.aDurationMs)}</span>
                </div>
              ))}
            </div>
            <div className="sc-diff-divider" />
            <div className="sc-diff-col">
              <div className="sc-diff-col-head">
                <div
                  className="sc-node-dot"
                  style={{ background: bRun?.status === "success" ? "var(--ok)" : "var(--error)" }}
                />
                #{bId.slice(0, 6)} · {bRun?.status ?? "—"}
              </div>
              {rows.map((row) => (
                <div
                  key={row.nodeId}
                  className={`sc-diff-row${row.change !== "none" ? ` ${row.change}` : ""}`}
                >
                  <div className="sc-node-dot" style={{ background: statusColor(row.bStatus) }} />
                  <span className="sc-diff-node-name">{row.nodeId}</span>
                  <span className="sc-diff-status" style={{ color: statusColor(row.bStatus) }}>
                    {statusSymbol(row.bStatus)}
                  </span>
                  <span className="sc-diff-ms">{fmtMs(row.bDurationMs)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
      <div className="sc-diff-footer">
        {improved > 0 && <span style={{ color: "var(--ok)" }}>+{improved} fixed</span>}
        {worse > 0 && <span style={{ color: "var(--error)" }}>-{worse} broke</span>}
        {changed > 0 && <span style={{ color: "var(--warn)" }}>{changed} changed</span>}
        {rows.length > 0 && improved === 0 && worse === 0 && changed === 0 && (
          <span>No differences</span>
        )}
      </div>
    </>
  );
}
