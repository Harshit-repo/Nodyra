import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useModalA11y } from "../useModalA11y";
import type { WorkflowVersionInfo } from "../types";

interface Props {
  workflowId: string;
  onClose: () => void;
  onRestore: (version: WorkflowVersionInfo) => void;
  onCompare: (version: WorkflowVersionInfo) => void;
  onVersionsLoaded?: (versions: WorkflowVersionInfo[]) => void;
}

export function WorkflowHistory({
  workflowId,
  onClose,
  onRestore,
  onCompare,
  onVersionsLoaded,
}: Props) {
  const [versions, setVersions] = useState<WorkflowVersionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onClose);

  useEffect(() => {
    setLoading(true);
    api
      .listWorkflowVersions(workflowId)
      .then((vs) => {
        const sorted = [...vs].sort((a, b) => b.version - a.version);
        setVersions(sorted);
        onVersionsLoaded?.(sorted);
      })
      .catch(() => setError("Failed to load version history."))
      .finally(() => setLoading(false));
  }, [workflowId]);

  function formatDate(iso: string): string {
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }

  function nodeDelta(idx: number): number | null {
    if (idx >= versions.length - 1) return null;
    return versions[idx].node_count - versions[idx + 1].node_count;
  }

  const canCompare = versions.length >= 2;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal modal-wide"
        style={{ maxWidth: 560 }}
        ref={dialogRef}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="history-title"
        tabIndex={-1}
      >
        <header className="modal-head">
          <h2 id="history-title">Version History</h2>
          <button className="ndv-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <div className="modal-body" style={{ maxHeight: "60vh", overflowY: "auto" }}>
          {loading && <p className="muted">Loading…</p>}
          {error && <p style={{ color: "#f0747a" }}>{error}</p>}
          {!loading && !error && versions.length === 0 && (
            <p className="muted">No saved versions yet. Versions are created when you publish.</p>
          )}
          {!loading && !error && versions.length > 0 && (
            <ul className="history-list">
              {versions.map((v, idx) => {
                const delta = nodeDelta(idx);
                return (
                  <li key={v.id} className="history-item">
                    <div className="history-item-meta">
                      <span className="history-version">v{v.version}</span>
                      <span className="history-date muted">{formatDate(v.created_at)}</span>
                      <div className="history-badges">
                        {v.published && (
                          <span className="history-badge history-badge--published">published</span>
                        )}
                        {v.notes && (
                          <span className="history-badge history-badge--notes">{v.notes}</span>
                        )}
                        {delta !== null && delta !== 0 ? (
                          <span className="history-badge">
                            {v.node_count} nodes{delta > 0 ? ` +${delta}` : ` ${delta}`}
                          </span>
                        ) : (
                          <span className="muted" style={{ fontSize: 11 }}>
                            {v.node_count} nodes
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="history-item-actions">
                      {canCompare && (
                        <button
                          className="btn btn-sm btn-ghost"
                          onClick={() => {
                            onCompare(v);
                            onClose();
                          }}
                          title="Compare this version visually"
                        >
                          Compare
                        </button>
                      )}
                      <button
                        className="btn btn-sm btn-primary"
                        onClick={() => {
                          onRestore(v);
                          onClose();
                        }}
                      >
                        Restore
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
