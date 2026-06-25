import type { NodeProps } from "@xyflow/react";
import { memo } from "react";

/**
 * Read-only visual frame drawn behind a loop's body nodes. Computed from the
 * graph in Canvas (never persisted); purely cosmetic, no handles.
 */
function LoopFrameComponent({ data }: NodeProps) {
  const label = (data as { label?: string }).label ?? "↻ loop";
  return (
    <div className="loop-frame" style={{ width: "100%", height: "100%" }}>
      <span className="loop-frame-label">{label}</span>
    </div>
  );
}

export const LoopFrame = memo(
  LoopFrameComponent,
  (previous, next) =>
    (previous.data as { label?: string }).label ===
    (next.data as { label?: string }).label,
);
