import { useState, useEffect } from "react";
import { useRun } from "../../queries";
import { safeGetItem, safeSetItem } from "../../safeStorage";
import type { RunInfo as RunInfoType } from "../../types";

interface RunInfoProps {
  selectedRunId: string | null;
  onOpenDiff: () => void;
}

function getNoteKey(runId: string) {
  return `run-note-${runId}`;
}

export function RunInfo({ selectedRunId, onOpenDiff }: RunInfoProps) {
  const runQuery = useRun(selectedRunId);
  const run = runQuery.data as RunInfoType | undefined;
  const [note, setNote] = useState("");

  useEffect(() => {
    if (!selectedRunId) return;
    setNote(safeGetItem(getNoteKey(selectedRunId)) ?? "");
  }, [selectedRunId]);

  function saveNote(value: string) {
    if (!selectedRunId) return;
    setNote(value);
    safeSetItem(getNoteKey(selectedRunId), value);
  }

  if (!selectedRunId) {
    return (
      <div style={{ padding: 16, color: "var(--ink-3)", fontSize: 12 }}>
        Select a run to view details.
      </div>
    );
  }

  if (runQuery.isLoading || !run) {
    return <div style={{ padding: 16, color: "var(--ink-3)", fontSize: 12 }}>Loading…</div>;
  }

  const triggerOutput = run.node_runs[0]?.output ?? null;
  const payloadStr =
    triggerOutput != null
      ? JSON.stringify(triggerOutput, null, 2)
      : `// trigger_type: ${run.trigger_type}\n// No payload captured`;

  return (
    <>
      <div className="sc-head">
        <span className="sc-head-title">Run info</span>
        <span className="sc-head-badge">#{run.id.slice(0, 6)}</span>
      </div>
      <div className="sc-info-body">
        <div className="sc-info-section">
          <div className="sc-info-sec-head">
            <span>Trigger payload</span>
            <span style={{ color: "var(--accent)", fontSize: "9px" }}>{run.trigger_type}</span>
          </div>
          <pre className="sc-info-payload">{payloadStr}</pre>
        </div>
        <div className="sc-info-section">
          <div className="sc-info-sec-head">Note</div>
          <textarea
            className="sc-info-note"
            placeholder="Add a note to this run…"
            value={note}
            onChange={(e) => saveNote(e.target.value)}
          />
        </div>
        <div className="sc-info-section" style={{ padding: "10px 12px" }}>
          <button className="sc-action-btn" style={{ width: "100%" }} onClick={onOpenDiff}>
            Compare with another run →
          </button>
        </div>
      </div>
    </>
  );
}
