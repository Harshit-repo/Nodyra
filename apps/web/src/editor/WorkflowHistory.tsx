import { useEffect, useState } from "react";
import { api } from "../api";
import type { WorkflowGraph, WorkflowVersionInfo } from "../types";

interface Props {
  workflowId: string;
  onClose: () => void;
  onRestore: (graph: WorkflowGraph) => void;
}

export function WorkflowHistory({ workflowId, onClose, onRestore }: Props) {
  const [versions, setVersions] = useState<WorkflowVersionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<WorkflowVersionInfo | null>(null);

  useEffect(() => {
    setLoading(true);
    api
      .listWorkflowVersions(workflowId)
      .then((vs) => {
        setVersions([...vs].sort((a, b) => b.version - a.version));
      })
      .catch(() => setError("Failed to load version history."))
      .finally(() => setLoading(false));
  }, [workflowId]);

  function formatDate(iso: string): string {
    try { return new Date(iso).toLocaleString(); } catch { return iso; }
  }

  function nodeDiff(idx: number): string {
    if (idx >= versions.length - 1) return "";
    const diff = versions[idx].graph.nodes.length - versions[idx + 1].graph.nodes.length;
    if (diff === 0) return "";
    return diff > 0 ? ` +${diff}` : ` ${diff}`;
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal modal-wide"
        style={{ maxWidth: 560 }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-labelledby="history-title"
      >
        <header className="modal-head">
          <h2 id="history-title">Version History</h2>
          <button className="ndv-close" onClick={onClose} aria-label="Close">×</button>
        </header>

        <div className="modal-body" style={{ maxHeight: "60vh", overflowY: "auto" }}>
          {loading && <p className="muted">Loading…</p>}
          {error && <p style={{ color: "#f0747a" }}>{error}</p>}
          {!loading && !error && versions.length === 0 && (
            <p className="muted">No saved versions yet. Versions are created when you publish.</p>
          )}
          {!loading && !error && versions.length > 0 && (
            <ul className="history-list">
              {versions.map((v, idx) => (
                <li
                  key={v.id}
                  className={`history-item${selected?.id === v.id ? " history-item--selected" : ""}`}
                  onClick={() => setSelected(selected?.id === v.id ? null : v)}
                >
                  <span className="history-version">v{v.version}</span>
                  <span className="history-date muted">{formatDate(v.created_at)}</span>
                  <div className="history-badges">
                    {v.published && <span className="history-badge history-badge--published">published</span>}
                    {nodeDiff(idx) && (
                      <span className="history-badge">{v.graph.nodes.length} nodes{nodeDiff(idx)}</span>
                    )}
                    {!nodeDiff(idx) && (
                      <span className="muted" style={{ fontSize: 11 }}>{v.graph.nodes.length} nodes</span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}

          {selected && (
            <div className="history-preview">
              <p className="history-preview-title">v{selected.version} — {formatDate(selected.created_at)}</p>
              <p className="muted" style={{ fontSize: 12 }}>
                {selected.graph.nodes.length} nodes · {selected.graph.edges.length} edges
              </p>
              <button
                className="btn btn-sm btn-primary"
                onClick={() => { onRestore(selected.graph); onClose(); }}
              >
                Restore v{selected.version}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
