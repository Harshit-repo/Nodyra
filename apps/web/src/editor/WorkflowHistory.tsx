import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useModalA11y } from "../useModalA11y";
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
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalA11y(dialogRef, onClose);

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
    const a = versions[idx].graph?.nodes?.length ?? 0;
    const b = versions[idx + 1].graph?.nodes?.length ?? 0;
    const diff = a - b;
    if (diff === 0) return "";
    return diff > 0 ? ` +${diff}` : ` ${diff}`;
  }

  function nodeLabel(graph: WorkflowGraph, id: string): string {
    const node = graph.nodes?.find((item) => item.id === id);
    if (!node) return id;
    const label = typeof node.label === "string" && node.label ? node.label : node.type;
    return `${label} (${id})`;
  }

  function selectedDiff() {
    if (!selected?.graph?.nodes) return null;
    const idx = versions.findIndex((version) => version.id === selected.id);
    if (idx < 0 || idx >= versions.length - 1) return null;
    const previous = versions[idx + 1];
    if (!previous.graph?.nodes) return null;
    const currentIds = new Set(selected.graph.nodes.map((node) => node.id));
    const previousIds = new Set(previous.graph.nodes.map((node) => node.id));
    return {
      previousVersion: previous.version,
      added: selected.graph.nodes
        .filter((node) => !previousIds.has(node.id))
        .map((node) => nodeLabel(selected.graph, node.id)),
      removed: previous.graph.nodes
        .filter((node) => !currentIds.has(node.id))
        .map((node) => nodeLabel(previous.graph, node.id)),
      edgeDelta: (selected.graph.edges?.length ?? 0) - (previous.graph.edges?.length ?? 0),
    };
  }

  const diff = selectedDiff();

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
                      <span className="history-badge">{v.graph?.nodes?.length ?? 0} nodes{nodeDiff(idx)}</span>
                    )}
                    {!nodeDiff(idx) && (
                      <span className="muted" style={{ fontSize: 11 }}>{v.graph?.nodes?.length ?? 0} nodes</span>
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
                {selected.graph?.nodes?.length ?? 0} nodes · {selected.graph?.edges?.length ?? 0} edges
              </p>
              {diff ? (
                <div className="history-diff">
                  <div>
                    <strong>Compared with v{diff.previousVersion}</strong>
                    <span>{diff.added.length} added · {diff.removed.length} removed · {diff.edgeDelta >= 0 ? "+" : ""}{diff.edgeDelta} edges</span>
                  </div>
                  {diff.added.length > 0 && (
                    <p><span>Added:</span> {diff.added.slice(0, 4).join(", ")}{diff.added.length > 4 ? `, +${diff.added.length - 4} more` : ""}</p>
                  )}
                  {diff.removed.length > 0 && (
                    <p><span>Removed:</span> {diff.removed.slice(0, 4).join(", ")}{diff.removed.length > 4 ? `, +${diff.removed.length - 4} more` : ""}</p>
                  )}
                </div>
              ) : (
                <p className="muted" style={{ fontSize: 12 }}>
                  No earlier version to compare.
                </p>
              )}
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
