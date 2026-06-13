import { useEffect, useRef, useState } from "react";

import { categoryColor } from "../categories";
import { isBrandIconName, NodeIcon } from "../NodeIcon";
import { useModalA11y } from "../useModalA11y";
import { NDVPanels } from "./NDVPanels";
import { isTriggerManifest, useEditor } from "./store";

export function NodeDetailModal({ nodeId }: { nodeId: string }) {
  const node = useEditor((s) => s.nodes.find((n) => n.id === nodeId));
  const closeNdv = useEditor((s) => s.closeNdv);
  const updateNodeSettings = useEditor((s) => s.updateNodeSettings);
  const runFromNode = useEditor((s) => s.runFromNode);
  const [editingName, setEditingName] = useState(false);
  const [nameVal, setNameVal] = useState("");
  const [nameSaved, setNameSaved] = useState(false);
  const modalRef = useRef<HTMLDivElement>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const nameSavedTimerRef = useRef<number | null>(null);
  const toggleDisabled = useEditor((s) => s.toggleDisabled);
  const deleteNode = useEditor((s) => s.deleteNode);
  const isTrigger = isTriggerManifest(node?.data.manifest);
  // Only allow running a node individually when it is wired to a trigger.
  const hasTriggerUpstream = useEditor((s) => {
    if (!node || isTrigger) return true;
    const bySource = new Map<string, string[]>();
    for (const e of s.edges) {
      const arr = bySource.get(e.target);
      if (arr) arr.push(e.source);
      else bySource.set(e.target, [e.source]);
    }
    const triggerById = new Map(
      s.nodes.map((n) => [n.id, isTriggerManifest(n.data.manifest)]),
    );
    const visited = new Set<string>([node.id]);
    const queue = [node.id];
    while (queue.length) {
      const cur = queue.pop() as string;
      for (const prev of bySource.get(cur) ?? []) {
        if (visited.has(prev)) continue;
        visited.add(prev);
        if (triggerById.get(prev)) return true;
        queue.push(prev);
      }
    }
    return false;
  });
  const canRunStep = isTrigger || hasTriggerUpstream;
  useModalA11y(modalRef, closeNdv, { trapFocus: false });

  useEffect(
    () => () => {
      if (nameSavedTimerRef.current !== null) {
        window.clearTimeout(nameSavedTimerRef.current);
      }
    },
    [],
  );

  if (!node) return null;
  const currentNode = node;
  const { manifest, disabled } = currentNode.data;
  const isWebhook = manifest.id === "webhook_trigger";
  const hasBrandIcon = isBrandIconName(manifest.icon);
  const color = categoryColor(manifest.category);

  function flashNameSaved(): void {
    setNameSaved(true);
    if (nameSavedTimerRef.current !== null) {
      window.clearTimeout(nameSavedTimerRef.current);
    }
    nameSavedTimerRef.current = window.setTimeout(() => {
      setNameSaved(false);
      nameSavedTimerRef.current = null;
    }, 1400);
  }

  function saveName(): void {
    const trimmed = nameVal.trim();
    const nextLabel = trimmed || undefined;
    if ((currentNode.data.label ?? undefined) !== nextLabel) {
      updateNodeSettings(nodeId, { label: nextLabel });
      flashNameSaved();
    }
    setEditingName(false);
  }

  return (
    <div className="modal-overlay ndv-overlay" onClick={closeNdv}>
      <div
        className="ndv-modal"
        ref={modalRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ndv-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="ndv-head">
          <div className="ndv-title">
            <span
              className={`inspector-glyph${hasBrandIcon ? " has-brand-icon" : ""}`}
              style={{ color }}
            >
              <NodeIcon name={manifest.icon} size={hasBrandIcon ? 30 : 20} />
            </span>
            <div>
              {editingName ? (
                <input
                  id="ndv-title"
                  ref={nameInputRef}
                  className="ndv-name-input"
                  value={nameVal}
                  onChange={(e) => setNameVal(e.target.value)}
                  onBlur={saveName}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") nameInputRef.current?.blur();
                    if (e.key === "Escape") { setNameVal(currentNode.data.label || manifest.name); setEditingName(false); }
                  }}
                />
              ) : (
                <h3
                  id="ndv-title"
                  className="ndv-name-editable"
                  title="Click to rename for this workflow"
                  onClick={() => { setNameVal(currentNode.data.label || manifest.name); setEditingName(true); }}
                >
                  {currentNode.data.label || manifest.name}
                  {currentNode.data.label && <span className="ndv-label-changed" title={`Original: ${manifest.name}`}>✎</span>}
                  {nameSaved && <span className="ndv-name-saved" title="Saved">✓</span>}
                </h3>
              )}
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
              onClick={() => runFromNode(currentNode.id)}
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
                  runFromNode(currentNode.id, { reuseUpstream: false })
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
              onClick={() => toggleDisabled(currentNode.id)}
            >
              {disabled ? "Enable" : "Disable"}
            </button>
            <button
              className="btn btn-sm btn-ghost"
              onClick={() => {
                deleteNode(currentNode.id);
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
