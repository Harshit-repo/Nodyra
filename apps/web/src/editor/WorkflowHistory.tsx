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
        const sorted = [...vs].sort((a, b) => b.version - a.version);
        setVersions(sorted);
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

  function nodeDiff(idx: number): string {
    if (idx >= versions.length - 1) return "";
    const curr = versions[idx].graph.nodes.length;
    const prev = versions[idx + 1].graph.nodes.length;
    const diff = curr - prev;
    if (diff === 0) return "";
    return diff > 0 ? ` (+${diff} nodes)` : ` (${diff} nodes)`;
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal"
        style={{ minWidth: 480, maxWidth: 640 }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-labelledby="history-title"
      >
        <header className="modal-head">
          <h2 id="history-title" className="modal-title">
            Version History
          </h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <div className="modal-body">
          {loading && <p>Loading…</p>}
          {error && <p style={{ color: "var(--color-danger, red)" }}>{error}</p>}
          {!loading && !error && versions.length === 0 && (
            <p>No versions found.</p>
          )}
          {!loading && !error && versions.length > 0 && (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {versions.map((v, idx) => (
                <li
                  key={v.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "8px 4px",
                    borderBottom: "1px solid var(--color-border, #e0e0e0)",
                    cursor: "pointer",
                    background:
                      selected?.id === v.id
                        ? "var(--color-surface-hover, #f5f5f5)"
                        : undefined,
                  }}
                  onClick={() => setSelected(selected?.id === v.id ? null : v)}
                >
                  <span style={{ fontWeight: 600, minWidth: 48 }}>
                    v{v.version}
                  </span>
                  <span style={{ flex: 1, color: "var(--color-muted, #666)" }}>
                    {formatDate(v.created_at)}
                  </span>
                  {v.published && (
                    <span
                      style={{
                        fontSize: 11,
                        background: "var(--color-primary, #4CAF50)",
                        color: "#fff",
                        borderRadius: 4,
                        padding: "1px 6px",
                      }}
                    >
                      published
                    </span>
                  )}
                  <span style={{ fontSize: 12, color: "var(--color-muted, #888)" }}>
                    {v.graph.nodes.length} nodes{nodeDiff(idx)}
                  </span>
                </li>
              ))}
            </ul>
          )}

          {selected && (
            <div
              style={{
                marginTop: 16,
                padding: 12,
                background: "var(--color-surface, #fafafa)",
                borderRadius: 6,
                border: "1px solid var(--color-border, #e0e0e0)",
              }}
            >
              <p style={{ margin: "0 0 8px", fontWeight: 600 }}>
                Preview: v{selected.version}
              </p>
              <p style={{ margin: "0 0 4px" }}>
                Nodes: {selected.graph.nodes.length} &nbsp; Edges:{" "}
                {selected.graph.edges.length}
              </p>
              <p style={{ margin: "0 0 12px", fontSize: 12, color: "var(--color-muted, #888)" }}>
                Created: {formatDate(selected.created_at)}
              </p>
              <button
                className="btn btn-primary"
                onClick={() => {
                  onRestore(selected.graph);
                }}
              >
                Restore v{selected.version}
              </button>
            </div>
          )}
        </div>

        <footer className="modal-foot" style={{ padding: "12px 16px", display: "flex", justifyContent: "flex-end" }}>
          <button className="btn" onClick={onClose}>
            Close
          </button>
        </footer>
      </div>
    </div>
  );
}
