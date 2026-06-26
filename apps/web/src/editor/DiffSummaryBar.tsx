import { useEffect } from "react";
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

  useEffect(() => {
    if (!hasChanges) return;
    const ids = [...added, ...removed, ...changed];
    const timer = setTimeout(() => {
      reactFlow.fitView({ nodes: ids.map((id) => ({ id })), padding: 0.3, duration: 300 });
    }, 50);
    return () => clearTimeout(timer);
  }, [added, removed, changed, hasChanges, reactFlow]);

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
