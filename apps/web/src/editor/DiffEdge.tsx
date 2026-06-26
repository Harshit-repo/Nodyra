import { getBezierPath, type EdgeProps } from "@xyflow/react";

export function DiffEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
}: EdgeProps) {
  const status = (data as Record<string, unknown>)?.diffStatus as string | undefined;
  const [edgePath] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition });

  const stroke =
    status === "added" ? "#22c55e" :
    status === "removed" ? "#f87171" :
    "#94a3b8";

  return (
    <path
      id={id}
      d={edgePath}
      stroke={stroke}
      strokeWidth={!status || status === "unchanged" ? 1.5 : 2}
      strokeDasharray={status === "removed" ? "6 3" : undefined}
      fill="none"
      opacity={status === "removed" ? 0.5 : 1}
      className="react-flow__edge-path"
    />
  );
}
