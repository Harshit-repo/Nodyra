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

function statusGlyph(s: string | null): string {
  if (s === "success") return "✓";
  if (s === "error") return "✗";
  if (s == null) return "—";
  return "·";
}

function statusColor(s: string | null): string {
  if (s === "success") return "var(--ok)";
  if (s === "error") return "var(--error)";
  return "var(--ink-3)";
}

function fmtMs(ms: number | null): string {
  if (ms == null) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

function deltaMs(a: number | null, b: number | null): string | null {
  if (a == null || b == null) return null;
  const d = b - a;
  if (Math.abs(d) < 5) return null;
  return d > 0 ? `+${fmtMs(d)}` : `-${fmtMs(Math.abs(d))}`;
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

      <div className="sc-diff-picks">
        <div className="sc-diff-pick">
          <div className="sc-diff-pick-label">Base</div>
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
        </div>
        <div className="sc-diff-vs">→</div>
        <div className="sc-diff-pick">
          <div className="sc-diff-pick-label">Compare</div>
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
      </div>

      {(aQuery.isLoading || bQuery.isLoading) && (
        <div style={{ padding: 16, color: "var(--ink-3)", fontSize: 12 }}>Loading…</div>
      )}

      {rows.length > 0 && (
        <div className="sc-diff-body">
          <div className="sc-diff-table-head">
            <div className="sc-dt-node">Node</div>
            <div className="sc-dt-cell">Base</div>
            <div className="sc-dt-cell">Compare</div>
            <div className="sc-dt-delta">Δ</div>
          </div>
          {rows.map((row) => {
            const delta = deltaMs(row.aDurationMs, row.bDurationMs);
            const deltaPositive = row.bDurationMs != null && row.aDurationMs != null && row.bDurationMs > row.aDurationMs;
            return (
              <div
                key={row.nodeId}
                className={`sc-diff-trow${row.change !== "none" ? ` ${row.change}` : ""}`}
              >
                <div className="sc-dt-node" title={row.nodeId}>{row.nodeId}</div>
                <div className="sc-dt-cell">
                  <span className="sc-dt-glyph" style={{ color: statusColor(row.aStatus) }}>
                    {statusGlyph(row.aStatus)}
                  </span>
                  <span className="sc-dt-ms">{fmtMs(row.aDurationMs)}</span>
                </div>
                <div className="sc-dt-cell">
                  <span className="sc-dt-glyph" style={{ color: statusColor(row.bStatus) }}>
                    {statusGlyph(row.bStatus)}
                  </span>
                  <span className="sc-dt-ms">{fmtMs(row.bDurationMs)}</span>
                </div>
                <div
                  className="sc-dt-delta"
                  style={{ color: delta ? (deltaPositive ? "var(--error)" : "var(--ok)") : "var(--border)" }}
                >
                  {delta ?? "—"}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="sc-diff-footer">
        {improved > 0 && <span style={{ color: "var(--ok)" }}>↑ {improved} fixed</span>}
        {worse > 0 && <span style={{ color: "var(--error)" }}>↓ {worse} broke</span>}
        {changed > 0 && <span style={{ color: "var(--warn)" }}>~ {changed} changed</span>}
        {rows.length > 0 && improved === 0 && worse === 0 && changed === 0 && (
          <span style={{ color: "var(--ink-3)" }}>Identical</span>
        )}
        {rows.length === 0 && !aQuery.isLoading && !bQuery.isLoading && (
          <span style={{ color: "var(--ink-3)" }}>Select two runs to compare</span>
        )}
      </div>
    </>
  );
}
