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
  const isTrigger = node?.data.manifest.category === "Triggers";
  // Only allow running a node individually when it is wired to a trigger.
  const hasTriggerUpstream = useEditor((s) => {
    if (!node || isTrigger) return true;
    const bySource = new Map<string, string[]>();
    for (const e of s.edges) {
      const arr = bySource.get(e.target);
      if (arr) arr.push(e.source);
      else bySource.set(e.target, [e.source]);
    }
    const catById = new Map(
      s.nodes.map((n) => [n.id, n.data.manifest.category]),
    );
    const visited = new Set<string>([node.id]);
    const queue = [node.id];
    while (queue.length) {
      const cur = queue.pop() as string;
      for (const prev of bySource.get(cur) ?? []) {
        if (visited.has(prev)) continue;
        visited.add(prev);
        if (catById.get(prev) === "Triggers") return true;
        queue.push(prev);
      }
    }
    return false;
  });
  const canRunStep = isTrigger || hasTriggerUpstream;

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
              disabled={!canRunStep}
              title={
                !canRunStep
                  ? "Connect a trigger upstream to run this node"
                  : isWebhook
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
                disabled={!canRunStep}
                title={
                  !canRunStep
                    ? "Connect a trigger upstream to run this node"
                    : "Execute this step after recomputing upstream nodes"
                }
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
