import { CaretLeft, CaretRight } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";

import { NodeDetails } from "./NodeDetails";
import { useEditor } from "./store";

const MIN_WIDTH = 260;
const MAX_WIDTH = 720;
const DEFAULT_WIDTH = 320;
const WIDTH_KEY = "noodle_inspector_width";
const COLLAPSED_KEY = "noodle_inspector_collapsed";

function clampWidth(value: number): number {
  return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, value));
}

function readStoredWidth(): number {
  try {
    const parsed = Number(localStorage.getItem(WIDTH_KEY));
    return Number.isFinite(parsed) ? clampWidth(parsed) : DEFAULT_WIDTH;
  } catch {
    return DEFAULT_WIDTH;
  }
}

export function Inspector() {
  const selectedId = useEditor((s) => s.selectedId);
  const nodes = useEditor((s) => s.nodes);
  const dirty = useEditor((s) => s.dirty);
  const running = useEditor((s) => s.running);
  const setSelected = useEditor((s) => s.setSelected);
  const [width, setWidth] = useState(readStoredWidth);
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(COLLAPSED_KEY) === "1"; } catch { return false; }
  });
  const activeListenersRef = useRef<{ move: (ev: MouseEvent) => void; up: () => void } | null>(null);
  const workflowNodes = nodes.filter((node) => node.data?.manifest);
  const firstWorkflowNode = workflowNodes[0] ?? null;

  function setCollapsedPersisted(next: boolean): void {
    setCollapsed(next);
    try { localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0"); } catch { /* */ }
  }

  function toggleCollapsed(): void {
    setCollapsed((v) => {
      const next = !v;
      try { localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0"); } catch { /* */ }
      return next;
    });
  }

  useEffect(() => {
    try { localStorage.setItem(WIDTH_KEY, String(width)); } catch { /* */ }
  }, [width]);

  useEffect(() => {
    return () => {
      if (activeListenersRef.current) {
        window.removeEventListener("mousemove", activeListenersRef.current.move);
        window.removeEventListener("mouseup", activeListenersRef.current.up);
        document.body.classList.remove("is-resizing");
        activeListenersRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    function toggleInspector(): void {
      toggleCollapsed();
    }
    window.addEventListener("noodle:toggle-inspector", toggleInspector);
    return () => window.removeEventListener("noodle:toggle-inspector", toggleInspector);
  }, []);

  function startResize(event: ReactMouseEvent<HTMLDivElement>) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    const move = (ev: MouseEvent) => {
      const next = startWidth + (startX - ev.clientX);
      setWidth(clampWidth(next));
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      document.body.classList.remove("is-resizing");
      activeListenersRef.current = null;
    };
    activeListenersRef.current = { move, up };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    document.body.classList.add("is-resizing");
  }

  if (collapsed) {
    return (
      <aside className="inspector inspector--collapsed" aria-label="Inspector">
        <button
          type="button"
          className="inspector-collapse-btn inspector-collapse-btn--rail"
          aria-label="Expand inspector"
          title="Expand inspector (Shift+I)"
          onClick={() => setCollapsedPersisted(false)}
        >
          <CaretLeft size={14} weight="bold" />
        </button>
        <span className="inspector-rail-label" aria-hidden>
          Inspector
        </span>
      </aside>
    );
  }

  return (
    <aside className="inspector" style={{ width }}>
      <div className="inspector-resize" onMouseDown={startResize} title="Drag to resize" />
      <button
        type="button"
        className="inspector-collapse-btn inspector-collapse-btn--float"
        aria-label="Collapse inspector"
        title="Collapse inspector (Shift+I)"
        onClick={() => setCollapsedPersisted(true)}
      >
        <CaretRight size={14} weight="bold" />
      </button>
      {selectedId ? (
        <div className="inspector-scroll">
          <NodeDetails nodeId={selectedId} />
        </div>
      ) : (
        <>
          <div className="panel-head">
            <h2>Inspector</h2>
          </div>
          <div className="inspector-empty">
            <div className="inspector-empty-summary">
              <div>
                <strong>{workflowNodes.length}</strong>
                <span>Nodes</span>
              </div>
              <div>
                <strong>{running ? "Running" : "Idle"}</strong>
                <span>Status</span>
              </div>
              <div>
                <strong>{dirty ? "Draft" : "Saved"}</strong>
                <span>Changes</span>
              </div>
            </div>
            <div>
              <h3>No node selected</h3>
              <p className="muted">
                Select a node to edit parameters, or add the next node directly on the canvas.
              </p>
            </div>
            <div className="inspector-empty-actions">
              <button
                type="button"
                onClick={() => window.dispatchEvent(new Event("noodle:focus-node-search"))}
              >
                Search nodes
              </button>
              <button
                type="button"
                onClick={() => window.dispatchEvent(new Event("noodle:fit-view"))}
              >
                Fit view
              </button>
              {firstWorkflowNode && (
                <button
                  type="button"
                  onClick={() => setSelected(firstWorkflowNode.id)}
                >
                  Select first node
                </button>
              )}
            </div>
          </div>
        </>
      )}
    </aside>
  );
}
