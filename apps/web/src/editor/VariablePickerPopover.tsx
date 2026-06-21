import { useEffect, useMemo, useRef, useState } from "react";

import {
  getUpstreamNodes,
  searchUpstreamFields,
} from "./node-details/upstreamFields";
import { useEditor } from "./store";

interface Props {
  nodeId: string;
  onInsert: (expression: string) => void;
  onClose: () => void;
}

function startDrag(e: React.DragEvent<HTMLElement>, expression: string) {
  e.dataTransfer.setData("text/plain", expression);
  e.dataTransfer.setData("application/x-noodle-expression", expression);
  e.dataTransfer.effectAllowed = "copy";
  const ghost = document.createElement("div");
  ghost.className = "expr-drag-ghost";
  ghost.textContent = expression;
  document.body.appendChild(ghost);
  e.dataTransfer.setDragImage(ghost, 12, 12);
  window.setTimeout(() => ghost.remove(), 0);
}

export function VariablePickerPopover({ nodeId, onInsert, onClose }: Props) {
  const nodes = useEditor((s) => s.nodes);
  const edges = useEditor((s) => s.edges);
  const runOutputs = useEditor((s) => s.runOutputs);
  const [query, setQuery] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const allUpstream = useMemo(
    () => getUpstreamNodes(nodeId, nodes, edges, runOutputs),
    [nodeId, nodes, edges, runOutputs],
  );
  const filtered = useMemo(
    () => searchUpstreamFields(allUpstream, query),
    [allUpstream, query],
  );

  useEffect(() => {
    searchRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    }
    // Capture phase so this fires before the modal's own Escape handler
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  useEffect(() => {
    function onMouseDown(e: MouseEvent) {
      if (!containerRef.current?.contains(e.target as Node)) onClose();
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [onClose]);

  return (
    <div
      className="var-picker-popover"
      ref={containerRef}
      role="dialog"
      aria-label="Pick a variable"
    >
      <div className="var-picker-head">
        <span className="var-picker-icon">$</span>
        <span>Pick a variable</span>
      </div>
      <input
        ref={searchRef}
        className="var-picker-search"
        type="search"
        placeholder="Search all nodes…"
        aria-label="Search variables"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="var-picker-body">
        {allUpstream.length === 0 && (
          <p className="var-picker-empty">
            Run the workflow first to see upstream data here.
          </p>
        )}
        {allUpstream.length > 0 && filtered.length === 0 && (
          <p className="var-picker-empty">No fields match "{query}".</p>
        )}
        {filtered.map((upNode) => (
          <div key={upNode.id} className="var-picker-node">
            <div className="var-picker-node-head">{upNode.label}</div>
            {upNode.fields.map((field) => (
              <div
                key={field.path}
                className="var-picker-field"
                draggable
                tabIndex={0}
                role="button"
                onDragStart={(e) => startDrag(e, field.expression)}
                onClick={() => {
                  onInsert(field.expression);
                  onClose();
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onInsert(field.expression);
                    onClose();
                  }
                }}
              >
                <span className="var-picker-grip">⠿</span>
                <span className="var-picker-name">{field.path}</span>
                <span className="var-picker-type">{field.type}</span>
                <button
                  type="button"
                  className="var-picker-copy"
                  title={`Copy ${field.expression}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    void navigator.clipboard.writeText(field.expression);
                  }}
                >
                  copy
                </button>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
