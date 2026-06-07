import type { NodeProps } from "@xyflow/react";

/**
 * Read-only visual frame drawn behind a loop's body nodes. Computed from the
 * graph in Canvas (never persisted); purely cosmetic, no handles.
 */
export function LoopFrame({ data }: NodeProps) {
  const label = (data as { label?: string }).label ?? "↻ loop";
  return (
    <div className="loop-frame" style={{ width: "100%", height: "100%" }}>
      <span className="loop-frame-label">{label}</span>
    </div>
  );
}
