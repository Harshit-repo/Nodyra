import { useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";

import { NodeDetails } from "./NodeDetails";
import { useEditor } from "./store";

const MIN_WIDTH = 260;
const MAX_WIDTH = 720;

export function Inspector() {
  const selectedId = useEditor((s) => s.selectedId);
  const [width, setWidth] = useState(320);

  function startResize(event: ReactMouseEvent<HTMLDivElement>) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    const move = (ev: MouseEvent) => {
      const next = startWidth + (startX - ev.clientX);
      setWidth(Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, next)));
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      document.body.classList.remove("is-resizing");
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    document.body.classList.add("is-resizing");
  }

  return (
    <aside className="inspector" style={{ width }}>
      <div className="inspector-resize" onMouseDown={startResize} title="Drag to resize" />
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
            <p>Select a node to edit its parameters.</p>
            <p className="muted">
              Drag nodes from the palette, then wire each output into the next node&apos;s input. Double-click a node to open its details.
            </p>
          </div>
        </>
      )}
    </aside>
  );
}
