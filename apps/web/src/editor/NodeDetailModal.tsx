import { useEffect } from "react";

import { categoryColor } from "../categories";
import { NodeIcon } from "../NodeIcon";
import { NDVPanels } from "./NDVPanels";
import { useEditor } from "./store";

export function NodeDetailModal({ nodeId }: { nodeId: string }) {
  const node = useEditor((s) => s.nodes.find((n) => n.id === nodeId));
  const closeNdv = useEditor((s) => s.closeNdv);
  const runFromNode = useEditor((s) => s.runFromNode);
  const toggleDisabled = useEditor((s) => s.toggleDisabled);
  const deleteNode = useEditor((s) => s.deleteNode);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") closeNdv();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closeNdv]);

  if (!node) return null;
  const { manifest, disabled } = node.data;
  const isWebhook = manifest.id === "webhook_trigger";
  const color = categoryColor(manifest.category);

  return (
    <div className="modal-overlay ndv-overlay" onClick={closeNdv}>
      <div className="ndv-modal" onClick={(e) => e.stopPropagation()}>
        <header className="ndv-head">
          <div className="ndv-title">
            <span className="inspector-glyph" style={{ color }}>
              <NodeIcon name={manifest.icon} size={20} />
            </span>
            <div>
              <h3>{manifest.name}</h3>
              <div className="ndv-meta">
                <span className="mono-tag" style={{ color }}>
                  {manifest.category}
                </span>
                <span className="mono-tag">{manifest.id}</span>
              </div>
            </div>
          </div>
          <div className="ndv-actions">
            <button
              className="btn btn-sm btn-run"
              onClick={() => runFromNode(node.id)}
              title={
                isWebhook
                  ? "Listen for a test event"
                  : "Execute this step using current upstream data"
              }
            >
              {isWebhook ? "▶ Listen for event" : "▶ Execute step"}
            </button>
            {!isWebhook && (
              <button
                className="btn btn-sm btn-ghost"
                onClick={() =>
                  runFromNode(node.id, { reuseUpstream: false })
                }
                title="Execute this step after recomputing upstream nodes"
              >
                ↻ Run fresh
              </button>
            )}
            <button
              className="btn btn-sm"
              onClick={() => toggleDisabled(node.id)}
            >
              {disabled ? "Enable" : "Disable"}
            </button>
            <button
              className="btn btn-sm btn-ghost"
              onClick={() => {
                deleteNode(node.id);
                closeNdv();
              }}
            >
              Delete
            </button>
            <button
              className="ndv-close"
              aria-label="Close"
              onClick={closeNdv}
            >
              ×
            </button>
          </div>
        </header>
        <div className="ndv-body">
          <NDVPanels nodeId={nodeId} />
        </div>
      </div>
    </div>
  );
}
