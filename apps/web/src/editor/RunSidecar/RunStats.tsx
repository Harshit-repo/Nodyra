import { useRuns } from "../../queries";
import type { RunInfo } from "../../types";

interface RunStatsProps {
  workflowId: string;
}

export interface Reliability {
  ok: number;
  err: number;
  skip: number;
}

export function nodeReliability(runs: RunInfo[], nodeId: string): Reliability {
  let ok = 0;
  let err = 0;
  let skip = 0;
  for (const run of runs) {
    const nr = run.node_runs.find((n) => n.node_id === nodeId);
    if (!nr) continue;
    if (nr.status === "success") ok++;
    else if (nr.status === "error") err++;
    else skip++;
  }
  return { ok, err, skip };
}

function HeatStrip({ ok, err, skip }: Reliability) {
  const total = ok + err + skip;
  if (total === 0) return <div className="sc-stats-strip-empty" />;
  const okPct = (ok / total) * 100;
  const errPct = (err / total) * 100;
  const skipPct = (skip / total) * 100;
  return (
    <div className="sc-stats-strip" title={`${ok} ok · ${err} err · ${skip} skip`}>
      {errPct > 0 && (
        <div className="sc-stats-bar error" style={{ width: `${errPct}%` }} />
      )}
      {skipPct > 0 && (
        <div className="sc-stats-bar skip" style={{ width: `${skipPct}%` }} />
      )}
      {okPct > 0 && (
        <div className="sc-stats-bar ok" style={{ width: `${okPct}%` }} />
      )}
    </div>
  );
}

function pct(n: number, total: number) {
  return total > 0 ? Math.round((n / total) * 100) : 0;
}

export function RunStats({ workflowId }: RunStatsProps) {
  const runsQuery = useRuns(workflowId, { staleTime: 30_000 });
  const runs: RunInfo[] = (runsQuery.data ?? []) as RunInfo[];

  if (runsQuery.isLoading) {
    return <div style={{ padding: 16, color: "var(--ink-3)", fontSize: 12 }}>Loading…</div>;
  }
  if (runs.length === 0) {
    return (
      <div style={{ padding: 16, color: "var(--ink-3)", fontSize: 12 }}>
        No runs yet.
      </div>
    );
  }

  const allNodeIds = Array.from(
    new Set(runs.flatMap((r) => r.node_runs.map((n) => n.node_id))),
  );

  const totalRuns = runs.length;
  const successRuns = runs.filter((r) => r.status === "success").length;

  return (
    <>
      <div className="sc-head">
        <span className="sc-head-title">Node reliability</span>
        <span className="sc-head-badge">
          {successRuns}/{totalRuns} runs ok
        </span>
      </div>
      <div className="sc-stats-body">
        <div className="sc-stats-legend">
          <span className="sc-stats-key error" />
          <span>error</span>
          <span className="sc-stats-key skip" />
          <span>skip</span>
          <span className="sc-stats-key ok" />
          <span>ok</span>
        </div>
        {allNodeIds.map((nodeId) => {
          const rel = nodeReliability(runs, nodeId);
          const total = rel.ok + rel.err + rel.skip;
          return (
            <div key={nodeId} className="sc-stats-row">
              <div className="sc-stats-name" title={nodeId}>
                {nodeId}
              </div>
              <HeatStrip {...rel} />
              <div className="sc-stats-pct">{pct(rel.ok, total)}%</div>
            </div>
          );
        })}
      </div>
    </>
  );
}
