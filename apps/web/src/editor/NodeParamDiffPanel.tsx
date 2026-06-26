import { useState } from "react";
import type { ParamDiff } from "./diffWorkflowGraphs";

interface Props {
  nodeId: string;
  nodeLabel?: string;
  params: ParamDiff[];
  onClose: () => void;
}

function valuePreview(v: unknown): string {
  if (v === undefined) return "(undefined)";
  if (v === null) return "null";
  if (typeof v === "string") return `"${v}"`;
  if (typeof v === "object") return JSON.stringify(v, null, 2);
  return String(v);
}

export function NodeParamDiffPanel({ nodeId, nodeLabel, params, onClose }: Props) {
  const [showAll, setShowAll] = useState(false);
  const changedParams = params.filter((p) => p.before !== p.after);
  const unchangedCount = params.length - changedParams.length;
  const displayParams = showAll ? params : changedParams;

  return (
    <div className="param-diff-panel" role="complementary" aria-label="Parameter changes">
      <div className="param-diff-panel__header">
        <span className="param-diff-panel__title">
          {nodeLabel ?? nodeId}
        </span>
        <button className="ndv-close" onClick={onClose} aria-label="Close param panel">×</button>
      </div>
      <div className="param-diff-panel__body">
        {displayParams.length === 0 ? (
          <p className="muted" style={{ padding: "8px 12px", fontSize: 12 }}>No changed params.</p>
        ) : (
          <table className="param-diff-table">
            <thead>
              <tr>
                <th>Parameter</th>
                <th>Before</th>
                <th>After</th>
              </tr>
            </thead>
            <tbody>
              {displayParams.map((p) => {
                const isDiff = !Object.is(p.before, p.after);
                return (
                  <tr key={p.key} className={isDiff ? "param-diff-table__row--changed" : ""}>
                    <td className="param-diff-table__key">{p.key}</td>
                    <td className="param-diff-table__before">
                      <code>{valuePreview(p.before)}</code>
                    </td>
                    <td className="param-diff-table__after">
                      <code>{valuePreview(p.after)}</code>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {!showAll && unchangedCount > 0 && (
          <button
            className="btn btn-sm btn-ghost"
            style={{ margin: "8px 12px" }}
            onClick={() => setShowAll(true)}
          >
            Show all {params.length} params
          </button>
        )}
        {showAll && unchangedCount > 0 && (
          <button
            className="btn btn-sm btn-ghost"
            style={{ margin: "8px 12px" }}
            onClick={() => setShowAll(false)}
          >
            Hide unchanged params
          </button>
        )}
      </div>
    </div>
  );
}
