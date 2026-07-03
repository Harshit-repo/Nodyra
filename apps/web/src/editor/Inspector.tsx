import { CaretLeft, CaretRight } from "@phosphor-icons/react";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent } from "react";
import { useShallow } from "zustand/react/shallow";

import { useEditor } from "./store";
import { safeGetItem, safeSetItem } from "../safeStorage";

const NodeDetails = lazy(() =>
  import("./NodeDetails").then((module) => ({ default: module.NodeDetails })),
);

const MIN_WIDTH = 260;
const MAX_WIDTH = 720;
const DEFAULT_WIDTH = 320;
const WIDTH_KEY = "nodyra_inspector_width";
const COLLAPSED_KEY = "nodyra_inspector_collapsed";

function clampWidth(value: number): number {
  return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, value));
}

function readStoredWidth(): number {
  const parsed = Number(safeGetItem(WIDTH_KEY));
  return Number.isFinite(parsed) ? clampWidth(parsed) : DEFAULT_WIDTH;
}

export function Inspector() {
  const selectedId = useEditor((s) => s.selectedId);
  const [workflowNodeCount, firstWorkflowNodeId] = useEditor(
    useShallow((s) => {
      let count = 0;
      let firstId: string | null = null;
      for (const node of s.nodes) {
        if (!node.data?.manifest) continue;
        count += 1;
        firstId ??= node.id;
      }
      return [count, firstId] as const;
    }),
  );
  const dirty = useEditor((s) => s.dirty);
  const running = useEditor((s) => s.running);
  const setSelected = useEditor((s) => s.setSelected);
  const [width, setWidth] = useState(readStoredWidth);
  const [collapsed, setCollapsed] = useState(() => {
    return safeGetItem(COLLAPSED_KEY) === "1";
  });
  const activeListenersRef = useRef<{ move: (ev: MouseEvent) => void; up: () => void } | null>(null);

  function setCollapsedPersisted(next: boolean): void {
    setCollapsed(next);
    safeSetItem(COLLAPSED_KEY, next ? "1" : "0");
  }

  function toggleCollapsed(): void {
    setCollapsed((v) => {
      const next = !v;
      safeSetItem(COLLAPSED_KEY, next ? "1" : "0");
      return next;
    });
  }

  useEffect(() => {
    safeSetItem(WIDTH_KEY, String(width));
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
    window.addEventListener("nodyra:toggle-inspector", toggleInspector);
    return () => window.removeEventListener("nodyra:toggle-inspector", toggleInspector);
  }, []);

  function handleResizeKey(event: ReactKeyboardEvent<HTMLDivElement>): void {
    const step = event.shiftKey ? 50 : 10;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setWidth((w) => clampWidth(w + step));
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      setWidth((w) => clampWidth(w - step));
    }
  }

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
      <div
        className="inspector-resize"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize inspector"
        aria-valuenow={width}
        aria-valuemin={MIN_WIDTH}
        aria-valuemax={MAX_WIDTH}
        tabIndex={0}
        onMouseDown={startResize}
        onKeyDown={handleResizeKey}
        title="Drag to resize (← → arrow keys)"
      />
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
          <Suspense fallback={<div className="inspector-empty">Loading inspector…</div>}>
            <NodeDetails nodeId={selectedId} />
          </Suspense>
        </div>
      ) : (
        <>
          <div className="panel-head">
            <h2>Inspector</h2>
          </div>
          <div className="inspector-empty">
            <div className="inspector-empty-summary">
              <div>
                <strong>{workflowNodeCount}</strong>
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
                onClick={() => window.dispatchEvent(new Event("nodyra:focus-node-search"))}
              >
                Search nodes
              </button>
              <button
                type="button"
                onClick={() => window.dispatchEvent(new Event("nodyra:fit-view"))}
              >
                Fit view
              </button>
              {firstWorkflowNodeId && (
                <button
                  type="button"
                  onClick={() => setSelected(firstWorkflowNodeId)}
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
