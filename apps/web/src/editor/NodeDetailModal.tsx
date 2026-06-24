import { useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";

import { categoryColor } from "../categories";
import { isBrandIconName, NodeIcon } from "../NodeIcon";
import { useModalA11y } from "../useModalA11y";
import { NDVPanels } from "./NDVPanels";
import { hasTriggerUpstream } from "./NodeCard";
import { isTriggerManifest, useEditor } from "./store";

export function NodeDetailModal({ nodeId }: { nodeId: string }) {
  const node = useEditor((s) => s.nodes.find((n) => n.id === nodeId));
  const closeNdv = useEditor((s) => s.closeNdv);
  const updateNodeSettings = useEditor((s) => s.updateNodeSettings);
  const runFromNode = useEditor((s) => s.runFromNode);
  const [editingName, setEditingName] = useState(false);
  const [nameVal, setNameVal] = useState("");
  const [nameSaved, setNameSaved] = useState(false);
  const [ndvHeight, setNdvHeight] = useState(65);
  const [resizing, setResizing] = useState(false);
  const [isWebhookListening, setIsWebhookListening] = useState(false);
  const modalRef = useRef<HTMLDivElement>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const nameSavedTimerRef = useRef<number | null>(null);
  const dragStartY = useRef(0);
  const dragStartH = useRef(0);
  const toggleDisabled = useEditor((s) => s.toggleDisabled);
  const deleteNode = useEditor((s) => s.deleteNode);
  const runStatus = useEditor((s) => s.runStatus[nodeId]);
  const isTrigger = isTriggerManifest(node?.data.manifest);
  const [graphNodes, graphEdges] = useEditor(
    useShallow((s) => [s.nodes, s.edges] as const),
  );
  const canRunStep = useMemo(
    () =>
      Boolean(
        node &&
          (isTrigger || hasTriggerUpstream(graphNodes, graphEdges, node.id)),
      ),
    [graphEdges, graphNodes, isTrigger, node],
  );
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

  function startResize(e: React.MouseEvent): void {
    e.preventDefault();
    dragStartY.current = e.clientY;
    dragStartH.current = ndvHeight;
    setResizing(true);
    function onMove(mv: MouseEvent): void {
      const deltaVh = ((mv.clientY - dragStartY.current) / window.innerHeight) * 100;
      setNdvHeight(Math.min(92, Math.max(35, dragStartH.current + deltaVh)));
    }
    function onUp(): void {
      setResizing(false);
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  function resetHeight(): void {
    setNdvHeight(65);
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
        className={`ndv-modal${resizing ? " ndv-resizing" : ""}${
          runStatus === "success"
            ? " ndv-edge-ok"
            : runStatus === "error"
              ? " ndv-edge-error"
              : runStatus === "running"
                ? " ndv-edge-running"
                : ""
        }`}
        ref={modalRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ndv-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        style={{ height: `${ndvHeight}vh` }}
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
                <span className="mono-tag mono-tag-cat" style={{ color }}>
                  {manifest.category}
                </span>
                <span className="mono-tag">{manifest.id}</span>
              </div>
            </div>
          </div>
          <div className="ndv-actions">
            {isWebhook && isWebhookListening ? (
              <div className="ndv-listening-pill" aria-live="polite">
                <span className="ndv-listening-dot" aria-hidden="true" />
                Listening…
              </div>
            ) : (
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
            )}
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
            <span className="ndv-action-sep" aria-hidden="true" />
            <button
              className="btn btn-sm"
              onClick={() => toggleDisabled(currentNode.id)}
            >
              {disabled ? "Enable" : "Disable"}
            </button>
            <button
              className="btn btn-sm btn-danger-soft"
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
          <NDVPanels
            nodeId={nodeId}
            onListeningChange={isWebhook ? setIsWebhookListening : undefined}
          />
        </div>
        <div
          className="ndv-drag-handle"
          onMouseDown={startResize}
          onDoubleClick={resetHeight}
          role="separator"
          aria-label="Drag to resize panel, double-click to reset"
        >
          <span className="ndv-drag-dots" aria-hidden="true">
            <span /><span /><span /><span /><span />
          </span>
          <span className="ndv-drag-tip" aria-hidden="true">double-click to reset</span>
        </div>
      </div>
    </div>
  );
}
