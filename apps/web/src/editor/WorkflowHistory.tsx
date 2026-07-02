import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useModalA11y } from "../useModalA11y";
import type { WorkflowRevisionInfo, WorkflowVersionInfo } from "../types";

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
  const [revisions, setRevisions] = useState<WorkflowRevisionInfo[]>([]);
  const [activeTab, setActiveTab] = useState<"versions" | "revisions">("versions");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onClose);

  useEffect(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      api.listWorkflowVersions(workflowId),
      api.listWorkflowRevisions(workflowId, 50),
    ])
      .then(([vs, revisionRows]) => {
        const sorted = [...vs].sort((a, b) => b.version - a.version);
        const sortedRevisions = [...revisionRows].sort(
          (a, b) => b.graph_revision - a.graph_revision,
        );
        setVersions(sorted);
        setRevisions(sortedRevisions);
        onVersionsLoaded?.(sorted);
      })
      .catch(() => setError("Failed to load workflow history."))
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

  function patchPreview(patch: WorkflowRevisionInfo["patch"]): string {
    if (!patch || !Object.keys(patch).length) return "";
    try {
      return JSON.stringify(patch, null, 2);
    } catch {
      return String(patch);
    }
  }

  const canCompare = versions.length >= 2;
  const hasVersionHistory = versions.length > 0;
  const hasRevisionHistory = revisions.length > 0;

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
          <h2 id="history-title">Workflow History</h2>
          <button className="ndv-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <div className="modal-body" style={{ maxHeight: "60vh", overflowY: "auto" }}>
          <div className="history-tabs" role="tablist" aria-label="Workflow history views">
            <button
              className={`history-tab${activeTab === "versions" ? " is-active" : ""}`}
              type="button"
              role="tab"
              aria-selected={activeTab === "versions"}
              onClick={() => setActiveTab("versions")}
            >
              Published versions
            </button>
            <button
              className={`history-tab${activeTab === "revisions" ? " is-active" : ""}`}
              type="button"
              role="tab"
              aria-selected={activeTab === "revisions"}
              onClick={() => setActiveTab("revisions")}
            >
              Draft changes
            </button>
          </div>
          {loading && <p className="muted">Loading…</p>}
          {error && <p style={{ color: "#f0747a" }}>{error}</p>}
          {!loading && !error && activeTab === "versions" && !hasVersionHistory && (
            <p className="muted">No saved versions yet. Versions are created when you publish.</p>
          )}
          {!loading && !error && activeTab === "versions" && hasVersionHistory && (
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
          {!loading && !error && activeTab === "revisions" && !hasRevisionHistory && (
            <p className="muted">No draft changes recorded yet.</p>
          )}
          {!loading && !error && activeTab === "revisions" && hasRevisionHistory && (
            <ul className="history-list">
              {revisions.map((revision) => {
                const preview = patchPreview(revision.patch);
                return (
                  <li key={revision.id} className="history-item history-item--revision">
                    <div className="history-item-meta history-item-meta--revision">
                      <span className="history-version">r{revision.graph_revision}</span>
                      <span className="history-date muted">{formatDate(revision.created_at)}</span>
                      <div className="history-badges">
                        <span className="history-badge">{revision.operation}</span>
                        <span className="history-badge">{revision.origin}</span>
                        <span className="history-badge">
                          {revision.actor_email || "MCP/API"}
                        </span>
                      </div>
                    </div>
                    {revision.summary && (
                      <p className="history-revision-summary">{revision.summary}</p>
                    )}
                    {preview && (
                      <details className="history-revision-patch">
                        <summary>Patch</summary>
                        <pre>{preview}</pre>
                      </details>
                    )}
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
