import { useEffect, useRef } from "react";
import { useReactFlow } from "@xyflow/react";

interface Props {
  added: string[];
  removed: string[];
  changed: string[];
  unchanged: string[];
}

export function DiffSummaryBar({ added, removed, changed, unchanged }: Props) {
  const reactFlow = useReactFlow();
  const hasChanges = added.length + removed.length + changed.length > 0;

  // Keep a ref to the latest arrays so the effect closure doesn't go stale
  // while still using primitive counts as deps to avoid firing on reference churn.
  const latestRef = useRef({ added, removed, changed, reactFlow });
  latestRef.current = { added, removed, changed, reactFlow };

  const addedLen = added.length;
  const removedLen = removed.length;
  const changedLen = changed.length;

  useEffect(() => {
    if (addedLen + removedLen + changedLen === 0) return;
    const { added: a, removed: r, changed: c, reactFlow: rf } = latestRef.current;
    const ids = [...a, ...r, ...c];
    const timer = setTimeout(() => {
      rf.fitView({ nodes: ids.map((id) => ({ id })), padding: 0.3, duration: 300 });
    }, 50);
    return () => clearTimeout(timer);
  // Using primitive counts as deps: the effect fires when the changed-node set
  // actually changes (different counts), not when array references rotate.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addedLen, removedLen, changedLen]);

  if (!hasChanges && unchanged.length === 0) return null;

  return (
    <div className="diff-summary-bar">
      {added.length > 0 && (
        <span className="diff-summary-dot diff-summary-dot--added">● {added.length} added</span>
      )}
      {removed.length > 0 && (
        <span className="diff-summary-dot diff-summary-dot--removed">● {removed.length} removed</span>
      )}
      {changed.length > 0 && (
        <span className="diff-summary-dot diff-summary-dot--changed">● {changed.length} changed</span>
      )}
      {unchanged.length > 0 && (
        <span className="diff-summary-dot diff-summary-dot--unchanged">
          · {unchanged.length} unchanged
        </span>
      )}
      {hasChanges && (
        <button
          className="btn btn-sm btn-ghost diff-summary-zoom"
          onClick={() => {
            const ids = [...added, ...removed, ...changed];
            reactFlow.fitView({ nodes: ids.map((id) => ({ id })), padding: 0.3, duration: 300 });
          }}
        >
          Zoom to changes
        </button>
      )}
      {!hasChanges && (
        <span className="diff-summary-no-diff">No differences — this workflow matches the selected version.</span>
      )}
    </div>
  );
}
